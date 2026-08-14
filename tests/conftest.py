from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, URL, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from testcontainers.community.postgres import PostgresContainer


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
        connection.execute(
            text(
                "TRUNCATE ledger_events, financing_requests "
                "RESTART IDENTITY CASCADE"
            )
        )
    yield factory
    with migrated_engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE ledger_events, financing_requests "
                "RESTART IDENTITY CASCADE"
            )
        )
