"""Pins who may see an application, including where the model stops short.

These exercise WorkflowService._can_view directly rather than through the
database, so the visibility rules stay legible as rules.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.domain.workflow import Status
from app.identity import AuthenticatedUser
from app.services.workflow import WorkflowService

SUPPLIER_ORG = uuid.uuid4()
CORE_ORG = uuid.uuid4()
FIRST_BANK = uuid.uuid4()
SECOND_BANK = uuid.uuid4()


def _user(role: str, organization_id: uuid.UUID) -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=uuid.uuid4(),
        username=f"{role}.test",
        display_name=role,
        role=role,
        organization_id=organization_id,
        organization_code="ORG-001",
        organization_name="Organisation",
    )


def _application(status: str = Status.TRADE_CONFIRMED.value) -> SimpleNamespace:
    return SimpleNamespace(
        status=status,
        supplier_organization_id=SUPPLIER_ORG,
        core_enterprise_organization_id=CORE_ORG,
    )


def test_a_supplier_sees_only_its_own_applications():
    application = _application()
    assert WorkflowService._can_view(application, _user("supplier", SUPPLIER_ORG))
    assert not WorkflowService._can_view(
        application, _user("supplier", uuid.uuid4())
    )


def test_a_core_enterprise_sees_only_trades_named_against_it():
    application = _application()
    assert WorkflowService._can_view(
        application, _user("core_enterprise", CORE_ORG)
    )
    assert not WorkflowService._can_view(
        application, _user("core_enterprise", uuid.uuid4())
    )


@pytest.mark.parametrize(
    "status",
    [Status.DRAFT.value, Status.SUBMITTED.value, Status.TRADE_RETURNED.value],
)
def test_a_financier_cannot_see_an_application_before_the_trade_is_confirmed(
    status,
):
    assert not WorkflowService._can_view(
        _application(status), _user("financier", FIRST_BANK)
    )


def test_financier_visibility_is_by_stage_not_by_organisation():
    """A second funding organisation sees the same pipeline as the first.

    This is the documented boundary, not an accident: an application never
    records which financier handles it, so there is nothing to scope these
    roles by. If an assignment concept is ever introduced, this test should
    fail and be replaced by one asserting organisational isolation.
    """

    application = _application()
    assert WorkflowService._can_view(
        application, _user("financier", FIRST_BANK)
    )
    assert WorkflowService._can_view(
        application, _user("financier", SECOND_BANK)
    )
    # A risk manager enters later in the lifecycle, and is unscoped in the
    # same way once it does.
    assert WorkflowService._can_view(
        _application(Status.APPROVED.value), _user("risk_manager", SECOND_BANK)
    )


def test_a_risk_manager_waits_for_a_financing_decision():
    assert not WorkflowService._can_view(
        _application(Status.TRADE_CONFIRMED.value),
        _user("risk_manager", FIRST_BANK),
    )


def test_an_auditor_sees_every_application_and_an_unknown_role_sees_none():
    application = _application()
    assert WorkflowService._can_view(application, _user("auditor", uuid.uuid4()))
    with pytest.raises(ValueError):
        WorkflowService._can_view(application, _user("intruder", uuid.uuid4()))
