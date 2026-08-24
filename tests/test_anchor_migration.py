from __future__ import annotations

from sqlalchemy import inspect
from alembic.config import Config
from alembic.script import ScriptDirectory


def test_anchor_outbox_schema_is_present_at_alembic_head(migrated_engine):
    inspector = inspect(migrated_engine)

    assert "anchor_outbox" in inspector.get_table_names()
    columns = {column["name"]: column for column in inspector.get_columns("anchor_outbox")}
    assert set(columns) >= {
        "anchor_id",
        "ledger_event_id",
        "event_id",
        "subject_id",
        "event_hash",
        "chain_head_hash",
        "recorded_at",
        "schema_version",
        "model_version",
        "policy_version",
        "circuit_version",
        "proof_sha256",
        "status",
        "attempt_count",
        "next_attempt_at",
        "lease_token",
        "lease_expires_at",
        "last_error_code",
        "last_attempt_at",
        "anchored_at",
        "created_at",
        "updated_at",
    }
    assert all(columns[name]["nullable"] is False for name in (
        "anchor_id",
        "ledger_event_id",
        "event_id",
        "subject_id",
        "event_hash",
        "chain_head_hash",
        "recorded_at",
        "schema_version",
        "status",
        "attempt_count",
        "next_attempt_at",
        "created_at",
        "updated_at",
    ))

    foreign_keys = inspector.get_foreign_keys("anchor_outbox")
    assert any(
        foreign_key["referred_table"] == "ledger_events"
        and foreign_key["constrained_columns"] == ["ledger_event_id"]
        for foreign_key in foreign_keys
    )
    check_constraints = inspector.get_check_constraints("anchor_outbox")
    checks = " ".join(constraint["sqltext"] for constraint in check_constraints)
    for status in ("pending", "anchored", "permanent_failed"):
        assert status in checks
    indexes = {index["name"] for index in inspector.get_indexes("anchor_outbox")}
    assert "ix_anchor_outbox_pending_claim" in indexes
    check_names = {constraint["name"] for constraint in check_constraints}
    assert {
        "ck_anchor_outbox_model_version",
        "ck_anchor_outbox_policy_version",
        "ck_anchor_outbox_circuit_version",
    } <= check_names


def test_anchor_migration_follows_facility_ledger_events():
    scripts = ScriptDirectory.from_config(Config("alembic.ini"))
    revision = scripts.get_revision("20260824_0006")

    assert revision.down_revision == "20260824_0005a"
