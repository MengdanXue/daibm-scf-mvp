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

from sqlalchemy import select, text
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
from app.models_identity import UserModel
from app.models_model_governance import ModelRegistryEventModel, OutcomeReviewEventModel
from app.models_outcome import ActualOutcomeModel, CalibrationRunModel
from app.models_research import ModelVersionModel, RiskAssessmentModel
from app.domain.model_registry import ModelVersionStatus
from app.domain.outcome_governance import training_failure_reason
from app.repositories.ledger import LedgerRepository
from app.repositories.model_registry import ModelRegistryRepository
from app.repositories.outcomes import OutcomeRepository
from app.schemas_outcome import (
    ActualOutcomeCreate,
    OutcomeCorrectionCreate,
    OutcomeReviewCreate,
    OutcomeSupersedeCreate,
)
from app.services.adaptive_risk import (
    ActivationDecision,
    evaluate_activation_gate,
    independent_validation_evidence,
    is_strictly_newer_candidate,
    load_verified_calibration,
)
from app.services.permissions import PermissionService
from app.services.outcome_eligibility import OutcomeEligibilityService, observation_for
from app.services.outcome_calibration import (
    UnmeasuredCalibrationDataset,
    CalibrationCandidate,
    CalibrationObservation,
    CalibrationTrainingConfig,
    StagedCalibrationArtifact,
    build_calibration_candidate,
    canonical_training_configuration,
    discard_staged_artifact,
    recover_candidate_artifact,
    stage_candidate_artifact,
)


