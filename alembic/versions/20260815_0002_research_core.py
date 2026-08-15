"""Add Research Core registries and generalize the audit ledger.

Revision ID: 20260815_0002
Revises: 20260814_0001
Create Date: 2026-08-15
"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260815_0002"
down_revision: str | None = "20260814_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


RESEARCH_EVENT_TYPES = (
    "MODEL_INFERENCE_COMPLETED",
    "RISK_POLICY_TRIGGERED",
    "CONTROL_ACTION_REQUESTED",
    "SIMULATED_RISK_INJECTED",
    "INTEGRITY_VIOLATION_DETECTED",
    "LEDGER_RECOVERY_COMPLETED",
)
ALL_EVENT_TYPES = (
    "FINANCING_REQUEST",
    "RISK_ASSESSMENT",
    "FINANCING_DECISION",
    "CONTROL_ACTION",
) + RESEARCH_EVENT_TYPES


def _event_type_check(values: tuple[str, ...]) -> str:
    return "event_type IN (" + ", ".join(f"'{value}'" for value in values) + ")"


def upgrade() -> None:
    op.drop_constraint(
        "ledger_events_entity_id_fkey",
        "ledger_events",
        type_="foreignkey",
    )
    op.drop_constraint(
        "ck_ledger_events_event_type",
        "ledger_events",
        type_="check",
    )
    op.add_column(
        "ledger_events",
        sa.Column(
            "stream_id",
            sa.Text(),
            server_default=sa.text("'global'::text"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_ledger_events_event_type",
        "ledger_events",
        _event_type_check(ALL_EVENT_TYPES),
    )

    op.create_table(
        "dataset_versions",
        sa.Column(
            "dataset_version_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("generation_seed", sa.BigInteger(), nullable=False),
        sa.Column("schema_version", sa.Text(), nullable=False),
        sa.Column("manifest", postgresql.JSONB(), nullable=False),
        sa.Column("content_sha256", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "char_length(content_sha256) = 64",
            name="ck_dataset_versions_content_hash_length",
        ),
        sa.PrimaryKeyConstraint("dataset_version_id"),
        sa.UniqueConstraint("content_sha256"),
        sa.UniqueConstraint(
            "name",
            "version",
            name="uq_dataset_versions_name_version",
        ),
    )

    op.create_table(
        "synthetic_scenarios",
        sa.Column(
            "synthetic_scenario_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "dataset_version_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("overlay", postgresql.JSONB(), nullable=False),
        sa.Column("overlay_sha256", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("creator", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("revision >= 0", name="ck_scenarios_revision"),
        sa.CheckConstraint(
            "status IN ('active', 'superseded')",
            name="ck_scenarios_status",
        ),
        sa.CheckConstraint(
            "char_length(overlay_sha256) = 64",
            name="ck_scenarios_overlay_hash_length",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_version_id"],
            ["dataset_versions.dataset_version_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("synthetic_scenario_id"),
        sa.UniqueConstraint(
            "dataset_version_id",
            "name",
            "revision",
            name="uq_scenarios_dataset_name_revision",
        ),
    )
    op.create_index(
        "ix_synthetic_scenarios_dataset_version_id",
        "synthetic_scenarios",
        ["dataset_version_id"],
    )

    op.create_table(
        "graph_snapshots",
        sa.Column(
            "graph_snapshot_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "dataset_version_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "synthetic_scenario_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("scenario_revision", sa.Integer(), nullable=True),
        sa.Column("overlay_sha256", sa.Text(), nullable=True),
        sa.Column("anchor_month", sa.Integer(), nullable=False),
        sa.Column("window_start_month", sa.Integer(), nullable=False),
        sa.Column("window_end_month", sa.Integer(), nullable=False),
        sa.Column("feature_schema_version", sa.Text(), nullable=False),
        sa.Column("normalization_id", sa.Text(), nullable=False),
        sa.Column("node_ordering_sha256", sa.Text(), nullable=False),
        sa.Column("adjacency_sha256", sa.Text(), nullable=False),
        sa.Column("feature_sha256", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.Text(), nullable=False),
        sa.Column("storage_locator", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "anchor_month BETWEEN 1 AND 24",
            name="ck_graph_snapshots_anchor_month",
        ),
        sa.CheckConstraint(
            "window_start_month >= 1 AND window_end_month >= window_start_month",
            name="ck_graph_snapshots_window",
        ),
        sa.CheckConstraint(
            "char_length(content_sha256) = 64",
            name="ck_graph_snapshots_content_hash_length",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_version_id"],
            ["dataset_versions.dataset_version_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["synthetic_scenario_id"],
            ["synthetic_scenarios.synthetic_scenario_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("graph_snapshot_id"),
        sa.UniqueConstraint("content_sha256"),
    )
    op.create_index(
        "ix_graph_snapshots_dataset_version_id",
        "graph_snapshots",
        ["dataset_version_id"],
    )
    op.create_index(
        "ix_graph_snapshots_scenario_id",
        "graph_snapshots",
        ["synthetic_scenario_id"],
    )

    op.create_table(
        "model_runs",
        sa.Column(
            "model_run_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("model_family", sa.Text(), nullable=False),
        sa.Column("run_seed", sa.BigInteger(), nullable=False),
        sa.Column(
            "dataset_version_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("configuration", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metrics", postgresql.JSONB(), nullable=True),
        sa.Column("logs_locator", sa.Text(), nullable=True),
        sa.Column("failure_summary", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'failed')",
            name="ck_model_runs_status",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_version_id"],
            ["dataset_versions.dataset_version_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("model_run_id"),
    )
    op.create_index(
        "ix_model_runs_dataset_version_id",
        "model_runs",
        ["dataset_version_id"],
    )

    op.create_table(
        "model_versions",
        sa.Column(
            "model_version_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("model_name", sa.Text(), nullable=False),
        sa.Column("semantic_version", sa.Text(), nullable=False),
        sa.Column("model_family", sa.Text(), nullable=False),
        sa.Column(
            "source_run_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "dataset_version_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("feature_schema_version", sa.Text(), nullable=False),
        sa.Column("inference_format", sa.Text(), nullable=False),
        sa.Column("artifact_locator", sa.Text(), nullable=False),
        sa.Column("checkpoint_sha256", sa.Text(), nullable=False),
        sa.Column("metrics", postgresql.JSONB(), nullable=False),
        sa.Column("lifecycle_status", sa.Text(), nullable=False),
        sa.Column("deployment_slot", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("promoted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "lifecycle_status IN ('candidate', 'evaluated', 'promoted', 'retired', 'failed')",
            name="ck_model_versions_lifecycle",
        ),
        sa.CheckConstraint(
            "(lifecycle_status = 'promoted' AND deployment_slot IS NOT NULL) "
            "OR (lifecycle_status <> 'promoted' AND deployment_slot IS NULL)",
            name="ck_model_versions_deployment_slot",
        ),
        sa.CheckConstraint(
            "char_length(checkpoint_sha256) = 64",
            name="ck_model_versions_checkpoint_hash_length",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_version_id"],
            ["dataset_versions.dataset_version_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_run_id"],
            ["model_runs.model_run_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("model_version_id"),
        sa.UniqueConstraint(
            "model_name",
            "semantic_version",
            name="uq_model_versions_name_version",
        ),
    )
    op.create_index(
        "ix_model_versions_source_run_id",
        "model_versions",
        ["source_run_id"],
    )
    op.create_index(
        "ix_model_versions_dataset_version_id",
        "model_versions",
        ["dataset_version_id"],
    )
    op.create_index(
        "uq_model_versions_promoted_slot",
        "model_versions",
        ["deployment_slot"],
        unique=True,
        postgresql_where=sa.text("lifecycle_status = 'promoted'"),
    )

    op.create_table(
        "risk_assessments",
        sa.Column(
            "risk_assessment_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("enterprise_id", sa.Text(), nullable=False),
        sa.Column(
            "graph_snapshot_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "model_version_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("input_sha256", sa.Text(), nullable=False),
        sa.Column(
            "risk_score",
            postgresql.DOUBLE_PRECISION(),
            nullable=False,
        ),
        sa.Column("band", sa.Text(), nullable=False),
        sa.Column("explanations", postgresql.JSONB(), nullable=False),
        sa.Column("inferred_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "risk_score BETWEEN 0 AND 1",
            name="ck_risk_assessments_score",
        ),
        sa.CheckConstraint(
            "char_length(input_sha256) = 64",
            name="ck_risk_assessments_input_hash_length",
        ),
        sa.ForeignKeyConstraint(
            ["graph_snapshot_id"],
            ["graph_snapshots.graph_snapshot_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["model_version_id"],
            ["model_versions.model_version_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("risk_assessment_id"),
    )
    op.create_index(
        "ix_risk_assessments_graph_snapshot_id",
        "risk_assessments",
        ["graph_snapshot_id"],
    )
    op.create_index(
        "ix_risk_assessments_model_version_id",
        "risk_assessments",
        ["model_version_id"],
    )
    op.create_index(
        "ix_risk_assessments_enterprise_id",
        "risk_assessments",
        ["enterprise_id"],
    )

    op.create_table(
        "policy_decisions",
        sa.Column(
            "policy_decision_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "risk_assessment_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("policy_version", sa.Text(), nullable=False),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column(
            "low_threshold",
            postgresql.DOUBLE_PRECISION(),
            nullable=False,
        ),
        sa.Column(
            "high_threshold",
            postgresql.DOUBLE_PRECISION(),
            nullable=False,
        ),
        sa.Column("reason_codes", postgresql.JSONB(), nullable=False),
        sa.Column("permitted_action", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "decision IN ('NORMAL', 'ADDITIONAL_CHECK', 'FINANCING_REVIEW')",
            name="ck_policy_decisions_decision",
        ),
        sa.CheckConstraint(
            "low_threshold >= 0 AND low_threshold < high_threshold "
            "AND high_threshold <= 1",
            name="ck_policy_decisions_thresholds",
        ),
        sa.ForeignKeyConstraint(
            ["risk_assessment_id"],
            ["risk_assessments.risk_assessment_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("policy_decision_id"),
        sa.UniqueConstraint("risk_assessment_id"),
    )

    op.create_table(
        "integrity_incidents",
        sa.Column(
            "integrity_incident_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("affected_ledger_event_id", sa.BigInteger(), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expected_hash", sa.Text(), nullable=False),
        sa.Column("actual_hash", sa.Text(), nullable=False),
        sa.Column("corrupted_payload", postgresql.JSONB(), nullable=False),
        sa.Column("recovery_status", sa.Text(), nullable=False),
        sa.Column("trusted_recovery_source", sa.Text(), nullable=False),
        sa.Column("recovery_method", sa.Text(), nullable=True),
        sa.Column("operator", sa.Text(), nullable=True),
        sa.Column("recovered_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "recovery_status IN ('unresolved', 'recovered')",
            name="ck_integrity_incidents_status",
        ),
        sa.CheckConstraint(
            "char_length(expected_hash) = 64 AND char_length(actual_hash) = 64",
            name="ck_integrity_incidents_hash_lengths",
        ),
        sa.ForeignKeyConstraint(
            ["affected_ledger_event_id"],
            ["ledger_events.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("integrity_incident_id"),
    )
    op.create_index(
        "ix_integrity_incidents_affected_event_id",
        "integrity_incidents",
        ["affected_ledger_event_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_integrity_incidents_affected_event_id",
        table_name="integrity_incidents",
    )
    op.drop_table("integrity_incidents")
    op.drop_table("policy_decisions")
    op.drop_index("ix_risk_assessments_enterprise_id", table_name="risk_assessments")
    op.drop_index("ix_risk_assessments_model_version_id", table_name="risk_assessments")
    op.drop_index("ix_risk_assessments_graph_snapshot_id", table_name="risk_assessments")
    op.drop_table("risk_assessments")
    op.drop_index("uq_model_versions_promoted_slot", table_name="model_versions")
    op.drop_index("ix_model_versions_dataset_version_id", table_name="model_versions")
    op.drop_index("ix_model_versions_source_run_id", table_name="model_versions")
    op.drop_table("model_versions")
    op.drop_index("ix_model_runs_dataset_version_id", table_name="model_runs")
    op.drop_table("model_runs")
    op.drop_index("ix_graph_snapshots_scenario_id", table_name="graph_snapshots")
    op.drop_index("ix_graph_snapshots_dataset_version_id", table_name="graph_snapshots")
    op.drop_table("graph_snapshots")
    op.drop_index(
        "ix_synthetic_scenarios_dataset_version_id",
        table_name="synthetic_scenarios",
    )
    op.drop_table("synthetic_scenarios")
    op.drop_table("dataset_versions")

    op.drop_constraint(
        "ck_ledger_events_event_type",
        "ledger_events",
        type_="check",
    )
    op.drop_column("ledger_events", "stream_id")
    op.create_check_constraint(
        "ck_ledger_events_event_type",
        "ledger_events",
        _event_type_check(ALL_EVENT_TYPES[:4]),
    )
    op.create_foreign_key(
        "ledger_events_entity_id_fkey",
        "ledger_events",
        "financing_requests",
        ["entity_id"],
        ["request_id"],
        ondelete="RESTRICT",
    )
