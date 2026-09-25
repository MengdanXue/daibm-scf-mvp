"""Phase 4 enterprise readiness: tenancy, permissions, security, ops, config, deployment."""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.config import PostgresSettings
from app.database import Database
from app.identity import AccountLocked, AuthenticationRequired, InvalidCredentials
from app.models import LedgerEventModel
from app.models_enterprise import AuditGrantModel, SecurityEventModel, SystemConfigModel
from app.models_facility import FinancingFacilityModel
from app.ops.backup import BackupError, create_backup, fingerprint, restore_backup, verify_backup
from app.ops.metrics import Heartbeats, RequestMetrics
from app.ops.service import OpsService
from app.services.config_center import ConfigConflict, ConfigService
from app.services.facility import FacilityService
from app.services.identity import IdentityService
from app.services.organizations import OrganizationService
from app.services.permissions import PERMISSIONS, PermissionDenied, PermissionService
from app.services.risk_operations import RiskAlertService, RiskDetectionService, RiskOpsNotFound
from app.services.risk_tasks import RiskTaskService
from app.services.security import SecuritySettings, WeakPassword, check_password_policy
from test_facility_service import _activate, _approved_application, _mark_overdue

ROOT = Path(__file__).resolve().parents[1]
STRONG = "Tenant-B-Pass-2026!"
NOW = datetime(2025, 6, 1, tzinfo=timezone.utc)


def _demo_users(session_factory):
    identity = IdentityService(session_factory)
    identity.seed_demo_accounts()
    return {
        name: identity.login(f"{name}.demo", "Demo123!").user
        for name in ("supplier", "core", "financier", "risk", "auditor", "admin")
    }


@pytest.fixture
def tenants(session_factory):
    """Bank A (demo BANK-001) and a second bank B with its own supplier, each owning a facility."""

    users = _demo_users(session_factory)
    admin = users["admin"]
    organizations = OrganizationService(session_factory)
    bank_b = organizations.create_organization(admin, code="BANK-B", name="Bank B", organization_type="financier")
    supplier_b = organizations.create_organization(
        admin, code="SUP-B", name="Supplier B", organization_type="supplier"
    )
    for username, role, organization in (
        ("fin.b", "financier", bank_b),
        ("risk.b", "risk_manager", bank_b),
        ("sup.b", "supplier", supplier_b),
    ):
        organizations.create_user(
            admin, username=username, display_name=username, role=role,
            organization_id=organization["organization_id"], password=STRONG,
        )
    identity = IdentityService(session_factory)
    b_users = {name: identity.login(name, STRONG).user for name in ("fin.b", "risk.b", "sup.b")}

    facilities = FacilityService(session_factory)
    a_map = {f"{name}.demo": user for name, user in users.items()}
    b_map = {**a_map, "financier.demo": b_users["fin.b"], "supplier.demo": b_users["sup.b"]}
    facility_a = _mark_overdue(facilities, a_map, _activate(facilities, a_map, _approved_application(session_factory, a_map)))
    facility_b = _mark_overdue(facilities, b_map, _activate(facilities, b_map, _approved_application(session_factory, b_map)))
    return {
        "users": users,
        "b": b_users,
        "facilities": facilities,
        "a": facility_a,
        "b_facility": facility_b,
        "orgs": {"bank_b": bank_b, "supplier_b": supplier_b},
    }


def _scan(session_factory):
    RiskDetectionService(session_factory, clock=lambda: NOW + timedelta(days=60)).scan()
    return RiskAlertService(session_factory)


def _app(migrated_engine, session_factory, **kwargs):
    from app.main import create_app

    return create_app(Database(migrated_engine, session_factory), calibration_worker_enabled=False, **kwargs)


def _login(client, username, password="Demo123!"):
    response = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response


# --- 1-4: organization isolation ---------------------------------------------------------


def test_every_facility_carries_its_owning_organization_and_the_owner_is_immutable(tenants, session_factory):
    with session_factory() as session:
        owners = {
            facility_id: organization_id
            for facility_id, organization_id in session.execute(
                select(FinancingFacilityModel.facility_id, FinancingFacilityModel.organization_id)
            )
        }
    assert owners[uuid.UUID(tenants["a"]["facility_id"])] == tenants["users"]["financier"].organization_id
    assert owners[uuid.UUID(tenants["b_facility"]["facility_id"])] == tenants["b"]["fin.b"].organization_id
    with pytest.raises(DBAPIError, match="owner"):
        with session_factory.begin() as session:
            session.execute(
                text("UPDATE financing_facilities SET organization_id = :org WHERE facility_id = :id"),
                {"org": tenants["b"]["fin.b"].organization_id, "id": tenants["a"]["facility_id"]},
            )


