from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from app.canonical import canonical_json

__all__ = [
    "GENESIS_HASH",
    "calculate_hash",
    "canonical_json",
    "canonical_timestamp",
]

GENESIS_HASH = "GENESIS"


def canonical_timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Ledger timestamps must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


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
