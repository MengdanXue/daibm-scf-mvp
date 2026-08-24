from __future__ import annotations

from pathlib import Path

from sqlalchemy import inspect, text


ROOT = Path(__file__).resolve().parents[1]


def test_outcome_calibration_migration_extends_single_head_from_0006():
    source = (
        ROOT / "alembic" / "versions" / "20260824_0007_outcome_calibration.py"
    ).read_text(encoding="utf-8")

    assert 'revision: str = "20260824_0007"' in source
    assert 'down_revision: str | None = "20260824_0006"' in source


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
