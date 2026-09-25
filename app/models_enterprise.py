"""Enterprise readiness: audit grants, security events and versioned system config."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base

SECURITY_EVENT_TYPES = (
    "LOGIN_SUCCESS",
    "LOGIN_FAILURE",
    "LOGIN_LOCKED",
    "LOGOUT",
    "SESSION_EXPIRED",
    "PERMISSION_DENIED",
    "ADMIN_ACTION",
)


def _fk(target: str) -> ForeignKey:
    return ForeignKey(target, ondelete="RESTRICT")


class AuditGrantModel(Base):
    """An auditor may see an organization while the grant is not revoked."""

    __tablename__ = "audit_grants"
    __table_args__ = (
        CheckConstraint(
            "(revoked_at IS NULL) = (revoked_by_user_id IS NULL)",
            name="ck_audit_grants_revocation",
        ),
    )

    grant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    auditor_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), _fk("users.user_id"), nullable=False
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), _fk("organizations.organization_id"), nullable=False, index=True
    )
    granted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), _fk("users.user_id"), index=True
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), _fk("users.user_id"), index=True
    )


Index(
    "uq_audit_grants_active",
    AuditGrantModel.auditor_user_id,
    AuditGrantModel.organization_id,
    unique=True,
    postgresql_where=text("revoked_at IS NULL"),
)


class SecurityEventModel(Base):
    __tablename__ = "security_events"
    __table_args__ = (
        CheckConstraint(
            "event_type IN (" + ", ".join(f"'{t}'" for t in SECURITY_EVENT_TYPES) + ")",
            name="ck_security_events_type",
        ),
    )

    event_id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), _fk("users.user_id"), index=True
    )
    username: Mapped[str | None] = mapped_column(Text)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), _fk("organizations.organization_id"), index=True
    )
    action: Mapped[str] = mapped_column(Text, nullable=False)
    resource_type: Mapped[str | None] = mapped_column(Text)
    resource_id: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    client_ip: Mapped[str | None] = mapped_column(Text)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("clock_timestamp()"), index=True
    )


Index(
    "ix_security_events_type_recorded_at",
    SecurityEventModel.event_type,
    SecurityEventModel.recorded_at,
)


class SystemConfigModel(Base):
    __tablename__ = "system_config"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_system_config_version"),
        CheckConstraint(
            "rollback_of_version IS NULL OR rollback_of_version < version",
            name="ck_system_config_rollback",
        ),
    )

    config_key: Mapped[str] = mapped_column(Text, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), _fk("users.user_id"), index=True
    )
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    change_reason: Mapped[str] = mapped_column(Text, nullable=False)
    rollback_of_version: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("clock_timestamp()")
    )


__all__ = ["AuditGrantModel", "SECURITY_EVENT_TYPES", "SecurityEventModel", "SystemConfigModel"]
