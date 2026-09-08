"""Join published lifecycle and anchor-version histories without changing data.

Either parent may already be deployed. Alembic applies the missing path before
this merge; the historical 0010 compatibility checks remain on the lifecycle
path. Do not renumber or reparent either published revision.
"""

from typing import Sequence


revision: str = "20260908_0014"
down_revision: tuple[str, str] = ("20260907_0013", "20260907_0011")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
