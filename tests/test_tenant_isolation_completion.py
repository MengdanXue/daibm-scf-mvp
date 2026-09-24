"""Phase 5.1: organization-owned models, snapshots and applications."""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from app.database import Database
from app.identity import AuthenticatedUser
from app.models import FinancingRequestModel
from app.models_governance import CalibrationJobModel, CalibrationRunObservationModel
from app.models_model_governance import (
    RiskDecisionRecordModel,
    RiskModelVersionModel,
    RiskModelVersionTransitionModel,
    TrainingDatasetSnapshotItemModel,
    TrainingDatasetSnapshotModel,
)
from app.models_outcome import ActualOutcomeModel, CalibrationRunModel
from app.services.adaptive_risk import AdaptiveRiskInferenceService
from app.services.calibration_jobs import CalibrationJobService
from app.services.facility import FacilityNotFound, FacilityService
from app.services.identity import IdentityService
from app.services.model_registry import ModelRegistryService, RegistryNotFound
from app.services.organizations import OrganizationService
from app.services.outcome_governance import OutcomeGovernanceService
from app.services.outcomes import OutcomeNotFound, OutcomeService
from app.services.workflow import (
    ApplicationNotFound,
    LenderLocked,
    LenderSelectionRequired,
    WorkflowService,
)
from test_facility_service import _activate
from test_model_governance import START, _seed_outcomes
from test_workflow_service import draft_payload

ROOT = Path(__file__).resolve().parents[1]
STRONG = "Tenant-B-Pass-2026!"


def _login_all(session_factory) -> dict[str, AuthenticatedUser]:
    identity = IdentityService(session_factory, clock=lambda: START)
    identity.seed_demo_accounts()
    users = {
        name: identity.login(f"{name}.demo", "Demo123!").user
        for name in ("supplier", "core", "financier", "risk", "auditor", "admin")
    }
    organizations = OrganizationService(session_factory)
    bank_b = organizations.create_organization(
        users["admin"], code="BANK-B", name="Bank B", organization_type="financier"
    )
    for username, role in (("fin.b", "financier"), ("risk.b", "risk_manager")):
        organizations.create_user(
            users["admin"], username=username, display_name=username, role=role,
            organization_id=bank_b["organization_id"], password=STRONG,
        )
    users["fin_b"] = identity.login("fin.b", STRONG).user
    users["risk_b"] = identity.login("risk.b", STRONG).user
    return users


@pytest.fixture
def banks(session_factory, tmp_path):
    users = _login_all(session_factory)
    clock = [START + timedelta(days=30)]
    outcomes = OutcomeService(session_factory, artifact_root=tmp_path, clock=lambda: clock[0])
    worker = CalibrationJobService(session_factory, outcome_service=outcomes, clock=lambda: clock[0])

    def drain() -> None:
        while worker.process_next("tenant-test-worker"):
            pass

    def seed(bank: str, count: int = 40) -> list[uuid.UUID]:
        financier = users["financier"] if bank == "A" else users["fin_b"]
        return _seed_outcomes(
            session_factory,
            {"financier": financier, "auditor": users["auditor"]},
            count=count,
            start=START if bank == "A" else START + timedelta(hours=2),
        )

    registry = ModelRegistryService(session_factory, outcome_service=outcomes, clock=lambda: clock[0])
    return users, outcomes, drain, seed, registry


def _active(session_factory) -> dict[uuid.UUID, RiskModelVersionModel]:
    with session_factory() as session:
        return {
            item.organization_id: item
            for item in session.scalars(
                select(RiskModelVersionModel).where(RiskModelVersionModel.status == "ACTIVE")
            )
        }


# --- Model registry tenancy ------------------------------------------------------------------


