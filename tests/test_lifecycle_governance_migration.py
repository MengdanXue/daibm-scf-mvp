from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import uuid

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError, IntegrityError


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


def _seed_orphan_outcome(engine: sa.Engine) -> uuid.UUID:
    outcome_id = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            sa.text("ALTER TABLE actual_outcomes DISABLE TRIGGER ALL")
        )
        connection.execute(
            sa.text(
                "INSERT INTO actual_outcomes ("
                "outcome_id, facility_id, request_id, risk_assessment_id, "
                "model_version_id, submitted_by_user_id, idempotency_key, "
                "request_sha256, defaulted, days_past_due, loss_amount, "
                "observed_at, evidence_sha256, provenance, original_risk_score, "
                "risk_engine_version, risk_input_sha256, recorded_at"
                ") VALUES ("
                ":outcome_id, :facility_id, :request_id, :assessment_id, NULL, "
                ":user_id, :idempotency_key, :request_sha, false, 0, 0.00, "
                "now(), :evidence_sha, 'CONTROLLED_DEMO', 0.2, 'baseline-v1', "
                ":input_sha, now())"
            ),
            {
                "outcome_id": outcome_id,
                "facility_id": uuid.uuid4(),
                "request_id": uuid.uuid4(),
                "assessment_id": uuid.uuid4(),
                "user_id": uuid.uuid4(),
                "idempotency_key": uuid.uuid4(),
                "request_sha": "1" * 64,
                "evidence_sha": "2" * 64,
                "input_sha": "3" * 64,
            },
        )
        connection.execute(
            sa.text("ALTER TABLE actual_outcomes ENABLE TRIGGER ALL")
        )
    return outcome_id


def _delete_orphan_outcome(engine: sa.Engine, outcome_id: uuid.UUID) -> None:
    with engine.begin() as connection:
        connection.execute(
            sa.text("ALTER TABLE actual_outcomes DISABLE TRIGGER ALL")
        )
        connection.execute(
            sa.text("DELETE FROM actual_outcomes WHERE outcome_id = :outcome_id"),
            {"outcome_id": outcome_id},
        )
        connection.execute(
            sa.text("ALTER TABLE actual_outcomes ENABLE TRIGGER ALL")
        )


@pytest.fixture(autouse=True)
def _single_lender(migrated_engine):
    """Rows seeded here have no facility lineage; since revision 0021 they
    belong to the only lending organization, so one must exist."""

    with migrated_engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO organizations (organization_id, organization_code, name, "
                "organization_type, created_at) VALUES (:id, 'TEST-LENDER', 'Test lender', "
                "'financier', now()) ON CONFLICT DO NOTHING"
            ),
            {"id": uuid.UUID("00000000-0000-4000-8000-0000000000f1")},
        )


def _seed_result_run(engine: sa.Engine) -> uuid.UUID:
    run_id = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO calibration_runs ("
                "calibration_run_id, dataset_sha256, sample_count, positive_count, "
                "negative_count, configuration, status, failure_code, "
                "deployment_status, deployment_scope, activation_reason, "
                "started_at, completed_at"
                ") VALUES ("
                ":run_id, :dataset_sha, 1, 0, 1, '{}'::jsonb, 'failed', "
                "'test_rejection', 'not_deployed', 'controlled_demo', "
                "'test_rejection', now(), now())"
            ),
            {"run_id": run_id, "dataset_sha": uuid.uuid4().hex * 2},
        )
    return run_id


def _delete_job_graph(
    engine: sa.Engine,
    *,
    outcome_id: uuid.UUID,
    run_id: uuid.UUID,
) -> None:
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "DELETE FROM calibration_jobs "
                "WHERE trigger_outcome_id = :outcome_id"
            ),
            {"outcome_id": outcome_id},
        )
        connection.execute(
            sa.text(
                "DELETE FROM calibration_runs "
                "WHERE calibration_run_id = :run_id"
            ),
            {"run_id": run_id},
        )
    _delete_orphan_outcome(engine, outcome_id)


