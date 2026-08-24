from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.identity import AuthenticatedUser
from app.models import FinancingRequestModel, LedgerEventModel
from app.models_facility import FacilityActionModel, PaymentModel
from app.repositories.facility import FacilityRepository
from app.schemas_facility import (
    CreateFacilityRequest,
    DecisionPaymentRequest,
    SubmitPaymentRequest,
    VersionedFacilityCommand,
)
from app.services.facility import (
    FacilityConflict,
    FacilityNotFound,
    FacilityService,
    ForbiddenFacility,
)
from app.services.identity import IdentityService


def _users(session_factory) -> dict[str, AuthenticatedUser]:
    identity = IdentityService(session_factory)
    identity.seed_demo_accounts()
    return {
        username: identity.login(username, "Demo123!").user
        for username in (
            "supplier.demo",
            "core.demo",
            "financier.demo",
            "risk.demo",
            "auditor.demo",
        )
    }


def _approved_application(
    session_factory,
    users: dict[str, AuthenticatedUser],
    *,
    amount: Decimal = Decimal("1000.00"),
) -> uuid.UUID:
    request_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    with session_factory.begin() as session:
        session.add(
            FinancingRequestModel(
                request_id=request_id,
                created_at=now,
                updated_at=now,
                applicant_id="supplier.demo",
                amount=amount,
                term_days=90,
                features={},
                risk_score=0.1,
                decision="approved",
                explanations=[],
                status="audited",
                version=7,
                created_by_user_id=users["supplier.demo"].user_id,
                supplier_organization_id=users[
                    "supplier.demo"
                ].organization_id,
                core_enterprise_organization_id=users[
                    "core.demo"
                ].organization_id,
            )
        )
    return request_id


def _create_request(
    request_id: uuid.UUID,
    *,
    key: uuid.UUID | None = None,
    amounts: tuple[str, ...] = ("500.00", "500.00"),
    due_dates: tuple[date, ...] | None = None,
) -> CreateFacilityRequest:
    active_due_dates = due_dates or tuple(
        date(2026, 10 + index, 1) for index in range(len(amounts))
    )
    return CreateFacilityRequest(
        request_id=request_id,
        principal=str(sum((Decimal(value) for value in amounts), Decimal("0.00"))),
        currency="RUB",
        version=1,
        idempotency_key=key or uuid.uuid4(),
        installments=[
            {"sequence": index + 1, "due_date": due, "amount": amount}
            for index, (due, amount) in enumerate(zip(active_due_dates, amounts))
        ],
    )


def _command(version: int, key: uuid.UUID | None = None) -> VersionedFacilityCommand:
    return VersionedFacilityCommand(
        version=version,
        idempotency_key=key or uuid.uuid4(),
    )


def _submit(
    facility: dict,
    installment_index: int,
    amount: str,
    reference: str,
    *,
    key: uuid.UUID | None = None,
) -> SubmitPaymentRequest:
    return SubmitPaymentRequest(
        installment_id=facility["installments"][installment_index]["installment_id"],
        amount=amount,
        payment_reference=reference,
        version=facility["version"],
        idempotency_key=key or uuid.uuid4(),
    )


def _decision(
    version: int,
    decision: str = "confirmed",
    *,
    key: uuid.UUID | None = None,
) -> DecisionPaymentRequest:
    return DecisionPaymentRequest(
        decision=decision,
        comment=f"Payment {decision}",
        version=version,
        idempotency_key=key or uuid.uuid4(),
    )


@pytest.fixture
def facility_context(session_factory):
    users = _users(session_factory)
    request_id = _approved_application(session_factory, users)
    return FacilityService(session_factory), users, request_id


