from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.domain.facility import (
    FacilityAction,
    FacilityStatus,
    InvalidFacilityTransition,
    next_facility_status,
)
from app.domain.workflow import Role
from app.schemas_facility import (
    CreateFacilityRequest,
    DecisionPaymentRequest,
    SubmitPaymentRequest,
    VersionedFacilityCommand,
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
            Role.FINANCIER,
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