def _seed_guarded_legacy_value(
    engine: sa.Engine,
    *,
    category: str,
    value: str,
) -> tuple[str, object]:
    if category == "facility_status":
        table_name = "financing_facilities"
        key: object = uuid.uuid4()
        statement = sa.text(
            "INSERT INTO financing_facilities ("
            "facility_id, request_id, principal, outstanding_amount, currency, "
            "status, version, current_schedule_version, created_by_user_id, "
            "created_at, updated_at, organization_id"
            ") VALUES ("
            ":key, :request_id, 100.00, 100.00, 'USD', :value, 1, 1, "
            ":user_id, now(), now(), :organization_id)"
        )
        parameters = {
            "key": key,
            "request_id": uuid.uuid4(),
            "user_id": uuid.uuid4(),
            "organization_id": uuid.uuid4(),
            "value": value,
        }
    else:
        table_name = "facility_actions"
        key = uuid.uuid4()
        statement = sa.text(
            "INSERT INTO facility_actions ("
            "facility_id, actor_user_id, actor_role, action_type, "
            "idempotency_key, expected_version, resulting_version, payload, "
            "created_at"
            ") VALUES ("
            ":facility_id, :user_id, 'auditor', :value, :key, 1, 2, "
            "'{}'::jsonb, now())"
        )
        parameters = {
            "facility_id": uuid.uuid4(),
            "user_id": uuid.uuid4(),
            "key": key,
            "value": value,
        }

    with engine.begin() as connection:
        connection.execute(sa.text(f"ALTER TABLE {table_name} DISABLE TRIGGER ALL"))
        connection.execute(statement, parameters)
        connection.execute(sa.text(f"ALTER TABLE {table_name} ENABLE TRIGGER ALL"))
    return table_name, key


def _delete_guarded_legacy_value(
    engine: sa.Engine,
    *,
    table_name: str,
    key: object,
) -> None:
    if table_name == "financing_facilities":
        predicate = "facility_id = :key"
    else:
        predicate = "idempotency_key = :key"
    with engine.begin() as connection:
        connection.execute(sa.text(f"ALTER TABLE {table_name} DISABLE TRIGGER ALL"))
        connection.execute(
            sa.text(f"DELETE FROM {table_name} WHERE {predicate}"),
            {"key": key},
        )
        connection.execute(sa.text(f"ALTER TABLE {table_name} ENABLE TRIGGER ALL"))


def test_lifecycle_governance_revision_is_single_head_and_constrained(
    migrated_engine,
):
    scripts = ScriptDirectory.from_config(Config("alembic.ini"))
    revision = scripts.get_revision("20260824_0010")
    assert revision.down_revision == "20260824_0009"
    recovery_revision = scripts.get_revision("20260824_0011")
    assert recovery_revision.down_revision == "20260824_0010"
    assert "20260907_0013" in {
        item.revision for item in scripts.iterate_revisions(scripts.get_current_head(), "base")
    }

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
    assert run_indexes["ix_calibration_runs_scope_dataset"]["column_names"] == [
        "deployment_scope",
        "dataset_sha256",
    ]
    assert run_indexes["uq_calibration_runs_active_scope"]["unique"] is True
    assert run_indexes["uq_calibration_runs_active_scope"]["column_names"] == [
        # One ACTIVE run per organization and scope since revision 0021.
        "organization_id",
        "deployment_scope",
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
        index_tuples = {
            tuple(index.get("column_names") or [])
            for index in inspector.get_indexes(table_name)
        }
        index_tuples |= {
            tuple(constraint.get("column_names") or [])
            for constraint in inspector.get_unique_constraints(table_name)
        }
        index_tuples.add(
            tuple(
                inspector.get_pk_constraint(table_name).get(
                    "constrained_columns", []
                )
            )
        )
        for foreign_key in inspector.get_foreign_keys(table_name):
            constrained = tuple(foreign_key["constrained_columns"])
            assert any(
                candidate[: len(constrained)] == constrained
                for candidate in index_tuples
            ), (table_name, constrained, index_tuples)

    run_foreign_keys = _named(inspector.get_foreign_keys("calibration_runs"))
    assert run_foreign_keys["fk_calibration_runs_trigger_job"][
        "constrained_columns"
    ] == ["trigger_job_id"]
    job_foreign_keys = _named(inspector.get_foreign_keys("calibration_jobs"))
    assert job_foreign_keys["fk_calibration_jobs_result_run"][
        "constrained_columns"
    ] == ["result_run_id"]


def test_calibration_recovery_migration_roundtrips_on_real_postgresql(isolated_postgres_engine):
    engine = isolated_postgres_engine
    config = Config(str(ROOT / "alembic.ini"))
    with engine.connect() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "20260824_0010")
        before = {
            item["name"]
            for item in sa.inspect(connection).get_unique_constraints(
                "calibration_runs"
            )
        }
        assert "calibration_runs_dataset_sha256_key" in before

        command.upgrade(config, "20260824_0011")
        upgraded = sa.inspect(connection)
        after = {
            item["name"]
            for item in upgraded.get_unique_constraints("calibration_runs")
        }
        indexes = {
            item["name"]: item
            for item in upgraded.get_indexes("calibration_runs")
        }
        assert "calibration_runs_dataset_sha256_key" not in after
        assert indexes["ix_calibration_runs_scope_dataset"][
            "column_names"
        ] == ["deployment_scope", "dataset_sha256"]

        command.downgrade(config, "20260824_0010")
        restored = {
            item["name"]
            for item in sa.inspect(connection).get_unique_constraints(
                "calibration_runs"
            )
        }
        assert "calibration_runs_dataset_sha256_key" in restored


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
    with pytest.raises(IntegrityError, match="uq_calibration_runs_active_scope"):
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
        # Revision 0016 forbids deleting an ACTIVE model, so fixture cleanup
        # truncates (row triggers do not fire) instead of deleting rows.
        with migrated_engine.begin() as connection:
            connection.execute(sa.text("TRUNCATE calibration_runs CASCADE"))


