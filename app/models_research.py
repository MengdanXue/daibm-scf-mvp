from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import DOUBLE_PRECISION, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class DatasetVersionModel(Base):
    __tablename__ = "dataset_versions"
    __table_args__ = (
        CheckConstraint(
            "char_length(content_sha256) = 64",
            name="ck_dataset_versions_content_hash_length",
        ),
        UniqueConstraint(
            "name",
            "version",
            name="uq_dataset_versions_name_version",
        ),
    )

    dataset_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(Text, nullable=False)
    generation_seed: Mapped[int] = mapped_column(BigInteger, nullable=False)
    schema_version: Mapped[str] = mapped_column(Text, nullable=False)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    content_sha256: Mapped[str] = mapped_column(
        Text, nullable=False, unique=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class SyntheticScenarioModel(Base):
    __tablename__ = "synthetic_scenarios"
    __table_args__ = (
        CheckConstraint("revision >= 0", name="ck_scenarios_revision"),
        CheckConstraint(
            "status IN ('active', 'superseded')",
            name="ck_scenarios_status",
        ),
        CheckConstraint(
            "char_length(overlay_sha256) = 64",
            name="ck_scenarios_overlay_hash_length",
        ),
        UniqueConstraint(
            "dataset_version_id",
            "name",
            "revision",
            name="uq_scenarios_dataset_name_revision",
        ),
    )

    synthetic_scenario_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True
    )
    dataset_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("dataset_versions.dataset_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    overlay: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    overlay_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    creator: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


Index(
    "ix_synthetic_scenarios_dataset_version_id",
    SyntheticScenarioModel.dataset_version_id,
)


class GraphSnapshotModel(Base):
    __tablename__ = "graph_snapshots"
    __table_args__ = (
        CheckConstraint(
            "anchor_month BETWEEN 1 AND 24",
            name="ck_graph_snapshots_anchor_month",
        ),
        CheckConstraint(
            "window_start_month >= 1 AND window_end_month >= window_start_month",
            name="ck_graph_snapshots_window",
        ),
        CheckConstraint(
            "char_length(content_sha256) = 64",
            name="ck_graph_snapshots_content_hash_length",
        ),
    )

    graph_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True
    )
    dataset_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("dataset_versions.dataset_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    synthetic_scenario_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "synthetic_scenarios.synthetic_scenario_id",
            ondelete="RESTRICT",
        ),
    )
    scenario_revision: Mapped[int | None] = mapped_column(Integer)
    overlay_sha256: Mapped[str | None] = mapped_column(Text)
    anchor_month: Mapped[int] = mapped_column(Integer, nullable=False)
    window_start_month: Mapped[int] = mapped_column(Integer, nullable=False)
    window_end_month: Mapped[int] = mapped_column(Integer, nullable=False)
    feature_schema_version: Mapped[str] = mapped_column(Text, nullable=False)
    normalization_id: Mapped[str] = mapped_column(Text, nullable=False)
    node_ordering_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    adjacency_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    feature_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha256: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    storage_locator: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


Index("ix_graph_snapshots_dataset_version_id", GraphSnapshotModel.dataset_version_id)
Index("ix_graph_snapshots_scenario_id", GraphSnapshotModel.synthetic_scenario_id)


