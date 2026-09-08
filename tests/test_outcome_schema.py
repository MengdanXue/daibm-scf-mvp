from datetime import datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas_outcome import ActualOutcomeCreate, OutcomeCorrectionCreate


def _submission() -> dict:
    return {
        "idempotency_key": str(uuid4()),
        "observed_at": "2026-08-24T12:00:00Z",
        "evidence_sha256": "a" * 64,
        "provenance": "CONTROLLED_DEMO",
    }


def test_actual_outcome_accepts_only_server_derived_fact_inputs():
    parsed = ActualOutcomeCreate.model_validate(_submission())

    assert parsed.observed_at == datetime.fromisoformat("2026-08-24T12:00:00+00:00")
    for client_fact in ("defaulted", "days_past_due", "loss_amount"):
        with pytest.raises(ValidationError, match="extra_forbidden"):
            ActualOutcomeCreate.model_validate({**_submission(), client_fact: 0})


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("observed_at", "2026-08-24T12:00:00"),
        ("evidence_sha256", "A" * 64),
        ("provenance", "SELF_REPORTED"),
    ),
)
def test_actual_outcome_rejects_unsafe_boundary_values(field, value):
    with pytest.raises(ValidationError):
        ActualOutcomeCreate.model_validate({**_submission(), field: value})


def test_correction_normalizes_reason_and_comment_and_forbids_unknown_fields():
    parsed = OutcomeCorrectionCreate.model_validate(
        {
            "idempotency_key": str(uuid4()),
            "action": "EXCLUDE",
            "reason_code": " inconsistent_lifecycle ",
            "comment": "  Verified against the immutable lifecycle.  ",
            "evidence_sha256": "b" * 64,
        }
    )

    assert parsed.reason_code == "INCONSISTENT_LIFECYCLE"
    assert parsed.comment == "Verified against the immutable lifecycle."
    with pytest.raises(ValidationError, match="extra_forbidden"):
        OutcomeCorrectionCreate.model_validate(
            {**parsed.model_dump(), "effective_training_eligible": False}
        )


@pytest.mark.parametrize(
    "change",
    (
        {"action": "DELETE"},
        {"reason_code": "x"},
        {"comment": "   "},
        {"comment": "x" * 501},
        {"evidence_sha256": "B" * 64},
    ),
)
def test_correction_rejects_invalid_contract_values(change):
    payload = {
        "idempotency_key": str(uuid4()),
        "action": "REINSTATE",
        "reason_code": "LIFECYCLE_VERIFIED",
        "comment": "Evidence verified",
        "evidence_sha256": "b" * 64,
    }
    with pytest.raises(ValidationError):
        OutcomeCorrectionCreate.model_validate({**payload, **change})
