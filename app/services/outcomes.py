from __future__ import annotations

import hashlib
import json
import math
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.domain.facility import derive_closure_reason
from app.identity import AuthenticatedUser
from app.ledger import canonical_json, canonical_timestamp
from app.models import FinancingRequestModel, LedgerEventModel
from app.models_facility import FinancingFacilityModel
from app.models_governance import (
    CalibrationJobModel,
    CalibrationRunObservationModel,
    OutcomeCorrectionModel,
)
from app.models_lifecycle import (
    FacilityDefaultModel,
    FacilityDelinquencyModel,
    FacilityWriteOffModel,
)
from app.models_outcome import ActualOutcomeModel, CalibrationRunModel
from app.models_research import ModelVersionModel, RiskAssessmentModel
from app.repositories.ledger import LedgerRepository
from app.repositories.outcomes import OutcomeRepository
from app.schemas_outcome import ActualOutcomeCreate, OutcomeCorrectionCreate
from app.services.adaptive_risk import (
    evaluate_activation_gate,
    is_strictly_newer_candidate,
    load_verified_calibration,
    resolve_deployment_scope,
)
from app.services.outcome_calibration import (
    CalibrationCandidate,
    CalibrationDatasetSummary,
    CalibrationObservation,
    CalibrationTrainingConfig,
    StagedCalibrationArtifact,
    build_calibration_candidate,
    canonical_training_configuration,
    discard_staged_artifact,
    publish_candidate_artifact,
    recover_candidate_artifact,
    stage_candidate_artifact,
    summarize_calibration_observations,
)


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


@dataclass(frozen=True)
class DerivedOutcomeFacts:
    defaulted: bool
    days_past_due: int
    loss_amount: Decimal


