from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Engine, URL, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

__all__ = ["Database"]


@dataclass
class Database:
    engine: Engine
    session_factory: sessionmaker[Session]

    @classmethod
    def create(cls, url: URL | str) -> "Database":
        engine = create_engine(url, pool_pre_ping=True)
        return cls(
            engine=engine,
            session_factory=sessionmaker(
                bind=engine,
                expire_on_commit=False,
            ),
        )

    def is_reachable(self) -> bool:
        with self.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True

    def dispose(self) -> None:
        self.engine.dispose()
