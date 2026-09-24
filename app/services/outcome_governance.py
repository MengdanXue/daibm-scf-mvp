"""Read side of outcome data governance: review queue, eligibility, snapshots, lineage."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.domain.outcome_governance import EXCLUSION_REASONS, training_failure_reason
from app.identity import AuthenticatedUser
from app.ledger import canonical_timestamp
from app.models_governance import OutcomeCorrectionModel
from app.models_model_governance import (
    RiskDecisionRecordModel,
    RiskModelVersionModel,
    TrainingDatasetSnapshotItemModel,
    TrainingDatasetSnapshotModel,
)
from app.models_outcome import ActualOutcomeModel, CalibrationRunModel
from app.repositories.outcomes import OutcomeRepository, is_effective, review_status_expression
from app.services.outcome_eligibility import OutcomeEligibilityService
from app.services.outcomes import ForbiddenOutcome, OutcomeNotFound, OutcomeService

REVIEW_ROLES = {"auditor", "risk_manager"}
SNAPSHOT_ROLES = {"auditor", "risk_manager", "financier"}
STATUSES = ("CREATED", "REVIEWING", "ELIGIBLE", "TRAINING_USED", "REJECTED")


class OutcomeGovernanceService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        outcome_service: OutcomeService,
        repository: OutcomeRepository | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.outcome_service = outcome_service
        self.repository = repository or outcome_service.repository
        self.eligibility: OutcomeEligibilityService = outcome_service.eligibility

    # --- Outcome review ------------------------------------------------------

    def overview(self, user: AuthenticatedUser) -> dict[str, Any]:
        """Headline counts over effective outcomes, plus corrections and snapshots."""

        self._require(user, REVIEW_ROLES)
        with self.session_factory() as session:
            by_status = dict.fromkeys(STATUSES, 0)
            for status, count in session.execute(
                select(review_status_expression().label("status"), func.count())
                .select_from(ActualOutcomeModel)
                .where(is_effective())
                .group_by("status")
            ):
                by_status[status or "CREATED"] = by_status.get(status or "CREATED", 0) + count
            total = session.scalar(select(func.count()).select_from(ActualOutcomeModel)) or 0
            superseded = session.scalar(
                select(func.count())
                .select_from(ActualOutcomeModel)
                .where(ActualOutcomeModel.supersedes_outcome_id.is_not(None))
            ) or 0
            corrections = session.scalar(
                select(func.count()).select_from(OutcomeCorrectionModel)
            ) or 0
            snapshots = session.scalar(
                select(func.count()).select_from(TrainingDatasetSnapshotModel)
            ) or 0
        return {
            "total_records": int(total),
            "effective_outcomes": int(sum(by_status.values())),
            "reviewing": by_status["REVIEWING"] + by_status["CREATED"],
            "eligible": by_status["ELIGIBLE"],
            "training_used": by_status["TRAINING_USED"],
            "rejected": by_status["REJECTED"],
            # Every correction: exclusion/reinstatement events and superseding revisions.
            "corrections": int(corrections) + int(superseded),
            "correction_events": int(corrections),
            "superseding_revisions": int(superseded),
            "dataset_snapshots": int(snapshots),
            "by_status": by_status,
            "exclusion_reason_labels": EXCLUSION_REASONS,
        }

    def review_queue(
        self, user: AuthenticatedUser, *, status: str = "REVIEWING", limit: int = 100
    ) -> list[dict[str, Any]]:
        self._require(user, REVIEW_ROLES)
        if status not in STATUSES:
            raise ValueError("unknown review status")
        with self.session_factory() as session:
            latest = review_status_expression()
            rows = session.scalars(
                select(ActualOutcomeModel)
                .where(is_effective(), latest == status)
                .order_by(ActualOutcomeModel.recorded_at.desc(), ActualOutcomeModel.outcome_id)
                .limit(limit)
            ).all()
            return [
                {
                    "outcome_id": str(item.outcome_id),
                    "facility_id": str(item.facility_id),
                    "revision": item.revision,
                    "provenance": item.provenance,
                    "defaulted": item.defaulted,
                    "days_past_due": item.days_past_due,
                    "loss_amount": f"{item.loss_amount:.2f}",
                    "original_risk_score": item.original_risk_score,
                    "observed_at": canonical_timestamp(item.observed_at),
                    "recorded_at": canonical_timestamp(item.recorded_at),
                    "review_status": status,
                    "review_reason": self.repository.review_status(session, item.outcome_id)[1],
                }
                for item in rows
            ]

    def review_history(self, outcome_id: str | uuid.UUID, user: AuthenticatedUser) -> dict[str, Any]:
        self._require(user, REVIEW_ROLES)
        normalized = _uuid(outcome_id)
        with self.session_factory() as session:
            if self.repository.get_outcome(session, normalized) is None:
                raise OutcomeNotFound(str(outcome_id))
            status, reason = self.repository.review_status(session, normalized)
            return {
                "outcome_id": str(normalized),
                "review_status": status,
                "review_reason": reason,
                "history": self.outcome_service.review_history(session, normalized),
            }

    def eligibility_preview(self, user: AuthenticatedUser, *, scope: str) -> dict[str, Any]:
        """What training would read right now, and why everything else is left out."""

        self._require(user, REVIEW_ROLES)
        with self.session_factory() as session:
            assessment = self.eligibility.assess(session, scope=scope)
            return {
                "scope": scope,
                "dataset_hash": assessment.dataset_hash(),
                "included_count": len(assessment.included),
                "excluded_count": len(assessment.excluded),
                "exclusion_summary": assessment.exclusion_summary(),
                "outcomes": [self.eligibility.serialize_verdict(item) for item in assessment.verdicts],
            }

    # --- Dataset snapshots ---------------------------------------------------

    def list_snapshots(
        self, user: AuthenticatedUser, *, scope: str | None = None
    ) -> list[dict[str, Any]]:
        self._require(user, SNAPSHOT_ROLES)
        with self.session_factory() as session:
            statement = select(TrainingDatasetSnapshotModel).order_by(
                TrainingDatasetSnapshotModel.created_at.desc(),
                TrainingDatasetSnapshotModel.snapshot_id,
            )
            if scope is not None:
                statement = statement.where(TrainingDatasetSnapshotModel.deployment_scope == scope)
            snapshots = list(session.scalars(statement))
            usage = self._usage(session, [item.snapshot_id for item in snapshots])
            return [
                {**self._snapshot_entry(item), **usage.get(item.snapshot_id, _EMPTY_USAGE())}
                for item in snapshots
            ]

    def get_snapshot(
        self, snapshot_id: str | uuid.UUID, user: AuthenticatedUser
    ) -> dict[str, Any]:
        self._require(user, SNAPSHOT_ROLES)
        normalized = _uuid(snapshot_id)
        with self.session_factory() as session:
            snapshot = session.get(TrainingDatasetSnapshotModel, normalized)
            if snapshot is None:
                raise OutcomeNotFound(str(snapshot_id))
            items = session.execute(
                select(TrainingDatasetSnapshotItemModel, ActualOutcomeModel)
                .join(
                    ActualOutcomeModel,
                    ActualOutcomeModel.outcome_id == TrainingDatasetSnapshotItemModel.outcome_id,
                )
                .where(TrainingDatasetSnapshotItemModel.snapshot_id == normalized)
                .order_by(
                    TrainingDatasetSnapshotItemModel.included.desc(),
                    ActualOutcomeModel.observed_at,
                    ActualOutcomeModel.outcome_id,
                )
            ).all()
            usage = self._usage(session, [normalized]).get(normalized, _EMPTY_USAGE())
            return {
                **self._snapshot_entry(snapshot),
                **usage,
                "outcomes": [
                    {
                        "outcome_id": str(outcome.outcome_id),
                        "facility_id": str(outcome.facility_id),
                        "revision": outcome.revision,
                        "defaulted": outcome.defaulted,
                        "original_risk_score": outcome.original_risk_score,
                        "observed_at": canonical_timestamp(outcome.observed_at),
                        "included": item.included,
                        "review_status": item.review_status,
                        "correction_head_id": (
                            str(item.correction_head_id) if item.correction_head_id else None
                        ),
                        "exclusion_reason": item.exclusion_reason,
                    }
                    for item, outcome in items
                ],
            }

    def decision_lineage(
        self, decision_record_id: str | uuid.UUID, user: AuthenticatedUser
    ) -> dict[str, Any]:
        """Risk decision -> model version -> dataset snapshot -> training outcomes."""

        self._require(user, SNAPSHOT_ROLES)
        normalized = _uuid(decision_record_id)
        with self.session_factory() as session:
            record = session.get(RiskDecisionRecordModel, normalized)
            if record is None:
                raise OutcomeNotFound(str(decision_record_id))
            version = (
                session.get(RiskModelVersionModel, record.model_version_id)
                if record.model_version_id is not None
                else None
            )
            snapshot = (
                session.get(TrainingDatasetSnapshotModel, version.dataset_snapshot_id)
                if version is not None and version.dataset_snapshot_id is not None
                else None
            )
            training_outcomes = (
                [
                    str(outcome_id)
                    for outcome_id in session.scalars(
                        select(TrainingDatasetSnapshotItemModel.outcome_id)
                        .where(
                            TrainingDatasetSnapshotItemModel.snapshot_id == snapshot.snapshot_id,
                            TrainingDatasetSnapshotItemModel.included.is_(True),
                        )
                        .order_by(TrainingDatasetSnapshotItemModel.outcome_id)
                    )
                ]
                if snapshot is not None
                else []
            )
            return {
                "decision": {
                    "decision_record_id": str(record.decision_record_id),
                    "request_id": str(record.request_id),
                    "final_score": record.final_score,
                    "band": record.band,
                    "scope_result": record.scope_result,
                    "fallback_code": record.fallback_code,
                    "applied": record.calibration_run_id is not None,
                    "recorded_at": canonical_timestamp(record.recorded_at),
                },
                "model_version": (
                    {
                        "id": str(version.id),
                        "label": f"{version.model_id}@v{version.version}",
                        "status": version.status,
                        "artifact_hash": version.artifact_hash,
                    }
                    if version is not None
                    else None
                ),
                "dataset_snapshot": self._snapshot_entry(snapshot) if snapshot is not None else None,
                "training_outcome_ids": training_outcomes,
            }

    # --- Helpers ---------------------------------------------------------------

    @staticmethod
    def _snapshot_entry(snapshot: TrainingDatasetSnapshotModel) -> dict[str, Any]:
        return {
            "snapshot_id": str(snapshot.snapshot_id),
            "scope": snapshot.deployment_scope,
            "dataset_hash": snapshot.dataset_hash,
            "eligibility_policy": snapshot.eligibility_policy,
            "included_count": snapshot.included_count,
            "excluded_count": snapshot.excluded_count,
            "exclusion_summary": snapshot.exclusion_summary,
            "source": snapshot.source,
            "created_by": snapshot.created_by,
            "trigger_job_id": str(snapshot.trigger_job_id) if snapshot.trigger_job_id else None,
            "created_at": canonical_timestamp(snapshot.created_at),
        }

    @staticmethod
    def _usage(session: Session, snapshot_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict[str, Any]]:
        if not snapshot_ids:
            return {}
        usage: dict[uuid.UUID, dict[str, Any]] = {}
        for version in session.scalars(
            select(RiskModelVersionModel)
            .where(RiskModelVersionModel.dataset_snapshot_id.in_(snapshot_ids))
            .order_by(RiskModelVersionModel.version)
        ):
            assert version.dataset_snapshot_id is not None
            usage.setdefault(version.dataset_snapshot_id, _EMPTY_USAGE())["model_versions"].append(
                {
                    "id": str(version.id),
                    "label": f"{version.model_id}@v{version.version}",
                    "status": version.status,
                }
            )
        for run in session.scalars(
            select(CalibrationRunModel)
            .where(CalibrationRunModel.dataset_snapshot_id.in_(snapshot_ids))
            .order_by(CalibrationRunModel.completed_at)
        ):
            assert run.dataset_snapshot_id is not None
            usage.setdefault(run.dataset_snapshot_id, _EMPTY_USAGE())["training_runs"].append(
                {
                    "calibration_run_id": str(run.calibration_run_id),
                    "status": run.status,
                    "sample_count": run.sample_count,
                    "deployment_status": run.deployment_status,
                    "activation_reason": run.activation_reason,
                    "failure_reason": training_failure_reason(
                        failure_code=run.failure_code, activation_reason=run.activation_reason
                    ),
                }
            )
        return usage

    @staticmethod
    def _require(user: AuthenticatedUser, roles: set[str]) -> None:
        if user.role not in roles:
            raise ForbiddenOutcome("Current role cannot view outcome governance")


def _EMPTY_USAGE() -> dict[str, Any]:
    return {"model_versions": [], "training_runs": []}


def _uuid(value: str | uuid.UUID) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except ValueError as error:
        raise OutcomeNotFound(str(value)) from error



__all__ = ["OutcomeGovernanceService"]
