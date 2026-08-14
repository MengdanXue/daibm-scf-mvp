from __future__ import annotations

import sqlite3
from pathlib import Path


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


def initialize(path: Path) -> None:
    with connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS financing_requests (
                request_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                applicant_id TEXT NOT NULL,
                amount REAL NOT NULL,
                term_days INTEGER NOT NULL,
                features_json TEXT NOT NULL,
                risk_score REAL NOT NULL,
                decision TEXT NOT NULL,
                explanation_json TEXT NOT NULL,
                control_action TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ledger_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                event_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                previous_hash TEXT NOT NULL,
                event_hash TEXT NOT NULL UNIQUE
            );

            CREATE INDEX IF NOT EXISTS idx_requests_created_at
                ON financing_requests(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_ledger_entity
                ON ledger_events(entity_id, id);
            """
        )