def test_each_organization_trains_and_activates_only_its_own_model(banks, session_factory):
    users, _, drain, seed, _ = banks
    seed("A")
    seed("B")
    drain()
    active = _active(session_factory)
    bank_a, bank_b = users["financier"].organization_id, users["fin_b"].organization_id
    # One ACTIVE model per organization in the same scope.
    assert set(active) == {bank_a, bank_b}
    assert {item.scope for item in active.values()} == {"controlled_demo"}
    with session_factory() as session:
        for owner, version in active.items():
            run = session.get(CalibrationRunModel, version.calibration_run_id)
            snapshot = session.get(TrainingDatasetSnapshotModel, version.dataset_snapshot_id)
            assert run.organization_id == snapshot.organization_id == owner
            # Every training outcome belongs to the owning organization.
            members = session.scalars(
                select(func.outcome_organization(TrainingDatasetSnapshotItemModel.outcome_id))
                .where(TrainingDatasetSnapshotItemModel.snapshot_id == snapshot.snapshot_id)
            ).all()
            assert set(members) == {owner} and len(members) == 40
            observed = session.scalars(
                select(func.outcome_organization(CalibrationRunObservationModel.outcome_id))
                .where(CalibrationRunObservationModel.calibration_run_id == run.calibration_run_id)
            ).all()
            assert set(observed) == {owner}
        jobs = session.execute(
            select(CalibrationJobModel.organization_id, func.count()).group_by(
                CalibrationJobModel.organization_id
            )
        ).all()
    assert dict(jobs) == {bank_a: 40, bank_b: 40}


def test_an_organization_sees_only_its_own_models_and_snapshots(banks, session_factory):
    users, outcomes, drain, seed, registry = banks
    seed("A")
    seed("B")
    drain()
    active = _active(session_factory)
    theirs = active[users["fin_b"].organization_id]
    mine = active[users["financier"].organization_id]

    listed = registry.list_versions(users["risk"])
    assert {item["id"] for item in listed["versions"]} == {str(mine.id)}
    assert listed["active_by_scope"]["controlled_demo"]["id"] == str(mine.id)
    with pytest.raises(RegistryNotFound):
        registry.get_version(theirs.id, users["risk"])
    models = registry.list_models(users["financier"])["models"]
    assert all(
        item["organization_id"] in (None, str(users["financier"].organization_id))
        for item in models
    )
    governance = OutcomeGovernanceService(session_factory, outcome_service=outcomes)
    snapshots = governance.list_snapshots(users["risk"])
    assert {item["organization_id"] for item in snapshots} == {str(users["financier"].organization_id)}
    with pytest.raises(OutcomeNotFound):
        governance.get_snapshot(theirs.dataset_snapshot_id, users["risk"])
    with pytest.raises(OutcomeNotFound):
        outcomes.get_run(theirs.calibration_run_id, users["auditor"])
    # An auditor without a grant for bank B cannot promote or roll back its models.
    with pytest.raises(RegistryNotFound):
        registry.rollback_version(theirs.id, users["auditor"], reason_code="CROSS_TENANT")
    # Admin sees every organization's models, keyed by organization.
    everything = registry.list_versions(users["admin"])
    assert {item["id"] for item in everything["versions"]} == {str(mine.id), str(theirs.id)}
    assert set(everything["active_by_organization"]) == {"BANK-001", "BANK-B"}


def test_decisions_use_only_the_lenders_active_model(banks, session_factory):
    users, _, drain, seed, _ = banks
    seed("B")
    drain()
    (theirs,) = _active(session_factory).values()
    inference = AdaptiveRiskInferenceService()
    with session_factory() as session:
        own = inference.assess(session, 0.4, "controlled_demo", users["financier"].organization_id)
        lender_b = inference.assess(session, 0.4, "controlled_demo", users["fin_b"].organization_id)
        nobody = inference.assess(session, 0.4, "controlled_demo", None)
    # Bank A has no model: another organization's ACTIVE model is never used.
    assert own.calibration_run_id is None and own.model_version_id is None
    assert own.final_score == 0.4
    assert lender_b.model_version_id == str(theirs.id) and lender_b.final_score != 0.4
    assert nobody.calibration_run_id is None

    # The workflow decision of bank A is uncalibrated and records no model.
    workflow = WorkflowService(session_factory)
    payload = draft_payload().model_copy(update={"lender_organization_code": "BANK-001"})
    draft = workflow.create_draft(payload, users["supplier"])
    submitted = workflow.submit(draft["request_id"], 1, users["supplier"])
    confirmed = workflow.confirm_trade(
        draft["request_id"], submitted["version"], confirmed=True, comment="ok",
        user=users["core"], confirmed_payable_amount=Decimal("1500000.00"),
    )
    assessed = workflow.assess_risk(draft["request_id"], confirmed["version"], users["financier"])
    assert assessed["risk_score"] == assessed["raw_risk_score"]
    with session_factory() as session:
        record = session.scalar(select(RiskDecisionRecordModel))
    assert record.model_version_id is None and record.calibration_run_id is None


