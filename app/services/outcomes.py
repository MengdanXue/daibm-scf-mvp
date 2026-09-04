from __future__ import annotations

import logging

import hashlib
import json
import math
import uuid
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.identity import AuthenticatedUser
from app.ledger import canonical_json, canonical_timestamp
from app.models import FinancingRequestModel
from app.models_outcome import ActualOutcomeModel, CalibrationRunModel
from app.models_research import ModelVersionModel, RiskAssessmentModel
from app.repositories.ledger import LedgerRepository
from app.repositories.outcomes import OutcomeRepository
from app.schemas_outcome import ActualOutcomeCreate
from app.services.adaptive_risk import (
    evaluate_activation_gate,
    is_strictly_newer_candidate,
    load_verified_calibration,
    resolve_deployment_scope,
)
from app.services.outcome_calibration import (
    CalibrationDatasetSummary,
    UnmeasuredCalibrationDataset,
    CalibrationCandidate,
    CalibrationObservation,
    CalibrationTrainingConfig,
    StagedCalibrationArtifact,
    build_calibration_candidate,
    discard_staged_artifact,
    publish_candidate_artifact,
    recover_candidate_artifact,
    stage_candidate_artifact,
    summarize_calibration_observations,
)


_logger = logging.getLogger(__name__)


class OutcomeError(Exception):
    """Base error for immutable actual-outcome operations."""


class OutcomeNotFound(OutcomeError):
    """The requested facility, outcome, or calibration run does not exist."""


class ForbiddenOutcome(OutcomeError):
    """Only auditors may write or inspect actual outcomes."""


class OutcomeConflict(OutcomeError):
    """The outcome conflicts with immutable state or required lineage."""


BUSINESS_BASELINE_ENGINE = "transparent_logistic_baseline_v0.1"


Trainer = Callable[..., CalibrationCandidate]
ArtifactWriter = Callable[[Path, CalibrationCandidate], StagedCalibrationArtifact]


