"""Phase 5.4 final security check: secrets, the role matrix and audit coverage."""

from __future__ import annotations

import re
import subprocess
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.database import Database
from app.demo_dataset import DemoDatasetSeeder
from app.models import LedgerEventModel
from app.models_enterprise import SecurityEventModel, SystemConfigModel
from app.models_lifecycle import FacilityStatusTransitionModel
from app.models_model_governance import RiskModelVersionTransitionModel

ROOT = Path(__file__).resolve().parents[1]
# The old literal default was lower case; the variable DAIBM_DEMO_PASSWORD is fine.
SECRET_PATTERNS = re.compile(
    r"Demo123|daibm_demo_password|BEGIN (RSA|EC|OPENSSH|PRIVATE)|AKIA[0-9A-Z]{16}"
    r"|ghp_[A-Za-z0-9]{20}|sk-[A-Za-z0-9]{20}"
    r"|(?i:password|secret|token|api_key)\s*[:=]\s*['\"][^'\"$\{][^'\"]{5,}['\"]"
)
# Labels such as `password: "Пароль"` are UI text, not credentials.
UI_LABEL = re.compile(r"(?i:password):\s*\"[^\"]*[^\x00-\x7f][^\"]*\"")


def test_no_tracked_file_contains_a_default_password_token_or_key():
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    offenders = []
    for name in tracked:
        # Tests use the test-only demo password; history docs and reports describe removals.
        if name.startswith(("tests/", "docs/superpowers/", "output/", "artifacts/")) or name.endswith(
            ("_REPORT.md", ".pdf", ".png", ".pt", ".pth", ".tar")
        ):
            continue
        path = ROOT / name
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue
        for line_number, line in enumerate(content.splitlines(), 1):
            if SECRET_PATTERNS.search(line) and not UI_LABEL.search(line):
                offenders.append(f"{name}:{line_number}")
    assert offenders == []
    assert not (ROOT / ".env").exists() or ".env" in (ROOT / ".gitignore").read_text()
    assert ".env" not in tracked


ROLE_MATRIX = {
    # endpoint: roles that may read it
    "/api/v1/admin/organizations": {"admin"},
    "/api/v1/admin/users": {"admin"},
    "/api/v1/ops/metrics": {"admin"},
    "/api/v1/admin/config": {"admin", "auditor"},
    "/api/v1/admin/security-events": {"admin", "auditor"},
    "/api/v1/risk/alerts": {"admin", "risk_manager", "auditor"},
    "/api/v1/risk/tasks": {"admin", "risk_manager", "auditor"},
    "/api/v1/risk/rules": {"admin", "risk_manager", "auditor"},
    "/api/v1/risk/dashboard": {"admin", "risk_manager", "auditor", "financier"},
    "/api/v1/model-versions": {"admin", "risk_manager", "auditor", "financier"},
    "/api/v1/facilities": {"admin", "risk_manager", "auditor", "financier", "supplier", "core_enterprise"},
}
ACCOUNTS = {
    "supplier": "supplier.demo",
    "core_enterprise": "core.demo",
    "financier": "financier.demo",
    "risk_manager": "risk.demo",
    "auditor": "auditor.demo",
    "admin": "admin.demo",
}


@pytest.fixture
def client(migrated_engine, session_factory):
    from app.main import create_app

    app = create_app(Database(migrated_engine, session_factory), calibration_worker_enabled=False)
    with TestClient(app) as test_client:
        yield test_client


def _as(client: TestClient, role: str) -> TestClient:
    client.cookies.clear()
    response = client.post(
        "/api/v1/auth/login", json={"username": ACCOUNTS[role], "password": "Demo123!"}
    )
    assert response.status_code == 200
    return client


@pytest.mark.parametrize("role", sorted(ACCOUNTS))
def test_every_role_gets_exactly_its_row_of_the_matrix(client, role):
    _as(client, role)
    for endpoint, allowed in ROLE_MATRIX.items():
        status = client.get(endpoint).status_code
        assert (status == 200) == (role in allowed), (role, endpoint, status)


def test_writes_are_limited_to_their_single_owning_role(client):
    config = {"value": 3, "reason": "tighten", "expected_version": 0}
    rule = {"expected_version": 1, "threshold": "5", "severity": "HIGH", "enabled": True,
            "change_reason": "tighten threshold"}
    unknown = str(uuid.uuid4())
    for role in ("risk_manager", "auditor", "financier"):
        _as(client, role)
        assert client.post("/api/v1/admin/config/security.lockout_minutes", json=config).status_code == 403
    for role in ("risk_manager", "auditor"):
        _as(client, role)
        assert client.post("/api/v1/risk/rules/OVERDUE_DAYS/versions", json=rule).status_code == 403
    # Model promotion is an auditor's decision, not an administrator's.
    for role in ("admin", "risk_manager", "financier"):
        _as(client, role)
        response = client.post(f"/api/v1/model-versions/{unknown}/activate", json={"reason": "go"})
        assert response.status_code in {403, 422}, (role, response.status_code)
        assert response.status_code != 200


def test_key_operations_all_leave_an_audit_record(client, session_factory, tmp_path):
    # State changes and a model switch through the services (demo dataset).
    DemoDatasetSeeder(session_factory, artifact_root=tmp_path, demo_password="Demo123!").seed()
    _as(client, "supplier")
    assert client.get("/api/v1/admin/users").status_code == 403
    _as(client, "admin")
    assert client.post(
        "/api/v1/admin/config/security.lockout_minutes",
        json={"value": 20, "reason": "policy review", "expected_version": 0},
    ).status_code == 200
    with session_factory() as session:
        security = dict(
            session.execute(
                select(SecurityEventModel.event_type, func.count()).group_by(SecurityEventModel.event_type)
            ).all()
        )
        ledger = set(session.scalars(select(LedgerEventModel.event_type).distinct()))
        facility_transitions = session.scalar(select(func.count()).select_from(FacilityStatusTransitionModel))
        activations = session.scalars(
            select(RiskModelVersionTransitionModel).where(RiskModelVersionTransitionModel.to_status == "ACTIVE")
        ).all()
        config_versions = session.scalar(select(func.count()).select_from(SystemConfigModel))
    # Sign-in and permission denial.
    assert security["LOGIN_SUCCESS"] >= 2 and security["PERMISSION_DENIED"] >= 1
    # Business state changes: facility transitions and their ledger events.
    assert facility_transitions > 0
    assert {"FACILITY_CREATED", "DISBURSEMENT_CONFIRMED", "FACILITY_MARKED_OVERDUE",
            "FACILITY_DEFAULTED", "FACILITY_WRITTEN_OFF", "FACILITY_CLOSED",
            "FINANCING_DECISION", "RISK_ALERT_TRANSITIONED", "RISK_TASK_RECORDED"} <= ledger
    # Model switch: an audited transition with its evidence, and the ledger event.
    assert activations and all(item.evaluation_metrics for item in activations)
    assert "CALIBRATION_AUTO_ACTIVATED" in ledger
    # Configuration change: version, admin action and ledger entry.
    assert config_versions == 1 and security["ADMIN_ACTION"] >= 1
    assert {"CONFIG_VERSIONED", "ADMIN_ACTION_RECORDED"} <= ledger
