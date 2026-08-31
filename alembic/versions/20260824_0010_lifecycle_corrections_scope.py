"""Persist governed lifecycle, outcome corrections, and scoped calibration jobs.

Revision ID: 20260824_0010
Revises: 20260824_0009
Create Date: 2026-08-24
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260824_0010"
down_revision: str | None = "20260824_0009"
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
    "CALIBRATION_AUTO_ACTIVATED",
    "CALIBRATION_AUTO_REJECTED",
    "CALIBRATION_ROLLED_BACK",
    "RISK_CALIBRATION_APPLIED",
    "RISK_CALIBRATION_FALLBACK",
)
_GOVERNANCE_EVENT_TYPES = (
    "FACILITY_RESTRUCTURED",
    "FACILITY_DEFAULTED",
    "FACILITY_WRITTEN_OFF",
    "OUTCOME_TRAINING_EXCLUDED",
    "OUTCOME_TRAINING_REINSTATED",
    "CALIBRATION_DEPLOYMENT_INVALIDATED",
)
_DOWNGRADE_GUARDED_TABLES = (
    "ledger_events",
    "financing_requests",
    "financing_facilities",
    "facility_installments",
    "facility_actions",
    "facility_delinquencies",
    "facility_restructures",
    "facility_defaults",
    "facility_writeoffs",
    "outcome_corrections",
    "calibration_jobs",
    "calibration_runs",
    "calibration_run_observations",
)


def _event_type_check(values: tuple[str, ...]) -> str:
    return "event_type IN (" + ", ".join(f"'{value}'" for value in values) + ")"


def _create_immutable_trigger(table_name: str) -> None:
    op.execute(
        f"""
        CREATE TRIGGER trg_{table_name}_immutable
        BEFORE UPDATE OR DELETE ON {table_name}
        FOR EACH ROW EXECUTE FUNCTION reject_governed_history_mutation()
        """
    )


def upgrade() -> None:
    op.add_column(
        "financing_requests",
        sa.Column(
            "assessment_scope",
            sa.Text(),
            nullable=False,
            server_default="controlled_demo",
        ),
    )
    op.create_check_constraint(
        "ck_financing_requests_assessment_scope",
        "financing_requests",
        "assessment_scope IN ('controlled_demo', 'external_verified')",
    )
    op.alter_column(
        "financing_requests",
        "assessment_scope",
        existing_type=sa.Text(),
        server_default=None,
    )

    op.add_column(
        "financing_facilities",
        sa.Column(
            "current_schedule_version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )
    op.add_column(
        "financing_facilities",
        sa.Column("closure_reason", sa.Text(), nullable=True),
    )
    op.create_check_constraint(
        "ck_financing_facilities_schedule_version",
        "financing_facilities",
        "current_schedule_version >= 1",
    )
    op.create_check_constraint(
        "ck_financing_facilities_closure_reason",
        "financing_facilities",
        "closure_reason IS NULL OR closure_reason IN "
        "('repaid', 'settled_after_default', 'written_off')",
    )
    op.drop_constraint(
        "ck_financing_facilities_status",
        "financing_facilities",
        type_="check",
    )
    op.create_check_constraint(
        "ck_financing_facilities_status",
        "financing_facilities",
        "status IN ('ready_for_disbursement', 'disbursed', 'active', "
        "'overdue', 'restructured', 'defaulted', 'repaid', 'written_off', "
        "'closed')",
    )
    op.alter_column(
        "financing_facilities",
        "current_schedule_version",
        existing_type=sa.Integer(),
        server_default=None,
    )

    op.execute(
        """
        CREATE FUNCTION reject_closure_reason_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.closure_reason IS NOT NULL
               AND NEW.closure_reason IS DISTINCT FROM OLD.closure_reason THEN
                RAISE EXCEPTION 'facility closure reason is immutable once set'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_financing_facilities_closure_immutable
        BEFORE UPDATE OF closure_reason ON financing_facilities
        FOR EACH ROW EXECUTE FUNCTION reject_closure_reason_mutation()
        """
    )

    op.add_column(
        "facility_installments",
        sa.Column(
            "schedule_version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )
    op.create_check_constraint(
        "ck_facility_installments_schedule_version",
        "facility_installments",
        "schedule_version >= 1",
    )
    op.drop_constraint(
        "ck_facility_installments_status",
        "facility_installments",
        type_="check",
    )
    op.create_check_constraint(
        "ck_facility_installments_status",
        "facility_installments",
        "status IN ('scheduled', 'partially_paid', 'paid', 'overdue', "
        "'superseded')",
    )
    op.drop_constraint(
        "uq_facility_installments_facility_sequence",
        "facility_installments",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_facility_installments_facility_schedule_sequence",
        "facility_installments",
        ["facility_id", "schedule_version", "sequence"],
    )
    op.alter_column(
        "facility_installments",
        "schedule_version",
        existing_type=sa.Integer(),
        server_default=None,
    )
    op.drop_constraint(
        "ck_facility_actions_action_type",
        "facility_actions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_facility_actions_action_type",
        "facility_actions",
        "action_type IN ('create', 'initiate_disbursement', "
        "'confirm_disbursement', 'submit_payment', 'confirm_payment', "
        "'reject_payment', 'confirm_final_payment', 'mark_overdue', "
        "'restructure', 'declare_default', 'write_off', 'close')",
    )

    lifecycle_tables = (
        (
            "facility_delinquencies",
            (
                sa.Column("delinquency_id", postgresql.UUID(as_uuid=True), primary_key=True),
                sa.Column("facility_id", postgresql.UUID(as_uuid=True), nullable=False),
                sa.Column("marked_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
                sa.Column("days_past_due", sa.Integer(), nullable=False),
                sa.Column("reason_code", sa.Text(), nullable=False),
                sa.Column("comment", sa.Text(), nullable=False),
                sa.Column("evidence_sha256", sa.Text(), nullable=False),
                sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
                sa.ForeignKeyConstraint(["facility_id"], ["financing_facilities.facility_id"], ondelete="RESTRICT"),
                sa.ForeignKeyConstraint(["marked_by_user_id"], ["users.user_id"], ondelete="RESTRICT"),
                sa.CheckConstraint("days_past_due > 0", name="ck_facility_delinquencies_days"),
                sa.CheckConstraint("evidence_sha256 ~ '^[0-9a-f]{64}$'", name="ck_facility_delinquencies_hash"),
            ),
        ),
        (
            "facility_restructures",
            (
                sa.Column("restructure_id", postgresql.UUID(as_uuid=True), primary_key=True),
                sa.Column("facility_id", postgresql.UUID(as_uuid=True), nullable=False),
                sa.Column("restructured_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
                sa.Column("old_schedule_version", sa.Integer(), nullable=False),
                sa.Column("new_schedule_version", sa.Integer(), nullable=False),
                sa.Column("reason_code", sa.Text(), nullable=False),
                sa.Column("comment", sa.Text(), nullable=False),
                sa.Column("evidence_sha256", sa.Text(), nullable=False),
                sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
                sa.ForeignKeyConstraint(["facility_id"], ["financing_facilities.facility_id"], ondelete="RESTRICT"),
                sa.ForeignKeyConstraint(["restructured_by_user_id"], ["users.user_id"], ondelete="RESTRICT"),
                sa.CheckConstraint("old_schedule_version >= 1 AND new_schedule_version = old_schedule_version + 1", name="ck_facility_restructures_versions"),
                sa.CheckConstraint("evidence_sha256 ~ '^[0-9a-f]{64}$'", name="ck_facility_restructures_hash"),
            ),
        ),
        (
            "facility_defaults",
            (
                sa.Column("default_id", postgresql.UUID(as_uuid=True), primary_key=True),
                sa.Column("facility_id", postgresql.UUID(as_uuid=True), nullable=False, unique=True),
                sa.Column("declared_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
                sa.Column("defaulted_at", sa.DateTime(timezone=True), nullable=False),
                sa.Column("days_past_due", sa.Integer(), nullable=False),
                sa.Column("reason_code", sa.Text(), nullable=False),
                sa.Column("comment", sa.Text(), nullable=False),
                sa.Column("evidence_sha256", sa.Text(), nullable=False),
                sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
                sa.ForeignKeyConstraint(["facility_id"], ["financing_facilities.facility_id"], ondelete="RESTRICT"),
                sa.ForeignKeyConstraint(["declared_by_user_id"], ["users.user_id"], ondelete="RESTRICT"),
                sa.CheckConstraint("days_past_due > 0", name="ck_facility_defaults_days"),
                sa.CheckConstraint("evidence_sha256 ~ '^[0-9a-f]{64}$'", name="ck_facility_defaults_hash"),
            ),
        ),
        (
            "facility_writeoffs",
            (
                sa.Column("writeoff_id", postgresql.UUID(as_uuid=True), primary_key=True),
                sa.Column("facility_id", postgresql.UUID(as_uuid=True), nullable=False, unique=True),
                sa.Column("amount", sa.Numeric(14, 2), nullable=False),
                sa.Column("auditor_user_id", postgresql.UUID(as_uuid=True), nullable=False),
                sa.Column("reason_code", sa.Text(), nullable=False),
                sa.Column("comment", sa.Text(), nullable=False),
                sa.Column("evidence_sha256", sa.Text(), nullable=False),
                sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
                sa.ForeignKeyConstraint(["facility_id"], ["financing_facilities.facility_id"], ondelete="RESTRICT"),
                sa.ForeignKeyConstraint(["auditor_user_id"], ["users.user_id"], ondelete="RESTRICT"),
                sa.CheckConstraint("amount > 0", name="ck_facility_writeoffs_amount"),
                sa.CheckConstraint("evidence_sha256 ~ '^[0-9a-f]{64}$'", name="ck_facility_writeoffs_hash"),
            ),
        ),
    )
    for table_name, columns in lifecycle_tables:
        op.create_table(table_name, *columns)

    op.create_index("ix_facility_delinquencies_facility_id", "facility_delinquencies", ["facility_id"])
    op.create_index("ix_facility_delinquencies_marked_by_user_id", "facility_delinquencies", ["marked_by_user_id"])
    op.create_index("ix_facility_delinquencies_recorded_at", "facility_delinquencies", ["recorded_at"])
    op.create_index("ix_facility_restructures_facility_id", "facility_restructures", ["facility_id"])
    op.create_index("ix_facility_restructures_restructured_by_user_id", "facility_restructures", ["restructured_by_user_id"])
    op.create_index("ix_facility_restructures_recorded_at", "facility_restructures", ["recorded_at"])
    op.create_index("ix_facility_defaults_declared_by_user_id", "facility_defaults", ["declared_by_user_id"])
    op.create_index("ix_facility_defaults_recorded_at", "facility_defaults", ["recorded_at"])
    op.create_index("ix_facility_writeoffs_auditor_user_id", "facility_writeoffs", ["auditor_user_id"])
    op.create_index("ix_facility_writeoffs_recorded_at", "facility_writeoffs", ["recorded_at"])

    op.create_table(
        "outcome_corrections",
        sa.Column("correction_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("outcome_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("reason_code", sa.Text(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("evidence_sha256", sa.Text(), nullable=False),
        sa.Column("auditor_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", postgresql.UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("request_sha256", sa.Text(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["outcome_id"], ["actual_outcomes.outcome_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["auditor_user_id"], ["users.user_id"], ondelete="RESTRICT"),
        sa.CheckConstraint("action IN ('EXCLUDE', 'REINSTATE')", name="ck_outcome_corrections_action"),
        sa.CheckConstraint(
            "evidence_sha256 ~ '^[0-9a-f]{64}$' AND request_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_outcome_corrections_hashes",
        ),
    )
    op.create_index("ix_outcome_corrections_outcome_id", "outcome_corrections", ["outcome_id"])
    op.create_index("ix_outcome_corrections_auditor_user_id", "outcome_corrections", ["auditor_user_id"])
    op.create_index("ix_outcome_corrections_recorded_at", "outcome_corrections", ["recorded_at"])

    op.create_table(
        "calibration_jobs",
        sa.Column("job_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("deployment_scope", sa.Text(), nullable=False),
        sa.Column("trigger_type", sa.Text(), nullable=False),
        sa.Column("trigger_outcome_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("trigger_correction_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("idempotency_key", postgresql.UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("lease_owner", sa.Text(), nullable=True),
        sa.Column("leased_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.Text(), nullable=True),
        sa.Column("result_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["trigger_outcome_id"], ["actual_outcomes.outcome_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["trigger_correction_id"], ["outcome_corrections.correction_id"], ondelete="RESTRICT"),
        sa.CheckConstraint("deployment_scope IN ('controlled_demo', 'external_verified')", name="ck_calibration_jobs_deployment_scope"),
        sa.CheckConstraint("trigger_type IN ('outcome_submitted', 'correction_exclude', 'correction_reinstate')", name="ck_calibration_jobs_trigger_type"),
        sa.CheckConstraint(
            "attempt_count BETWEEN 0 AND 3 AND "
            "((trigger_type = 'outcome_submitted' AND trigger_outcome_id IS NOT NULL AND trigger_correction_id IS NULL) OR "
            "(trigger_type IN ('correction_exclude', 'correction_reinstate') AND trigger_outcome_id IS NULL AND trigger_correction_id IS NOT NULL)) AND "
            "((status = 'queued' AND lease_owner IS NULL AND leased_until IS NULL "
            "AND started_at IS NULL AND completed_at IS NULL AND failure_code IS NULL AND result_run_id IS NULL) OR "
            "(status = 'running' AND lease_owner IS NOT NULL AND btrim(lease_owner) <> '' "
            "AND leased_until IS NOT NULL AND started_at IS NOT NULL AND leased_until > started_at "
            "AND completed_at IS NULL AND failure_code IS NULL AND result_run_id IS NULL) OR "
            "(status = 'completed' AND lease_owner IS NULL AND leased_until IS NULL "
            "AND started_at IS NOT NULL AND completed_at IS NOT NULL AND completed_at >= started_at "
            "AND failure_code IS NULL AND result_run_id IS NOT NULL) OR "
            "(status = 'failed' AND lease_owner IS NULL AND leased_until IS NULL "
            "AND started_at IS NOT NULL AND completed_at IS NOT NULL AND completed_at >= started_at "
            "AND failure_code IS NOT NULL AND failure_code ~ '^[a-z][a-z0-9_]{2,63}$' "
            "AND result_run_id IS NULL))",
            name="ck_calibration_jobs_contract",
        ),
    )
    op.create_index("ix_calibration_jobs_trigger_outcome_id", "calibration_jobs", ["trigger_outcome_id"])
    op.create_index("ix_calibration_jobs_trigger_correction_id", "calibration_jobs", ["trigger_correction_id"])
    op.create_index("ix_calibration_jobs_result_run_id", "calibration_jobs", ["result_run_id"])
    op.create_index("ix_calibration_jobs_status_created_at", "calibration_jobs", ["status", "created_at"])
    op.create_index("ix_calibration_jobs_deployment_scope", "calibration_jobs", ["deployment_scope"])

    op.add_column("calibration_runs", sa.Column("trigger_job_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("calibration_runs", sa.Column("artifact_schema", sa.Text(), nullable=True))
    op.execute(
        "UPDATE calibration_runs SET artifact_schema = 'daibm.platt-calibration.v2' "
        "WHERE artifact_sha256 IS NOT NULL"
    )
    op.alter_column(
        "calibration_runs",
        "trigger_outcome_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )
    op.create_foreign_key(
        "fk_calibration_runs_trigger_job",
        "calibration_runs",
        "calibration_jobs",
        ["trigger_job_id"],
        ["job_id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint("uq_calibration_runs_trigger_job_id", "calibration_runs", ["trigger_job_id"])
    op.create_foreign_key(
        "fk_calibration_jobs_result_run",
        "calibration_jobs",
        "calibration_runs",
        ["result_run_id"],
        ["calibration_run_id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_calibration_runs_trigger_outcome_id", "calibration_runs", ["trigger_outcome_id"])
    op.create_index("ix_calibration_runs_trigger_job_id", "calibration_runs", ["trigger_job_id"])

    op.drop_constraint("ck_calibration_runs_artifact_contract", "calibration_runs", type_="check")
    op.create_check_constraint(
        "ck_calibration_runs_artifact_contract",
        "calibration_runs",
        "(status = 'failed' AND artifact_locator IS NULL AND artifact_sha256 IS NULL "
        "AND metrics_after IS NULL AND failure_code IS NOT NULL) OR "
        "(status IN ('exploratory_candidate', 'eligible_candidate') AND artifact_locator IS NOT NULL "
        "AND artifact_sha256 IS NOT NULL AND metrics_before IS NOT NULL "
        "AND metrics_after IS NOT NULL AND failure_code IS NULL)",
    )
    op.drop_constraint("ck_calibration_runs_deployment_status", "calibration_runs", type_="check")
    op.create_check_constraint(
        "ck_calibration_runs_deployment_status",
        "calibration_runs",
        "deployment_status IN ('not_deployed', 'active', 'superseded', "
        "'rejected', 'activation_failed', 'invalidated')",
    )
    op.drop_constraint("ck_calibration_runs_deployment_contract", "calibration_runs", type_="check")
    op.create_check_constraint(
        "ck_calibration_runs_deployment_contract",
        "calibration_runs",
        "calibration_run_id IS DISTINCT FROM previous_active_run_id AND ("
        "(deployment_status = 'active' AND deployment_scope IN ('controlled_demo', 'external_verified') AND status = 'eligible_candidate' AND activation_mode IS NOT NULL AND activated_at IS NOT NULL AND deactivated_at IS NULL) OR "
        "(deployment_status = 'superseded' AND status = 'eligible_candidate' AND activation_mode IS NOT NULL AND activated_at IS NOT NULL AND deactivated_at IS NOT NULL) OR "
        "(deployment_status IN ('not_deployed', 'rejected', 'activation_failed') AND activation_mode IS NULL AND activated_at IS NULL AND deactivated_at IS NULL AND previous_active_run_id IS NULL) OR "
        "(deployment_status = 'invalidated' AND status = 'eligible_candidate' AND activation_mode IS NOT NULL AND activated_at IS NOT NULL AND deactivated_at IS NOT NULL))",
    )
    op.drop_index("uq_calibration_runs_single_active", table_name="calibration_runs")
    op.create_index(
        "uq_calibration_runs_active_scope",
        "calibration_runs",
        ["deployment_scope"],
        unique=True,
        postgresql_where=sa.text("deployment_status = 'active'"),
    )

    op.create_table(
        "calibration_run_observations",
        sa.Column("calibration_run_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("outcome_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("correction_head_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(["calibration_run_id"], ["calibration_runs.calibration_run_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["outcome_id"], ["actual_outcomes.outcome_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["correction_head_id"], ["outcome_corrections.correction_id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_calibration_run_observations_outcome_id", "calibration_run_observations", ["outcome_id"])
    op.create_index("ix_calibration_run_observations_correction_head_id", "calibration_run_observations", ["correction_head_id"])
    op.execute(
        """
        INSERT INTO calibration_run_observations (calibration_run_id, outcome_id, correction_head_id)
        SELECT run.calibration_run_id, member.outcome_id, NULL
        FROM calibration_runs AS run
        JOIN actual_outcomes AS boundary
          ON boundary.outcome_id = run.trigger_outcome_id
        JOIN actual_outcomes AS member
          ON (member.recorded_at, member.outcome_id)
             <= (boundary.recorded_at, boundary.outcome_id)
         AND (
              run.deployment_scope = 'mixed'
              OR run.deployment_scope = CASE member.provenance
                    WHEN 'EXTERNAL_VERIFIED' THEN 'external_verified'
                    ELSE 'controlled_demo'
                 END
         )
        ON CONFLICT DO NOTHING
        """
    )

    op.execute(
        """
        CREATE FUNCTION reject_governed_history_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'governed history is immutable'
                USING ERRCODE = 'integrity_constraint_violation';
        END;
        $$
        """
    )
    for table_name in (
        "facility_delinquencies",
        "facility_restructures",
        "facility_defaults",
        "facility_writeoffs",
        "outcome_corrections",
        "calibration_run_observations",
    ):
        _create_immutable_trigger(table_name)

    op.drop_constraint("ck_ledger_events_event_type", "ledger_events", type_="check")
    op.create_check_constraint(
        "ck_ledger_events_event_type",
        "ledger_events",
        _event_type_check(_PREVIOUS_EVENT_TYPES + _GOVERNANCE_EVENT_TYPES),
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "LOCK TABLE "
            + ", ".join(_DOWNGRADE_GUARDED_TABLES)
            + " IN ACCESS EXCLUSIVE MODE"
        )
    )
    count = bind.scalar(
        sa.text(
            "SELECT "
            "(SELECT count(*) FROM facility_delinquencies) + "
            "(SELECT count(*) FROM facility_restructures) + "
            "(SELECT count(*) FROM facility_defaults) + "
            "(SELECT count(*) FROM facility_writeoffs) + "
            "(SELECT count(*) FROM outcome_corrections) + "
            "(SELECT count(*) FROM calibration_jobs) + "
            "(SELECT count(*) FROM financing_facilities WHERE current_schedule_version <> 1 OR closure_reason IS NOT NULL OR status IN ('restructured', 'defaulted', 'written_off')) + "
            "(SELECT count(*) FROM facility_actions WHERE action_type IN ('restructure', 'declare_default', 'write_off')) + "
            "(SELECT count(*) FROM facility_installments WHERE schedule_version <> 1 OR status = 'superseded') + "
            "(SELECT count(*) FROM financing_requests WHERE assessment_scope <> 'controlled_demo') + "
            "(SELECT count(*) FROM calibration_runs WHERE trigger_job_id IS NOT NULL OR trigger_outcome_id IS NULL OR deployment_status = 'invalidated' OR (artifact_schema IS NOT NULL AND artifact_schema <> 'daibm.platt-calibration.v2')) + "
            "(SELECT GREATEST(count(*) - 1, 0) FROM calibration_runs WHERE deployment_status = 'active') + "
            "(SELECT count(*) FROM calibration_run_observations WHERE correction_head_id IS NOT NULL) + "
            "(SELECT count(*) FROM ledger_events WHERE event_type IN ("
            + ", ".join(f"'{value}'" for value in _GOVERNANCE_EVENT_TYPES)
            + "))"
        )
    )
    if count:
        raise RuntimeError(
            "Cannot downgrade while governed lifecycle or calibration data exists"
        )

    op.drop_constraint("ck_ledger_events_event_type", "ledger_events", type_="check")
    op.create_check_constraint(
        "ck_ledger_events_event_type",
        "ledger_events",
        _event_type_check(_PREVIOUS_EVENT_TYPES),
    )

    for table_name in (
        "calibration_run_observations",
        "outcome_corrections",
        "facility_writeoffs",
        "facility_defaults",
        "facility_restructures",
        "facility_delinquencies",
    ):
        op.execute(f"DROP TRIGGER trg_{table_name}_immutable ON {table_name}")
    op.execute("DROP FUNCTION reject_governed_history_mutation()")

    op.drop_index("ix_calibration_run_observations_correction_head_id", table_name="calibration_run_observations")
    op.drop_index("ix_calibration_run_observations_outcome_id", table_name="calibration_run_observations")
    op.drop_table("calibration_run_observations")

    op.drop_index("uq_calibration_runs_active_scope", table_name="calibration_runs")
    op.create_index(
        "uq_calibration_runs_single_active",
        "calibration_runs",
        ["deployment_status"],
        unique=True,
        postgresql_where=sa.text("deployment_status = 'active'"),
    )
    op.drop_constraint("ck_calibration_runs_deployment_contract", "calibration_runs", type_="check")
    op.create_check_constraint(
        "ck_calibration_runs_deployment_contract",
        "calibration_runs",
        "calibration_run_id IS DISTINCT FROM previous_active_run_id AND ("
        "(deployment_status = 'active' AND status = 'eligible_candidate' AND activation_mode IS NOT NULL AND activated_at IS NOT NULL AND deactivated_at IS NULL) OR "
        "(deployment_status = 'superseded' AND status = 'eligible_candidate' AND activation_mode IS NOT NULL AND activated_at IS NOT NULL AND deactivated_at IS NOT NULL) OR "
        "(deployment_status IN ('not_deployed', 'rejected', 'activation_failed') AND activation_mode IS NULL AND activated_at IS NULL AND deactivated_at IS NULL AND previous_active_run_id IS NULL))",
    )
    op.drop_constraint("ck_calibration_runs_deployment_status", "calibration_runs", type_="check")
    op.create_check_constraint(
        "ck_calibration_runs_deployment_status",
        "calibration_runs",
        "deployment_status IN ('not_deployed', 'active', 'superseded', 'rejected', 'activation_failed')",
    )
    op.drop_constraint("ck_calibration_runs_artifact_contract", "calibration_runs", type_="check")
    op.create_check_constraint(
        "ck_calibration_runs_artifact_contract",
        "calibration_runs",
        "(status = 'failed' AND artifact_locator IS NULL AND artifact_sha256 IS NULL AND metrics_after IS NULL AND failure_code IS NOT NULL) OR "
        "(status IN ('exploratory_candidate', 'eligible_candidate') AND artifact_locator IS NOT NULL AND artifact_sha256 IS NOT NULL AND metrics_before IS NOT NULL AND metrics_after IS NOT NULL AND failure_code IS NULL)",
    )
    op.drop_index("ix_calibration_runs_trigger_job_id", table_name="calibration_runs")
    op.drop_index("ix_calibration_runs_trigger_outcome_id", table_name="calibration_runs")
    op.drop_constraint("fk_calibration_runs_trigger_job", "calibration_runs", type_="foreignkey")
    op.drop_constraint("uq_calibration_runs_trigger_job_id", "calibration_runs", type_="unique")
    op.drop_constraint("fk_calibration_jobs_result_run", "calibration_jobs", type_="foreignkey")
    op.drop_column("calibration_runs", "artifact_schema")
    op.drop_column("calibration_runs", "trigger_job_id")
    op.alter_column(
        "calibration_runs",
        "trigger_outcome_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )

    op.drop_index("ix_calibration_jobs_deployment_scope", table_name="calibration_jobs")
    op.drop_index("ix_calibration_jobs_status_created_at", table_name="calibration_jobs")
    op.drop_index("ix_calibration_jobs_result_run_id", table_name="calibration_jobs")
    op.drop_index("ix_calibration_jobs_trigger_correction_id", table_name="calibration_jobs")
    op.drop_index("ix_calibration_jobs_trigger_outcome_id", table_name="calibration_jobs")
    op.drop_table("calibration_jobs")
    op.drop_index("ix_outcome_corrections_recorded_at", table_name="outcome_corrections")
    op.drop_index("ix_outcome_corrections_auditor_user_id", table_name="outcome_corrections")
    op.drop_index("ix_outcome_corrections_outcome_id", table_name="outcome_corrections")
    op.drop_table("outcome_corrections")

    op.drop_index("ix_facility_writeoffs_recorded_at", table_name="facility_writeoffs")
    op.drop_index("ix_facility_writeoffs_auditor_user_id", table_name="facility_writeoffs")
    op.drop_table("facility_writeoffs")
    op.drop_index("ix_facility_defaults_recorded_at", table_name="facility_defaults")
    op.drop_index("ix_facility_defaults_declared_by_user_id", table_name="facility_defaults")
    op.drop_table("facility_defaults")
    op.drop_index("ix_facility_restructures_recorded_at", table_name="facility_restructures")
    op.drop_index("ix_facility_restructures_restructured_by_user_id", table_name="facility_restructures")
    op.drop_index("ix_facility_restructures_facility_id", table_name="facility_restructures")
    op.drop_table("facility_restructures")
    op.drop_index("ix_facility_delinquencies_recorded_at", table_name="facility_delinquencies")
    op.drop_index("ix_facility_delinquencies_marked_by_user_id", table_name="facility_delinquencies")
    op.drop_index("ix_facility_delinquencies_facility_id", table_name="facility_delinquencies")
    op.drop_table("facility_delinquencies")

    op.drop_constraint("uq_facility_installments_facility_schedule_sequence", "facility_installments", type_="unique")
    op.create_unique_constraint("uq_facility_installments_facility_sequence", "facility_installments", ["facility_id", "sequence"])
    op.drop_constraint("ck_facility_actions_action_type", "facility_actions", type_="check")
    op.create_check_constraint(
        "ck_facility_actions_action_type",
        "facility_actions",
        "action_type IN ('create', 'initiate_disbursement', 'confirm_disbursement', "
        "'submit_payment', 'confirm_payment', 'reject_payment', "
        "'confirm_final_payment', 'mark_overdue', 'close')",
    )
    op.drop_constraint("ck_facility_installments_status", "facility_installments", type_="check")
    op.create_check_constraint("ck_facility_installments_status", "facility_installments", "status IN ('scheduled', 'partially_paid', 'paid', 'overdue')")
    op.drop_constraint("ck_facility_installments_schedule_version", "facility_installments", type_="check")
    op.drop_column("facility_installments", "schedule_version")

    op.execute("DROP TRIGGER trg_financing_facilities_closure_immutable ON financing_facilities")
    op.execute("DROP FUNCTION reject_closure_reason_mutation()")
    op.drop_constraint("ck_financing_facilities_status", "financing_facilities", type_="check")
    op.create_check_constraint("ck_financing_facilities_status", "financing_facilities", "status IN ('ready_for_disbursement', 'disbursed', 'active', 'overdue', 'repaid', 'closed')")
    op.drop_constraint("ck_financing_facilities_closure_reason", "financing_facilities", type_="check")
    op.drop_constraint("ck_financing_facilities_schedule_version", "financing_facilities", type_="check")
    op.drop_column("financing_facilities", "closure_reason")
    op.drop_column("financing_facilities", "current_schedule_version")

    op.drop_constraint("ck_financing_requests_assessment_scope", "financing_requests", type_="check")
    op.drop_column("financing_requests", "assessment_scope")
