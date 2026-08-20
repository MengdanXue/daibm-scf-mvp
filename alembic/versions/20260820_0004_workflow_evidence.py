"""Add trade and business-risk evidence to financing applications.

Revision ID: 20260820_0004
Revises: 20260820_0003
Create Date: 2026-08-20
"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260820_0004"
down_revision: str | None = "20260820_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for column in (
        sa.Column("trade_evidence_sha256", sa.Text()),
        sa.Column("invoice_claim_sha256", sa.Text()),
        sa.Column("risk_assessment_id", postgresql.UUID(as_uuid=True)),
        sa.Column("risk_engine_version", sa.Text()),
        sa.Column("risk_input_sha256", sa.Text()),
        sa.Column("risk_assessed_at", sa.DateTime(timezone=True)),
    ):
        op.add_column("financing_requests", column)
    op.create_check_constraint(
        "ck_financing_requests_trade_evidence_hash_length",
        "financing_requests",
        "trade_evidence_sha256 IS NULL OR char_length(trade_evidence_sha256) = 64",
    )
    op.create_check_constraint(
        "ck_financing_requests_invoice_claim_hash_length",
        "financing_requests",
        "invoice_claim_sha256 IS NULL OR char_length(invoice_claim_sha256) = 64",
    )
    op.create_check_constraint(
        "ck_financing_requests_risk_input_hash_length",
        "financing_requests",
        "risk_input_sha256 IS NULL OR char_length(risk_input_sha256) = 64",
    )
    op.create_unique_constraint(
        "uq_financing_requests_invoice_claim_sha256",
        "financing_requests",
        ["invoice_claim_sha256"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_financing_requests_invoice_claim_sha256",
        "financing_requests",
        type_="unique",
    )
    for constraint in (
        "ck_financing_requests_risk_input_hash_length",
        "ck_financing_requests_invoice_claim_hash_length",
        "ck_financing_requests_trade_evidence_hash_length",
    ):
        op.drop_constraint(constraint, "financing_requests", type_="check")
    for column in (
        "risk_assessed_at",
        "risk_input_sha256",
        "risk_engine_version",
        "risk_assessment_id",
        "invoice_claim_sha256",
        "trade_evidence_sha256",
    ):
        op.drop_column("financing_requests", column)
