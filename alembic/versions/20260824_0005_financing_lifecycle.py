"""Add the exact-cent financing lifecycle.

Revision ID: 20260824_0005
Revises: 20260820_0004
Create Date: 2026-08-24
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260824_0005"
down_revision: str | None = "20260820_0004"
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
)
_FACILITY_LEDGER_EVENT_TYPES = (
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


def _event_type_check(values: tuple[str, ...]) -> str:
    return "event_type IN (" + ", ".join(f"'{value}'" for value in values) + ")"


def upgrade() -> None:
    op.drop_constraint(
        "ck_ledger_events_event_type",
        "ledger_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_ledger_events_event_type",
        "ledger_events",
        _event_type_check(
            _PREVIOUS_LEDGER_EVENT_TYPES + _FACILITY_LEDGER_EVENT_TYPES
        ),
    )
    op.create_table(
        "financing_facilities",
        sa.Column("facility_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("principal", sa.Numeric(14, 2), nullable=False),
        sa.Column("outstanding_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("currency", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("disbursement_reference", sa.Text()),
        sa.Column("disbursement_evidence_sha256", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("disbursement_initiated_at", sa.DateTime(timezone=True)),
        sa.Column("disbursed_at", sa.DateTime(timezone=True)),
        sa.Column("repaid_at", sa.DateTime(timezone=True)),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "principal > 0",
            name="ck_financing_facilities_principal_positive",
        ),
        sa.CheckConstraint(
            "outstanding_amount BETWEEN 0 AND principal",
            name="ck_financing_facilities_outstanding_range",
        ),
        sa.CheckConstraint(
            "currency = upper(currency) AND char_length(currency) = 3",
            name="ck_financing_facilities_currency",
        ),
        sa.CheckConstraint(
            "status IN ('ready_for_disbursement', 'disbursed', 'active', "
            "'overdue', 'repaid', 'closed')",
            name="ck_financing_facilities_status",
        ),
        sa.CheckConstraint(
            "version >= 1",
            name="ck_financing_facilities_version",
        ),
        sa.CheckConstraint(
            "disbursement_evidence_sha256 IS NULL OR "
            "char_length(disbursement_evidence_sha256) = 64",
            name="ck_financing_facilities_disbursement_hash_length",
        ),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["financing_requests.request_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.user_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("facility_id"),
        sa.UniqueConstraint(
            "request_id",
            name="uq_financing_facilities_request_id",
        ),
    )
    op.create_index(
        "ix_financing_facilities_request_id",
        "financing_facilities",
        ["request_id"],
    )
    op.create_index(
        "ix_financing_facilities_created_by_user_id",
        "financing_facilities",
        ["created_by_user_id"],
    )
    op.create_index(
        "ix_financing_facilities_status",
        "financing_facilities",
        ["status"],
    )
    op.create_index(
        "ix_financing_facilities_updated_at",
        "financing_facilities",
        [sa.text("updated_at DESC")],
    )

    op.create_table(
        "facility_installments",
        sa.Column("installment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("facility_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("paid_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "sequence > 0",
            name="ck_facility_installments_sequence_positive",
        ),
        sa.CheckConstraint(
            "amount > 0",
            name="ck_facility_installments_amount_positive",
        ),
        sa.CheckConstraint(
            "paid_amount BETWEEN 0 AND amount",
            name="ck_facility_installments_paid_range",
        ),
        sa.CheckConstraint(
            "status IN ('scheduled', 'partially_paid', 'paid', 'overdue')",
            name="ck_facility_installments_status",
        ),
        sa.ForeignKeyConstraint(
            ["facility_id"],
            ["financing_facilities.facility_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("installment_id"),
        sa.UniqueConstraint(
            "facility_id",
            "sequence",
            name="uq_facility_installments_facility_sequence",
        ),
        sa.UniqueConstraint(
            "facility_id",
            "installment_id",
            name="uq_facility_installments_facility_installment",
        ),
    )
    op.create_index(
        "ix_facility_installments_facility_id",
        "facility_installments",
        ["facility_id"],
    )
    op.create_index(
        "ix_facility_installments_due_date",
        "facility_installments",
        ["due_date"],
    )

    op.create_table(
        "facility_payments",
        sa.Column("payment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("facility_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("installment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "submitted_by_user_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("payment_reference", sa.Text(), nullable=False),
        sa.Column("evidence_sha256", sa.Text()),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("decided_by_user_id", postgresql.UUID(as_uuid=True)),
        sa.Column("decision_comment", sa.Text()),
        sa.CheckConstraint(
            "amount > 0",
            name="ck_facility_payments_amount_positive",
        ),
        sa.CheckConstraint(
            "status IN ('submitted', 'confirmed', 'rejected')",
            name="ck_facility_payments_status",
        ),
        sa.CheckConstraint(
            "evidence_sha256 IS NULL OR char_length(evidence_sha256) = 64",
            name="ck_facility_payments_evidence_hash_length",
        ),
        sa.ForeignKeyConstraint(
            ["facility_id"],
            ["financing_facilities.facility_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["facility_id", "installment_id"],
            [
                "facility_installments.facility_id",
                "facility_installments.installment_id",
            ],
            ondelete="RESTRICT",
            name="fk_facility_payments_owned_installment",
        ),
        sa.ForeignKeyConstraint(
            ["submitted_by_user_id"],
            ["users.user_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by_user_id"],
            ["users.user_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("payment_id"),
        sa.UniqueConstraint(
            "facility_id",
            "payment_reference",
            name="uq_facility_payments_facility_reference",
        ),
    )
    for column in (
        "facility_id",
        "installment_id",
        "submitted_by_user_id",
        "decided_by_user_id",
        "submitted_at",
    ):
        op.create_index(
            f"ix_facility_payments_{column}",
            "facility_payments",
            [column],
        )
    op.create_index(
        "ix_facility_payments_facility_installment",
        "facility_payments",
        ["facility_id", "installment_id"],
    )

    op.create_table(
        "facility_actions",
        sa.Column(
            "action_id",
            sa.BigInteger(),
            sa.Identity(always=True),
            nullable=False,
        ),
        sa.Column("facility_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_role", sa.Text(), nullable=False),
        sa.Column("action_type", sa.Text(), nullable=False),
        sa.Column("idempotency_key", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("expected_version", sa.Integer(), nullable=False),
        sa.Column("resulting_version", sa.Integer(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "actor_role IN ('supplier', 'core_enterprise', 'financier', "
            "'risk_manager', 'auditor')",
            name="ck_facility_actions_actor_role",
        ),
        sa.CheckConstraint(
            "action_type IN ('create', 'initiate_disbursement', "
            "'confirm_disbursement', 'submit_payment', 'confirm_payment', "
            "'reject_payment', 'confirm_final_payment', 'mark_overdue', 'close')",
            name="ck_facility_actions_action_type",
        ),
        sa.CheckConstraint(
            "expected_version >= 1",
            name="ck_facility_actions_expected_version",
        ),
        sa.CheckConstraint(
            "resulting_version >= expected_version",
            name="ck_facility_actions_resulting_version",
        ),
        sa.ForeignKeyConstraint(
            ["facility_id"],
            ["financing_facilities.facility_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.user_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("action_id"),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_facility_actions_idempotency_key",
        ),
    )
    op.create_index(
        "ix_facility_actions_facility_id",
        "facility_actions",
        ["facility_id"],
    )
    op.create_index(
        "ix_facility_actions_actor_user_id",
        "facility_actions",
        ["actor_user_id"],
    )
    op.create_index(
        "ix_facility_actions_created_at",
        "facility_actions",
        ["created_at"],
    )


def downgrade() -> None:
    for index in (
        "ix_facility_actions_created_at",
        "ix_facility_actions_actor_user_id",
        "ix_facility_actions_facility_id",
    ):
        op.drop_index(index, table_name="facility_actions")
    op.drop_table("facility_actions")

    for index in (
        "ix_facility_payments_submitted_at",
        "ix_facility_payments_facility_installment",
        "ix_facility_payments_decided_by_user_id",
        "ix_facility_payments_submitted_by_user_id",
        "ix_facility_payments_installment_id",
        "ix_facility_payments_facility_id",
    ):
        op.drop_index(index, table_name="facility_payments")
    op.drop_table("facility_payments")

    op.drop_index(
        "ix_facility_installments_due_date",
        table_name="facility_installments",
    )
    op.drop_index(
        "ix_facility_installments_facility_id",
        table_name="facility_installments",
    )
    op.drop_table("facility_installments")

    for index in (
        "ix_financing_facilities_updated_at",
        "ix_financing_facilities_status",
        "ix_financing_facilities_created_by_user_id",
        "ix_financing_facilities_request_id",
    ):
        op.drop_index(index, table_name="financing_facilities")
    op.drop_table("financing_facilities")

    op.drop_constraint(
        "ck_ledger_events_event_type",
        "ledger_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_ledger_events_event_type",
        "ledger_events",
        _event_type_check(_PREVIOUS_LEDGER_EVENT_TYPES),
    )
