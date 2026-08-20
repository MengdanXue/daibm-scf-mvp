import uuid
from dataclasses import replace
from datetime import datetime, timezone
import pytest
from sqlalchemy import func, select

from app.identity import AuthenticatedUser
from app.models import FinancingRequestModel, LedgerEventModel
from app.models_identity import OrganizationModel
from app.models_workflow import WorkflowActionModel
from app.repositories.ledger import LedgerRepository
from app.schemas_workflow import ApplicationDraftCreate
from app.services.identity import IdentityService
from app.services.workflow import (
    ApplicationNotFound,
    DuplicateInvoiceClaim,
    ForbiddenWorkflow,
    StaleApplication,
    WorkflowService,
)


def draft_payload() -> ApplicationDraftCreate:
    return ApplicationDraftCreate(
        core_enterprise_organization_code="CORE-001",
        contract_number="SCF-2026-001",
        invoice_number="INV-2026-001",
        amount=1_200_000,
        term_days=90,
        payment_delay_days=18,
        counterparty_risk=0.58,
        invoice_mismatch=True,
        relationship_months=18,
        transactions_last_30d=12,
    )


def demo_users(session_factory) -> dict[str, AuthenticatedUser]:
    identity = IdentityService(session_factory)
    identity.seed_demo_accounts()
    users = {}
    for username in (
        "supplier.demo",
        "core.demo",
        "financier.demo",
        "risk.demo",
        "auditor.demo",
    ):
        login = identity.login(username, "Demo123!")
        users[login.user.role] = login.user
    return users


def test_five_roles_complete_one_application_with_traceable_versions(
    session_factory,
):
    users = demo_users(session_factory)
    service = WorkflowService(session_factory)

    draft = service.create_draft(draft_payload(), users["supplier"])
    assert draft["status"] == "draft"
    assert draft["version"] == 1
    assert draft["risk_score"] is None
    assert draft["allowed_actions"] == ["update", "submit"]
    assert len(draft["trade_evidence"]["fingerprint_sha256"]) == 64
    assert len(draft["trade_evidence"]["invoice_claim_sha256"]) == 64
    assert draft["trade_evidence"]["duplicate_check"] == "passed"
    assert draft["risk_evidence"] is None

    submitted = service.submit(
        draft["request_id"], draft["version"], users["supplier"]
    )
    confirmed = service.confirm_trade(
        draft["request_id"],
        submitted["version"],
        confirmed=True,
        comment="Trade documents confirmed",
        user=users["core_enterprise"],
    )
    assessed = service.assess_risk(
        draft["request_id"], confirmed["version"], users["financier"]
    )
    assert 0 <= assessed["risk_score"] <= 1
    assert assessed["explanations"]
    assert assessed["risk_evidence"]["engine_version"] == (
        "transparent_logistic_baseline_v0.1"
    )
    assert assessed["risk_evidence"]["engine_type"] == "business_baseline"
    assert len(assessed["risk_evidence"]["assessment_id"]) == 36
    assert len(assessed["risk_evidence"]["input_sha256"]) == 64
    assert assessed["risk_evidence"]["provenance"] == "DEMO_WORKFLOW"

    decided = service.decide(
        draft["request_id"],
        assessed["version"],
        decision="manual_review",
        comment="Additional documents required",
        user=users["financier"],
    )
    controlled = service.apply_control(
        draft["request_id"],
        decided["version"],
        comment="Enhanced validation assigned",
        user=users["risk_manager"],
    )
    audited = service.audit(
        draft["request_id"],
        controlled["version"],
        comment="Ledger and workflow verified",
        user=users["auditor"],
    )

    assert audited["status"] == "audited"
    assert audited["version"] == 7
    assert audited["decision"] == "manual_review"
    assert audited["allowed_actions"] == []
    assert [entry["actor_role"] for entry in audited["timeline"]] == [
        "supplier",
        "supplier",
        "core_enterprise",
        "financier",
        "financier",
        "risk_manager",
        "auditor",
    ]

    with session_factory() as session:
        assert (
            session.scalar(select(func.count()).select_from(WorkflowActionModel))
            == 7
        )
        event_types = list(
            session.scalars(
                select(LedgerEventModel.event_type).order_by(LedgerEventModel.id)
            )
        )
    assert event_types == [
        "APPLICATION_DRAFT_CREATED",
        "APPLICATION_SUBMITTED",
        "TRADE_CONFIRMED",
        "RISK_ASSESSMENT",
        "FINANCING_DECISION",
        "CONTROL_ACTION",
        "AUDIT_REVIEW_COMPLETED",
    ]


