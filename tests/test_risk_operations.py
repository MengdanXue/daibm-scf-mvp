"""Phase 3 risk operations: dashboard, alerts, tasks, rules, risk detail and permissions."""

from __future__ import annotations

import base64
import importlib.util
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.domain.risk_operations import (
    ALERT_TRANSITIONS,
    TASK_TRANSITIONS,
    AlertStatus,
    TaskStatus,
    risk_class,
)
from app.identity import AuthenticatedUser
from app.models import LedgerEventModel
from app.models_identity import OrganizationModel, UserModel
from app.models_model_governance import RiskDecisionRecordModel
from app.models_risk_ops import (
    RiskAlertEventModel,
    RiskAlertModel,
    RiskRuleModel,
    RiskTaskEventModel,
)
from app.services.facility import FacilityService
from app.services.identity import IdentityService
from app.services.risk_insight import RiskInsightService
from app.services.risk_operations import (
    RiskAlertService,
    RiskDetectionService,
    RiskOpsConflict,
    RiskOpsForbidden,
    RiskOpsNotFound,
    RiskRuleService,
)
from app.services.risk_tasks import RiskTaskService
from test_facility_lifecycle_governance import _pay, _recovery
from test_facility_service import (
    _activate,
    _approved_application,
    _decision,
    _defaulted,
    _mark_overdue,
    _start_recovery,
    _submit,
    _write_off,
)

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "alembic" / "versions" / "20260928_0019_risk_operations.py"
NOW = datetime(2025, 6, 1, tzinfo=timezone.utc)


def _all_users(session_factory) -> dict[str, AuthenticatedUser]:
    identity = IdentityService(session_factory)
    identity.seed_demo_accounts()
    return {
        name: identity.login(f"{name}.demo", "Demo123!").user
        for name in ("supplier", "core", "financier", "risk", "auditor", "admin")
    }


def _as_facility_users(users):
    return {f"{name}.demo": user for name, user in users.items()}


def _decision_record(session_factory, request_id, user, score: float, band: str, at: datetime):
    with session_factory.begin() as session:
        session.add(
            RiskDecisionRecordModel(
                decision_record_id=uuid.uuid4(), request_id=request_id,
                risk_assessment_id=uuid.uuid4(), assessed_by_user_id=user.user_id,
                actor_role=user.role, request_scope="controlled_demo", input_sha256="e" * 64,
                input_snapshot={}, engine_version="transparent_logistic_baseline_v0.1",
                raw_score=score, final_score=score, band=band, scope_result="no_active_model",
                scope_reason="no_active_model_in_scope", recorded_at=at,
            )
        )


@pytest.fixture
def portfolio(session_factory):
    """Four real facilities driven through the governed lifecycle services."""

    users = _all_users(session_factory)
    fusers = _as_facility_users(users)
    facilities = FacilityService(session_factory)

    healthy = _activate(facilities, fusers, _approved_application(session_factory, fusers))
    for index, reference in ((0, "BOUNCE-1"), (1, "BOUNCE-2")):
        healthy = facilities.submit_payment(
            healthy["facility_id"], _submit(healthy, index, "500.00", reference), fusers["supplier.demo"]
        )
        healthy = facilities.decide_payment(
            healthy["facility_id"], healthy["payments"][-1]["payment_id"],
            _decision(healthy["version"], "rejected"), fusers["financier.demo"],
        )

    overdue = _mark_overdue(
        facilities, fusers, _activate(facilities, fusers, _approved_application(session_factory, fusers))
    )

    lost = _start_recovery(
        facilities, fusers, _defaulted(facilities, fusers, _approved_application(session_factory, fusers))
    )
    lost = facilities.record_recovery(
        lost["facility_id"], _recovery(lost["version"], "250.00", "PRE-WRITEOFF"), fusers["financier.demo"]
    )
    lost = facilities.write_off(lost["facility_id"], _write_off(lost["version"]), fusers["auditor.demo"])
    lost = facilities.record_recovery(
        lost["facility_id"], _recovery(lost["version"], "150.00", "POST-WRITEOFF", "LEGAL_ENFORCEMENT"),
        fusers["financier.demo"],
    )

    repaid = _activate(facilities, fusers, _approved_application(session_factory, fusers))
    for index, reference in ((0, "PAY-1"), (1, "PAY-2")):
        repaid = _pay(facilities, fusers, repaid, index, "500.00", reference)

    _decision_record(session_factory, uuid.UUID(healthy["request_id"]), users["financier"], 0.12, "low", NOW)
    _decision_record(session_factory, uuid.UUID(overdue["request_id"]), users["financier"], 0.35, "medium", NOW)
    _decision_record(
        session_factory, uuid.UUID(overdue["request_id"]), users["financier"], 0.71, "high",
        NOW + timedelta(days=1),
    )
    return users, facilities, {
        "healthy": healthy, "overdue": overdue, "lost": lost, "repaid": repaid,
    }


