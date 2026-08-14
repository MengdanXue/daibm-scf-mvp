from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .db import connect


GENESIS_HASH = "GENESIS"


def canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def calculate_hash(
    previous_hash: str,
    created_at: str,
    event_type: str,
    entity_id: str,
    payload_json: str,
) -> str:
    material = "\x1f".join(
        [previous_hash, created_at, event_type, entity_id, payload_json]
    ).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


class Ledger:
    def __init__(self, db_path: Path):
        self.db_path = db_path

    def append(self, event_type: str, entity_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        created_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        payload_json = canonical_json(payload)
        with connect(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT event_hash FROM ledger_events ORDER BY id DESC LIMIT 1"
            ).fetchone()
            previous_hash = row["event_hash"] if row else GENESIS_HASH
            event_hash = calculate_hash(
                previous_hash, created_at, event_type, entity_id, payload_json
            )
            cursor = connection.execute(
                """
                INSERT INTO ledger_events
                    (created_at, event_type, entity_id, payload_json, previous_hash, event_hash)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (created_at, event_type, entity_id, payload_json, previous_hash, event_hash),
            )
        return {
            "id": cursor.lastrowid,
            "created_at": created_at,
            "event_type": event_type,
            "entity_id": entity_id,
            "payload": payload,
            "previous_hash": previous_hash,
            "event_hash": event_hash,
        }

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        with connect(self.db_path) as connection:
            rows = connection.execute(
                "SELECT * FROM ledger_events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._row_to_event(row) for row in rows]

    def verify(self) -> dict[str, Any]:
        with connect(self.db_path) as connection:
            rows = connection.execute(
                "SELECT * FROM ledger_events ORDER BY id ASC"
            ).fetchall()
        expected_previous = GENESIS_HASH
        for row in rows:
            if row["previous_hash"] != expected_previous:
                return {
                    "valid": False,
                    "event_count": len(rows),
                    "invalid_event_id": row["id"],
                    "reason": "previous_hash_mismatch",
                }
            expected_hash = calculate_hash(
                row["previous_hash"],
                row["created_at"],
                row["event_type"],
                row["entity_id"],
                row["payload_json"],
            )
            if row["event_hash"] != expected_hash:
                return {
                    "valid": False,
                    "event_count": len(rows),
                    "invalid_event_id": row["id"],
                    "reason": "event_hash_mismatch",
                }
            expected_previous = row["event_hash"]
        return {
            "valid": True,
            "event_count": len(rows),
            "head_hash": expected_previous,
        }

    @staticmethod
    def _row_to_event(row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "created_at": row["created_at"],
            "event_type": row["event_type"],
            "entity_id": row["entity_id"],
            "payload": json.loads(row["payload_json"]),
            "previous_hash": row["previous_hash"],
            "event_hash": row["event_hash"],
        }

