from decimal import Decimal

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
from app.services.adaptive_risk import AdaptiveRiskResult
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
        confirmed_payable_amount=Decimal("1500000.00"),
    )
    assessed = service.assess_risk(
        draft["request_id"], confirmed["version"], users["financier"]
    )
    assert 0 <= assessed["risk_score"] <= 1
    assert assessed["raw_risk_score"] == assessed["risk_score"]
    assert assessed["explanations"]
    assert assessed["risk_evidence"]["engine_version"] == (
        "transparent_logistic_baseline_v0.1"
    )
    assert assessed["risk_evidence"]["engine_type"] == "business_baseline"
    assert len(assessed["risk_evidence"]["assessment_id"]) == 36
    assert len(assessed["risk_evidence"]["input_sha256"]) == 64
    assert assessed["risk_evidence"]["provenance"] == "DEMO_WORKFLOW"
    assert assessed["risk_evidence"]["raw_score"] == assessed["risk_score"]
    assert assessed["risk_evidence"]["final_score"] == assessed["risk_score"]
    assert assessed["risk_evidence"]["calibration_run_id"] is None
    assert assessed["risk_evidence"]["calibration_fallback_code"] is None

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


def test_invalid_active_calibration_falls_back_with_persisted_audit_lineage(
    session_factory,
):
    users = demo_users(session_factory)

    class CorruptActiveCalibration:
        def assess(self, _session, baseline_score, assessment_scope):
            return AdaptiveRiskResult(
                raw_score=baseline_score,
                final_score=baseline_score,
                calibration_run_id=None,
                deployment_scope=None,
                fallback_code="active_artifact_invalid",
            )

    service = WorkflowService(
        session_factory,
        adaptive_risk_service=CorruptActiveCalibration(),
    )
    draft = service.create_draft(draft_payload(), users["supplier"])
    submitted = service.submit(draft["request_id"], 1, users["supplier"])
    confirmed = service.confirm_trade(
        draft["request_id"],
        submitted["version"],
        confirmed=True,
        comment="Verified",
        user=users["core_enterprise"],
        confirmed_payable_amount=Decimal("1500000.00"),
    )

    assessed = service.assess_risk(
        draft["request_id"],
        confirmed["version"],
        users["financier"],
    )

    assert assessed["raw_risk_score"] == assessed["risk_score"]
    assert assessed["risk_evidence"]["calibration_run_id"] is None
    assert assessed["risk_evidence"]["calibration_fallback_code"] == "active_artifact_invalid"
    with session_factory() as session:
        stored = session.get(
            FinancingRequestModel,
            uuid.UUID(assessed["request_id"]),
        )
        event_types = list(
            session.scalars(select(LedgerEventModel.event_type).order_by(LedgerEventModel.id))
        )
    assert stored is not None
    assert stored.calibration_fallback_code == "active_artifact_invalid"
    assert event_types[-2:] == [
        "RISK_ASSESSMENT",
        "RISK_CALIBRATION_FALLBACK",
    ]


def test_external_request_never_uses_demo_deployment_and_records_attempt(session_factory, tmp_path):
    from tests.test_outcome_service import (
        _seed_closed_facility,
        _submission,
        _active_run_with_membership,
    )
    from app.services.outcomes import OutcomeService

    facility_id, auditor = _seed_closed_facility(session_factory)
    outcome_service = OutcomeService(session_factory, artifact_root=tmp_path)
    submitted_outcome = outcome_service.submit(facility_id, _submission(), auditor)
    attempted = _active_run_with_membership(
        session_factory, uuid.UUID(submitted_outcome["outcome"]["outcome_id"]), "controlled_demo"
    )
    users = demo_users(session_factory)
    service = WorkflowService(session_factory)
    draft = service.create_draft(draft_payload(), users["supplier"])
    # Trusted server-side declaration, never an application request-body field.
    with session_factory.begin() as session:
        session.get(
            FinancingRequestModel, uuid.UUID(draft["request_id"])
        ).assessment_scope = "external_verified"
    submitted = service.submit(draft["request_id"], 1, users["supplier"])
    confirmed = service.confirm_trade(
        draft["request_id"],
        submitted["version"],
        confirmed=True,
        comment="Verified",
        user=users["core_enterprise"],
        confirmed_payable_amount=Decimal("1500000.00"),
    )
    assessed = service.assess_risk(draft["request_id"], confirmed["version"], users["financier"])
    assert assessed["risk_score"] == assessed["raw_risk_score"]
    assert assessed["risk_evidence"]["calibration_run_id"] is None
    assert assessed["risk_evidence"]["calibration_fallback_code"] == "calibration_scope_mismatch"
    with session_factory() as session:
        event = session.scalar(
            select(LedgerEventModel).where(
                LedgerEventModel.entity_id == uuid.UUID(draft["request_id"]),
                LedgerEventModel.event_type == "RISK_CALIBRATION_FALLBACK",
            )
        )
    assert event.payload["attempted_calibration_run_id"] == str(attempted)
    assert event.payload["assessment_scope"] == "external_verified"


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
