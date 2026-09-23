from __future__ import annotations

import uuid
import json
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from sqlalchemy import select

from app.models import LedgerEventModel
from app.models_advanced import AnchorOutboxModel
from app.config import FabricGatewaySettings
from app.repositories.ledger import LedgerRepository
from app.services.anchor_dispatch import (
    AnchorDispatchService,
    FabricGatewayClient,
    GatewayRequestError,
    GatewayResult,
)


class _GatewayHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("content-length", "0"))
        self.server.received_path = self.path
        self.server.received_type = self.headers.get("content-type")
        self.server.received_body = self.rfile.read(length)
        status_code = self.server.response_status
        if status_code in {200, 201}:
            response = json.loads(self.server.received_body)
        else:
            response = self.server.response_body or {
                "code": "ANCHOR_CONFLICT",
                "message": "Anchor ID already exists with different content",
                "retryable": False,
            }
        encoded = self.server.response_bytes
        if encoded is None:
            encoded = json.dumps(response, separators=(",", ":")).encode("utf-8")
        self.send_response(status_code)
        self.send_header("content-type", self.server.response_type)
        self.send_header("content-length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, _format, *args):
        del args


def _start_gateway(
    status_code, response_body=None, *, response_bytes=None, response_type="application/json"
):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _GatewayHandler)
    server.response_status = status_code
    server.response_body = response_body
    server.response_bytes = response_bytes
    server.response_type = response_type
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


class ContractGateway:
    def __init__(self, status_code: int, *, retryable: bool | None = None):
        self.status_code = status_code
        self.retryable = retryable
        self.sent = []

    def create_anchor(self, envelope):
        self.sent.append(dict(envelope))
        if self.status_code >= 400:
            code = {
                409: "ANCHOR_CONFLICT",
                422: "INVALID_ANCHOR",
                500: "INTERNAL_ERROR",
                502: "GATEWAY_PROTOCOL_ERROR",
                503: "FABRIC_UNAVAILABLE",
                504: "FABRIC_TIMEOUT",
            }[self.status_code]
            raise GatewayRequestError(
                status_code=self.status_code,
                code=code,
                message=f"ENOENT /network/private/{code}.pem",
                retryable=self.retryable,
            )
        return GatewayResult(status_code=self.status_code, anchor=dict(envelope))


