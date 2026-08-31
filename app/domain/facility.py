from __future__ import annotations

from decimal import Decimal, InvalidOperation
from enum import StrEnum

from app.domain.workflow import Role


class FacilityStatus(StrEnum):
    READY = "ready_for_disbursement"
    DISBURSED = "disbursed"
    ACTIVE = "active"
    OVERDUE = "overdue"
    RESTRUCTURED = "restructured"
    DEFAULTED = "defaulted"
    REPAID = "repaid"
    WRITTEN_OFF = "written_off"
    CLOSED = "closed"


class InstallmentStatus(StrEnum):
    SCHEDULED = "scheduled"
    PARTIALLY_PAID = "partially_paid"
    PAID = "paid"
    OVERDUE = "overdue"
    SUPERSEDED = "superseded"


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
    RESTRUCTURE = "restructure"
    DECLARE_DEFAULT = "declare_default"
    WRITE_OFF = "write_off"
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
        FacilityStatus.RESTRUCTURED,
        FacilityAction.SUBMIT_PAYMENT,
        Role.SUPPLIER,
    ): FacilityStatus.RESTRUCTURED,
    (
        FacilityStatus.DEFAULTED,
        FacilityAction.SUBMIT_PAYMENT,
        Role.SUPPLIER,
    ): FacilityStatus.DEFAULTED,
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
        FacilityStatus.RESTRUCTURED,
        FacilityAction.CONFIRM_PAYMENT,
        Role.FINANCIER,
    ): FacilityStatus.RESTRUCTURED,
    (
        FacilityStatus.DEFAULTED,
        FacilityAction.CONFIRM_PAYMENT,
        Role.FINANCIER,
    ): FacilityStatus.DEFAULTED,
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
        FacilityStatus.RESTRUCTURED,
        FacilityAction.REJECT_PAYMENT,
        Role.FINANCIER,
    ): FacilityStatus.RESTRUCTURED,
    (
        FacilityStatus.DEFAULTED,
        FacilityAction.REJECT_PAYMENT,
        Role.FINANCIER,
    ): FacilityStatus.DEFAULTED,
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
        FacilityStatus.RESTRUCTURED,
        FacilityAction.CONFIRM_FINAL_PAYMENT,
        Role.FINANCIER,
    ): FacilityStatus.REPAID,
    (
        FacilityStatus.DEFAULTED,
        FacilityAction.CONFIRM_FINAL_PAYMENT,
        Role.FINANCIER,
    ): FacilityStatus.REPAID,
    (
        FacilityStatus.ACTIVE,
        FacilityAction.MARK_OVERDUE,
        Role.FINANCIER,
    ): FacilityStatus.OVERDUE,
    (
        FacilityStatus.RESTRUCTURED,
        FacilityAction.MARK_OVERDUE,
        Role.FINANCIER,
    ): FacilityStatus.OVERDUE,
    (
        FacilityStatus.OVERDUE,
        FacilityAction.RESTRUCTURE,
        Role.RISK_MANAGER,
    ): FacilityStatus.RESTRUCTURED,
    (
        FacilityStatus.OVERDUE,
        FacilityAction.DECLARE_DEFAULT,
        Role.RISK_MANAGER,
    ): FacilityStatus.DEFAULTED,
    (
        FacilityStatus.RESTRUCTURED,
        FacilityAction.DECLARE_DEFAULT,
        Role.RISK_MANAGER,
    ): FacilityStatus.DEFAULTED,
    (
        FacilityStatus.DEFAULTED,
        FacilityAction.WRITE_OFF,
        Role.AUDITOR,
    ): FacilityStatus.WRITTEN_OFF,
    (
        FacilityStatus.REPAID,
        FacilityAction.CLOSE,
        Role.AUDITOR,
    ): FacilityStatus.CLOSED,
    (
        FacilityStatus.WRITTEN_OFF,
        FacilityAction.CLOSE,
        Role.AUDITOR,
    ): FacilityStatus.CLOSED,
}


def derive_closure_reason(*, has_default: bool, has_writeoff: bool) -> str:
    if has_writeoff:
        if not has_default:
            raise ValueError("write-off requires default history")
        return "written_off"
    return "settled_after_default" if has_default else "repaid"


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
