"""Record the core-enterprise payable ceiling that the invoice-limit proof binds.

Revision ID: 20260824_0010
Revises: 20260824_0009
Create Date: 2026-09-04
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260824_0010"
down_revision: str | None = "20260824_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "financing_requests",
        sa.Column("confirmed_payable_amount", sa.Numeric(14, 2), nullable=True),
    )
    op.create_check_constraint(
        "ck_financing_requests_confirmed_payable_amount",
        "financing_requests",
        "confirmed_payable_amount IS NULL OR confirmed_payable_amount > 0",
    )
    # The proof statement is invoice amount <= payable ceiling. Persisting the
    # ceiling lets an auditor re-derive the public signals of any archived
    # proof, so the database never disagrees with the ledger payload.
    op.create_check_constraint(
        "ck_financing_requests_payable_covers_amount",
        "financing_requests",
        "confirmed_payable_amount IS NULL OR confirmed_payable_amount >= amount",
    )


def downgrade() -> None:
    bind = op.get_bind()
    confirmed = bind.scalar(
        sa.text(
            "SELECT count(*) FROM financing_requests "
            "WHERE confirmed_payable_amount IS NOT NULL"
        )
    )
    if confirmed:
        raise RuntimeError(
            "Refusing to drop confirmed payable ceilings that anchor "
            "invoice-limit proofs; archive the affected applications first"
        )
    op.drop_constraint(
        "ck_financing_requests_payable_covers_amount",
        "financing_requests",
        type_="check",
    )
    op.drop_constraint(
        "ck_financing_requests_confirmed_payable_amount",
        "financing_requests",
        type_="check",
    )
    op.drop_column("financing_requests", "confirmed_payable_amount")
