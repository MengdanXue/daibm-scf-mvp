from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Identity, Index, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class WorkflowActionModel(Base):
    __tablename__ = "workflow_actions"
    __table_args__ = (
        CheckConstraint(
            "actor_role IN ('supplier', 'core_enterprise', 'financier', "
            "'risk_manager', 'auditor')",
            name="ck_workflow_actions_actor_role",
        ),
    )

    action_id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True
    )
    request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("financing_requests.request_id", ondelete="RESTRICT"),
        nullable=False,
    )
    actor_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="RESTRICT"),
        nullable=False,
    )
    actor_role: Mapped[str] = mapped_column(Text, nullable=False)
    action_type: Mapped[str] = mapped_column(Text, nullable=False)
    from_status: Mapped[str | None] = mapped_column(Text)
    to_status: Mapped[str] = mapped_column(Text, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


Index("ix_workflow_actions_request_id", WorkflowActionModel.request_id)
Index("ix_workflow_actions_actor_user_id", WorkflowActionModel.actor_user_id)
Index("ix_workflow_actions_created_at", WorkflowActionModel.created_at)