def test_http_gateway_client_sends_canonical_contract_bytes():
    server, thread = _start_gateway(201)
    try:
        envelope = {
            "subjectId": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
            "schemaVersion": 1,
            "anchorId": "11111111-2222-4333-8444-555555555555",
        }
        client = FabricGatewayClient(
            FabricGatewaySettings(
                base_url=f"http://127.0.0.1:{server.server_port}",
                timeout_seconds=5.0,
            )
        )

        result = client.create_anchor(envelope)

        assert result == GatewayResult(status_code=201, anchor=envelope)
        assert server.received_path == "/anchors"
        assert server.received_type == "application/json"
        assert server.received_body == (
            b'{"anchorId":"11111111-2222-4333-8444-555555555555",'
            b'"schemaVersion":1,'
            b'"subjectId":"aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"}'
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_http_gateway_client_maps_conflict_without_leaking_response_details():
    server, thread = _start_gateway(409)
    try:
        client = FabricGatewayClient(
            FabricGatewaySettings(
                base_url=f"http://127.0.0.1:{server.server_port}",
                timeout_seconds=5.0,
            )
        )
        with pytest.raises(GatewayRequestError) as caught:
            client.create_anchor({"anchorId": "11111111-2222-4333-8444-555555555555"})
        assert caught.value.status_code == 409
        assert caught.value.code == "ANCHOR_CONFLICT"
        assert caught.value.retryable is False
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize(
    ("response_body", "expected_retryable"),
    [
        ({"code": "INTERNAL_ERROR", "retryable": True}, True),
        ({"code": "INTERNAL_ERROR", "retryable": "true"}, None),
        ({"code": "INTERNAL_ERROR"}, None),
    ],
)
def test_http_gateway_client_preserves_only_boolean_retryable_from_500(
    response_body, expected_retryable
):
    server, thread = _start_gateway(500, response_body)
    try:
        client = FabricGatewayClient(
            FabricGatewaySettings(
                base_url=f"http://127.0.0.1:{server.server_port}",
                timeout_seconds=5.0,
            )
        )
        with pytest.raises(GatewayRequestError) as caught:
            client.create_anchor({"anchorId": "11111111-2222-4333-8444-555555555555"})
        assert caught.value.status_code == 500
        assert caught.value.code == "INTERNAL_ERROR"
        assert caught.value.retryable is expected_retryable
        assert "/network/" not in str(caught.value)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize(
    ("response_bytes", "response_type"),
    [
        (b'{"code":"INTERNAL_ERROR","retryable":true', "application/json"),
        (
            b'{"code":"INTERNAL_ERROR","retryable":true}' + b" " * (16 * 1024),
            "application/json",
        ),
        (b'{"code":"INTERNAL_ERROR","retryable":true}', "text/plain"),
    ],
)
def test_http_gateway_client_rejects_malformed_oversized_or_non_json_500(
    response_bytes, response_type
):
    server, thread = _start_gateway(
        500, response_bytes=response_bytes, response_type=response_type
    )
    try:
        client = FabricGatewayClient(
            FabricGatewaySettings(
                base_url=f"http://127.0.0.1:{server.server_port}",
                timeout_seconds=5.0,
            )
        )
        with pytest.raises(GatewayRequestError) as caught:
            client.create_anchor({"anchorId": "11111111-2222-4333-8444-555555555555"})
        assert caught.value.status_code == 500
        assert caught.value.code == "GATEWAY_REQUEST_FAILED"
        assert caught.value.retryable is None
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize(
    ("status_code", "code", "retryable", "attempt_count", "expected"),
    [
        (500, "INTERNAL_ERROR", True, 1, True),
        (500, "INTERNAL_ERROR", True, 2, True),
        (500, "INTERNAL_ERROR", True, 3, False),
        (500, "INTERNAL_ERROR", None, 1, False),
        (500, "INTERNAL_ERROR", False, 1, False),
        (500, "GATEWAY_REQUEST_FAILED", True, 1, False),
        (502, "GATEWAY_PROTOCOL_ERROR", True, 1, False),
        (503, "FABRIC_UNAVAILABLE", None, 3, True),
        (504, "FABRIC_TIMEOUT", None, 3, True),
        (409, "ANCHOR_CONFLICT", False, 1, False),
    ],
)
def test_gateway_retry_policy_is_narrow_and_bounds_internal_errors(
    status_code, code, retryable, attempt_count, expected
):
    error = GatewayRequestError(
        status_code=status_code,
        code=code,
        message="safe error",
        retryable=retryable,
    )
    assert AnchorDispatchService._should_retry_gateway_error(error, attempt_count) is expected


def _seed_anchor(session_factory) -> uuid.UUID:
    with session_factory.begin() as session:
        LedgerRepository().append_many(
            session,
            uuid.UUID("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"),
            [("FINANCING_REQUEST", {"sensitive": "must-not-leak"})],
        )
        return session.scalar(select(AnchorOutboxModel.anchor_id))


@pytest.mark.parametrize("status_code", [200, 201])
def test_gateway_success_marks_anchor_complete(session_factory, status_code):
    anchor_id = _seed_anchor(session_factory)
    now = datetime.now(timezone.utc) + timedelta(minutes=1)
    service = AnchorDispatchService(
        session_factory,
        ContractGateway(status_code),
        clock=lambda: now,
    )

    assert service.dispatch_batch(limit=10) == {
        "claimed": 1,
        "anchored": 1,
        "retryable": 0,
        "permanent_failed": 0,
    }

    with session_factory() as session:
        row = session.get(AnchorOutboxModel, anchor_id)
        assert row.status == "anchored"
        assert row.anchored_at == now
        assert row.attempt_count == 1
        assert row.lease_token is None and row.lease_expires_at is None
        assert row.last_error_code is None


def test_gateway_conflict_is_permanent_and_not_automatically_retried(session_factory):
    anchor_id = _seed_anchor(session_factory)
    now = datetime.now(timezone.utc) + timedelta(minutes=1)
    service = AnchorDispatchService(
        session_factory,
        ContractGateway(409),
        clock=lambda: now,
    )

    summary = service.dispatch_batch(limit=10)

    assert summary["permanent_failed"] == 1
    with session_factory() as session:
        row = session.get(AnchorOutboxModel, anchor_id)
        assert row.status == "permanent_failed"
        assert row.last_error_code == "ANCHOR_CONFLICT"
        assert row.lease_token is None and row.lease_expires_at is None


def test_other_gateway_client_errors_are_terminal_not_infinite_retries(session_factory):
    anchor_id = _seed_anchor(session_factory)
    now = datetime.now(timezone.utc) + timedelta(minutes=1)
    service = AnchorDispatchService(
        session_factory,
        ContractGateway(422),
        clock=lambda: now,
    )

    summary = service.dispatch_batch(limit=10)

    assert summary["permanent_failed"] == 1
    assert summary["retryable"] == 0
    with session_factory() as session:
        row = session.get(AnchorOutboxModel, anchor_id)
        assert row.status == "permanent_failed"
        assert row.last_error_code == "INVALID_ANCHOR"


@pytest.mark.parametrize(
    ("status_code", "error_code"),
    [(503, "FABRIC_UNAVAILABLE"), (504, "FABRIC_TIMEOUT")],
)
def test_gateway_outage_stays_pending_with_exponential_backoff_and_no_path_leak(
    session_factory,
    status_code,
    error_code,
):
    anchor_id = _seed_anchor(session_factory)
    first_attempt = datetime.now(timezone.utc) + timedelta(minutes=1)
    clock_value = [first_attempt]
    service = AnchorDispatchService(
        session_factory,
        ContractGateway(status_code),
        clock=lambda: clock_value[0],
    )

    first = service.dispatch_batch(limit=10)

    assert first["retryable"] == 1
    with session_factory() as session:
        row = session.get(AnchorOutboxModel, anchor_id)
        assert row.status == "pending"
        assert row.attempt_count == 1
        assert row.next_attempt_at == first_attempt + timedelta(seconds=2)
        assert row.last_error_code == error_code
        assert "/network/" not in str(row.last_error_code)
        assert row.lease_token is None and row.lease_expires_at is None

    clock_value[0] = first_attempt + timedelta(seconds=2)
    service.dispatch_batch(limit=10)
    with session_factory() as session:
        row = session.get(AnchorOutboxModel, anchor_id)
        assert row.attempt_count == 2
        assert row.next_attempt_at == clock_value[0] + timedelta(seconds=4)


def test_retryable_internal_error_recovers_once_without_duplicate_business_state(
    session_factory,
):
    anchor_id = _seed_anchor(session_factory)
    first_attempt = datetime.now(timezone.utc) + timedelta(minutes=1)
    clock_value = [first_attempt]
    gateway = ContractGateway(500, retryable=True)
    service = AnchorDispatchService(
        session_factory, gateway, clock=lambda: clock_value[0]
    )

    first = service.dispatch_batch(limit=10)
    assert first == {
        "claimed": 1, "anchored": 0, "retryable": 1, "permanent_failed": 0,
    }
    with session_factory() as session:
        row = session.get(AnchorOutboxModel, anchor_id)
        assert row.status == "pending"
        assert row.attempt_count == 1
        assert row.next_attempt_at == first_attempt + timedelta(seconds=2)
        assert row.last_error_code == "INTERNAL_ERROR"
        assert row.lease_token is None and row.lease_expires_at is None

    assert service.dispatch_batch(limit=10)["claimed"] == 0
    clock_value[0] += timedelta(seconds=2)
    gateway.status_code = 201
    recovered = service.dispatch_batch(limit=10)
    assert recovered == {
        "claimed": 1, "anchored": 1, "retryable": 0, "permanent_failed": 0,
    }
    assert service.dispatch_batch(limit=10)["claimed"] == 0
    assert len(gateway.sent) == 2
    assert gateway.sent[0] == gateway.sent[1]
    with session_factory() as session:
        row = session.get(AnchorOutboxModel, anchor_id)
        assert row.status == "anchored"
        assert row.attempt_count == 2
        assert row.last_error_code is None
        assert len(list(session.scalars(select(AnchorOutboxModel)))) == 1
        assert len(list(session.scalars(select(LedgerEventModel)))) == 1


def test_retryable_internal_error_stops_after_three_attempts(session_factory):
    anchor_id = _seed_anchor(session_factory)
    first_attempt = datetime.now(timezone.utc) + timedelta(minutes=1)
    clock_value = [first_attempt]
    gateway = ContractGateway(500, retryable=True)
    service = AnchorDispatchService(
        session_factory, gateway, clock=lambda: clock_value[0]
    )

    for attempt_count, backoff in ((1, 2), (2, 4)):
        result = service.dispatch_batch(limit=10)
        assert (result["retryable"], result["permanent_failed"]) == (1, 0)
        with session_factory() as session:
            row = session.get(AnchorOutboxModel, anchor_id)
            assert row.status == "pending"
            assert row.attempt_count == attempt_count
            assert row.next_attempt_at == clock_value[0] + timedelta(seconds=backoff)
        clock_value[0] += timedelta(seconds=backoff)

    exhausted = service.dispatch_batch(limit=10)
    assert (exhausted["retryable"], exhausted["permanent_failed"]) == (0, 1)
    with session_factory() as session:
        row = session.get(AnchorOutboxModel, anchor_id)
        assert row.status == "permanent_failed"
        assert row.attempt_count == 3
        assert row.last_error_code == "INTERNAL_ERROR"
        assert row.lease_token is None and row.lease_expires_at is None
    assert service.dispatch_batch(limit=10)["claimed"] == 0
    assert len(gateway.sent) == 3


@pytest.mark.parametrize("retryable", [False, None])
def test_internal_error_without_retryable_true_is_permanent(
    session_factory, retryable
):
    anchor_id = _seed_anchor(session_factory)
    now = datetime.now(timezone.utc) + timedelta(minutes=1)
    service = AnchorDispatchService(
        session_factory,
        ContractGateway(500, retryable=retryable),
        clock=lambda: now,
    )

    summary = service.dispatch_batch(limit=10)
    assert (summary["retryable"], summary["permanent_failed"]) == (0, 1)
    with session_factory() as session:
        row = session.get(AnchorOutboxModel, anchor_id)
        assert (row.status, row.attempt_count, row.last_error_code) == (
            "permanent_failed", 1, "INTERNAL_ERROR",
        )


def test_dispatch_claims_each_row_only_when_it_is_ready_to_send(session_factory):
    with session_factory.begin() as session:
        LedgerRepository().append_many(
            session,
            uuid.UUID("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"),
            [
                ("FINANCING_REQUEST", {"sequence": 1}),
                ("RISK_ASSESSMENT", {"sequence": 2}),
            ],
        )
    now = datetime.now(timezone.utc) + timedelta(minutes=1)
    nested_service = AnchorDispatchService(
        session_factory,
        ContractGateway(201),
        clock=lambda: now,
    )

    class ReentrantGateway:
        nested_summary = None

        def create_anchor(self, envelope):
            self.nested_summary = nested_service.dispatch_batch(limit=10)
            return GatewayResult(status_code=201, anchor=dict(envelope))

    gateway = ReentrantGateway()
    outer_service = AnchorDispatchService(
        session_factory,
        gateway,
        clock=lambda: now,
    )

    outer_summary = outer_service.dispatch_batch(limit=2)

    assert gateway.nested_summary["claimed"] == 1
    assert gateway.nested_summary["anchored"] == 1
    assert outer_summary["claimed"] == 1
    assert outer_summary["anchored"] == 1
    with session_factory() as session:
        rows = list(session.scalars(select(AnchorOutboxModel)))
        assert len(rows) == 2
        assert all(row.status == "anchored" for row in rows)
