from __future__ import annotations

from pathlib import Path
import importlib.util
import uuid

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
import pytest


_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "20260824_0007_outcome_calibration.py"
)
_SPEC = importlib.util.spec_from_file_location("outcome_migration_0007", _MIGRATION_PATH)
assert _SPEC is not None and _SPEC.loader is not None
outcome_migration = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(outcome_migration)

_BASELINE_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "20260824_0008_baseline_outcome_lineage.py"
)
_BASELINE_SPEC = importlib.util.spec_from_file_location(
    "outcome_migration_0008", _BASELINE_MIGRATION_PATH
)
assert _BASELINE_SPEC is not None and _BASELINE_SPEC.loader is not None
baseline_outcome_migration = importlib.util.module_from_spec(_BASELINE_SPEC)
_BASELINE_SPEC.loader.exec_module(baseline_outcome_migration)


ROOT = Path(__file__).resolve().parents[1]


def _migrate(engine, revision: str) -> None:
    config = Config(str(ROOT / "alembic.ini"))
    with engine.connect() as connection:
        config.attributes["connection"] = connection
        if revision == "head":
            command.upgrade(config, revision)
        else:
            command.downgrade(config, revision)


def test_outcome_calibration_migration_extends_single_head_from_0006():
    source = (
        ROOT / "alembic" / "versions" / "20260824_0007_outcome_calibration.py"
    ).read_text(encoding="utf-8")

    assert 'revision: str = "20260824_0007"' in source
    assert 'down_revision: str | None = "20260824_0006"' in source

    baseline_source = (
        ROOT / "alembic" / "versions" / "20260824_0008_baseline_outcome_lineage.py"
    ).read_text(encoding="utf-8")
    assert 'revision: str = "20260824_0008"' in baseline_source
    assert 'down_revision: str | None = "20260824_0007"' in baseline_source


def test_outcome_and_calibration_schema_is_present_at_postgresql_head(
    migrated_engine,
):
    inspector = inspect(migrated_engine)

    assert {"actual_outcomes", "calibration_runs"}.issubset(
        inspector.get_table_names()
    )
    outcome_columns = {
        column["name"]: column for column in inspector.get_columns("actual_outcomes")
    }
    run_columns = {
        column["name"]: column for column in inspector.get_columns("calibration_runs")
    }
    assert set(outcome_columns) == {
        "outcome_id",
        "facility_id",
        "request_id",
        "risk_assessment_id",
        "model_version_id",
        "submitted_by_user_id",
        "idempotency_key",
        "request_sha256",
        "defaulted",
        "days_past_due",
        "loss_amount",
        "observed_at",
        "evidence_sha256",
        "provenance",
        "original_risk_score",
        "risk_engine_version",
        "risk_input_sha256",
        "recorded_at",
    }
    assert set(run_columns) == {
        "calibration_run_id",
        "trigger_outcome_id",
        "dataset_sha256",
        "sample_count",
        "positive_count",
        "negative_count",
        "metrics_before",
        "metrics_after",
        "configuration",
        "status",
        "artifact_locator",
        "artifact_sha256",
        "failure_code",
        "started_at",
        "completed_at",
    }
    assert outcome_columns["model_version_id"]["nullable"] is True
    assert outcome_columns["risk_engine_version"]["nullable"] is False
    outcome_foreign_keys = {
        tuple(item["constrained_columns"])
        for item in inspector.get_foreign_keys("actual_outcomes")
    }
    assert ("risk_assessment_id",) not in outcome_foreign_keys
    assert ("model_version_id",) in outcome_foreign_keys
    outcome_checks = {
        item["name"] for item in inspector.get_check_constraints("actual_outcomes")
    }
    run_checks = {
        item["name"] for item in inspector.get_check_constraints("calibration_runs")
    }
    assert {
        "ck_actual_outcomes_days_past_due",
        "ck_actual_outcomes_loss_amount",
        "ck_actual_outcomes_hashes",
        "ck_actual_outcomes_provenance",
        "ck_actual_outcomes_original_score",
    }.issubset(outcome_checks)
    assert {
        "ck_calibration_runs_counts",
        "ck_calibration_runs_status",
        "ck_calibration_runs_artifact_contract",
    }.issubset(run_checks)

    with migrated_engine.connect() as connection:
        triggers = set(
            connection.scalars(
                text(
                    "SELECT tgname FROM pg_trigger "
                    "WHERE tgrelid = 'actual_outcomes'::regclass "
                    "AND NOT tgisinternal"
                )
            )
        )
    assert triggers == {"trg_actual_outcomes_immutable"}


