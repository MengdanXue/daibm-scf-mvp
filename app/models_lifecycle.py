from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


_HASH_CHECK = "evidence_sha256 ~ '^[0-9a-f]{64}$'"


class FacilityDelinquencyModel(Base):
    __tablename__ = "facility_delinquencies"
    __table_args__ = (
        CheckConstraint("days_past_due > 0", name="ck_facility_delinquencies_days"),
        CheckConstraint(_HASH_CHECK, name="ck_facility_delinquencies_hash"),
    )

    delinquency_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    facility_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("financing_facilities.facility_id", ondelete="RESTRICT"),
        nullable=False,
    )
    marked_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="RESTRICT"), nullable=False
    )
    days_past_due: Mapped[int] = mapped_column(Integer, nullable=False)
    reason_code: Mapped[str] = mapped_column(Text, nullable=False)
    comment: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


Index("ix_facility_delinquencies_facility_id", FacilityDelinquencyModel.facility_id)
Index("ix_facility_delinquencies_marked_by_user_id", FacilityDelinquencyModel.marked_by_user_id)
Index("ix_facility_delinquencies_recorded_at", FacilityDelinquencyModel.recorded_at)


class FacilityRestructureModel(Base):
    __tablename__ = "facility_restructures"
    __table_args__ = (
        CheckConstraint(
            "old_schedule_version >= 1 AND new_schedule_version = old_schedule_version + 1",
            name="ck_facility_restructures_versions",
        ),
        CheckConstraint(_HASH_CHECK, name="ck_facility_restructures_hash"),
    )

    restructure_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    facility_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("financing_facilities.facility_id", ondelete="RESTRICT"),
        nullable=False,
    )
    restructured_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="RESTRICT"), nullable=False
    )
    old_schedule_version: Mapped[int] = mapped_column(Integer, nullable=False)
    new_schedule_version: Mapped[int] = mapped_column(Integer, nullable=False)
    reason_code: Mapped[str] = mapped_column(Text, nullable=False)
    comment: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


Index("ix_facility_restructures_facility_id", FacilityRestructureModel.facility_id)
Index("ix_facility_restructures_restructured_by_user_id", FacilityRestructureModel.restructured_by_user_id)
Index("ix_facility_restructures_recorded_at", FacilityRestructureModel.recorded_at)


class FacilityDefaultModel(Base):
    __tablename__ = "facility_defaults"
    __table_args__ = (
        UniqueConstraint("facility_id", "schedule_version", name="uq_facility_defaults_schedule"),
        CheckConstraint("schedule_version >= 1", name="ck_facility_defaults_schedule"),
        CheckConstraint("days_past_due > 0", name="ck_facility_defaults_days"),
        CheckConstraint(_HASH_CHECK, name="ck_facility_defaults_hash"),
    )

    default_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    facility_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("financing_facilities.facility_id", ondelete="RESTRICT"),
        nullable=False,
    )
    schedule_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    declared_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="RESTRICT"), nullable=False
    )
    defaulted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    days_past_due: Mapped[int] = mapped_column(Integer, nullable=False)
    reason_code: Mapped[str] = mapped_column(Text, nullable=False)
    comment: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


Index("ix_facility_defaults_declared_by_user_id", FacilityDefaultModel.declared_by_user_id)
Index("ix_facility_defaults_recorded_at", FacilityDefaultModel.recorded_at)


class FacilityWriteOffModel(Base):
    __tablename__ = "facility_writeoffs"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_facility_writeoffs_amount"),
        CheckConstraint(_HASH_CHECK, name="ck_facility_writeoffs_hash"),
    )

    writeoff_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    facility_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("financing_facilities.facility_id", ondelete="RESTRICT"),
        unique=True,
        nullable=False,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    auditor_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="RESTRICT"), nullable=False
    )
    reason_code: Mapped[str] = mapped_column(Text, nullable=False)
    comment: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


Index("ix_facility_writeoffs_auditor_user_id", FacilityWriteOffModel.auditor_user_id)
Index("ix_facility_writeoffs_recorded_at", FacilityWriteOffModel.recorded_at)


__all__ = [
    "FacilityDefaultModel",
    "FacilityDelinquencyModel",
    "FacilityRestructureModel",
    "FacilityWriteOffModel",
]