def test_duplicate_invoice_claim_is_rejected_case_insensitively(session_factory):
    users = demo_users(session_factory)
    service = WorkflowService(session_factory)
    service.create_draft(draft_payload(), users["supplier"])
    duplicate = draft_payload().model_copy(
        update={
            "contract_number": "SCF-2026-OTHER",
            "invoice_number": "  inv-2026-001  ",
            "amount": 900_000,
        }
    )

    with pytest.raises(DuplicateInvoiceClaim):
        service.create_draft(duplicate, users["supplier"])

    with session_factory() as session:
        assert (
            session.scalar(
                select(func.count()).select_from(FinancingRequestModel)
            )
            == 1
        )


def test_application_serializes_the_selected_core_enterprise(session_factory):
    users = demo_users(session_factory)
    with session_factory.begin() as session:
        session.add(
            OrganizationModel(
                organization_id=uuid.uuid4(),
                organization_code="CORE-ALT-002",
                name="АО «Вторая якорная компания»",
                organization_type="core_enterprise",
                created_at=datetime.now(timezone.utc),
            )
        )

    service = WorkflowService(session_factory)
    created = service.create_draft(
        draft_payload().model_copy(
            update={
                "core_enterprise_organization_code": "CORE-ALT-002",
                "contract_number": "SCF-2026-ALT",
                "invoice_number": "INV-2026-ALT",
            }
        ),
        users["supplier"],
    )

    assert created["core_enterprise_organization_code"] == "CORE-ALT-002"
    assert created["core_enterprise_organization_name"] == (
        "АО «Вторая якорная компания»"
    )


def test_returned_application_can_be_updated_and_resubmitted(session_factory):
    users = demo_users(session_factory)
    service = WorkflowService(session_factory)
    draft = service.create_draft(draft_payload(), users["supplier"])
    submitted = service.submit(
        draft["request_id"], draft["version"], users["supplier"]
    )
    returned = service.confirm_trade(
        draft["request_id"],
        submitted["version"],
        confirmed=False,
        comment="Invoice copy is unreadable",
        user=users["core_enterprise"],
    )
    revised_payload = draft_payload().model_copy(
        update={"invoice_number": "INV-2026-001-R1"}
    )

    revised = service.update_draft(
        draft["request_id"],
        returned["version"],
        revised_payload,
        users["supplier"],
    )
    resubmitted = service.submit(
        draft["request_id"], revised["version"], users["supplier"]
    )

    assert revised["invoice_number"] == "INV-2026-001-R1"
    assert resubmitted["status"] == "submitted"


def test_stale_version_is_rejected_without_new_action(session_factory):
    users = demo_users(session_factory)
    service = WorkflowService(session_factory)
    draft = service.create_draft(draft_payload(), users["supplier"])
    service.submit(draft["request_id"], 1, users["supplier"])

    with pytest.raises(StaleApplication):
        service.submit(draft["request_id"], 1, users["supplier"])

    with session_factory() as session:
        action_count = session.scalar(
            select(func.count()).select_from(WorkflowActionModel)
        )
    assert action_count == 2


def test_role_and_organization_scope_are_enforced(session_factory):
    users = demo_users(session_factory)
    service = WorkflowService(session_factory)
    draft = service.create_draft(draft_payload(), users["supplier"])

    with pytest.raises(ForbiddenWorkflow):
        service.confirm_trade(
            draft["request_id"],
            draft["version"],
            confirmed=True,
            comment="not allowed",
            user=users["supplier"],
        )

    other_supplier = replace(
        users["supplier"],
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        organization_code="SUPPLIER-OTHER",
    )
    assert service.list_for_user(other_supplier) == []
    with pytest.raises(ApplicationNotFound):
        service.get(draft["request_id"], other_supplier)


class FailingLedgerRepository(LedgerRepository):
    def append_many(self, *args, **kwargs):
        raise RuntimeError("ledger unavailable")


def test_ledger_failure_rolls_back_draft_and_workflow_action(session_factory):
    users = demo_users(session_factory)
    service = WorkflowService(
        session_factory,
        ledger_repository=FailingLedgerRepository(),
    )

    with pytest.raises(RuntimeError, match="ledger unavailable"):
        service.create_draft(draft_payload(), users["supplier"])

    with session_factory() as session:
        assert (
            session.scalar(
                select(func.count()).select_from(FinancingRequestModel)
            )
            == 0
        )
        assert (
            session.scalar(select(func.count()).select_from(WorkflowActionModel))
            == 0
        )
