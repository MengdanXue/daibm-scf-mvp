from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.domain.facility import (
    FacilityAction,
    FacilityStatus,
    InvalidFacilityTransition,
    derive_closure_reason,
    next_facility_status,
)
from app.domain.workflow import Role
from app.schemas_facility import (
    CreateFacilityRequest,
    DeclareDefaultRequest,
    DecisionPaymentRequest,
    MarkOverdueRequest,
    RestructureFacilityRequest,
    SubmitPaymentRequest,
    VersionedFacilityCommand,
    WriteOffRequest,
)


def _create_payload(**overrides):
    payload = {
        "request_id": uuid.uuid4(),
        "version": 7,
        "principal": "100.00",
        "currency": "RUB",
        "installments": [
            {
                "sequence": 1,
                "due_date": "2026-10-01",
                "amount": "40.00",
            },
            {
                "sequence": 2,
                "due_date": "2026-11-01",
                "amount": "60.00",
            },
        ],
        "idempotency_key": uuid.uuid4(),
    }
    payload.update(overrides)
    return payload


def test_happy_path_requires_real_roles():
    status = next_facility_status(
        FacilityStatus.READY,
        FacilityAction.INITIATE_DISBURSEMENT,
        Role.FINANCIER,
    )
    status = next_facility_status(
        status,
        FacilityAction.CONFIRM_DISBURSEMENT,
        Role.FINANCIER,
    )

    assert status is FacilityStatus.ACTIVE
    assert next_facility_status(
        FacilityStatus.REPAID,
        FacilityAction.CLOSE,
        Role.AUDITOR,
    ) is FacilityStatus.CLOSED


@pytest.mark.parametrize(
    ("current", "action", "role"),
    (
        (
            FacilityStatus.READY,
            FacilityAction.INITIATE_DISBURSEMENT,
            Role.SUPPLIER,
        ),
        (
            FacilityStatus.DISBURSED,
            FacilityAction.CONFIRM_DISBURSEMENT,
            Role.AUDITOR,
        ),
        (
            FacilityStatus.ACTIVE,
            FacilityAction.MARK_OVERDUE,
            Role.RISK_MANAGER,
        ),
        (FacilityStatus.ACTIVE, FacilityAction.CLOSE, Role.AUDITOR),
    ),
)
def test_facility_transition_rejects_wrong_role_or_state(current, action, role):
    with pytest.raises(InvalidFacilityTransition):
        next_facility_status(current, action, role)


def test_payment_actions_preserve_state_until_final_confirmation():
    assert next_facility_status(
        FacilityStatus.ACTIVE,
        FacilityAction.SUBMIT_PAYMENT,
        Role.SUPPLIER,
    ) is FacilityStatus.ACTIVE
    assert next_facility_status(
        FacilityStatus.OVERDUE,
        FacilityAction.REJECT_PAYMENT,
        Role.FINANCIER,
    ) is FacilityStatus.OVERDUE
    assert next_facility_status(
        FacilityStatus.OVERDUE,
        FacilityAction.CONFIRM_FINAL_PAYMENT,
        Role.FINANCIER,
    ) is FacilityStatus.REPAID


def test_restructure_default_recovery_writeoff_and_close_transitions():
    assert next_facility_status(
        FacilityStatus.OVERDUE,
        FacilityAction.OPEN_DISPOSAL,
        Role.RISK_MANAGER,
    ) is FacilityStatus.IN_DISPOSAL
    assert next_facility_status(
        FacilityStatus.IN_DISPOSAL,
        FacilityAction.RESTRUCTURE,
        Role.RISK_MANAGER,
    ) is FacilityStatus.RESTRUCTURED
    assert next_facility_status(
        FacilityStatus.IN_DISPOSAL,
        FacilityAction.DECLARE_DEFAULT,
        Role.RISK_MANAGER,
    ) is FacilityStatus.DEFAULTED
    assert next_facility_status(
        FacilityStatus.DEFAULTED,
        FacilityAction.CONFIRM_PAYMENT,
        Role.FINANCIER,
    ) is FacilityStatus.DEFAULTED
    assert next_facility_status(
        FacilityStatus.DEFAULTED,
        FacilityAction.CONFIRM_FINAL_PAYMENT,
        Role.FINANCIER,
    ) is FacilityStatus.RECOVERED
    assert next_facility_status(
        FacilityStatus.DEFAULTED,
        FacilityAction.START_RECOVERY,
        Role.RISK_MANAGER,
    ) is FacilityStatus.IN_RECOVERY
    assert next_facility_status(
        FacilityStatus.IN_RECOVERY,
        FacilityAction.WRITE_OFF,
        Role.AUDITOR,
    ) is FacilityStatus.WRITTEN_OFF
    assert next_facility_status(
        FacilityStatus.WRITTEN_OFF,
        FacilityAction.CLOSE,
        Role.AUDITOR,
    ) is FacilityStatus.CLOSED


