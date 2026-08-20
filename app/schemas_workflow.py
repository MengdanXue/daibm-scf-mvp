from __future__ import annotations

from decimal import Decimal

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas import FinancingRequestCreate


class ApplicationDraftCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    core_enterprise_organization_code: str = Field(min_length=2, max_length=80)
    contract_number: str = Field(min_length=2, max_length=120)
    invoice_number: str = Field(min_length=2, max_length=120)
    amount: float = Field(gt=0, le=20_000_000)
    term_days: int = Field(ge=7, le=365)
    payment_delay_days: int = Field(ge=0, le=365)
    counterparty_risk: float = Field(ge=0, le=1)
    invoice_mismatch: bool = False
    relationship_months: int = Field(ge=0, le=240)
    transactions_last_30d: int = Field(ge=0, le=500)

    @field_validator(
        "core_enterprise_organization_code",
        "contract_number",
        "invoice_number",
    )
    @classmethod
    def text_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized

    @field_validator("amount")
    @classmethod
    def amount_has_at_most_two_decimal_places(cls, value: float) -> float:
        decimal_value = Decimal(str(value))
        if decimal_value != decimal_value.quantize(Decimal("0.01")):
            raise ValueError("amount must have at most two decimal places")
        return float(decimal_value)

    def to_risk_request(self, applicant_id: str) -> FinancingRequestCreate:
        return FinancingRequestCreate(
            applicant_id=applicant_id,
            amount=self.amount,
            term_days=self.term_days,
            payment_delay_days=self.payment_delay_days,
            counterparty_risk=self.counterparty_risk,
            invoice_mismatch=self.invoice_mismatch,
            relationship_months=self.relationship_months,
            transactions_last_30d=self.transactions_last_30d,
        )


class ApplicationDraftUpdate(ApplicationDraftCreate):
    version: int = Field(ge=1)


class VersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)


class TradeConfirmationRequest(VersionRequest):
    confirmed: bool
    comment: str = Field(min_length=1, max_length=500)


class FinancingDecisionRequest(VersionRequest):
    decision: Literal["approved", "manual_review", "rejected"]
    comment: str = Field(min_length=1, max_length=500)


class CommentVersionRequest(VersionRequest):
    comment: str = Field(min_length=1, max_length=500)