def test_database_rejects_active_legacy_mixed_scope(migrated_engine):
    connection = migrated_engine.connect()
    transaction = connection.begin()
    try:
        with pytest.raises(IntegrityError) as captured:
            connection.execute(
                sa.text(
                    "INSERT INTO calibration_runs ("
                    "calibration_run_id, dataset_sha256, sample_count, "
                    "positive_count, negative_count, metrics_before, "
                    "metrics_after, configuration, status, artifact_locator, "
                    "artifact_sha256, artifact_schema, deployment_status, "
                    "deployment_scope, activation_mode, activated_at, "
                    "activation_reason, started_at, completed_at"
                    ") VALUES ("
                    ":run_id, :dataset_sha, 2, 1, 1, '{}'::jsonb, '{}'::jsonb, "
                    "'{}'::jsonb, 'eligible_candidate', 'memory://mixed', "
                    ":artifact_sha, 'daibm.platt-calibration.v2', 'active', "
                    "'mixed', 'automatic', now(), 'test_activation', now(), now())"
                ),
                {
                    "run_id": uuid.uuid4(),
                    "dataset_sha": "6" * 64,
                    "artifact_sha": "7" * 64,
                },
            )
        assert "ck_calibration_runs_deployment_contract" in str(
            captured.value.orig
        )
    finally:
        transaction.rollback()
        connection.close()


_INVALID_JOB_CASES = (
    "queued_at_retry_limit",
    "queued_with_started_at",
    "running_with_zero_attempts",
    "running_without_started_at",
    "running_with_blank_lease_owner",
    "running_with_expired_initial_lease",
    "completed_with_zero_attempts",
    "completed_without_result",
    "completed_without_started_at",
    "completed_before_started_at",
    "failed_with_zero_attempts",
    "failed_without_failure_code",
    "failed_with_blank_failure_code",
    "failed_without_started_at",
    "failed_before_started_at",
)


