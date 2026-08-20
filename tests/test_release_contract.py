from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from app.database import Database
from app.main import create_app


ROOT = Path(__file__).parents[1]


@pytest.fixture
def client(migrated_engine, session_factory, login_user):
    database = Database(
        engine=migrated_engine,
        session_factory=session_factory,
    )
    with TestClient(create_app(database)) as test_client:
        login_user(test_client, "auditor.demo")
        yield test_client


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_promoted_reference_artifact_verifies_from_public_cli():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "research.cli",
            "verify",
            "--reference",
            "artifacts/reference",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["status"] == "verified"
    assert len(payload["artifact_sha256"]) == 64


def test_public_verify_command_does_not_require_training_frameworks():
    script = """
import importlib.abc
import runpy
import sys

class BlockTrainingFrameworks(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.', 1)[0] in {'torch', 'xgboost'}:
            raise ModuleNotFoundError(f'blocked training dependency: {fullname}')
        return None

sys.meta_path.insert(0, BlockTrainingFrameworks())
sys.argv = [
    'research.cli',
    'verify',
    '--reference',
    'artifacts/reference',
]
runpy.run_module('research.cli', run_name='__main__')
"""

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "verified"


def test_clean_migration_is_at_head_and_metadata_has_no_drift(migrated_engine):
    config = Config(str(ROOT / "alembic.ini"))
    with migrated_engine.connect() as connection:
        config.attributes["connection"] = connection
        command.check(config)


def test_compose_requires_research_core_in_application_service_only():
    compose = _read("docker-compose.yml")
    postgres, mvp = compose.split("  mvp:", 1)

    assert "RESEARCH_ARTIFACT_DIR" not in postgres
    assert "RESEARCH_CORE_REQUIRED" not in postgres
    assert "RESEARCH_ARTIFACT_DIR: /app/artifacts/reference" in mvp
    assert 'RESEARCH_CORE_REQUIRED: "true"' in mvp
    assert "DATABASE_URL" not in compose


def test_launcher_opens_browser_only_after_semantic_health_check():
    launcher = _read("start-demo.cmd")

    assert "ConvertFrom-Json" in launcher
    assert "$payload.status -eq 'ok'" in launcher
    assert "$payload.database.backend -eq 'postgresql'" in launcher
    assert "$payload.database.reachable -eq $true" in launcher
    assert "$payload.ledger.valid -eq $true" in launcher
    assert "$payload.research_core.status -eq 'ready'" in launcher


def test_release_documentation_exposes_reproducibility_and_true_boundaries():
    readme = _read("README.md")
    design = _read("docs/mvp-design.md")
    demo = _read("docs/demo-script.md")

    for command in (
        "python -m research.cli generate",
        "python -m research.cli train-xgboost",
        "python -m research.cli train-tgnn",
        "python -m research.cli promote",
        "python -m research.cli verify",
    ):
        assert command in readme
    assert "GCN" in design and "BiLSTM" in design and "ONNX Runtime" in design
    assert "Исследовательское ядро" in demo
    assert "科研核心" in demo
    assert "0.5623" in demo and "0.7519" in demo
    assert "INTEGRITY_VIOLATION_DETECTED" in demo
    assert "LEDGER_RECOVERY_COMPLETED" in demo


def test_traceability_uses_only_canonical_values_and_matches_implemented_work():
    matrix = _read("docs/thesis-traceability.md")
    allowed_statuses = {
        "IMPLEMENTED",
        "PARTIAL",
        "SIMULATED",
        "PLANNED",
        "NOT IMPLEMENTED",
    }
    allowed_provenance = {
        "THESIS_REPORTED",
        "2026_REIMPLEMENTATION",
        "SYNTHETIC_DEMO",
        "CONFERENCE_RERUN",
    }
    rows = [line for line in matrix.splitlines() if line.startswith("|")][2:]
    assert rows
    for row in rows:
        cells = [cell.strip() for cell in row.strip("|").split("|")]
        assert cells[3] in allowed_statuses
        assert cells[4] in allowed_provenance

    recovery = next(row for row in rows if "Integrity-violation recovery" in row)
    injection = next(row for row in rows if "Risk-injection demonstration" in row)
    assert "| IMPLEMENTED |" in recovery
    assert "| IMPLEMENTED |" in injection


def test_health_rejects_an_invalid_ledger(client):
    client.post("/api/demo/reset")
    tampered = client.post("/api/demo/tamper")
    assert tampered.json()["valid"] is False

    response = client.get("/api/health")

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "ledger_integrity_failed",
            "message": "Audit ledger integrity verification failed",
        }
    }


def test_integrated_application_version_advances_after_acceptance(client):
    assert client.get("/openapi.json").json()["info"]["version"] == "0.6.0"


def test_release_documentation_lists_five_demo_roles_and_workflow():
    readme = _read("README.md")
    demo = _read("docs/demo-script.md")

    for username in (
        "supplier.demo",
        "core.demo",
        "financier.demo",
        "risk.demo",
        "auditor.demo",
    ):
        assert username in readme
        assert username in demo
    assert "Demo123!" in readme
    assert "draft → submitted" in readme
    assert "/api/v1/applications" in readme