class OutcomeService:
    """Record immutable outcomes and govern adaptive calibration deployments."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        artifact_root: Path,
        repository: OutcomeRepository | None = None,
        ledger_repository: LedgerRepository | None = None,
        trainer: Trainer = build_calibration_candidate,
        artifact_writer: ArtifactWriter = stage_candidate_artifact,
        training_config: CalibrationTrainingConfig | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.artifact_root = Path(artifact_root)
        self.repository = repository or OutcomeRepository()
        self.ledger_repository = ledger_repository or LedgerRepository()
        self.trainer = trainer
        self.artifact_writer = artifact_writer
        self.training_config = training_config or CalibrationTrainingConfig()
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def submit(
        self,
        facility_id: str | uuid.UUID,
        payload: ActualOutcomeCreate,
        user: AuthenticatedUser,
    ) -> dict[str, Any]:
        self._require_auditor(user)
        normalized_id = self._uuid(facility_id)
        request_sha256 = self._request_sha256(normalized_id, payload)
        staged: StagedCalibrationArtifact | None = None
        outcome_id: uuid.UUID | None = None
        run_id: uuid.UUID | None = None
        try:
            with self.session_factory.begin() as session:
                self.repository.acquire_training_lock(session)
                replay = self.repository.get_by_idempotency_key(
                    session, payload.idempotency_key
                )
                if replay is not None:
                    return self._replay(
                        session, replay, normalized_id, request_sha256
                    )

                facility = self.repository.get_facility_for_update(
                    session, normalized_id
                )
                if facility is None:
                    raise OutcomeNotFound(str(normalized_id))
                existing = self.repository.get_by_facility(session, normalized_id)
                if existing is not None:
                    if existing.request_sha256 != request_sha256:
                        raise OutcomeConflict(
                            "Facility already has a different actual outcome"
                        )
                    return self._result(session, existing)
                if facility.status != "closed" or Decimal(
                    facility.outstanding_amount
                ) != Decimal("0.00"):
                    raise OutcomeConflict(
                        "Actual outcomes require a closed facility with zero balance"
                    )
                if payload.loss_amount > Decimal(facility.principal):
                    raise OutcomeConflict(
                        "Actual outcome loss amount cannot exceed principal"
                    )
                if facility.closed_at is None or payload.observed_at < facility.closed_at:
                    raise OutcomeConflict("observed_at cannot precede facility closure")

                application = session.get(FinancingRequestModel, facility.request_id)
                if (
                    application is None
                    or application.risk_assessment_id is None
                    or application.risk_score is None
                    or not application.risk_input_sha256
                    or not application.risk_engine_version
                    or application.risk_assessed_at is None
                ):
                    raise OutcomeConflict(
                        "Facility request prediction lineage is incomplete"
                    )
                assessment = session.get(
                    RiskAssessmentModel, application.risk_assessment_id
                )
                if assessment is None:
                    if application.risk_engine_version != BUSINESS_BASELINE_ENGINE:
                        raise OutcomeConflict(
                            "Unregistered prediction engine lineage is not supported"
                        )
                    model_version_id = None
                    original_risk_score = float(
                        application.raw_risk_score
                        if application.raw_risk_score is not None
                        else application.risk_score
                    )
                    risk_input_sha256 = application.risk_input_sha256
                else:
                    model_version = session.get(
                        ModelVersionModel, assessment.model_version_id
                    )
                    if model_version is None:
                        raise OutcomeConflict("Model-version lineage is missing")
                    expected_engine_version = (
                        f"{model_version.model_name}@{model_version.semantic_version}"
                    )
                    if application.risk_engine_version != expected_engine_version:
                        raise OutcomeConflict(
                            "Request engine lineage disagrees with assessed model version"
                        )
                    if not math.isclose(
                        float(application.risk_score),
                        float(assessment.risk_score),
                        rel_tol=0.0,
                        abs_tol=1e-12,
                    ):
                        raise OutcomeConflict("Request and assessment scores disagree")
                    if application.risk_input_sha256 != assessment.input_sha256:
                        raise OutcomeConflict(
                            "Request and assessment input lineage disagree"
                        )
                    model_version_id = assessment.model_version_id
                    original_risk_score = float(assessment.risk_score)
                    risk_input_sha256 = assessment.input_sha256

                now = self._now()
                outcome = self.repository.add_outcome(
                    session,
                    ActualOutcomeModel(
                        outcome_id=uuid.uuid4(),
                        facility_id=facility.facility_id,
                        request_id=facility.request_id,
                        risk_assessment_id=application.risk_assessment_id,
                        model_version_id=model_version_id,
                        submitted_by_user_id=user.user_id,
                        idempotency_key=payload.idempotency_key,
                        request_sha256=request_sha256,
                        defaulted=payload.defaulted,
                        days_past_due=payload.days_past_due,
                        loss_amount=payload.loss_amount,
                        observed_at=payload.observed_at,
                        evidence_sha256=payload.evidence_sha256,
                        provenance=payload.provenance,
                        original_risk_score=original_risk_score,
                        risk_engine_version=application.risk_engine_version,
                        risk_input_sha256=risk_input_sha256,
                        recorded_at=now,
                    ),
                )
                observations = tuple(
                    self._observation(item)
                    for item in self.repository.list_all_outcomes(session)
                )
                provenances = tuple(item.provenance for item in observations)
                deployment_scope = resolve_deployment_scope(provenances)
                summary: CalibrationDatasetSummary | UnmeasuredCalibrationDataset = (
                    self._fallback_summary(observations)
                )
                candidate: CalibrationCandidate | None = None
                failure_code: str | None = None
                try:
                    summary = summarize_calibration_observations(
                        observations,
                        config=self.training_config,
                    )
                    candidate = self.trainer(
                        observations, config=self.training_config
                    )
                except Exception:
                    # A calibration run is recorded either way, so without
                    # this the only trace of a defect in the trainer is a
                    # run row reading "failed" with no cause attached.
                    _logger.exception(
                        "Calibration candidate training failed for outcome %s",
                        outcome.outcome_id,
                    )
                    failure_code = "candidate_training_failed"
                if candidate is not None:
                    try:
                        staged = self.artifact_writer(
                            self.artifact_root, candidate
                        )
                    except Exception:
                        _logger.exception(
                            "Staging the calibration artifact failed "
                            "under %s",
                            self.artifact_root,
                        )
                        failure_code = "artifact_write_failed"
                        staged = None

                completed_at = self._now()
                configuration = self._configuration()
                run = self.repository.add_run(
                    session,
                    CalibrationRunModel(
                        calibration_run_id=uuid.uuid4(),
                        trigger_outcome_id=outcome.outcome_id,
                        dataset_sha256=summary.dataset_sha256,
                        sample_count=summary.sample_count,
                        positive_count=summary.positive_count,
                        negative_count=summary.negative_count,
                        metrics_before=summary.metrics_before,
                        metrics_after=(
                            candidate.metrics_after
                            if candidate is not None and failure_code is None
                            else None
                        ),
                        configuration=configuration,
                        status=(
                            candidate.status
                            if candidate is not None and failure_code is None
                            else "failed"
                        ),
                        artifact_locator=(
                            str(staged.path.resolve())
                            if staged is not None and failure_code is None
                            else None
                        ),
                        artifact_sha256=(
                            staged.sha256
                            if staged is not None and failure_code is None
                            else None
                        ),
                        failure_code=failure_code,
                        deployment_status="not_deployed",
                        deployment_scope=deployment_scope,
                        activation_mode=None,
                        activated_at=None,
                        deactivated_at=None,
                        previous_active_run_id=None,
                        activation_reason=(
                            "not_evaluated"
                            if candidate is not None and failure_code is None
                            else failure_code or "training_failed"
                        ),
                        started_at=now,
                        completed_at=completed_at,
                    ),
                )
                self.ledger_repository.append_many(
                    session,
                    outcome.outcome_id,
                    self._events(
                        outcome,
                        run,
                        request_risk_engine_version=application.risk_engine_version,
                    ),
                )
                outcome_id = outcome.outcome_id
                run_id = run.calibration_run_id
        except Exception:
            discard_staged_artifact(staged)
            raise

        if staged is not None:
            try:
                publish_candidate_artifact(staged)
            except Exception:
                _logger.exception(
                    "Publishing the calibration artifact failed for run %s",
                    run_id,
                )
                assert outcome_id is not None and run_id is not None
                self._mark_publication_failed(outcome_id, run_id)
                self._discard_failed_publication(staged)
            else:
                assert candidate is not None
                self._evaluate_deployment(
                    run_id,
                    candidate,
                    provenances=provenances,
                )

        assert outcome_id is not None and run_id is not None
        with self.session_factory() as session:
            committed_outcome = self.repository.get_outcome(session, outcome_id)
            committed_run = self.repository.get_run(session, run_id)
            if committed_outcome is None or committed_run is None:
                raise RuntimeError("Committed outcome calibration result is missing")
            return self._result(session, committed_outcome, committed_run)

    def get_outcome(
        self,
        outcome_id: str | uuid.UUID,
        user: AuthenticatedUser,
    ) -> dict[str, Any]:
        self._require_auditor(user)
        with self.session_factory() as session:
            outcome = self.repository.get_outcome(session, self._uuid(outcome_id))
            if outcome is None:
                raise OutcomeNotFound(str(outcome_id))
            return self._serialize_outcome(outcome)

    def list_outcomes(
        self,
        user: AuthenticatedUser,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        self._require_auditor(user)
        self._page(limit, offset)
        with self.session_factory() as session:
            return [
                self._serialize_outcome(item)
                for item in self.repository.list_outcomes(
                    session, limit=limit, offset=offset
                )
            ]

    def get_run(
        self,
        run_id: str | uuid.UUID,
        user: AuthenticatedUser,
    ) -> dict[str, Any]:
        self._require_auditor(user)
        with self.session_factory() as session:
            run = self.repository.get_run(session, self._uuid(run_id))
            if run is None:
                raise OutcomeNotFound(str(run_id))
            return self._serialize_run(run)

    def list_runs(
        self,
        user: AuthenticatedUser,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        self._require_auditor(user)
        self._page(limit, offset)
        with self.session_factory() as session:
            return [
                self._serialize_run(item)
                for item in self.repository.list_runs(
                    session, limit=limit, offset=offset
                )
            ]

    def reconcile_deployments(self) -> None:
        with self.session_factory() as session:
            pending_ids = [
                run.calibration_run_id
                for run in self.repository.list_pending_deployment_runs(session)
            ]
        for run_id in pending_ids:
            try:
                with self.session_factory() as session:
                    run = self.repository.get_run(session, run_id)
                    if run is None:
                        continue
                    candidate = self._candidate_from_run(run)
                    dataset = candidate.artifact.get("dataset")
                    if not isinstance(dataset, dict):
                        raise ValueError("calibration dataset manifest is invalid")
                    outcome_ids = {
                        str(value) for value in dataset.get("outcome_ids", [])
                    }
                    provenances = tuple(
                        item.provenance
                        for item in self.repository.list_all_outcomes(session)
                        if str(item.outcome_id) in outcome_ids
                    )
                self._evaluate_deployment(
                    run_id,
                    candidate,
                    provenances=provenances,
                )
            except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                self._reject_unrecoverable_deployment(run_id)

    def get_active_deployment(self, user: AuthenticatedUser) -> dict[str, Any]:
        self._require_auditor(user)
        with self.session_factory() as session:
            run = self.repository.get_active_run(session)
            if run is None:
                raise OutcomeNotFound("active calibration deployment")
            return self._serialize_run(run)

    def rollback(
        self,
        expected_active_run_id: str | uuid.UUID,
        user: AuthenticatedUser,
    ) -> dict[str, Any]:
        self._require_auditor(user)
        expected_id = self._uuid(expected_active_run_id)
        with self.session_factory.begin() as session:
            self.repository.acquire_training_lock(session)
            current = self.repository.get_active_run(session, for_update=True)
            if current is None:
                raise OutcomeNotFound("active calibration deployment")
            if current.calibration_run_id != expected_id:
                raise OutcomeConflict("active calibration changed; refresh and retry")
            if current.previous_active_run_id is None:
                raise OutcomeConflict("active calibration has no rollback predecessor")
            restored = self.repository.get_run_for_update(
                session,
                current.previous_active_run_id,
            )
            if restored is None or restored.deployment_status != "superseded":
                raise OutcomeConflict("rollback predecessor is unavailable")

            now = self._now()
            current.deployment_status = "superseded"
            current.deactivated_at = now
            session.flush()
            restored.deployment_status = "active"
            restored.activation_mode = "manual_rollback"
            restored.activated_at = now
            restored.deactivated_at = None
            restored.activation_reason = "manual_rollback"
            session.flush()
            self.ledger_repository.append_many(
                session,
                restored.calibration_run_id,
                [
                    (
                        "CALIBRATION_ROLLED_BACK",
                        {
                            "deactivated_run_id": str(current.calibration_run_id),
                            "restored_run_id": str(restored.calibration_run_id),
                            "deployment_scope": restored.deployment_scope,
                            "expected_active_run_id": str(expected_id),
                        },
                    )
                ],
            )
            return self._serialize_run(restored)

    def _replay(
        self,
        session: Session,
        outcome: ActualOutcomeModel,
        facility_id: uuid.UUID,
        request_sha256: str,
    ) -> dict[str, Any]:
        if outcome.facility_id != facility_id or outcome.request_sha256 != request_sha256:
            raise OutcomeConflict(
                "Idempotency key was already used for different outcome semantics"
            )
        return self._result(session, outcome)

    def _result(
        self,
        session: Session,
        outcome: ActualOutcomeModel,
        run: CalibrationRunModel | None = None,
    ) -> dict[str, Any]:
        active_run = run or self.repository.get_run_by_outcome(
            session, outcome.outcome_id
        )
        if active_run is None:
            raise RuntimeError("Outcome calibration run is missing")
        return {
            "outcome": self._serialize_outcome(outcome),
            "calibration_run": self._serialize_run(active_run),
        }

    def _mark_publication_failed(
        self,
        outcome_id: uuid.UUID,
        run_id: uuid.UUID,
    ) -> datetime:
        with self.session_factory.begin() as session:
            run = self.repository.get_run_for_update(session, run_id)
            if run is None:
                raise RuntimeError("Committed calibration run is missing")
            if run.status == "failed":
                return run.completed_at
            if run.deployment_status == "active":
                raise RuntimeError("Cannot invalidate an active deployment in place")
            run.status = "failed"
            run.artifact_locator = None
            run.artifact_sha256 = None
            run.metrics_after = None
            run.failure_code = "artifact_write_failed"
            run.deployment_status = "activation_failed"
            run.activation_reason = "artifact_publication_failed"
            run.completed_at = self._now()
            self.ledger_repository.append_many(
                session,
                outcome_id,
                [
                    (
                        "CALIBRATION_CANDIDATE_FAILED",
                        {
                            "calibration_run_id": str(run.calibration_run_id),
                            "dataset_sha256": run.dataset_sha256,
                            "sample_count": run.sample_count,
                            "positive_count": run.positive_count,
                            "negative_count": run.negative_count,
                            "status": "failed",
                            "artifact_sha256": None,
                            "failure_code": "artifact_write_failed",
                            "deployment_status": run.deployment_status,
                            "activation_reason": run.activation_reason,
                        },
                    )
                ],
            )
            return run.completed_at

    def _evaluate_deployment(
        self,
        run_id: uuid.UUID,
        candidate: CalibrationCandidate,
        *,
        provenances: tuple[str, ...],
    ) -> None:
        with self.session_factory.begin() as session:
            self.repository.acquire_training_lock(session)
            run = self.repository.get_run_for_update(session, run_id)
            if run is None:
                raise RuntimeError("Committed calibration run is missing")
            if run.deployment_status != "not_deployed":
                return
            integrity = "not_applicable"
            if run.artifact_locator is not None and run.artifact_sha256 is not None:
                try:
                    load_verified_calibration(
                        Path(run.artifact_locator),
                        expected_sha256=run.artifact_sha256,
                        run_id=str(run.calibration_run_id),
                        deployment_scope=run.deployment_scope,
                        expected_dataset_sha256=run.dataset_sha256,
                    )
                    integrity = "verified"
                except ValueError:
                    integrity = "invalid"
            decision = evaluate_activation_gate(
                candidate,
                artifact_integrity=integrity,
                provenances=provenances,
            )
            run.deployment_scope = decision.deployment_scope
            run.activation_reason = decision.reason
            if not decision.activate:
                run.deployment_status = "rejected"
                event_type = "CALIBRATION_AUTO_REJECTED"
                payload = {
                    "calibration_run_id": str(run.calibration_run_id),
                    "deployment_scope": decision.deployment_scope,
                    "activation_reason": decision.reason,
                    "sample_count": run.sample_count,
                    "positive_count": run.positive_count,
                    "negative_count": run.negative_count,
                    "artifact_integrity": integrity,
                }
            else:
                now = self._now()
                previous = self.repository.get_active_run(session, for_update=True)
                if previous is not None and not is_strictly_newer_candidate(
                    run.sample_count,
                    active_sample_count=previous.sample_count,
                ):
                    run.deployment_status = "rejected"
                    run.activation_reason = "stale_candidate"
                    event_type = "CALIBRATION_AUTO_REJECTED"
                    payload = {
                        "calibration_run_id": str(run.calibration_run_id),
                        "deployment_scope": decision.deployment_scope,
                        "activation_reason": run.activation_reason,
                        "active_run_id": str(previous.calibration_run_id),
                        "sample_count": run.sample_count,
                        "active_sample_count": previous.sample_count,
                        "artifact_integrity": integrity,
                    }
                else:
                    if previous is not None:
                        previous.deployment_status = "superseded"
                        previous.deactivated_at = now
                        session.flush()
                    run.deployment_status = "active"
                    run.activation_mode = "automatic"
                    run.activated_at = now
                    run.deactivated_at = None
                    run.previous_active_run_id = (
                        previous.calibration_run_id if previous is not None else None
                    )
                    event_type = "CALIBRATION_AUTO_ACTIVATED"
                    payload = {
                        "calibration_run_id": str(run.calibration_run_id),
                        "deployment_scope": decision.deployment_scope,
                        "activation_reason": decision.reason,
                        "previous_active_run_id": (
                            str(previous.calibration_run_id)
                            if previous is not None
                            else None
                        ),
                        "artifact_sha256": run.artifact_sha256,
                        "sample_count": run.sample_count,
                        "positive_count": run.positive_count,
                        "negative_count": run.negative_count,
                    }
            self.ledger_repository.append_many(
                session,
                run.calibration_run_id,
                [(event_type, payload)],
            )

    def _reject_unrecoverable_deployment(self, run_id: uuid.UUID) -> None:
        with self.session_factory.begin() as session:
            self.repository.acquire_training_lock(session)
            run = self.repository.get_run_for_update(session, run_id)
            if run is None or run.deployment_status != "not_deployed":
                return
            run.deployment_status = "activation_failed"
            run.activation_reason = "artifact_unverified"
            self.ledger_repository.append_many(
                session,
                run.calibration_run_id,
                [
                    (
                        "CALIBRATION_AUTO_REJECTED",
                        {
                            "calibration_run_id": str(run.calibration_run_id),
                            "deployment_scope": run.deployment_scope,
                            "activation_reason": run.activation_reason,
                            "artifact_integrity": "unavailable",
                        },
                    )
                ],
            )

    @staticmethod
    def _candidate_from_run(run: CalibrationRunModel) -> CalibrationCandidate:
        if (
            run.artifact_locator is None
            or run.artifact_sha256 is None
            or run.metrics_before is None
            or run.metrics_after is None
        ):
            raise ValueError("calibration run is not deployable")
        path = Path(run.artifact_locator)
        if recover_candidate_artifact(path, run.artifact_sha256) != "verified":
            raise ValueError("calibration artifact cannot be recovered")
        artifact_bytes = path.read_bytes()
        artifact = json.loads(artifact_bytes)
        coefficients = artifact["coefficients"]
        return CalibrationCandidate(
            dataset_sha256=run.dataset_sha256,
            sample_count=run.sample_count,
            positive_count=run.positive_count,
            negative_count=run.negative_count,
            status=run.status,
            slope=float(coefficients["slope"]),
            intercept=float(coefficients["intercept"]),
            metrics_before={
                key: float(value) for key, value in run.metrics_before.items()
            },
            metrics_after={
                key: float(value) for key, value in run.metrics_after.items()
            },
            artifact=artifact,
            artifact_bytes=artifact_bytes,
        )

    @staticmethod
    def _discard_failed_publication(staged: StagedCalibrationArtifact) -> None:
        try:
            discard_staged_artifact(staged)
            if staged.staged_path is not None:
                staged.path.unlink(missing_ok=True)
        except OSError:
            # The durable failed run and ledger event remain authoritative.
            pass

    def _configuration(self) -> dict[str, float | int]:
        return {
            "epochs": self.training_config.epochs,
            "l2_penalty": self.training_config.l2_penalty,
            "learning_rate": self.training_config.learning_rate,
            "probability_epsilon": self.training_config.probability_epsilon,
        }

    @staticmethod
    def _fallback_summary(
        observations: tuple[CalibrationObservation, ...],
    ) -> UnmeasuredCalibrationDataset:
        ordered = tuple(sorted(observations, key=lambda item: item.outcome_id))
        payload = [asdict(item) for item in ordered]
        positive_count = sum(int(item.defaulted) for item in ordered)
        return UnmeasuredCalibrationDataset(
            dataset_sha256=hashlib.sha256(
                canonical_json(payload).encode("utf-8")
            ).hexdigest(),
            sample_count=len(ordered),
            positive_count=positive_count,
            negative_count=len(ordered) - positive_count,
        )

    @staticmethod
    def _events(
        outcome: ActualOutcomeModel,
        run: CalibrationRunModel,
        *,
        request_risk_engine_version: str,
    ) -> list[tuple[str, dict[str, Any]]]:
        return [
            (
                "ACTUAL_OUTCOME_RECORDED",
                {
                    "outcome_id": str(outcome.outcome_id),
                    "facility_id": str(outcome.facility_id),
                    "request_id": str(outcome.request_id),
                    "risk_assessment_id": str(outcome.risk_assessment_id),
                    "model_version_id": (
                        str(outcome.model_version_id)
                        if outcome.model_version_id is not None
                        else None
                    ),
                    "request_risk_engine_version": request_risk_engine_version,
                    "original_risk_score": outcome.original_risk_score,
                    "defaulted": outcome.defaulted,
                    "evidence_sha256": outcome.evidence_sha256,
                    "provenance": outcome.provenance,
                },
            ),
            (
                (
                    "CALIBRATION_CANDIDATE_FAILED"
                    if run.failure_code is not None
                    else "CALIBRATION_CANDIDATE_TRAINED"
                ),
                {
                    "calibration_run_id": str(run.calibration_run_id),
                    "dataset_sha256": run.dataset_sha256,
                    "sample_count": run.sample_count,
                    "positive_count": run.positive_count,
                    "negative_count": run.negative_count,
                    "status": run.status,
                    "artifact_sha256": run.artifact_sha256,
                    "failure_code": run.failure_code,
                    "deployment_status": run.deployment_status,
                    "activation_reason": run.activation_reason,
                },
            ),
        ]

    @staticmethod
    def _serialize_outcome(outcome: ActualOutcomeModel) -> dict[str, Any]:
        return {
            "outcome_id": str(outcome.outcome_id),
            "facility_id": str(outcome.facility_id),
            "request_id": str(outcome.request_id),
            "risk_assessment_id": str(outcome.risk_assessment_id),
            "model_version_id": (
                str(outcome.model_version_id)
                if outcome.model_version_id is not None
                else None
            ),
            "risk_engine_version": outcome.risk_engine_version,
            "defaulted": outcome.defaulted,
            "days_past_due": outcome.days_past_due,
            "loss_amount": f"{Decimal(outcome.loss_amount):.2f}",
            "observed_at": canonical_timestamp(outcome.observed_at),
            "evidence_sha256": outcome.evidence_sha256,
            "provenance": outcome.provenance,
            "original_risk_score": outcome.original_risk_score,
            "risk_input_sha256": outcome.risk_input_sha256,
            "recorded_at": canonical_timestamp(outcome.recorded_at),
        }

    def _serialize_run(self, run: CalibrationRunModel) -> dict[str, Any]:
        if run.artifact_locator is None or run.artifact_sha256 is None:
            integrity = "not_applicable"
        else:
            artifact_path = Path(run.artifact_locator)
            try:
                integrity = recover_candidate_artifact(
                    artifact_path, run.artifact_sha256
                )
            except OSError:
                integrity = "unavailable"
            if integrity != "verified" and run.deployment_status != "active":
                staged = StagedCalibrationArtifact(
                    path=artifact_path,
                    staged_path=(
                        artifact_path.parent
                        / f".pending-{run.artifact_sha256}.json"
                    ),
                    sha256=run.artifact_sha256,
                )
                completed_at = self._mark_publication_failed(
                    run.trigger_outcome_id,
                    run.calibration_run_id,
                )
                self._discard_failed_publication(staged)
                run.status = "failed"
                run.artifact_locator = None
                run.artifact_sha256 = None
                run.metrics_after = None
                run.failure_code = "artifact_write_failed"
                run.completed_at = completed_at
                integrity = "not_applicable"
        return {
            "calibration_run_id": str(run.calibration_run_id),
            "trigger_outcome_id": str(run.trigger_outcome_id),
            "dataset_sha256": run.dataset_sha256,
            "sample_count": run.sample_count,
            "positive_count": run.positive_count,
            "negative_count": run.negative_count,
            "metrics_before": run.metrics_before,
            "metrics_after": run.metrics_after,
            "configuration": run.configuration,
            "status": run.status,
            "artifact_sha256": run.artifact_sha256,
            "artifact_integrity": integrity,
            "failure_code": run.failure_code,
            "deployment_status": run.deployment_status,
            "deployment_scope": run.deployment_scope,
            "activation_mode": run.activation_mode,
            "activation_reason": run.activation_reason,
            "activated_at": (
                canonical_timestamp(run.activated_at)
                if run.activated_at is not None
                else None
            ),
            "deactivated_at": (
                canonical_timestamp(run.deactivated_at)
                if run.deactivated_at is not None
                else None
            ),
            "previous_active_run_id": (
                str(run.previous_active_run_id)
                if run.previous_active_run_id is not None
                else None
            ),
            "started_at": canonical_timestamp(run.started_at),
            "completed_at": canonical_timestamp(run.completed_at),
        }

    @staticmethod
    def _observation(outcome: ActualOutcomeModel) -> CalibrationObservation:
        return CalibrationObservation(
            outcome_id=str(outcome.outcome_id),
            facility_id=str(outcome.facility_id),
            request_id=str(outcome.request_id),
            risk_assessment_id=str(outcome.risk_assessment_id),
            model_version_id=(
                str(outcome.model_version_id)
                if outcome.model_version_id is not None
                else None
            ),
            risk_engine_version=outcome.risk_engine_version,
            risk_input_sha256=outcome.risk_input_sha256,
            evidence_sha256=outcome.evidence_sha256,
            original_score=outcome.original_risk_score,
            defaulted=outcome.defaulted,
            observed_at=canonical_timestamp(outcome.observed_at),
            provenance=outcome.provenance,
        )

    @staticmethod
    def _request_sha256(
        facility_id: uuid.UUID,
        payload: ActualOutcomeCreate,
    ) -> str:
        semantic = {
            "facility_id": str(facility_id),
            "outcome": {
                "defaulted": payload.defaulted,
                "days_past_due": payload.days_past_due,
                "loss_amount": f"{payload.loss_amount.quantize(Decimal('0.01')):.2f}",
                "observed_at": canonical_timestamp(payload.observed_at),
                "evidence_sha256": payload.evidence_sha256,
                "provenance": payload.provenance,
            },
        }
        return hashlib.sha256(canonical_json(semantic).encode("utf-8")).hexdigest()

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Outcome service clock must be timezone-aware")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _uuid(value: str | uuid.UUID) -> uuid.UUID:
        try:
            return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
        except (TypeError, ValueError, AttributeError) as error:
            raise OutcomeNotFound(str(value)) from error

    @staticmethod
    def _require_auditor(user: AuthenticatedUser) -> None:
        if user.role != "auditor":
            raise ForbiddenOutcome("Only auditors may access actual outcomes")

    @staticmethod
    def _page(limit: int, offset: int) -> None:
        if not 1 <= limit <= 200 or offset < 0:
            raise ValueError("limit must be 1-200 and offset must be non-negative")


__all__ = [
    "ForbiddenOutcome",
    "OutcomeConflict",
    "OutcomeNotFound",
    "OutcomeService",
]
