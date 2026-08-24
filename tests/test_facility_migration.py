from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from testcontainers.community.postgres import PostgresContainer


def _constraint_definition(connection) -> str:
    result = connection.scalar(
        text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conname = 'ck_ledger_events_event_type'"
        )
    )
    assert result is not None
    return result


def test_0005a_upgrades_an_existing_0005_database_and_downgrades_when_empty():
    with PostgresContainer("postgres:17-alpine") as postgres:
        url = make_url(postgres.get_connection_url()).set(
            drivername="postgresql+psycopg",
            host="127.0.0.1",
        )
        engine = create_engine(url, pool_pre_ping=True)
        config = Config("alembic.ini")
        with engine.connect() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "20260824_0005")
            assert "FACILITY_CREATED" not in _constraint_definition(connection)

            command.upgrade(config, "20260824_0005a")
            assert "FACILITY_CREATED" in _constraint_definition(connection)

            command.downgrade(config, "20260824_0005")
            assert "FACILITY_CREATED" not in _constraint_definition(connection)
        engine.dispose()


def test_0005a_downgrade_refuses_to_orphan_existing_facility_ledger_events():
    with PostgresContainer("postgres:17-alpine") as postgres:
        url = make_url(postgres.get_connection_url()).set(
            drivername="postgresql+psycopg",
            host="127.0.0.1",
        )
        engine = create_engine(url, pool_pre_ping=True)
        config = Config("alembic.ini")
        with engine.connect() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "20260824_0005a")
            connection.execute(
                text(
                    "INSERT INTO ledger_events "
                    "(created_at, stream_id, event_type, entity_id, payload, "
                    "previous_hash, event_hash) VALUES "
                    "(:created_at, 'global', 'FACILITY_CREATED', :entity_id, "
                    "'{}'::jsonb, 'GENESIS', :event_hash)"
                ),
                {
                    "created_at": datetime.now(timezone.utc),
                    "entity_id": uuid.uuid4(),
                    "event_hash": "a" * 64,
                },
            )
            connection.commit()

            with pytest.raises(
                RuntimeError,
                match="facility ledger events exist",
            ):
                command.downgrade(config, "20260824_0005")

        with engine.connect() as verification:
            assert verification.scalar(
                text(
                    "SELECT count(*) FROM ledger_events "
                    "WHERE event_type = 'FACILITY_CREATED'"
                )
            ) == 1
            assert verification.scalar(
                text("SELECT version_num FROM alembic_version")
            ) == "20260824_0005a"
            assert "FACILITY_CREATED" in _constraint_definition(verification)
        engine.dispose()
