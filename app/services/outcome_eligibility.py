"""The single entry point through which business outcomes become training data.

Training never reads ``actual_outcomes`` directly. A calibration attempt asks
this service for a :class:`TrainingDatasetSnapshotModel`; the snapshot freezes
which outcomes were included (with their correction head) and which were
excluded and why. The trainer then reads observations *from the snapshot*.

An outcome is included only when all of these hold:

1. it is the effective revision (not superseded by a correction revision);
2. its latest correction is not an exclusion;
3. its review status is ELIGIBLE or TRAINING_USED (reviewed and approved);
4. the database eligibility rules still pass now: scope matches the
   application, the facility lifecycle has ended, the record is complete and
   business-consistent (``outcome_eligibility_reason``).
"""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.governance import TRAINING_ELIGIBLE_STATUSES
from app.domain.outcome_governance import ELIGIBILITY_POLICY, snapshot_hash
from app.ledger import canonical_timestamp
from app.models_model_governance import (
    TrainingDatasetSnapshotItemModel,
    TrainingDatasetSnapshotModel,
)
from app.models_facility import FinancingFacilityModel
from app.models_governance import CalibrationJobModel
from app.models_outcome import ActualOutcomeModel
from app.repositories.outcomes import OutcomeRepository
from app.services.outcome_calibration import CalibrationObservation


@dataclass(frozen=True)
class EligibilityVerdict:
    outcome: ActualOutcomeModel
    correction_head_id: uuid.UUID | None
    review_status: str | None
    exclusion_reason: str | None

    @property
    def eligible(self) -> bool:
        return self.exclusion_reason is None


@dataclass(frozen=True)
class EligibilityAssessment:
    scope: str
    verdicts: tuple[EligibilityVerdict, ...]

    @property
    def included(self) -> tuple[EligibilityVerdict, ...]:
        return tuple(item for item in self.verdicts if item.eligible)

    @property
    def excluded(self) -> tuple[EligibilityVerdict, ...]:
        return tuple(item for item in self.verdicts if not item.eligible)

    def exclusion_summary(self) -> dict[str, int]:
        return dict(sorted(Counter(item.exclusion_reason for item in self.excluded).items()))  # type: ignore[arg-type]

    def dataset_hash(self) -> str:
        return snapshot_hash(
            scope=self.scope,
            policy=ELIGIBILITY_POLICY,
            included=[
                (str(item.outcome.outcome_id), _optional(item.correction_head_id))
                for item in self.included
            ],
            excluded=[
                (str(item.outcome.outcome_id), str(item.exclusion_reason))
                for item in self.excluded
            ],
        )


def exclusion_reason(
    *,
    head_action: str | None,
    review_status: str | None,
    review_reason: str | None,
    rule_reason: str | None,
    effective: bool,
) -> str | None:
    """Why an outcome may not train; None when it may. Order is significant."""

    if not effective:
        return "SUPERSEDED_BY_CORRECTION"
    if head_action == "EXCLUDE":
        return "EXCLUDED_BY_CORRECTION"
    if review_status == "REJECTED":
        return review_reason or "REVIEW_REJECTED"
    if review_status not in TRAINING_ELIGIBLE_STATUSES:
        return "REVIEW_PENDING"
    if rule_reason is not None:
        return rule_reason
    return None


