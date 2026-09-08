from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class AnchorOutboxModel(Base):
    __tablename__ = "anchor_outbox"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'anchored', 'permanent_failed')",
            name="ck_anchor_outbox_status",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_anchor_outbox_attempt_count"),
        CheckConstraint("schema_version = 1", name="ck_anchor_outbox_schema_version"),
        CheckConstraint(
            "event_hash ~ '^[0-9a-f]{64}$' AND "
            "chain_head_hash ~ '^[0-9a-f]{64}$'",
            name="ck_anchor_outbox_hashes",
        ),
        CheckConstraint(
            "proof_sha256 IS NULL OR proof_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_anchor_outbox_proof_hash",
        ),
        CheckConstraint(
            "model_version IS NULL OR "
            "char_length(model_version) BETWEEN 1 AND 64 AND "
            "model_version ~ '^[A-Za-z0-9][A-Za-z0-9._-]*(@[A-Za-z0-9][A-Za-z0-9._-]*)?$'",
            name="ck_anchor_outbox_model_version",
        ),
        CheckConstraint(
            "policy_version IS NULL OR "
            "char_length(policy_version) BETWEEN 1 AND 64 AND "
            "policy_version ~ '^[A-Za-z0-9][A-Za-z0-9._-]*(@[A-Za-z0-9][A-Za-z0-9._-]*)?$'",
            name="ck_anchor_outbox_policy_version",
        ),
        CheckConstraint(
            "circuit_version IS NULL OR "
            "char_length(circuit_version) BETWEEN 1 AND 64 AND "
            "circuit_version ~ '^[A-Za-z0-9][A-Za-z0-9._-]*(@[A-Za-z0-9][A-Za-z0-9._-]*)?$'",
            name="ck_anchor_outbox_circuit_version",
        ),
        CheckConstraint(
            "(lease_token IS NULL) = (lease_expires_at IS NULL)",
            name="ck_anchor_outbox_lease_pair",
        ),
        CheckConstraint(
            "status != 'anchored' OR anchored_at IS NOT NULL",
            name="ck_anchor_outbox_anchored_at",
        ),
    )

    anchor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    ledger_event_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("ledger_events.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, unique=True
    )
    subject_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    event_hash: Mapped[str] = mapped_column(Text, nullable=False)
    chain_head_hash: Mapped[str] = mapped_column(Text, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    model_version: Mapped[str | None] = mapped_column(Text)
    policy_version: Mapped[str | None] = mapped_column(Text)
    circuit_version: Mapped[str | None] = mapped_column(Text)
    proof_sha256: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(Text)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    anchored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


Index(
    "ix_anchor_outbox_pending_claim",
    AnchorOutboxModel.next_attempt_at,
    AnchorOutboxModel.lease_expires_at,
    AnchorOutboxModel.created_at,
    postgresql_where=AnchorOutboxModel.status == "pending",
)


__all__ = ["AnchorOutboxModel"]
