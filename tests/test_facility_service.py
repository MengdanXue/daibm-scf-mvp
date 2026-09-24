from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import DBAPIError

from app.identity import AuthenticatedUser
from app.models import FinancingRequestModel, LedgerEventModel
from app.models_facility import (
    FacilityActionModel,
    FinancingFacilityModel,
    InstallmentModel,
    PaymentModel,
)
from app.models_lifecycle import (
    FacilityDefaultModel,
    FacilityDelinquencyModel,
    FacilityRestructureModel,
    FacilityWriteOffModel,
)
from app.repositories.facility import FacilityRepository
from app.schemas_facility import (
    CreateFacilityRequest,
    DeclareDefaultRequest,
    DecisionPaymentRequest,
    LifecycleDecisionRequest,
    MarkOverdueRequest,
    RestructureFacilityRequest,
    SubmitPaymentRequest,
    VersionedFacilityCommand,
    WriteOffRequest,
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


def _activate(
    service: FacilityService,
    users: dict[str, AuthenticatedUser],
    request_id: uuid.UUID,
    *,
    amounts: tuple[str, ...] = ("500.00", "500.00"),
) -> dict:
    facility = service.create(
        _create_request(
            request_id,
            amounts=amounts,
            due_dates=tuple(
                date(2025, index + 1, 1) for index in range(len(amounts))
            ),
        ),
        users["financier.demo"],
    )
    facility = service.initiate_disbursement(
        facility["facility_id"],
        _command(facility["version"]),
        users["financier.demo"],
    )
    return service.confirm_disbursement(
        facility["facility_id"],
        _command(facility["version"]),
        users["financier.demo"],
    )


def _mark_overdue(
    service: FacilityService,
    users: dict[str, AuthenticatedUser],
    facility: dict,
    *,
    installment_index: int = 0,
    days: int = 45,
) -> dict:
    installment_id = facility["installments"][installment_index]["installment_id"]
    return service.mark_overdue(
        facility["facility_id"],
        installment_id,
        MarkOverdueRequest(
            installment_id=installment_id,
            days_past_due=days,
            evidence_sha256="d" * 64,
            version=facility["version"],
            idempotency_key=uuid.uuid4(),
        ),
        users["financier.demo"],
    )


def _restructure(
    version: int,
    *,
    total: str = "500.00",
    due_date: str = "2027-01-01",
    key=None,
):
    return RestructureFacilityRequest(
        version=version,
        idempotency_key=key or uuid.uuid4(),
        reason_code="BORROWER_CASH_FLOW",
        comment="Verified revised repayment capacity",
        evidence_sha256="e" * 64,
        installments=[
            {"sequence": 1, "due_date": due_date, "amount": total}
        ],
    )


def _declare_default(version: int, *, key=None):
    return DeclareDefaultRequest(
        version=version,
        idempotency_key=key or uuid.uuid4(),
        reason_code="PAYMENT_DEFAULT",
        comment="Governed delinquency remains unresolved",
        evidence_sha256="f" * 64,
        defaulted_at="2026-08-24T12:00:00Z",
        days_past_due=90,
    )


def _write_off(version: int, *, key=None):
    return WriteOffRequest(
        version=version,
        idempotency_key=key or uuid.uuid4(),
        reason_code="UNCOLLECTIBLE_BALANCE",
        comment="Independent recovery review completed",
        evidence_sha256="a" * 64,
    )


def _defaulted(
    service: FacilityService,
    users: dict[str, AuthenticatedUser],
    request_id: uuid.UUID,
    *,
    amounts: tuple[str, ...] = ("500.00", "500.00"),
) -> dict:
    facility = _mark_overdue(
        service,
        users,
        _activate(service, users, request_id, amounts=amounts),
    )
    facility = _open_disposal(service, users, facility)
    return service.declare_default(
        facility["facility_id"],
        _declare_default(facility["version"]),
        users["risk.demo"],
    )


def _lifecycle(version: int, reason: str = "RISK_REVIEW", *, key=None):
    return LifecycleDecisionRequest(
        version=version,
        idempotency_key=key or uuid.uuid4(),
        reason_code=reason,
        comment="Governed lifecycle decision",
        evidence_sha256="c" * 64,
    )


def _open_disposal(service: FacilityService, users, facility: dict) -> dict:
    return service.open_disposal(
        facility["facility_id"],
        _lifecycle(facility["version"], "ARREARS_WORKOUT"),
        users["risk.demo"],
    )


def _start_recovery(service: FacilityService, users, facility: dict) -> dict:
    return service.start_recovery(
        facility["facility_id"],
        _lifecycle(facility["version"], "LEGAL_RECOVERY"),
        users["risk.demo"],
    )


def _persistence_snapshot(service: FacilityService, facility_id: str) -> dict:
    normalized_id = uuid.UUID(facility_id)
    with service.session_factory() as session:
        facility = session.get(FinancingFacilityModel, normalized_id)
        return {
            "facility": (
                facility.status,
                facility.version,
                Decimal(facility.outstanding_amount),
                facility.current_schedule_version,
                facility.closure_reason,
            ),
            "installments": [
                (
                    row.installment_id,
                    row.schedule_version,
                    Decimal(row.paid_amount),
                    row.status,
                )
                for row in session.scalars(
                    select(InstallmentModel)
                    .where(InstallmentModel.facility_id == normalized_id)
                    .order_by(
                        InstallmentModel.schedule_version,
                        InstallmentModel.sequence,
                    )
                )
            ],
            "payments": [
                (row.payment_id, row.status, Decimal(row.amount))
                for row in session.scalars(
                    select(PaymentModel)
                    .where(PaymentModel.facility_id == normalized_id)
                    .order_by(PaymentModel.submitted_at, PaymentModel.payment_id)
                )
            ],
            "actions": session.scalar(
                select(func.count()).select_from(FacilityActionModel).where(
                    FacilityActionModel.facility_id == normalized_id
                )
            ),
            "ledger": session.scalar(
                select(func.count()).select_from(LedgerEventModel).where(
                    LedgerEventModel.entity_id == normalized_id
                )
            ),
            "delinquencies": session.scalar(
                select(func.count()).select_from(FacilityDelinquencyModel).where(
                    FacilityDelinquencyModel.facility_id == normalized_id
                )
            ),
            "restructures": session.scalar(
                select(func.count()).select_from(FacilityRestructureModel).where(
                    FacilityRestructureModel.facility_id == normalized_id
                )
            ),
            "defaults": session.scalar(
                select(func.count()).select_from(FacilityDefaultModel).where(
                    FacilityDefaultModel.facility_id == normalized_id
                )
            ),
            "writeoffs": session.scalar(
                select(func.count()).select_from(FacilityWriteOffModel).where(
                    FacilityWriteOffModel.facility_id == normalized_id
                )
            ),
        }


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
    assert facility["closure_reason"] == "repaid"

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


def test_mark_overdue_requires_financier_and_a_past_due_installment(
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
    overdue_command = MarkOverdueRequest(
        installment_id=facility["installments"][0]["installment_id"],
        days_past_due=30,
        evidence_sha256="c" * 64,
        version=facility["version"],
        idempotency_key=uuid.uuid4(),
    )
    with pytest.raises(ForbiddenFacility):
        service.mark_overdue(
            facility["facility_id"],
            facility["installments"][0]["installment_id"],
            overdue_command,
            users["risk.demo"],
        )
    overdue = service.mark_overdue(
        facility["facility_id"],
        facility["installments"][0]["installment_id"],
        overdue_command,
        users["financier.demo"],
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


def test_restructure_preserves_paid_history_and_replaces_exact_outstanding(
    facility_context,
):
    service, users, request_id = facility_context
    facility = _activate(service, users, request_id)
    facility = service.submit_payment(
        facility["facility_id"],
        _submit(facility, 0, "500.00", "PAID-BEFORE-RESTRUCTURE"),
        users["supplier.demo"],
    )
    facility = service.decide_payment(
        facility["facility_id"],
        facility["payments"][0]["payment_id"],
        _decision(facility["version"]),
        users["financier.demo"],
    )
    facility = _mark_overdue(service, users, facility, installment_index=1)
    facility = _open_disposal(service, users, facility)

    result = service.restructure(
        facility["facility_id"],
        _restructure(facility["version"]),
        users["risk.demo"],
    )

    assert result["status"] == "restructured"
    assert result["outstanding_amount"] == "500.00"
    assert result["current_schedule_version"] == 2
    old_rows = [row for row in result["installments"] if row["schedule_version"] == 1]
    new_rows = [row for row in result["installments"] if row["schedule_version"] == 2]
    assert [(row["status"], row["paid_amount"]) for row in old_rows] == [
        ("paid", "500.00"),
        ("superseded", "0.00"),
    ]
    assert [row["sequence"] for row in new_rows] == [1]
    assert sum(Decimal(row["amount"]) for row in new_rows) == Decimal("500.00")
    assert result["restructures"][0]["old_schedule_version"] == 1
    assert result["restructures"][0]["new_schedule_version"] == 2
    assert result["delinquencies"][0]["days_past_due"] == 45
    original, replacement = result["contract_versions"]
    assert (original["contract_version"], original["origin"]) == (1, "origination")
    assert [row["amount"] for row in original["schedule"]] == ["500.00", "500.00"]
    assert (replacement["contract_version"], replacement["origin"]) == (2, "restructure")
    assert replacement["restructure_id"] == result["restructures"][0]["restructure_id"]
    assert replacement["outstanding_at_start"] == "500.00"
    assert replacement["superseded_schedule"] == [
        {
            "installment_id": old_rows[1]["installment_id"],
            "sequence": 2,
            "due_date": old_rows[1]["due_date"],
            "amount": "500.00",
            "paid_amount": "0.00",
            "status_before": "overdue",
        }
    ]
    assert original["terms_sha256"] != replacement["terms_sha256"]


def test_database_rejects_unfunded_balance_reduction(facility_context):
    service, users, request_id = facility_context
    facility = _activate(service, users, request_id)
    with pytest.raises(DBAPIError, match="principal conservation"):
        with service.session_factory.begin() as session:
            session.execute(text(
                "UPDATE financing_facilities SET outstanding_amount = 900.00 "
                "WHERE facility_id = :id"
            ), {"id": facility["facility_id"]})
    assert service.get(facility["facility_id"], users["financier.demo"])["outstanding_amount"] == "1000.00"


def test_default_restructure_rejects_pending_and_stale_without_mutation(facility_context):
    service, users, request_id = facility_context
    facility = _defaulted(service, users, request_id)
    before = _persistence_snapshot(service, facility["facility_id"])
    with pytest.raises(FacilityConflict, match="version"):
        service.restructure(facility["facility_id"], _restructure(facility["version"] - 1, total="1000.00"), users["risk.demo"])
    assert _persistence_snapshot(service, facility["facility_id"]) == before
    facility = service.submit_payment(facility["facility_id"], _submit(facility, 0, "100.00", "pending"), users["supplier.demo"])
    before = _persistence_snapshot(service, facility["facility_id"])
    with pytest.raises(FacilityConflict, match="Pending payment"):
        service.restructure(facility["facility_id"], _restructure(facility["version"], total="1000.00"), users["risk.demo"])
    assert _persistence_snapshot(service, facility["facility_id"]) == before


def test_cash_writer_invalidates_repeatable_read_snapshot(facility_context):
    service, users, request_id = facility_context
    facility = _activate(service, users, request_id)
    for reference in ("cash-a", "cash-b"):
        facility = service.submit_payment(facility["facility_id"], _submit(facility, 0, "100.00", reference), users["supplier.demo"])
    engine = service.session_factory.kw["bind"]
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as stale:
        transaction = stale.begin()
        stale.execute(text("SELECT count(*) FROM facility_payments"))
        with engine.begin() as first:
            first.execute(text("UPDATE facility_payments SET status='confirmed' WHERE payment_id=:id"), {"id": facility["payments"][0]["payment_id"]})
            first.execute(text("UPDATE financing_facilities SET outstanding_amount=900.00 WHERE facility_id=:id"), {"id": facility["facility_id"]})
        with pytest.raises(DBAPIError) as captured:
            stale.execute(text("UPDATE facility_payments SET status='confirmed' WHERE payment_id=:id"), {"id": facility["payments"][1]["payment_id"]})
        assert captured.value.orig.sqlstate == "40001"
        transaction.rollback()
    result = service.get(facility["facility_id"], users["financier.demo"])
    assert result["recovered_amount"] == "100.00"
    assert result["outstanding_balance"] == "900.00"


def test_default_restructure_recovery_preserves_episode_and_conserves_cash(facility_context):
    service, users, request_id = facility_context
    facility = _defaulted(service, users, request_id)
    original = facility["default_event"]
    facility = service.restructure(
        facility["facility_id"], _restructure(facility["version"], total="1000.00"),
        users["risk.demo"],
    )
    assert facility["default_history"] == [original]
    facility = service.submit_payment(
        facility["facility_id"], _submit(facility, 2, "1000.00", "replacement-cash"),
        users["supplier.demo"],
    )
    assert facility["recovered_amount"] == "0.00"
    facility = service.decide_payment(
        facility["facility_id"], facility["payments"][-1]["payment_id"],
        _decision(facility["version"]), users["financier.demo"],
    )
    assert facility["status"] == "recovered"
    closed = service.close(
        facility["facility_id"], _command(facility["version"]), users["auditor.demo"],
    )
    assert closed["settlement_classification"] == "SETTLED_AFTER_DEFAULT"
    assert closed["closure_reason"] == "settled_after_default"
    assert closed["default_history"] == [original]
    assert closed["outstanding_balance"] == "0.00"
    assert closed["recovered_amount"] == "1000.00"
    assert closed["written_off_amount"] == closed["realized_loss"] == "0.00"


def test_replacement_schedule_can_default_again_and_restructure_again(facility_context):
    service, users, request_id = facility_context
    facility = _defaulted(service, users, request_id)
    original = facility["default_event"]
    facility = service.restructure(
        facility["facility_id"], _restructure(facility["version"], total="1000.00"),
        users["risk.demo"],
    )
    service.clock = lambda: datetime(2027, 2, 1, tzinfo=timezone.utc)
    facility = _mark_overdue(service, users, facility, installment_index=2)
    facility = _open_disposal(service, users, facility)
    facility = service.declare_default(
        facility["facility_id"], _declare_default(facility["version"]), users["risk.demo"],
    )
    assert [row["schedule_version"] for row in facility["default_history"]] == [1, 2]
    assert facility["default_history"][0] == original
    assert facility["default_event"] == original
    from app.services.outcomes import derive_outcome_facts
    with service.session_factory() as session:
        row = session.get(FinancingFacilityModel, uuid.UUID(facility["facility_id"]))
        facts = derive_outcome_facts(session, row)
        assert facts.defaulted is True
        assert facts.loss_amount == Decimal("0.00")
    facility = service.restructure(
        facility["facility_id"],
        _restructure(facility["version"], total="1000.00", due_date="2028-01-01"),
        users["risk.demo"],
    )
    assert facility["current_schedule_version"] == 3
    assert len(facility["restructures"]) == 2
    assert [row["status"] for row in facility["installments"]] == [
        "superseded", "superseded", "superseded", "scheduled",
    ]
    listed = service.list_for_user(users["risk.demo"])
    assert listed[0]["default_history"] == facility["default_history"]


def test_default_recovery_and_close_retain_default_evidence(facility_context):
    service, users, request_id = facility_context
    facility = _mark_overdue(service, users, _activate(service, users, request_id))
    facility = _open_disposal(service, users, facility)
    facility = service.declare_default(
        facility["facility_id"],
        _declare_default(facility["version"]),
        users["risk.demo"],
    )
    default_id = facility["default_event"]["default_id"]
    assert facility["status"] == "defaulted"

    for index, reference in enumerate(("DEFAULT-RECOVERY-A", "DEFAULT-RECOVERY-B")):
        facility = service.submit_payment(
            facility["facility_id"],
            _submit(facility, index, "500.00", reference),
            users["supplier.demo"],
        )
        facility = service.decide_payment(
            facility["facility_id"],
            facility["payments"][-1]["payment_id"],
            _decision(facility["version"]),
            users["financier.demo"],
        )
    # A zero balance never erases the default: settlement lands in recovered.
    assert facility["status"] == "recovered"
    assert facility["repaid_at"] is None
    assert facility["outstanding_amount"] == "0.00"
    assert facility["default_event"]["default_id"] == default_id

    closed = service.close(
        facility["facility_id"],
        _command(facility["version"]),
        users["auditor.demo"],
    )
    assert closed["status"] == "closed"
    assert closed["closure_reason"] == "settled_after_default"
    assert closed["settlement_classification"] == "SETTLED_AFTER_DEFAULT"
    assert closed["default_event"]["default_id"] == default_id


def test_writeoff_records_exact_remaining_balance_and_closes_from_history(
    facility_context,
):
    service, users, request_id = facility_context
    facility = _mark_overdue(service, users, _activate(service, users, request_id))
    facility = _open_disposal(service, users, facility)
    facility = service.declare_default(
        facility["facility_id"],
        _declare_default(facility["version"]),
        users["risk.demo"],
    )
    with pytest.raises(FacilityConflict, match="cannot write_off from defaulted"):
        service.write_off(
            facility["facility_id"],
            _write_off(facility["version"]),
            users["auditor.demo"],
        )
    facility = _start_recovery(service, users, facility)
    with pytest.raises(ForbiddenFacility):
        service.write_off(
            facility["facility_id"],
            _write_off(facility["version"]),
            users["risk.demo"],
        )

    writeoff_key = uuid.uuid4()
    writeoff_command = _write_off(facility["version"], key=writeoff_key)
    facility = service.write_off(
        facility["facility_id"],
        writeoff_command,
        users["auditor.demo"],
    )
    assert facility["status"] == "written_off"
    assert facility["outstanding_amount"] == "0.00"
    assert facility["writeoff_event"]["amount"] == "1000.00"
    assert facility["default_event"] is not None
    replay = service.write_off(
        facility["facility_id"], writeoff_command, users["auditor.demo"]
    )
    assert replay["writeoff_event"]["writeoff_id"] == facility["writeoff_event"][
        "writeoff_id"
    ]
    with pytest.raises(FacilityConflict):
        service.write_off(
            facility["facility_id"],
            _write_off(facility["version"]),
            users["auditor.demo"],
        )
    closed = service.close(
        facility["facility_id"],
        _command(facility["version"]),
        users["auditor.demo"],
    )
    assert closed["closure_reason"] == "written_off"
    assert closed["settlement_classification"] == "WRITTEN_OFF"
    assert closed["repaid_at"] is None


def test_lifecycle_conflicts_and_replays_preserve_all_transactional_records(
    facility_context,
):
    service, users, request_id = facility_context
    facility = _mark_overdue(service, users, _activate(service, users, request_id))
    facility = _open_disposal(service, users, facility)
    facility_uuid = uuid.UUID(facility["facility_id"])
    with service.session_factory() as session:
        before = (
            session.get(FinancingFacilityModel, facility_uuid).version,
            session.scalar(select(func.count()).select_from(InstallmentModel)),
            session.scalar(select(func.count()).select_from(FacilityActionModel)),
            session.scalar(select(func.count()).select_from(LedgerEventModel)),
            session.scalar(select(func.count()).select_from(FacilityDefaultModel)),
            session.scalar(
                select(func.count()).select_from(FacilityDelinquencyModel)
            ),
            session.scalar(select(func.count()).select_from(FacilityRestructureModel)),
            session.scalar(select(func.count()).select_from(FacilityWriteOffModel)),
        )

    with pytest.raises(FacilityConflict, match="exact outstanding"):
        service.restructure(
            facility["facility_id"],
            _restructure(facility["version"], total="499.99"),
            users["risk.demo"],
        )
    with service.session_factory() as session:
        after = (
            session.get(FinancingFacilityModel, facility_uuid).version,
            session.scalar(select(func.count()).select_from(InstallmentModel)),
            session.scalar(select(func.count()).select_from(FacilityActionModel)),
            session.scalar(select(func.count()).select_from(LedgerEventModel)),
            session.scalar(select(func.count()).select_from(FacilityDefaultModel)),
            session.scalar(
                select(func.count()).select_from(FacilityDelinquencyModel)
            ),
            session.scalar(select(func.count()).select_from(FacilityRestructureModel)),
            session.scalar(select(func.count()).select_from(FacilityWriteOffModel)),
        )
    assert after == before

    with pytest.raises(FacilityConflict, match="future"):
        service.restructure(
            facility["facility_id"],
            _restructure(
                facility["version"], total="1000.00", due_date="2025-01-01"
            ),
            users["risk.demo"],
        )
    with service.session_factory() as session:
        assert (
            session.get(FinancingFacilityModel, facility_uuid).version,
            session.scalar(select(func.count()).select_from(InstallmentModel)),
            session.scalar(select(func.count()).select_from(FacilityActionModel)),
            session.scalar(select(func.count()).select_from(LedgerEventModel)),
            session.scalar(select(func.count()).select_from(FacilityDefaultModel)),
            session.scalar(
                select(func.count()).select_from(FacilityDelinquencyModel)
            ),
            session.scalar(select(func.count()).select_from(FacilityRestructureModel)),
            session.scalar(select(func.count()).select_from(FacilityWriteOffModel)),
        ) == before

    key = uuid.uuid4()
    command = _declare_default(facility["version"], key=key)
    first = service.declare_default(facility["facility_id"], command, users["risk.demo"])
    replay = service.declare_default(facility["facility_id"], command, users["risk.demo"])
    assert replay["default_event"]["default_id"] == first["default_event"]["default_id"]
    with service.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(FacilityDefaultModel)) == 1
        assert session.scalar(
            select(func.count()).select_from(FacilityActionModel).where(
                FacilityActionModel.idempotency_key == key
            )
        ) == 1

    with pytest.raises(FacilityConflict):
        service.declare_default(
            facility["facility_id"],
            _declare_default(facility["version"]),
            users["risk.demo"],
        )
    with service.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(FacilityDefaultModel)) == 1
        assert session.scalar(select(func.count()).select_from(FacilityWriteOffModel)) == 0


def test_writeoff_rejects_pending_recovery_without_any_transactional_change(
    facility_context,
):
    service, users, request_id = facility_context
    facility = _start_recovery(service, users, _defaulted(service, users, request_id))
    facility = service.submit_payment(
        facility["facility_id"],
        _submit(facility, 0, "100.00", "PENDING-BEFORE-WRITEOFF"),
        users["supplier.demo"],
    )
    before = _persistence_snapshot(service, facility["facility_id"])

    with pytest.raises(FacilityConflict, match="Pending payment"):
        service.write_off(
            facility["facility_id"],
            _write_off(facility["version"]),
            users["auditor.demo"],
        )

    assert _persistence_snapshot(service, facility["facility_id"]) == before
    auditor_view = service.get(facility["facility_id"], users["auditor.demo"])
    assert auditor_view["allowed_actions"] == []
    assert auditor_view["payments"][0]["status"] == "submitted"


def test_partial_default_recovery_writes_off_exact_remaining_balance(
    facility_context,
):
    service, users, request_id = facility_context
    facility = _defaulted(service, users, request_id, amounts=("1000.00",))
    facility = service.submit_payment(
        facility["facility_id"],
        _submit(facility, 0, "400.00", "PARTIAL-RECOVERY"),
        users["supplier.demo"],
    )
    facility = service.decide_payment(
        facility["facility_id"],
        facility["payments"][0]["payment_id"],
        _decision(facility["version"]),
        users["financier.demo"],
    )
    assert facility["status"] == "defaulted"
    assert facility["outstanding_amount"] == "600.00"
    facility = _start_recovery(service, users, facility)

    written_off = service.write_off(
        facility["facility_id"],
        _write_off(facility["version"]),
        users["auditor.demo"],
    )
    assert written_off["writeoff_event"]["amount"] == "600.00"
    assert written_off["recovered_amount"] == "400.00"
    assert written_off["realized_loss"] == written_off["written_off_amount"] == "600.00"
    assert written_off["outstanding_amount"] == "0.00"
    assert written_off["payments"][0]["status"] == "confirmed"


def test_defaulted_over_recovery_rejection_rolls_back_every_record(
    facility_context,
):
    service, users, request_id = facility_context
    facility = _defaulted(service, users, request_id, amounts=("1000.00",))
    for amount, reference in (
        ("600.00", "RECOVERY-FIRST"),
        ("600.00", "RECOVERY-TOO-LARGE"),
    ):
        facility = service.submit_payment(
            facility["facility_id"],
            _submit(facility, 0, amount, reference),
            users["supplier.demo"],
        )
    first, second = facility["payments"]
    facility = service.decide_payment(
        facility["facility_id"],
        first["payment_id"],
        _decision(facility["version"]),
        users["financier.demo"],
    )
    before = _persistence_snapshot(service, facility["facility_id"])

    with pytest.raises(FacilityConflict, match="exceed"):
        service.decide_payment(
            facility["facility_id"],
            second["payment_id"],
            _decision(facility["version"]),
            users["financier.demo"],
        )

    assert _persistence_snapshot(service, facility["facility_id"]) == before
    unchanged = service.get(facility["facility_id"], users["financier.demo"])
    assert unchanged["outstanding_amount"] == "400.00"
    assert [row["status"] for row in unchanged["payments"]] == [
        "confirmed",
        "submitted",
    ]


def test_allowed_actions_role_state_and_pending_payment_matrix(session_factory):
    users = _users(session_factory)
    roles = (
        "supplier.demo",
        "core.demo",
        "financier.demo",
        "risk.demo",
        "auditor.demo",
    )
    default_on_v1 = [FacilityDefaultModel(schedule_version=1)]
    # (status, arrears, default history, write-off remaining) -> role -> actions
    cases = (
        ("ready_for_disbursement", False, [], "0.00", {
            "financier.demo": ["initiate_disbursement"],
        }),
        ("disbursed", False, [], "0.00", {
            "financier.demo": ["confirm_disbursement"],
        }),
        ("active", False, [], "0.00", {
            "supplier.demo": ["submit_payment"],
            "financier.demo": ["mark_overdue"],
        }),
        ("overdue", True, [], "0.00", {
            "supplier.demo": ["submit_payment"],
            "risk.demo": ["open_disposal"],
        }),
        ("in_disposal", True, [], "0.00", {
            "supplier.demo": ["submit_payment"],
            "risk.demo": ["restructure", "declare_default"],
        }),
        ("in_disposal", False, [], "0.00", {
            "supplier.demo": ["submit_payment"],
            "risk.demo": ["close_disposal", "restructure", "declare_default"],
        }),
        ("restructured", False, [], "0.00", {
            "supplier.demo": ["submit_payment"],
            "financier.demo": ["mark_overdue"],
        }),
        ("defaulted", True, default_on_v1, "0.00", {
            "supplier.demo": ["submit_payment"],
            "risk.demo": ["restructure", "start_recovery"],
        }),
        ("in_recovery", True, default_on_v1, "0.00", {
            "supplier.demo": ["submit_payment"],
            "financier.demo": ["record_recovery"],
            "auditor.demo": ["write_off"],
        }),
        ("repaid", False, [], "0.00", {"auditor.demo": ["close"]}),
        ("recovered", False, default_on_v1, "0.00", {"auditor.demo": ["close"]}),
        ("written_off", False, default_on_v1, "250.00", {
            "financier.demo": ["record_recovery"],
            "auditor.demo": ["close"],
        }),
        ("written_off", False, default_on_v1, "0.00", {"auditor.demo": ["close"]}),
        ("closed", False, [], "0.00", {}),
    )
    for status, arrears, defaults, remaining, role_expectations in cases:
        facility = FinancingFacilityModel(
            status=status,
            current_schedule_version=1,
            outstanding_amount=Decimal("0.00" if status in {
                "repaid", "recovered", "written_off", "closed"
            } else "100.00"),
        )
        for role in roles:
            assert FacilityService._allowed_actions(
                facility,
                [],
                users[role],
                arrears=arrears,
                default_history=defaults,
                writeoff_remaining=Decimal(remaining),
            ) == role_expectations.get(role, []), (status, arrears, role)

    pending = [PaymentModel(status="submitted")]
    for status in ("defaulted", "in_recovery"):
        facility = FinancingFacilityModel(
            status=status,
            current_schedule_version=1,
            outstanding_amount=Decimal("100.00"),
        )
        kwargs = {
            "arrears": True,
            "default_history": default_on_v1,
            "writeoff_remaining": Decimal("0.00"),
        }
        assert FacilityService._allowed_actions(
            facility, pending, users["supplier.demo"], **kwargs
        ) == ["submit_payment"]
        assert FacilityService._allowed_actions(
            facility, pending, users["financier.demo"], **kwargs
        ) == ["confirm_payment", "reject_payment"]
        assert FacilityService._allowed_actions(
            facility, pending, users["auditor.demo"], **kwargs
        ) == []
    assert FacilityService._allowed_actions(
        FinancingFacilityModel(status="defaulted", current_schedule_version=1),
        pending,
        users["risk.demo"],
        arrears=True,
        default_history=default_on_v1,
        writeoff_remaining=Decimal("0.00"),
    ) == ["start_recovery"]


def test_each_new_lifecycle_command_rejects_a_stale_version(facility_context):
    service, users, request_id = facility_context
    overdue = _mark_overdue(service, users, _activate(service, users, request_id))
    with pytest.raises(FacilityConflict, match="Expected version"):
        service.restructure(
            overdue["facility_id"],
            _restructure(overdue["version"] - 1, total="1000.00"),
            users["risk.demo"],
        )
    with pytest.raises(FacilityConflict, match="Expected version"):
        service.declare_default(
            overdue["facility_id"],
            _declare_default(overdue["version"] - 1),
            users["risk.demo"],
        )
    with pytest.raises(FacilityConflict, match="Expected version"):
        service.open_disposal(
            overdue["facility_id"],
            _lifecycle(overdue["version"] - 1),
            users["risk.demo"],
        )
    disposal = _open_disposal(service, users, overdue)
    with pytest.raises(FacilityConflict, match="Expected version"):
        service.close_disposal(
            disposal["facility_id"],
            _lifecycle(disposal["version"] - 1),
            users["risk.demo"],
        )
    defaulted = service.declare_default(
        disposal["facility_id"],
        _declare_default(disposal["version"]),
        users["risk.demo"],
    )
    with pytest.raises(FacilityConflict, match="Expected version"):
        service.start_recovery(
            defaulted["facility_id"],
            _lifecycle(defaulted["version"] - 1),
            users["risk.demo"],
        )
    recovery = _start_recovery(service, users, defaulted)
    before = _persistence_snapshot(service, recovery["facility_id"])
    with pytest.raises(FacilityConflict, match="Expected version"):
        service.write_off(
            recovery["facility_id"],
            _write_off(recovery["version"] - 1),
            users["auditor.demo"],
        )
    assert _persistence_snapshot(service, recovery["facility_id"]) == before


def test_restructure_semantic_replay_does_not_duplicate_any_record(
    facility_context,
):
    service, users, request_id = facility_context
    overdue = _open_disposal(
        service, users, _mark_overdue(service, users, _activate(service, users, request_id))
    )
    key = uuid.uuid4()
    command = _restructure(overdue["version"], total="1000.00", key=key)
    first = service.restructure(overdue["facility_id"], command, users["risk.demo"])
    snapshot = _persistence_snapshot(service, first["facility_id"])

    replay = service.restructure(overdue["facility_id"], command, users["risk.demo"])

    assert replay["version"] == first["version"]
    assert replay["restructures"] == first["restructures"]
    assert _persistence_snapshot(service, first["facility_id"]) == snapshot
    assert snapshot["restructures"] == 1


def test_concurrent_default_and_writeoff_return_one_conflict_not_integrity_error(
    facility_context,
):
    service, users, request_id = facility_context
    overdue = _open_disposal(
        service, users, _mark_overdue(service, users, _activate(service, users, request_id))
    )

    def race(commands, operation):
        barrier = threading.Barrier(2)

        def run(command):
            barrier.wait(timeout=5)
            try:
                return ("ok", operation(command))
            except FacilityConflict:
                return ("conflict", None)

        with ThreadPoolExecutor(max_workers=2) as executor:
            return list(executor.map(run, commands))

    default_results = race(
        [_declare_default(overdue["version"]), _declare_default(overdue["version"])],
        lambda command: service.declare_default(
            overdue["facility_id"], command, users["risk.demo"]
        ),
    )
    assert sorted(row[0] for row in default_results) == ["conflict", "ok"]
    defaulted = _start_recovery(
        service, users, next(row[1] for row in default_results if row[0] == "ok")
    )

    writeoff_results = race(
        [_write_off(defaulted["version"]), _write_off(defaulted["version"])],
        lambda command: service.write_off(
            defaulted["facility_id"], command, users["auditor.demo"]
        ),
    )
    assert sorted(row[0] for row in writeoff_results) == ["conflict", "ok"]
    with service.session_factory() as session:
        facility_uuid = uuid.UUID(defaulted["facility_id"])
        assert session.scalar(
            select(func.count()).select_from(FacilityDefaultModel).where(
                FacilityDefaultModel.facility_id == facility_uuid
            )
        ) == 1
        assert session.scalar(
            select(func.count()).select_from(FacilityWriteOffModel).where(
                FacilityWriteOffModel.facility_id == facility_uuid
            )
        ) == 1


def test_flushed_writeoff_failure_rolls_back_aggregate_and_every_child_record(
    facility_context,
):
    service, users, request_id = facility_context
    defaulted = _start_recovery(service, users, _defaulted(service, users, request_id))
    before = _persistence_snapshot(service, defaulted["facility_id"])

    class FlushThenFailRepository(FacilityRepository):
        def list_installments(self, session, facility_id):
            session.flush()
            raise RuntimeError("injected failure after flush")

        def list_installments_batch(self, session, facility_ids):
            session.flush()
            raise RuntimeError("injected failure after flush")

    failing_service = FacilityService(
        service.session_factory,
        repository=FlushThenFailRepository(),
    )
    with pytest.raises(RuntimeError, match="after flush"):
        failing_service.write_off(
            defaulted["facility_id"],
            _write_off(defaulted["version"]),
            users["auditor.demo"],
        )

    assert _persistence_snapshot(service, defaulted["facility_id"]) == before


def test_list_paginates_in_sql_and_uses_bounded_real_query_count(
    session_factory,
    migrated_engine,
):
    users = _users(session_factory)
    service = FacilityService(session_factory)
    created = []
    for _ in range(6):
        request_id = _approved_application(session_factory, users)
        created.append(
            service.create(_create_request(request_id), users["financier.demo"])
        )

    def measured_page(*, limit, offset):
        statements = []

        def capture(_connection, _cursor, statement, _parameters, _context, _many):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        event.listen(migrated_engine, "before_cursor_execute", capture)
        try:
            page = service.list_for_user(
                users["financier.demo"], limit=limit, offset=offset
            )
        finally:
            event.remove(migrated_engine, "before_cursor_execute", capture)
        return page, len(statements)

    page, narrow_query_count = measured_page(limit=2, offset=1)
    wide_page, wide_query_count = measured_page(limit=6, offset=0)

    assert [row["facility_id"] for row in page] == [
        created[-2]["facility_id"],
        created[-3]["facility_id"],
    ]
    assert len(wide_page) == 6
    assert narrow_query_count == wide_query_count
    # One page query plus ten batched child reads, independent of page size.
    assert wide_query_count <= 11


def test_list_uses_facility_id_as_stable_tiebreaker_across_pages(
    session_factory,
):
    users = _users(session_factory)
    service = FacilityService(session_factory)
    fixed_updated_at = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    expected_ids = [str(uuid.UUID(int=value)) for value in range(1, 7)]

    with session_factory.begin() as session:
        for value in range(6, 0, -1):
            request_id = _approved_application(session_factory, users)
            facility_id = uuid.UUID(int=value)
            session.add(
                FinancingFacilityModel(
                    facility_id=facility_id,
                    request_id=request_id,
                    principal=Decimal("1000.00"),
                    outstanding_amount=Decimal("1000.00"),
                    currency="RUB",
                    status="ready_for_disbursement",
                    version=1,
                    current_schedule_version=1,
                    created_by_user_id=users["financier.demo"].user_id,
                    created_at=fixed_updated_at,
                    updated_at=fixed_updated_at,
                )
            )
            session.add(
                InstallmentModel(
                    installment_id=uuid.UUID(int=100 + value),
                    facility_id=facility_id,
                    sequence=1,
                    schedule_version=1,
                    due_date=date(2027, 1, 1),
                    amount=Decimal("1000.00"),
                    paid_amount=Decimal("0.00"),
                    status="scheduled",
                    created_at=fixed_updated_at,
                    updated_at=fixed_updated_at,
                )
            )

    def traverse_pages() -> list[str]:
        return [
            row["facility_id"]
            for offset in range(0, 6, 2)
            for row in service.list_for_user(
                users["financier.demo"], limit=2, offset=offset
            )
        ]

    first_traversal = traverse_pages()
    second_traversal = traverse_pages()

    assert first_traversal == expected_ids
    assert second_traversal == expected_ids
    assert len(set(first_traversal)) == len(expected_ids)


def test_replay_holds_aggregate_lock_until_all_child_reads_finish(
    facility_context,
):
    service, users, request_id = facility_context
    overdue = _open_disposal(
        service, users, _mark_overdue(service, users, _activate(service, users, request_id))
    )
    key = uuid.uuid4()
    command = _restructure(overdue["version"], total="1000.00", key=key)
    first = service.restructure(
        overdue["facility_id"], command, users["risk.demo"]
    )
    child_reads_started = threading.Event()
    allow_child_reads = threading.Event()

    class PausingReplayRepository(FacilityRepository):
        def list_installments_batch(self, session, facility_ids):
            child_reads_started.set()
            if not allow_child_reads.wait(timeout=5):
                raise TimeoutError("replay child reads were not released")
            return super().list_installments_batch(session, facility_ids)

    replay_service = FacilityService(
        service.session_factory,
        repository=PausingReplayRepository(),
    )
    writer_was_blocked = False
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            replay_service.restructure,
            overdue["facility_id"],
            command,
            users["risk.demo"],
        )
        assert child_reads_started.wait(timeout=5)
        writer = service.session_factory()
        try:
            writer.execute(text("SET LOCAL lock_timeout = '100ms'"))
            try:
                writer.execute(
                    text(
                        "UPDATE financing_facilities SET version = version + 1 "
                        "WHERE facility_id = :facility_id"
                    ),
                    {"facility_id": uuid.UUID(overdue["facility_id"])},
                )
            except DBAPIError:
                writer_was_blocked = True
        finally:
            writer.rollback()
            writer.close()
            allow_child_reads.set()
        replay = future.result(timeout=5)

    assert writer_was_blocked
    assert replay["version"] == first["version"]
    assert replay["status"] == first["status"]
    assert replay["installments"] == first["installments"]
    assert replay["restructures"] == first["restructures"]

    writer_after_replay = service.session_factory()
    try:
        writer_after_replay.execute(text("SET LOCAL lock_timeout = '100ms'"))
        result = writer_after_replay.execute(
            text(
                "UPDATE financing_facilities SET version = version + 1 "
                "WHERE facility_id = :facility_id"
            ),
            {"facility_id": uuid.UUID(overdue["facility_id"])},
        )
        assert result.rowcount == 1
    finally:
        writer_after_replay.rollback()
        writer_after_replay.close()
