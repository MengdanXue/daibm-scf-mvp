from __future__ import annotations

from pydantic import BaseModel, Field


class FinancingRequestCreate(BaseModel):
    applicant_id: str = Field(min_length=2, max_length=80)
    amount: float = Field(gt=0, le=20_000_000)
    term_days: int = Field(ge=7, le=365)
    payment_delay_days: int = Field(ge=0, le=365)
    counterparty_risk: float = Field(ge=0, le=1)
    invoice_mismatch: bool = False
    relationship_months: int = Field(ge=0, le=240)
    transactions_last_30d: int = Field(ge=0, le=500)


class Contribution(BaseModel):
    feature: str
    normalized_value: float
    weighted_contribution: float


class RiskAssessment(BaseModel):
    score: float
    band: str
    contributions: list[Contribution]

