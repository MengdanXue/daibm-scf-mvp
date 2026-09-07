from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.models_advanced import AnchorOutboxModel
from app.repositories.anchors import AnchorOutboxRepository, _safe_version
from app.repositories.ledger import LedgerRepository


VECTORS = json.loads(
    (Path(__file__).parents[1] / "advanced/contracts/anchor-version-tokens.json")
    .read_text(encoding="utf-8")
)
FIELDS = ("model_version", "policy_version", "circuit_version")


@pytest.mark.parametrize("value", VECTORS["valid"])
def test_application_preserves_valid_version_tokens(value):
    assert _safe_version(value) == value


@pytest.mark.parametrize("value", VECTORS["invalid"])
def test_application_omits_invalid_version_tokens(value):
    assert _safe_version(value) is None


def test_new_proof_event_keeps_version_but_reenqueue_does_not_backfill(session_factory):
    repo = AnchorOutboxRepository()
    with session_factory.begin() as session:
        event = LedgerRepository().append_many(
            session, uuid.uuid4(), [("TRADE_CONFIRMED", {
                "circuit_version": "invoice_limit@1", "proof_sha256": "b" * 64,
            })],
        )[0]
        row = session.scalar(select(AnchorOutboxModel))
        assert row.circuit_version == "invoice_limit@1"
        assert repo.envelope(row)["circuitVersion"] == "invoice_limit@1"
        assert repo.envelope(row)["proofSha256"] == "b" * 64
        # Represent a pre-fix anchor in this isolated test database only.
        row.circuit_version = None
        session.flush()
        before = repo.envelope(row)
        assert repo.envelope(repo.enqueue(session, event)) == before
        assert event.payload["circuit_version"] == "invoice_limit@1"


@pytest.mark.parametrize("field", FIELDS)
def test_postgres_enforces_shared_version_contract(session_factory, field):
    with session_factory.begin() as session:
        LedgerRepository().append_many(
            session, uuid.uuid4(), [("FINANCING_REQUEST", {})],
        )
        row = session.scalar(select(AnchorOutboxModel))
        for value in VECTORS["valid"]:
            session.execute(text(f"UPDATE anchor_outbox SET {field} = :value"), {"value": value})
        for value in VECTORS["invalid"]:
            if not isinstance(value, str):
                continue  # SQL column is nullable TEXT; JSON type checks live at the boundary.
            with pytest.raises(IntegrityError):
                with session.begin_nested():
                    session.execute(text(f"UPDATE anchor_outbox SET {field} = :value"), {"value": value})
        session.refresh(row)
        assert getattr(row, field) == VECTORS["valid"][-1]


def test_version_migration_preserves_history_and_refuses_lossy_downgrade(session_factory, migrated_engine):
    config = Config("alembic.ini")
    assert ScriptDirectory.from_config(config).get_current_head() == "20260907_0011"
    with session_factory.begin() as session:
        LedgerRepository().append_many(session, uuid.uuid4(), [("FINANCING_REQUEST", {})])
    snapshot_sql = text(
        "SELECT circuit_version, event_hash, schema_version, status "
        "FROM anchor_outbox"
    )
    ledger_sql = text("SELECT event_hash, payload FROM ledger_events")
    with migrated_engine.begin() as connection:
        config.attributes["connection"] = connection
        before = connection.execute(snapshot_sql).mappings().one()
        ledger_before = connection.execute(ledger_sql).mappings().one()
        command.downgrade(config, "20260824_0010")
        command.upgrade(config, "head")
        assert connection.execute(snapshot_sql).mappings().one() == before
        assert connection.execute(ledger_sql).mappings().one() == ledger_before
        connection.execute(text("UPDATE anchor_outbox SET circuit_version = 'invoice_limit@1'"))
    with pytest.raises(RuntimeError, match="Refusing to downgrade"):
        with migrated_engine.begin() as connection:
            config.attributes["connection"] = connection
            command.downgrade(config, "20260824_0010")
    with migrated_engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20260907_0011"
        assert connection.execute(snapshot_sql).mappings().one()["circuit_version"] == "invoice_limit@1"
        assert connection.execute(ledger_sql).mappings().one() == ledger_before