def test_full_path_commits_exact_balances_actions_and_ledger(facility_context):
    service, users, request_id = facility_context
    facility = service.create(_create_request(request_id), users["financier.demo"])
    facility = service.initiate_disbursement(
        facility["facility_id"],
        _command(facility["version"]),
        users["financier.demo"],
    )
    facility = service.confirm_disbursement(
        facility["facility_id"],
        _command(facility["version"]),
        users["financier.demo"],
    )

    facility = service.submit_payment(
        facility["facility_id"],
        _submit(facility, 0, "500.00", "PAY-001"),
        users["supplier.demo"],
    )
    facility = service.decide_payment(
        facility["facility_id"],
        facility["payments"][0]["payment_id"],
        _decision(facility["version"]),
        users["financier.demo"],
    )
    assert facility["outstanding_amount"] == "500.00"
    assert facility["installments"][0]["paid_amount"] == "500.00"
    assert facility["installments"][0]["status"] == "paid"

    facility = service.submit_payment(
        facility["facility_id"],
        _submit(facility, 1, "500.00", "PAY-002"),
        users["supplier.demo"],
    )
    facility = service.decide_payment(
        facility["facility_id"],
        facility["payments"][1]["payment_id"],
        _decision(facility["version"]),
        users["financier.demo"],
    )
    assert facility["outstanding_amount"] == "0.00"
    assert facility["status"] == "repaid"
    facility = service.close(
        facility["facility_id"],
        _command(facility["version"]),
        users["auditor.demo"],
    )
    assert facility["status"] == "closed"

    with service.session_factory() as session:
        actions = list(
            session.scalars(
                select(FacilityActionModel.action_type).order_by(
                    FacilityActionModel.action_id
                )
            )
        )
        events = list(
            session.scalars(
                select(LedgerEventModel.event_type).order_by(LedgerEventModel.id)
            )
        )
    assert actions == [
        "create",
        "initiate_disbursement",
        "confirm_disbursement",
        "submit_payment",
        "confirm_payment",
        "submit_payment",
        "confirm_final_payment",
        "close",
    ]
    assert events == [
        "FACILITY_CREATED",
        "DISBURSEMENT_INITIATED",
        "DISBURSEMENT_CONFIRMED",
        "REPAYMENT_SUBMITTED",
        "REPAYMENT_CONFIRMED",
        "REPAYMENT_SUBMITTED",
        "REPAYMENT_CONFIRMED",
        "FACILITY_REPAID",
        "FACILITY_CLOSED",
    ]


def test_rejected_payment_preserves_balance_and_records_rejection(facility_context):
    service, users, request_id = facility_context
    facility = service.create(_create_request(request_id), users["financier.demo"])
    facility = service.initiate_disbursement(
        facility["facility_id"], _command(1), users["financier.demo"]
    )
    facility = service.confirm_disbursement(
        facility["facility_id"], _command(2), users["financier.demo"]
    )
    facility = service.submit_payment(
        facility["facility_id"],
        _submit(facility, 0, "100.00", "PAY-REJECT"),
        users["supplier.demo"],
    )
    facility = service.decide_payment(
        facility["facility_id"],
        facility["payments"][0]["payment_id"],
        _decision(facility["version"], "rejected"),
        users["financier.demo"],
    )
    assert facility["outstanding_amount"] == "1000.00"
    assert facility["payments"][0]["status"] == "rejected"


def test_command_retry_is_global_and_does_not_append_duplicate_records(
    facility_context,
):
    service, users, request_id = facility_context
    key = uuid.uuid4()
    request = _create_request(request_id, key=key)
    first = service.create(request, users["financier.demo"])
    service.initiate_disbursement(
        first["facility_id"], _command(first["version"]), users["financier.demo"]
    )

    replay = service.create(request, users["financier.demo"])
    assert replay["facility_id"] == first["facility_id"]
    assert replay["status"] == "disbursed"
    with service.session_factory() as session:
        assert session.scalar(
            select(func.count())
            .select_from(FacilityActionModel)
            .where(FacilityActionModel.idempotency_key == key)
        ) == 1


def test_concurrent_create_with_same_semantics_replays_one_facility(
    session_factory,
):
    users = _users(session_factory)
    request_id = _approved_application(session_factory, users)
    key = uuid.uuid4()
    request = _create_request(request_id, key=key)
    barrier = threading.Barrier(2)

    class FirstLookupBarrierRepository(FacilityRepository):
        def __init__(self) -> None:
            self._calls = 0
            self._calls_lock = threading.Lock()

        def find_action(self, session, candidate_key):
            with self._calls_lock:
                self._calls += 1
                should_wait = self._calls <= 2
            if should_wait:
                barrier.wait(timeout=5)
            return super().find_action(session, candidate_key)

    service = FacilityService(
        session_factory,
        repository=FirstLookupBarrierRepository(),
    )

    def create_once():
        return service.create(request, users["financier.demo"])

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: create_once(), range(2)))

    assert results[0]["facility_id"] == results[1]["facility_id"]
    with session_factory() as session:
        assert session.scalar(
            select(func.count()).select_from(FacilityActionModel)
        ) == 1
        assert session.scalar(
            select(func.count()).select_from(LedgerEventModel)
        ) == 1


