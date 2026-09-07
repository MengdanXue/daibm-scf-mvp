from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import func, select

from app.ledger import GENESIS_HASH
from app.models import FinancingRequestModel, LedgerEventModel
from app.repositories import LedgerRepository
from app.schemas import FinancingRequestCreate
from app.service import FinancingService


def request_payload(applicant_id: str = "supplier-stable-01"):
    return FinancingRequestCreate(
        applicant_id=applicant_id,
        amount=450_000,
        term_days=60,
        payment_delay_days=2,
        counterparty_risk=0.12,
        invoice_mismatch=False,
        relationship_months=48,
        transactions_last_30d=8,
    )


def test_create_request_commits_one_request_and_four_events(session_factory):
    service = FinancingService(session_factory)

    result = service.create_request(request_payload())

    with session_factory() as session:
        request_count = session.scalar(
            select(func.count()).select_from(FinancingRequestModel)
        )
        event_count = session.scalar(
            select(func.count()).select_from(LedgerEventModel)
        )
    assert request_count == 1
    assert event_count == 4
    assert result["decision"] == "approved"
    assert result["amount"] == 450_000


def test_create_request_rolls_back_request_and_partial_events(
    session_factory,
    monkeypatch,
):
    service = FinancingService(session_factory)
    original = service.ledger_repository._build_event
    calls = 0

    def fail_on_third_event(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError("forced ledger failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(
        service.ledger_repository,
        "_build_event",
        fail_on_third_event,
    )

    with pytest.raises(RuntimeError, match="forced ledger failure"):
        service.create_request(request_payload())

    with session_factory() as session:
        request_count = session.scalar(
            select(func.count()).select_from(FinancingRequestModel)
        )
        event_count = session.scalar(
            select(func.count()).select_from(LedgerEventModel)
        )
    assert request_count == 0
    assert event_count == 0


def test_reset_rebuilds_three_scenarios_and_identity_sequence(session_factory):
    service = FinancingService(session_factory)
    service.seed_demo()
    service.tamper_demo_ledger()

    reset = service.reset_demo()

    assert len(reset) == 3
    assert {item["decision"] for item in reset} == {
        "approved",
        "manual_review",
        "rejected",
    }
    with session_factory() as session:
        events = service.ledger_repository.list_recent(session, limit=20)
        verification = service.ledger_repository.verify(session)
    assert sorted(event["id"] for event in events) == list(range(1, 13))
    assert verification["valid"] is True
    assert verification["event_count"] == 12


def test_concurrent_requests_do_not_fork_ledger_chain(session_factory):
    class SynchronizedLedgerRepository(LedgerRepository):
        def __init__(self, barrier):
            super().__init__()
            self.barrier = barrier

        def append_many(self, session, entity_id, event_specs):
            self.barrier.wait(timeout=10)
            return super().append_many(session, entity_id, event_specs)

    barrier = Barrier(6)
    service = FinancingService(
        session_factory,
        ledger_repository=SynchronizedLedgerRepository(barrier),
    )

    with ThreadPoolExecutor(max_workers=6) as executor:
        results = list(
            executor.map(
                service.create_request,
                [request_payload(f"supplier-{index}") for index in range(6)],
            )
        )

    assert len(results) == 6
    with session_factory() as session:
        verification = service.ledger_repository.verify(session)
        previous_hashes = list(
            session.scalars(
                select(LedgerEventModel.previous_hash).where(
                    LedgerEventModel.previous_hash != GENESIS_HASH
                )
            )
        )
    assert verification["valid"] is True
    assert verification["event_count"] == 24
    assert len(previous_hashes) == len(set(previous_hashes))
