"""Pins who may see an application: organization ownership, then stage.

These exercise WorkflowService._can_view directly rather than through the
database, so the visibility rules stay legible as rules. Auditor scope comes
from audit grants in the database and is covered by the tenant isolation
tests; without a session the rule fails closed.
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
        lender_organization_id=FIRST_BANK,
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


def test_financier_visibility_is_scoped_to_the_lender_organisation():
    """The assignment concept exists now: an application names its lender.

    Only that organisation's financiers and risk managers see it, each from
    the stage their work starts; a second funding organisation sees nothing.
    """

    application = _application()
    assert WorkflowService._can_view(
        application, _user("financier", FIRST_BANK)
    )
    assert not WorkflowService._can_view(
        application, _user("financier", SECOND_BANK)
    )
    approved = _application(Status.APPROVED.value)
    assert WorkflowService._can_view(approved, _user("risk_manager", FIRST_BANK))
    assert not WorkflowService._can_view(approved, _user("risk_manager", SECOND_BANK))


def test_a_risk_manager_waits_for_a_financing_decision():
    assert not WorkflowService._can_view(
        _application(Status.TRADE_CONFIRMED.value),
        _user("risk_manager", FIRST_BANK),
    )


def test_admin_sees_every_application_and_an_unknown_role_sees_none():
    application = _application()
    assert WorkflowService._can_view(application, _user("admin", uuid.uuid4()))
    # Auditor scope needs the audit grants; without them the rule fails closed.
    assert not WorkflowService._can_view(application, _user("auditor", uuid.uuid4()))
    with pytest.raises(ValueError):
        WorkflowService._can_view(application, _user("intruder", uuid.uuid4()))