def test_organization_a_cannot_read_or_command_organization_b_facility(tenants):
    facilities = tenants["facilities"]
    fin_a, fin_b = tenants["users"]["financier"], tenants["b"]["fin.b"]
    a_id, b_id = tenants["a"]["facility_id"], tenants["b_facility"]["facility_id"]

    assert {f["facility_id"] for f in facilities.list_for_user(fin_a)} == {a_id}
    assert {f["facility_id"] for f in facilities.list_for_user(fin_b)} == {b_id}
    with pytest.raises(Exception) as denied:
        facilities.get(b_id, fin_a)
    assert "not found" in str(denied.value).lower() or type(denied.value).__name__.endswith("NotFound")
    # Enterprise users see only facilities financing their own organization.
    assert {f["facility_id"] for f in facilities.list_for_user(tenants["users"]["supplier"])} == {a_id}
    assert {f["facility_id"] for f in facilities.list_for_user(tenants["b"]["sup.b"])} == {b_id}


def test_organization_a_cannot_view_organization_b_alerts_or_task_them(tenants, session_factory):
    alerts = _scan(session_factory)
    risk_a, risk_b = tenants["users"]["risk"], tenants["b"]["risk.b"]
    a_alerts = alerts.list_alerts(risk_a)
    b_alerts = alerts.list_alerts(risk_b)
    assert a_alerts and b_alerts
    assert {a["facility_id"] for a in a_alerts} == {tenants["a"]["facility_id"]}
    assert {a["facility_id"] for a in b_alerts} == {tenants["b_facility"]["facility_id"]}
    with pytest.raises(RiskOpsNotFound):
        alerts.get_alert(b_alerts[0]["alert_id"], risk_a)
    with pytest.raises(Exception):
        RiskTaskService(session_factory).create(
            risk_a, title="cross-tenant", task_type="REVIEW", description="x",
            assignee_user_id=str(risk_a.user_id), due_at=datetime.now(timezone.utc) + timedelta(days=1),
            alert_id=b_alerts[0]["alert_id"],
        )


def test_admin_sees_every_organization(tenants, session_factory):
    alerts = _scan(session_factory)
    admin = tenants["users"]["admin"]
    ids = {tenants["a"]["facility_id"], tenants["b_facility"]["facility_id"]}
    assert {f["facility_id"] for f in tenants["facilities"].list_for_user(admin)} == ids
    assert {a["facility_id"] for a in alerts.list_alerts(admin)} == ids
    with session_factory() as session:
        assert PermissionService.scope(session, admin).everything


def test_auditor_scope_follows_audit_grants(tenants, session_factory):
    auditor, admin = tenants["users"]["auditor"], tenants["users"]["admin"]
    facilities = tenants["facilities"]
    # Demo grants cover the demo bank only; bank B is outside the auditor's scope.
    assert {f["facility_id"] for f in facilities.list_for_user(auditor)} == {tenants["a"]["facility_id"]}
    organizations = OrganizationService(session_factory)
    grant = organizations.grant_audit(
        admin, auditor_user_id=str(auditor.user_id),
        organization_id=tenants["orgs"]["bank_b"]["organization_id"], reason="annual audit",
    )
    assert len(facilities.list_for_user(auditor)) == 2
    organizations.revoke_audit(admin, grant["grant_id"], reason="audit finished")
    assert len(facilities.list_for_user(auditor)) == 1
    with pytest.raises(DBAPIError):
        with session_factory.begin() as session:
            session.execute(text("DELETE FROM audit_grants"))


