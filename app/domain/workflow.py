from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    SUPPLIER = "supplier"
    CORE_ENTERPRISE = "core_enterprise"
    FINANCIER = "financier"
    RISK_MANAGER = "risk_manager"
    AUDITOR = "auditor"


class Status(StrEnum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    TRADE_RETURNED = "trade_returned"
    TRADE_CONFIRMED = "trade_confirmed"
    RISK_ASSESSED = "risk_assessed"
    APPROVED = "approved"
    MANUAL_REVIEW = "manual_review"
    REJECTED = "rejected"
    CONTROLLED = "controlled"
    AUDITED = "audited"


class Action(StrEnum):
    UPDATE = "update"
    SUBMIT = "submit"
    CONFIRM_TRADE = "confirm_trade"
    RETURN_TRADE = "return_trade"
    ASSESS_RISK = "assess_risk"
    DECIDE = "decide"
    APPLY_CONTROL = "apply_control"
    AUDIT = "audit"


class InvalidTransition(Exception):
    pass


_TRANSITIONS = {
    (Status.DRAFT, Action.UPDATE, Role.SUPPLIER): Status.DRAFT,
    (
        Status.TRADE_RETURNED,
        Action.UPDATE,
        Role.SUPPLIER,
    ): Status.TRADE_RETURNED,
    (Status.DRAFT, Action.SUBMIT, Role.SUPPLIER): Status.SUBMITTED,
    (
        Status.TRADE_RETURNED,
        Action.SUBMIT,
        Role.SUPPLIER,
    ): Status.SUBMITTED,
    (
        Status.SUBMITTED,
        Action.CONFIRM_TRADE,
        Role.CORE_ENTERPRISE,
    ): Status.TRADE_CONFIRMED,
    (
        Status.SUBMITTED,
        Action.RETURN_TRADE,
        Role.CORE_ENTERPRISE,
    ): Status.TRADE_RETURNED,
    (
        Status.TRADE_CONFIRMED,
        Action.ASSESS_RISK,
        Role.FINANCIER,
    ): Status.RISK_ASSESSED,
    (
        Status.APPROVED,
        Action.APPLY_CONTROL,
        Role.RISK_MANAGER,
    ): Status.CONTROLLED,
    (
        Status.MANUAL_REVIEW,
        Action.APPLY_CONTROL,
        Role.RISK_MANAGER,
    ): Status.CONTROLLED,
    (
        Status.REJECTED,
        Action.APPLY_CONTROL,
        Role.RISK_MANAGER,
    ): Status.CONTROLLED,
    (Status.CONTROLLED, Action.AUDIT, Role.AUDITOR): Status.AUDITED,
}


def next_status(
    current: Status,
    action: Action,
    role: Role,
    *,
    decision: str | None = None,
) -> Status:
    if (
        current == Status.RISK_ASSESSED
        and action == Action.DECIDE
        and role == Role.FINANCIER
    ):
        try:
            target = Status(decision or "")
        except ValueError as error:
            raise InvalidTransition("Unsupported financing decision") from error
        if target in {Status.APPROVED, Status.MANUAL_REVIEW, Status.REJECTED}:
            return target
    try:
        return _TRANSITIONS[(current, action, role)]
    except KeyError as error:
        raise InvalidTransition(
            f"{role.value} cannot {action.value} from {current.value}"
        ) from error


def allowed_actions(status: Status, role: Role) -> list[Action]:
    actions: list[Action] = []
    for action in Action:
        try:
            next_status(
                status,
                action,
                role,
                decision="approved" if action == Action.DECIDE else None,
            )
        except InvalidTransition:
            continue
        actions.append(action)
    return actions
