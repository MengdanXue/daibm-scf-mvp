from __future__ import annotations

import math
from dataclasses import dataclass

from .schemas import FinancingRequestCreate


WEIGHTS = {
    "amount": 0.15,
    "payment_delay_days": 0.25,
    "counterparty_risk": 0.25,
    "invoice_mismatch": 0.18,
    "relationship_age_risk": 0.10,
    "transaction_velocity": 0.07,
}


# The single source for how a score becomes a band, a band becomes a
# decision, and a decision becomes a control action. These were previously
# copied between app.risk, app.service, and app.services.workflow, so moving
# a threshold in one place silently disagreed with the band written into the
# audit ledger by another.
HIGH_RISK_THRESHOLD = 0.72
MEDIUM_RISK_THRESHOLD = 0.45

BAND_DECISIONS = {
    "low": "approved",
    "medium": "manual_review",
    "high": "rejected",
}

DECISION_CONTROLS = {
    "approved": "standard_monitoring",
    "manual_review": "request_documents_and_enhanced_validation",
    "rejected": "suspend_auto_approval_and_enhanced_validation",
}


def band_for_score(score: float) -> str:
    if score >= HIGH_RISK_THRESHOLD:
        return "high"
    if score >= MEDIUM_RISK_THRESHOLD:
        return "medium"
    return "low"


@dataclass(frozen=True)
class RiskResult:
    score: float
    band: str
    contributions: list[dict[str, float | str]]


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def assess(request: FinancingRequestCreate) -> RiskResult:
    normalized = {
        "amount": _clamp(request.amount / 5_000_000),
        "payment_delay_days": _clamp(request.payment_delay_days / 60),
        "counterparty_risk": request.counterparty_risk,
        "invoice_mismatch": 1.0 if request.invoice_mismatch else 0.0,
        "relationship_age_risk": 1.0 - _clamp(request.relationship_months / 36),
        "transaction_velocity": _clamp(request.transactions_last_30d / 30),
    }
    contributions: list[dict[str, float | str]] = [
        {
            "feature": feature,
            "normalized_value": round(value, 4),
            "weighted_contribution": round(value * WEIGHTS[feature], 4),
        }
        for feature, value in normalized.items()
    ]
    # Only the weighted contribution is ever ordered, and it is always a
    # float; the annotation admits str because "feature" shares the mapping.
    contributions.sort(
        key=lambda item: float(item["weighted_contribution"]), reverse=True
    )
    weighted_risk = sum(
        normalized[feature] * weight for feature, weight in WEIGHTS.items()
    )
    score = 1.0 / (1.0 + math.exp(-(-1.2 + 2.8 * weighted_risk)))
    score = round(score, 4)
    band = band_for_score(score)
    return RiskResult(score=score, band=band, contributions=contributions)