def test_isolation_holds_for_api_and_page_data_with_tampered_ids(tenants, migrated_engine, session_factory):
    _scan(session_factory)
    b_id = tenants["b_facility"]["facility_id"]
    with TestClient(_app(migrated_engine, session_factory)) as client:
        _login(client, "risk.demo")
        # API: changing the id parameter never reveals another organization's data.
        assert client.get(f"/api/v1/facilities/{b_id}").status_code == 404
        assert client.get(f"/api/v1/risk/facilities/{b_id}").status_code == 404
        b_alert = RiskAlertService(session_factory).list_alerts(tenants["b"]["risk.b"])[0]
        assert client.get(f"/api/v1/risk/alerts/{b_alert['alert_id']}").status_code == 404
        assert client.post(
            f"/api/v1/risk/alerts/{b_alert['alert_id']}/comments",
            json={"version": b_alert["version"], "comment": "tamper"},
        ).status_code == 404
        # Pages: the lists and the dashboard that render the pages contain only org A.
        listed = client.get("/api/v1/facilities").json()
        assert {f["facility_id"] for f in listed} == {tenants["a"]["facility_id"]}
        page_alerts = client.get("/api/v1/risk/alerts").json()
        assert all(a["facility_id"] != b_id for a in page_alerts)
        dashboard = client.get("/api/v1/risk/dashboard").json()
        assert dashboard["assets"]["financing_count"] == 1
        risk_list = client.get("/api/v1/risk/facilities").json()
        assert b_id not in json.dumps(risk_list)


# --- 5-6: unauthorized roles and audited denials -------------------------------------


def test_permission_service_is_the_single_role_table():
    assert set(PERMISSIONS["config:write"]) == {"admin"}
    assert "supplier" not in PERMISSIONS["alert:read"]
    source = (ROOT / "app" / "services" / "risk_operations.py").read_text(encoding="utf-8")
    assert "PermissionService" in source
    for module in ("risk_operations", "risk_tasks", "risk_insight", "outcome_governance", "model_registry", "facility"):
        assert "PermissionService" in (ROOT / "app" / "services" / f"{module}.py").read_text(encoding="utf-8"), module


def test_unauthorized_roles_are_rejected_and_every_denial_is_audited(tenants, migrated_engine, session_factory):
    with TestClient(_app(migrated_engine, session_factory)) as client:
        _login(client, "supplier.demo")
        assert client.get("/api/v1/admin/organizations").status_code == 403
        assert client.get("/api/v1/risk/alerts").status_code == 403
        assert client.get("/api/v1/ops/metrics").status_code == 403
        client.post("/api/v1/auth/logout")
        _login(client, "auditor.demo")
        assert client.get("/api/v1/admin/config").status_code == 200
        assert client.post(
            "/api/v1/admin/config/security.login_max_failures",
            json={"value": 3, "reason": "tighten", "expected_version": 0},
        ).status_code == 403
        assert client.post(
            "/api/v1/admin/organizations", json={"code": "EVIL-1", "name": "Evil", "organization_type": "supplier"}
        ).status_code == 403
    with session_factory() as session:
        denials = list(
            session.scalars(select(SecurityEventModel).where(SecurityEventModel.event_type == "PERMISSION_DENIED"))
        )
    assert len(denials) == 5
    assert {d.username for d in denials} == {"supplier.demo", "auditor.demo"}
    assert any(d.action == "GET /api/v1/admin/organizations" for d in denials)
    with pytest.raises(DBAPIError):
        with session_factory.begin() as session:
            session.execute(text("UPDATE security_events SET username = 'nobody'"))


def test_admin_actions_are_audited_in_security_events_and_the_ledger(tenants, session_factory):
    with session_factory() as session:
        admin_events = list(
            session.scalars(select(SecurityEventModel).where(SecurityEventModel.event_type == "ADMIN_ACTION"))
        )
        ledger = list(
            session.scalars(select(LedgerEventModel).where(LedgerEventModel.event_type == "ADMIN_ACTION_RECORDED"))
        )
    actions = [event.action for event in admin_events]
    assert actions.count("organization_created") == 2 and actions.count("user_created") == 3
    assert len(ledger) == len(admin_events)


def test_non_admin_cannot_use_administration_services(tenants, session_factory):
    organizations = OrganizationService(session_factory)
    for user in (tenants["users"]["financier"], tenants["users"]["auditor"], tenants["b"]["risk.b"]):
        with pytest.raises(PermissionDenied):
            organizations.create_organization(user, code="NOPE-1", name="Nope", organization_type="supplier")
    with pytest.raises(PermissionDenied):
        ConfigService(session_factory).set_value(
            tenants["users"]["risk"], "security.lockout_minutes", value=5, reason="x" * 3, expected_version=0
        )


# --- 7-8: login lock-out and session expiry --------------------------------------------


