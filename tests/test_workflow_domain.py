import pytest

from app.domain.workflow import (
    Action,
    InvalidTransition,
    Role,
    Status,
    allowed_actions,
    next_status,
)


@pytest.mark.parametrize(
    ("current", "action", "role", "decision", "expected"),
    (
        (Status.DRAFT, Action.UPDATE, Role.SUPPLIER, None, Status.DRAFT),
        (
            Status.TRADE_RETURNED,
            Action.UPDATE,
            Role.SUPPLIER,
            None,
            Status.TRADE_RETURNED,
        ),
        (Status.DRAFT, Action.SUBMIT, Role.SUPPLIER, None, Status.SUBMITTED),
        (
            Status.TRADE_RETURNED,
            Action.SUBMIT,
            Role.SUPPLIER,
            None,
            Status.SUBMITTED,
        ),
        (
            Status.SUBMITTED,
            Action.CONFIRM_TRADE,
            Role.CORE_ENTERPRISE,
            None,
            Status.TRADE_CONFIRMED,
        ),
        (
            Status.SUBMITTED,
            Action.RETURN_TRADE,
            Role.CORE_ENTERPRISE,
            None,
            Status.TRADE_RETURNED,
        ),
        (
            Status.TRADE_CONFIRMED,
            Action.ASSESS_RISK,
            Role.FINANCIER,
            None,
            Status.RISK_ASSESSED,
        ),
        (
            Status.RISK_ASSESSED,
            Action.DECIDE,
            Role.FINANCIER,
            "approved",
            Status.APPROVED,
        ),
        (
            Status.RISK_ASSESSED,
            Action.DECIDE,
            Role.FINANCIER,
            "manual_review",
            Status.MANUAL_REVIEW,
        ),
        (
            Status.RISK_ASSESSED,
            Action.DECIDE,
            Role.FINANCIER,
            "rejected",
            Status.REJECTED,
        ),
        (
            Status.APPROVED,
            Action.APPLY_CONTROL,
            Role.RISK_MANAGER,
            None,
            Status.CONTROLLED,
        ),
        (
            Status.MANUAL_REVIEW,
            Action.APPLY_CONTROL,
            Role.RISK_MANAGER,
            None,
            Status.CONTROLLED,
        ),
        (
            Status.REJECTED,
            Action.APPLY_CONTROL,
            Role.RISK_MANAGER,
            None,
            Status.CONTROLLED,
        ),
        (
            Status.CONTROLLED,
            Action.AUDIT,
            Role.AUDITOR,
            None,
            Status.AUDITED,
        ),
    ),
)
def test_next_status_accepts_every_approved_transition(
    current,
    action,
    role,
    decision,
    expected,
):
    assert next_status(current, action, role, decision=decision) == expected


@pytest.mark.parametrize(
    ("current", "action", "role", "decision"),
    (
        (Status.DRAFT, Action.SUBMIT, Role.CORE_ENTERPRISE, None),
        (Status.SUBMITTED, Action.CONFIRM_TRADE, Role.SUPPLIER, None),
        (Status.TRADE_CONFIRMED, Action.DECIDE, Role.FINANCIER, "approved"),
        (Status.RISK_ASSESSED, Action.DECIDE, Role.FINANCIER, "unknown"),
        (Status.APPROVED, Action.AUDIT, Role.AUDITOR, None),
        (Status.AUDITED, Action.UPDATE, Role.SUPPLIER, None),
    ),
)
def test_next_status_rejects_wrong_role_state_or_decision(
    current,
    action,
    role,
    decision,
):
    with pytest.raises(InvalidTransition):
        next_status(current, action, role, decision=decision)


def test_allowed_actions_are_derived_from_role_and_status():
    assert allowed_actions(Status.DRAFT, Role.SUPPLIER) == [
        Action.UPDATE,
        Action.SUBMIT,
    ]
    assert allowed_actions(Status.SUBMITTED, Role.CORE_ENTERPRISE) == [
        Action.CONFIRM_TRADE,
        Action.RETURN_TRADE,
    ]
    assert allowed_actions(Status.TRADE_CONFIRMED, Role.FINANCIER) == [
        Action.ASSESS_RISK
    ]
    assert allowed_actions(Status.CONTROLLED, Role.AUDITOR) == [Action.AUDIT]
    assert allowed_actions(Status.AUDITED, Role.AUDITOR) == []