def test_downgrade_refuses_to_drop_immutable_rows_even_without_ledger_events(
    monkeypatch,
):
    class FakeBind:
        calls = 0

        def scalar(self, _statement):
            self.calls += 1
            return 0 if self.calls == 1 else 1

    bind = FakeBind()
    monkeypatch.setattr(outcome_migration.op, "get_bind", lambda: bind)

    with pytest.raises(RuntimeError, match="immutable outcome"):
        outcome_migration.downgrade()


def test_0008_downgrade_refuses_business_baseline_rows(monkeypatch):
    class FakeBind:
        def scalar(self, _statement):
            return 1

    monkeypatch.setattr(
        baseline_outcome_migration.op,
        "get_bind",
        lambda: FakeBind(),
    )

    with pytest.raises(RuntimeError, match="business-baseline outcomes"):
        baseline_outcome_migration.downgrade()


def test_0008_backfills_existing_research_outcome_despite_immutable_trigger(
    migrated_engine,
):
    model_version_id = uuid.uuid4()
    outcome_id = uuid.uuid4()
    try:
        _migrate(migrated_engine, "20260824_0007")
        with migrated_engine.begin() as connection:
            connection.execute(text("ALTER TABLE model_versions DISABLE TRIGGER ALL"))
            connection.execute(
                text(
                    "INSERT INTO model_versions ("
                    "model_version_id, model_name, semantic_version, model_family, "
                    "source_run_id, dataset_version_id, feature_schema_version, "
                    "inference_format, artifact_locator, checkpoint_sha256, metrics, "
                    "lifecycle_status, deployment_slot, created_at"
                    ") VALUES ("
                    ":model_id, 'legacy-tgnn', '1.2.3', 'tgnn', :source_id, "
                    ":dataset_id, 'v1', 'onnx', 'memory://legacy', :checkpoint, "
                    "CAST('{}' AS jsonb), 'candidate', NULL, now())"
                ),
                {
                    "model_id": model_version_id,
                    "source_id": uuid.uuid4(),
                    "dataset_id": uuid.uuid4(),
                    "checkpoint": "4" * 64,
                },
            )
            connection.execute(text("ALTER TABLE model_versions ENABLE TRIGGER ALL"))
            connection.execute(text("ALTER TABLE actual_outcomes DISABLE TRIGGER ALL"))
            connection.execute(
                text(
                    "INSERT INTO actual_outcomes ("
                    "outcome_id, facility_id, request_id, risk_assessment_id, "
                    "model_version_id, submitted_by_user_id, idempotency_key, "
                    "request_sha256, defaulted, days_past_due, loss_amount, "
                    "observed_at, evidence_sha256, provenance, original_risk_score, "
                    "risk_input_sha256, recorded_at"
                    ") VALUES ("
                    ":outcome_id, :facility_id, :request_id, :assessment_id, "
                    ":model_id, :user_id, :key, :request_sha, false, 0, 0.00, "
                    "now(), :evidence_sha, 'CONTROLLED_DEMO', 0.2, :input_sha, now())"
                ),
                {
                    "outcome_id": outcome_id,
                    "facility_id": uuid.uuid4(),
                    "request_id": uuid.uuid4(),
                    "assessment_id": uuid.uuid4(),
                    "model_id": model_version_id,
                    "user_id": uuid.uuid4(),
                    "key": uuid.uuid4(),
                    "request_sha": "1" * 64,
                    "evidence_sha": "2" * 64,
                    "input_sha": "3" * 64,
                },
            )
            connection.execute(text("ALTER TABLE actual_outcomes ENABLE TRIGGER ALL"))

        _migrate(migrated_engine, "head")

        with migrated_engine.connect() as connection:
            assert connection.scalar(
                text(
                    "SELECT risk_engine_version FROM actual_outcomes "
                    "WHERE outcome_id = :outcome_id"
                ),
                {"outcome_id": outcome_id},
            ) == "legacy-tgnn@1.2.3"
    finally:
        with migrated_engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE actual_outcomes "
                    "DISABLE TRIGGER trg_actual_outcomes_immutable"
                )
            )
            connection.execute(
                text("DELETE FROM actual_outcomes WHERE outcome_id = :outcome_id"),
                {"outcome_id": outcome_id},
            )
            connection.execute(
                text(
                    "ALTER TABLE actual_outcomes "
                    "ENABLE TRIGGER trg_actual_outcomes_immutable"
                )
            )
            connection.execute(
                text("DELETE FROM model_versions WHERE model_version_id = :model_id"),
                {"model_id": model_version_id},
            )
        _migrate(migrated_engine, "head")