@pytest.mark.parametrize("case", _INVALID_JOB_CASES)
def test_database_rejects_incomplete_calibration_job_states(
    migrated_engine,
    case,
):
    outcome_id = _seed_orphan_outcome(migrated_engine)
    run_id = _seed_result_run(migrated_engine)
    now = datetime.now(timezone.utc)
    status = case.split("_", 1)[0]
    states = {
        "queued": {
            "attempt_count": 0,
            "lease_owner": None,
            "leased_until": None,
            "started_at": None,
            "completed_at": None,
            "failure_code": None,
            "result_run_id": None,
        },
        "running": {
            "attempt_count": 1,
            "lease_owner": "worker-a",
            "leased_until": now + timedelta(minutes=5),
            "started_at": now,
            "completed_at": None,
            "failure_code": None,
            "result_run_id": None,
        },
        "completed": {
            "attempt_count": 1,
            "lease_owner": None,
            "leased_until": None,
            "started_at": now,
            "completed_at": now + timedelta(seconds=1),
            "failure_code": None,
            "result_run_id": run_id,
        },
        "failed": {
            "attempt_count": 1,
            "lease_owner": None,
            "leased_until": None,
            "started_at": now,
            "completed_at": now + timedelta(seconds=1),
            "failure_code": "calibration_infrastructure_failure",
            "result_run_id": None,
        },
    }
    values = states[status]
    if case == "queued_at_retry_limit":
        values["attempt_count"] = 3
    elif case == "queued_with_started_at":
        values["started_at"] = now
    elif case == "running_with_zero_attempts":
        values["attempt_count"] = 0
    elif case == "running_without_started_at":
        values["started_at"] = None
    elif case == "running_with_blank_lease_owner":
        values["lease_owner"] = ""
    elif case == "running_with_expired_initial_lease":
        values["leased_until"] = now - timedelta(seconds=1)
    elif case == "completed_with_zero_attempts":
        values["attempt_count"] = 0
    elif case == "completed_without_result":
        values["result_run_id"] = None
    elif case == "completed_without_started_at":
        values["started_at"] = None
    elif case == "completed_before_started_at":
        values["completed_at"] = now - timedelta(seconds=1)
    elif case == "failed_with_zero_attempts":
        values["attempt_count"] = 0
    elif case == "failed_without_failure_code":
        values["failure_code"] = None
    elif case == "failed_with_blank_failure_code":
        values["failure_code"] = ""
    elif case == "failed_without_started_at":
        values["started_at"] = None
    elif case == "failed_before_started_at":
        values["completed_at"] = now - timedelta(seconds=1)

    connection = migrated_engine.connect()
    transaction = connection.begin()
    try:
        with pytest.raises(IntegrityError) as captured:
            connection.execute(
                sa.text(
                    "INSERT INTO calibration_jobs ("
                    "job_id, deployment_scope, trigger_type, trigger_outcome_id, "
                    "trigger_correction_id, idempotency_key, status, attempt_count, "
                    "lease_owner, leased_until, failure_code, result_run_id, "
                    "created_at, started_at, completed_at"
                    ") VALUES ("
                    ":job_id, 'controlled_demo', 'outcome_submitted', :outcome_id, "
                    "NULL, :idempotency_key, :status, :attempt_count, :lease_owner, "
                    ":leased_until, :failure_code, :result_run_id, :created_at, "
                    ":started_at, :completed_at)"
                ),
                {
                    "job_id": uuid.uuid4(),
                    "outcome_id": outcome_id,
                    "idempotency_key": uuid.uuid4(),
                    "status": status,
                    "created_at": now,
                    **values,
                },
            )
        assert "ck_calibration_jobs_contract" in str(captured.value.orig)
    finally:
        transaction.rollback()
        connection.close()
        _delete_job_graph(
            migrated_engine,
            outcome_id=outcome_id,
            run_id=run_id,
        )


def test_database_accepts_complete_calibration_job_states(migrated_engine):
    outcome_id = _seed_orphan_outcome(migrated_engine)
    run_id = _seed_result_run(migrated_engine)
    now = datetime.now(timezone.utc)
    rows = (
        ("queued", 0, None, None, None, None, None),
        (
            "running",
            1,
            "worker-a",
            now + timedelta(minutes=5),
            now,
            None,
            None,
        ),
        (
            "completed",
            1,
            None,
            None,
            now,
            now + timedelta(seconds=1),
            None,
        ),
        (
            "failed",
            1,
            None,
            None,
            now,
            now + timedelta(seconds=1),
            "calibration_infrastructure_failure",
        ),
    )
    connection = migrated_engine.connect()
    transaction = connection.begin()
    try:
        for status, attempt, owner, lease, started, completed, failure in rows:
            connection.execute(
                sa.text(
                    "INSERT INTO calibration_jobs ("
                    "job_id, deployment_scope, trigger_type, trigger_outcome_id, "
                    "idempotency_key, status, attempt_count, lease_owner, "
                    "leased_until, failure_code, result_run_id, created_at, "
                    "started_at, completed_at"
                    ") VALUES ("
                    ":job_id, 'controlled_demo', 'outcome_submitted', :outcome_id, "
                    ":idempotency_key, :status, :attempt_count, :lease_owner, "
                    ":leased_until, :failure_code, :result_run_id, :created_at, "
                    ":started_at, :completed_at)"
                ),
                {
                    "job_id": uuid.uuid4(),
                    "outcome_id": outcome_id,
                    "idempotency_key": uuid.uuid4(),
                    "status": status,
                    "attempt_count": attempt,
                    "lease_owner": owner,
                    "leased_until": lease,
                    "failure_code": failure,
                    "result_run_id": run_id if status == "completed" else None,
                    "created_at": now,
                    "started_at": started,
                    "completed_at": completed,
                },
            )
        assert connection.scalar(
            sa.text(
                "SELECT count(*) FROM calibration_jobs "
                "WHERE trigger_outcome_id = :outcome_id"
            ),
            {"outcome_id": outcome_id},
        ) == 4
    finally:
        transaction.rollback()
        connection.close()
        _delete_job_graph(
            migrated_engine,
            outcome_id=outcome_id,
            run_id=run_id,
        )


