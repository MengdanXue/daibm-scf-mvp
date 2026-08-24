"""Add immutable actual outcomes and candidate-only calibration runs.

Revision ID: 20260824_0007
Revises: 20260824_0006
Create Date: 2026-08-24
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260824_0007"
down_revision: str | None = "20260824_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_PREVIOUS_LEDGER_EVENT_TYPES = (
    "FINANCING_REQUEST",
    "RISK_ASSESSMENT",
    "FINANCING_DECISION",
    "CONTROL_ACTION",
    "MODEL_INFERENCE_COMPLETED",
    "RISK_POLICY_TRIGGERED",
    "CONTROL_ACTION_REQUESTED",
    "SIMULATED_RISK_INJECTED",
    "INTEGRITY_VIOLATION_DETECTED",
    "LEDGER_RECOVERY_COMPLETED",
    "APPLICATION_DRAFT_CREATED",
    "APPLICATION_UPDATED",
    "APPLICATION_SUBMITTED",
    "TRADE_CONFIRMED",
    "TRADE_RETURNED",
    "AUDIT_REVIEW_COMPLETED",
    "FACILITY_CREATED",
    "DISBURSEMENT_INITIATED",
    "DISBURSEMENT_CONFIRMED",
    "REPAYMENT_SUBMITTED",
    "REPAYMENT_CONFIRMED",
    "REPAYMENT_REJECTED",
    "FACILITY_MARKED_OVERDUE",
    "FACILITY_REPAID",
    "FACILITY_CLOSED",
)
_OUTCOME_LEDGER_EVENT_TYPES = (
    "ACTUAL_OUTCOME_RECORDED",
    "CALIBRATION_CANDIDATE_TRAINED",
    "CALIBRATION_CANDIDATE_FAILED",
)


def _event_type_check(values: tuple[str, ...]) -> str:
    return "event_type IN (" + ", ".join(f"'{value}'" for value in values) + ")"


def upgrade() -> None:
    op.create_table(
        "actual_outcomes",
        sa.Column("outcome_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "facility_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("financing_facilities.facility_id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "request_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("financing_requests.request_id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "risk_assessment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("risk_assessments.risk_assessment_id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "model_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("model_versions.model_version_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "submitted_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.user_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "idempotency_key", postgresql.UUID(as_uuid=True), nullable=False, unique=True
        ),
        sa.Column("request_sha256", sa.Text(), nullable=False),
        sa.Column("defaulted", sa.Boolean(), nullable=False),
        sa.Column("days_past_due", sa.Integer(), nullable=False),
        sa.Column("loss_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence_sha256", sa.Text(), nullable=False),
        sa.Column("provenance", sa.Text(), nullable=False),
        sa.Column("original_risk_score", postgresql.DOUBLE_PRECISION(), nullable=False),
        sa.Column("risk_input_sha256", sa.Text(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "days_past_due >= 0", name="ck_actual_outcomes_days_past_due"
        ),
        sa.CheckConstraint(
            "loss_amount >= 0", name="ck_actual_outcomes_loss_amount"
        ),
        sa.CheckConstraint(
            "request_sha256 ~ '^[0-9a-f]{64}$' AND "
            "evidence_sha256 ~ '^[0-9a-f]{64}$' AND "
            "risk_input_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_actual_outcomes_hashes",
        ),
        sa.CheckConstraint(
            "provenance IN ('CONTROLLED_DEMO', 'EXTERNAL_VERIFIED')",
            name="ck_actual_outcomes_provenance",
        ),
        sa.CheckConstraint(
            "original_risk_score BETWEEN 0 AND 1",
            name="ck_actual_outcomes_original_score",
        ),
    )
    op.create_index(
        "ix_actual_outcomes_recorded_at",
        "actual_outcomes",
        [sa.text("recorded_at DESC")],
    )
    op.create_index(
        "ix_actual_outcomes_model_version_id",
        "actual_outcomes",
        ["model_version_id"],
    )
    op.execute(
        """
        CREATE FUNCTION reject_actual_outcome_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'actual outcomes are immutable'
                USING ERRCODE = 'integrity_constraint_violation';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_actual_outcomes_immutable
        BEFORE UPDATE OR DELETE ON actual_outcomes
        FOR EACH ROW EXECUTE FUNCTION reject_actual_outcome_mutation()
        """
    )

    op.create_table(
        "calibration_runs",
        sa.Column(
            "calibration_run_id", postgresql.UUID(as_uuid=True), primary_key=True
        ),
        sa.Column(
            "trigger_outcome_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("actual_outcomes.outcome_id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("dataset_sha256", sa.Text(), nullable=False, unique=True),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("positive_count", sa.Integer(), nullable=False),
        sa.Column("negative_count", sa.Integer(), nullable=False),
        sa.Column("metrics_before", postgresql.JSONB()),
        sa.Column("metrics_after", postgresql.JSONB()),
        sa.Column("configuration", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("artifact_locator", sa.Text()),
        sa.Column("artifact_sha256", sa.Text()),
        sa.Column("failure_code", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "sample_count >= 1 AND positive_count >= 0 AND negative_count >= 0 "
            "AND positive_count + negative_count = sample_count",
            name="ck_calibration_runs_counts",
        ),
        sa.CheckConstraint(
            "status IN ('exploratory_candidate', 'eligible_candidate', 'failed')",
            name="ck_calibration_runs_status",
        ),
        sa.CheckConstraint(
            "dataset_sha256 ~ '^[0-9a-f]{64}$' AND "
            "(artifact_sha256 IS NULL OR artifact_sha256 ~ '^[0-9a-f]{64}$')",
            name="ck_calibration_runs_hashes",
        ),
        sa.CheckConstraint(
            "(status = 'failed' AND artifact_locator IS NULL AND "
            "artifact_sha256 IS NULL AND metrics_after IS NULL AND "
            "failure_code IS NOT NULL) OR "
            "(status IN ('exploratory_candidate', 'eligible_candidate') AND "
            "artifact_locator IS NOT NULL AND artifact_sha256 IS NOT NULL AND "
            "metrics_before IS NOT NULL AND metrics_after IS NOT NULL AND "
            "failure_code IS NULL)",
            name="ck_calibration_runs_artifact_contract",
        ),
    )
    op.create_index(
        "ix_calibration_runs_completed_at",
        "calibration_runs",
        [sa.text("completed_at DESC")],
    )
    op.create_index(
        "ix_calibration_runs_status", "calibration_runs", ["status"]
    )

    op.drop_constraint(
        "ck_ledger_events_event_type", "ledger_events", type_="check"
    )
    op.create_check_constraint(
        "ck_ledger_events_event_type",
        "ledger_events",
        _event_type_check(_PREVIOUS_LEDGER_EVENT_TYPES + _OUTCOME_LEDGER_EVENT_TYPES),
    )


def downgrade() -> None:
    event_sql = ", ".join(f"'{value}'" for value in _OUTCOME_LEDGER_EVENT_TYPES)
    bind = op.get_bind()
    if bind.scalar(
        sa.text(
            "SELECT count(*) FROM ledger_events "
            f"WHERE event_type IN ({event_sql})"
        )
    ):
        raise RuntimeError(
            "Cannot downgrade outcome calibration while append-only outcome events exist"
        )
    if bind.scalar(
        sa.text(
            "SELECT (SELECT count(*) FROM actual_outcomes) + "
            "(SELECT count(*) FROM calibration_runs)"
        )
    ):
        raise RuntimeError(
            "Cannot downgrade immutable outcome calibration while immutable outcome data exists"
        )
    op.drop_constraint(
        "ck_ledger_events_event_type", "ledger_events", type_="check"
    )
    op.create_check_constraint(
        "ck_ledger_events_event_type",
        "ledger_events",
        _event_type_check(_PREVIOUS_LEDGER_EVENT_TYPES),
    )
    op.drop_index("ix_calibration_runs_status", table_name="calibration_runs")
    op.drop_index("ix_calibration_runs_completed_at", table_name="calibration_runs")
    op.drop_table("calibration_runs")
    op.execute("DROP TRIGGER trg_actual_outcomes_immutable ON actual_outcomes")
    op.execute("DROP FUNCTION reject_actual_outcome_mutation()")
    op.drop_index("ix_actual_outcomes_model_version_id", table_name="actual_outcomes")
    op.drop_index("ix_actual_outcomes_recorded_at", table_name="actual_outcomes")
    op.drop_table("actual_outcomes")
