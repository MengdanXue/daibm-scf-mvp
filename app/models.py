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
)
from sqlalchemy.dialects.postgresql import DOUBLE_PRECISION, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

__all__ = ["Base", "FinancingRequestModel", "LedgerEventModel"]

LEDGER_EVENT_TYPES = (
    "FINANCING_REQUEST",
    "RISK_ASSESSMENT",
    "FINANCING_DECISION",
    "CONTROL_ACTION",
    "MODEL_INFERENCE_COMPLETED",
    "RISK_POLICY_TRIGGERED",
    "CONTROL_ACTION_REQUESTED",
    "SIMULATED_RISK_INJECTED",
    "INTEGRITY_VIOLATION_DETECTED",
    "LEDGER_RECOVERY_COMPLETED",
    "APPLICATION_DRAFT_CREATED",
    "APPLICATION_UPDATED",
    "APPLICATION_SUBMITTED",
    "TRADE_CONFIRMED",
    "TRADE_RETURNED",
    "AUDIT_REVIEW_COMPLETED",
)


class Base(DeclarativeBase):
    pass


class FinancingRequestModel(Base):
    __tablename__ = "financing_requests"
    __table_args__ = (
        CheckConstraint(
            "amount > 0",
            name="ck_financing_requests_amount_positive",
        ),
        CheckConstraint(
            "term_days BETWEEN 1 AND 365",
            name="ck_financing_requests_term_days",
        ),
        CheckConstraint(
            "risk_score IS NULL OR risk_score BETWEEN 0 AND 1",
            name="ck_financing_requests_risk_score",
        ),
        CheckConstraint(
            "decision IS NULL OR decision IN ('approved', 'manual_review', 'rejected')",
            name="ck_financing_requests_decision",
        ),
        CheckConstraint(
            "status IN ('draft', 'submitted', 'trade_returned', "
            "'trade_confirmed', 'risk_assessed', 'approved', "
            "'manual_review', 'rejected', 'controlled', 'audited')",
            name="ck_financing_requests_status",
        ),
        CheckConstraint(
            "version >= 1",
            name="ck_financing_requests_version",
        ),
    )

    request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    applicant_id: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    term_days: Mapped[int] = mapped_column(Integer, nullable=False)
    features: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    risk_score: Mapped[float | None] = mapped_column(DOUBLE_PRECISION)
    decision: Mapped[str | None] = mapped_column(Text)
    explanations: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSONB,
    )
    control_action: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="draft",
        server_default="audited",
    )
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="RESTRICT"),
    )
    supplier_organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.organization_id", ondelete="RESTRICT"),
    )
    core_enterprise_organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.organization_id", ondelete="RESTRICT"),
    )
    contract_number: Mapped[str | None] = mapped_column(Text)
    invoice_number: Mapped[str | None] = mapped_column(Text)


Index(
    "ix_financing_requests_created_at",
    FinancingRequestModel.created_at.desc(),
)
Index(
    "ix_financing_requests_decision",
    FinancingRequestModel.decision,
)
Index(
    "ix_financing_requests_applicant_id",
    FinancingRequestModel.applicant_id,
)
Index("ix_financing_requests_status", FinancingRequestModel.status)
Index(
    "ix_financing_requests_supplier_organization_id",
    FinancingRequestModel.supplier_organization_id,
)
Index(
    "ix_financing_requests_core_enterprise_organization_id",
    FinancingRequestModel.core_enterprise_organization_id,
)
Index(
    "ix_financing_requests_created_by_user_id",
    FinancingRequestModel.created_by_user_id,
)
Index(
    "ix_financing_requests_updated_at",
    FinancingRequestModel.updated_at.desc(),
)


class LedgerEventModel(Base):
    __tablename__ = "ledger_events"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ("
            + ", ".join(f"'{value}'" for value in LEDGER_EVENT_TYPES)
            + ")",
            name="ck_ledger_events_event_type",
        ),
        CheckConstraint(
            "char_length(event_hash) = 64",
            name="ck_ledger_events_hash_length",
        ),
        CheckConstraint(
            "char_length(previous_hash) IN (7, 64)",
            name="ck_ledger_events_previous_hash_length",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        Identity(always=True),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    stream_id: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="global",
        server_default="global",
    )
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    previous_hash: Mapped[str] = mapped_column(Text, nullable=False)
    event_hash: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        unique=True,
    )


Index(
    "ix_ledger_events_entity_id_id",
    LedgerEventModel.entity_id,
    LedgerEventModel.id,
)