def _services(session_factory, facilities):
    clock = lambda: NOW + timedelta(days=5)  # noqa: E731
    return {
        "rules": RiskRuleService(session_factory, clock=clock),
        "detect": RiskDetectionService(session_factory, clock=clock),
        "alerts": RiskAlertService(session_factory, clock=clock),
        "tasks": RiskTaskService(session_factory, clock=clock),
        "insight": RiskInsightService(session_factory, facility_service=facilities, clock=clock),
    }


# --- Domain contract ------------------------------------------------------------------


def test_domain_transitions_equal_the_frozen_database_lists():
    spec = importlib.util.spec_from_file_location("risk_ops_0019", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert {(AlertStatus(a), AlertStatus(b)) for a, b in module._ALERT_TRANSITIONS} == set(ALERT_TRANSITIONS)
    assert {(TaskStatus(a), TaskStatus(b)) for a, b in module._TASK_TRANSITIONS} == set(TASK_TRANSITIONS)


def test_risk_class_is_derived_from_lifecycle_and_scores():
    base = dict(status="active", closure_reason=None, max_days_past_due=0, latest_band="low", overdue_threshold=30)
    assert risk_class(**base) == "NORMAL"
    assert risk_class(**{**base, "latest_band": "medium"}) == "WATCH"
    assert risk_class(**{**base, "status": "overdue", "max_days_past_due": 10}) == "WATCH"
    assert risk_class(**{**base, "status": "overdue", "max_days_past_due": 30}) == "HIGH_RISK"
    assert risk_class(**{**base, "latest_band": "high"}) == "HIGH_RISK"
    assert risk_class(**{**base, "status": "in_disposal"}) == "HIGH_RISK"
    for status in ("defaulted", "in_recovery", "recovered", "written_off"):
        assert risk_class(**{**base, "status": status}) == "DEFAULTED"
    assert risk_class(**{**base, "status": "closed", "closure_reason": "written_off"}) == "DEFAULTED"
    assert risk_class(**{**base, "status": "repaid", "latest_band": "high"}) == "NORMAL"


# --- 1. Dashboard ---------------------------------------------------------------------------


def test_dashboard_statistics_come_from_recorded_business_data(session_factory, portfolio):
    users, facilities, items = portfolio
    services = _services(session_factory, facilities)
    board = services["insight"].dashboard(users["admin"])
    assert board["scope"] == "all"
    expected_balance = sum(
        Decimal(facilities.get(item["facility_id"], users["admin"])["outstanding_amount"])
        for item in items.values()
    )
    assert Decimal(board["assets"]["current_balance"]) == expected_balance
    assert board["assets"]["total_financed"] == "4000.00"
    assert board["assets"]["enterprise_count"] == 1
    assert board["assets"]["financing_count"] == 4
    assert board["risk_distribution"] == {"NORMAL": 2, "WATCH": 0, "HIGH_RISK": 1, "DEFAULTED": 1}
    assert board["lifecycle"] == {
        "normal_repaid": 1,
        "overdue": 2,  # the overdue facility and the one that later defaulted
        "disposal": 1,
        "restructured": 0,
        "recovery": 1,
        "written_off": 1,
    }
    assert board["losses"] == {
        "total_recovered": "400.00",
        "written_off": "750.00",
        "net_loss": "600.00",
        "recovery_rate": 0.4,
        "defaulted_exposure": "1000.00",
    }
    assert board["model"]["active"] == [] and board["model"]["latest_failure"] is None
    # Bank roles see their own organization's book: here the same four facilities.
    assert services["insight"].dashboard(users["risk"])["assets"]["financing_count"] == 4
    assert services["insight"].dashboard(users["risk"])["scope"] == "organization"
    with pytest.raises(RiskOpsForbidden):
        services["insight"].dashboard(users["supplier"])


# --- 2. Alert creation ---------------------------------------------------------------------


def test_rules_raise_alerts_once_per_source_record(session_factory, portfolio):
    users, facilities, items = portfolio
    services = _services(session_factory, facilities)
    result = services["detect"].scan_as(users["risk"])
    with session_factory() as session:
        alerts = list(session.scalars(select(RiskAlertModel)))
    by_rule = {}
    for alert in alerts:
        by_rule.setdefault(alert.rule_key, []).append(alert)
    assert result["created"] == len(alerts)
    assert {key: len(value) for key, value in by_rule.items()} == {
        "MODEL_SCORE_THRESHOLD": 1,  # 0.71 >= 0.60; 0.35 and 0.12 are below
        "OVERDUE_DAYS": 2,  # two delinquency records of 45 days
        "REPAYMENT_ANOMALY": 1,  # two rejected payments on the healthy facility
        "LIFECYCLE_ANOMALY": 2,  # the lost facility entered disposal and default
    }
    score = by_rule["MODEL_SCORE_THRESHOLD"][0]
    assert (score.severity, str(score.facility_id)) == ("HIGH", items["overdue"]["facility_id"])
    assert score.evidence["final_score"] == 0.71 and "0.7100" in score.trigger_reason
    assert {a.severity for a in by_rule["OVERDUE_DAYS"]} == {"HIGH"}
    assert sorted(a.severity for a in by_rule["LIFECYCLE_ANOMALY"]) == ["CRITICAL", "HIGH"]
    assert str(by_rule["REPAYMENT_ANOMALY"][0].facility_id) == items["healthy"]["facility_id"]
    assert all(a.status == "OPEN" and a.owner_user_id is None and a.version == 1 for a in alerts)
    assert services["detect"].scan()["created"] == 0
    with session_factory() as session:
        created_events = session.scalar(
            select(func.count()).select_from(LedgerEventModel).where(
                LedgerEventModel.event_type == "RISK_ALERT_CREATED"
            )
        )
    assert created_events == len(alerts)
    with pytest.raises(RiskOpsForbidden):
        services["detect"].scan_as(users["auditor"])


def test_rule_changes_are_versioned_and_drive_detection(session_factory, portfolio):
    users, facilities, _ = portfolio
    services = _services(session_factory, facilities)
    with pytest.raises(RiskOpsForbidden):
        services["rules"].change_rule(
            "MODEL_SCORE_THRESHOLD", users["risk"], threshold=Decimal("0.3"), severity="HIGH",
            enabled=True, effective_from=None, change_reason="lower it", expected_version=1,
        )
    changed = services["rules"].change_rule(
        "MODEL_SCORE_THRESHOLD", users["admin"], threshold=Decimal("0.3000"), severity="MEDIUM",
        enabled=True, effective_from=None, change_reason="Portfolio review lowered threshold",
        expected_version=1,
    )
    assert (changed["version"], changed["threshold"], changed["created_by"]) == (2, "0.3000", "admin.demo")
    with pytest.raises(RiskOpsConflict, match="changed since"):
        services["rules"].change_rule(
            "MODEL_SCORE_THRESHOLD", users["admin"], threshold=Decimal("0.5"), severity="HIGH",
            enabled=True, effective_from=None, change_reason="stale", expected_version=1,
        )
    services["rules"].change_rule(
        "REPAYMENT_ANOMALY", users["admin"], threshold=Decimal("2"), severity="MEDIUM", enabled=False,
        effective_from=None, change_reason="Pause during migration", expected_version=1,
    )
    future = services["rules"].change_rule(
        "OVERDUE_DAYS", users["admin"], threshold=Decimal("60"), severity="HIGH", enabled=True,
        effective_from=NOW + timedelta(days=30), change_reason="From next quarter", expected_version=1,
    )
    listed = services["rules"].list_rules(users["auditor"])
    current = {rule["rule_key"]: rule for rule in listed["rules"]}
    assert current["MODEL_SCORE_THRESHOLD"]["version"] == 2
    assert current["OVERDUE_DAYS"]["version"] == 1  # v2 is not yet in effect
    assert [rule["version"] for rule in listed["pending"]] == [future["version"]]
    assert len(listed["history"]) == 5 + 3
    services["detect"].scan()
    with session_factory() as session:
        rules = {(a.rule_key, a.rule_version) for a in session.scalars(select(RiskAlertModel))}
        with pytest.raises(DBAPIError):
            with session_factory.begin() as edit:
                edit.execute(text("UPDATE risk_rules SET threshold = 0.1"))
        assert session.scalar(select(func.count()).select_from(RiskRuleModel)) == 8
    # 0.35 and 0.71 both cross the new 0.30 threshold; the paused rule raised nothing.
    assert ("MODEL_SCORE_THRESHOLD", 2) in rules
    assert not any(key == "REPAYMENT_ANOMALY" for key, _ in rules)


# --- 3. Alert state transitions and 6. audit ----------------------------------------------


def _first_alert(session_factory, rule_key="OVERDUE_DAYS"):
    with session_factory() as session:
        return session.scalar(
            select(RiskAlertModel).where(RiskAlertModel.rule_key == rule_key).order_by(RiskAlertModel.created_at)
        )


def test_alert_lifecycle_is_governed_and_fully_audited(session_factory, portfolio):
    users, facilities, _ = portfolio
    services = _services(session_factory, facilities)
    services["detect"].scan()
    alert = _first_alert(session_factory)
    alerts = services["alerts"]
    aid = str(alert.alert_id)
    with pytest.raises(RiskOpsConflict, match="cannot move"):
        alerts.start(aid, users["admin"], version=1, comment=None)
    with pytest.raises(RiskOpsForbidden):
        alerts.assign(aid, users["auditor"], owner_user_id=str(users["risk"].user_id), version=1, comment=None)
    with pytest.raises(RiskOpsConflict, match="owner must be"):
        alerts.assign(aid, users["risk"], owner_user_id=str(users["auditor"].user_id), version=1, comment=None)
    detail = alerts.assign(aid, users["risk"], owner_user_id=str(users["risk"].user_id), version=1,
                           comment="I take it")
    assert (detail["status"], detail["owner"], detail["version"]) == ("ASSIGNED", "risk.demo", 2)
    with pytest.raises(RiskOpsConflict, match="changed since"):
        alerts.start(aid, users["risk"], version=1, comment=None)
    with pytest.raises(RiskOpsForbidden, match="owner"):
        alerts.start(aid, users["auditor"], version=2, comment=None)
    with pytest.raises(RiskOpsForbidden):
        alerts.start(aid, users["financier"], version=2, comment=None)
    detail = alerts.start(aid, users["risk"], version=2, comment="Calling the supplier")
    detail = alerts.comment(aid, users["auditor"], version=3, comment="Please attach the call log")
    detail = alerts.resolve(aid, users["risk"], version=4, resolution="Supplier paid arrears on 2025-06-03")
    assert detail["status"] == "RESOLVED" and detail["resolved_at"]
    with pytest.raises(RiskOpsForbidden):
        alerts.close(aid, users["risk"], version=5, comment="self close")
    detail = alerts.reopen(aid, users["auditor"], version=5, comment="Bank statement missing")
    assert (detail["status"], detail["resolution"]) == ("PROCESSING", None)
    detail = alerts.resolve(aid, users["risk"], version=6, resolution="Bank statement attached")
    detail = alerts.close(aid, users["auditor"], version=7, comment="Evidence verified")
    assert (detail["status"], detail["version"]) == ("CLOSED", 8)
    assert [(e["action"], e["from_status"], e["to_status"], e["actor"], e["actor_role"]) for e in detail["events"]] == [
        ("CREATED", None, "OPEN", "system", "system"),
        ("ASSIGNED", "OPEN", "ASSIGNED", "risk.demo", "risk_manager"),
        ("STARTED", "ASSIGNED", "PROCESSING", "risk.demo", "risk_manager"),
        ("COMMENTED", "PROCESSING", "PROCESSING", "auditor.demo", "auditor"),
        ("RESOLVED", "PROCESSING", "RESOLVED", "risk.demo", "risk_manager"),
        ("REOPENED", "RESOLVED", "PROCESSING", "auditor.demo", "auditor"),
        ("RESOLVED", "PROCESSING", "RESOLVED", "risk.demo", "risk_manager"),
        ("CLOSED", "RESOLVED", "CLOSED", "auditor.demo", "auditor"),
    ]
    assert all(e["recorded_at"] for e in detail["events"])
    assert detail["events"][5]["payload"]["rejected_resolution"] == "Supplier paid arrears on 2025-06-03"
    with session_factory() as session:
        transitioned = session.scalar(
            select(func.count()).select_from(LedgerEventModel).where(
                LedgerEventModel.event_type == "RISK_ALERT_TRANSITIONED",
                LedgerEventModel.entity_id == alert.alert_id,
            )
        )
    assert transitioned == 7
    # The database refuses illegal jumps, unaudited changes, identity edits and deletes.
    for statement in (
        "UPDATE risk_alerts SET status = 'OPEN', owner_user_id = NULL, resolution = NULL, "
        "resolved_at = NULL, closed_at = NULL, version = version + 1 WHERE alert_id = :id",
        "UPDATE risk_alerts SET severity = 'LOW', version = version + 1 WHERE alert_id = :id",
        "DELETE FROM risk_alerts WHERE alert_id = :id",
        "UPDATE risk_alert_events SET comment = 'x' WHERE alert_id = :id",
    ):
        with pytest.raises(DBAPIError):
            with session_factory.begin() as session:
                session.execute(text(statement), {"id": alert.alert_id})
    other = _first_alert(session_factory, "LIFECYCLE_ANOMALY")
    with pytest.raises(DBAPIError, match="no audit event"):
        with session_factory.begin() as session:
            session.execute(
                text(
                    "UPDATE risk_alerts SET status = 'ASSIGNED', owner_user_id = :u, "
                    "version = version + 1 WHERE alert_id = :id"
                ),
                {"u": users["risk"].user_id, "id": other.alert_id},
            )


# --- 4. Tasks -------------------------------------------------------------------------------


def test_tasks_are_assigned_worked_and_closed_with_results(session_factory, portfolio):
    users, facilities, items = portfolio
    services = _services(session_factory, facilities)
    services["detect"].scan()
    alert = _first_alert(session_factory, "LIFECYCLE_ANOMALY")
    tasks = services["tasks"]
    with pytest.raises(RiskOpsForbidden):
        tasks.create(users["supplier"], title="x", task_type="OTHER", description="x",
                     assignee_user_id=str(users["risk"].user_id), due_at=NOW)
    with pytest.raises(RiskOpsConflict, match="Tasks go to"):
        tasks.create(users["risk"], title="Collect", task_type="COLLECTION", description="d",
                     assignee_user_id=str(users["supplier"].user_id), due_at=NOW)
    task = tasks.create(
        users["risk"], title="Review disposal file", task_type="DISPOSAL_REVIEW",
        description="Check the recovery evidence before write-off", alert_id=str(alert.alert_id),
        assignee_user_id=str(users["auditor"].user_id), due_at=NOW + timedelta(days=12),
    )
    assert task["facility_id"] == str(alert.facility_id)
    assert (task["status"], task["assignee"], task["created_by"], task["overdue"]) == (
        "OPEN", "auditor.demo", "risk.demo", False,
    )
    tid = task["task_id"]
    assert [t["task_id"] for t in tasks.list_tasks(users["auditor"], view="mine")] == [tid]
    assert tasks.list_tasks(users["risk"], view="mine") == []
    assert [t["task_id"] for t in tasks.list_tasks(users["risk"], view="pending")] == [tid]
    with pytest.raises(RiskOpsForbidden, match="assignee"):
        tasks.start(tid, users["risk"], version=1)
    task = tasks.set_due(tid, users["risk"], version=1, due_at=NOW + timedelta(days=9), comment="Sooner")
    task = tasks.add_note(tid, users["auditor"], version=2, note="Requested bank statements")
    task = tasks.start(tid, users["auditor"], version=3)
    task = tasks.attach_result(
        tid, users["auditor"], version=4, filename="review.txt", content_type="text/plain",
        content=b"recovery evidence verified",
    )
    attachment = task["attachments"][0]
    assert attachment["size_bytes"] == 26 and len(attachment["sha256"]) == 64
    assert tasks.attachment(tid, attachment["attachment_id"], users["risk"])[2] == b"recovery evidence verified"
    with pytest.raises(RiskOpsConflict):
        tasks.attach_result(tid, users["auditor"], version=5, filename="big.bin",
                            content_type="application/octet-stream", content=b"x" * (2 * 1024 * 1024 + 1))
    task = tasks.complete(tid, users["auditor"], version=5, result_summary="Recovery evidence complete")
    assert (task["status"], task["result_summary"]) == ("COMPLETED", "Recovery evidence complete")
    assert [t["task_id"] for t in tasks.list_tasks(users["auditor"], view="completed")] == [tid]
    assert [e["action"] for e in task["events"]] == [
        "CREATED", "DUE_CHANGED", "NOTE_ADDED", "STARTED", "RESULT_ATTACHED", "COMPLETED",
    ]
    assert services["alerts"].get_alert(str(alert.alert_id), users["risk"])["tasks"][0]["status"] == "COMPLETED"
    with pytest.raises(RiskOpsConflict):
        tasks.cancel(tid, users["risk"], version=6, comment="too late")

    second = tasks.create(
        users["admin"], title="Chase payment", task_type="COLLECTION", description="Call supplier",
        facility_id=items["overdue"]["facility_id"], assignee_user_id=str(users["risk"].user_id),
        due_at=NOW - timedelta(days=1),
    )
    assert second["overdue"] is True
    second = tasks.reassign(second["task_id"], users["admin"], version=1,
                            assignee_user_id=str(users["auditor"].user_id), comment="Rebalance")
    second = tasks.cancel(second["task_id"], users["admin"], version=2, comment="Duplicate")
    assert second["status"] == "CANCELLED"
    with session_factory() as session:
        recorded = session.scalar(
            select(func.count()).select_from(LedgerEventModel).where(LedgerEventModel.event_type == "RISK_TASK_RECORDED")
        )
        events = session.scalar(select(func.count()).select_from(RiskTaskEventModel))
    assert recorded == events == 9
    with pytest.raises(DBAPIError):
        with session_factory.begin() as session:
            session.execute(text("UPDATE risk_tasks SET status = 'OPEN', version = version + 1"))


# --- 5. Permissions and data boundaries -------------------------------------------------


def test_access_is_limited_by_role_and_organization(session_factory, portfolio):
    users, facilities, items = portfolio
    services = _services(session_factory, facilities)
    services["detect"].scan()
    other_org, other_user = uuid.uuid4(), uuid.uuid4()
    with session_factory.begin() as session:
        session.add(OrganizationModel(organization_id=other_org, organization_code="BANK-002",
                                      name="Other bank", organization_type="financier", created_at=NOW))
        session.flush()
        template = session.get(UserModel, users["risk"].user_id)
        session.add(UserModel(user_id=other_user, username="risk.other", display_name="Other risk",
                              role="risk_manager", organization_id=other_org,
                              password_hash=template.password_hash, password_salt=template.password_salt,
                              is_active=True, created_at=NOW))
    outsider = AuthenticatedUser(
        user_id=other_user, username="risk.other", display_name="Other risk", role="risk_manager",
        organization_id=other_org, organization_code="BANK-002", organization_name="Other bank",
    )
    assert services["insight"].dashboard(outsider)["assets"]["financing_count"] == 0
    assert services["alerts"].list_alerts(outsider) == []
    some_alert = services["alerts"].list_alerts(users["risk"])[0]["alert_id"]
    with pytest.raises(RiskOpsNotFound):
        services["alerts"].get_alert(some_alert, outsider)
    with pytest.raises(RiskOpsNotFound):
        services["insight"].facility_detail(items["overdue"]["facility_id"], outsider)
    assert services["insight"].facilities(outsider) == []

    for role in ("supplier", "core", "financier"):
        with pytest.raises(RiskOpsForbidden):
            services["alerts"].list_alerts(users[role])
    for role in ("supplier", "core", "financier"):
        with pytest.raises(RiskOpsForbidden):
            services["tasks"].list_tasks(users[role])
    with pytest.raises(RiskOpsForbidden):
        services["rules"].list_rules(users["supplier"])
    # An enterprise sees its own financing, without internal risk operations.
    own = services["insight"].facility_detail(items["overdue"]["facility_id"], users["supplier"])
    assert own["facility"]["facility_id"] == items["overdue"]["facility_id"]
    assert own["model"] is None and own["alerts"] is None and own["tasks"] is None
    assert {row["source"] for row in own["audit"]} == {"lifecycle"}
    assert len(services["insight"].facilities(users["supplier"])) == 4
    # Administrators and auditors see every organization.
    assert len(services["alerts"].list_alerts(users["admin"])) == len(services["alerts"].list_alerts(users["auditor"]))


# --- 7. Risk detail --------------------------------------------------------------------------


def test_risk_detail_assembles_every_view_of_a_facility(session_factory, portfolio):
    users, facilities, items = portfolio
    services = _services(session_factory, facilities)
    services["detect"].scan()
    detail = services["insight"].facility_detail(items["overdue"]["facility_id"], users["risk"])
    assert detail["enterprise"]["supplier"]["code"] == "SUPPLIER-001"
    assert detail["enterprise"]["core_enterprise"]["code"] == "CORE-001"
    assert detail["financing"]["principal"] == "1000.00" and detail["financing"]["status"] == "overdue"
    assert detail["risk"]["risk_class"] == "HIGH_RISK"
    assert (detail["risk"]["current_band"], detail["risk"]["current_score"]) == ("high", 0.71)
    assert [row["final_score"] for row in detail["risk"]["history"]] == [0.35, 0.71]
    assert detail["risk"]["trend"] == {"delta": 0.36, "direction": "up"}
    assert detail["model"]["engine_version"] == "transparent_logistic_baseline_v0.1"
    assert {"model_version", "artifact_hash", "dataset_snapshot_id"} <= set(detail["model"])
    assert {a["risk_type"] for a in detail["alerts"]} == {"MODEL_SCORE", "OVERDUE"}

    lost = services["insight"].facility_detail(items["lost"]["facility_id"], users["admin"])
    stages = [entry["stage"] for entry in lost["timeline"]]
    for stage in ("disbursement", "overdue", "disposal", "default", "recovery", "write_off"):
        assert stage in stages, stage
    assert stages.index("disbursement") < stages.index("disposal") < stages.index("write_off")
    assert all(entry["time"] for entry in lost["timeline"])
    assert any(row["source"] == "alert" and row["actor"] == "system" for row in lost["audit"])
    assert any(row["source"] == "ledger" for row in lost["audit"])
    assert lost["risk"]["risk_class"] == "DEFAULTED"


# --- 8. Restart recovery ------------------------------------------------------------------


def test_operations_state_survives_a_restart(session_factory, portfolio):
    users, facilities, _ = portfolio
    services = _services(session_factory, facilities)
    services["detect"].scan()
    alert = _first_alert(session_factory)
    services["alerts"].assign(str(alert.alert_id), users["risk"], owner_user_id=str(users["risk"].user_id),
                              version=1, comment=None)
    services["tasks"].create(users["risk"], title="Follow up", task_type="INVESTIGATION", description="d",
                             assignee_user_id=str(users["risk"].user_id), due_at=NOW + timedelta(days=3))
    before = (
        services["insight"].dashboard(users["admin"]),
        services["alerts"].list_alerts(users["admin"]),
        services["tasks"].list_tasks(users["admin"], view="all"),
        services["rules"].list_rules(users["admin"]),
    )
    restarted = _services(session_factory, FacilityService(session_factory))
    assert restarted["detect"].scan()["created"] == 0
    after = (
        restarted["insight"].dashboard(users["admin"]),
        restarted["alerts"].list_alerts(users["admin"]),
        restarted["tasks"].list_tasks(users["admin"], view="all"),
        restarted["rules"].list_rules(users["admin"]),
    )
    assert after == before


# --- 9. API contract ----------------------------------------------------------------------


def _login(client, username):
    client.cookies.clear()
    assert client.post("/api/v1/auth/login", json={"username": username, "password": "Demo123!"}).status_code == 200


def test_risk_operations_api_contract(migrated_engine, session_factory, portfolio):
    from app.database import Database
    from app.main import create_app

    users, _, items = portfolio
    app = create_app(Database(migrated_engine, session_factory), calibration_worker_enabled=False)
    with TestClient(app) as client:
        for path in ("/api/v1/risk/dashboard", "/api/v1/risk/alerts", "/api/v1/risk/tasks", "/api/v1/risk/rules"):
            assert client.get(path).status_code == 401, path
        _login(client, "risk.demo")
        scan = client.post("/api/v1/risk/alerts/scan")
        assert scan.status_code == 200 and scan.json()["created"] == 6
        board = client.get("/api/v1/risk/dashboard").json()
        assert set(board) >= {"assets", "risk_distribution", "lifecycle", "losses", "model", "alerts", "tasks"}
        alerts = client.get("/api/v1/risk/alerts", params={"severity": "CRITICAL"}).json()
        assert [a["risk_type"] for a in alerts] == ["LIFECYCLE"]
        alert = alerts[0]
        assert set(alert) >= {"alert_id", "facility_id", "risk_type", "severity", "trigger_reason",
                              "created_at", "owner", "resolution", "status", "version"}
        assigned = client.post(f"/api/v1/risk/alerts/{alert['alert_id']}/assign",
                               json={"owner_user_id": str(users["risk"].user_id), "version": 1})
        assert assigned.status_code == 200 and assigned.json()["status"] == "ASSIGNED"
        stale = client.post(f"/api/v1/risk/alerts/{alert['alert_id']}/start", json={"version": 1})
        assert stale.status_code == 409
        bad = client.post(f"/api/v1/risk/alerts/{alert['alert_id']}/resolve", json={"version": 2})
        assert bad.status_code == 422
        assignees = client.get("/api/v1/risk/assignees").json()
        assert {row["role"] for row in assignees} == {"admin", "auditor", "risk_manager"}
        created = client.post("/api/v1/risk/tasks", json={
            "title": "Collect arrears", "task_type": "COLLECTION", "description": "Call and document",
            "assignee_user_id": str(users["risk"].user_id), "due_at": "2025-06-10T00:00:00+00:00",
            "facility_id": items["overdue"]["facility_id"],
        })
        assert created.status_code == 201, created.text
        tid = created.json()["task_id"]
        assert client.post(f"/api/v1/risk/tasks/{tid}/start", json={"version": 1}).status_code == 200
        upload = client.post(f"/api/v1/risk/tasks/{tid}/result", json={
            "version": 2, "filename": "call.txt", "content_type": "text/plain",
            "content_base64": base64.b64encode(b"called supplier").decode(),
        })
        assert upload.status_code == 200, upload.text
        attachment = upload.json()["attachments"][0]
        download = client.get(f"/api/v1/risk/tasks/{tid}/attachments/{attachment['attachment_id']}")
        assert download.content == b"called supplier"
        assert client.post(f"/api/v1/risk/tasks/{tid}/result", json={
            "version": 3, "filename": "x", "content_base64": "not base64!"}).status_code == 422
        done = client.post(f"/api/v1/risk/tasks/{tid}/complete", json={"version": 3, "result_summary": "Paid"})
        assert done.json()["status"] == "COMPLETED"
        assert [t["task_id"] for t in client.get("/api/v1/risk/tasks", params={"view": "completed"}).json()] == [tid]
        detail = client.get(f"/api/v1/risk/facilities/{items['overdue']['facility_id']}").json()
        assert set(detail) == {"facility", "enterprise", "financing", "risk", "model", "timeline", "audit",
                               "alerts", "tasks"}
        assert client.get("/api/v1/risk/rules").status_code == 200
        assert client.post("/api/v1/risk/rules/OVERDUE_DAYS/versions", json={
            "expected_version": 1, "threshold": "45", "severity": "HIGH", "enabled": True,
            "change_reason": "try"}).status_code == 403
        _login(client, "admin.demo")
        changed = client.post("/api/v1/risk/rules/OVERDUE_DAYS/versions", json={
            "expected_version": 1, "threshold": "45", "severity": "HIGH", "enabled": True,
            "change_reason": "Align with credit policy"})
        assert changed.status_code == 201 and changed.json()["version"] == 2
        assert client.get(f"/api/v1/risk/alerts/{uuid.uuid4()}").status_code == 404
        _login(client, "supplier.demo")
        for path in ("/api/v1/risk/dashboard", "/api/v1/risk/alerts", "/api/v1/risk/tasks", "/api/v1/risk/rules"):
            assert client.get(path).status_code == 403, path
        own = client.get(f"/api/v1/risk/facilities/{items['overdue']['facility_id']}")
        assert own.status_code == 200 and own.json()["alerts"] is None
        _login(client, "auditor.demo")
        with migrated_engine.connect() as connection:
            assert connection.scalar(select(func.count()).select_from(RiskAlertEventModel)) == 7
        verify = client.get("/api/ledger/verify")
        assert verify.status_code == 200 and verify.json()["valid"] is True