SYSTEM_ACTOR = "system:calibration-worker"
REVIEWER_ROLES = frozenset({"auditor", "risk_manager"})
AWAITING_PROMOTION = "awaiting_manual_promotion"


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
        model_registry: ModelRegistryRepository | None = None,
        auto_promotion: bool = True,
        manual_review: bool = False,
        policy_provider: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.artifact_root = Path(artifact_root)
        self.repository = repository or OutcomeRepository()
        self.model_registry = model_registry or ModelRegistryRepository()
        # Runtime policy comes from the configuration center when wired; the
        # constructor values are the fallback, and assignment pins a value.
        self.policy_provider = policy_provider
        self._policy_defaults = {
            "calibration.auto_promotion": auto_promotion,
            "outcome.manual_review": manual_review,
        }
        self._policy_pinned: dict[str, bool] = {}
        self.eligibility = OutcomeEligibilityService(self.repository)
        self.ledger_repository = ledger_repository or LedgerRepository()
        self.trainer = trainer
        self.artifact_writer = artifact_writer
        self.training_config = training_config or CalibrationTrainingConfig()
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _policy(self, key: str) -> bool:
        if key in self._policy_pinned:
            return self._policy_pinned[key]
        if self.policy_provider is not None:
            values = self.policy_provider()
            if key in values:
                return bool(values[key])
        return bool(self._policy_defaults[key])

    @property
    def auto_promotion(self) -> bool:
        """When false, a validated candidate waits for an auditor's promotion."""

        return self._policy("calibration.auto_promotion")

    @auto_promotion.setter
    def auto_promotion(self, value: bool) -> None:
        self._policy_pinned["calibration.auto_promotion"] = value

    @property
    def manual_review(self) -> bool:
        """When true, outcomes that pass the rules wait in REVIEWING for a reviewer."""

        return self._policy("outcome.manual_review")

    @manual_review.setter
    def manual_review(self, value: bool) -> None:
        self._policy_pinned["outcome.manual_review"] = value

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
            self._apply_review_mode(session)
            replay = self.repository.get_by_idempotency_key(
                session, payload.idempotency_key
            )
            if replay is not None:
                return self._replay(session, replay, normalized_id, request_sha256)

            facility = self.repository.get_facility_for_update(session, normalized_id)
            if facility is None:
                raise OutcomeNotFound(str(normalized_id))
            self._require_visible_facility(session, user, facility)
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
            # Eligible-set writers and activation use one lock order. The
            # facility row is unrelated to calibration ownership; no
            # calibration path acquires it after the scope lock.
            self.repository.acquire_scope_lock(
                session,
                scope=application.assessment_scope,
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
            self._require_visible_facility(session, user, facility)
            application = session.get(FinancingRequestModel, facility.request_id)
            if application is None:
                raise OutcomeConflict("Facility request lineage is missing")
            facts = self._validate_lifecycle_snapshot(session, facility)
            assert facility.closed_at is not None
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
            self._apply_review_mode(session)
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
            self._require_visible_outcome(session, user, outcome)
            scope = self._scope_for_provenance(outcome.provenance)
            self.repository.acquire_scope_lock(session, scope=scope)
            outcome = self.repository.get_outcome_for_update(session, normalized_id)
            if outcome is None:
                raise OutcomeNotFound(str(normalized_id))
            if self._scope_for_provenance(outcome.provenance) != scope:
                raise OutcomeConflict("Outcome provenance changed during correction")
            if self.repository.get_successor(session, outcome.outcome_id) is not None:
                raise OutcomeConflict("Superseded outcomes cannot be corrected; use the effective revision")
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

    def supersede(
        self,
        outcome_id: str | uuid.UUID,
        payload: OutcomeSupersedeCreate,
        user: AuthenticatedUser,
    ) -> dict[str, Any]:
        """Append a corrected revision; the original stays and loses eligibility."""

        self._require_auditor(user)
        normalized_id = self._uuid(outcome_id)
        request_sha256 = hashlib.sha256(
            canonical_json(
                {"supersedes": str(normalized_id), **payload.model_dump(mode="json")}
            ).encode("utf-8")
        ).hexdigest()
        with self.session_factory.begin() as session:
            self._apply_review_mode(session)
            replay = self.repository.get_by_idempotency_key(session, payload.idempotency_key)
            if replay is not None:
                if replay.request_sha256 != request_sha256:
                    raise OutcomeConflict(
                        "Idempotency key was already used for different outcome semantics"
                    )
                return self._result(session, replay)
            original = self.repository.get_outcome(session, normalized_id)
            if original is None:
                raise OutcomeNotFound(str(normalized_id))
            self._require_visible_outcome(session, user, original)
            scope = self._scope_for_provenance(original.provenance)
            self.repository.acquire_scope_lock(session, scope=scope)
            original = self.repository.get_outcome_for_update(session, normalized_id)
            if original is None:
                raise OutcomeNotFound(str(normalized_id))
            if self.repository.get_successor(session, original.outcome_id) is not None:
                raise OutcomeConflict("Only the effective outcome revision can be superseded")
            facility = session.get(FinancingFacilityModel, original.facility_id)
            if facility is None:
                raise OutcomeConflict("Outcome lifecycle lineage is missing")
            loss = Decimal(payload.loss_amount)
            if loss > Decimal(facility.principal):
                raise OutcomeConflict("Corrected loss amount cannot exceed principal")
            now = self._now()
            corrected = self.repository.add_outcome(
                session,
                ActualOutcomeModel(
                    outcome_id=uuid.uuid4(),
                    facility_id=original.facility_id,
                    request_id=original.request_id,
                    risk_assessment_id=original.risk_assessment_id,
                    model_version_id=original.model_version_id,
                    submitted_by_user_id=user.user_id,
                    idempotency_key=payload.idempotency_key,
                    request_sha256=request_sha256,
                    defaulted=payload.defaulted,
                    days_past_due=payload.days_past_due,
                    loss_amount=loss,
                    observed_at=payload.observed_at,
                    evidence_sha256=payload.evidence_sha256,
                    provenance=original.provenance,
                    original_risk_score=original.original_risk_score,
                    risk_engine_version=original.risk_engine_version,
                    risk_input_sha256=original.risk_input_sha256,
                    recorded_at=now,
                    revision=original.revision + 1,
                    supersedes_outcome_id=original.outcome_id,
                    correction_reason_code=payload.reason_code,
                    correction_comment=payload.comment,
                ),
            )
            invalidated = self.repository.invalidate_active_runs_containing(
                session, original.outcome_id, scope=scope, now=now
            )
            events: list[tuple[str, dict[str, Any]]] = [
                (
                    "ACTUAL_OUTCOME_SUPERSEDED",
                    {
                        "superseded_outcome_id": str(original.outcome_id),
                        "effective_outcome_id": str(corrected.outcome_id),
                        "revision": corrected.revision,
                        "reason_code": payload.reason_code,
                        "evidence_sha256": payload.evidence_sha256,
                        "deployment_scope": scope,
                    },
                )
            ]
            events.extend(
                (
                    "CALIBRATION_DEPLOYMENT_INVALIDATED",
                    {
                        "outcome_id": str(original.outcome_id),
                        "calibration_run_id": str(run.calibration_run_id),
                        "deployment_scope": scope,
                        "reason": "outcome_superseded",
                    },
                )
                for run in invalidated
            )
            self.ledger_repository.append_many(session, corrected.outcome_id, events)
            job = self.repository.add_job(
                session,
                self._new_job(
                    scope=scope,
                    trigger_type="outcome_submitted",
                    idempotency_key=payload.idempotency_key,
                    now=now,
                    outcome_id=corrected.outcome_id,
                ),
            )
            return self._result(session, corrected, job=job)

    def lineage(
        self,
        outcome_id: str | uuid.UUID,
        user: AuthenticatedUser,
    ) -> dict[str, Any]:
        """Full audit chain: every revision, its corrections and review history."""

        self._require_auditor(user)
        normalized_id = self._uuid(outcome_id)
        with self.session_factory() as session:
            outcome = self.repository.get_outcome(session, normalized_id)
            if outcome is None:
                raise OutcomeNotFound(str(normalized_id))
            self._require_visible_outcome(session, user, outcome)
            chain = self.repository.list_revision_chain(session, outcome.facility_id)
            reviews = self.repository.list_review_events(
                session, [item.outcome_id for item in chain]
            )
            effective = chain[-1]
            return {
                "facility_id": str(outcome.facility_id),
                "effective_outcome_id": str(effective.outcome_id),
                "revisions": [
                    {
                        "outcome": self._serialize_outcome(
                            item,
                            effective_training_eligible=self._is_eligible(
                                session, item.outcome_id
                            ),
                            review=self.repository.review_status(session, item.outcome_id),
                        ),
                        "is_effective": item.outcome_id == effective.outcome_id,
                        "corrections": [
                            self._serialize_correction(row)
                            for row in self.repository.list_corrections(
                                session, item.outcome_id
                            )
                        ],
                        "review_history": [
                            {
                                "status": event.status,
                                "reason_code": event.reason_code,
                                "calibration_run_id": (
                                    str(event.calibration_run_id)
                                    if event.calibration_run_id is not None
                                    else None
                                ),
                                "recorded_at": canonical_timestamp(event.recorded_at),
                            }
                            for event in reviews
                            if event.outcome_id == item.outcome_id
                        ],
                    }
                    for item in chain
                ],
            }

    def governance_summary(self, user: AuthenticatedUser) -> dict[str, Any]:
        """Outcome counts by review status, rejection reasons and supersession."""

        if user.role not in {"auditor", "risk_manager"}:
            raise ForbiddenOutcome("Only auditors and risk managers can view outcome governance")
        with self.session_factory() as session:
            rows = self.repository.review_summary(
                session, facility_ids=PermissionService.visible_facility_ids(session, user)
            )
        by_scope: dict[str, dict[str, Any]] = {}
        for provenance, status, reason, effective, count in rows:
            scope = self._scope_for_provenance(provenance)
            bucket = by_scope.setdefault(
                scope,
                {
                    "total_records": 0,
                    "effective_outcomes": 0,
                    "superseded_records": 0,
                    "by_status": {},
                    "rejected_reasons": {},
                    "training_eligible": 0,
                },
            )
            bucket["total_records"] += count
            if not effective:
                bucket["superseded_records"] += count
                continue
            bucket["effective_outcomes"] += count
            key = status or "UNREVIEWED"
            bucket["by_status"][key] = bucket["by_status"].get(key, 0) + count
            if status in ("ELIGIBLE", "TRAINING_USED"):
                bucket["training_eligible"] += count
            if status == "REJECTED" and reason:
                bucket["rejected_reasons"][reason] = (
                    bucket["rejected_reasons"].get(reason, 0) + count
                )
        return {"scopes": by_scope}

    def list_corrections(
        self,
        outcome_id: str | uuid.UUID,
        user: AuthenticatedUser,
    ) -> list[dict[str, Any]]:
        self._require_auditor(user)
        normalized_id = self._uuid(outcome_id)
        with self.session_factory() as session:
            outcome = self.repository.get_outcome(session, normalized_id)
            if outcome is None:
                raise OutcomeNotFound(str(normalized_id))
            self._require_visible_outcome(session, user, outcome)
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
            self._require_visible_outcome(session, user, outcome)
            return self._serialize_outcome(
                outcome,
                effective_training_eligible=self._is_eligible(
                    session, outcome.outcome_id
                ),
                review=self.repository.review_status(session, outcome.outcome_id),
            )

    def list_outcomes(
        self,
        user: AuthenticatedUser,
        *,
        limit: int = 50,
        offset: int = 0,
        include_superseded: bool = False,
    ) -> list[dict[str, Any]]:
        """Effective outcomes by default; superseded revisions only on request."""

        self._require_auditor(user)
        self._page(limit, offset)
        with self.session_factory() as session:
            rows = self.repository.list_outcomes_with_eligibility(
                session,
                limit=limit,
                offset=offset,
                include_superseded=include_superseded,
                facility_ids=PermissionService.visible_facility_ids(session, user),
            )
            return [
                self._serialize_outcome(
                    item,
                    effective_training_eligible=eligible,
                    review=(status, reason),
                )
                for item, eligible, status, reason in rows
            ]

    def get_run(
        self,
        run_id: str | uuid.UUID,
        user: AuthenticatedUser,
    ) -> dict[str, Any]:
        self._require_auditor(user)
        with self.session_factory() as session:
            run = self.repository.get_run(session, self._uuid(run_id))
            if run is None or not PermissionService.can_access_organization(
                session, user, run.organization_id
            ):
                raise OutcomeNotFound(str(run_id))
            return self._serialize_run(run, session=session)

    def get_job(
        self,
        job_id: str | uuid.UUID,
        user: AuthenticatedUser,
    ) -> dict[str, Any]:
        self._require_auditor(user)
        with self.session_factory() as session:
            job = self.repository.get_job(session, self._uuid(job_id))
            if job is None or not PermissionService.can_access_organization(
                session, user, job.organization_id
            ):
                raise OutcomeNotFound(str(job_id))
            return self._serialize_job(job)

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
                self._serialize_run(item, session=session)
                for item in self.repository.list_runs(
                    session,
                    limit=limit,
                    offset=offset,
                    visibility=PermissionService.organization_filter(
                        user, CalibrationRunModel.organization_id
                    ),
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
                    if run.trigger_job_id is not None:
                        job = self.repository.get_job(session, run.trigger_job_id)
                        if job is not None and job.status == "failed":
                            self._reject_unrecoverable_deployment(
                                run_id,
                                reason="job_failed",
                            )
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

    def get_active_deployment(
        self,
        user: AuthenticatedUser,
        *,
        scope: str,
        organization_id: str | uuid.UUID | None = None,
    ) -> dict[str, Any]:
        self._require_auditor(user)
        with self.session_factory() as session:
            owner = self.resolve_organization(session, user, organization_id)
            run = self.repository.get_active_run(session, scope=scope, organization_id=owner)
            if run is None:
                raise OutcomeNotFound("active calibration deployment")
            return self._serialize_run(run, session=session)

    def rollback(
        self,
        expected_active_run_id: str | uuid.UUID,
        user: AuthenticatedUser,
        *,
        scope: str,
        reason: str = "manual_rollback",
    ) -> dict[str, Any]:
        self._require_auditor(user)
        expected_id = self._uuid(expected_active_run_id)
        with self.session_factory.begin() as session:
            # The expected run names the organization; an unknown or foreign
            # id is simply "not the active run" of the caller's own lender, so
            # the answer never reveals another organization's runs.
            expected = self.repository.get_run(session, expected_id)
            if expected is not None and PermissionService.can_access_organization(
                session, user, expected.organization_id
            ):
                owner = expected.organization_id
            else:
                owner = self.resolve_organization(session, user, None)
            self.repository.acquire_scope_lock(session, scope=scope)
            current = self.repository.get_active_run(
                session,
                scope=scope,
                organization_id=owner,
                for_update=True,
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
            if (
                restored.deployment_scope != scope
                or restored.organization_id != current.organization_id
            ):
                raise OutcomeConflict("rollback predecessor scope is inconsistent")
            if restored.retired_at is not None:
                raise OutcomeConflict("rollback predecessor has been retired")
            if not self.repository.run_membership_is_eligible(
                session, restored.calibration_run_id, scope=scope
            ):
                raise OutcomeConflict("rollback predecessor membership is no longer eligible")
            try:
                self._candidate_from_run(
                    restored,
                    tuple(
                        self.repository.list_run_membership(session, restored.calibration_run_id)
                    ),
                )
            except (OSError, TypeError, KeyError, ValueError) as error:
                raise OutcomeConflict("rollback predecessor artifact is invalid") from error

            now = self._now()
            self.set_governance_actor(session, user)
            current.deployment_status = "superseded"
            current.deactivated_at = now
            current.rolled_back_at = now
            session.flush()
            restored.deployment_status = "active"
            restored.activation_mode = "manual_rollback"
            restored.activated_at = now
            restored.deactivated_at = None
            restored.activation_reason = reason
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
                            **({} if reason == "manual_rollback" else {"reason": reason}),
                        },
                    )
                ],
            )
            return self._serialize_run(restored, session=session)

    def register_version(
        self,
        run_id: str | uuid.UUID,
        user: AuthenticatedUser,
    ) -> uuid.UUID:
        """Register a published artifact that has no version and evaluate it.

        Evaluation is the same independent-holdout gate the worker applies; a
        passing version becomes CANDIDATE and waits for manual promotion.
        """

        self._require_auditor(user)
        normalized = self._uuid(run_id)
        with self.session_factory.begin() as session:
            unlocked = self.repository.get_run(session, normalized)
            if unlocked is None or not PermissionService.can_access_organization(
                session, user, unlocked.organization_id
            ):
                raise OutcomeNotFound("calibration run")
            self._acquire_run_lock(session, unlocked.deployment_scope)
            run = self.repository.get_run_for_update(session, normalized)
            assert run is not None
            if run.artifact_locator is None or run.artifact_sha256 is None:
                raise OutcomeConflict("calibration run has no published artifact")
            if self.model_registry.get_by_run(session, normalized) is not None:
                raise OutcomeConflict("calibration artifact is already registered")
            self.set_governance_actor(session, user)
            version = self.model_registry.register(
                session,
                run,
                created_by=user.username,
                created_by_user_id=user.user_id,
                reason="manual_registration",
            )
            memberships = tuple(self.repository.list_run_membership(session, normalized))
            try:
                candidate = self._candidate_from_run(run, memberships)
            except (OSError, KeyError, TypeError, ValueError) as error:
                self.model_registry.record_evaluation(
                    session,
                    version,
                    evidence={"artifact_integrity": "invalid", "error": type(error).__name__},
                    passed=False,
                    reason="evaluation_failed:artifact_unverified",
                )
                return version.id
            try:
                self._verify_version_artifact(
                    path=version.artifact_path,
                    expected_sha256=version.artifact_hash,
                    run_id=run.calibration_run_id,
                    scope=run.deployment_scope,
                    dataset_sha256=run.dataset_sha256,
                )
                integrity = "verified"
            except OutcomeConflict:
                integrity = "invalid"
            member_ids = {item.outcome_id for item in memberships}
            provenances = tuple(
                item.provenance
                for item in self.repository.list_all_outcomes(session)
                if item.outcome_id in member_ids
            )
            decision = evaluate_activation_gate(
                candidate, artifact_integrity=integrity, provenances=provenances
            )
            evidence = independent_validation_evidence(candidate)
            reason = decision.reason
            if decision.activate and not evidence["independent"]:
                reason = "validation_not_independent"
            elif decision.deployment_scope != run.deployment_scope:
                reason = "deployment_scope_mismatch"
            elif run.deployment_status != "not_deployed":
                reason = f"artifact_not_deployable:{run.deployment_status}"
            passed = reason == decision.reason and decision.activate
            self.model_registry.record_evaluation(
                session,
                version,
                evidence={
                    **evidence,
                    "gate_reason": decision.reason,
                    "artifact_integrity": integrity,
                    "artifact_sha256": run.artifact_sha256,
                },
                passed=passed,
                reason="evaluation_passed" if passed else f"evaluation_failed:{reason}",
            )
            if passed:
                # A manually registered candidate is never auto-activated.
                run.activation_reason = AWAITING_PROMOTION
            return version.id

    def promote_version(
        self,
        version_id: str | uuid.UUID,
        user: AuthenticatedUser,
        *,
        reason: str,
    ) -> uuid.UUID:
        """Promote an evaluated CANDIDATE to ACTIVE after re-verifying its artifact.

        The previous ACTIVE version of the scope is superseded in the same
        transaction; the promotion transition records the reason, the
        evaluation metrics and the verified artifact hash.
        """

        self._require_auditor(user)
        normalized = self._uuid(version_id)
        with self.session_factory.begin() as session:
            unlocked = self.model_registry.get(session, normalized)
            if unlocked is None or not PermissionService.can_access_organization(
                session, user, unlocked.organization_id
            ):
                raise OutcomeNotFound("model version")
            scope = unlocked.scope
            self._acquire_run_lock(session, scope)
            version = self.model_registry.get(session, normalized, for_update=True)
            assert version is not None
            if version.status != ModelVersionStatus.CANDIDATE.value:
                raise OutcomeConflict(f"model version is {version.status}, not CANDIDATE")
            if not version.evaluation_passed or version.evaluation is None:
                raise OutcomeConflict("model version has no passed evaluation")
            run = self.repository.get_run_for_update(session, version.calibration_run_id)
            if run is None or run.deployment_status != "not_deployed":
                raise OutcomeConflict("model artifact is no longer deployable")
            if run.status != "eligible_candidate" or scope not in (
                "controlled_demo",
                "external_verified",
            ):
                raise OutcomeConflict("model scope is not deployable")
            if not self.repository.run_membership_matches_eligible_snapshot(
                session, run.calibration_run_id, scope=scope
            ):
                raise OutcomeConflict("training data changed since evaluation; retrain")
            actual_hash = self._verify_version_artifact(
                path=version.artifact_path,
                expected_sha256=version.artifact_hash,
                run_id=run.calibration_run_id,
                scope=scope,
                dataset_sha256=version.training_dataset_version,
            )
            previous = self.repository.get_active_run(
                session, scope=scope, organization_id=version.organization_id, for_update=True
            )
            if previous is not None and not is_strictly_newer_candidate(
                run.sample_count, active_sample_count=previous.sample_count
            ):
                raise OutcomeConflict("candidate is not newer than the ACTIVE model")
            now = self._now()
            self.set_governance_actor(session, user)
            if previous is not None:
                previous.deployment_status = "superseded"
                previous.deactivated_at = now
                session.flush()
            self.model_registry.transition(
                session,
                version,
                ModelVersionStatus.ACTIVE,
                reason=reason,
                metrics={
                    "evaluation": version.evaluation,
                    "artifact_sha256_verified": actual_hash,
                },
            )
            run.deployment_status = "active"
            run.activation_mode = "manual_promotion"
            run.activation_reason = reason
            run.activated_at = now
            run.deactivated_at = None
            run.previous_active_run_id = (
                previous.calibration_run_id if previous is not None else None
            )
            session.flush()
            self._registry_event(session, run, "PROMOTION_DECISION", reason="manually_promoted")
            self.ledger_repository.append_many(
                session,
                run.calibration_run_id,
                [
                    (
                        "CALIBRATION_MANUALLY_ACTIVATED",
                        {
                            "calibration_run_id": str(run.calibration_run_id),
                            "model_version_id": str(version.id),
                            "deployment_scope": scope,
                            "promotion_reason": reason,
                            "previous_active_run_id": (
                                str(previous.calibration_run_id)
                                if previous is not None
                                else None
                            ),
                            "artifact_sha256": actual_hash,
                            "promoted_by_user_id": str(user.user_id),
                        },
                    )
                ],
            )
            return version.id

    @staticmethod
    def resolve_organization(
        session: Session, user: AuthenticatedUser, organization_id: str | uuid.UUID | None
    ) -> uuid.UUID:
        """The lending organization a model question is about.

        Explicit ids must be in the caller's scope; without one, the caller's
        only visible lending organization is used.
        """

        if organization_id is not None:
            try:
                owner = uuid.UUID(str(organization_id))
            except ValueError as error:
                raise OutcomeNotFound(str(organization_id)) from error
            if not PermissionService.can_access_organization(session, user, owner):
                raise OutcomeNotFound(str(organization_id))
            return owner
        visible = PermissionService.lending_organizations(session, user)
        if len(visible) != 1:
            raise OutcomeConflict("organization_id is required to choose a lending organization")
        return visible[0]

    @staticmethod
    def _verify_version_artifact(
        *,
        path: str,
        expected_sha256: str,
        run_id: uuid.UUID,
        scope: str,
        dataset_sha256: str,
    ) -> str:
        try:
            actual = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        except OSError as error:
            raise OutcomeConflict("model artifact is unavailable") from error
        if actual != expected_sha256:
            raise OutcomeConflict("model artifact hash does not match the registry")
        try:
            load_verified_calibration(
                Path(path),
                expected_sha256=expected_sha256,
                run_id=str(run_id),
                deployment_scope=scope,
                expected_dataset_sha256=dataset_sha256,
            )
        except ValueError as error:
            raise OutcomeConflict("model artifact failed verification") from error
        return actual

    def _validate_submission_snapshot(
        self,
        session: Session,
        facility: FinancingFacilityModel,
        payload: ActualOutcomeCreate,
    ) -> tuple[DerivedOutcomeFacts, FinancingRequestModel, dict[str, Any]]:
        facts = self._validate_lifecycle_snapshot(session, facility)
        assert facility.closed_at is not None
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
        assert facility.closed_at is not None
        lineage = self._prediction_lineage(session, application)
        if outcome.revision > 1:
            # A superseding revision deliberately corrects lifecycle-derived facts;
            # only its prediction lineage must still match the application.
            facts = DerivedOutcomeFacts(
                defaulted=outcome.defaulted,
                days_past_due=outcome.days_past_due,
                loss_amount=Decimal(outcome.loss_amount),
            )
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
                review=self.repository.review_status(session, outcome.outcome_id),
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
        status, _ = self.repository.review_status(session, outcome_id)
        return (head is None or head.action == "REINSTATE") and status in (
            "ELIGIBLE",
            "TRAINING_USED",
        )

    def _mark_publication_failed(
        self,
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
                run.calibration_run_id,
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
        scope, integrity, decision = self._deployment_decision(
            run_id,
            candidate,
            provenances=provenances,
        )
        with self.session_factory.begin() as session:
            self._acquire_run_lock(session, scope)
            run = self.repository.get_run_for_update(session, run_id)
            if run is None:
                raise RuntimeError("Committed calibration run is missing")
            if (
                run.status not in ("exploratory_candidate", "eligible_candidate")
                or run.failure_code is not None
                or run.artifact_locator is None
                or run.artifact_sha256 is None
                or run.deployment_status not in ("not_deployed", "active")
            ):
                raise RuntimeError("calibration run is no longer deployable")
            if not self.repository.run_membership_matches_eligible_snapshot(
                session,
                run_id,
                scope=scope,
            ):
                if run.deployment_status == "not_deployed":
                    run.deployment_status = "rejected"
                    run.activation_reason = "dataset_snapshot_changed"
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
            self._apply_deployment_decision(
                session,
                run,
                decision=decision,
                integrity=integrity,
                candidate=candidate,
            )

    def _complete_claimed_deployment(
        self,
        *,
        job_id: uuid.UUID,
        worker_id: str,
        run_id: uuid.UUID,
        candidate: CalibrationCandidate,
        provenances: tuple[str, ...],
        now: datetime,
    ) -> None:
        # The caller timestamp is diagnostic/backward-compatible only. Lease
        # fencing must sample time after both advisory and row locks are held.
        del now
        scope, integrity, decision = self._deployment_decision(
            run_id,
            candidate,
            provenances=provenances,
        )
        with self.session_factory.begin() as session:
            self._acquire_run_lock(session, scope)
            job = self.repository.get_job_for_update(session, job_id)
            self.repository._require_job_owner(job, worker_id)
            assert job is not None  # Validated by _require_job_owner.
            if job.deployment_scope != scope:
                raise RuntimeError("calibration job and run scopes differ")
            fenced_now = self._now()
            if job.leased_until is None or job.leased_until <= fenced_now:
                raise RuntimeError("calibration job lease expired")
            run = self.repository.get_run_for_update(session, run_id)
            if run is None:
                raise RuntimeError("Committed calibration run is missing")
            if (
                run.status not in ("exploratory_candidate", "eligible_candidate")
                or run.failure_code is not None
                or run.artifact_locator is None
                or run.artifact_sha256 is None
                or run.deployment_status not in ("not_deployed", "active")
            ):
                raise RuntimeError("calibration run is no longer deployable")
            if not self.repository.run_membership_matches_eligible_snapshot(
                session,
                run_id,
                scope=scope,
            ):
                if run.deployment_status == "not_deployed":
                    run.deployment_status = "rejected"
                    run.activation_reason = "dataset_snapshot_changed"
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
            else:
                self._apply_deployment_decision(
                    session,
                    run,
                    decision=decision,
                    integrity=integrity,
                    candidate=candidate,
                )
            self.repository.complete_job(
                session,
                job_id=job_id,
                worker_id=worker_id,
                result_run_id=run_id,
                now=fenced_now,
            )

    def _deployment_decision(
        self,
        run_id: uuid.UUID,
        candidate: CalibrationCandidate,
        *,
        provenances: tuple[str, ...],
    ) -> tuple[str, str, ActivationDecision]:
        with self.session_factory() as session:
            run = self.repository.get_run(session, run_id)
            if run is None:
                raise RuntimeError("Committed calibration run is missing")
            scope = run.deployment_scope
            integrity = "not_applicable"
            if run.artifact_locator is not None and run.artifact_sha256 is not None:
                try:
                    load_verified_calibration(
                        Path(run.artifact_locator),
                        expected_sha256=run.artifact_sha256,
                        run_id=str(run.calibration_run_id),
                        deployment_scope=scope,
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
        return scope, integrity, decision

    def _apply_deployment_decision(
        self,
        session: Session,
        run: CalibrationRunModel,
        *,
        decision: ActivationDecision,
        integrity: str,
        candidate: CalibrationCandidate,
    ) -> None:
        if run.deployment_status != "not_deployed":
            return
        scope = run.deployment_scope
        payload: dict[str, Any]
        # Promotion evidence is the chronological holdout, never the training fit.
        evidence = independent_validation_evidence(candidate)
        if decision.activate and not evidence["independent"]:
            decision = ActivationDecision(False, "validation_not_independent", scope)
        version = self.model_registry.get_by_run(
            session, run.calibration_run_id
        ) or self.model_registry.register(
            session,
            run,
            created_by=SYSTEM_ACTOR,
            created_by_user_id=None,
        )
        if version.status in (
            ModelVersionStatus.DRAFT.value,
            ModelVersionStatus.EVALUATING.value,
        ):
            passed = decision.activate and decision.deployment_scope == scope
            self.model_registry.record_evaluation(
                session,
                version,
                evidence={
                    **evidence,
                    "gate_reason": decision.reason,
                    "artifact_integrity": integrity,
                    "artifact_sha256": run.artifact_sha256,
                },
                passed=passed,
                reason="evaluation_passed" if passed else f"evaluation_failed:{decision.reason}",
            )
        self._registry_event(
            session,
            run,
            "VALIDATED",
            reason="validation_passed" if decision.activate else "validation_failed",
            metrics={**evidence, "gate_reason": decision.reason, "artifact_integrity": integrity},
        )
        if decision.activate:
            self._registry_event(
                session,
                run,
                "STATUS_CHANGED",
                reason="independent_validation_passed",
                to_status="CANDIDATE",
            )
        if decision.deployment_scope != scope:
            run.deployment_status = "rejected"
            run.activation_reason = "deployment_scope_mismatch"
            event_type = "CALIBRATION_AUTO_REJECTED"
            payload = {
                "calibration_run_id": str(run.calibration_run_id),
                "deployment_scope": scope,
                "activation_reason": run.activation_reason,
                "artifact_integrity": integrity,
            }
        elif not decision.activate:
            run.deployment_status = "rejected"
            run.activation_reason = decision.reason
            event_type = "CALIBRATION_AUTO_REJECTED"
            payload = {
                "calibration_run_id": str(run.calibration_run_id),
                "deployment_scope": scope,
                "activation_reason": decision.reason,
                "sample_count": run.sample_count,
                "positive_count": run.positive_count,
                "negative_count": run.negative_count,
                "artifact_integrity": integrity,
            }
        else:
            now = self._now()
            previous = self.repository.get_active_run(
                session, scope=scope, organization_id=run.organization_id, for_update=True
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
                    "deployment_scope": scope,
                    "activation_reason": run.activation_reason,
                    "active_run_id": str(previous.calibration_run_id),
                    "sample_count": run.sample_count,
                    "active_sample_count": previous.sample_count,
                    "artifact_integrity": integrity,
                }
            elif not self.auto_promotion:
                # Validated but held: the version stays CANDIDATE until an
                # auditor promotes it through the model registry.
                run.activation_reason = AWAITING_PROMOTION
                event_type = ""
                payload = {}
            else:
                if previous is not None:
                    previous.deployment_status = "superseded"
                    previous.deactivated_at = now
                    session.flush()
                run.deployment_status = "active"
                run.activation_mode = "automatic"
                run.activation_reason = decision.reason
                run.activated_at = now
                run.deactivated_at = None
                run.previous_active_run_id = (
                    previous.calibration_run_id if previous is not None else None
                )
                event_type = "CALIBRATION_AUTO_ACTIVATED"
                payload = {
                    "calibration_run_id": str(run.calibration_run_id),
                    "deployment_scope": scope,
                    "activation_reason": decision.reason,
                    "previous_active_run_id": (
                        str(previous.calibration_run_id)
                        if previous is not None else None
                    ),
                    "artifact_sha256": run.artifact_sha256,
                    "sample_count": run.sample_count,
                    "positive_count": run.positive_count,
                    "negative_count": run.negative_count,
                }
        self._registry_event(
            session,
            run,
            "PROMOTION_DECISION",
            reason=(
                "promoted"
                if event_type == "CALIBRATION_AUTO_ACTIVATED"
                else f"not_promoted:{run.activation_reason}"
            ),
        )
        if event_type:
            self.ledger_repository.append_many(
                session,
                run.calibration_run_id,
                [(event_type, payload)],
            )

    def _registry_event(
        self,
        session: Session,
        run: CalibrationRunModel,
        event_type: str,
        *,
        reason: str,
        metrics: dict[str, Any] | None = None,
        to_status: str | None = None,
    ) -> None:
        from_status = None
        if to_status is not None:
            from_status = session.scalar(
                select(ModelRegistryEventModel.to_status)
                .where(
                    ModelRegistryEventModel.model_kind == "calibration",
                    ModelRegistryEventModel.model_id == run.calibration_run_id,
                    ModelRegistryEventModel.event_type == "STATUS_CHANGED",
                )
                .order_by(ModelRegistryEventModel.event_id.desc())
                .limit(1)
            )
        session.add(
            ModelRegistryEventModel(
                model_kind="calibration",
                model_id=run.calibration_run_id,
                event_type=event_type,
                from_status=from_status,
                to_status=to_status,
                deployment_scope=run.deployment_scope,
                dataset_sha256=run.dataset_sha256,
                sample_count=run.sample_count,
                metrics=metrics,
                artifact_sha256=run.artifact_sha256,
                reason=reason,
                actor_user_id=None,
            )
        )
        session.flush()

    @staticmethod
    def _require_visible_facility(
        session: Session, user: AuthenticatedUser, facility: FinancingFacilityModel
    ) -> None:
        """Tenant boundary: another organization's facility is simply not found."""

        if not PermissionService.can_view_facility(session, user, facility):
            raise OutcomeNotFound(str(facility.facility_id))

    @classmethod
    def _require_visible_outcome(
        cls, session: Session, user: AuthenticatedUser, outcome: ActualOutcomeModel
    ) -> None:
        facility = session.get(FinancingFacilityModel, outcome.facility_id)
        if facility is None or not PermissionService.can_view_facility(session, user, facility):
            raise OutcomeNotFound(str(outcome.outcome_id))

    def _apply_review_mode(self, session: Session) -> None:
        """Tell the review trigger whether rule-passing outcomes need a reviewer."""

        if self.manual_review:
            session.execute(text("SELECT set_config('daibm.outcome_review_mode', 'manual', true)"))

    def review(
        self,
        outcome_id: str | uuid.UUID,
        payload: OutcomeReviewCreate,
        user: AuthenticatedUser,
    ) -> dict[str, Any]:
        """A reviewer approves a REVIEWING outcome or rejects one not yet trained on."""

        if user.role not in REVIEWER_ROLES:
            raise ForbiddenOutcome("Only auditors and risk managers can review outcomes")
        normalized_id = self._uuid(outcome_id)
        with self.session_factory.begin() as session:
            outcome = self.repository.get_outcome(session, normalized_id)
            if outcome is None:
                raise OutcomeNotFound(str(normalized_id))
            self._require_visible_outcome(session, user, outcome)
            scope = self._scope_for_provenance(outcome.provenance)
            self._acquire_run_lock(session, scope)
            if self.repository.get_successor(session, normalized_id) is not None:
                raise OutcomeConflict("A superseded revision cannot be reviewed")
            status, _ = self.repository.review_status(session, normalized_id)
            now = self._now()
            job = None
            if payload.decision == "APPROVE":
                if status != "REVIEWING":
                    raise OutcomeConflict(f"Only a REVIEWING outcome can be approved (is {status})")
                rule_reason = session.scalar(
                    text("SELECT outcome_eligibility_reason(:id)"), {"id": normalized_id}
                )
                if rule_reason is not None:
                    raise OutcomeConflict(f"Eligibility rules fail: {rule_reason}")
                target, reason_code = "ELIGIBLE", None
            else:
                if status not in ("REVIEWING", "ELIGIBLE"):
                    raise OutcomeConflict(
                        "Only an outcome not yet used for training can be rejected by review; "
                        "use a correction for trained outcomes"
                    )
                target, reason_code = "REJECTED", payload.reason_code
            session.add(
                OutcomeReviewEventModel(
                    outcome_id=normalized_id,
                    status=target,
                    reason_code=reason_code,
                    actor_user_id=user.user_id,
                    comment=payload.comment,
                )
            )
            session.flush()
            if target == "ELIGIBLE" and scope in self.repository.DEPLOYABLE_SCOPES:
                job = self.repository.add_job(
                    session,
                    self._new_job(
                        scope=scope,
                        trigger_type="outcome_reviewed",
                        idempotency_key=uuid.uuid4(),
                        now=now,
                        outcome_id=normalized_id,
                    ),
                )
            self.ledger_repository.append_many(
                session,
                normalized_id,
                [
                    (
                        "ACTUAL_OUTCOME_REVIEWED",
                        {
                            "outcome_id": str(normalized_id),
                            "from_status": status,
                            "to_status": target,
                            "reason_code": reason_code,
                            "comment": payload.comment,
                            "reviewer_user_id": str(user.user_id),
                            "reviewer_role": user.role,
                            "calibration_job_id": str(job.job_id) if job is not None else None,
                        },
                    )
                ],
            )
            return {
                "outcome_id": str(normalized_id),
                "review_status": target,
                "review_reason": reason_code,
                "calibration_job_id": str(job.job_id) if job is not None else None,
                "review_history": self.review_history(session, normalized_id),
            }

    def review_history(self, session: Session, outcome_id: uuid.UUID) -> list[dict[str, Any]]:
        events = self.repository.list_review_events(session, [outcome_id])
        usernames = {
            row.user_id: row.username
            for row in session.scalars(
                select(UserModel).where(
                    UserModel.user_id.in_(
                        [item.actor_user_id for item in events if item.actor_user_id]
                    )
                )
            )
        }
        return [
            {
                "from_status": event.from_status,
                "to_status": event.status,
                "reason_code": event.reason_code,
                "comment": event.comment,
                "operator": usernames.get(event.actor_user_id, "system")
                if event.actor_user_id
                else "system",
                "role": event.actor_role or ("system" if event.actor_user_id is None else None),
                "calibration_run_id": (
                    str(event.calibration_run_id) if event.calibration_run_id else None
                ),
                "recorded_at": canonical_timestamp(event.recorded_at),
            }
            for event in events
        ]

    @staticmethod
    def set_governance_actor(session: Session, user: AuthenticatedUser) -> None:
        """Attribute trigger-written registry events in this transaction."""

        session.execute(
            text("SELECT set_config('daibm.actor_user_id', :actor, true)"),
            {"actor": str(user.user_id)},
        )

    def _reject_unrecoverable_deployment(
        self,
        run_id: uuid.UUID,
        *,
        reason: str = "artifact_unverified",
    ) -> None:
        with self.session_factory.begin() as session:
            unlocked = self.repository.get_run(session, run_id)
            if unlocked is None:
                return
            self._acquire_run_lock(session, unlocked.deployment_scope)
            run = self.repository.get_run_for_update(session, run_id)
            if run is None or run.deployment_status != "not_deployed":
                return
            run.deployment_status = "activation_failed"
            run.activation_reason = reason
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
        if schema in {"daibm.platt-calibration.v3", "daibm.platt-calibration.v4"}:
            if not isinstance(validation, dict):
                raise ValueError("calibration artifact validation is invalid")
            distinct_score_count = int(dataset["distinct_score_count"])
            fold_assignment_sha256 = str(validation.get("fold_assignment_sha256", ""))
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
            metrics_before={key: float(value) for key, value in run.metrics_before.items()},
            metrics_after={key: float(value) for key, value in run.metrics_after.items()},
            artifact=artifact,
            artifact_bytes=artifact_bytes,
            distinct_score_count=distinct_score_count,
            fold_assignment_sha256=fold_assignment_sha256,
            artifact_sha256=run.artifact_sha256,
            outcome_ids=tuple(str(item.outcome_id) for item in memberships),
            correction_heads=tuple(
                (
                    str(item.outcome_id),
                    (str(item.correction_head_id) if item.correction_head_id is not None else None),
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
    def _serialize_outcome(
        outcome: ActualOutcomeModel,
        *,
        effective_training_eligible: bool = True,
        review: tuple[str | None, str | None] = (None, None),
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
            "revision": outcome.revision,
            "supersedes_outcome_id": (
                str(outcome.supersedes_outcome_id)
                if outcome.supersedes_outcome_id is not None
                else None
            ),
            "correction_reason_code": outcome.correction_reason_code,
            "review_status": review[0],
            "review_reason": review[1],
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
            "organization_id": str(job.organization_id),
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

    def _serialize_run(
        self,
        run: CalibrationRunModel,
        *,
        session: Session | None = None,
    ) -> dict[str, Any]:
        fold_assignment_sha256: str | None = None
        temporal_validation: dict[str, Any] | None = None
        if run.artifact_locator is None or run.artifact_sha256 is None:
            integrity = "not_applicable"
        else:
            artifact_path = Path(run.artifact_locator)
            try:
                integrity = recover_candidate_artifact(artifact_path, run.artifact_sha256)
            except OSError:
                integrity = "unavailable"
            if integrity == "verified" and run.artifact_schema in {
                "daibm.platt-calibration.v3",
                "daibm.platt-calibration.v4",
            }:
                try:
                    artifact = json.loads(artifact_path.read_bytes())
                    validation = artifact.get("validation")
                    if isinstance(validation, dict):
                        if run.artifact_schema == "daibm.platt-calibration.v4":
                            temporal_validation = validation
                        value = validation.get("fold_assignment_sha256")
                        if isinstance(value, str):
                            fold_assignment_sha256 = value
                except (OSError, TypeError, json.JSONDecodeError):
                    fold_assignment_sha256 = None
        evidence: dict[str, Any] = {}
        if session is not None:
            event = session.scalar(
                select(LedgerEventModel)
                .where(
                    LedgerEventModel.entity_id == run.calibration_run_id,
                    LedgerEventModel.event_type.in_(
                        (
                            "CALIBRATION_CANDIDATE_TRAINED",
                            "CALIBRATION_CANDIDATE_FAILED",
                        )
                    ),
                )
                .order_by(LedgerEventModel.id.desc())
                .limit(1)
            )
            if event is not None and isinstance(event.payload, dict):
                evidence = event.payload
        eligible_count = evidence.get("eligible_count", run.sample_count)
        excluded_count = evidence.get("excluded_count", 0)
        if temporal_validation is None and isinstance(evidence.get("temporal_validation"), dict):
            temporal_validation = evidence["temporal_validation"]
        if fold_assignment_sha256 is None:
            value = evidence.get("fold_assignment_sha256")
            if isinstance(value, str):
                fold_assignment_sha256 = value
        return {
            "calibration_run_id": str(run.calibration_run_id),
            "organization_id": str(run.organization_id),
            "trigger_outcome_id": (
                str(run.trigger_outcome_id) if run.trigger_outcome_id is not None else None
            ),
            "dataset_sha256": run.dataset_sha256,
            "sample_count": run.sample_count,
            "positive_count": run.positive_count,
            "negative_count": run.negative_count,
            "metrics_before": run.metrics_before,
            "metrics_after": run.metrics_after,
            "oof_metrics_before": run.metrics_before
            if run.artifact_schema == "daibm.platt-calibration.v3"
            else None,
            "oof_metrics_after": run.metrics_after
            if run.artifact_schema == "daibm.platt-calibration.v3"
            else None,
            "temporal_validation": temporal_validation,
            "validation_policy": temporal_validation.get("policy")
            if temporal_validation
            else evidence.get("validation_policy"),
            "configuration": run.configuration,
            "status": run.status,
            "artifact_sha256": run.artifact_sha256,
            "artifact_schema": run.artifact_schema,
            "artifact_integrity": integrity,
            "fold_assignment_sha256": fold_assignment_sha256,
            "eligible_count": int(eligible_count),
            "excluded_count": int(excluded_count),
            "failure_code": run.failure_code,
            "failure_reason": training_failure_reason(
                failure_code=run.failure_code, activation_reason=run.activation_reason
            ),
            "dataset_snapshot_id": (
                str(run.dataset_snapshot_id) if run.dataset_snapshot_id is not None else None
            ),
            "deployment_status": run.deployment_status,
            "deployment_scope": run.deployment_scope,
            "activation_mode": run.activation_mode,
            "activation_reason": run.activation_reason,
            "activated_at": (
                canonical_timestamp(run.activated_at) if run.activated_at is not None else None
            ),
            "deactivated_at": (
                canonical_timestamp(run.deactivated_at) if run.deactivated_at is not None else None
            ),
            "previous_active_run_id": (
                str(run.previous_active_run_id) if run.previous_active_run_id is not None else None
            ),
            "started_at": canonical_timestamp(run.started_at),
            "completed_at": canonical_timestamp(run.completed_at),
        }

    @staticmethod
    def _observation(outcome: ActualOutcomeModel) -> CalibrationObservation:
        return observation_for(outcome)

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