def test_database_rejects_cross_tenant_models_artifacts_and_snapshots(banks, session_factory):
    users, _, drain, seed, _ = banks
    mine_outcomes = seed("A")
    seed("B")
    drain()
    active = _active(session_factory)
    theirs = active[users["fin_b"].organization_id]

    def rejected(statement: str, **values) -> None:
        with pytest.raises(DBAPIError):
            with session_factory.begin() as session:
                session.execute(text(statement), values)

    # Organization A's outcome cannot enter organization B's snapshot or run.
    rejected(
        "INSERT INTO training_dataset_snapshot_items (snapshot_id, outcome_id, included, "
        "review_status) VALUES (:s, :o, true, 'ELIGIBLE')",
        s=theirs.dataset_snapshot_id, o=mine_outcomes[0],
    )
    rejected(
        "INSERT INTO calibration_run_observations (calibration_run_id, outcome_id) VALUES (:r, :o)",
        r=theirs.calibration_run_id, o=mine_outcomes[0],
    )
    # Organization A's financier cannot record a decision on organization B's model.
    rejected(
        "INSERT INTO risk_decision_records (decision_record_id, request_id, risk_assessment_id, "
        "assessed_by_user_id, actor_role, request_scope, input_sha256, input_snapshot, "
        "engine_version, raw_score, final_score, band, scope_result, scope_reason, recorded_at, "
        "model_version_id) SELECT :id, request_id, gen_random_uuid(), :user, 'financier', "
        "'controlled_demo', repeat('a', 64), '{}'::jsonb, 'v', 0.4, 0.4, 'medium', "
        "'no_active_model', 'x', now(), :version FROM financing_requests LIMIT 1",
        id=uuid.uuid4(), user=users["financier"].user_id, version=theirs.id,
    )
    # Ownership never moves between organizations.
    rejected(
        "UPDATE risk_model_versions SET organization_id = :org WHERE id = :id",
        org=users["financier"].organization_id, id=theirs.id,
    )
    rejected(
        "UPDATE calibration_runs SET organization_id = :org WHERE calibration_run_id = :id",
        org=users["financier"].organization_id, id=theirs.calibration_run_id,
    )
    # A version of B's run cannot claim to belong to A.
    rejected(
        "INSERT INTO risk_model_versions (id, model_id, version, model_type, calibration_run_id, "
        "artifact_path, artifact_hash, scope, training_dataset_version, metrics, status, "
        "status_sequence, created_by, created_at, organization_id) SELECT gen_random_uuid(), "
        "'x', 999, 'platt_calibration', calibration_run_id, artifact_path, artifact_hash, scope, "
        "training_dataset_version, '{}'::jsonb, 'DRAFT', 1, 'test', now(), :org "
        "FROM risk_model_versions WHERE id = :id",
        org=users["financier"].organization_id, id=theirs.id,
    )
    # A recorded outcome cannot point at another organization's model version.
    with session_factory() as session:
        template = session.get(ActualOutcomeModel, mine_outcomes[1])
        facility_id, request_id = template.facility_id, template.request_id
    rejected(
        "INSERT INTO actual_outcomes (outcome_id, facility_id, request_id, risk_assessment_id, "
        "model_version_id, submitted_by_user_id, idempotency_key, request_sha256, defaulted, "
        "days_past_due, loss_amount, observed_at, evidence_sha256, provenance, "
        "original_risk_score, risk_engine_version, risk_input_sha256, recorded_at) "
        "VALUES (gen_random_uuid(), :f, :r, gen_random_uuid(), :v, :u, gen_random_uuid(), "
        "repeat('b', 64), false, 0, 0, now(), repeat('c', 64), 'CONTROLLED_DEMO', 0.2, 'x', "
        "repeat('d', 64), now())",
        f=facility_id, r=request_id, v=theirs.id, u=users["auditor"].user_id,
    )