def test_login_failure_limit_locks_the_account_until_it_expires(session_factory):
    _demo_users(session_factory)
    clock = {"now": NOW}
    identity = IdentityService(
        session_factory, clock=lambda: clock["now"],
        settings_provider=lambda: SecuritySettings(login_max_failures=3, lockout_minutes=15),
    )
    for _ in range(2):
        with pytest.raises(InvalidCredentials):
            identity.login("risk.demo", "wrong-password")
    with pytest.raises(AccountLocked):
        identity.login("risk.demo", "wrong-password")
    # Even the right password is refused while locked.
    with pytest.raises(AccountLocked):
        identity.login("risk.demo", "Demo123!")
    clock["now"] = NOW + timedelta(minutes=16)
    assert identity.login("risk.demo", "Demo123!").user.username == "risk.demo"
    with session_factory() as session:
        types = [e.event_type for e in session.scalars(
            select(SecurityEventModel).where(SecurityEventModel.username == "risk.demo").order_by(SecurityEventModel.event_id)
        )]
    assert types.count("LOGIN_FAILURE") >= 2 and "LOGIN_LOCKED" in types and types[-1] == "LOGIN_SUCCESS"


def test_locked_account_returns_423_over_http(migrated_engine, session_factory, monkeypatch):
    monkeypatch.setenv("DAIBM_LOGIN_MAX_FAILURES", "2")
    with TestClient(_app(migrated_engine, session_factory)) as client:
        for _ in range(2):
            client.post("/api/v1/auth/login", json={"username": "core.demo", "password": "bad-password"})
        response = client.post("/api/v1/auth/login", json={"username": "core.demo", "password": "Demo123!"})
    assert response.status_code == 423
    assert response.json()["detail"]["code"] == "account_locked"


def test_sessions_expire_after_idle_timeout_and_absolute_lifetime(session_factory):
    _demo_users(session_factory)
    clock = {"now": NOW}
    identity = IdentityService(
        session_factory, clock=lambda: clock["now"],
        settings_provider=lambda: SecuritySettings(session_idle_minutes=30, session_absolute_hours=2),
    )
    token = identity.login("financier.demo", "Demo123!").token
    # Activity keeps an idle session alive ...
    for minutes in (20, 40, 60, 80, 100):
        clock["now"] = NOW + timedelta(minutes=minutes)
        identity.authenticate(token)
    # ... but never beyond the absolute lifetime.
    clock["now"] = NOW + timedelta(minutes=121)
    with pytest.raises(AuthenticationRequired):
        identity.authenticate(token)

    clock["now"] = NOW
    idle = identity.login("financier.demo", "Demo123!").token
    clock["now"] = NOW + timedelta(minutes=31)
    with pytest.raises(AuthenticationRequired):
        identity.authenticate(idle)
    with session_factory() as session:
        expired = session.scalars(
            select(SecurityEventModel).where(SecurityEventModel.event_type == "SESSION_EXPIRED")
        ).all()
    assert {e.detail["reason"] for e in expired} == {"absolute", "idle"}


def test_session_cookie_is_http_only_strict_and_secure_when_configured(migrated_engine, session_factory, monkeypatch):
    monkeypatch.setenv("DAIBM_COOKIE_SECURE", "true")
    with TestClient(_app(migrated_engine, session_factory), base_url="https://testserver") as client:
        cookie = _login(client, "financier.demo").headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "secure" in cookie


def test_password_policy_and_password_change(session_factory):
    for weak in ("short1!", "alllowercase123!", "NoDigitsHere!!", "NoSymbols12345", "Admin.Demo-12345"):
        with pytest.raises(WeakPassword):
            check_password_policy(weak, username="admin.demo")
    check_password_policy(STRONG, username="fin.b")
    users = _demo_users(session_factory)
    identity = IdentityService(session_factory)
    with pytest.raises(WeakPassword):
        identity.change_password(users["risk"], "Demo123!", "weak")
    with pytest.raises(InvalidCredentials):
        identity.change_password(users["risk"], "not-the-password", STRONG)
    identity.change_password(users["risk"], "Demo123!", STRONG)
    with pytest.raises(InvalidCredentials):
        identity.login("risk.demo", "Demo123!")
    assert identity.login("risk.demo", STRONG).user.role == "risk_manager"


# --- 9: secrets come from the environment ------------------------------------------------


