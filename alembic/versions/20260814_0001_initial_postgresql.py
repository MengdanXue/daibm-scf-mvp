"""Create the PostgreSQL-only financing and ledger schema.

Revision ID: 20260814_0001
Revises:
Create Date: 2026-08-14
"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260814_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "financing_requests",
        sa.Column(
            "request_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("applicant_id", sa.Text(), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("term_days", sa.Integer(), nullable=False),
        sa.Column("features", postgresql.JSONB(), nullable=False),
        sa.Column(
            "risk_score",
            postgresql.DOUBLE_PRECISION(),
            nullable=False,
        ),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("explanations", postgresql.JSONB(), nullable=False),
        sa.Column("control_action", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "amount > 0",
            name="ck_financing_requests_amount_positive",
        ),
        sa.CheckConstraint(
            "decision IN ('approved', 'manual_review', 'rejected')",
            name="ck_financing_requests_decision",
        ),
        sa.CheckConstraint(
            "risk_score BETWEEN 0 AND 1",
            name="ck_financing_requests_risk_score",
        ),
        sa.CheckConstraint(
            "term_days BETWEEN 1 AND 365",
            name="ck_financing_requests_term_days",
        ),
        sa.PrimaryKeyConstraint("request_id"),
    )
    op.create_index(
        "ix_financing_requests_created_at",
        "financing_requests",
        [sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_financing_requests_decision",
        "financing_requests",
        ["decision"],
    )
    op.create_index(
        "ix_financing_requests_applicant_id",
        "financing_requests",
        ["applicant_id"],
    )

    op.create_table(
        "ledger_events",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=True),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column(
            "entity_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("previous_hash", sa.Text(), nullable=False),
        sa.Column("event_hash", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "event_type IN ("
            "'FINANCING_REQUEST', "
            "'RISK_ASSESSMENT', "
            "'FINANCING_DECISION', "
            "'CONTROL_ACTION'"
            ")",
            name="ck_ledger_events_event_type",
        ),
        sa.CheckConstraint(
            "char_length(event_hash) = 64",
            name="ck_ledger_events_hash_length",
        ),
        sa.CheckConstraint(
            "char_length(previous_hash) IN (7, 64)",
            name="ck_ledger_events_previous_hash_length",
        ),
        sa.ForeignKeyConstraint(
            ["entity_id"],
            ["financing_requests.request_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_hash"),
    )
    op.create_index(
        "ix_ledger_events_entity_id_id",
        "ledger_events",
        ["entity_id", "id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ledger_events_entity_id_id",
        table_name="ledger_events",
    )
    op.drop_table("ledger_events")
    op.drop_index(
        "ix_financing_requests_applicant_id",
        table_name="financing_requests",
    )
    op.drop_index(
        "ix_financing_requests_decision",
        table_name="financing_requests",
    )
    op.drop_index(
        "ix_financing_requests_created_at",
        table_name="financing_requests",
    )
    op.drop_table("financing_requests")