def derive_outcome_facts(
    session: Session,
    facility: FinancingFacilityModel,
) -> DerivedOutcomeFacts:
    """Derive immutable outcome facts only from governed lifecycle evidence."""
    delinquencies = tuple(
        session.scalars(
            select(FacilityDelinquencyModel).where(
                FacilityDelinquencyModel.facility_id == facility.facility_id
            )
        )
    )
    defaults = tuple(
        session.scalars(
            select(FacilityDefaultModel).where(
                FacilityDefaultModel.facility_id == facility.facility_id
            )
        )
    )
    writeoff = session.scalar(
        select(FacilityWriteOffModel).where(
            FacilityWriteOffModel.facility_id == facility.facility_id
        )
    )
    days_past_due = max(
        [
            0,
            *(row.days_past_due for row in delinquencies),
            *(row.days_past_due for row in defaults),
        ]
    )
    return DerivedOutcomeFacts(
        defaulted=bool(defaults),
        days_past_due=days_past_due,
        loss_amount=(
            Decimal(writeoff.amount).quantize(Decimal("0.01"))
            if writeoff is not None
            else Decimal("0.00")
        ),
    )


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
        return self._submit_governed(facility_id, payload, user)

    def _submit_governed(
        self,
        facility_id: str | uuid.UUID,
        payload: ActualOutcomeCreate,
        user: AuthenticatedUser,
    ) -> dict[str, Any]:
        self._require_auditor(user)
        normalized_id = self._uuid(facility_id)
        request_sha256 = self._request_sha256(normalized_id, payload)
        with self.session_factory.begin() as session:
            replay = self.repository.get_by_idempotency_key(
                session, payload.idempotency_key
            )
            if replay is not None:
                return self._replay(session, replay, normalized_id, request_sha256)

            facility = self.repository.get_facility_for_update(session, normalized_id)
            if facility is None:
                raise OutcomeNotFound(str(normalized_id))
            replay = self.repository.get_by_idempotency_key(
                session, payload.idempotency_key
            )
            if replay is not None:
                return self._replay(session, replay, normalized_id, request_sha256)
            existing = self.repository.get_by_facility(session, normalized_id)
            if existing is not None:
                raise OutcomeConflict("Facility already has an immutable actual outcome")

            facts, application, lineage = self._validate_submission_snapshot(
                session, facility, payload
            )
            now = self._now()
            outcome = self.repository.add_outcome(
                session,
                ActualOutcomeModel(
                    outcome_id=uuid.uuid4(),
                    facility_id=facility.facility_id,
                    request_id=facility.request_id,
                    risk_assessment_id=application.risk_assessment_id,
                    model_version_id=lineage["model_version_id"],
                    submitted_by_user_id=user.user_id,
                    idempotency_key=payload.idempotency_key,
                    request_sha256=request_sha256,
                    defaulted=facts.defaulted,
                    days_past_due=facts.days_past_due,
                    loss_amount=facts.loss_amount,
                    observed_at=payload.observed_at,
                    evidence_sha256=payload.evidence_sha256,
                    provenance=payload.provenance,
                    original_risk_score=lineage["original_risk_score"],
                    risk_engine_version=application.risk_engine_version,
                    risk_input_sha256=lineage["risk_input_sha256"],
                    recorded_at=now,
                ),
            )
            self.ledger_repository.append_many(
                session,
                outcome.outcome_id,
                [("ACTUAL_OUTCOME_RECORDED", self._outcome_event(outcome))],
            )
            job = self.repository.add_job(
                session,
                self._new_job(
                    scope=application.assessment_scope,
                    trigger_type="outcome_submitted",
                    idempotency_key=payload.idempotency_key,
                    now=now,
                    outcome_id=outcome.outcome_id,
                ),
            )
            return self._result(session, outcome, job=job)

    def preview(
        self,
        facility_id: str | uuid.UUID,
        user: AuthenticatedUser,
    ) -> dict[str, Any]:
        self._require_auditor(user)
        normalized_id = self._uuid(facility_id)
        with self.session_factory.begin() as session:
            facility = self.repository.get_facility_for_update(session, normalized_id)
            if facility is None:
                raise OutcomeNotFound(str(normalized_id))
            application = session.get(FinancingRequestModel, facility.request_id)
            if application is None:
                raise OutcomeConflict("Facility request lineage is missing")
            facts = self._validate_lifecycle_snapshot(session, facility)
            return {
                "facility_id": str(facility.facility_id),
                "defaulted": facts.defaulted,
                "days_past_due": facts.days_past_due,
                "loss_amount": self._money(facts.loss_amount),
                "closure_reason": facility.closure_reason,
                "closed_at": canonical_timestamp(facility.closed_at),
                "expected_provenance": self._provenance(application.assessment_scope),
                "deployment_scope": application.assessment_scope,
            }

    def create_correction(
        self,
        outcome_id: str | uuid.UUID,
        payload: OutcomeCorrectionCreate,
        user: AuthenticatedUser,
    ) -> dict[str, Any]:
        self._require_auditor(user)
        normalized_id = self._uuid(outcome_id)
        request_sha256 = self._correction_sha256(normalized_id, payload)
        with self.session_factory.begin() as session:
            replay = self.repository.get_correction_by_idempotency_key(
                session, payload.idempotency_key
            )
            if replay is not None:
                return self._replay_correction(
                    session, replay, normalized_id, request_sha256
                )

            # Global governed-write lock order: immutable identity/provenance read,
            # scope advisory xact lock, outcome row, correction head, ordered runs,
            # then append-only correction/ledger/job writes.
            outcome = self.repository.get_outcome(session, normalized_id)
            if outcome is None:
                raise OutcomeNotFound(str(normalized_id))
            scope = self._scope_for_provenance(outcome.provenance)
            self.repository.acquire_scope_lock(session, scope=scope)
            outcome = self.repository.get_outcome_for_update(session, normalized_id)
            if outcome is None:
                raise OutcomeNotFound(str(normalized_id))
            if self._scope_for_provenance(outcome.provenance) != scope:
                raise OutcomeConflict("Outcome provenance changed during correction")
            replay = self.repository.get_correction_by_idempotency_key(
                session, payload.idempotency_key
            )
            if replay is not None:
                return self._replay_correction(
                    session, replay, normalized_id, request_sha256
                )
            head = self.repository.get_correction_head(
                session, normalized_id, for_update=True
            )
            eligible = head is None or head.action == "REINSTATE"
            if payload.action == "EXCLUDE" and not eligible:
                raise OutcomeConflict("Outcome is already excluded from training")
            if payload.action == "REINSTATE" and eligible:
                raise OutcomeConflict("Outcome is already eligible for training")

            application = session.get(FinancingRequestModel, outcome.request_id)
            facility = session.get(FinancingFacilityModel, outcome.facility_id)
            if application is None or facility is None:
                raise OutcomeConflict("Outcome lifecycle lineage is missing")
            if payload.action == "REINSTATE":
                self._validate_existing_outcome(session, facility, application, outcome)

            now = self._now()
            if head is not None and now <= head.recorded_at:
                now = head.recorded_at + timedelta(microseconds=1)
            correction = OutcomeCorrectionModel(
                correction_id=uuid.uuid4(), outcome_id=outcome.outcome_id,
                action=payload.action, reason_code=payload.reason_code,
                comment=payload.comment, evidence_sha256=payload.evidence_sha256,
                auditor_user_id=user.user_id,
                idempotency_key=payload.idempotency_key,
                request_sha256=request_sha256, recorded_at=now,
            )
            invalidated = (
                self.repository.invalidate_active_runs_containing(
                    session, outcome.outcome_id,
                    scope=scope, now=now,
                )
                if payload.action == "EXCLUDE"
                else []
            )
            self.repository.add_correction(session, correction)
            events: list[tuple[str, dict[str, Any]]] = [
                (
                    "OUTCOME_TRAINING_EXCLUDED"
                    if payload.action == "EXCLUDE"
                    else "OUTCOME_TRAINING_REINSTATED",
                    {
                        "outcome_id": str(outcome.outcome_id),
                        "correction_id": str(correction.correction_id),
                        "action": correction.action,
                        "reason_code": correction.reason_code,
                        "evidence_sha256": correction.evidence_sha256,
                        "deployment_scope": scope,
                    },
                )
            ]
            events.extend(
                (
                    "CALIBRATION_DEPLOYMENT_INVALIDATED",
                    {
                        "outcome_id": str(outcome.outcome_id),
                        "correction_id": str(correction.correction_id),
                        "calibration_run_id": str(run.calibration_run_id),
                        "deployment_scope": scope,
                        "reason": "outcome_excluded",
                    },
                )
                for run in invalidated
            )
            self.ledger_repository.append_many(session, outcome.outcome_id, events)
            job = self.repository.add_job(
                session,
                self._new_job(
                    scope=scope,
                    trigger_type=(
                        "correction_exclude"
                        if payload.action == "EXCLUDE"
                        else "correction_reinstate"
                    ),
                    idempotency_key=payload.idempotency_key,
                    now=now,
                    correction_id=correction.correction_id,
                ),
            )
            return self._correction_result(
                correction, job, invalidated_run_ids=[
                    run.calibration_run_id for run in invalidated
                ]
            )

    def list_corrections(
        self,
        outcome_id: str | uuid.UUID,
        user: AuthenticatedUser,
    ) -> list[dict[str, Any]]:
        self._require_auditor(user)
        normalized_id = self._uuid(outcome_id)
        with self.session_factory() as session:
            if self.repository.get_outcome(session, normalized_id) is None:
                raise OutcomeNotFound(str(normalized_id))
            return [
                self._serialize_correction(item)
                for item in self.repository.list_corrections(session, normalized_id)
            ]

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
            return self._serialize_outcome(
                outcome,
                effective_training_eligible=self._is_eligible(
                    session, outcome.outcome_id
                ),
            )

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
                self._serialize_outcome(
                    item,
                    effective_training_eligible=eligible,
                )
                for item, eligible in self.repository.list_outcomes_with_eligibility(
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
                    memberships = tuple(session.scalars(
                        select(CalibrationRunObservationModel)
                        .where(
                            CalibrationRunObservationModel.calibration_run_id
                            == run_id
                        )
                        .order_by(CalibrationRunObservationModel.outcome_id)
                    ))
                    candidate = self._candidate_from_run(run, memberships)
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
            current_hint = self.repository.get_active_run(session)
            if current_hint is None:
                raise OutcomeNotFound("active calibration deployment")
            scope = current_hint.deployment_scope
            self.repository.acquire_scope_lock(session, scope=scope)
            current = self.repository.get_active_run(
                session, scope=scope, for_update=True
            )
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
            if restored.deployment_scope != scope:
                raise OutcomeConflict("rollback predecessor scope is inconsistent")

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

    def _validate_submission_snapshot(
        self,
        session: Session,
        facility: FinancingFacilityModel,
        payload: ActualOutcomeCreate,
    ) -> tuple[DerivedOutcomeFacts, FinancingRequestModel, dict[str, Any]]:
        facts = self._validate_lifecycle_snapshot(session, facility)
        if payload.observed_at < facility.closed_at:
            raise OutcomeConflict("observed_at cannot precede facility closure")
        application = session.scalar(
            select(FinancingRequestModel)
            .where(FinancingRequestModel.request_id == facility.request_id)
            .with_for_update(read=True)
        )
        if application is None:
            raise OutcomeConflict("Facility request lineage is missing")
        expected_provenance = self._provenance(application.assessment_scope)
        if payload.provenance != expected_provenance:
            raise OutcomeConflict(
                "Outcome provenance must match the authorized assessment scope"
            )
        return facts, application, self._prediction_lineage(session, application)

    def _validate_lifecycle_snapshot(
        self,
        session: Session,
        facility: FinancingFacilityModel,
    ) -> DerivedOutcomeFacts:
        if (
            facility.status != "closed"
            or Decimal(facility.outstanding_amount) != Decimal("0.00")
            or facility.closed_at is None
            or facility.closure_reason is None
        ):
            raise OutcomeConflict(
                "Actual outcomes require a closed facility with zero balance"
            )
        facts = derive_outcome_facts(session, facility)
        writeoff = session.scalar(
            select(FacilityWriteOffModel).where(
                FacilityWriteOffModel.facility_id == facility.facility_id
            )
        )
        try:
            expected_reason = derive_closure_reason(
                has_default=facts.defaulted,
                has_writeoff=writeoff is not None,
            )
        except ValueError as error:
            raise OutcomeConflict(str(error)) from error
        if facility.closure_reason != expected_reason:
            raise OutcomeConflict("Facility closure lineage is inconsistent")
        if facts.loss_amount > Decimal(facility.principal):
            raise OutcomeConflict("Derived loss amount cannot exceed principal")
        return facts

    def _validate_existing_outcome(
        self,
        session: Session,
        facility: FinancingFacilityModel,
        application: FinancingRequestModel,
        outcome: ActualOutcomeModel,
    ) -> None:
        if (
            facility.facility_id != outcome.facility_id
            or facility.request_id != outcome.request_id
            or application.request_id != outcome.request_id
        ):
            raise OutcomeConflict("Outcome request/facility association is inconsistent")
        if application.risk_assessment_id != outcome.risk_assessment_id:
            raise OutcomeConflict("Outcome assessment identity is inconsistent")
        if self._provenance(application.assessment_scope) != outcome.provenance:
            raise OutcomeConflict("Outcome provenance lineage is inconsistent")
        facts = self._validate_lifecycle_snapshot(session, facility)
        lineage = self._prediction_lineage(session, application)
        if (
            facts.defaulted != outcome.defaulted
            or facts.days_past_due != outcome.days_past_due
            or facts.loss_amount != Decimal(outcome.loss_amount)
            or lineage["model_version_id"] != outcome.model_version_id
            or lineage["risk_input_sha256"] != outcome.risk_input_sha256
            or not math.isclose(
                lineage["original_risk_score"],
                outcome.original_risk_score,
                rel_tol=0.0,
                abs_tol=1e-12,
            )
        ):
            raise OutcomeConflict("Outcome facts or prediction lineage are inconsistent")

    @staticmethod
    def _prediction_lineage(
        session: Session,
        application: FinancingRequestModel,
    ) -> dict[str, Any]:
        if (
            application.risk_assessment_id is None
            or application.risk_score is None
            or not application.risk_input_sha256
            or not application.risk_engine_version
            or application.risk_assessed_at is None
        ):
            raise OutcomeConflict("Facility request prediction lineage is incomplete")
        assessment = session.get(RiskAssessmentModel, application.risk_assessment_id)
        if assessment is None:
            if application.risk_engine_version != BUSINESS_BASELINE_ENGINE:
                raise OutcomeConflict(
                    "Unregistered prediction engine lineage is not supported"
                )
            return {
                "model_version_id": None,
                "original_risk_score": float(
                    application.raw_risk_score
                    if application.raw_risk_score is not None
                    else application.risk_score
                ),
                "risk_input_sha256": application.risk_input_sha256,
            }
        model_version = session.get(ModelVersionModel, assessment.model_version_id)
        if model_version is None:
            raise OutcomeConflict("Model-version lineage is missing")
        expected_engine = f"{model_version.model_name}@{model_version.semantic_version}"
        if application.risk_engine_version != expected_engine:
            raise OutcomeConflict(
                "Request engine lineage disagrees with assessed model version"
            )
        if not math.isclose(
            float(application.risk_score), float(assessment.risk_score),
            rel_tol=0.0, abs_tol=1e-12,
        ):
            raise OutcomeConflict("Request and assessment scores disagree")
        if application.risk_input_sha256 != assessment.input_sha256:
            raise OutcomeConflict("Request and assessment input lineage disagree")
        return {
            "model_version_id": assessment.model_version_id,
            "original_risk_score": float(assessment.risk_score),
            "risk_input_sha256": assessment.input_sha256,
        }

    @staticmethod
    def _new_job(
        *,
        scope: str,
        trigger_type: str,
        idempotency_key: uuid.UUID,
        now: datetime,
        outcome_id: uuid.UUID | None = None,
        correction_id: uuid.UUID | None = None,
    ) -> CalibrationJobModel:
        return CalibrationJobModel(
            job_id=uuid.uuid4(), deployment_scope=scope,
            trigger_type=trigger_type, trigger_outcome_id=outcome_id,
            trigger_correction_id=correction_id,
            idempotency_key=idempotency_key, status="queued", attempt_count=0,
            lease_owner=None, leased_until=None, failure_code=None,
            result_run_id=None, created_at=now, started_at=None, completed_at=None,
        )

    @staticmethod
    def _outcome_event(outcome: ActualOutcomeModel) -> dict[str, Any]:
        return {
            "outcome_id": str(outcome.outcome_id),
            "facility_id": str(outcome.facility_id),
            "request_id": str(outcome.request_id),
            "risk_assessment_id": str(outcome.risk_assessment_id),
            "model_version_id": (
                str(outcome.model_version_id)
                if outcome.model_version_id is not None else None
            ),
            "request_risk_engine_version": outcome.risk_engine_version,
            "original_risk_score": outcome.original_risk_score,
            "defaulted": outcome.defaulted,
            "days_past_due": outcome.days_past_due,
            "loss_amount": OutcomeService._money(outcome.loss_amount),
            "evidence_sha256": outcome.evidence_sha256,
            "provenance": outcome.provenance,
        }

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
        *,
        job: CalibrationJobModel | None = None,
    ) -> dict[str, Any]:
        calibration_job = job or self.repository.get_job_by_outcome(
            session, outcome.outcome_id
        )
        if calibration_job is None:
            raise RuntimeError("Outcome calibration job is missing")
        return {
            "outcome": self._serialize_outcome(
                outcome,
                effective_training_eligible=self._is_eligible(session, outcome.outcome_id),
            ),
            "calibration_job": self._serialize_job(calibration_job),
        }

    def _replay_correction(
        self,
        session: Session,
        correction: OutcomeCorrectionModel,
        outcome_id: uuid.UUID,
        request_sha256: str,
    ) -> dict[str, Any]:
        if (
            correction.outcome_id != outcome_id
            or correction.request_sha256 != request_sha256
        ):
            raise OutcomeConflict(
                "Idempotency key was already used for different correction semantics"
            )
        job = self.repository.get_job_by_correction(session, correction.correction_id)
        if job is None:
            raise RuntimeError("Correction calibration job is missing")
        invalidated_ids = [
            uuid.UUID(event.payload["calibration_run_id"])
            for event in session.scalars(
                select(LedgerEventModel)
                .where(
                    LedgerEventModel.entity_id == outcome_id,
                    LedgerEventModel.event_type
                    == "CALIBRATION_DEPLOYMENT_INVALIDATED",
                )
                .order_by(LedgerEventModel.id)
            )
            if event.payload.get("correction_id") == str(correction.correction_id)
        ]
        return self._correction_result(
            correction, job,
            invalidated_run_ids=(
                invalidated_ids if correction.action == "EXCLUDE" else []
            ),
        )

    def _correction_result(
        self,
        correction: OutcomeCorrectionModel,
        job: CalibrationJobModel,
        *,
        invalidated_run_ids: list[uuid.UUID],
    ) -> dict[str, Any]:
        return {
            "correction": self._serialize_correction(correction),
            "effective_training_eligible": correction.action == "REINSTATE",
            "invalidated_run_ids": [str(item) for item in invalidated_run_ids],
            "calibration_job": self._serialize_job(job),
        }

    def _is_eligible(self, session: Session, outcome_id: uuid.UUID) -> bool:
        head = self.repository.get_correction_head(session, outcome_id)
        return head is None or head.action == "REINSTATE"

    def _mark_publication_failed(
        self,
        outcome_id: uuid.UUID,
        run_id: uuid.UUID,
    ) -> datetime:
        with self.session_factory.begin() as session:
            unlocked = self.repository.get_run(session, run_id)
            if unlocked is None:
                raise RuntimeError("Committed calibration run is missing")
            self._acquire_run_lock(session, unlocked.deployment_scope)
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
            unlocked = self.repository.get_run(session, run_id)
            if unlocked is None:
                raise RuntimeError("Committed calibration run is missing")
            scope = unlocked.deployment_scope
            self._acquire_run_lock(session, scope)
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
            if decision.deployment_scope != scope:
                run.deployment_status = "rejected"
                run.activation_reason = "deployment_scope_mismatch"
                self.ledger_repository.append_many(
                    session,
                    run.calibration_run_id,
                    [("CALIBRATION_AUTO_REJECTED", {
                        "calibration_run_id": str(run.calibration_run_id),
                        "deployment_scope": scope,
                        "activation_reason": run.activation_reason,
                        "artifact_integrity": integrity,
                    })],
                )
                return
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
                if not self.repository.run_membership_is_eligible(
                    session, run.calibration_run_id, scope=scope
                ):
                    run.deployment_status = "rejected"
                    run.activation_reason = "outcome_ineligible"
                    event_type = "CALIBRATION_AUTO_REJECTED"
                    payload = {
                        "calibration_run_id": str(run.calibration_run_id),
                        "deployment_scope": scope,
                        "activation_reason": run.activation_reason,
                        "artifact_integrity": integrity,
                    }
                    self.ledger_repository.append_many(
                        session, run.calibration_run_id, [(event_type, payload)]
                    )
                    return
                previous = self.repository.get_active_run(
                    session, scope=scope, for_update=True
                )
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
            unlocked = self.repository.get_run(session, run_id)
            if unlocked is None:
                return
            self._acquire_run_lock(session, unlocked.deployment_scope)
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
    def _candidate_from_run(
        run: CalibrationRunModel,
        memberships: tuple[CalibrationRunObservationModel, ...] = (),
    ) -> CalibrationCandidate:
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
        calibration = load_verified_calibration(
            path,
            expected_sha256=run.artifact_sha256,
            run_id=str(run.calibration_run_id),
            deployment_scope=run.deployment_scope,
            expected_dataset_sha256=run.dataset_sha256,
        )
        schema = artifact.get("artifact_schema")
        dataset = artifact.get("dataset")
        validation = artifact.get("validation")
        if not isinstance(dataset, dict):
            raise ValueError("calibration artifact dataset is invalid")
        if schema == "daibm.platt-calibration.v3":
            if not isinstance(validation, dict):
                raise ValueError("calibration artifact validation is invalid")
            distinct_score_count = int(dataset["distinct_score_count"])
            fold_assignment_sha256 = str(
                validation["fold_assignment_sha256"]
            )
        else:
            distinct_score_count = 0
            fold_assignment_sha256 = ""
        return CalibrationCandidate(
            dataset_sha256=run.dataset_sha256,
            sample_count=run.sample_count,
            positive_count=run.positive_count,
            negative_count=run.negative_count,
            status=run.status,
            slope=calibration.slope,
            intercept=calibration.intercept,
            metrics_before={
                key: float(value) for key, value in run.metrics_before.items()
            },
            metrics_after={
                key: float(value) for key, value in run.metrics_after.items()
            },
            artifact=artifact,
            artifact_bytes=artifact_bytes,
            distinct_score_count=distinct_score_count,
            fold_assignment_sha256=fold_assignment_sha256,
            artifact_sha256=run.artifact_sha256,
            outcome_ids=tuple(str(item.outcome_id) for item in memberships),
            correction_heads=tuple(
                (
                    str(item.outcome_id),
                    (
                        str(item.correction_head_id)
                        if item.correction_head_id is not None
                        else None
                    ),
                )
                for item in memberships
            ),
            configuration=tuple(sorted(run.configuration.items())),
            artifact_schema=run.artifact_schema or "",
            deployment_scope=run.deployment_scope,
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
        return canonical_training_configuration(self.training_config)

    @staticmethod
    def _fallback_summary(
        observations: tuple[CalibrationObservation, ...],
    ) -> CalibrationDatasetSummary:
        ordered = tuple(sorted(observations, key=lambda item: item.outcome_id))
        payload = [asdict(item) for item in ordered]
        positive_count = sum(int(item.defaulted) for item in ordered)
        return CalibrationDatasetSummary(
            dataset_sha256=hashlib.sha256(
                canonical_json(payload).encode("utf-8")
            ).hexdigest(),
            sample_count=len(ordered),
            positive_count=positive_count,
            negative_count=len(ordered) - positive_count,
            metrics_before=None,
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
    def _serialize_outcome(
        outcome: ActualOutcomeModel,
        *,
        effective_training_eligible: bool = True,
    ) -> dict[str, Any]:
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
            "effective_training_eligible": effective_training_eligible,
        }

    @staticmethod
    def _serialize_correction(
        correction: OutcomeCorrectionModel,
    ) -> dict[str, Any]:
        return {
            "correction_id": str(correction.correction_id),
            "outcome_id": str(correction.outcome_id),
            "action": correction.action,
            "reason_code": correction.reason_code,
            "comment": correction.comment,
            "evidence_sha256": correction.evidence_sha256,
            "auditor_user_id": str(correction.auditor_user_id),
            "idempotency_key": str(correction.idempotency_key),
            "recorded_at": canonical_timestamp(correction.recorded_at),
            "effective_training_eligible": correction.action == "REINSTATE",
        }

    @staticmethod
    def _serialize_job(job: CalibrationJobModel) -> dict[str, Any]:
        return {
            "job_id": str(job.job_id),
            "deployment_scope": job.deployment_scope,
            "trigger_type": job.trigger_type,
            "trigger_outcome_id": (
                str(job.trigger_outcome_id)
                if job.trigger_outcome_id is not None else None
            ),
            "trigger_correction_id": (
                str(job.trigger_correction_id)
                if job.trigger_correction_id is not None else None
            ),
            "status": job.status,
            "attempt_count": job.attempt_count,
            "failure_code": job.failure_code,
            "result_run_id": (
                str(job.result_run_id) if job.result_run_id is not None else None
            ),
            "created_at": canonical_timestamp(job.created_at),
            "started_at": (
                canonical_timestamp(job.started_at)
                if job.started_at is not None else None
            ),
            "completed_at": (
                canonical_timestamp(job.completed_at)
                if job.completed_at is not None else None
            ),
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
                "observed_at": canonical_timestamp(payload.observed_at),
                "evidence_sha256": payload.evidence_sha256,
                "provenance": payload.provenance,
            },
        }
        return hashlib.sha256(canonical_json(semantic).encode("utf-8")).hexdigest()

    @staticmethod
    def _correction_sha256(
        outcome_id: uuid.UUID,
        payload: OutcomeCorrectionCreate,
    ) -> str:
        semantic = {
            "outcome_id": str(outcome_id),
            "correction": {
                "action": payload.action,
                "reason_code": payload.reason_code,
                "comment": payload.comment,
                "evidence_sha256": payload.evidence_sha256,
            },
        }
        return hashlib.sha256(canonical_json(semantic).encode("utf-8")).hexdigest()

    @staticmethod
    def _provenance(scope: str) -> str:
        mapping = {
            "controlled_demo": "CONTROLLED_DEMO",
            "external_verified": "EXTERNAL_VERIFIED",
        }
        try:
            return mapping[scope]
        except KeyError as error:
            raise OutcomeConflict("Assessment scope is not deployable") from error

    @staticmethod
    def _scope_for_provenance(provenance: str) -> str:
        mapping = {
            "CONTROLLED_DEMO": "controlled_demo",
            "EXTERNAL_VERIFIED": "external_verified",
        }
        try:
            return mapping[provenance]
        except KeyError as error:
            raise OutcomeConflict("Outcome provenance is not deployable") from error

    def _acquire_run_lock(self, session: Session, scope: str) -> None:
        if scope in {"controlled_demo", "external_verified"}:
            self.repository.acquire_scope_lock(session, scope=scope)
        else:
            # Frozen-schema legacy/mixed candidates cannot collide with a governed
            # deployable scope; retain the Task 3 dataset lock for their recovery.
            self.repository.acquire_training_lock(session)

    @staticmethod
    def _money(value: Decimal) -> str:
        return f"{Decimal(value).quantize(Decimal('0.01')):.2f}"

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
    "DerivedOutcomeFacts",
    "ForbiddenOutcome",
    "OutcomeConflict",
    "OutcomeNotFound",
    "OutcomeService",
    "derive_outcome_facts",
]