def test_no_fixed_passwords_or_secrets_in_application_code(session_factory, monkeypatch):
    offenders = []
    for path in list((ROOT / "app").rglob("*.py")) + list((ROOT / "app" / "static").glob("*")) + list(
        (ROOT / "scripts").rglob("*.py")
    ):
        content = path.read_text(encoding="utf-8", errors="ignore")
        if "Demo123!" in content or "daibm_demo_password" in content:
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert re.search(r"POSTGRES_PASSWORD: \$\{POSTGRES_PASSWORD:\?", compose)
    assert ":-daibm_demo_password" not in compose
    with pytest.raises(ValueError, match="POSTGRES_PASSWORD"):
        PostgresSettings.from_env({})
    # Without DAIBM_DEMO_PASSWORD no demo account can sign in at all.
    monkeypatch.delenv("DAIBM_DEMO_PASSWORD")
    IdentityService(session_factory).seed_demo_accounts()
    with session_factory() as session:
        assert session.execute(text("SELECT count(*) FROM users")).scalar_one() == 0
    assert ".env" in (ROOT / ".gitignore").read_text(encoding="utf-8").split()


def test_demo_password_must_satisfy_policy_in_production(monkeypatch):
    from app.services.security import demo_password_from_env

    monkeypatch.setenv("DAIBM_ENV", "production")
    monkeypatch.setenv("DAIBM_DEMO_PASSWORD", "Demo123!")
    with pytest.raises(WeakPassword):
        demo_password_from_env()
    monkeypatch.delenv("DAIBM_DEMO_PASSWORD")
    assert demo_password_from_env() is None


# --- 10: health and metrics ----------------------------------------------------------------


def test_health_reports_application_database_and_workers(migrated_engine, session_factory):
    with TestClient(_app(migrated_engine, session_factory)) as client:
        health = client.get("/api/v1/ops/health")
        assert health.status_code == 200
        body = health.json()
        assert body["status"] == "ok"
        assert body["database"]["ok"] and body["database"]["revision"] == body["database"]["expected_revision"]
        assert set(body["workers"]) == {"calibration_worker", "risk_monitor"}
        assert client.get("/metrics").status_code == 401
        _login(client, "admin.demo")
        metrics = client.get("/api/v1/ops/metrics").json()
        assert metrics["system"]["request_count"] >= 2
        assert {"facilities_by_status", "alerts_by_status", "tasks_by_status"} <= set(metrics["business"])
        text_metrics = client.get("/metrics").text
        assert "daibm_requests_total" in text_metrics and "daibm_worker_alive" in text_metrics


def test_health_is_503_when_the_database_is_unreachable():
    def broken():
        raise ConnectionError("database down")

    ops = OpsService(broken, metrics=RequestMetrics(), heartbeats=Heartbeats(), monitor_interval=lambda: 60)  # type: ignore[arg-type]
    assert ops.health()["status"] == "down"


def test_worker_heartbeats_mark_a_stalled_worker_degraded(session_factory):
    beats = Heartbeats()
    beats.enable("calibration_worker", True)
    beats.enable("risk_monitor", True)
    ops = OpsService(session_factory, metrics=RequestMetrics(), heartbeats=beats, monitor_interval=lambda: 60)
    assert ops.health()["status"] == "degraded"
    beats.beat("calibration_worker")
    beats.beat("risk_monitor")
    assert ops.health()["status"] == "ok"


# --- 11-12: backup and verified restore ------------------------------------------------------


def test_backup_restore_drill_is_hash_verified(tenants, session_factory, migrated_engine, isolated_postgres_engine, tmp_path):
    _scan(session_factory)
    artifacts = tmp_path / "artifacts"
    (artifacts / "run-1").mkdir(parents=True)
    (artifacts / "run-1" / "model.json").write_text('{"weights": [1, 2, 3]}', encoding="utf-8")

    backup = tmp_path / "backup"
    manifest = create_backup(migrated_engine, backup, artifacts_dir=artifacts)
    assert manifest["ledger_valid"] and manifest["row_counts"]["financing_facilities"] == 2
    assert {"database/financing_facilities.csv", "audit/ledger.jsonl", "audit/security_events.jsonl",
            "artifacts.tar"} <= set(manifest["files"])
    assert verify_backup(backup)["revision"] == manifest["revision"]

    restored_artifacts = tmp_path / "restored-artifacts"
    result = restore_backup(backup, isolated_postgres_engine, artifacts_dir=restored_artifacts)
    assert result["verified"] and result["ledger_valid"]
    assert result["row_counts"] == manifest["row_counts"]
    assert result["ledger_head_hash"] == manifest["ledger_head_hash"]
    assert fingerprint(isolated_postgres_engine)["row_counts"] == fingerprint(migrated_engine)["row_counts"]
    assert (restored_artifacts / "run-1" / "model.json").read_text(encoding="utf-8") == '{"weights": [1, 2, 3]}'

    # The restored database is fully usable: login, tenant isolation and new writes.
    from sqlalchemy.orm import sessionmaker

    restored = sessionmaker(bind=isolated_postgres_engine, expire_on_commit=False)
    user = IdentityService(restored).login("fin.b", STRONG).user
    assert [f["facility_id"] for f in FacilityService(restored).list_for_user(user)] == [
        tenants["b_facility"]["facility_id"]
    ]
    IdentityService(restored).login("financier.demo", "Demo123!")


