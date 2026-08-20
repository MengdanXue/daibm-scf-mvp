"""Add identity and multi-role financing workflow.

Revision ID: 20260820_0003
Revises: 20260815_0002
Create Date: 2026-08-20
"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260820_0003"
down_revision: str | None = "20260815_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ROLES = (
    "supplier",
    "core_enterprise",
    "financier",
    "risk_manager",
    "auditor",
)
STATUSES = (
    "draft",
    "submitted",
    "trade_returned",
    "trade_confirmed",
    "risk_assessed",
    "approved",
    "manual_review",
    "rejected",
    "controlled",
    "audited",
)
LEDGER_EVENT_TYPES = (
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


def _in_check(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{value}'" for value in values) + ")"


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_code", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("organization_type", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            _in_check(
                "organization_type",
                ("supplier", "core_enterprise", "financier", "auditor"),
            ),
            name="ck_organizations_type",
        ),
        sa.PrimaryKeyConstraint("organization_id"),
        sa.UniqueConstraint("organization_code"),
    )

    op.create_table(
        "users",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("password_salt", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(_in_check("role", ROLES), name="ck_users_role"),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.organization_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("user_id"),
        sa.UniqueConstraint("username"),
    )
    op.create_index("ix_users_organization_id", "users", ["organization_id"])

    op.create_table(
        "user_sessions",
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "char_length(token_hash) = 64",
            name="ck_user_sessions_token_hash_length",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.user_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("session_id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])
    op.create_index(
        "ix_user_sessions_expires_at", "user_sessions", ["expires_at"]
    )

    op.add_column(
        "financing_requests",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.add_column(
        "financing_requests",
        sa.Column(
            "status",
            sa.Text(),
            server_default=sa.text("'audited'::text"),
            nullable=False,
        ),
    )
    op.add_column(
        "financing_requests",
        sa.Column(
            "version", sa.Integer(), server_default=sa.text("1"), nullable=False
        ),
    )
    op.add_column(
        "financing_requests",
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True)),
    )
    op.add_column(
        "financing_requests",
        sa.Column("supplier_organization_id", postgresql.UUID(as_uuid=True)),
    )
    op.add_column(
        "financing_requests",
        sa.Column("core_enterprise_organization_id", postgresql.UUID(as_uuid=True)),
    )
    op.add_column(
        "financing_requests", sa.Column("contract_number", sa.Text())
    )
    op.add_column("financing_requests", sa.Column("invoice_number", sa.Text()))
    op.alter_column("financing_requests", "risk_score", nullable=True)
    op.alter_column("financing_requests", "decision", nullable=True)
    op.alter_column("financing_requests", "explanations", nullable=True)
    op.alter_column("financing_requests", "control_action", nullable=True)
    op.drop_constraint(
        "ck_financing_requests_risk_score",
        "financing_requests",
        type_="check",
    )
    op.drop_constraint(
        "ck_financing_requests_decision",
        "financing_requests",
        type_="check",
    )
    op.create_check_constraint(
        "ck_financing_requests_risk_score",
        "financing_requests",
        "risk_score IS NULL OR risk_score BETWEEN 0 AND 1",
    )
    op.create_check_constraint(
        "ck_financing_requests_decision",
        "financing_requests",
        "decision IS NULL OR decision IN ('approved', 'manual_review', 'rejected')",
    )
    op.create_check_constraint(
        "ck_financing_requests_status",
        "financing_requests",
        _in_check("status", STATUSES),
    )
    op.create_check_constraint(
        "ck_financing_requests_version",
        "financing_requests",
        "version >= 1",
    )
    for column, target, constraint in (
        ("created_by_user_id", "users.user_id", "fk_financing_requests_creator"),
        (
            "supplier_organization_id",
            "organizations.organization_id",
            "fk_financing_requests_supplier_org",
        ),
        (
            "core_enterprise_organization_id",
            "organizations.organization_id",
            "fk_financing_requests_core_org",
        ),
    ):
        target_table, target_column = target.split(".")
        op.create_foreign_key(
            constraint,
            "financing_requests",
            target_table,
            [column],
            [target_column],
            ondelete="RESTRICT",
        )
        op.create_index(f"ix_financing_requests_{column}", "financing_requests", [column])
    op.create_index("ix_financing_requests_status", "financing_requests", ["status"])
    op.create_index(
        "ix_financing_requests_updated_at",
        "financing_requests",
        [sa.text("updated_at DESC")],
    )

    op.create_table(
        "workflow_actions",
        sa.Column(
            "action_id",
            sa.BigInteger(),
            sa.Identity(always=True),
            nullable=False,
        ),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_role", sa.Text(), nullable=False),
        sa.Column("action_type", sa.Text(), nullable=False),
        sa.Column("from_status", sa.Text()),
        sa.Column("to_status", sa.Text(), nullable=False),
        sa.Column("comment", sa.Text()),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            _in_check("actor_role", ROLES),
            name="ck_workflow_actions_actor_role",
        ),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["financing_requests.request_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"], ["users.user_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("action_id"),
    )
    op.create_index(
        "ix_workflow_actions_request_id", "workflow_actions", ["request_id"]
    )
    op.create_index(
        "ix_workflow_actions_actor_user_id",
        "workflow_actions",
        ["actor_user_id"],
    )
    op.create_index(
        "ix_workflow_actions_created_at", "workflow_actions", ["created_at"]
    )

    op.drop_constraint(
        "ck_ledger_events_event_type", "ledger_events", type_="check"
    )
    op.create_check_constraint(
        "ck_ledger_events_event_type",
        "ledger_events",
        _in_check("event_type", LEDGER_EVENT_TYPES),
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_ledger_events_event_type", "ledger_events", type_="check"
    )
    op.create_check_constraint(
        "ck_ledger_events_event_type",
        "ledger_events",
        _in_check("event_type", LEDGER_EVENT_TYPES[:10]),
    )
    op.drop_index("ix_workflow_actions_created_at", table_name="workflow_actions")
    op.drop_index(
        "ix_workflow_actions_actor_user_id", table_name="workflow_actions"
    )
    op.drop_index("ix_workflow_actions_request_id", table_name="workflow_actions")
    op.drop_table("workflow_actions")
    op.drop_index("ix_financing_requests_updated_at", table_name="financing_requests")
    op.drop_index("ix_financing_requests_status", table_name="financing_requests")
    for column, constraint in (
        ("core_enterprise_organization_id", "fk_financing_requests_core_org"),
        ("supplier_organization_id", "fk_financing_requests_supplier_org"),
        ("created_by_user_id", "fk_financing_requests_creator"),
    ):
        op.drop_index(f"ix_financing_requests_{column}", table_name="financing_requests")
        op.drop_constraint(constraint, "financing_requests", type_="foreignkey")
    op.drop_constraint(
        "ck_financing_requests_version", "financing_requests", type_="check"
    )
    op.drop_constraint(
        "ck_financing_requests_status", "financing_requests", type_="check"
    )
    op.drop_constraint(
        "ck_financing_requests_decision", "financing_requests", type_="check"
    )
    op.drop_constraint(
        "ck_financing_requests_risk_score", "financing_requests", type_="check"
    )
    op.create_check_constraint(
        "ck_financing_requests_decision",
        "financing_requests",
        "decision IN ('approved', 'manual_review', 'rejected')",
    )
    op.create_check_constraint(
        "ck_financing_requests_risk_score",
        "financing_requests",
        "risk_score BETWEEN 0 AND 1",
    )
    for column in (
        "invoice_number",
        "contract_number",
        "core_enterprise_organization_id",
        "supplier_organization_id",
        "created_by_user_id",
        "version",
        "status",
        "updated_at",
    ):
        op.drop_column("financing_requests", column)
    op.alter_column("financing_requests", "control_action", nullable=False)
    op.alter_column("financing_requests", "explanations", nullable=False)
    op.alter_column("financing_requests", "decision", nullable=False)
    op.alter_column("financing_requests", "risk_score", nullable=False)
    op.drop_index("ix_user_sessions_expires_at", table_name="user_sessions")
    op.drop_index("ix_user_sessions_user_id", table_name="user_sessions")
    op.drop_table("user_sessions")
    op.drop_index("ix_users_organization_id", table_name="users")
    op.drop_table("users")
    op.drop_table("organizations")
