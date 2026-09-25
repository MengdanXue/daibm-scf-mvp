"""Risk operations: versioned rules, alerts, tasks and their audit events."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from app.models import Base


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{value}'" for value in values) + ")"


_ALERT_STATUSES = ("OPEN", "ASSIGNED", "PROCESSING", "RESOLVED", "CLOSED")
_TASK_STATUSES = ("OPEN", "IN_PROGRESS", "COMPLETED", "CANCELLED")
_RISK_TYPES = ("MODEL_SCORE", "OVERDUE", "REPAYMENT_ANOMALY", "LIFECYCLE", "DATA_QUALITY")
_SEVERITIES = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
_TASK_TYPES = ("INVESTIGATION", "COLLECTION", "DISPOSAL_REVIEW", "DATA_FIX", "OTHER")
_ALERT_ACTIONS = (
    "CREATED", "ASSIGNED", "REASSIGNED", "STARTED", "RESOLVED", "CLOSED", "REOPENED", "COMMENTED",
)
_TASK_ACTIONS = (
    "CREATED", "REASSIGNED", "DUE_CHANGED", "NOTE_ADDED", "STARTED", "RESULT_ATTACHED",
    "COMPLETED", "CANCELLED",
)


def _fk(target: str) -> ForeignKey:
    return ForeignKey(target, ondelete="RESTRICT")


class RiskRuleModel(Base):
    __tablename__ = "risk_rules"
    __table_args__ = (
        CheckConstraint(_in("risk_type", _RISK_TYPES), name="ck_risk_rules_risk_type"),
        CheckConstraint(_in("severity", _SEVERITIES), name="ck_risk_rules_severity"),
        CheckConstraint("version >= 1", name="ck_risk_rules_version"),
        CheckConstraint("threshold IS NULL OR threshold >= 0", name="ck_risk_rules_threshold"),
    )

    rule_key: Mapped[str] = mapped_column(Text, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    risk_type: Mapped[str] = mapped_column(Text, nullable=False)
    threshold: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), _fk("users.user_id"), index=True
    )
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    change_reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("clock_timestamp()")
    )


class RiskAlertModel(Base):
    __tablename__ = "risk_alerts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["rule_key", "rule_version"],
            ["risk_rules.rule_key", "risk_rules.version"],
            name="fk_risk_alerts_rule",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("rule_key", "source_ref", name="uq_risk_alerts_source"),
        CheckConstraint(_in("status", _ALERT_STATUSES), name="ck_risk_alerts_status"),
        CheckConstraint(_in("risk_type", _RISK_TYPES), name="ck_risk_alerts_risk_type"),
        CheckConstraint(_in("severity", _SEVERITIES), name="ck_risk_alerts_severity"),
        CheckConstraint(
            "facility_id IS NOT NULL OR request_id IS NOT NULL", name="ck_risk_alerts_subject"
        ),
        CheckConstraint("(status = 'OPEN') = (owner_user_id IS NULL)", name="ck_risk_alerts_owner"),
        CheckConstraint(
            "(status IN ('RESOLVED', 'CLOSED')) = (resolution IS NOT NULL AND resolved_at IS NOT NULL)",
            name="ck_risk_alerts_resolution",
        ),
        CheckConstraint("(status = 'CLOSED') = (closed_at IS NOT NULL)", name="ck_risk_alerts_closed"),
        CheckConstraint("version >= 1", name="ck_risk_alerts_version"),
    )

    alert_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    rule_key: Mapped[str] = mapped_column(Text, nullable=False)
    rule_version: Mapped[int] = mapped_column(Integer, nullable=False)
    risk_type: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_ref: Mapped[str] = mapped_column(Text, nullable=False)
    facility_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), _fk("financing_facilities.facility_id"), index=True
    )
    request_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), _fk("financing_requests.request_id"), index=True
    )
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), _fk("organizations.organization_id"), index=True
    )
    trigger_reason: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), _fk("users.user_id"), index=True
    )
    resolution: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


Index("ix_risk_alerts_status_created_at", RiskAlertModel.status, RiskAlertModel.created_at)


class _EventColumns:
    event_id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    from_status: Mapped[str | None] = mapped_column(Text)
    to_status: Mapped[str] = mapped_column(Text, nullable=False)
    resulting_version: Mapped[int] = mapped_column(Integer, nullable=False)
    actor_role: Mapped[str] = mapped_column(Text, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)

    @declared_attr
    def actor_user_id(cls) -> Mapped[uuid.UUID | None]:
        return mapped_column(UUID(as_uuid=True), _fk("users.user_id"), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("clock_timestamp()"), index=True
    )


class RiskAlertEventModel(_EventColumns, Base):
    __tablename__ = "risk_alert_events"
    __table_args__ = (
        CheckConstraint(_in("action", _ALERT_ACTIONS), name="ck_risk_alert_events_action"),
        CheckConstraint(_in("to_status", _ALERT_STATUSES), name="ck_risk_alert_events_to_status"),
        CheckConstraint(
            "from_status IS NULL OR " + _in("from_status", _ALERT_STATUSES),
            name="ck_risk_alert_events_from_status",
        ),
        UniqueConstraint("alert_id", "resulting_version", name="uq_risk_alert_events_version"),
    )

    alert_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), _fk("risk_alerts.alert_id"), nullable=False
    )


class RiskTaskModel(Base):
    __tablename__ = "risk_tasks"
    __table_args__ = (
        CheckConstraint(_in("status", _TASK_STATUSES), name="ck_risk_tasks_status"),
        CheckConstraint(_in("task_type", _TASK_TYPES), name="ck_risk_tasks_type"),
        CheckConstraint(
            "(status = 'COMPLETED') = (completed_at IS NOT NULL AND result_summary IS NOT NULL)",
            name="ck_risk_tasks_completion",
        ),
        CheckConstraint("version >= 1", name="ck_risk_tasks_version"),
    )

    task_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    alert_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), _fk("risk_alerts.alert_id"), index=True
    )
    facility_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), _fk("financing_facilities.facility_id"), index=True
    )
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), _fk("organizations.organization_id"), index=True
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    task_type: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    assignee_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), _fk("users.user_id"), nullable=False, index=True
    )
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), _fk("users.user_id"), nullable=False, index=True
    )
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    result_summary: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


Index("ix_risk_tasks_status_due_at", RiskTaskModel.status, RiskTaskModel.due_at)


class RiskTaskEventModel(_EventColumns, Base):
    __tablename__ = "risk_task_events"
    __table_args__ = (
        CheckConstraint(_in("action", _TASK_ACTIONS), name="ck_risk_task_events_action"),
        CheckConstraint(_in("to_status", _TASK_STATUSES), name="ck_risk_task_events_to_status"),
        CheckConstraint(
            "from_status IS NULL OR " + _in("from_status", _TASK_STATUSES),
            name="ck_risk_task_events_from_status",
        ),
        UniqueConstraint("task_id", "resulting_version", name="uq_risk_task_events_version"),
    )

    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), _fk("risk_tasks.task_id"), nullable=False
    )


class RiskTaskAttachmentModel(Base):
    __tablename__ = "risk_task_attachments"
    __table_args__ = (
        CheckConstraint(
            "size_bytes BETWEEN 1 AND 2097152 AND size_bytes = octet_length(content)",
            name="ck_risk_task_attachments_size",
        ),
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="ck_risk_task_attachments_sha256"),
    )

    attachment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), _fk("risk_tasks.task_id"), nullable=False, index=True
    )
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    uploaded_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), _fk("users.user_id"), nullable=False, index=True
    )
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


__all__ = [
    "RiskAlertEventModel",
    "RiskAlertModel",
    "RiskRuleModel",
    "RiskTaskAttachmentModel",
    "RiskTaskEventModel",
    "RiskTaskModel",
]