def test_tampered_backup_is_refused(session_factory, migrated_engine, tmp_path):
    _demo_users(session_factory)
    backup = tmp_path / "backup"
    create_backup(migrated_engine, backup)
    target = backup / "database" / "users.csv"
    target.write_bytes(target.read_bytes().replace(b"demo", b"evil", 1))
    with pytest.raises(BackupError, match="hash mismatch"):
        verify_backup(backup)
    (backup / "extra.csv").write_text("x", encoding="utf-8")
    with pytest.raises(BackupError):
        verify_backup(backup)


def test_backup_cli_and_scripts_exist():
    for script in ("backup.sh", "restore.sh", "init-env.sh", "init-env.ps1"):
        assert (ROOT / "scripts" / "ops" / script).exists(), script
    assert "--yes" in (ROOT / "scripts" / "ops" / "restore.sh").read_text(encoding="utf-8")


# --- Config center ------------------------------------------------------------------------------


def test_config_changes_are_versioned_audited_reversible_and_applied(session_factory):
    users = _demo_users(session_factory)
    admin = users["admin"]
    config = ConfigService(session_factory, cache_seconds=0)
    assert config.get("security.login_max_failures") == 5
    first = config.set_value(admin, "security.login_max_failures", value=2, reason="pilot hardening", expected_version=0)
    assert first["version"] == 1 and config.get("security.login_max_failures") == 2
    config.set_value(admin, "security.login_max_failures", value=4, reason="support load", expected_version=1)
    with pytest.raises(ConfigConflict):
        config.set_value(admin, "security.login_max_failures", value=9, reason="stale", expected_version=1)
    with pytest.raises(ConfigConflict):
        config.set_value(admin, "security.login_max_failures", value=999, reason="out of range", expected_version=2)
    rolled = config.rollback(admin, "security.login_max_failures", to_version=1, reason="back to pilot", expected_version=2)
    assert rolled == {**rolled, "version": 3, "value": 2, "rollback_of_version": 1}
    assert config.get("security.login_max_failures") == 2

    # The effective value drives login lock-out through the settings provider.
    identity = IdentityService(
        session_factory, settings_provider=lambda: SecuritySettings.from_env().with_overrides(config.values())
    )
    with pytest.raises(InvalidCredentials):
        identity.login("supplier.demo", "bad")
    with pytest.raises(AccountLocked):
        identity.login("supplier.demo", "bad")

    with session_factory() as session:
        versions = session.scalars(select(SystemConfigModel.version).order_by(SystemConfigModel.version)).all()
        ledger = session.scalars(
            select(LedgerEventModel).where(LedgerEventModel.event_type == "CONFIG_VERSIONED")
        ).all()
    assert versions == [1, 2, 3] and len(ledger) == 3
    with pytest.raises(DBAPIError):
        with session_factory.begin() as session:
            session.execute(text("UPDATE system_config SET value = '7'::jsonb"))


def test_config_center_drives_outcome_review_policy(session_factory, tmp_path):
    from app.services.outcomes import OutcomeService

    users = _demo_users(session_factory)
    config = ConfigService(session_factory, cache_seconds=0)
    outcomes = OutcomeService(session_factory, artifact_root=tmp_path, policy_provider=config.values)
    assert outcomes.manual_review is False
    config.set_value(users["admin"], "outcome.manual_review", value=True, reason="pilot review", expected_version=0)
    assert outcomes.manual_review is True
    outcomes.manual_review = False  # an explicit assignment pins the value
    assert outcomes.manual_review is False


