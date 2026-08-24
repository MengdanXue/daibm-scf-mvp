from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, Text
from sqlalchemy.dialects.postgresql import DOUBLE_PRECISION, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class ActualOutcomeModel(Base):
    __tablename__ = "actual_outcomes"
    __table_args__ = (
        CheckConstraint("days_past_due >= 0", name="ck_actual_outcomes_days_past_due"),
        CheckConstraint("loss_amount >= 0", name="ck_actual_outcomes_loss_amount"),
        CheckConstraint(
            "request_sha256 ~ '^[0-9a-f]{64}$' AND "
            "evidence_sha256 ~ '^[0-9a-f]{64}$' AND "
            "risk_input_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_actual_outcomes_hashes",
        ),
        CheckConstraint(
            "provenance IN ('CONTROLLED_DEMO', 'EXTERNAL_VERIFIED')",
            name="ck_actual_outcomes_provenance",
        ),
        CheckConstraint(
            "original_risk_score BETWEEN 0 AND 1",
            name="ck_actual_outcomes_original_score",
        ),
    )

    outcome_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    facility_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("financing_facilities.facility_id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("financing_requests.request_id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    risk_assessment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("risk_assessments.risk_assessment_id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    model_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("model_versions.model_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    submitted_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="RESTRICT"), nullable=False
    )
    idempotency_key: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, unique=True
    )
    request_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    defaulted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    days_past_due: Mapped[int] = mapped_column(Integer, nullable=False)
    loss_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    evidence_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    provenance: Mapped[str] = mapped_column(Text, nullable=False)
    original_risk_score: Mapped[float] = mapped_column(DOUBLE_PRECISION, nullable=False)
    risk_input_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


Index("ix_actual_outcomes_recorded_at", ActualOutcomeModel.recorded_at.desc())
Index("ix_actual_outcomes_model_version_id", ActualOutcomeModel.model_version_id)


class CalibrationRunModel(Base):
    __tablename__ = "calibration_runs"
    __table_args__ = (
        CheckConstraint(
            "sample_count >= 1 AND positive_count >= 0 AND negative_count >= 0 "
            "AND positive_count + negative_count = sample_count",
            name="ck_calibration_runs_counts",
        ),
        CheckConstraint(
            "status IN ('exploratory_candidate', 'eligible_candidate', 'failed')",
            name="ck_calibration_runs_status",
        ),
        CheckConstraint(
            "dataset_sha256 ~ '^[0-9a-f]{64}$' AND "
            "(artifact_sha256 IS NULL OR artifact_sha256 ~ '^[0-9a-f]{64}$')",
            name="ck_calibration_runs_hashes",
        ),
        CheckConstraint(
            "(status = 'failed' AND artifact_locator IS NULL AND "
            "artifact_sha256 IS NULL AND metrics_after IS NULL AND "
            "failure_code IS NOT NULL) OR "
            "(status IN ('exploratory_candidate', 'eligible_candidate') AND "
            "artifact_locator IS NOT NULL AND artifact_sha256 IS NOT NULL AND "
            "metrics_before IS NOT NULL AND metrics_after IS NOT NULL AND "
            "failure_code IS NULL)",
            name="ck_calibration_runs_artifact_contract",
        ),
    )

    calibration_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True
    )
    trigger_outcome_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("actual_outcomes.outcome_id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    dataset_sha256: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False)
    positive_count: Mapped[int] = mapped_column(Integer, nullable=False)
    negative_count: Mapped[int] = mapped_column(Integer, nullable=False)
    metrics_before: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True)
    )
    metrics_after: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True)
    )
    configuration: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    artifact_locator: Mapped[str | None] = mapped_column(Text)
    artifact_sha256: Mapped[str | None] = mapped_column(Text)
    failure_code: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


Index("ix_calibration_runs_completed_at", CalibrationRunModel.completed_at.desc())
Index("ix_calibration_runs_status", CalibrationRunModel.status)


__all__ = ["ActualOutcomeModel", "CalibrationRunModel"]
