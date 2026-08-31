from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class OutcomeCorrectionModel(Base):
    __tablename__ = "outcome_corrections"
    __table_args__ = (
        CheckConstraint("action IN ('EXCLUDE', 'REINSTATE')", name="ck_outcome_corrections_action"),
        CheckConstraint(
            "evidence_sha256 ~ '^[0-9a-f]{64}$' AND request_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_outcome_corrections_hashes",
        ),
    )

    correction_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    outcome_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("actual_outcomes.outcome_id", ondelete="RESTRICT"), nullable=False
    )
    action: Mapped[str] = mapped_column(Text, nullable=False)
    reason_code: Mapped[str] = mapped_column(Text, nullable=False)
    comment: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    auditor_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="RESTRICT"), nullable=False
    )
    idempotency_key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), unique=True, nullable=False)
    request_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


Index("ix_outcome_corrections_outcome_id", OutcomeCorrectionModel.outcome_id)
Index("ix_outcome_corrections_auditor_user_id", OutcomeCorrectionModel.auditor_user_id)
Index("ix_outcome_corrections_recorded_at", OutcomeCorrectionModel.recorded_at)


class CalibrationJobModel(Base):
    __tablename__ = "calibration_jobs"
    __table_args__ = (
        CheckConstraint(
            "deployment_scope IN ('controlled_demo', 'external_verified')",
            name="ck_calibration_jobs_deployment_scope",
        ),
        CheckConstraint(
            "trigger_type IN ('outcome_submitted', 'correction_exclude', 'correction_reinstate')",
            name="ck_calibration_jobs_trigger_type",
        ),
        CheckConstraint(
            "attempt_count BETWEEN 0 AND 3 AND "
            "((trigger_type = 'outcome_submitted' AND trigger_outcome_id IS NOT NULL "
            "AND trigger_correction_id IS NULL) OR "
            "(trigger_type IN ('correction_exclude', 'correction_reinstate') "
            "AND trigger_outcome_id IS NULL AND trigger_correction_id IS NOT NULL)) AND "
            "((status = 'queued' AND lease_owner IS NULL AND leased_until IS NULL "
            "AND started_at IS NULL AND completed_at IS NULL "
            "AND failure_code IS NULL AND result_run_id IS NULL) OR "
            "(status = 'running' AND lease_owner IS NOT NULL "
            "AND btrim(lease_owner) <> '' AND leased_until IS NOT NULL "
            "AND started_at IS NOT NULL AND leased_until > started_at "
            "AND completed_at IS NULL AND failure_code IS NULL "
            "AND result_run_id IS NULL) OR "
            "(status = 'completed' AND lease_owner IS NULL "
            "AND leased_until IS NULL AND started_at IS NOT NULL "
            "AND completed_at IS NOT NULL AND completed_at >= started_at "
            "AND failure_code IS NULL AND result_run_id IS NOT NULL) OR "
            "(status = 'failed' AND lease_owner IS NULL AND leased_until IS NULL "
            "AND started_at IS NOT NULL AND completed_at IS NOT NULL "
            "AND completed_at >= started_at "
            "AND failure_code IS NOT NULL "
            "AND failure_code ~ '^[a-z][a-z0-9_]{2,63}$' "
            "AND result_run_id IS NULL))",
            name="ck_calibration_jobs_contract",
        ),
        ForeignKeyConstraint(
            ["result_run_id"],
            ["calibration_runs.calibration_run_id"],
            name="fk_calibration_jobs_result_run",
            ondelete="RESTRICT",
            use_alter=True,
        ),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    deployment_scope: Mapped[str] = mapped_column(Text, nullable=False)
    trigger_type: Mapped[str] = mapped_column(Text, nullable=False)
    trigger_outcome_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("actual_outcomes.outcome_id", ondelete="RESTRICT")
    )
    trigger_correction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("outcome_corrections.correction_id", ondelete="RESTRICT")
    )
    idempotency_key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(Text)
    leased_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_code: Mapped[str | None] = mapped_column(Text)
    result_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


Index("ix_calibration_jobs_trigger_outcome_id", CalibrationJobModel.trigger_outcome_id)
Index("ix_calibration_jobs_trigger_correction_id", CalibrationJobModel.trigger_correction_id)
Index("ix_calibration_jobs_result_run_id", CalibrationJobModel.result_run_id)
Index("ix_calibration_jobs_status_created_at", CalibrationJobModel.status, CalibrationJobModel.created_at)
Index("ix_calibration_jobs_deployment_scope", CalibrationJobModel.deployment_scope)


class CalibrationRunObservationModel(Base):
    __tablename__ = "calibration_run_observations"

    calibration_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("calibration_runs.calibration_run_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    outcome_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("actual_outcomes.outcome_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    correction_head_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("outcome_corrections.correction_id", ondelete="RESTRICT"),
    )


Index("ix_calibration_run_observations_outcome_id", CalibrationRunObservationModel.outcome_id)
Index("ix_calibration_run_observations_correction_head_id", CalibrationRunObservationModel.correction_head_id)


__all__ = [
    "CalibrationJobModel",
    "CalibrationRunObservationModel",
    "OutcomeCorrectionModel",
]
