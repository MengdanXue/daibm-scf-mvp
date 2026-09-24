"""Upgrade the two historical 0010 shapes on disposable PostgreSQL databases."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from decimal import Decimal
import uuid

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
import pytest
import sqlalchemy as sa

from test_lifecycle_governance_migration import _seed_orphan_outcome


def test_integration_has_no_duplicate_revision_identity():
    scripts = ScriptDirectory.from_config(Config("alembic.ini"))
    revisions = list(scripts.walk_revisions())
    assert len({item.revision for item in revisions}) == len(revisions)
    assert "20260907_0013" in {
        item.revision for item in scripts.iterate_revisions(scripts.get_current_head(), "base")
    }


@pytest.fixture
def historical_engine(isolated_postgres_engine):
    with isolated_postgres_engine.begin() as connection:
        _upgrade(connection, "20260824_0009")
    yield isolated_postgres_engine


def _upgrade(connection, target="head"):
    config = Config("alembic.ini")
    config.attributes["connection"] = connection
    command.upgrade(config, target)


def _historical_shape(engine, shape):
    with engine.begin() as connection:
        if shape.startswith("local"):
            path = Path("alembic/versions/20260824_0010_lifecycle_corrections_scope.py")
            spec = importlib.util.spec_from_file_location("historical_lifecycle", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            with Operations.context(MigrationContext.configure(connection)):
                module.upgrade()
        elif shape == "remote0010":
            connection.execute(
                sa.text(
                    "ALTER TABLE financing_requests ADD COLUMN confirmed_payable_amount numeric(14,2)"
                )
            )
            connection.execute(
                sa.text(
                    "ALTER TABLE financing_requests ADD CONSTRAINT ck_financing_requests_confirmed_payable_amount CHECK (confirmed_payable_amount IS NULL OR confirmed_payable_amount > 0)"
                )
            )
            connection.execute(
                sa.text(
                    "ALTER TABLE financing_requests ADD CONSTRAINT ck_financing_requests_payable_covers_amount CHECK (confirmed_payable_amount IS NULL OR confirmed_payable_amount >= amount)"
                )
            )
        if shape == "local0011":
            connection.execute(
                sa.text(
                    "ALTER TABLE calibration_runs DROP CONSTRAINT calibration_runs_dataset_sha256_key"
                )
            )
            connection.execute(
                sa.text(
                    "CREATE INDEX ix_calibration_runs_scope_dataset ON calibration_runs (deployment_scope, dataset_sha256)"
                )
            )
        if shape != "baseline0009":
            revision = "20260824_0011" if shape == "local0011" else "20260824_0010"
            connection.execute(
                sa.text("UPDATE alembic_version SET version_num = :revision"),
                {"revision": revision},
            )


def _snapshot(engine):
    with engine.connect() as connection:
        return tuple(
            tuple(
                connection.scalars(sa.text(f"SELECT {projection} FROM {table} t ORDER BY 1"))
            )
            for table, projection in (
                ("ledger_events", "row_to_json(t)::text"),
                # Revision 0016 appends revision columns; every original byte
                # must still be identical.
                (
                    "actual_outcomes",
                    "(to_jsonb(t) - 'revision' - 'supersedes_outcome_id' "
                    "- 'correction_reason_code' - 'correction_comment')::text",
                ),
            )
        )


def _seed_proof_request(engine):
    request_id = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO financing_requests (request_id, created_at, applicant_id, amount, term_days, features, risk_score, decision, explanations, control_action, confirmed_payable_amount) VALUES (:id, now(), 'historical-proof', 100.00, 30, '{}', 0.2, 'approved', '[]', 'standard_monitoring', 123.45)"
            ),
            {"id": request_id},
        )
    return request_id


def test_remote_payable_ceiling_survives_reconciliation(historical_engine):
    engine = historical_engine
    _historical_shape(engine, "remote0010")
    request_id = _seed_proof_request(engine)
    with engine.begin() as connection:
        _upgrade(connection)
        assert connection.scalar(
            sa.text(
                "SELECT confirmed_payable_amount FROM financing_requests WHERE request_id = :id"
            ),
            {"id": request_id},
        ) == Decimal("123.45")


def test_proof_downgrade_locks_before_refusing_historical_values(historical_engine, monkeypatch):
    engine = historical_engine
    _historical_shape(engine, "remote0010")
    _seed_proof_request(engine)
    with engine.begin() as connection:
        _upgrade(connection)
    path = Path("alembic/versions/20260907_0012_confirmed_payable_proof.py")
    spec = importlib.util.spec_from_file_location("proof_downgrade", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with engine.connect() as connection:
        transaction = connection.begin()
        monkeypatch.setattr(module.op, "get_bind", lambda: connection)
        with pytest.raises(RuntimeError, match="Refusing to drop confirmed payable"):
            module.downgrade()
        assert (
            connection.scalar(
                sa.text(
                    "SELECT count(*) FROM pg_locks WHERE pid = pg_backend_pid() AND relation = 'financing_requests'::regclass AND mode = 'AccessExclusiveLock' AND granted"
                )
            )
            == 1
        )
        transaction.rollback()


@pytest.mark.parametrize("shape", ["baseline0009", "remote0010", "local0010", "local0011"])
def test_upgrade_preserves_immutable_bytes_and_both_feature_schemas(historical_engine, shape):
    engine = historical_engine
    _historical_shape(engine, shape)
    _seed_orphan_outcome(engine)
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO ledger_events (created_at, event_type, entity_id, payload, previous_hash, event_hash) VALUES (now(), 'FINANCING_REQUEST', :id, '{\"immutable\":\"历史 доказательство\"}', :previous, :hash)"
            ),
            {"id": uuid.uuid4(), "previous": "0" * 64, "hash": "1" * 64},
        )
    before = _snapshot(engine)
    with engine.begin() as connection:
        _upgrade(connection)
    inspector = sa.inspect(engine)
    assert {"outcome_corrections", "calibration_jobs", "facility_restructures"} <= set(
        inspector.get_table_names()
    )
    assert "confirmed_payable_amount" in {
        item["name"] for item in inspector.get_columns("financing_requests")
    }
    assert "assessment_scope" in {
        item["name"] for item in inspector.get_columns("financing_requests")
    }
    assert "ix_calibration_runs_scope_dataset" in {
        item["name"] for item in inspector.get_indexes("calibration_runs")
    }
    assert _snapshot(engine) == before
    with engine.begin() as connection:
        _upgrade(connection)
    assert _snapshot(engine) == before


@pytest.mark.parametrize(
    "damage", ["unknown", "partial_proof", "partial_lifecycle", "missing_job_constraint"]
)
def test_upgrade_refuses_unknown_or_partial_shapes_without_advancing(historical_engine, damage):
    engine = historical_engine
    _historical_shape(engine, "local0010" if damage == "missing_job_constraint" else "baseline0009")
    with engine.begin() as connection:
        if damage == "partial_proof":
            connection.execute(
                sa.text(
                    "ALTER TABLE financing_requests ADD COLUMN confirmed_payable_amount numeric(14,2)"
                )
            )
        if damage == "partial_lifecycle":
            connection.execute(
                sa.text("ALTER TABLE financing_requests ADD COLUMN assessment_scope text")
            )
        if damage == "missing_job_constraint":
            connection.execute(
                sa.text("ALTER TABLE calibration_jobs DROP CONSTRAINT ck_calibration_jobs_contract")
            )
        connection.execute(sa.text("UPDATE alembic_version SET version_num = '20260824_0010'"))
    with pytest.raises(RuntimeError, match="incompatible historical schema"):
        with engine.begin() as connection:
            _upgrade(connection)
    with engine.connect() as connection:
        assert (
            connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == "20260824_0010"
        )
