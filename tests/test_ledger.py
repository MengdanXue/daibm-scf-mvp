import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.ledger import (
    GENESIS_HASH,
    calculate_hash,
    canonical_json,
    canonical_timestamp,
)
from app.models import FinancingRequestModel
from app.repositories import FinancingRequestRepository, LedgerRepository


def financing_request_model() -> FinancingRequestModel:
    return FinancingRequestModel(
        request_id=uuid.uuid4(),
        created_at=datetime.now(timezone.utc),
        applicant_id="supplier-ledger-test",
        amount=Decimal("100.00"),
        term_days=30,
        features={"amount": 100.0, "term_days": 30},
        risk_score=0.2,
        decision="approved",
        explanations=[{"feature": "payment_delay_days", "value": 2}],
        control_action="standard_monitoring",
    )


def test_canonical_json_is_stable_across_key_order():
    assert canonical_json({"b": 2, "a": 1}) == '{"a":1,"b":2}'


def test_canonical_timestamp_normalizes_utc_representation():
    value = datetime(2026, 8, 14, 12, 30, 15, 123456, tzinfo=timezone.utc)

    assert canonical_timestamp(value) == "2026-08-14T12:30:15.123456+00:00"


def test_canonical_timestamp_rejects_naive_datetime():
    with pytest.raises(ValueError, match="timezone-aware"):
        canonical_timestamp(datetime(2026, 8, 14, 12, 30, 15))


def test_hash_changes_when_payload_changes():
    timestamp = "2026-08-14T00:00:00.000000+00:00"
    entity_id = "00000000-0000-0000-0000-000000000001"

    first = calculate_hash(
        GENESIS_HASH,
        timestamp,
        "FINANCING_REQUEST",
        entity_id,
        canonical_json({"amount": 100}),
    )
    second = calculate_hash(
        GENESIS_HASH,
        timestamp,
        "FINANCING_REQUEST",
        entity_id,
        canonical_json({"amount": 999}),
    )

    assert first != second


def test_ledger_repository_appends_and_verifies_jsonb_chain(session_factory):
    financing_repository = FinancingRequestRepository()
    ledger_repository = LedgerRepository()
    request = financing_request_model()

    with session_factory.begin() as session:
        financing_repository.add(session, request)
        ledger_repository.append_many(
            session,
            request.request_id,
            [
                ("FINANCING_REQUEST", {"amount": 100}),
                ("FINANCING_DECISION", {"decision": "approved"}),
            ],
        )

    with session_factory() as session:
        verification = ledger_repository.verify(session)
        events = ledger_repository.list(session, limit=10)

    assert verification == {
        "valid": True,
        "event_count": 2,
        "head_hash": events[0]["event_hash"],
    }
    assert events[0]["event_type"] == "FINANCING_DECISION"
    assert events[1]["previous_hash"] == GENESIS_HASH
    assert events[1]["payload"] == {"amount": 100}


def test_ledger_repository_detects_jsonb_tampering(session_factory):
    financing_repository = FinancingRequestRepository()
    ledger_repository = LedgerRepository()
    request = financing_request_model()

    with session_factory.begin() as session:
        financing_repository.add(session, request)
        ledger_repository.append_many(
            session,
            request.request_id,
            [
                ("FINANCING_REQUEST", {"amount": 100}),
                ("RISK_ASSESSMENT", {"score": 0.2}),
            ],
        )

    with session_factory.begin() as session:
        tampered_event_id = ledger_repository.tamper_first_risk_event(session)

    with session_factory() as session:
        verification = ledger_repository.verify(session)

    assert verification["valid"] is False
    assert verification["invalid_event_id"] == tampered_event_id
    assert verification["reason"] == "event_hash_mismatch"
