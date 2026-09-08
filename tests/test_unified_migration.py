"""Both deployed PR histories must converge without rewriting their evidence."""

from __future__ import annotations

import uuid

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest
import sqlalchemy as sa

from test_integration_migration import _historical_shape
from test_lifecycle_governance_migration import _seed_orphan_outcome


MERGE_REVISION = "20260908_0014"
SOURCE_REVISIONS = ("20260907_0013", "20260907_0011")
EVIDENCE_TABLES = ("financing_requests", "actual_outcomes", "ledger_events", "anchor_outbox")


def _upgrade(engine, target):
    with engine.begin() as connection:
        config = Config("alembic.ini")
        config.attributes["connection"] = connection
        command.upgrade(config, target)


def _snapshot(engine, columns):
    with engine.connect() as connection:
        return {
            table: list(connection.scalars(sa.text(
                f"SELECT row_to_json(original)::text FROM "
                f"(SELECT {', '.join(names)} FROM {table}) original ORDER BY 1"
            )))
            for table, names in columns.items()
        }


def _seed_anchor(engine, circuit_version):
    anchor_id = uuid.uuid4()
    with engine.begin() as connection:
        event_id = connection.scalar(sa.text(
            "INSERT INTO ledger_events "
            "(created_at, event_type, entity_id, payload, previous_hash, event_hash) "
            "VALUES (now(), 'FINANCING_REQUEST', :subject, "
            "'{\"evidence\":\"历史 доказательство\"}', :previous, :hash) RETURNING id"
        ), {"subject": anchor_id, "previous": "0" * 64, "hash": "a" * 64})
        connection.execute(sa.text(
            "INSERT INTO anchor_outbox "
            "(anchor_id, ledger_event_id, event_id, subject_id, event_hash, chain_head_hash, "
            "recorded_at, circuit_version, proof_sha256, next_attempt_at, created_at, updated_at) "
            "VALUES (:id, :event, :id, :id, :hash, :hash, now(), :version, :proof, now(), now(), now())"
        ), {"id": anchor_id, "event": event_id, "hash": "a" * 64,
            "version": circuit_version, "proof": "b" * 64})


def _seed_request(engine):
    table = sa.Table("financing_requests", sa.MetaData(), autoload_with=engine)
    values = {
        "request_id": uuid.uuid4(), "created_at": sa.func.now(),
        "applicant_id": "historical-proof", "amount": 100, "term_days": 30,
        "features": {}, "risk_score": 0.2, "decision": "approved", "explanations": [],
        "control_action": "standard_monitoring", "confirmed_payable_amount": "123.45",
    }
    if "assessment_scope" in table.c:
        values["assessment_scope"] = "controlled_demo"
    with engine.begin() as connection:
        connection.execute(table.insert().values(**values))


def test_one_head_contains_both_unchanged_published_paths():
    scripts = ScriptDirectory.from_config(Config("alembic.ini"))
    head = scripts.get_current_head()
    ancestry = {revision.revision for revision in scripts.iterate_revisions(head, "base")}
    assert set(SOURCE_REVISIONS) | {MERGE_REVISION} <= ancestry
    assert set(scripts.get_revision(MERGE_REVISION).down_revision) == set(SOURCE_REVISIONS)
    assert scripts.get_revision("20260907_0011").down_revision == "20260824_0010"
    assert scripts.get_revision("20260824_0011").down_revision == "20260824_0010"


@pytest.mark.parametrize("source", ["lifecycle_pr5", "version_pr6"])
def test_upgrade_from_either_deployed_history_preserves_all_original_fields(
    isolated_postgres_engine, source,
):
    engine = isolated_postgres_engine
    if source == "version_pr6":
        _upgrade(engine, "20260824_0009")
        # Construct the historical proof-only 0010, not today's lifecycle 0010.
        _historical_shape(engine, "remote0010")
        _upgrade(engine, "20260907_0011")
        circuit_version = "invoice_limit@1"
    else:
        _upgrade(engine, "20260907_0013")
        circuit_version = None
    _seed_request(engine)
    _seed_orphan_outcome(engine)
    _seed_anchor(engine, circuit_version)
    columns = {
        table: [column["name"] for column in sa.inspect(engine).get_columns(table)]
        for table in EVIDENCE_TABLES
    }
    before = _snapshot(engine, columns)

    _upgrade(engine, "head")
    assert _snapshot(engine, columns) == before
    inspector = sa.inspect(engine)
    assert {"outcome_corrections", "calibration_jobs", "facility_defaults"} <= set(
        inspector.get_table_names()
    )
    assert "schedule_version" in {
        column["name"] for column in inspector.get_columns("facility_defaults")
    }
    with engine.connect() as connection:
        assert list(connection.scalars(sa.text("SELECT version_num FROM alembic_version"))) == [
            MERGE_REVISION
        ]
        assert connection.scalar(sa.text("SELECT circuit_version FROM anchor_outbox")) == circuit_version
    _upgrade(engine, "head")
    assert _snapshot(engine, columns) == before
