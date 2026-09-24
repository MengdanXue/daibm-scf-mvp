from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
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


FACILITY_STATUS_VALUES = (
    "ready_for_disbursement",
    "disbursed",
    "active",
    "overdue",
    "in_disposal",
    "restructured",
    "defaulted",
    "in_recovery",
    "repaid",
    "recovered",
    "written_off",
    "closed",
)
_STATUS_LIST = ", ".join(f"'{value}'" for value in FACILITY_STATUS_VALUES)


class FacilityStatusTransitionModel(Base):
    """Append-only from/to history of every facility status change."""

    __tablename__ = "facility_status_transitions"
    __table_args__ = (
        CheckConstraint(
            f"from_status IS NULL OR from_status IN ({_STATUS_LIST})",
            name="ck_facility_status_transitions_from",
        ),
        CheckConstraint(
            f"to_status IN ({_STATUS_LIST})",
            name="ck_facility_status_transitions_to",
        ),
        CheckConstraint(
            "from_status IS DISTINCT FROM to_status",
            name="ck_facility_status_transitions_changes_status",
        ),
        CheckConstraint(
            "resulting_version >= 1",
            name="ck_facility_status_transitions_version",
        ),
        CheckConstraint(
            "(actor_user_id IS NULL) = (trigger_action = 'migration_baseline')",
            name="ck_facility_status_transitions_actor",
        ),
    )

    transition_id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True
    )
    facility_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("financing_facilities.facility_id", ondelete="RESTRICT"),
        nullable=False,
    )
    from_status: Mapped[str | None] = mapped_column(Text)
    to_status: Mapped[str] = mapped_column(Text, nullable=False)
    trigger_action: Mapped[str] = mapped_column(Text, nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="RESTRICT")
    )
    actor_role: Mapped[str | None] = mapped_column(Text)
    resulting_version: Mapped[int] = mapped_column(Integer, nullable=False)
    reason_code: Mapped[str | None] = mapped_column(Text)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


Index(
    "ix_facility_status_transitions_facility_id",
    FacilityStatusTransitionModel.facility_id,
    FacilityStatusTransitionModel.transition_id,
)
Index(
    "ix_facility_status_transitions_actor_user_id",
    FacilityStatusTransitionModel.actor_user_id,
)
Index(
    "ix_facility_status_transitions_recorded_at",
    FacilityStatusTransitionModel.recorded_at,
)


class FacilityContractVersionModel(Base):
    """Immutable snapshot of each repayment contract a facility has run on."""

    __tablename__ = "facility_contract_versions"
    __table_args__ = (
        UniqueConstraint(
            "facility_id", "contract_version", name="uq_facility_contract_versions_version"
        ),
        CheckConstraint(
            "contract_version >= 1", name="ck_facility_contract_versions_version"
        ),
        CheckConstraint(
            "origin IN ('origination', 'restructure', 'migration_backfill')",
            name="ck_facility_contract_versions_origin",
        ),
        CheckConstraint(
            "(origin = 'restructure') = (restructure_id IS NOT NULL)",
            name="ck_facility_contract_versions_restructure_link",
        ),
        CheckConstraint(
            "outstanding_at_start IS NULL OR outstanding_at_start > 0",
            name="ck_facility_contract_versions_outstanding",
        ),
        CheckConstraint(
            "terms_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_facility_contract_versions_hash",
        ),
    )

    contract_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True
    )
    facility_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("financing_facilities.facility_id", ondelete="RESTRICT"),
        nullable=False,
    )
    contract_version: Mapped[int] = mapped_column(Integer, nullable=False)
    origin: Mapped[str] = mapped_column(Text, nullable=False)
    principal: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    outstanding_at_start: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(Text, nullable=False)
    schedule: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    superseded_schedule: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    restructure_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("facility_restructures.restructure_id", ondelete="RESTRICT"),
    )
    terms_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="RESTRICT")
    )
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


