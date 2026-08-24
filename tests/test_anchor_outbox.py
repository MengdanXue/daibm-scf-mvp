from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.models import LedgerEventModel
from app.models_advanced import AnchorOutboxModel
from app.repositories.ledger import LedgerRepository
from app.repositories.anchors import AnchorOutboxRepository


def test_ledger_append_enqueues_anchor_in_the_same_transaction(session_factory):
    subject_id = uuid.UUID("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")

    with session_factory.begin() as session:
        event = LedgerRepository().append_many(
            session,
            subject_id,
            [("FINANCING_REQUEST", {"invoice_number": "MUST-NOT-LEAK"})],
        )[0]

    with session_factory() as session:
        row = session.scalar(
            select(AnchorOutboxModel).where(
                AnchorOutboxModel.ledger_event_id == event.id
            )
        )
        assert row is not None
        assert row.subject_id == subject_id
        assert row.event_hash == event.event_hash
        assert row.chain_head_hash == event.event_hash
        assert row.status == "pending"
        assert row.attempt_count == 0


def test_business_transaction_rollback_leaves_neither_event_nor_anchor(session_factory):
    with pytest.raises(RuntimeError, match="abort business transaction"):
        with session_factory.begin() as session:
            LedgerRepository().append_many(
                session,
                uuid.UUID("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"),
                [("FINANCING_REQUEST", {"safe": "rollback"})],
            )
            raise RuntimeError("abort business transaction")

    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(LedgerEventModel)) == 0
        assert session.scalar(select(func.count()).select_from(AnchorOutboxModel)) == 0


def test_anchor_envelope_is_deterministic_idempotent_and_payload_free(session_factory):
    recorded_at = datetime(2026, 8, 24, 12, 55, tzinfo=timezone.utc)
    model_id = "12345678-1234-4567-89ab-1234567890ab"
    with session_factory.begin() as session:
        event = LedgerEventModel(
            created_at=recorded_at,
            stream_id="global",
            event_type="FINANCING_REQUEST",
            entity_id=uuid.UUID("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"),
            payload={
                "invoiceNumber": "INV-SECRET",
                "name": "Secret Supplier",
                "amount": "999.99",
                "credential": "secret-token",
                "model_version_id": model_id,
                "policy_version": "policy-v1",
                "circuit_version": "invoice-limit-v1",
                "proof_sha256": "b" * 64,
            },
            previous_hash="GENESIS",
            event_hash="a" * 64,
        )
        session.add(event)
        session.flush()
        repository = AnchorOutboxRepository()
        first = repository.enqueue(session, event)
        second = repository.enqueue(session, event)

        assert first.anchor_id == second.anchor_id
        assert repository.envelope(first) == {
            "anchorId": "3bb167cb-42ce-5835-bc8a-6375e232061c",
            "chainHeadHash": "a" * 64,
            "circuitVersion": "invoice-limit-v1",
            "eventHash": "a" * 64,
            "eventId": "39668847-2fe7-5352-94b9-6615fc5c15cf",
            "modelVersion": model_id,
            "policyVersion": "policy-v1",
            "proofSha256": "b" * 64,
            "recordedAt": "2026-08-24T12:55:00.000000+00:00",
            "schemaVersion": 1,
            "subjectId": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
        }

    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AnchorOutboxModel)) == 1


def test_concurrent_claims_skip_locked_rows(session_factory):
    subject_id = uuid.UUID("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")
    with session_factory.begin() as session:
        LedgerRepository().append_many(
            session,
            subject_id,
            [
                ("FINANCING_REQUEST", {"sequence": 1}),
                ("RISK_ASSESSMENT", {"sequence": 2}),
            ],
        )

    repository = AnchorOutboxRepository()
    now = datetime.now(timezone.utc) + timedelta(minutes=1)
    first_session = session_factory()
    second_session = session_factory()
    first_transaction = first_session.begin()
    second_transaction = second_session.begin()
    try:
        first = repository.claim_batch(
            first_session,
            now=now,
            limit=1,
            lease_duration=timedelta(seconds=30),
        )
        second = repository.claim_batch(
            second_session,
            now=now,
            limit=2,
            lease_duration=timedelta(seconds=30),
        )

        assert len(first) == 1
        assert len(second) == 1
        assert first[0].anchor_id != second[0].anchor_id
        assert first[0].attempt_count == second[0].attempt_count == 1
        assert first[0].lease_token is not None
        assert second[0].lease_token is not None
    finally:
        second_transaction.rollback()
        first_transaction.rollback()
        second_session.close()
        first_session.close()
