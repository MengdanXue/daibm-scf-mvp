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
            "risk_score BETWEEN 0 AND 1",
            name="ck_financing_requests_risk_score",
        ),
        CheckConstraint(
            "decision IN ('approved', 'manual_review', 'rejected')",
            name="ck_financing_requests_decision",
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
    applicant_id: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    term_days: Mapped[int] = mapped_column(Integer, nullable=False)
    features: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    risk_score: Mapped[float] = mapped_column(DOUBLE_PRECISION, nullable=False)
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    explanations: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
    )
    control_action: Mapped[str] = mapped_column(Text, nullable=False)


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


class LedgerEventModel(Base):
    __tablename__ = "ledger_events"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ("
            "'FINANCING_REQUEST', "
            "'RISK_ASSESSMENT', "
            "'FINANCING_DECISION', "
            "'CONTROL_ACTION'"
            ")",
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
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("financing_requests.request_id", ondelete="RESTRICT"),
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
