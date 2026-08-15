from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID

from app.domain.research import LedgerEvent


class AuditPort(Protocol):
    def append(
        self,
        *,
        stream_id: str,
        entity_id: UUID,
        events: list[tuple[str, dict[str, Any]]],
    ) -> list[LedgerEvent]: ...