def test_promotion_and_rollback_stay_inside_the_organization(banks, session_factory):
    users, outcomes, drain, seed, registry = banks
    seed("A")
    seed("B")
    drain()
    bank_b = users["fin_b"].organization_id
    OrganizationService(session_factory).grant_audit(
        users["admin"], auditor_user_id=str(users["auditor"].user_id),
        organization_id=str(bank_b), reason="annual audit",
    )
    theirs = _active(session_factory)[bank_b]
    detail = registry.get_version(theirs.id, users["auditor"])
    assert detail["organization_code"] == "BANK-B"
    # The active deployment is asked per organization.
    active = outcomes.get_active_deployment(
        users["auditor"], scope="controlled_demo", organization_id=bank_b
    )
    assert active["organization_id"] == str(bank_b)
    with pytest.raises(Exception, match="organization_id is required"):
        outcomes.get_active_deployment(users["auditor"], scope="controlled_demo")
    mine = _active(session_factory)[users["financier"].organization_id]
    assert outcomes.get_active_deployment(
        users["auditor"], scope="controlled_demo", organization_id=mine.organization_id
    )["calibration_run_id"] == str(mine.calibration_run_id)


# --- Application stage ------------------------------------------------------------------------


def _addressed_to_b(session_factory, users) -> tuple[WorkflowService, dict]:
    workflow = WorkflowService(session_factory)
    payload = draft_payload().model_copy(update={"lender_organization_code": "BANK-B"})
    draft = workflow.create_draft(payload, users["supplier"])
    submitted = workflow.submit(draft["request_id"], 1, users["supplier"])
    confirmed = workflow.confirm_trade(
        draft["request_id"], submitted["version"], confirmed=True, comment="ok",
        user=users["core"], confirmed_payable_amount=Decimal("1500000.00"),
    )
    return workflow, confirmed


def test_application_lender_is_required_when_several_banks_exist(session_factory):
    users = _login_all(session_factory)
    workflow = WorkflowService(session_factory)
    with pytest.raises(LenderSelectionRequired):
        workflow.create_draft(draft_payload(), users["supplier"])
    with pytest.raises(ApplicationNotFound):
        workflow.create_draft(
            draft_payload().model_copy(update={"lender_organization_code": "CORE-001"}),
            users["supplier"],
        )
    draft = workflow.create_draft(
        draft_payload().model_copy(update={"lender_organization_code": "BANK-B"}),
        users["supplier"],
    )
    assert draft["lender_organization_code"] == "BANK-B"
    assert IdentityService(session_factory).lenders() == [
        {"organization_code": "BANK-001", "name": "Банк «Развитие»"},
        {"organization_code": "BANK-B", "name": "Bank B"},
    ]


