from __future__ import annotations

import importlib.util
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import inspect


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = (
    ROOT
    / "alembic"
    / "versions"
    / "20260824_0009_governed_self_training.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "governed_self_training_0009",
        MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_self_training_revision_extends_the_single_head():
    scripts = ScriptDirectory.from_config(Config("alembic.ini"))
    revision = scripts.get_revision("20260824_0009")

    assert revision.down_revision == "20260824_0008"
    assert scripts.get_current_head() == "20260824_0009"


def test_self_training_schema_has_constrained_single_active_deployment(
    migrated_engine,
):
    inspector = inspect(migrated_engine)
    run_columns = {
        column["name"]: column
        for column in inspector.get_columns("calibration_runs")
    }
    assert {
        "deployment_status",
        "deployment_scope",
        "activation_mode",
        "activated_at",
        "deactivated_at",
        "previous_active_run_id",
        "activation_reason",
    } <= set(run_columns)
    assert run_columns["deployment_status"]["nullable"] is False
    assert run_columns["deployment_scope"]["nullable"] is False
    assert run_columns["activation_reason"]["nullable"] is False

    checks = {
        item["name"]: item["sqltext"]
        for item in inspector.get_check_constraints("calibration_runs")
    }
    assert {
        "ck_calibration_runs_deployment_status",
        "ck_calibration_runs_deployment_scope",
        "ck_calibration_runs_activation_mode",
        "ck_calibration_runs_deployment_contract",
    } <= set(checks)
    deployment_contract = " ".join(checks.values())
    for value in (
        "not_deployed",
        "active",
        "superseded",
        "rejected",
        "activation_failed",
        "controlled_demo",
        "external_verified",
        "mixed",
        "automatic",
        "manual_rollback",
    ):
        assert value in deployment_contract
    assert "eligible_candidate" in checks[
        "ck_calibration_runs_deployment_contract"
    ]

    run_foreign_keys = inspector.get_foreign_keys("calibration_runs")
    assert any(
        item["constrained_columns"] == ["previous_active_run_id"]
        and item["referred_table"] == "calibration_runs"
        and item["options"].get("ondelete") == "RESTRICT"
        for item in run_foreign_keys
    )
    indexes = {
        item["name"]: item
        for item in inspector.get_indexes("calibration_runs")
    }
    assert "ix_calibration_runs_previous_active_run_id" in indexes
    assert "uq_calibration_runs_single_active" in indexes
    assert indexes["uq_calibration_runs_single_active"]["unique"] is True
    predicate = str(
        indexes["uq_calibration_runs_single_active"]
        .get("dialect_options", {})
        .get("postgresql_where", "")
    )
    assert "active" in predicate


def test_self_training_schema_preserves_raw_and_deployment_lineage(
    migrated_engine,
):
    inspector = inspect(migrated_engine)
    request_columns = {
        column["name"]: column
        for column in inspector.get_columns("financing_requests")
    }
    assert {
        "raw_risk_score",
        "calibration_run_id",
        "calibration_fallback_code",
    } <= set(request_columns)
    assert all(
        request_columns[name]["nullable"] is True
        for name in (
            "raw_risk_score",
            "calibration_run_id",
            "calibration_fallback_code",
        )
    )
    foreign_keys = inspector.get_foreign_keys("financing_requests")
    assert any(
        item["constrained_columns"] == ["calibration_run_id"]
        and item["referred_table"] == "calibration_runs"
        and item["options"].get("ondelete") == "RESTRICT"
        for item in foreign_keys
    )
    indexes = {
        item["name"] for item in inspector.get_indexes("financing_requests")
    }
    assert "ix_financing_requests_calibration_run_id" in indexes
    checks = {
        item["name"]
        for item in inspector.get_check_constraints("financing_requests")
    }
    assert {
        "ck_financing_requests_raw_risk_score",
        "ck_financing_requests_calibration_lineage",
    } <= checks


def test_self_training_migration_allows_new_ledger_event_types(migrated_engine):
    checks = " ".join(
        item["sqltext"]
        for item in inspect(migrated_engine).get_check_constraints("ledger_events")
    )
    for event_type in (
        "CALIBRATION_AUTO_ACTIVATED",
        "CALIBRATION_AUTO_REJECTED",
        "CALIBRATION_ROLLED_BACK",
        "RISK_CALIBRATION_APPLIED",
        "RISK_CALIBRATION_FALLBACK",
    ):
        assert event_type in checks


def test_self_training_downgrade_refuses_adaptive_lineage(monkeypatch):
    migration = _load_migration()

    class FakeBind:
        def scalar(self, _statement):
            return 1

    monkeypatch.setattr(migration.op, "get_bind", lambda: FakeBind())

    with pytest.raises(RuntimeError, match="adaptive calibration lineage"):
        migration.downgrade()
