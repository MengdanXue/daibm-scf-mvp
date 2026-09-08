"""Allow governed reruns of a previously seen calibration dataset.

Revision ID: 20260824_0011
Revises: 20260824_0010
"""

from alembic import op
import sqlalchemy as sa
from typing import Sequence


revision: str = "20260824_0011"
down_revision: str | None = "20260824_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    from app.migration_compatibility import reconcile_lifecycle

    reconcile_lifecycle(op.get_bind())
    op.drop_constraint(
        "calibration_runs_dataset_sha256_key",
        "calibration_runs",
        type_="unique",
    )
    op.create_index(
        "ix_calibration_runs_scope_dataset",
        "calibration_runs",
        ["deployment_scope", "dataset_sha256"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    duplicate = bind.execute(
        sa.text(
            "SELECT dataset_sha256 FROM calibration_runs "
            "GROUP BY dataset_sha256 HAVING count(*) > 1 LIMIT 1"
        )
    ).scalar_one_or_none()
    if duplicate is not None:
        raise RuntimeError(
            "Cannot restore dataset uniqueness while governed reruns exist"
        )
    op.drop_index(
        "ix_calibration_runs_scope_dataset",
        table_name="calibration_runs",
    )
    op.create_unique_constraint(
        "calibration_runs_dataset_sha256_key",
        "calibration_runs",
        ["dataset_sha256"],
    )