def test_downgrade_refuses_new_governance_history(monkeypatch):
    migration = _load_migration()

    class FakeBind:
        def execute(self, _statement):
            return None

        def scalar(self, _statement):
            return 1

    monkeypatch.setattr(migration.op, "get_bind", lambda: FakeBind())
    with pytest.raises(
        RuntimeError,
        match="governed lifecycle or calibration data",
    ):
        migration.downgrade()


def test_downgrade_locks_every_guarded_table_before_counting(
    migrated_engine,
    monkeypatch,
):
    migration = _load_migration()
    expected_tables = {
        "ledger_events",
        "financing_requests",
        "financing_facilities",
        "facility_installments",
        "facility_actions",
        "facility_delinquencies",
        "facility_restructures",
        "facility_defaults",
        "facility_writeoffs",
        "outcome_corrections",
        "calibration_jobs",
        "calibration_runs",
        "calibration_run_observations",
    }
    operations: list[str] = []
    connection = migrated_engine.connect()
    transaction = connection.begin()
    backend_pid = connection.scalar(sa.text("SELECT pg_backend_pid()"))

    class RecordingBind:
        def execute(self, statement):
            operations.append("lock")
            return connection.execute(statement)

        def scalar(self, _statement):
            operations.append("count")
            return 1

    monkeypatch.setattr(migration.op, "get_bind", lambda: RecordingBind())
    try:
        with pytest.raises(
            RuntimeError,
            match="governed lifecycle or calibration data",
        ):
            migration.downgrade()

        assert operations[:2] == ["lock", "count"]
        with migrated_engine.connect() as observer:
            locked_tables = set(
                observer.scalars(
                    sa.text(
                        "SELECT relation::regclass::text "
                        "FROM pg_locks "
                        "WHERE pid = :pid AND locktype = 'relation' "
                        "AND mode = 'AccessExclusiveLock' AND granted"
                    ),
                    {"pid": backend_pid},
                )
            )
        assert expected_tables <= locked_tables

        with migrated_engine.connect() as writer:
            writer_transaction = writer.begin()
            try:
                writer.execute(sa.text("SET LOCAL lock_timeout = '100ms'"))
                with pytest.raises(DBAPIError) as captured:
                    writer.execute(
                        sa.text(
                            "INSERT INTO calibration_jobs (job_id) VALUES (:job_id)"
                        ),
                        {"job_id": uuid.uuid4()},
                    )
                assert captured.value.orig.sqlstate == "55P03"
            finally:
                writer_transaction.rollback()
    finally:
        transaction.rollback()
        connection.close()


@pytest.mark.parametrize(
    ("category", "value"),
    (
        ("facility_status", "restructured"),
        ("facility_status", "defaulted"),
        ("facility_status", "written_off"),
        ("facility_action", "restructure"),
        ("facility_action", "declare_default"),
        ("facility_action", "write_off"),
    ),
)
def test_downgrade_refuses_values_removed_by_revision_0009_checks(
    migrated_engine,
    monkeypatch,
    category,
    value,
):
    migration = _load_migration()
    table_name, key = _seed_guarded_legacy_value(
        migrated_engine,
        category=category,
        value=value,
    )
    connection = migrated_engine.connect()
    transaction = connection.begin()
    monkeypatch.setattr(migration.op, "get_bind", lambda: connection)
    try:
        with pytest.raises(
            RuntimeError,
            match="governed lifecycle or calibration data",
        ):
            migration.downgrade()
    finally:
        transaction.rollback()
        connection.close()
        _delete_guarded_legacy_value(
            migrated_engine,
            table_name=table_name,
            key=key,
        )


_IMMUTABLE_HISTORY_CASES = (
    ("facility_delinquencies", "delinquency_id"),
    ("facility_restructures", "restructure_id"),
    ("facility_defaults", "default_id"),
    ("facility_writeoffs", "writeoff_id"),
    ("outcome_corrections", "correction_id"),
    ("calibration_run_observations", "calibration_run_id"),
)


