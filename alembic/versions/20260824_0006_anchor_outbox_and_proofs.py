"""Add the payload-free Fabric anchor outbox.

Revision ID: 20260824_0006
Revises: 20260824_0005a
Create Date: 2026-08-24
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260824_0006"
down_revision: str | None = "20260824_0005a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "anchor_outbox",
        sa.Column("anchor_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "ledger_event_id",
            sa.BigInteger(),
            sa.ForeignKey("ledger_events.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "event_id", postgresql.UUID(as_uuid=True), nullable=False, unique=True
        ),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_hash", sa.Text(), nullable=False),
        sa.Column("chain_head_hash", sa.Text(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("model_version", sa.Text()),
        sa.Column("policy_version", sa.Text()),
        sa.Column("circuit_version", sa.Text()),
        sa.Column("proof_sha256", sa.Text()),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_token", postgresql.UUID(as_uuid=True)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("last_error_code", sa.Text()),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("anchored_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'anchored', 'permanent_failed')",
            name="ck_anchor_outbox_status",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0", name="ck_anchor_outbox_attempt_count"
        ),
        sa.CheckConstraint(
            "schema_version = 1", name="ck_anchor_outbox_schema_version"
        ),
        sa.CheckConstraint(
            "event_hash ~ '^[0-9a-f]{64}$' AND "
            "chain_head_hash ~ '^[0-9a-f]{64}$'",
            name="ck_anchor_outbox_hashes",
        ),
        sa.CheckConstraint(
            "proof_sha256 IS NULL OR proof_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_anchor_outbox_proof_hash",
        ),
        sa.CheckConstraint(
            "model_version IS NULL OR "
            "model_version ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'",
            name="ck_anchor_outbox_model_version",
        ),
        sa.CheckConstraint(
            "policy_version IS NULL OR "
            "policy_version ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'",
            name="ck_anchor_outbox_policy_version",
        ),
        sa.CheckConstraint(
            "circuit_version IS NULL OR "
            "circuit_version ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'",
            name="ck_anchor_outbox_circuit_version",
        ),
        sa.CheckConstraint(
            "(lease_token IS NULL) = (lease_expires_at IS NULL)",
            name="ck_anchor_outbox_lease_pair",
        ),
        sa.CheckConstraint(
            "status != 'anchored' OR anchored_at IS NOT NULL",
            name="ck_anchor_outbox_anchored_at",
        ),
    )
    op.create_index(
        "ix_anchor_outbox_pending_claim",
        "anchor_outbox",
        ["next_attempt_at", "lease_expires_at", "created_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index("ix_anchor_outbox_pending_claim", table_name="anchor_outbox")
    op.drop_table("anchor_outbox")
