from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ActualOutcomeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: UUID
    defaulted: bool
    days_past_due: int = Field(ge=0)
    loss_amount: Decimal = Field(ge=Decimal("0.00"), max_digits=14, decimal_places=2)
    observed_at: datetime
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provenance: Literal["CONTROLLED_DEMO", "EXTERNAL_VERIFIED"]

    @field_validator("observed_at")
    @classmethod
    def observed_at_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        return value


__all__ = ["ActualOutcomeCreate"]
