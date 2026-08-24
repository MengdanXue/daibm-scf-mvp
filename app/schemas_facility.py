from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    WithJsonSchema,
    field_validator,
    model_validator,
)

from app.domain.facility import exact_money


def _parse_money(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, Decimal)):
        raise ValueError("money must be supplied as a decimal string")
    if isinstance(value, str):
        if not value or value != value.strip():
            raise ValueError("money must be a non-blank decimal string")
        try:
            return Decimal(value)
        except InvalidOperation as error:
            raise ValueError("money must be a valid decimal string") from error
    return value


Money = Annotated[
    Decimal,
    BeforeValidator(_parse_money),
    AfterValidator(exact_money),
    WithJsonSchema({"type": "string"}),
]


class StrictCommandModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VersionedFacilityCommand(StrictCommandModel):
    version: int = Field(strict=True, ge=1)
    idempotency_key: UUID


class InstallmentRequest(StrictCommandModel):
    sequence: int = Field(strict=True, ge=1)
    due_date: date
    amount: Money


class CreateFacilityRequest(VersionedFacilityCommand):
    request_id: UUID
    principal: Money
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    installments: list[InstallmentRequest] = Field(min_length=1, max_length=120)

    @model_validator(mode="after")
    def schedule_must_reconcile_to_principal(self) -> CreateFacilityRequest:
        sequences = [item.sequence for item in self.installments]
        if sorted(sequences) != list(range(1, len(self.installments) + 1)):
            raise ValueError("installment sequences must be unique and contiguous")
        scheduled_total = sum(
            (item.amount for item in self.installments),
            start=Decimal("0.00"),
        )
        if scheduled_total != self.principal:
            raise ValueError("installment schedule must equal principal exactly")
        return self


class SubmitPaymentRequest(VersionedFacilityCommand):
    installment_id: UUID
    amount: Money
    payment_reference: str = Field(min_length=1, max_length=120)

    @field_validator("payment_reference")
    @classmethod
    def payment_reference_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("payment reference must not be blank")
        return normalized


class DecisionPaymentRequest(VersionedFacilityCommand):
    decision: Literal["confirmed", "rejected"]
    comment: str = Field(min_length=1, max_length=500)

    @field_validator("comment")
    @classmethod
    def comment_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("comment must not be blank")
        return normalized