def test_same_actor_cannot_reuse_key_for_another_command(facility_context):
    service, users, request_id = facility_context
    key = uuid.uuid4()
    facility = service.create(
        _create_request(request_id, key=key),
        users["financier.demo"],
    )

    with pytest.raises(FacilityConflict):
        service.initiate_disbursement(
            facility["facility_id"],
            _command(facility["version"], key),
            users["financier.demo"],
        )


def test_same_actor_cannot_reuse_key_for_another_facility_scope(session_factory):
    users = _users(session_factory)
    request_a = _approved_application(session_factory, users)
    request_b = _approved_application(session_factory, users)
    service = FacilityService(session_factory)
    key = uuid.uuid4()
    service.create(_create_request(request_a, key=key), users["financier.demo"])

    with pytest.raises(FacilityConflict):
        service.create(
            _create_request(request_b, key=key),
            users["financier.demo"],
        )


def test_same_actor_cannot_reuse_key_with_changed_payload(facility_context):
    service, users, request_id = facility_context
    facility = service.create(_create_request(request_id), users["financier.demo"])
    key = uuid.uuid4()
    facility = service.initiate_disbursement(
        facility["facility_id"],
        _command(facility["version"], key),
        users["financier.demo"],
    )

    with pytest.raises(FacilityConflict):
        service.initiate_disbursement(
            facility["facility_id"],
            _command(facility["version"], key),
            users["financier.demo"],
        )


def test_action_persists_canonical_command_fingerprint(facility_context):
    service, users, request_id = facility_context
    service.create(_create_request(request_id), users["financier.demo"])

    with service.session_factory() as session:
        payload = session.scalar(select(FacilityActionModel.payload))

    assert payload["command_name"] == "create"
    assert payload["scope"] == {"request_id": str(request_id)}
    assert len(payload["command_fingerprint_sha256"]) == 64


def test_stale_version_and_wrong_role_are_rejected_without_side_effects(
    facility_context,
):
    service, users, request_id = facility_context
    facility = service.create(_create_request(request_id), users["financier.demo"])
    with pytest.raises(ForbiddenFacility):
        service.initiate_disbursement(
            facility["facility_id"], _command(1), users["supplier.demo"]
        )
    with pytest.raises(FacilityConflict):
        service.initiate_disbursement(
            facility["facility_id"], _command(2), users["financier.demo"]
        )
    unchanged = service.get(facility["facility_id"], users["financier.demo"])
    assert unchanged["status"] == "ready_for_disbursement"
    assert unchanged["version"] == 1


def test_supplier_organization_isolation_hides_facility(facility_context):
    service, users, request_id = facility_context
    facility = service.create(_create_request(request_id), users["financier.demo"])
    outsider = replace(
        users["supplier.demo"],
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        organization_code="SUPPLIER-OTHER",
    )
    with pytest.raises(FacilityNotFound):
        service.get(facility["facility_id"], outsider)
    assert service.list_for_user(outsider) == []


def test_creation_requires_financier_and_approved_audited_application(
    facility_context,
):
    service, users, request_id = facility_context
    with pytest.raises(ForbiddenFacility):
        service.create(_create_request(request_id), users["supplier.demo"])

    with service.session_factory.begin() as session:
        application = session.get(FinancingRequestModel, request_id)
        application.decision = "manual_review"
    with pytest.raises(FacilityConflict):
        service.create(_create_request(request_id), users["financier.demo"])


def test_mark_overdue_requires_risk_manager_and_a_past_due_installment(
    facility_context,
):
    service, users, request_id = facility_context
    facility = service.create(
        _create_request(
            request_id,
            due_dates=(date(2025, 1, 1), date(2026, 11, 1)),
        ),
        users["financier.demo"],
    )
    facility = service.initiate_disbursement(
        facility["facility_id"], _command(1), users["financier.demo"]
    )
    facility = service.confirm_disbursement(
        facility["facility_id"], _command(2), users["financier.demo"]
    )
    with pytest.raises(ForbiddenFacility):
        service.mark_overdue(
            facility["facility_id"],
            facility["installments"][0]["installment_id"],
            _command(facility["version"]),
            users["financier.demo"],
        )
    overdue = service.mark_overdue(
        facility["facility_id"],
        facility["installments"][0]["installment_id"],
        _command(facility["version"]),
        users["risk.demo"],
    )
    assert overdue["status"] == "overdue"
    assert overdue["installments"][0]["status"] == "overdue"


