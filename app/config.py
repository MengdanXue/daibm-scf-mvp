from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

from sqlalchemy import URL


@dataclass(frozen=True)
class PostgresSettings:
    host: str
    port: int
    database: str
    user: str
    password: str

    @classmethod
    def from_env(
        cls, environ: Mapping[str, str] | None = None
    ) -> "PostgresSettings":
        values = os.environ if environ is None else environ
        raw_port = values.get("POSTGRES_PORT", "5432")
        try:
            port = int(raw_port)
        except ValueError as error:
            raise ValueError("POSTGRES_PORT must be an integer") from error
        if not 1 <= port <= 65535:
            raise ValueError("POSTGRES_PORT must be between 1 and 65535")
        return cls(
            host=values.get("POSTGRES_HOST", "localhost"),
            port=port,
            database=values.get("POSTGRES_DB", "daibm_scf"),
            user=values.get("POSTGRES_USER", "daibm"),
            password=values.get("POSTGRES_PASSWORD", "daibm_demo_password"),
        )

    @property
    def sqlalchemy_url(self) -> URL:
        return URL.create(
            "postgresql+psycopg",
            username=self.user,
            password=self.password,
            host=self.host,
            port=self.port,
            database=self.database,
        )
