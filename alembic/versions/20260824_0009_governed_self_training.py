"""Add governed self-training deployment and inference lineage.

Revision ID: 20260824_0009
Revises: 20260824_0008
Create Date: 2026-08-24
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260824_0009"
down_revision: str | None = "20260824_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_PREVIOUS_EVENT_TYPES = (
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
    "ACTUAL_OUTCOME_RECORDED",
    "CALIBRATION_CANDIDATE_TRAINED",
    "CALIBRATION_CANDIDATE_FAILED",
)
_SELF_TRAINING_EVENT_TYPES = (
    "CALIBRATION_AUTO_ACTIVATED",
    "CALIBRATION_AUTO_REJECTED",
    "CALIBRATION_ROLLED_BACK",
    "RISK_CALIBRATION_APPLIED",
    "RISK_CALIBRATION_FALLBACK",
)


def _event_type_check(values: tuple[str, ...]) -> str:
    return "event_type IN (" + ", ".join(f"'{value}'" for value in values) + ")"


def upgrade() -> None:
    op.add_column(
        "calibration_runs",
        sa.Column(
            "deployment_status",
            sa.Text(),
            nullable=False,
            server_default="not_deployed",
        ),
    )
    op.add_column(
        "calibration_runs",
        sa.Column("deployment_scope", sa.Text(), nullable=True),
    )
    op.add_column(
        "calibration_runs",
        sa.Column("activation_mode", sa.Text(), nullable=True),
    )
    op.add_column(
        "calibration_runs",
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "calibration_runs",
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "calibration_runs",
        sa.Column(
            "previous_active_run_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.add_column(
        "calibration_runs",
        sa.Column(
            "activation_reason",
            sa.Text(),
            nullable=False,
            server_default="legacy_not_deployable",
        ),
    )
    op.execute(
        """
        UPDATE calibration_runs AS run
        SET deployment_scope = CASE outcome.provenance
            WHEN 'EXTERNAL_VERIFIED' THEN 'external_verified'
            ELSE 'controlled_demo'
        END
        FROM actual_outcomes AS outcome
        WHERE outcome.outcome_id = run.trigger_outcome_id
        """
    )
    op.alter_column(
        "calibration_runs",
        "deployment_scope",
        existing_type=sa.Text(),
        nullable=False,
    )
    op.create_foreign_key(
        "calibration_runs_previous_active_run_id_fkey",
        "calibration_runs",
        "calibration_runs",
        ["previous_active_run_id"],
        ["calibration_run_id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_calibration_runs_deployment_status",
        "calibration_runs",
        "deployment_status IN ('not_deployed', 'active', 'superseded', "
        "'rejected', 'activation_failed')",
    )
    op.create_check_constraint(
        "ck_calibration_runs_deployment_scope",
        "calibration_runs",
        "deployment_scope IN ('controlled_demo', 'external_verified', 'mixed')",
    )
    op.create_check_constraint(
        "ck_calibration_runs_activation_mode",
        "calibration_runs",
        "activation_mode IS NULL OR activation_mode IN ('automatic', 'manual_rollback')",
    )
    op.create_check_constraint(
        "ck_calibration_runs_deployment_contract",
        "calibration_runs",
        "calibration_run_id IS DISTINCT FROM previous_active_run_id AND ("
        "(deployment_status = 'active' AND status = 'eligible_candidate' "
        "AND activation_mode IS NOT NULL "
        "AND activated_at IS NOT NULL AND deactivated_at IS NULL) OR "
        "(deployment_status = 'superseded' AND status = 'eligible_candidate' "
        "AND activation_mode IS NOT NULL "
        "AND activated_at IS NOT NULL AND deactivated_at IS NOT NULL) OR "
        "(deployment_status IN ('not_deployed', 'rejected', 'activation_failed') "
        "AND activation_mode IS NULL AND activated_at IS NULL "
        "AND deactivated_at IS NULL AND previous_active_run_id IS NULL))",
    )
    op.create_index(
        "ix_calibration_runs_previous_active_run_id",
        "calibration_runs",
        ["previous_active_run_id"],
    )
    op.create_index(
        "uq_calibration_runs_single_active",
        "calibration_runs",
        ["deployment_status"],
        unique=True,
        postgresql_where=sa.text("deployment_status = 'active'"),
    )

    op.add_column(
        "financing_requests",
        sa.Column("raw_risk_score", postgresql.DOUBLE_PRECISION(), nullable=True),
    )
    op.add_column(
        "financing_requests",
        sa.Column(
            "calibration_run_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.add_column(
        "financing_requests",
        sa.Column("calibration_fallback_code", sa.Text(), nullable=True),
    )
    op.create_foreign_key(
        "financing_requests_calibration_run_id_fkey",
        "financing_requests",
        "calibration_runs",
        ["calibration_run_id"],
        ["calibration_run_id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_financing_requests_raw_risk_score",
        "financing_requests",
        "raw_risk_score IS NULL OR raw_risk_score BETWEEN 0 AND 1",
    )
    op.create_check_constraint(
        "ck_financing_requests_calibration_lineage",
        "financing_requests",
        "(calibration_run_id IS NULL OR (raw_risk_score IS NOT NULL "
        "AND calibration_fallback_code IS NULL)) AND "
        "(calibration_fallback_code IS NULL OR (calibration_run_id IS NULL "
        "AND raw_risk_score IS NOT NULL))",
    )
    op.create_index(
        "ix_financing_requests_calibration_run_id",
        "financing_requests",
        ["calibration_run_id"],
    )

    op.drop_constraint(
        "ck_ledger_events_event_type",
        "ledger_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_ledger_events_event_type",
        "ledger_events",
        _event_type_check(_PREVIOUS_EVENT_TYPES + _SELF_TRAINING_EVENT_TYPES),
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.scalar(
        sa.text(
            "SELECT "
            "(SELECT count(*) FROM calibration_runs "
            " WHERE deployment_status <> 'not_deployed') + "
            "(SELECT count(*) FROM financing_requests "
            " WHERE raw_risk_score IS NOT NULL "
            "    OR calibration_run_id IS NOT NULL "
            "    OR calibration_fallback_code IS NOT NULL)"
        )
    ):
        raise RuntimeError(
            "Cannot downgrade while adaptive calibration lineage exists"
        )

    op.drop_constraint(
        "ck_ledger_events_event_type",
        "ledger_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_ledger_events_event_type",
        "ledger_events",
        _event_type_check(_PREVIOUS_EVENT_TYPES),
    )

    op.drop_index(
        "ix_financing_requests_calibration_run_id",
        table_name="financing_requests",
    )
    op.drop_constraint(
        "ck_financing_requests_calibration_lineage",
        "financing_requests",
        type_="check",
    )
    op.drop_constraint(
        "ck_financing_requests_raw_risk_score",
        "financing_requests",
        type_="check",
    )
    op.drop_constraint(
        "financing_requests_calibration_run_id_fkey",
        "financing_requests",
        type_="foreignkey",
    )
    op.drop_column("financing_requests", "calibration_fallback_code")
    op.drop_column("financing_requests", "calibration_run_id")
    op.drop_column("financing_requests", "raw_risk_score")

    op.drop_index(
        "uq_calibration_runs_single_active",
        table_name="calibration_runs",
    )
    op.drop_index(
        "ix_calibration_runs_previous_active_run_id",
        table_name="calibration_runs",
    )
    op.drop_constraint(
        "ck_calibration_runs_deployment_contract",
        "calibration_runs",
        type_="check",
    )
    op.drop_constraint(
        "ck_calibration_runs_activation_mode",
        "calibration_runs",
        type_="check",
    )
    op.drop_constraint(
        "ck_calibration_runs_deployment_scope",
        "calibration_runs",
        type_="check",
    )
    op.drop_constraint(
        "ck_calibration_runs_deployment_status",
        "calibration_runs",
        type_="check",
    )
    op.drop_constraint(
        "calibration_runs_previous_active_run_id_fkey",
        "calibration_runs",
        type_="foreignkey",
    )
    op.drop_column("calibration_runs", "activation_reason")
    op.drop_column("calibration_runs", "previous_active_run_id")
    op.drop_column("calibration_runs", "deactivated_at")
    op.drop_column("calibration_runs", "activated_at")
    op.drop_column("calibration_runs", "activation_mode")
    op.drop_column("calibration_runs", "deployment_scope")
    op.drop_column("calibration_runs", "deployment_status")
