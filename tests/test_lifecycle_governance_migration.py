from __future__ import annotations

import importlib.util
from pathlib import Path
import uuid

from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = (
    ROOT
    / "alembic"
    / "versions"
    / "20260824_0010_lifecycle_corrections_scope.py"
)


def _load_migration():
    assert MIGRATION_PATH.exists(), "revision 0010 has not been implemented"
    spec = importlib.util.spec_from_file_location(
        "lifecycle_corrections_scope_0010",
        MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _named(items: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    return {str(item["name"]): item for item in items}


def _trigger_names(engine: sa.Engine, table_name: str) -> set[str]:
    with engine.connect() as connection:
        return set(
            connection.scalars(
                sa.text(
                    "SELECT tgname FROM pg_trigger "
                    "WHERE tgrelid = CAST(:table_name AS regclass) "
                    "AND NOT tgisinternal"
                ),
                {"table_name": table_name},
            )
        )


def test_lifecycle_governance_revision_is_single_head_and_constrained(
    migrated_engine,
):
    scripts = ScriptDirectory.from_config(Config("alembic.ini"))
    revision = scripts.get_revision("20260824_0010")
    assert revision.down_revision == "20260824_0009"
    assert scripts.get_current_head() == "20260824_0010"

    inspector = sa.inspect(migrated_engine)
    governed_tables = {
        "facility_delinquencies",
        "facility_restructures",
        "facility_defaults",
        "facility_writeoffs",
        "outcome_corrections",
        "calibration_jobs",
        "calibration_run_observations",
    }
    assert governed_tables <= set(inspector.get_table_names())

    request_columns = _named(inspector.get_columns("financing_requests"))
    assert request_columns["assessment_scope"]["nullable"] is False
    request_checks = _named(
        inspector.get_check_constraints("financing_requests")
    )
    assert "ck_financing_requests_assessment_scope" in request_checks

    facility_columns = _named(inspector.get_columns("financing_facilities"))
    assert facility_columns["current_schedule_version"]["nullable"] is False
    assert facility_columns["closure_reason"]["nullable"] is True
    action_checks = _named(
        inspector.get_check_constraints("facility_actions")
    )
    action_contract = str(
        action_checks["ck_facility_actions_action_type"]["sqltext"]
    )
    for action in ("restructure", "declare_default", "write_off"):
        assert action in action_contract

    installment_columns = _named(inspector.get_columns("facility_installments"))
    assert installment_columns["schedule_version"]["nullable"] is False
    installment_uniques = _named(
        inspector.get_unique_constraints("facility_installments")
    )
    assert installment_uniques[
        "uq_facility_installments_facility_schedule_sequence"
    ]["column_names"] == ["facility_id", "schedule_version", "sequence"]
    assert "uq_facility_installments_facility_sequence" not in installment_uniques

    run_columns = _named(inspector.get_columns("calibration_runs"))
    assert run_columns["trigger_outcome_id"]["nullable"] is True
    assert run_columns["trigger_job_id"]["nullable"] is True
    assert run_columns["artifact_schema"]["nullable"] is True
    run_checks = _named(inspector.get_check_constraints("calibration_runs"))
    assert "invalidated" in str(
        run_checks["ck_calibration_runs_deployment_status"]["sqltext"]
    )
    run_indexes = _named(inspector.get_indexes("calibration_runs"))
    assert run_indexes["uq_calibration_runs_active_scope"]["unique"] is True
    assert run_indexes["uq_calibration_runs_active_scope"]["column_names"] == [
        "deployment_scope"
    ]
    predicate = str(
        run_indexes["uq_calibration_runs_active_scope"]
        .get("dialect_options", {})
        .get("postgresql_where", "")
    )
    assert "active" in predicate
    assert "uq_calibration_runs_single_active" not in run_indexes

    job_checks = _named(inspector.get_check_constraints("calibration_jobs"))
    job_check_text = " ".join(
        str(item["sqltext"]) for item in job_checks.values()
    )
    for token in (
        "queued",
        "running",
        "completed",
        "failed",
        "attempt_count",
        "lease_owner",
        "leased_until",
        "trigger_outcome_id",
        "trigger_correction_id",
        "result_run_id",
    ):
        assert token in job_check_text
    assert "ck_calibration_jobs_contract" in job_checks

    correction_checks = _named(
        inspector.get_check_constraints("outcome_corrections")
    )
    correction_check_text = " ".join(
        str(item["sqltext"]) for item in correction_checks.values()
    )
    assert "EXCLUDE" in correction_check_text
    assert "REINSTATE" in correction_check_text
    assert "^[0-9a-f]{64}$" in correction_check_text

    for table_name in (
        "facility_delinquencies",
        "facility_restructures",
        "facility_defaults",
        "facility_writeoffs",
        "outcome_corrections",
    ):
        checks = " ".join(
            str(item["sqltext"])
            for item in inspector.get_check_constraints(table_name)
        )
        assert "^[0-9a-f]{64}$" in checks

    expected_triggers = {
        **{
            table_name: {f"trg_{table_name}_immutable"}
            for table_name in governed_tables
            if table_name != "calibration_jobs"
        },
        "financing_facilities": {"trg_financing_facilities_closure_immutable"},
    }
    for table_name, expected in expected_triggers.items():
        assert expected <= _trigger_names(migrated_engine, table_name)

    for table_name in governed_tables:
        indexes = inspector.get_indexes(table_name)
        indexed_columns = {
            column
            for index in indexes
            for column in (index.get("column_names") or [])
        }
        unique_columns = {
            column
            for constraint in inspector.get_unique_constraints(table_name)
            for column in (constraint.get("column_names") or [])
        }
        primary_key_columns = set(
            inspector.get_pk_constraint(table_name).get(
                "constrained_columns", []
            )
        )
        indexed_columns |= unique_columns | primary_key_columns
        for foreign_key in inspector.get_foreign_keys(table_name):
            assert set(foreign_key["constrained_columns"]) <= indexed_columns

    run_foreign_keys = _named(inspector.get_foreign_keys("calibration_runs"))
    assert run_foreign_keys["fk_calibration_runs_trigger_job"][
        "constrained_columns"
    ] == ["trigger_job_id"]
    job_foreign_keys = _named(inspector.get_foreign_keys("calibration_jobs"))
    assert job_foreign_keys["fk_calibration_jobs_result_run"][
        "constrained_columns"
    ] == ["result_run_id"]


def test_database_rejects_two_active_runs_in_the_same_scope(migrated_engine):
    statement = sa.text(
        "INSERT INTO calibration_runs ("
        "calibration_run_id, trigger_outcome_id, trigger_job_id, dataset_sha256, "
        "sample_count, positive_count, negative_count, metrics_before, "
        "metrics_after, configuration, status, artifact_locator, "
        "artifact_sha256, artifact_schema, failure_code, deployment_status, "
        "deployment_scope, activation_mode, activated_at, deactivated_at, "
        "previous_active_run_id, activation_reason, started_at, completed_at"
        ") VALUES ("
        ":run_id, NULL, NULL, :dataset_sha, 2, 1, 1, '{}'::jsonb, '{}'::jsonb, "
        "'{}'::jsonb, 'eligible_candidate', :locator, :artifact_sha, "
        "'daibm.platt-calibration.v3', NULL, 'active', :scope, 'automatic', "
        "now(), NULL, NULL, 'test_activation', now(), now())"
    )
    with pytest.raises(IntegrityError):
        with migrated_engine.begin() as connection:
            for suffix in ("a", "b"):
                connection.execute(
                    statement,
                    {
                        "run_id": uuid.uuid4(),
                        "dataset_sha": suffix * 64,
                        "artifact_sha": ("c" if suffix == "a" else "d") * 64,
                        "locator": f"memory://{suffix}",
                        "scope": "controlled_demo",
                    },
                )


def test_database_allows_one_active_run_per_distinct_scope(migrated_engine):
    statement = sa.text(
        "INSERT INTO calibration_runs ("
        "calibration_run_id, dataset_sha256, sample_count, positive_count, "
        "negative_count, metrics_before, metrics_after, configuration, status, "
        "artifact_locator, artifact_sha256, artifact_schema, deployment_status, "
        "deployment_scope, activation_mode, activated_at, activation_reason, "
        "started_at, completed_at"
        ") VALUES ("
        ":run_id, :dataset_sha, 2, 1, 1, '{}'::jsonb, '{}'::jsonb, '{}'::jsonb, "
        "'eligible_candidate', :locator, :artifact_sha, "
        "'daibm.platt-calibration.v3', 'active', :scope, 'automatic', now(), "
        "'test_activation', now(), now())"
    )
    run_ids = [uuid.uuid4(), uuid.uuid4()]
    with migrated_engine.begin() as connection:
        for index, scope in enumerate(("controlled_demo", "external_verified")):
            connection.execute(
                statement,
                {
                    "run_id": run_ids[index],
                    "dataset_sha": str(index + 1) * 64,
                    "artifact_sha": str(index + 3) * 64,
                    "locator": f"memory://scope-{index}",
                    "scope": scope,
                },
            )
    try:
        with migrated_engine.connect() as connection:
            assert connection.scalar(
                sa.text(
                    "SELECT count(*) FROM calibration_runs "
                    "WHERE calibration_run_id = ANY(:run_ids)"
                ),
                {"run_ids": run_ids},
            ) == 2
    finally:
        with migrated_engine.begin() as connection:
            connection.execute(
                sa.text(
                    "DELETE FROM calibration_runs "
                    "WHERE calibration_run_id = ANY(:run_ids)"
                ),
                {"run_ids": run_ids},
            )


def test_downgrade_refuses_new_governance_history(monkeypatch):
    migration = _load_migration()

    class FakeBind:
        def scalar(self, _statement):
            return 1

    monkeypatch.setattr(migration.op, "get_bind", lambda: FakeBind())
    with pytest.raises(
        RuntimeError,
        match="governed lifecycle or calibration data",
    ):
        migration.downgrade()
