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


def test_fabric_overlay_joins_only_gateway_to_the_internal_application_network():
    base = _read("docker-compose.yml")
    fabric = _read("advanced/fabric/network/docker-compose.fabric.yml")

    assert "name: daibm-scf-mvp-network" in base
    assert "name: daibm-scf-mvp-network" in fabric
    gateway = fabric.split("  gateway:", 1)[1].split("\nnetworks:", 1)[0]
    assert "ports:" not in gateway
    assert "app:" in gateway
    assert "- fabric-gateway" in gateway
    for service in ("bootstrap", "orderer.example.com", "peer0.org1.example.com", "cli"):
        section = fabric.split(f"  {service}:", 1)[1].split("\n  ", 1)[0]
        assert "- app" not in section


def test_advanced_launcher_has_fixed_targets_and_fabric_only_cleanup():
    launcher = _read("start-fabric-demo.cmd")

    assert launcher.isascii()
    assert 'set "DOCKER_CONTEXT=desktop-linux"' in launcher
    assert 'set "DOCKER_HOST="' in launcher
    assert 'set "COMPOSE_FILE="' in launcher
    assert 'set "COMPOSE_PROJECT_NAME="' in launcher
    assert (
        'compose -f "%~dp0docker-compose.yml" --project-name daibm-scf-mvp up --build -d'
        in launcher
    )
    assert (
        'compose -f "%~dp0advanced\\fabric\\network\\docker-compose.fabric.yml" '
        '--project-name daibm-fabric-demo up --build -d' in launcher
    )
    assert "RESET FABRIC" in launcher
    assert "down -v" not in launcher
    assert "postgres-data" not in launcher
    assert "FABRIC_HEALTH_ATTEMPT" in launcher
    assert "GEQ 30" in launcher
    assert ":wait_fabric_gateway" in launcher
    messages = json.loads(_read("launcher-messages.json"))
    for key in (
        "fabric_start_base",
        "fabric_start_network",
        "fabric_deploy",
        "fabric_gateway_check",
        "fabric_ready",
        "fabric_reset_warning",
        "fabric_reset_cancelled",
    ):
        assert any("а" <= character.lower() <= "я" for character in messages[key])
        assert any("\u4e00" <= character <= "\u9fff" for character in messages[key])


def test_optional_fabric_mode_is_documented_without_overclaiming():
    readme = _read("README.md")
    demo = _read("docs/demo-script.md")

    for document in (readme, demo):
        assert "start-fabric-demo.cmd" in document
        assert "RESET FABRIC" in document
        assert "permanent_failed" in document
        assert "Gateway" in document
    assert "Gateway не публикует порт" in readme
    assert "Gateway 不暴露宿主机端口" in readme
    assert "production blockchain" in readme
    assert "生产级区块链" in readme


def test_actual_outcome_feedback_boundary_is_documented_without_promotion_claims():
    readme = _read("README.md")
    demo = _read("docs/demo-script.md")
    defense = _read("docs/defense-one-page.md")
    brief = _read("docs/research-brief-en.md")

    for document in (readme, demo, defense, brief):
        assert "Platt" in document
        assert "not_promoted" in document or "never promoted" in document
    for document in (defense, brief):
        assert "does not retrain the TGNN" in document
        assert "does not trigger on drift" in document
        assert "does not prove real-enterprise effects" in document
    assert "There is no actual-outcome feedback loop" not in brief


def test_actual_outcome_browser_acceptance_covers_retry_lineage_and_candidate_boundary():
    acceptance = _read("scripts/outcome_browser_acceptance.py")

    for expected in (
        "_exercise_primary(page)",
        "actual-outcome",
        "route.fulfill(status=503",
        "first_payload == retry_payload",
        "#outcomeLineage",
        "#calibrationCandidate",
        "exploratory_candidate",
        "eligible_candidate",
        "expected_status",
        ".candidate-status.failed",
        "never promoted",
        'page.set_viewport_size({"width": 390, "height": 844})',
    ):
        assert expected in acceptance


def test_fabric_acceptance_drains_persistent_backlog_with_a_time_bound():
    acceptance = _read("scripts/fabric_browser_acceptance.py")

    assert "time.monotonic()" in acceptance
    assert "dispatch_deadline" in acceptance
    assert "range(6)" not in acceptance


def test_launcher_opens_browser_only_after_semantic_health_check():
    launcher = _read("start-demo.cmd")

    assert "ConvertFrom-Json" in launcher
    assert "$payload.status -eq 'ok'" in launcher
    assert "$payload.database.backend -eq 'postgresql'" in launcher
    assert "$payload.database.reachable -eq $true" in launcher
    assert "$payload.ledger.valid -eq $true" in launcher
    assert "$payload.research_core.status -eq 'ready'" in launcher


def test_reset_launcher_is_separate_confirmed_and_scoped():
    reset = _read("reset-defense-demo.cmd")
    start = _read("start-demo.cmd")

    assert "RESET DEMO" in reset
    assert "Read-Host" in reset and "-ceq 'RESET DEMO'" in reset
    assert "set /p" not in reset
    assert 'set "COMPOSE_FILE="' in reset
    assert 'set "COMPOSE_PROJECT_NAME="' in reset
    assert 'set "DOCKER_HOST="' in reset
    assert 'set "DOCKER_CONTEXT=desktop-linux"' in reset
    down = (
        'docker --context desktop-linux compose -f "%~dp0docker-compose.yml" '
        "--project-name daibm-scf-mvp down -v"
    )
    assert reset.count(down) == 1
    assert 'call "%~dp0start-demo.cmd"' in reset
    assert "scripts\\defense_preflight.py" in reset
    assert reset.index(down) < reset.index(
        'call "%~dp0start-demo.cmd"'
    ) < reset.index("scripts\\defense_preflight.py")
    assert "Remove-Item" not in reset
    assert "rm -rf" not in reset
    assert "docker compose down -v" not in start


def test_reset_launcher_messages_and_documentation_are_bilingual():
    messages = json.loads(_read("launcher-messages.json"))
    readme = _read("README.md")

    for key in (
        "reset_warning",
        "reset_cancelled",
        "docker_context_unavailable",
        "preflight_failed",
    ):
        assert any("а" <= character.lower() <= "я" for character in messages[key])
        assert any("\u4e00" <= character <= "\u9fff" for character in messages[key])
    assert "reset-defense-demo.cmd" in readme
    assert "scripts\\defense_preflight.py" in readme
    assert "docker compose down -v" not in readme


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


def test_ci_runs_research_suite_in_a_separate_pinned_environment():
    workflow = _read(".github/workflows/ci.yml")
    application_job, research_job = workflow.split("  research-tests:", 1)

    assert workflow.count("actions/checkout@v5") == 2
    assert workflow.count("actions/setup-python@v6") == 2
    assert "requirements-dev.txt" in application_job
    assert "tests --ignore=tests/research" in application_job
    assert "cache-dependency-path: requirements-research.txt" in research_job
    assert "python -m pip install -r requirements-research.txt" in research_job
    assert "python -m pytest tests/research -q" in research_job


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