def test_risk_rule_rollback_writes_a_new_version(session_factory):
    from app.services.risk_operations import RiskRuleService, seed_default_rules

    users = _demo_users(session_factory)
    with session_factory.begin() as session:
        seed_default_rules(session)
    rules = RiskRuleService(session_factory)
    rule = next(r for r in rules.list_rules(users["admin"])["rules"] if r["threshold"] is not None)
    changed = rules.change_rule(
        rule["rule_key"], users["admin"], threshold=Decimal("0.9"), severity=rule["severity"], enabled=True,
        effective_from=None, change_reason="experiment", expected_version=rule["version"],
    )
    rolled = rules.rollback_rule(
        rule["rule_key"], users["admin"], to_version=rule["version"], change_reason="undo experiment",
        expected_version=changed["version"],
    )
    assert rolled["version"] == changed["version"] + 1
    assert str(rolled["threshold"]) == str(rule["threshold"])
    assert rolled["change_reason"].startswith(f"rollback_to_v{rule['version']}")


# --- 13: restart recovery ----------------------------------------------------------------------


def test_application_restart_recovers_sessions_data_and_health(tenants, migrated_engine, session_factory):
    with TestClient(_app(migrated_engine, session_factory)) as client:
        _login(client, "financier.demo")
        cookie = client.cookies.get("daibm_session")
        before = client.get("/api/v1/facilities").json()
    # A new process (same database) keeps sessions and data and reports healthy.
    with TestClient(_app(migrated_engine, session_factory)) as client:
        client.cookies.set("daibm_session", cookie)
        assert client.get("/api/v1/facilities").json() == before
        assert client.get("/api/v1/ops/health").json()["status"] == "ok"


def test_suspending_an_organization_ends_its_sessions(tenants, session_factory):
    identity = IdentityService(session_factory)
    token = identity.login("fin.b", STRONG).token
    OrganizationService(session_factory).set_organization_status(
        tenants["users"]["admin"], tenants["orgs"]["bank_b"]["organization_id"], status="suspended", reason="offboarding"
    )
    with pytest.raises(AuthenticationRequired):
        identity.authenticate(token)
    with pytest.raises(InvalidCredentials):
        identity.login("fin.b", STRONG)


# --- 4.6: deployment contract ------------------------------------------------------------------


def test_deployment_has_healthcheck_restart_policy_and_guide():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert compose.count("restart: unless-stopped") == 2
    assert "/api/v1/ops/health" in compose
    assert "HEALTHCHECK" in (ROOT / "Dockerfile").read_text(encoding="utf-8")
    guide = (ROOT / "DEPLOYMENT_GUIDE.md").read_text(encoding="utf-8")
    for heading in ("环境要求", "配置", "启动", "数据初始化", "备份", "恢复"):
        assert heading in guide
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert re.search(r"^POSTGRES_PASSWORD=$", example, re.M) and re.search(r"^DAIBM_DEMO_PASSWORD=$", example, re.M)


def test_grants_table_rejects_rewrites(tenants, session_factory):
    with session_factory() as session:
        assert session.scalars(select(AuditGrantModel)).first() is not None
    with pytest.raises(DBAPIError):
        with session_factory.begin() as session:
            session.execute(text("UPDATE audit_grants SET reason = 'rewritten'"))


def test_migration_backfills_ownership_on_a_database_that_already_has_facilities(isolated_postgres_engine):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy.orm import sessionmaker

    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))

    def migrate(target: str, *, down: bool = False) -> None:
        with isolated_postgres_engine.connect() as connection:
            config.attributes["connection"] = connection
            (command.downgrade if down else command.upgrade)(config, target)
            connection.commit()

    migrate("head")
    factory = sessionmaker(bind=isolated_postgres_engine, expire_on_commit=False)
    users = {f"{name}.demo": user for name, user in _demo_users(factory).items()}
    facilities = FacilityService(factory)
    facility = _mark_overdue(facilities, users, _activate(facilities, users, _approved_application(factory, users)))
    with isolated_postgres_engine.begin() as connection:
        # Phase 4 rows that did not exist before this revision.
        connection.execute(text("TRUNCATE security_events, audit_grants"))
    migrate("20260928_0019", down=True)
    migrate("head")
    with factory() as session:
        owner = session.scalar(
            select(FinancingFacilityModel.organization_id).where(
                FinancingFacilityModel.facility_id == uuid.UUID(facility["facility_id"])
            )
        )
    assert owner == users["financier.demo"].organization_id