def test_other_banks_cannot_see_review_or_approve_an_application(session_factory):
    users = _login_all(session_factory)
    workflow, confirmed = _addressed_to_b(session_factory, users)
    request_id = confirmed["request_id"]
    # Bank A's financier: the application does not exist, even by id.
    assert request_id not in {item["request_id"] for item in workflow.list_for_user(users["financier"])}
    with pytest.raises(ApplicationNotFound):
        workflow.get(request_id, users["financier"])
    with pytest.raises(ApplicationNotFound):
        workflow.assess_risk(request_id, confirmed["version"], users["financier"])
    with pytest.raises(ApplicationNotFound):
        workflow.risk_decisions(request_id, users["financier"])
    # Bank B reviews and approves it.
    assessed = workflow.assess_risk(request_id, confirmed["version"], users["fin_b"])
    decided = workflow.decide(
        request_id, assessed["version"], decision="approved", comment="ok", user=users["fin_b"]
    )
    assert decided["status"] in {"approved", "manual_review", "rejected"}
    assert request_id in {item["request_id"] for item in workflow.list_for_user(users["fin_b"])}
    # Bank A's risk manager never sees it; bank B's does once it is decided.
    assert request_id not in {item["request_id"] for item in workflow.list_for_user(users["risk"])}
    assert request_id in {item["request_id"] for item in workflow.list_for_user(users["risk_b"])}
    # The auditor sees it only with a grant for bank B; admin always.
    with pytest.raises(ApplicationNotFound):
        workflow.get(request_id, users["auditor"])
    OrganizationService(session_factory).grant_audit(
        users["admin"], auditor_user_id=str(users["auditor"].user_id),
        organization_id=str(users["fin_b"].organization_id), reason="annual audit",
    )
    assert workflow.get(request_id, users["auditor"])["request_id"] == request_id
    assert workflow.get(request_id, users["admin"])["request_id"] == request_id


def test_lender_is_fixed_once_submitted(session_factory):
    users = _login_all(session_factory)
    workflow, confirmed = _addressed_to_b(session_factory, users)
    returned = WorkflowService(session_factory)
    draft = returned.create_draft(
        draft_payload().model_copy(
            update={"lender_organization_code": "BANK-B", "invoice_number": "INV-LOCK-1"}
        ),
        users["supplier"],
    )
    # Still a draft: the supplier may readdress it.
    moved = returned.update_draft(
        draft["request_id"], 1,
        draft_payload().model_copy(
            update={"lender_organization_code": "BANK-001", "invoice_number": "INV-LOCK-1"}
        ),
        users["supplier"],
    )
    assert moved["lender_organization_code"] == "BANK-001"
    submitted = returned.submit(draft["request_id"], moved["version"], users["supplier"])
    sent_back = returned.confirm_trade(
        draft["request_id"], submitted["version"], confirmed=False, comment="fix the invoice",
        user=users["core"],
    )
    assert sent_back["status"] == "trade_returned"
    with pytest.raises(LenderLocked):
        returned.update_draft(
            draft["request_id"], sent_back["version"],
            draft_payload().model_copy(
                update={"lender_organization_code": "BANK-B", "invoice_number": "INV-LOCK-1"}
            ),
            users["supplier"],
        )
    with pytest.raises(DBAPIError, match="fixed once"):
        with session_factory.begin() as session:
            session.execute(
                text("UPDATE financing_requests SET lender_organization_id = :org WHERE request_id = :id"),
                {"org": users["financier"].organization_id, "id": confirmed["request_id"]},
            )


def test_only_the_lender_can_open_a_facility(session_factory):
    users = _login_all(session_factory)
    facilities = FacilityService(session_factory)
    request_id = uuid.uuid4()
    with session_factory.begin() as session:
        template = session.scalar(select(FinancingRequestModel).limit(1))
        assert template is None
        session.add(
            FinancingRequestModel(
                request_id=request_id, created_at=START, updated_at=START,
                applicant_id="SUPPLIER-001", amount=Decimal("1000.00"), term_days=90,
                features={}, risk_score=0.1, decision="approved", explanations=[],
                status="audited", version=7, created_by_user_id=users["supplier"].user_id,
                supplier_organization_id=users["supplier"].organization_id,
                core_enterprise_organization_id=users["core"].organization_id,
                lender_organization_id=users["fin_b"].organization_id,
            )
        )
    a_map = {
        "financier.demo": users["financier"],
        "supplier.demo": users["supplier"],
        "core.demo": users["core"],
    }
    with pytest.raises(FacilityNotFound):
        _activate(facilities, a_map, request_id)
    with pytest.raises(DBAPIError, match="application lender"):
        with session_factory.begin() as session:
            session.execute(
                text(
                    "INSERT INTO financing_facilities (facility_id, request_id, principal, "
                    "outstanding_amount, currency, status, version, current_schedule_version, "
                    "created_by_user_id, created_at, updated_at) VALUES (gen_random_uuid(), :r, "
                    "1000, 1000, 'CNY', 'ready', 1, 1, :u, now(), now())"
                ),
                {"r": request_id, "u": users["financier"].user_id},
            )
    b_map = {**a_map, "financier.demo": users["fin_b"]}
    facility = _activate(facilities, b_map, request_id)
    assert facility["status"] == "active"


