from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, TypeAlias

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.ledger import (
    GENESIS_HASH,
    calculate_hash,
    canonical_json,
    canonical_timestamp,
)
from app.models import LedgerEventModel
from app.repositories.anchors import AnchorOutboxRepository

__all__ = ["LedgerEventSpec", "LedgerRepository"]

LedgerEventSpec: TypeAlias = tuple[str, dict[str, Any]]
LEDGER_LOCK_KEY = 0x444149424D


class LedgerRepository:
    def __init__(
        self,
        *,
        anchor_repository: AnchorOutboxRepository | None = None,
    ) -> None:
        self.anchor_repository = anchor_repository or AnchorOutboxRepository()

    @staticmethod
    def acquire_global_lock(session: Session) -> None:
        session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": LEDGER_LOCK_KEY},
        )

    def clear_demo_data(self, session: Session) -> None:
        session.execute(
            text(
                "TRUNCATE ledger_events, financing_requests "
                "RESTART IDENTITY CASCADE"
            )
        )

    def append_many(
        self,
        session: Session,
        entity_id: uuid.UUID,
        events: list[LedgerEventSpec],
        *,
        stream_id: str = "global",
    ) -> list[LedgerEventModel]:
        if stream_id != "global":
            raise ValueError("PostgreSQL audit adapter supports only global stream")
        self.acquire_global_lock(session)
        previous_hash = session.scalar(
            select(LedgerEventModel.event_hash)
            .order_by(LedgerEventModel.id.desc())
            .limit(1)
        ) or GENESIS_HASH

        appended: list[LedgerEventModel] = []
        for event_type, payload in events:
            event = self._build_event(
                previous_hash=previous_hash,
                event_type=event_type,
                entity_id=entity_id,
                payload=payload,
                stream_id=stream_id,
            )
            session.add(event)
            session.flush()
            self.anchor_repository.enqueue(session, event)
            appended.append(event)
            previous_hash = event.event_hash
        return appended

    def list_recent(
        self,
        session: Session,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        statement = (
            select(LedgerEventModel)
            .order_by(LedgerEventModel.id.desc())
            .limit(limit)
        )
        return [
            self._event_to_dict(event)
            for event in session.scalars(statement)
        ]

    def verify(self, session: Session) -> dict[str, Any]:
        events = list(
            session.scalars(
                select(LedgerEventModel).order_by(LedgerEventModel.id.asc())
            )
        )
        expected_previous = GENESIS_HASH
        for event in events:
            if event.previous_hash != expected_previous:
                return {
                    "valid": False,
                    "event_count": len(events),
                    "invalid_event_id": event.id,
                    "reason": "previous_hash_mismatch",
                }
            expected_hash = calculate_hash(
                event.previous_hash,
                canonical_timestamp(event.created_at),
                event.event_type,
                str(event.entity_id),
                canonical_json(event.payload),
            )
            if event.event_hash != expected_hash:
                return {
                    "valid": False,
                    "event_count": len(events),
                    "invalid_event_id": event.id,
                    "reason": "event_hash_mismatch",
                }
            expected_previous = event.event_hash
        return {
            "valid": True,
            "event_count": len(events),
            "head_hash": expected_previous,
        }

    def tamper_first_risk_event(self, session: Session) -> int:
        event = session.scalar(
            select(LedgerEventModel)
            .where(LedgerEventModel.event_type == "RISK_ASSESSMENT")
            .order_by(LedgerEventModel.id.asc())
            .limit(1)
        )
        if event is None:
            raise RuntimeError("No risk-assessment event available for the demo")
        payload = dict(event.payload)
        payload["demo_tampered"] = True
        payload["score"] = 0.9999
        event.payload = payload
        session.flush()
        return event.id

    @staticmethod
    def get_event(
        session: Session,
        event_id: int,
        *,
        for_update: bool = False,
    ) -> LedgerEventModel | None:
        statement = select(LedgerEventModel).where(
            LedgerEventModel.id == event_id
        )
        if for_update:
            statement = statement.with_for_update()
        return session.scalar(statement)

    @staticmethod
    def recompute_event_hash(event: LedgerEventModel) -> str:
        return calculate_hash(
            event.previous_hash,
            canonical_timestamp(event.created_at),
            event.event_type,
            str(event.entity_id),
            canonical_json(event.payload),
        )

    @staticmethod
    def _build_event(
        previous_hash: str,
        event_type: str,
        entity_id: uuid.UUID,
        payload: dict[str, Any],
        stream_id: str = "global",
    ) -> LedgerEventModel:
        created_at = datetime.now(timezone.utc)
        event_hash = calculate_hash(
            previous_hash,
            canonical_timestamp(created_at),
            event_type,
            str(entity_id),
            canonical_json(payload),
        )
        return LedgerEventModel(
            created_at=created_at,
            stream_id=stream_id,
            event_type=event_type,
            entity_id=entity_id,
            payload=payload,
            previous_hash=previous_hash,
            event_hash=event_hash,
        )

    @staticmethod
    def _event_to_dict(event: LedgerEventModel) -> dict[str, Any]:
        return {
            "id": event.id,
            "created_at": canonical_timestamp(event.created_at),
            "stream_id": event.stream_id,
            "event_type": event.event_type,
            "entity_id": str(event.entity_id),
            "payload": event.payload,
            "previous_hash": event.previous_hash,
            "event_hash": event.event_hash,
        }
