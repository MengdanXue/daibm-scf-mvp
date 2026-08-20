from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, URL, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from testcontainers.community.postgres import PostgresContainer


ALL_DATA_TABLES = (
    "user_sessions",
    "workflow_actions",
    "integrity_incidents",
    "policy_decisions",
    "risk_assessments",
    "model_versions",
    "model_runs",
    "graph_snapshots",
    "synthetic_scenarios",
    "dataset_versions",
    "ledger_events",
    "financing_requests",
    "users",
    "organizations",
)


def truncate_all(connection) -> None:
    connection.execute(
        text(
            "TRUNCATE "
            + ", ".join(ALL_DATA_TABLES)
            + " RESTART IDENTITY CASCADE"
        )
    )


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[URL]:
    with PostgresContainer("postgres:17-alpine") as postgres:
        yield make_url(postgres.get_connection_url()).set(
            drivername="postgresql+psycopg",
            host="127.0.0.1",
        )


@pytest.fixture(scope="session")
def migrated_engine(postgres_url: URL) -> Iterator[Engine]:
    engine = create_engine(postgres_url, pool_pre_ping=True)
    config = Config("alembic.ini")
    with engine.connect() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        yield engine
    engine.dispose()


@pytest.fixture
def session_factory(
    migrated_engine: Engine,
) -> Iterator[sessionmaker[Session]]:
    factory = sessionmaker(bind=migrated_engine, expire_on_commit=False)
    with migrated_engine.begin() as connection:
        truncate_all(connection)
    yield factory
    with migrated_engine.begin() as connection:
        truncate_all(connection)


@pytest.fixture
def login_user():
    def login(client, username: str):
        response = client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": "Demo123!"},
        )
        assert response.status_code == 200
        return response.json()

    return login