def test_overpayment_rolls_back_balance_payment_and_ledger_head(facility_context):
    service, users, request_id = facility_context
    facility = service.create(_create_request(request_id), users["financier.demo"])
    facility = service.initiate_disbursement(
        facility["facility_id"], _command(1), users["financier.demo"]
    )
    facility = service.confirm_disbursement(
        facility["facility_id"], _command(2), users["financier.demo"]
    )
    facility = service.submit_payment(
        facility["facility_id"],
        _submit(facility, 0, "400.00", "PAY-400"),
        users["supplier.demo"],
    )
    facility = service.submit_payment(
        facility["facility_id"],
        _submit(facility, 0, "200.00", "PAY-200"),
        users["supplier.demo"],
    )
    first, second = facility["payments"]
    facility = service.decide_payment(
        facility["facility_id"],
        first["payment_id"],
        _decision(facility["version"]),
        users["financier.demo"],
    )
    with service.session_factory() as session:
        ledger_count = session.scalar(
            select(func.count()).select_from(LedgerEventModel)
        )

    with pytest.raises(FacilityConflict):
        service.decide_payment(
            facility["facility_id"],
            second["payment_id"],
            _decision(facility["version"]),
            users["financier.demo"],
        )

    unchanged = service.get(facility["facility_id"], users["financier.demo"])
    assert unchanged["outstanding_amount"] == "600.00"
    assert unchanged["installments"][0]["paid_amount"] == "400.00"
    assert unchanged["payments"][1]["status"] == "submitted"
    with service.session_factory() as session:
        assert session.scalar(
            select(func.count()).select_from(LedgerEventModel)
        ) == ledger_count


def test_real_barrier_concurrency_allows_only_one_final_confirmation(
    facility_context,
):
    service, users, request_id = facility_context
    facility = service.create(
        _create_request(request_id, amounts=("1000.00",)),
        users["financier.demo"],
    )
    facility = service.initiate_disbursement(
        facility["facility_id"], _command(1), users["financier.demo"]
    )
    facility = service.confirm_disbursement(
        facility["facility_id"], _command(2), users["financier.demo"]
    )
    for reference in ("CONCURRENT-A", "CONCURRENT-B"):
        facility = service.submit_payment(
            facility["facility_id"],
            _submit(facility, 0, "1000.00", reference),
            users["supplier.demo"],
        )

    payment_ids = [item["payment_id"] for item in facility["payments"]]
    shared_version = facility["version"]
    barrier = threading.Barrier(2)

    def confirm(payment_id: str):
        barrier.wait(timeout=5)
        try:
            result = service.decide_payment(
                facility["facility_id"],
                payment_id,
                _decision(shared_version),
                users["financier.demo"],
            )
            return ("ok", result["outstanding_amount"])
        except FacilityConflict:
            return ("conflict", None)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(confirm, payment_ids))

    assert sorted(item[0] for item in results) == ["conflict", "ok"]
    final = service.get(facility["facility_id"], users["financier.demo"])
    assert final["outstanding_amount"] == "0.00"
    assert final["status"] == "repaid"
    assert [payment["status"] for payment in final["payments"]].count(
        "confirmed"
    ) == 1


def test_duplicate_payment_reference_rolls_back_without_action_or_ledger(
    facility_context,
):
    service, users, request_id = facility_context
    facility = service.create(_create_request(request_id), users["financier.demo"])
    facility = service.initiate_disbursement(
        facility["facility_id"], _command(1), users["financier.demo"]
    )
    facility = service.confirm_disbursement(
        facility["facility_id"], _command(2), users["financier.demo"]
    )
    facility = service.submit_payment(
        facility["facility_id"],
        _submit(facility, 0, "100.00", "DUPLICATE-REF"),
        users["supplier.demo"],
    )
    with service.session_factory() as session:
        before = (
            session.scalar(select(func.count()).select_from(PaymentModel)),
            session.scalar(select(func.count()).select_from(FacilityActionModel)),
            session.scalar(select(func.count()).select_from(LedgerEventModel)),
        )
    with pytest.raises(FacilityConflict):
        service.submit_payment(
            facility["facility_id"],
            _submit(facility, 0, "50.00", "DUPLICATE-REF"),
            users["supplier.demo"],
        )
    with service.session_factory() as session:
        after = (
            session.scalar(select(func.count()).select_from(PaymentModel)),
            session.scalar(select(func.count()).select_from(FacilityActionModel)),
            session.scalar(select(func.count()).select_from(LedgerEventModel)),
        )
    assert after == before
