from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    FetchedValue,
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
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


class FinancingFacilityModel(Base):
    __tablename__ = "financing_facilities"
    # The database fills organization_id when omitted; it is read on access
    # rather than via INSERT ... RETURNING (keeps pre-0020 schemas insertable).
    __mapper_args__ = {"eager_defaults": False}
    __table_args__ = (
        CheckConstraint(
            "principal > 0",
            name="ck_financing_facilities_principal_positive",
        ),
        CheckConstraint(
            "outstanding_amount BETWEEN 0 AND principal",
            name="ck_financing_facilities_outstanding_range",
        ),
        CheckConstraint(
            "currency = upper(currency) AND char_length(currency) = 3",
            name="ck_financing_facilities_currency",
        ),
        CheckConstraint(
            "status IN ('ready_for_disbursement', 'disbursed', 'active', "
            "'overdue', 'in_disposal', 'restructured', 'defaulted', "
            "'in_recovery', 'repaid', 'recovered', 'written_off', 'closed')",
            name="ck_financing_facilities_status",
        ),
        CheckConstraint(
            "current_schedule_version >= 1",
            name="ck_financing_facilities_schedule_version",
        ),
        CheckConstraint(
            "closure_reason IS NULL OR closure_reason IN "
            "('repaid', 'settled_after_default', 'written_off')",
            name="ck_financing_facilities_closure_reason",
        ),
        CheckConstraint(
            "version >= 1",
            name="ck_financing_facilities_version",
        ),
        CheckConstraint(
            "disbursement_evidence_sha256 IS NULL OR "
            "char_length(disbursement_evidence_sha256) = 64",
            name="ck_financing_facilities_disbursement_hash_length",
        ),
        UniqueConstraint(
            "request_id",
            name="uq_financing_facilities_request_id",
        ),
    )

    facility_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
    )
    request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("financing_requests.request_id", ondelete="RESTRICT"),
        nullable=False,
    )
    principal: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    outstanding_amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2),
        nullable=False,
    )
    currency: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    current_schedule_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
    )
    closure_reason: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="RESTRICT"),
        nullable=False,
    )
    disbursement_reference: Mapped[str | None] = mapped_column(Text)
    disbursement_evidence_sha256: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    disbursement_initiated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    disbursed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    repaid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The lending organization that owns the facility (tenant boundary).
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.organization_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        # Defaults to the creator's organization in the database when omitted.
        server_default=FetchedValue(),
    )


Index("ix_financing_facilities_request_id", FinancingFacilityModel.request_id)
Index(
    "ix_financing_facilities_created_by_user_id",
    FinancingFacilityModel.created_by_user_id,
)
Index("ix_financing_facilities_status", FinancingFacilityModel.status)
Index("ix_financing_facilities_updated_at", FinancingFacilityModel.updated_at.desc())


class InstallmentModel(Base):
    __tablename__ = "facility_installments"
    __table_args__ = (
        CheckConstraint(
            "sequence > 0",
            name="ck_facility_installments_sequence_positive",
        ),
        CheckConstraint(
            "amount > 0",
            name="ck_facility_installments_amount_positive",
        ),
        CheckConstraint(
            "paid_amount BETWEEN 0 AND amount",
            name="ck_facility_installments_paid_range",
        ),
        CheckConstraint(
            "status IN ('scheduled', 'partially_paid', 'paid', 'overdue', "
            "'superseded')",
            name="ck_facility_installments_status",
        ),
        CheckConstraint(
            "schedule_version >= 1",
            name="ck_facility_installments_schedule_version",
        ),
        UniqueConstraint(
            "facility_id",
            "schedule_version",
            "sequence",
            name="uq_facility_installments_facility_schedule_sequence",
        ),
        UniqueConstraint(
            "facility_id",
            "installment_id",
            name="uq_facility_installments_facility_installment",
        ),
    )

    installment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
    )
    facility_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("financing_facilities.facility_id", ondelete="RESTRICT"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    schedule_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
    )
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    paid_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )


