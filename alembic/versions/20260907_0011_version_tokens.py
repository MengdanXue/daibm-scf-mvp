"""Allow one explicit @ revision separator in anchor version tokens.

Revision ID: 20260907_0011
Revises: 20260824_0010
Create Date: 2026-09-07
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260907_0011"
down_revision: str | None = "20260824_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD = "^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
_NEW = "^[A-Za-z0-9][A-Za-z0-9._-]*(@[A-Za-z0-9][A-Za-z0-9._-]*)?$"


def _constraint(name: str, column: str, expression: str) -> None:
    op.create_check_constraint(
        name,
        "anchor_outbox",
        f"{column} IS NULL OR (char_length({column}) BETWEEN 1 AND 64 AND {column} ~ '{expression}')",
    )


def upgrade() -> None:
    for name in (
        "ck_anchor_outbox_model_version",
        "ck_anchor_outbox_policy_version",
        "ck_anchor_outbox_circuit_version",
    ):
        op.drop_constraint(name, "anchor_outbox", type_="check")
    _constraint("ck_anchor_outbox_model_version", "model_version", _NEW)
    _constraint("ck_anchor_outbox_policy_version", "policy_version", _NEW)
    _constraint("ck_anchor_outbox_circuit_version", "circuit_version", _NEW)


def downgrade() -> None:
    bind = op.get_bind()
    for column in ("model_version", "policy_version", "circuit_version"):
        invalid = bind.execute(
            sa.text(
                f"SELECT count(*) FROM anchor_outbox WHERE {column} IS NOT NULL AND "
                f"NOT ({column} ~ '{_OLD}')"
            )
        ).scalar_one()
        if invalid:
            raise RuntimeError(
                "Refusing to downgrade version-token constraints while "
                f"anchor_outbox.{column} contains @-qualified values"
            )
        op.drop_constraint(f"ck_anchor_outbox_{column}", "anchor_outbox", type_="check")
        _constraint(f"ck_anchor_outbox_{column}", column, _OLD)
