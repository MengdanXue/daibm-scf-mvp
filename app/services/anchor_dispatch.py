from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol
import json
import re
import socket
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from sqlalchemy.orm import Session, sessionmaker

from app.repositories.anchors import AnchorOutboxRepository
from app.config import FabricGatewaySettings
from app.ledger import canonical_json


_ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_MAX_GATEWAY_BODY = 16 * 1024


@dataclass(frozen=True)
class GatewayResult:
    status_code: int
    anchor: dict[str, Any]


class GatewayRequestError(RuntimeError):
    def __init__(self, *, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class AnchorGateway(Protocol):
    def create_anchor(self, envelope: dict[str, Any]) -> GatewayResult: ...


class FabricGatewayClient:
    def __init__(self, settings: FabricGatewaySettings) -> None:
        self.settings = settings

    def create_anchor(self, envelope: dict[str, Any]) -> GatewayResult:
        request = Request(
            f"{self.settings.base_url}/anchors",
            data=canonical_json(envelope).encode("utf-8"),
            headers={"content-type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.settings.timeout_seconds) as response:
                status_code = response.status
                content_type = response.headers.get_content_type()
                payload = response.read(_MAX_GATEWAY_BODY + 1)
        except HTTPError as error:
            payload = error.read(_MAX_GATEWAY_BODY + 1)
            code = self._error_code(payload, error.code)
            raise GatewayRequestError(
                status_code=error.code,
                code=code,
                message="Fabric gateway rejected the anchor request",
            ) from error
        except URLError as error:
            if isinstance(error.reason, (TimeoutError, socket.timeout)):
                raise TimeoutError("Fabric gateway request timed out") from error
            raise OSError("Fabric gateway is unavailable") from error
        if len(payload) > _MAX_GATEWAY_BODY or content_type != "application/json":
            raise GatewayRequestError(
                status_code=502,
                code="GATEWAY_PROTOCOL_ERROR",
                message="Fabric gateway returned an invalid response",
            )
        try:
            decoded = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise GatewayRequestError(
                status_code=502,
                code="GATEWAY_PROTOCOL_ERROR",
                message="Fabric gateway returned invalid JSON",
            ) from error
        if not isinstance(decoded, dict):
            raise GatewayRequestError(
                status_code=502,
                code="GATEWAY_PROTOCOL_ERROR",
                message="Fabric gateway returned an invalid anchor",
            )
        return GatewayResult(status_code=status_code, anchor=decoded)

    @staticmethod
    def _error_code(payload: bytes, status_code: int) -> str:
        try:
            decoded = json.loads(payload[:_MAX_GATEWAY_BODY])
        except (UnicodeDecodeError, json.JSONDecodeError):
            decoded = None
        if isinstance(decoded, dict):
            code = decoded.get("code")
            if isinstance(code, str) and _ERROR_CODE.fullmatch(code):
                return code
        return {
            409: "ANCHOR_CONFLICT",
            503: "FABRIC_UNAVAILABLE",
            504: "FABRIC_TIMEOUT",
        }.get(status_code, "GATEWAY_REQUEST_FAILED")


class AnchorDispatchService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        gateway: AnchorGateway,
        *,
        repository: AnchorOutboxRepository | None = None,
        clock: Callable[[], datetime] | None = None,
        lease_duration: timedelta = timedelta(seconds=30),
    ) -> None:
        self.session_factory = session_factory
        self.gateway = gateway
        self.repository = repository or AnchorOutboxRepository()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.lease_duration = lease_duration

    def dispatch_batch(self, *, limit: int = 20) -> dict[str, int]:
        summary = {
            "claimed": 0,
            "anchored": 0,
            "retryable": 0,
            "permanent_failed": 0,
        }
        for _ in range(limit):
            with self.session_factory.begin() as session:
                claimed = self.repository.claim_batch(
                    session,
                    now=self.clock(),
                    limit=1,
                    lease_duration=self.lease_duration,
                )
            if not claimed:
                break
            row = claimed[0]
            summary["claimed"] += 1
            envelope = self.repository.envelope(row)
            try:
                result = self.gateway.create_anchor(envelope)
            except GatewayRequestError as error:
                error_code = self._safe_error_code(error.code)
                if error.status_code in {503, 504}:
                    self._retry(row.anchor_id, row.lease_token, error_code)
                    summary["retryable"] += 1
                else:
                    self._permanent(row.anchor_id, row.lease_token, error_code)
                    summary["permanent_failed"] += 1
            except TimeoutError:
                self._retry(row.anchor_id, row.lease_token, "FABRIC_TIMEOUT")
                summary["retryable"] += 1
            except OSError:
                self._retry(row.anchor_id, row.lease_token, "FABRIC_UNAVAILABLE")
                summary["retryable"] += 1
            else:
                if result.status_code not in {200, 201} or result.anchor != envelope:
                    self._permanent(
                        row.anchor_id,
                        row.lease_token,
                        "GATEWAY_PROTOCOL_ERROR",
                    )
                    summary["permanent_failed"] += 1
                else:
                    self._anchored(row.anchor_id, row.lease_token)
                    summary["anchored"] += 1
        return summary

    @staticmethod
    def _safe_error_code(code: str) -> str:
        return code if _ERROR_CODE.fullmatch(code) else "GATEWAY_REQUEST_FAILED"

    def list_outbox(self, *, limit: int = 50) -> list[dict[str, Any]]:
        if not 1 <= limit <= 200:
            raise ValueError("list limit must be between 1 and 200")
        with self.session_factory() as session:
            return [
                self.repository.public_record(row)
                for row in self.repository.list(session, limit=limit)
            ]

    def get_outbox(self, anchor_id) -> dict[str, Any]:
        with self.session_factory() as session:
            row = self.repository.get(session, anchor_id)
            if row is None:
                raise KeyError(str(anchor_id))
            return self.repository.public_record(row)

    def retry(self, anchor_id) -> dict[str, Any]:
        with self.session_factory.begin() as session:
            row = self.repository.retry(
                session,
                anchor_id=anchor_id,
                now=self.clock(),
            )
            if row is None:
                raise KeyError(str(anchor_id))
            return self.repository.public_record(row)

    def _anchored(self, anchor_id, lease_token) -> None:
        with self.session_factory.begin() as session:
            self.repository.mark_anchored(
                session,
                anchor_id=anchor_id,
                lease_token=lease_token,
                now=self.clock(),
            )

    def _retry(self, anchor_id, lease_token, error_code: str) -> None:
        with self.session_factory.begin() as session:
            self.repository.mark_retryable(
                session,
                anchor_id=anchor_id,
                lease_token=lease_token,
                now=self.clock(),
                error_code=error_code,
            )

    def _permanent(self, anchor_id, lease_token, error_code: str) -> None:
        with self.session_factory.begin() as session:
            self.repository.mark_permanent_failed(
                session,
                anchor_id=anchor_id,
                lease_token=lease_token,
                now=self.clock(),
                error_code=error_code,
            )


__all__ = [
    "AnchorDispatchService",
    "FabricGatewayClient",
    "GatewayRequestError",
    "GatewayResult",
]