def _insert_immutable_history_row(
    connection,
    table_name: str,
) -> dict[str, uuid.UUID]:
    identifiers = {
        "row_id": uuid.uuid4(),
        "facility_id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "outcome_id": uuid.uuid4(),
    }
    statements = {
        "facility_delinquencies": (
            "INSERT INTO facility_delinquencies ("
            "delinquency_id, facility_id, marked_by_user_id, days_past_due, "
            "reason_code, comment, evidence_sha256, recorded_at"
            ") VALUES (:row_id, :facility_id, :user_id, 1, 'late', 'test', "
            ":evidence_sha, now())"
        ),
        "facility_restructures": (
            "INSERT INTO facility_restructures ("
            "restructure_id, facility_id, restructured_by_user_id, "
            "old_schedule_version, new_schedule_version, reason_code, "
            "comment, evidence_sha256, recorded_at"
            ") VALUES (:row_id, :facility_id, :user_id, 1, 2, 'terms', "
            "'test', :evidence_sha, now())"
        ),
        "facility_defaults": (
            "INSERT INTO facility_defaults ("
            "default_id, facility_id, declared_by_user_id, defaulted_at, "
            "days_past_due, reason_code, comment, evidence_sha256, recorded_at"
            ") VALUES (:row_id, :facility_id, :user_id, now(), 1, 'default', "
            "'test', :evidence_sha, now())"
        ),
        "facility_writeoffs": (
            "INSERT INTO facility_writeoffs ("
            "writeoff_id, facility_id, amount, auditor_user_id, reason_code, "
            "comment, evidence_sha256, recorded_at"
            ") VALUES (:row_id, :facility_id, 1.00, :user_id, 'loss', 'test', "
            ":evidence_sha, now())"
        ),
        "outcome_corrections": (
            "INSERT INTO outcome_corrections ("
            "correction_id, outcome_id, action, reason_code, comment, "
            "evidence_sha256, auditor_user_id, idempotency_key, request_sha256, "
            "recorded_at"
            ") VALUES (:row_id, :outcome_id, 'EXCLUDE', 'invalid', 'test', "
            ":evidence_sha, :user_id, :idempotency_key, :request_sha, now())"
        ),
        "calibration_run_observations": (
            "INSERT INTO calibration_run_observations ("
            "calibration_run_id, outcome_id, correction_head_id"
            ") VALUES (:row_id, :outcome_id, NULL)"
        ),
    }
    connection.execute(
        sa.text(statements[table_name]),
        {
            **identifiers,
            "evidence_sha": "8" * 64,
            "request_sha": "9" * 64,
            "idempotency_key": uuid.uuid4(),
        },
    )
    return identifiers


@pytest.mark.parametrize(("table_name", "pk_column"), _IMMUTABLE_HISTORY_CASES)
def test_governed_history_rejects_update_and_delete(
    migrated_engine,
    table_name,
    pk_column,
):
    with migrated_engine.begin() as connection:
        connection.execute(sa.text(f"ALTER TABLE {table_name} DISABLE TRIGGER ALL"))
        identifiers = _insert_immutable_history_row(connection, table_name)
        connection.execute(sa.text(f"ALTER TABLE {table_name} ENABLE TRIGGER ALL"))

    predicate = f"{pk_column} = :row_id"
    if table_name == "calibration_run_observations":
        predicate += " AND outcome_id = :outcome_id"
    try:
        with pytest.raises(IntegrityError) as update_error:
            with migrated_engine.begin() as connection:
                connection.execute(
                    sa.text(
                        f"UPDATE {table_name} SET {pk_column} = {pk_column} "
                        f"WHERE {predicate}"
                    ),
                    identifiers,
                )
        assert "governed history is immutable" in str(update_error.value.orig)

        with pytest.raises(IntegrityError) as delete_error:
            with migrated_engine.begin() as connection:
                connection.execute(
                    sa.text(f"DELETE FROM {table_name} WHERE {predicate}"),
                    identifiers,
                )
        assert "governed history is immutable" in str(delete_error.value.orig)
    finally:
        with migrated_engine.begin() as connection:
            connection.execute(sa.text(f"ALTER TABLE {table_name} DISABLE TRIGGER ALL"))
            connection.execute(
                sa.text(f"DELETE FROM {table_name} WHERE {predicate}"),
                identifiers,
            )
            connection.execute(sa.text(f"ALTER TABLE {table_name} ENABLE TRIGGER ALL"))
