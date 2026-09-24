"""Append-only model registry, outcome review and risk decision audit records."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    FetchedValue,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import DOUBLE_PRECISION, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class ModelRegistryEventModel(Base):
    __tablename__ = "model_registry_events"
    __table_args__ = (
        CheckConstraint(
            "model_kind IN ('calibration', 'research')", name="ck_model_registry_events_kind"
        ),
        CheckConstraint(
            "event_type IN ('REGISTERED', 'STATUS_CHANGED', 'VALIDATED', 'PROMOTION_DECISION')",
            name="ck_model_registry_events_type",
        ),
        CheckConstraint(
            "(event_type = 'STATUS_CHANGED') = (to_status IS NOT NULL)",
            name="ck_model_registry_events_status",
        ),
        CheckConstraint(
            "to_status IS NULL OR to_status IN ('DRAFT', 'EVALUATING', 'CANDIDATE', "
            "'ACTIVE', 'ROLLED_BACK', 'RETIRED', 'REJECTED')",
            name="ck_model_registry_events_to_status",
        ),
    )

    event_id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    model_kind: Mapped[str] = mapped_column(Text, nullable=False)
    model_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    from_status: Mapped[str | None] = mapped_column(Text)
    to_status: Mapped[str | None] = mapped_column(Text)
    deployment_scope: Mapped[str | None] = mapped_column(Text)
    dataset_sha256: Mapped[str | None] = mapped_column(Text)
    sample_count: Mapped[int | None] = mapped_column(Integer)
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    artifact_sha256: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="RESTRICT")
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("clock_timestamp()")
    )


Index(
    "ix_model_registry_events_model",
    ModelRegistryEventModel.model_kind,
    ModelRegistryEventModel.model_id,
    ModelRegistryEventModel.event_id,
)
Index("ix_model_registry_events_actor_user_id", ModelRegistryEventModel.actor_user_id)
Index("ix_model_registry_events_recorded_at", ModelRegistryEventModel.recorded_at)


class OutcomeReviewEventModel(Base):
    __tablename__ = "outcome_review_events"
    __table_args__ = (
        CheckConstraint(
            "status IN ('CREATED', 'REVIEWING', 'ELIGIBLE', 'TRAINING_USED', 'REJECTED')",
            name="ck_outcome_review_events_status",
        ),
        CheckConstraint(
            "status <> 'REJECTED' OR reason_code IN ("
            "'SCOPE_MISMATCH', 'DATA_QUALITY_INSUFFICIENT', 'BUSINESS_INCONSISTENT', "
            "'BUSINESS_EXCEPTION', 'MANUAL_CORRECTION', 'SUPERSEDED_BY_CORRECTION', "
            "'QUALITY_ANOMALY')",
            name="ck_outcome_review_events_rejection_reason",
        ),
        CheckConstraint(
            "from_status IS NULL OR from_status IN "
            "('CREATED', 'REVIEWING', 'ELIGIBLE', 'TRAINING_USED', 'REJECTED')",
            name="ck_outcome_review_events_from_status",
        ),
        CheckConstraint(
            "(status = 'TRAINING_USED') = (calibration_run_id IS NOT NULL)",
            name="ck_outcome_review_events_training_run",
        ),
    )

    event_id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    outcome_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("actual_outcomes.outcome_id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    reason_code: Mapped[str | None] = mapped_column(Text)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="RESTRICT")
    )
    calibration_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("calibration_runs.calibration_run_id", ondelete="RESTRICT"),
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("clock_timestamp()")
    )
    # Filled by trigger for every event since revision 20260927_0018.
    from_status: Mapped[str | None] = mapped_column(Text)
    actor_role: Mapped[str | None] = mapped_column(Text)
    comment: Mapped[str | None] = mapped_column(Text)


Index(
    "ix_outcome_review_events_outcome_id",
    OutcomeReviewEventModel.outcome_id,
    OutcomeReviewEventModel.event_id,
)
Index("ix_outcome_review_events_actor_user_id", OutcomeReviewEventModel.actor_user_id)
Index(
    "ix_outcome_review_events_calibration_run_id", OutcomeReviewEventModel.calibration_run_id
)
Index("ix_outcome_review_events_recorded_at", OutcomeReviewEventModel.recorded_at)


class RiskDecisionRecordModel(Base):
    __tablename__ = "risk_decision_records"
    __table_args__ = (
        UniqueConstraint("risk_assessment_id", name="uq_risk_decision_records_assessment"),
        CheckConstraint(
            "scope_result IN ('allow', 'allow_with_warning', 'reject', 'no_active_model')",
            name="ck_risk_decision_records_scope_result",
        ),
        CheckConstraint("band IN ('low', 'medium', 'high')", name="ck_risk_decision_records_band"),
        CheckConstraint(
            "raw_score BETWEEN 0 AND 1 AND final_score BETWEEN 0 AND 1",
            name="ck_risk_decision_records_scores",
        ),
        CheckConstraint(
            "input_sha256 ~ '^[0-9a-f]{64}$' AND (calibration_artifact_sha256 IS NULL "
            "OR calibration_artifact_sha256 ~ '^[0-9a-f]{64}$')",
            name="ck_risk_decision_records_hashes",
        ),
        CheckConstraint(
            "(calibration_run_id IS NULL) = (calibration_artifact_sha256 IS NULL) "
            "AND (calibration_run_id IS NULL OR scope_result IN ('allow', 'allow_with_warning'))",
            name="ck_risk_decision_records_model_contract",
        ),
    )

    decision_record_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("financing_requests.request_id", ondelete="RESTRICT"),
        nullable=False,
    )
    risk_assessment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    assessed_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="RESTRICT"), nullable=False
    )
    actor_role: Mapped[str] = mapped_column(Text, nullable=False)
    request_scope: Mapped[str] = mapped_column(Text, nullable=False)
    input_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    input_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    engine_version: Mapped[str] = mapped_column(Text, nullable=False)
    raw_score: Mapped[float] = mapped_column(DOUBLE_PRECISION, nullable=False)
    final_score: Mapped[float] = mapped_column(DOUBLE_PRECISION, nullable=False)
    band: Mapped[str] = mapped_column(Text, nullable=False)
    calibration_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("calibration_runs.calibration_run_id", ondelete="RESTRICT"),
    )
    calibration_artifact_sha256: Mapped[str | None] = mapped_column(Text)
    model_scope: Mapped[str | None] = mapped_column(Text)
    scope_result: Mapped[str] = mapped_column(Text, nullable=False)
    scope_reason: Mapped[str] = mapped_column(Text, nullable=False)
    attempted_calibration_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("calibration_runs.calibration_run_id", ondelete="RESTRICT"),
    )
    fallback_code: Mapped[str | None] = mapped_column(Text)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    model_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("risk_model_versions.id", ondelete="RESTRICT")
    )


Index("ix_risk_decision_records_model_version_id", RiskDecisionRecordModel.model_version_id)
Index(
    "ix_risk_decision_records_request_id",
    RiskDecisionRecordModel.request_id,
    RiskDecisionRecordModel.recorded_at,
)
Index("ix_risk_decision_records_assessed_by_user_id", RiskDecisionRecordModel.assessed_by_user_id)
Index("ix_risk_decision_records_calibration_run_id", RiskDecisionRecordModel.calibration_run_id)
Index(
    "ix_risk_decision_records_attempted_calibration_run_id",
    RiskDecisionRecordModel.attempted_calibration_run_id,
)
Index("ix_risk_decision_records_recorded_at", RiskDecisionRecordModel.recorded_at)




class RiskModelVersionModel(Base):
    """Registry of record: one governed version per calibration artifact."""

    __tablename__ = "risk_model_versions"
    # organization_id is read on access, not via INSERT ... RETURNING.
    __mapper_args__ = {"eager_defaults": False}
    __table_args__ = (
        UniqueConstraint("model_id", "version", name="uq_risk_model_versions_model_version"),
        UniqueConstraint("calibration_run_id", name="uq_risk_model_versions_artifact"),
        CheckConstraint(
            "status IN ('DRAFT', 'EVALUATING', 'CANDIDATE', 'ACTIVE', 'ROLLED_BACK', "
            "'RETIRED', 'REJECTED')",
            name="ck_risk_model_versions_status",
        ),
        CheckConstraint("model_type IN ('platt_calibration')", name="ck_risk_model_versions_type"),
        CheckConstraint(
            "scope IN ('controlled_demo', 'external_verified', 'mixed')",
            name="ck_risk_model_versions_scope",
        ),
        CheckConstraint(
            "artifact_hash ~ '^[0-9a-f]{64}$' AND training_dataset_version ~ '^[0-9a-f]{64}$'",
            name="ck_risk_model_versions_hashes",
        ),
        CheckConstraint(
            "version >= 1 AND status_sequence >= 1", name="ck_risk_model_versions_counters"
        ),
        CheckConstraint(
            "(evaluation IS NULL) = (evaluation_passed IS NULL) "
            "AND (evaluation IS NULL) = (evaluated_at IS NULL)",
            name="ck_risk_model_versions_evaluation",
        ),
        CheckConstraint(
            "status <> 'ACTIVE' OR (evaluation_passed IS TRUE AND activated_at IS NOT NULL "
            "AND promotion_reason IS NOT NULL)",
            name="ck_risk_model_versions_active_contract",
        ),
        CheckConstraint(
            "status NOT IN ('CANDIDATE', 'ACTIVE') OR evaluation_passed IS TRUE",
            name="ck_risk_model_versions_evaluated_contract",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    model_id: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    model_type: Mapped[str] = mapped_column(Text, nullable=False)
    calibration_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("calibration_runs.calibration_run_id", ondelete="RESTRICT"),
        nullable=False,
    )
    artifact_path: Mapped[str] = mapped_column(Text, nullable=False)
    artifact_hash: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    training_dataset_version: Mapped[str] = mapped_column(Text, nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    evaluation: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    evaluation_passed: Mapped[bool | None] = mapped_column(Boolean)
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text, nullable=False)
    status_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    promotion_reason: Mapped[str | None] = mapped_column(Text)
    previous_active_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("risk_model_versions.id", ondelete="RESTRICT")
    )
    dataset_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("training_dataset_snapshots.snapshot_id", ondelete="RESTRICT"),
        index=True,
    )
    # Owning (lending) organization; the database fills it from the lineage and
    # rejects rows whose lineage belongs to another organization.
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.organization_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        server_default=FetchedValue(),
    )


Index(
    "uq_risk_model_versions_active_scope",
    RiskModelVersionModel.organization_id,
    RiskModelVersionModel.scope,
    unique=True,
    postgresql_where=text("status = 'ACTIVE'"),
)
Index("ix_risk_model_versions_status", RiskModelVersionModel.status)
Index("ix_risk_model_versions_created_by_user_id", RiskModelVersionModel.created_by_user_id)
Index(
    "ix_risk_model_versions_previous_active_version_id",
    RiskModelVersionModel.previous_active_version_id,
)


class RiskModelVersionTransitionModel(Base):
    __tablename__ = "risk_model_version_transitions"
    __table_args__ = (
        CheckConstraint(
            "from_status IS NULL OR from_status IN ('DRAFT', 'EVALUATING', 'CANDIDATE', "
            "'ACTIVE', 'ROLLED_BACK', 'RETIRED', 'REJECTED')",
            name="ck_risk_model_version_transitions_from",
        ),
        CheckConstraint(
            "to_status IN ('DRAFT', 'EVALUATING', 'CANDIDATE', 'ACTIVE', 'ROLLED_BACK', "
            "'RETIRED', 'REJECTED')",
            name="ck_risk_model_version_transitions_to",
        ),
        CheckConstraint(
            "from_status IS DISTINCT FROM to_status",
            name="ck_risk_model_version_transitions_changes",
        ),
        CheckConstraint(
            "to_status <> 'ACTIVE' OR evaluation_metrics IS NOT NULL",
            name="ck_risk_model_version_transitions_activation_evidence",
        ),
        UniqueConstraint(
            "model_version_id",
            "status_sequence",
            name="uq_risk_model_version_transitions_sequence",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    model_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("risk_model_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    from_status: Mapped[str | None] = mapped_column(Text)
    to_status: Mapped[str] = mapped_column(Text, nullable=False)
    status_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="RESTRICT")
    )
    actor_label: Mapped[str] = mapped_column(Text, nullable=False)
    evaluation_metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    artifact_hash: Mapped[str] = mapped_column(Text, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("clock_timestamp()")
    )


Index(
    "ix_risk_model_version_transitions_actor_user_id",
    RiskModelVersionTransitionModel.actor_user_id,
)
Index(
    "ix_risk_model_version_transitions_recorded_at",
    RiskModelVersionTransitionModel.recorded_at,
)


class TrainingDatasetSnapshotModel(Base):
    """Immutable record of exactly what one training attempt was allowed to read."""

    __tablename__ = "training_dataset_snapshots"
    # organization_id is read on access, not via INSERT ... RETURNING.
    __mapper_args__ = {"eager_defaults": False}
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "deployment_scope",
            "dataset_hash",
            name="uq_training_dataset_snapshots_hash",
        ),
        CheckConstraint(
            "deployment_scope IN ('controlled_demo', 'external_verified', 'mixed')",
            name="ck_training_dataset_snapshots_scope",
        ),
        CheckConstraint(
            "dataset_hash ~ '^[0-9a-f]{64}$'", name="ck_training_dataset_snapshots_hash"
        ),
        CheckConstraint(
            "included_count >= 0 AND excluded_count >= 0",
            name="ck_training_dataset_snapshots_counts",
        ),
        CheckConstraint(
            "source IN ('calibration_job', 'migration_backfill')",
            name="ck_training_dataset_snapshots_source",
        ),
    )

    snapshot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    deployment_scope: Mapped[str] = mapped_column(Text, nullable=False)
    dataset_hash: Mapped[str] = mapped_column(Text, nullable=False)
    eligibility_policy: Mapped[str] = mapped_column(Text, nullable=False)
    included_count: Mapped[int] = mapped_column(Integer, nullable=False)
    excluded_count: Mapped[int] = mapped_column(Integer, nullable=False)
    exclusion_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    trigger_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("calibration_jobs.job_id", ondelete="RESTRICT"),
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    # Owning (lending) organization; the database fills it from the lineage and
    # rejects rows whose lineage belongs to another organization.
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.organization_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        server_default=FetchedValue(),
    )


class TrainingDatasetSnapshotItemModel(Base):
    __tablename__ = "training_dataset_snapshot_items"
    __table_args__ = (
        CheckConstraint(
            "included = (exclusion_reason IS NULL)",
            name="ck_training_dataset_snapshot_items_reason",
        ),
    )

    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("training_dataset_snapshots.snapshot_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    outcome_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("actual_outcomes.outcome_id", ondelete="RESTRICT"),
        primary_key=True,
        index=True,
    )
    included: Mapped[bool] = mapped_column(Boolean, nullable=False)
    correction_head_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("outcome_corrections.correction_id", ondelete="RESTRICT"),
        index=True,
    )
    review_status: Mapped[str | None] = mapped_column(Text)
    exclusion_reason: Mapped[str | None] = mapped_column(Text)


__all__ = [
    "ModelRegistryEventModel",
    "OutcomeReviewEventModel",
    "RiskDecisionRecordModel",
    "RiskModelVersionModel",
    "RiskModelVersionTransitionModel",
    "TrainingDatasetSnapshotItemModel",
    "TrainingDatasetSnapshotModel",
]