class ModelRunModel(Base):
    __tablename__ = "model_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'completed', 'failed')",
            name="ck_model_runs_status",
        ),
    )

    model_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True
    )
    model_family: Mapped[str] = mapped_column(Text, nullable=False)
    run_seed: Mapped[int] = mapped_column(BigInteger, nullable=False)
    dataset_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("dataset_versions.dataset_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    configuration: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    logs_locator: Mapped[str | None] = mapped_column(Text)
    failure_summary: Mapped[str | None] = mapped_column(Text)


Index("ix_model_runs_dataset_version_id", ModelRunModel.dataset_version_id)


class ModelVersionModel(Base):
    __tablename__ = "model_versions"
    __table_args__ = (
        CheckConstraint(
            "lifecycle_status IN ('candidate', 'evaluated', 'promoted', 'retired', 'failed')",
            name="ck_model_versions_lifecycle",
        ),
        CheckConstraint(
            "(lifecycle_status = 'promoted' AND deployment_slot IS NOT NULL) "
            "OR (lifecycle_status <> 'promoted' AND deployment_slot IS NULL)",
            name="ck_model_versions_deployment_slot",
        ),
        CheckConstraint(
            "char_length(checkpoint_sha256) = 64",
            name="ck_model_versions_checkpoint_hash_length",
        ),
        UniqueConstraint(
            "model_name",
            "semantic_version",
            name="uq_model_versions_name_version",
        ),
    )

    model_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True
    )
    model_name: Mapped[str] = mapped_column(Text, nullable=False)
    semantic_version: Mapped[str] = mapped_column(Text, nullable=False)
    model_family: Mapped[str] = mapped_column(Text, nullable=False)
    source_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("model_runs.model_run_id", ondelete="RESTRICT"),
        nullable=False,
    )
    dataset_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("dataset_versions.dataset_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    feature_schema_version: Mapped[str] = mapped_column(Text, nullable=False)
    inference_format: Mapped[str] = mapped_column(Text, nullable=False)
    artifact_locator: Mapped[str] = mapped_column(Text, nullable=False)
    checkpoint_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    lifecycle_status: Mapped[str] = mapped_column(Text, nullable=False)
    deployment_slot: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


Index("ix_model_versions_source_run_id", ModelVersionModel.source_run_id)
Index("ix_model_versions_dataset_version_id", ModelVersionModel.dataset_version_id)
Index(
    "uq_model_versions_promoted_slot",
    ModelVersionModel.deployment_slot,
    unique=True,
    postgresql_where=text("lifecycle_status = 'promoted'"),
)


class RiskAssessmentModel(Base):
    __tablename__ = "risk_assessments"
    __table_args__ = (
        CheckConstraint(
            "risk_score BETWEEN 0 AND 1",
            name="ck_risk_assessments_score",
        ),
        CheckConstraint(
            "char_length(input_sha256) = 64",
            name="ck_risk_assessments_input_hash_length",
        ),
    )

    risk_assessment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True
    )
    enterprise_id: Mapped[str] = mapped_column(Text, nullable=False)
    graph_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("graph_snapshots.graph_snapshot_id", ondelete="RESTRICT"),
        nullable=False,
    )
    model_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("model_versions.model_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    input_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    risk_score: Mapped[float] = mapped_column(
        DOUBLE_PRECISION, nullable=False
    )
    band: Mapped[str] = mapped_column(Text, nullable=False)
    explanations: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False
    )
    inferred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


Index("ix_risk_assessments_graph_snapshot_id", RiskAssessmentModel.graph_snapshot_id)
Index("ix_risk_assessments_model_version_id", RiskAssessmentModel.model_version_id)
Index("ix_risk_assessments_enterprise_id", RiskAssessmentModel.enterprise_id)


class PolicyDecisionModel(Base):
    __tablename__ = "policy_decisions"
    __table_args__ = (
        CheckConstraint(
            "decision IN ('NORMAL', 'ADDITIONAL_CHECK', 'FINANCING_REVIEW')",
            name="ck_policy_decisions_decision",
        ),
        CheckConstraint(
            "low_threshold >= 0 AND low_threshold < high_threshold AND high_threshold <= 1",
            name="ck_policy_decisions_thresholds",
        ),
    )

    policy_decision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True
    )
    risk_assessment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("risk_assessments.risk_assessment_id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    policy_version: Mapped[str] = mapped_column(Text, nullable=False)
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    low_threshold: Mapped[float] = mapped_column(
        DOUBLE_PRECISION, nullable=False
    )
    high_threshold: Mapped[float] = mapped_column(
        DOUBLE_PRECISION, nullable=False
    )
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    permitted_action: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class IntegrityIncidentModel(Base):
    __tablename__ = "integrity_incidents"
    __table_args__ = (
        CheckConstraint(
            "recovery_status IN ('unresolved', 'recovered')",
            name="ck_integrity_incidents_status",
        ),
        CheckConstraint(
            "char_length(expected_hash) = 64 AND char_length(actual_hash) = 64",
            name="ck_integrity_incidents_hash_lengths",
        ),
    )

    integrity_incident_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True
    )
    affected_ledger_event_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("ledger_events.id", ondelete="RESTRICT"),
        nullable=False,
    )
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expected_hash: Mapped[str] = mapped_column(Text, nullable=False)
    actual_hash: Mapped[str] = mapped_column(Text, nullable=False)
    corrupted_payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False
    )
    recovery_status: Mapped[str] = mapped_column(Text, nullable=False)
    trusted_recovery_source: Mapped[str] = mapped_column(Text, nullable=False)
    recovery_method: Mapped[str | None] = mapped_column(Text)
    operator: Mapped[str | None] = mapped_column(Text)
    recovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


Index(
    "ix_integrity_incidents_affected_event_id",
    IntegrityIncidentModel.affected_ledger_event_id,
)
