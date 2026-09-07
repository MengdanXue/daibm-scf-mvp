from datetime import datetime, timezone

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.services.facility import FacilityService
from test_facility_service import (
    _approved_application, _declare_default, _defaulted, _restructure, _users,
)


def _migrate(engine, target, *, down=False):
    with engine.begin() as connection:
        config = Config("alembic.ini")
        config.attributes["connection"] = connection
        (command.downgrade if down else command.upgrade)(config, target)


def _seed(engine):
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    users = _users(factory)
    service = FacilityService(factory, clock=lambda: datetime(2026, 9, 7, tzinfo=timezone.utc))
    facility = _defaulted(service, users, _approved_application(factory, users))
    return service, users, facility


def _bytes(engine):
    with engine.connect() as connection:
        return list(connection.scalars(text(
            "SELECT row_to_json(e)::text FROM ledger_events e ORDER BY id"
        )))


def test_episode_migration_roundtrip_preserves_original_payload_and_ledger(isolated_postgres_engine):
    engine = isolated_postgres_engine
    _migrate(engine, "head")
    _, _, facility = _seed(engine)
    before = _bytes(engine)
    with engine.connect() as connection:
        original = connection.scalar(text("SELECT to_jsonb(d) - 'schedule_version' FROM facility_defaults d"))
    _migrate(engine, "20260907_0012", down=True)
    _migrate(engine, "head")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT to_jsonb(d) - 'schedule_version' FROM facility_defaults d")) == original
        assert connection.scalar(text("SELECT schedule_version FROM facility_defaults")) == 1
        assert str(connection.scalar(text("SELECT default_id FROM facility_defaults"))) == facility["default_event"]["default_id"]
    assert _bytes(engine) == before


def test_episode_downgrade_refuses_multiple_history_without_changes(isolated_postgres_engine):
    engine = isolated_postgres_engine
    _migrate(engine, "head")
    service, users, facility = _seed(engine)
    facility = service.restructure(facility["facility_id"], _restructure(facility["version"], total="1000.00"), users["risk.demo"])
    facility = service.declare_default(facility["facility_id"], _declare_default(facility["version"]), users["risk.demo"])
    before = _bytes(engine)
    with pytest.raises(RuntimeError, match="multiple default episodes"):
        _migrate(engine, "20260907_0012", down=True)
    assert service.get(facility["facility_id"], users["risk.demo"])["default_history"] == facility["default_history"]
    assert _bytes(engine) == before
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20260907_0013"


def test_conservation_preflight_refuses_inconsistent_legacy_without_repair(isolated_postgres_engine):
    engine = isolated_postgres_engine
    _migrate(engine, "head")
    _seed(engine)
    _migrate(engine, "20260907_0012", down=True)
    with engine.begin() as connection:
        connection.execute(text("UPDATE financing_facilities SET outstanding_amount = 900.00"))
    before = _bytes(engine)
    with pytest.raises(RuntimeError, match="principal conservation failed for facility"):
        _migrate(engine, "head")
    with engine.connect() as connection:
        assert str(connection.scalar(text("SELECT outstanding_amount FROM financing_facilities"))) == "900.00"
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20260907_0012"
    assert _bytes(engine) == before


def test_downgrade_refuses_single_default_followed_by_restructure(isolated_postgres_engine):
    engine = isolated_postgres_engine
    _migrate(engine, "head")
    service, users, facility = _seed(engine)
    service.restructure(facility["facility_id"], _restructure(facility["version"], total="1000.00"), users["risk.demo"])
    with pytest.raises(RuntimeError, match="post-default restructure"):
        _migrate(engine, "20260907_0012", down=True)
