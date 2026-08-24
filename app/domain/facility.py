from __future__ import annotations

from decimal import Decimal, InvalidOperation
from enum import StrEnum

from app.domain.workflow import Role


class FacilityStatus(StrEnum):
    READY = "ready_for_disbursement"
    DISBURSED = "disbursed"
    ACTIVE = "active"
    OVERDUE = "overdue"
    REPAID = "repaid"
    CLOSED = "closed"


class InstallmentStatus(StrEnum):
    SCHEDULED = "scheduled"
    PARTIALLY_PAID = "partially_paid"
    PAID = "paid"
    OVERDUE = "overdue"


class PaymentStatus(StrEnum):
    SUBMITTED = "submitted"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class FacilityAction(StrEnum):
    CREATE = "create"
    INITIATE_DISBURSEMENT = "initiate_disbursement"
    CONFIRM_DISBURSEMENT = "confirm_disbursement"
    SUBMIT_PAYMENT = "submit_payment"
    CONFIRM_PAYMENT = "confirm_payment"
    REJECT_PAYMENT = "reject_payment"
    CONFIRM_FINAL_PAYMENT = "confirm_final_payment"
    MARK_OVERDUE = "mark_overdue"
    CLOSE = "close"


class InvalidFacilityTransition(Exception):
    """Raised when a role cannot perform an action from the current state."""


CENT = Decimal("0.01")


def exact_money(value: Decimal) -> Decimal:
    """Validate and normalize a positive monetary value to exact cents."""

    try:
        normalized = value.quantize(CENT)
    except (InvalidOperation, ValueError) as error:
        raise ValueError("money must be finite and expressed as exact cents") from error
    if not value.is_finite() or value <= 0 or value != normalized:
        raise ValueError("money must be positive with at most two decimal places")
    return normalized


_TRANSITIONS = {
    (
        FacilityStatus.READY,
        FacilityAction.INITIATE_DISBURSEMENT,
        Role.FINANCIER,
    ): FacilityStatus.DISBURSED,
    (
        FacilityStatus.DISBURSED,
        FacilityAction.CONFIRM_DISBURSEMENT,
        Role.FINANCIER,
    ): FacilityStatus.ACTIVE,
    (
        FacilityStatus.ACTIVE,
        FacilityAction.SUBMIT_PAYMENT,
        Role.SUPPLIER,
    ): FacilityStatus.ACTIVE,
    (
        FacilityStatus.OVERDUE,
        FacilityAction.SUBMIT_PAYMENT,
        Role.SUPPLIER,
    ): FacilityStatus.OVERDUE,
    (
        FacilityStatus.ACTIVE,
        FacilityAction.CONFIRM_PAYMENT,
        Role.FINANCIER,
    ): FacilityStatus.ACTIVE,
    (
        FacilityStatus.OVERDUE,
        FacilityAction.CONFIRM_PAYMENT,
        Role.FINANCIER,
    ): FacilityStatus.OVERDUE,
    (
        FacilityStatus.ACTIVE,
        FacilityAction.REJECT_PAYMENT,
        Role.FINANCIER,
    ): FacilityStatus.ACTIVE,
    (
        FacilityStatus.OVERDUE,
        FacilityAction.REJECT_PAYMENT,
        Role.FINANCIER,
    ): FacilityStatus.OVERDUE,
    (
        FacilityStatus.ACTIVE,
        FacilityAction.CONFIRM_FINAL_PAYMENT,
        Role.FINANCIER,
    ): FacilityStatus.REPAID,
    (
        FacilityStatus.OVERDUE,
        FacilityAction.CONFIRM_FINAL_PAYMENT,
        Role.FINANCIER,
    ): FacilityStatus.REPAID,
    (
        FacilityStatus.ACTIVE,
        FacilityAction.MARK_OVERDUE,
        Role.RISK_MANAGER,
    ): FacilityStatus.OVERDUE,
    (
        FacilityStatus.REPAID,
        FacilityAction.CLOSE,
        Role.AUDITOR,
    ): FacilityStatus.CLOSED,
}


def next_facility_status(
    current: FacilityStatus,
    action: FacilityAction,
    role: Role,
) -> FacilityStatus:
    try:
        return _TRANSITIONS[(current, action, role)]
    except KeyError as error:
        raise InvalidFacilityTransition(
            f"{role.value} cannot {action.value} from {current.value}"
        ) from error