class OutcomeEligibilityService:
    def __init__(self, repository: OutcomeRepository | None = None) -> None:
        self.repository = repository or OutcomeRepository()

    def assess(
        self, session: Session, *, scope: str, organization_id: uuid.UUID | None = None
    ) -> EligibilityAssessment:
        """Eligibility of one organization's outcomes (every organization for None)."""

        verdicts = tuple(
            EligibilityVerdict(
                outcome=outcome,
                correction_head_id=head_id,
                review_status=status,
                exclusion_reason=exclusion_reason(
                    head_action=head_action,
                    review_status=status,
                    review_reason=reason,
                    rule_reason=rule_reason,
                    effective=effective,
                ),
            )
            for outcome, head_id, head_action, status, reason, rule_reason, effective in (
                self.repository.eligibility_population(
                    session, scope=scope, organization_id=organization_id
                )
            )
        )
        return EligibilityAssessment(scope=scope, verdicts=verdicts)

    def create_snapshot(
        self,
        session: Session,
        *,
        scope: str,
        created_by: str,
        trigger_job_id: uuid.UUID | None,
        now: datetime | None = None,
        organization_id: uuid.UUID | None = None,
    ) -> TrainingDatasetSnapshotModel:
        """Freeze the current eligibility of one organization's scope.

        A snapshot never mixes tenants: the owner is the explicit
        organization, else the triggering job's, else the single organization
        of the population. Identical states share one snapshot.
        """

        if organization_id is None and trigger_job_id is not None:
            organization_id = session.scalar(
                select(CalibrationJobModel.organization_id).where(
                    CalibrationJobModel.job_id == trigger_job_id
                )
            )
        if organization_id is None:
            owners = set(
                session.scalars(
                    select(FinancingFacilityModel.organization_id)
                    .join(
                        ActualOutcomeModel,
                        ActualOutcomeModel.facility_id == FinancingFacilityModel.facility_id,
                    )
                    .distinct()
                )
            )
            if len(owners) > 1:
                raise ValueError("a dataset snapshot must name its organization")
            organization_id = next(iter(owners), None)
        assessment = self.assess(session, scope=scope, organization_id=organization_id)
        digest = assessment.dataset_hash()
        existing = session.scalar(
            select(TrainingDatasetSnapshotModel).where(
                TrainingDatasetSnapshotModel.organization_id == organization_id,
                TrainingDatasetSnapshotModel.deployment_scope == scope,
                TrainingDatasetSnapshotModel.dataset_hash == digest,
            )
        )
        if existing is not None:
            return existing
        snapshot = TrainingDatasetSnapshotModel(
            snapshot_id=uuid.uuid4(),
            deployment_scope=scope,
            dataset_hash=digest,
            eligibility_policy=ELIGIBILITY_POLICY,
            included_count=len(assessment.included),
            excluded_count=len(assessment.excluded),
            exclusion_summary=assessment.exclusion_summary(),
            source="calibration_job",
            created_by=created_by,
            trigger_job_id=trigger_job_id,
            created_at=now or datetime.now(timezone.utc),
            organization_id=organization_id,
        )
        session.add(snapshot)
        session.flush()
        session.add_all(
            TrainingDatasetSnapshotItemModel(
                snapshot_id=snapshot.snapshot_id,
                outcome_id=item.outcome.outcome_id,
                included=item.eligible,
                correction_head_id=item.correction_head_id,
                review_status=item.review_status,
                exclusion_reason=item.exclusion_reason,
            )
            for item in assessment.verdicts
        )
        session.flush()
        return snapshot

    def training_observations(
        self, session: Session, snapshot: TrainingDatasetSnapshotModel
    ) -> tuple[CalibrationObservation, ...]:
        """The only training input: the included rows of a frozen snapshot."""

        rows = session.execute(
            select(ActualOutcomeModel, TrainingDatasetSnapshotItemModel.correction_head_id)
            .join(
                TrainingDatasetSnapshotItemModel,
                TrainingDatasetSnapshotItemModel.outcome_id == ActualOutcomeModel.outcome_id,
            )
            .where(
                TrainingDatasetSnapshotItemModel.snapshot_id == snapshot.snapshot_id,
                TrainingDatasetSnapshotItemModel.included.is_(True),
            )
            .order_by(ActualOutcomeModel.outcome_id)
        ).all()
        return tuple(
            observation_for(outcome, correction_head_id=head) for outcome, head in rows
        )

    def snapshot_is_current(
        self, session: Session, snapshot_id: uuid.UUID | None, *, scope: str
    ) -> bool:
        """True while the snapshot's included set is still exactly what is eligible now."""

        if snapshot_id is None:
            return False
        organization_id = session.scalar(
            select(TrainingDatasetSnapshotModel.organization_id).where(
                TrainingDatasetSnapshotModel.snapshot_id == snapshot_id
            )
        )
        frozen = {
            (row.outcome_id, row.correction_head_id)
            for row in session.scalars(
                select(TrainingDatasetSnapshotItemModel).where(
                    TrainingDatasetSnapshotItemModel.snapshot_id == snapshot_id,
                    TrainingDatasetSnapshotItemModel.included.is_(True),
                )
            )
        }
        current = {
            (item.outcome.outcome_id, item.correction_head_id)
            for item in self.assess(
                session, scope=scope, organization_id=organization_id
            ).included
        }
        return bool(current) and frozen == current

    @staticmethod
    def serialize_verdict(item: EligibilityVerdict) -> dict[str, Any]:
        outcome = item.outcome
        return {
            "outcome_id": str(outcome.outcome_id),
            "facility_id": str(outcome.facility_id),
            "revision": outcome.revision,
            "defaulted": outcome.defaulted,
            "original_risk_score": outcome.original_risk_score,
            "observed_at": canonical_timestamp(outcome.observed_at),
            "review_status": item.review_status,
            "correction_head_id": _optional(item.correction_head_id),
            "included": item.eligible,
            "exclusion_reason": item.exclusion_reason,
        }


def observation_for(
    outcome: ActualOutcomeModel, *, correction_head_id: uuid.UUID | None = None
) -> CalibrationObservation:
    return CalibrationObservation(
        outcome_id=str(outcome.outcome_id),
        facility_id=str(outcome.facility_id),
        request_id=str(outcome.request_id),
        risk_assessment_id=str(outcome.risk_assessment_id),
        model_version_id=_optional(outcome.model_version_id),
        risk_engine_version=outcome.risk_engine_version,
        risk_input_sha256=outcome.risk_input_sha256,
        evidence_sha256=outcome.evidence_sha256,
        original_score=outcome.original_risk_score,
        defaulted=outcome.defaulted,
        observed_at=canonical_timestamp(outcome.observed_at),
        provenance=outcome.provenance,
        correction_head_id=_optional(correction_head_id),
    )


def _optional(value: uuid.UUID | None) -> str | None:
    return str(value) if value is not None else None


__all__ = [
    "EligibilityAssessment",
    "EligibilityVerdict",
    "OutcomeEligibilityService",
    "exclusion_reason",
    "observation_for",
]