@pytest.mark.parametrize(
    ("current", "action", "role", "expected"),
    (
        (
            FacilityStatus.ACTIVE,
            FacilityAction.MARK_OVERDUE,
            Role.FINANCIER,
            FacilityStatus.OVERDUE,
        ),
        (
            FacilityStatus.RESTRUCTURED,
            FacilityAction.MARK_OVERDUE,
            Role.FINANCIER,
            FacilityStatus.OVERDUE,
        ),
        (
            FacilityStatus.RESTRUCTURED,
            FacilityAction.CONFIRM_PAYMENT,
            Role.FINANCIER,
            FacilityStatus.RESTRUCTURED,
        ),
        (
            FacilityStatus.RESTRUCTURED,
            FacilityAction.REJECT_PAYMENT,
            Role.FINANCIER,
            FacilityStatus.RESTRUCTURED,
        ),
        (
            FacilityStatus.DEFAULTED,
            FacilityAction.SUBMIT_PAYMENT,
            Role.SUPPLIER,
            FacilityStatus.DEFAULTED,
        ),
    ),
)
def test_extended_payment_and_overdue_actions_preserve_governed_state(
    current,
    action,
    role,
    expected,
):
    assert next_facility_status(current, action, role) is expected


def test_closure_reason_is_derived_from_immutable_lifecycle_history():
    assert derive_closure_reason(has_default=False, has_writeoff=False) == "repaid"
    assert (
        derive_closure_reason(has_default=True, has_writeoff=False)
        == "settled_after_default"
    )
    assert (
        derive_closure_reason(has_default=True, has_writeoff=True) == "written_off"
    )

    with pytest.raises(ValueError, match="default history"):
        derive_closure_reason(has_default=False, has_writeoff=True)


def _evidence_payload(**overrides):
    payload = {
        "version": 7,
        "idempotency_key": str(uuid.uuid4()),
        "reason_code": "BORROWER_CASH_FLOW",
        "comment": "Verified revised repayment capacity",
        "evidence_sha256": "a" * 64,
    }
    payload.update(overrides)
    return payload


def test_restructure_schedule_requires_contiguous_sequences_and_exposes_total():
    payload = RestructureFacilityRequest.model_validate(
        _evidence_payload(
            installments=[
                {
                    "sequence": 1,
                    "due_date": "2027-12-01",
                    "amount": "400000.00",
                },
                {
                    "sequence": 2,
                    "due_date": "2028-01-01",
                    "amount": "500000.00",
                },
            ]
        )
    )
    assert payload.schedule_total == Decimal("900000.00")

    with pytest.raises(ValidationError, match="unique and contiguous"):
        RestructureFacilityRequest.model_validate(
            _evidence_payload(
                installments=[
                    {
                        "sequence": 1,
                        "due_date": "2027-12-01",
                        "amount": "400000.00",
                    },
                    {
                        "sequence": 3,
                        "due_date": "2028-01-01",
                        "amount": "500000.00",
                    },
                ]
            )
        )


