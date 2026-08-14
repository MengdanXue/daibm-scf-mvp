from sqlalchemy import inspect, text

from app.database import Database


def test_initial_migration_creates_postgresql_native_schema(migrated_engine):
    inspector = inspect(migrated_engine)

    assert set(inspector.get_table_names()) >= {
        "alembic_version",
        "financing_requests",
        "ledger_events",
    }

    request_columns = {
        column["name"]: column
        for column in inspector.get_columns("financing_requests")
    }
    ledger_columns = {
        column["name"]: column
        for column in inspector.get_columns("ledger_events")
    }

    assert str(request_columns["request_id"]["type"]) == "UUID"
    assert str(request_columns["features"]["type"]) == "JSONB"
    assert str(request_columns["amount"]["type"]) == "NUMERIC(14, 2)"
    assert str(ledger_columns["payload"]["type"]) == "JSONB"
    assert str(ledger_columns["id"]["type"]) == "BIGINT"


def test_database_session_factory_reaches_postgresql(postgres_url):
    database = Database.create(postgres_url)
    try:
        with database.session_factory() as session:
            assert session.scalar(text("SELECT current_database()"))
        assert database.is_reachable() is True
    finally:
        database.dispose()