def test_application_isolation_over_http(session_factory, migrated_engine):
    from app.main import create_app

    users = _login_all(session_factory)
    _, confirmed = _addressed_to_b(session_factory, users)
    app = create_app(Database(migrated_engine, session_factory), calibration_worker_enabled=False)
    with TestClient(app) as client:
        client.post("/api/v1/auth/login", json={"username": "supplier.demo", "password": "Demo123!"})
        lenders = client.get("/api/v1/organizations/lenders").json()
        assert [item["organization_code"] for item in lenders] == ["BANK-001", "BANK-B"]
        response = client.post("/api/v1/applications", json=draft_payload().model_dump())
        assert response.status_code == 422 and response.json()["detail"]["code"] == "lender_required"
        client.post("/api/v1/auth/logout")
        client.post("/api/v1/auth/login", json={"username": "financier.demo", "password": "Demo123!"})
        assert client.get(f"/api/v1/applications/{confirmed['request_id']}").status_code == 404
        assert client.post(
            f"/api/v1/applications/{confirmed['request_id']}/risk-assessment",
            json={"version": confirmed["version"]},
        ).status_code == 404
        listed = client.get("/api/v1/applications").json()
        items = listed["items"] if isinstance(listed, dict) else listed
        assert all(item["request_id"] != confirmed["request_id"] for item in items)


# --- Migration on existing data -----------------------------------------------------------------


def test_upgrade_assigns_existing_models_to_their_organization(isolated_postgres_engine, tmp_path):
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))

    def migrate(target: str, *, down: bool = False) -> None:
        with isolated_postgres_engine.connect() as connection:
            config.attributes["connection"] = connection
            (command.downgrade if down else command.upgrade)(config, target)
            connection.commit()

    migrate("head")
    factory = sessionmaker(bind=isolated_postgres_engine, expire_on_commit=False)
    identity = IdentityService(factory, clock=lambda: START)
    identity.seed_demo_accounts()
    users = {
        name: identity.login(f"{name}.demo", "Demo123!").user for name in ("financier", "auditor")
    }
    clock = [START + timedelta(days=30)]
    outcomes = OutcomeService(factory, artifact_root=tmp_path, clock=lambda: clock[0])
    worker = CalibrationJobService(factory, outcome_service=outcomes, clock=lambda: clock[0])
    _seed_outcomes(factory, users, count=40, start=START)
    while worker.process_next("migration-worker"):
        pass

    def state():
        with factory() as session:
            return (
                session.execute(
                    select(RiskModelVersionModel.id, RiskModelVersionModel.status)
                    .order_by(RiskModelVersionModel.id)
                ).all(),
                session.scalar(select(func.count()).select_from(RiskModelVersionTransitionModel)),
                session.execute(
                    select(CalibrationRunModel.calibration_run_id, CalibrationRunModel.deployment_status)
                    .order_by(CalibrationRunModel.calibration_run_id)
                ).all(),
            )

    before = state()
    migrate("20260929_0020", down=True)
    migrate("head")
    assert state() == before
    with factory() as session:
        for model in (CalibrationJobModel, TrainingDatasetSnapshotModel, CalibrationRunModel, RiskModelVersionModel):
            owners = set(session.scalars(select(model.organization_id)))
            assert owners == {users["financier"].organization_id}, model.__tablename__
