from __future__ import annotations

import uuid
import re
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.ledger import canonical_timestamp
from app.models import LedgerEventModel
from app.models_advanced import AnchorOutboxModel


_EVENT_NAMESPACE = uuid.UUID("8d30ef6b-6a61-4f2c-a706-6f815844a6f5")
_ANCHOR_NAMESPACE = uuid.UUID("d90883d7-5347-4dc7-9c89-ea2299e4b94c")
_VERSION_TOKEN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*(?:@[A-Za-z0-9][A-Za-z0-9._-]*)?$"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _safe_version(value: object) -> str | None:
    return (
        value
        if isinstance(value, str)
        and len(value) <= 64
        and _VERSION_TOKEN.fullmatch(value)
        else None
    )


def _safe_sha256(value: object) -> str | None:
    return value if isinstance(value, str) and _SHA256.fullmatch(value) else None


class AnchorOutboxRepository:
    @staticmethod
    def get(
        session: Session, anchor_id: uuid.UUID, *, for_update: bool = False
    ) -> AnchorOutboxModel | None:
        statement = select(AnchorOutboxModel).where(
            AnchorOutboxModel.anchor_id == anchor_id
        )
        if for_update:
            statement = statement.with_for_update()
        return session.scalar(statement)

    @staticmethod
    def list_recent(session: Session, *, limit: int) -> list[AnchorOutboxModel]:
        return list(
            session.scalars(
                select(AnchorOutboxModel)
                .order_by(AnchorOutboxModel.created_at.desc())
                .limit(limit)
            )
        )

    @staticmethod
    def enqueue(session: Session, event: LedgerEventModel) -> AnchorOutboxModel:
        existing = session.scalar(
            select(AnchorOutboxModel).where(
                AnchorOutboxModel.ledger_event_id == event.id
            )
        )
        if existing is not None:
            return existing

        event_id = uuid.uuid5(_EVENT_NAMESPACE, event.event_hash)
        anchor_id = uuid.uuid5(_ANCHOR_NAMESPACE, event.event_hash)
        payload = event.payload if isinstance(event.payload, dict) else {}
        row = AnchorOutboxModel(
            anchor_id=anchor_id,
            ledger_event_id=event.id,
            event_id=event_id,
            subject_id=event.entity_id,
            event_hash=event.event_hash,
            chain_head_hash=event.event_hash,
            recorded_at=event.created_at,
            schema_version=1,
            model_version=_safe_version(payload.get("model_version_id")),
            policy_version=_safe_version(payload.get("policy_version")),
            circuit_version=_safe_version(payload.get("circuit_version")),
            proof_sha256=_safe_sha256(payload.get("proof_sha256")),
            status="pending",
            attempt_count=0,
            next_attempt_at=event.created_at,
            created_at=event.created_at,
            updated_at=event.created_at,
        )
        session.add(row)
        session.flush()
        return row

    @staticmethod
    def envelope(row: AnchorOutboxModel) -> dict[str, Any]:
        envelope: dict[str, Any] = {
            "anchorId": str(row.anchor_id),
            "chainHeadHash": row.chain_head_hash,
            "eventHash": row.event_hash,
            "eventId": str(row.event_id),
            "recordedAt": canonical_timestamp(row.recorded_at),
            "schemaVersion": row.schema_version,
            "subjectId": str(row.subject_id),
        }
        for name, value in (
            ("circuitVersion", row.circuit_version),
            ("modelVersion", row.model_version),
            ("policyVersion", row.policy_version),
            ("proofSha256", row.proof_sha256),
        ):
            if value is not None:
                envelope[name] = value
        return envelope

    @staticmethod
    def claim_batch(
        session: Session,
        *,
        now: datetime,
        limit: int,
        lease_duration: timedelta,
    ) -> list[AnchorOutboxModel]:
        if not 1 <= limit <= 100:
            raise ValueError("claim limit must be between 1 and 100")
        if lease_duration <= timedelta(0):
            raise ValueError("lease duration must be positive")
        statement = (
            select(AnchorOutboxModel)
            .where(
                AnchorOutboxModel.status == "pending",
                AnchorOutboxModel.next_attempt_at <= now,
                or_(
                    AnchorOutboxModel.lease_token.is_(None),
                    AnchorOutboxModel.lease_expires_at <= now,
                ),
            )
            .order_by(
                AnchorOutboxModel.next_attempt_at.asc(),
                AnchorOutboxModel.created_at.asc(),
            )
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        rows = list(session.scalars(statement))
        for row in rows:
            row.lease_token = uuid.uuid4()
            row.lease_expires_at = now + lease_duration
            row.attempt_count += 1
            row.last_attempt_at = now
            row.updated_at = now
        session.flush()
        return rows

    @staticmethod
    def _leased_row(
        session: Session,
        anchor_id: uuid.UUID,
        lease_token: uuid.UUID,
    ) -> AnchorOutboxModel | None:
        return session.scalar(
            select(AnchorOutboxModel)
            .where(
                AnchorOutboxModel.anchor_id == anchor_id,
                AnchorOutboxModel.status == "pending",
                AnchorOutboxModel.lease_token == lease_token,
            )
            .with_for_update()
        )

    def mark_anchored(
        self,
        session: Session,
        *,
        anchor_id: uuid.UUID,
        lease_token: uuid.UUID,
        now: datetime,
    ) -> bool:
        row = self._leased_row(session, anchor_id, lease_token)
        if row is None:
            return False
        row.status = "anchored"
        row.anchored_at = now
        row.last_error_code = None
        row.lease_token = None
        row.lease_expires_at = None
        row.updated_at = now
        session.flush()
        return True

    def mark_retryable(
        self,
        session: Session,
        *,
        anchor_id: uuid.UUID,
        lease_token: uuid.UUID,
        now: datetime,
        error_code: str,
    ) -> bool:
        row = self._leased_row(session, anchor_id, lease_token)
        if row is None:
            return False
        delay_seconds = min(2 ** row.attempt_count, 60)
        row.next_attempt_at = now + timedelta(seconds=delay_seconds)
        row.last_error_code = error_code
        row.lease_token = None
        row.lease_expires_at = None
        row.updated_at = now
        session.flush()
        return True

    def mark_permanent_failed(
        self,
        session: Session,
        *,
        anchor_id: uuid.UUID,
        lease_token: uuid.UUID,
        now: datetime,
        error_code: str,
    ) -> bool:
        row = self._leased_row(session, anchor_id, lease_token)
        if row is None:
            return False
        row.status = "permanent_failed"
        row.last_error_code = error_code
        row.lease_token = None
        row.lease_expires_at = None
        row.updated_at = now
        session.flush()
        return True

    def retry(
        self,
        session: Session,
        *,
        anchor_id: uuid.UUID,
        now: datetime,
    ) -> AnchorOutboxModel | None:
        row = self.get(session, anchor_id, for_update=True)
        if row is None:
            return None
        if row.status != "permanent_failed":
            raise ValueError("only permanently failed records can be retried")
        row.status = "pending"
        row.next_attempt_at = now
        row.lease_token = None
        row.lease_expires_at = None
        row.last_error_code = None
        row.updated_at = now
        session.flush()
        return row

    @staticmethod
    def public_record(row: AnchorOutboxModel) -> dict[str, Any]:
        return {
            "anchor_id": str(row.anchor_id),
            "event_id": str(row.event_id),
            "subject_id": str(row.subject_id),
            "event_hash": row.event_hash,
            "chain_head_hash": row.chain_head_hash,
            "recorded_at": row.recorded_at,
            "schema_version": row.schema_version,
            "model_version": row.model_version,
            "policy_version": row.policy_version,
            "circuit_version": row.circuit_version,
            "proof_sha256": row.proof_sha256,
            "status": row.status,
            "attempt_count": row.attempt_count,
            "next_attempt_at": row.next_attempt_at,
            "last_error_code": row.last_error_code,
            "anchored_at": row.anchored_at,
        }


__all__ = ["AnchorOutboxRepository"]