Index("ix_facility_installments_facility_id", InstallmentModel.facility_id)
Index("ix_facility_installments_due_date", InstallmentModel.due_date)


class PaymentModel(Base):
    __tablename__ = "facility_payments"
    __table_args__ = (
        CheckConstraint(
            "amount > 0",
            name="ck_facility_payments_amount_positive",
        ),
        CheckConstraint(
            "status IN ('submitted', 'confirmed', 'rejected')",
            name="ck_facility_payments_status",
        ),
        CheckConstraint(
            "evidence_sha256 IS NULL OR char_length(evidence_sha256) = 64",
            name="ck_facility_payments_evidence_hash_length",
        ),
        UniqueConstraint(
            "facility_id",
            "payment_reference",
            name="uq_facility_payments_facility_reference",
        ),
        ForeignKeyConstraint(
            ["facility_id", "installment_id"],
            [
                "facility_installments.facility_id",
                "facility_installments.installment_id",
            ],
            ondelete="RESTRICT",
            name="fk_facility_payments_owned_installment",
        ),
    )

    payment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
    )
    facility_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("financing_facilities.facility_id", ondelete="RESTRICT"),
        nullable=False,
    )
    installment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    submitted_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="RESTRICT"),
        nullable=False,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    payment_reference: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_sha256: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="RESTRICT"),
    )
    decision_comment: Mapped[str | None] = mapped_column(Text)


Index("ix_facility_payments_facility_id", PaymentModel.facility_id)
Index("ix_facility_payments_installment_id", PaymentModel.installment_id)
Index(
    "ix_facility_payments_facility_installment",
    PaymentModel.facility_id,
    PaymentModel.installment_id,
)
Index("ix_facility_payments_submitted_by_user_id", PaymentModel.submitted_by_user_id)
Index("ix_facility_payments_decided_by_user_id", PaymentModel.decided_by_user_id)
Index("ix_facility_payments_submitted_at", PaymentModel.submitted_at)


class FacilityActionModel(Base):
    __tablename__ = "facility_actions"
    __table_args__ = (
        CheckConstraint(
            "actor_role IN ('supplier', 'core_enterprise', 'financier', "
            "'risk_manager', 'auditor')",
            name="ck_facility_actions_actor_role",
        ),
        CheckConstraint(
            "action_type IN ('create', 'initiate_disbursement', "
            "'confirm_disbursement', 'submit_payment', 'confirm_payment', "
            "'reject_payment', 'confirm_final_payment', 'mark_overdue', "
            "'open_disposal', 'close_disposal', 'restructure', "
            "'declare_default', 'start_recovery', 'record_recovery', "
            "'record_final_recovery', 'write_off', 'close')",
            name="ck_facility_actions_action_type",
        ),
        CheckConstraint(
            "expected_version >= 1",
            name="ck_facility_actions_expected_version",
        ),
        CheckConstraint(
            "resulting_version >= expected_version",
            name="ck_facility_actions_resulting_version",
        ),
        UniqueConstraint(
            "idempotency_key",
            name="uq_facility_actions_idempotency_key",
        ),
    )

    action_id: Mapped[int] = mapped_column(
        BigInteger,
        Identity(always=True),
        primary_key=True,
    )
    facility_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("financing_facilities.facility_id", ondelete="RESTRICT"),
        nullable=False,
    )
    actor_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="RESTRICT"),
        nullable=False,
    )
    actor_role: Mapped[str] = mapped_column(Text, nullable=False)
    action_type: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    expected_version: Mapped[int] = mapped_column(Integer, nullable=False)
    resulting_version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )


Index("ix_facility_actions_facility_id", FacilityActionModel.facility_id)
Index("ix_facility_actions_actor_user_id", FacilityActionModel.actor_user_id)
Index("ix_facility_actions_created_at", FacilityActionModel.created_at)


__all__ = [
    "FacilityActionModel",
    "FinancingFacilityModel",
    "InstallmentModel",
    "PaymentModel",
]
