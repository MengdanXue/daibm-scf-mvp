from datetime import datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas_outcome import ActualOutcomeCreate


def _payload() -> dict:
    return {
        "idempotency_key": str(uuid4()),
        "defaulted": False,
        "days_past_due": 0,
        "loss_amount": "0.00",
        "observed_at": "2026-08-24T12:00:00Z",
        "evidence_sha256": "a" * 64,
        "provenance": "CONTROLLED_DEMO",
    }


def test_actual_outcome_schema_normalizes_money_and_requires_aware_timestamp():
    parsed = ActualOutcomeCreate.model_validate(_payload())

    assert parsed.loss_amount == Decimal("0.00")
    assert parsed.observed_at == datetime.fromisoformat("2026-08-24T12:00:00+00:00")

    invalid = _payload()
    invalid["observed_at"] = "2026-08-24T12:00:00"
    with pytest.raises(ValidationError, match="timezone-aware"):
        ActualOutcomeCreate.model_validate(invalid)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("days_past_due", -1),
        ("loss_amount", "-0.01"),
        ("evidence_sha256", "A" * 64),
        ("provenance", "SELF_REPORTED"),
    ),
)
def test_actual_outcome_schema_rejects_out_of_contract_values(field, value):
    payload = _payload()
    payload[field] = value

    with pytest.raises(ValidationError):
        ActualOutcomeCreate.model_validate(payload)


def test_actual_outcome_schema_forbids_unknown_fields():
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ActualOutcomeCreate.model_validate({**_payload(), "notes": "not allowed"})