Index(
    "ix_facility_contract_versions_restructure_id",
    FacilityContractVersionModel.restructure_id,
)
Index(
    "ix_facility_contract_versions_created_by_user_id",
    FacilityContractVersionModel.created_by_user_id,
)
Index(
    "ix_facility_contract_versions_recorded_at",
    FacilityContractVersionModel.recorded_at,
)


class FacilityLifecycleDecisionModel(Base):
    """Governed risk decisions that open/close disposal and start recovery."""

    __tablename__ = "facility_lifecycle_decisions"
    __table_args__ = (
        CheckConstraint(
            "decision_type IN ('disposal_opened', 'disposal_closed', 'recovery_started')",
            name="ck_facility_lifecycle_decisions_type",
        ),
        CheckConstraint(
            "schedule_version >= 1", name="ck_facility_lifecycle_decisions_schedule"
        ),
        CheckConstraint(_HASH_CHECK, name="ck_facility_lifecycle_decisions_hash"),
    )

    decision_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    facility_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("financing_facilities.facility_id", ondelete="RESTRICT"),
        nullable=False,
    )
    decision_type: Mapped[str] = mapped_column(Text, nullable=False)
    schedule_version: Mapped[int] = mapped_column(Integer, nullable=False)
    decided_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="RESTRICT"), nullable=False
    )
    reason_code: Mapped[str] = mapped_column(Text, nullable=False)
    comment: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


Index(
    "ix_facility_lifecycle_decisions_facility_id",
    FacilityLifecycleDecisionModel.facility_id,
    FacilityLifecycleDecisionModel.recorded_at,
)
Index(
    "ix_facility_lifecycle_decisions_decided_by_user_id",
    FacilityLifecycleDecisionModel.decided_by_user_id,
)
Index(
    "ix_facility_lifecycle_decisions_recorded_at",
    FacilityLifecycleDecisionModel.recorded_at,
)


class FacilityRecoveryModel(Base):
    """Recovery cash from third-party sources after default.

    ``applied_to = 'outstanding'`` reduces the live balance during recovery and
    is part of principal conservation. ``applied_to = 'written_off'`` records
    cash collected on an already written-off claim; it reduces net loss but
    never re-opens the balance.
    """

    __tablename__ = "facility_recoveries"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_facility_recoveries_amount"),
        CheckConstraint(
            "applied_to IN ('outstanding', 'written_off')",
            name="ck_facility_recoveries_applied_to",
        ),
        CheckConstraint(
            "source IN ('GUARANTOR', 'COLLATERAL', 'CORE_ENTERPRISE_BUYBACK', "
            "'LEGAL_ENFORCEMENT', 'COLLECTION_AGENCY', 'INSURANCE', 'OTHER')",
            name="ck_facility_recoveries_source",
        ),
        CheckConstraint(_HASH_CHECK, name="ck_facility_recoveries_hash"),
        UniqueConstraint(
            "facility_id",
            "recovery_reference",
            name="uq_facility_recoveries_facility_reference",
        ),
    )

    recovery_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    facility_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("financing_facilities.facility_id", ondelete="RESTRICT"),
        nullable=False,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    applied_to: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    recovery_reference: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    recorded_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="RESTRICT"), nullable=False
    )
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


Index(
    "ix_facility_recoveries_facility_id",
    FacilityRecoveryModel.facility_id,
    FacilityRecoveryModel.recorded_at,
)
Index(
    "ix_facility_recoveries_recorded_by_user_id",
    FacilityRecoveryModel.recorded_by_user_id,
)
Index("ix_facility_recoveries_recorded_at", FacilityRecoveryModel.recorded_at)


__all__ = [
    "FACILITY_STATUS_VALUES",
    "FacilityContractVersionModel",
    "FacilityDefaultModel",
    "FacilityDelinquencyModel",
    "FacilityLifecycleDecisionModel",
    "FacilityRecoveryModel",
    "FacilityRestructureModel",
    "FacilityStatusTransitionModel",
    "FacilityWriteOffModel",
]
