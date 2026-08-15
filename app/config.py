from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
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


@dataclass(frozen=True)
class ResearchSettings:
    reference_dir: Path
    required: bool = True

    @classmethod
    def from_env(
        cls, environ: Mapping[str, str] | None = None
    ) -> "ResearchSettings":
        values = os.environ if environ is None else environ
        default_dir = Path(__file__).resolve().parents[1] / "artifacts" / "reference"
        raw_required = values.get("RESEARCH_CORE_REQUIRED", "true").strip().lower()
        if raw_required not in {"true", "false"}:
            raise ValueError("RESEARCH_CORE_REQUIRED must be true or false")
        return cls(
            reference_dir=Path(
                values.get("RESEARCH_ARTIFACT_DIR", str(default_dir))
            ),
            required=raw_required == "true",
        )
