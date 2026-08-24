from __future__ import annotations

import hashlib
import math
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.identity import AuthenticatedUser
from app.ledger import canonical_json, canonical_timestamp
from app.models import FinancingRequestModel
from app.models_outcome import ActualOutcomeModel, CalibrationRunModel
from app.models_research import RiskAssessmentModel
from app.repositories.ledger import LedgerRepository
from app.repositories.outcomes import OutcomeRepository
from app.schemas_outcome import ActualOutcomeCreate
from app.services.outcome_calibration import (
    CalibrationArtifact,
    CalibrationCandidate,
    CalibrationObservation,
    CalibrationTrainingConfig,
    build_calibration_candidate,
    summarize_calibration_observations,
    verify_candidate_artifact,
    write_candidate_artifact,
)


class OutcomeError(Exception):
    """Base error for immutable actual-outcome operations."""


class OutcomeNotFound(OutcomeError):
    """The requested facility, outcome, or calibration run does not exist."""


class ForbiddenOutcome(OutcomeError):
    """Only auditors may write or inspect actual outcomes."""


class OutcomeConflict(OutcomeError):
    """The outcome conflicts with immutable state or required lineage."""


Trainer = Callable[..., CalibrationCandidate]
ArtifactWriter = Callable[[Path, CalibrationCandidate], CalibrationArtifact]


class OutcomeService:
    """Record immutable outcomes and train candidate-only calibration layers."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        artifact_root: Path,
        repository: OutcomeRepository | None = None,
        ledger_repository: LedgerRepository | None = None,
        trainer: Trainer = build_calibration_candidate,
        artifact_writer: ArtifactWriter = write_candidate_artifact,
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
        with self.session_factory.begin() as session:
            self.repository.acquire_training_lock(session)
            replay = self.repository.get_by_idempotency_key(
                session, payload.idempotency_key
            )
            if replay is not None:
                return self._replay(session, replay, normalized_id, request_sha256)

            facility = self.repository.get_facility_for_update(session, normalized_id)
            if facility is None:
                raise OutcomeNotFound(str(normalized_id))
            existing = self.repository.get_by_facility(session, normalized_id)
            if existing is not None:
                if existing.request_sha256 != request_sha256:
                    raise OutcomeConflict("Facility already has a different actual outcome")
                return self._result(session, existing)
            if facility.status != "closed" or Decimal(facility.outstanding_amount) != Decimal(
                "0.00"
            ):
                raise OutcomeConflict(
                    "Actual outcomes require a closed facility with zero balance"
                )
            if payload.loss_amount > Decimal(facility.principal):
                raise OutcomeConflict("Actual outcome loss amount cannot exceed principal")
            if facility.closed_at is None or payload.observed_at < facility.closed_at:
                raise OutcomeConflict("observed_at cannot precede facility closure")

            application = session.get(FinancingRequestModel, facility.request_id)
            if (
                application is None
                or application.risk_assessment_id is None
                or application.risk_score is None
            ):
                raise OutcomeConflict("Facility request has no risk-assessment lineage")
            assessment = session.get(
                RiskAssessmentModel, application.risk_assessment_id
            )
            if assessment is None:
                raise OutcomeConflict("Risk-assessment lineage is missing")
            if not math.isclose(
                float(application.risk_score),
                float(assessment.risk_score),
                rel_tol=0.0,
                abs_tol=1e-12,
            ):
                raise OutcomeConflict("Request and assessment scores disagree")
            if (
                application.risk_input_sha256 is not None
                and application.risk_input_sha256 != assessment.input_sha256
            ):
                raise OutcomeConflict("Request and assessment input lineage disagree")

            now = self._now()
            outcome = self.repository.add_outcome(
                session,
                ActualOutcomeModel(
                    outcome_id=uuid.uuid4(),
                    facility_id=facility.facility_id,
                    request_id=facility.request_id,
                    risk_assessment_id=assessment.risk_assessment_id,
                    model_version_id=assessment.model_version_id,
                    submitted_by_user_id=user.user_id,
                    idempotency_key=payload.idempotency_key,
                    request_sha256=request_sha256,
                    defaulted=payload.defaulted,
                    days_past_due=payload.days_past_due,
                    loss_amount=payload.loss_amount,
                    observed_at=payload.observed_at,
                    evidence_sha256=payload.evidence_sha256,
                    provenance=payload.provenance,
                    original_risk_score=float(assessment.risk_score),
                    risk_input_sha256=assessment.input_sha256,
                    recorded_at=now,
                ),
            )
            observations = tuple(
                self._observation(item)
                for item in self.repository.list_all_outcomes(session)
            )
            candidate: CalibrationCandidate | None = None
            artifact: CalibrationArtifact | None = None
            failure_code: str | None = None
            summary = summarize_calibration_observations(
                observations,
                config=self.training_config,
            )
            try:
                candidate = self.trainer(observations, config=self.training_config)
            except (ArithmeticError, RuntimeError, ValueError):
                failure_code = "candidate_training_failed"
            if candidate is not None:
                try:
                    artifact = self.artifact_writer(self.artifact_root, candidate)
                except (OSError, RuntimeError, ValueError):
                    failure_code = "artifact_write_failed"

            completed_at = self._now()
            configuration = {
                "epochs": self.training_config.epochs,
                "l2_penalty": self.training_config.l2_penalty,
                "learning_rate": self.training_config.learning_rate,
                "probability_epsilon": self.training_config.probability_epsilon,
            }
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
                        str(artifact.path.resolve()) if artifact is not None else None
                    ),
                    artifact_sha256=(artifact.sha256 if artifact is not None else None),
                    failure_code=failure_code,
                    started_at=now,
                    completed_at=completed_at,
                ),
            )
            events = [
                (
                    "ACTUAL_OUTCOME_RECORDED",
                    {
                        "outcome_id": str(outcome.outcome_id),
                        "facility_id": str(outcome.facility_id),
                        "request_id": str(outcome.request_id),
                        "risk_assessment_id": str(outcome.risk_assessment_id),
                        "model_version_id": str(outcome.model_version_id),
                        "original_risk_score": outcome.original_risk_score,
                        "defaulted": outcome.defaulted,
                        "evidence_sha256": outcome.evidence_sha256,
                        "provenance": outcome.provenance,
                    },
                ),
                (
                    (
                        "CALIBRATION_CANDIDATE_FAILED"
                        if failure_code is not None
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
                        "candidate_only": True,
                        "promotion_status": "not_promoted",
                    },
                ),
            ]
            self.ledger_repository.append_many(session, outcome.outcome_id, events)
            return self._result(session, outcome, run)

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

    @staticmethod
    def _serialize_outcome(outcome: ActualOutcomeModel) -> dict[str, Any]:
        return {
            "outcome_id": str(outcome.outcome_id),
            "facility_id": str(outcome.facility_id),
            "request_id": str(outcome.request_id),
            "risk_assessment_id": str(outcome.risk_assessment_id),
            "model_version_id": str(outcome.model_version_id),
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

    @staticmethod
    def _serialize_run(run: CalibrationRunModel) -> dict[str, Any]:
        if run.artifact_locator is None or run.artifact_sha256 is None:
            integrity = "not_applicable"
        else:
            integrity = verify_candidate_artifact(
                Path(run.artifact_locator), run.artifact_sha256
            )
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
            "promotion_status": "not_promoted",
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
            model_version_id=str(outcome.model_version_id),
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
            "outcome": payload.model_dump(
                mode="json", exclude={"idempotency_key"}
            ),
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