def test_lifecycle_commands_require_stable_evidence_and_timezone_aware_default():
    mark_overdue = MarkOverdueRequest.model_validate(
        {
            "version": 2,
            "idempotency_key": uuid.uuid4(),
            "installment_id": uuid.uuid4(),
            "days_past_due": 1,
            "evidence_sha256": "b" * 64,
        }
    )
    assert mark_overdue.days_past_due == 1

    default = DeclareDefaultRequest.model_validate(
        _evidence_payload(
            defaulted_at=datetime(2027, 2, 3, 12, 0, tzinfo=timezone.utc),
            days_past_due=45,
        )
    )
    assert default.defaulted_at.tzinfo is not None
    assert default.comment == "Verified revised repayment capacity"
    assert WriteOffRequest.model_validate(_evidence_payload()).reason_code == (
        "BORROWER_CASH_FLOW"
    )

    with pytest.raises(ValidationError, match="timezone-aware"):
        DeclareDefaultRequest.model_validate(
            _evidence_payload(
                defaulted_at=datetime(2027, 2, 3, 12, 0),
                days_past_due=45,
            )
        )
    with pytest.raises(ValidationError):
        MarkOverdueRequest.model_validate(
            {
                "version": 2,
                "idempotency_key": uuid.uuid4(),
                "installment_id": uuid.uuid4(),
                "days_past_due": 0,
                "evidence_sha256": "not-a-hash",
            }
        )
    with pytest.raises(ValidationError, match="comment must not be blank"):
        WriteOffRequest.model_validate(_evidence_payload(comment="  "))


def test_create_facility_preserves_decimal_money_and_schedule_metadata():
    request = CreateFacilityRequest.model_validate(_create_payload())

    assert request.principal == Decimal("100.00")
    assert [item.amount for item in request.installments] == [
        Decimal("40.00"),
        Decimal("60.00"),
    ]
    assert request.installments[0].due_date == date(2026, 10, 1)


def test_schedule_requires_exact_cent_sum():
    with pytest.raises(ValidationError, match="principal"):
        CreateFacilityRequest.model_validate(
            _create_payload(
                installments=[
                    {
                        "sequence": 1,
                        "due_date": "2026-10-01",
                        "amount": "99.99",
                    }
                ]
            )
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("principal", "0.00"),
        ("principal", "100.001"),
        ("principal", 100.00),
    ),
)
def test_create_facility_rejects_non_positive_inexact_or_float_money(field, value):
    with pytest.raises(ValidationError):
        CreateFacilityRequest.model_validate(_create_payload(**{field: value}))


def test_schedule_rejects_duplicate_sequences_and_unknown_fields():
    duplicate_sequence = [
        {
            "sequence": 1,
            "due_date": "2026-10-01",
            "amount": "40.00",
        },
        {
            "sequence": 1,
            "due_date": "2026-11-01",
            "amount": "60.00",
        },
    ]
    with pytest.raises(ValidationError, match="sequence"):
        CreateFacilityRequest.model_validate(
            _create_payload(installments=duplicate_sequence)
        )
    with pytest.raises(ValidationError, match="extra_forbidden"):
        CreateFacilityRequest.model_validate(_create_payload(unexpected=True))


def test_versioned_commands_require_version_and_idempotency_key():
    command = VersionedFacilityCommand(
        version=3,
        idempotency_key=uuid.uuid4(),
    )
    assert command.version == 3

    with pytest.raises(ValidationError):
        VersionedFacilityCommand.model_validate({"version": 0})


def test_submit_payment_accepts_string_decimal_and_rejects_float():
    payload = {
        "version": 2,
        "idempotency_key": uuid.uuid4(),
        "installment_id": uuid.uuid4(),
        "amount": "25.50",
        "payment_reference": "BANK-DEMO-0001",
    }
    command = SubmitPaymentRequest.model_validate(payload)
    assert command.amount == Decimal("25.50")
    assert command.payment_reference == "BANK-DEMO-0001"

    with pytest.raises(ValidationError):
        SubmitPaymentRequest.model_validate({**payload, "amount": 25.50})


def test_payment_decision_allows_only_confirmed_or_rejected():
    payload = {
        "version": 2,
        "idempotency_key": uuid.uuid4(),
        "decision": "confirmed",
        "comment": "Evidence matched",
    }
    assert DecisionPaymentRequest.model_validate(payload).decision == "confirmed"

    with pytest.raises(ValidationError):
        DecisionPaymentRequest.model_validate({**payload, "decision": "submitted"})
