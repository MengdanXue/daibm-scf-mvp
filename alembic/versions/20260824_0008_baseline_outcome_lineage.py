"""Allow actual outcomes to retain business-baseline prediction lineage.

Revision ID: 20260824_0008
Revises: 20260824_0007
Create Date: 2026-08-24
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260824_0008"
down_revision: str | None = "20260824_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "actual_outcomes",
        sa.Column("risk_engine_version", sa.Text(), nullable=True),
    )
    op.execute(
        "ALTER TABLE actual_outcomes "
        "DISABLE TRIGGER trg_actual_outcomes_immutable"
    )
    op.execute(
        """
        UPDATE actual_outcomes AS outcome
        SET risk_engine_version = model.model_name || '@' || model.semantic_version
        FROM model_versions AS model
        WHERE model.model_version_id = outcome.model_version_id
          AND outcome.risk_engine_version IS NULL
        """
    )
    op.execute(
        "ALTER TABLE actual_outcomes "
        "ENABLE TRIGGER trg_actual_outcomes_immutable"
    )
    bind = op.get_bind()
    if bind.scalar(
        sa.text(
            "SELECT count(*) FROM actual_outcomes "
            "WHERE risk_engine_version IS NULL"
        )
    ):
        raise RuntimeError(
            "Cannot migrate actual outcomes with incomplete prediction lineage"
        )
    op.alter_column(
        "actual_outcomes",
        "risk_engine_version",
        existing_type=sa.Text(),
        nullable=False,
    )
    op.drop_constraint(
        "actual_outcomes_risk_assessment_id_fkey",
        "actual_outcomes",
        type_="foreignkey",
    )
    op.alter_column(
        "actual_outcomes",
        "model_version_id",
        existing_type=sa.Uuid(),
        nullable=True,
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.scalar(
        sa.text(
            "SELECT count(*) FROM actual_outcomes AS outcome "
            "LEFT JOIN risk_assessments AS assessment "
            "ON assessment.risk_assessment_id = outcome.risk_assessment_id "
            "WHERE outcome.model_version_id IS NULL "
            "OR assessment.risk_assessment_id IS NULL"
        )
    ):
        raise RuntimeError(
            "Cannot downgrade business-baseline outcomes without research lineage"
        )
    op.alter_column(
        "actual_outcomes",
        "model_version_id",
        existing_type=sa.Uuid(),
        nullable=False,
    )
    op.create_foreign_key(
        "actual_outcomes_risk_assessment_id_fkey",
        "actual_outcomes",
        "risk_assessments",
        ["risk_assessment_id"],
        ["risk_assessment_id"],
        ondelete="RESTRICT",
    )
    op.drop_column("actual_outcomes", "risk_engine_version")
