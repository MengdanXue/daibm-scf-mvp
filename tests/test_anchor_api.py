from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import Database
from app.main import create_app
from app.models_advanced import AnchorOutboxModel
from app.repositories.ledger import LedgerRepository
from app.services.anchor_dispatch import GatewayResult


class SuccessfulGateway:
    def create_anchor(self, envelope):
        return GatewayResult(status_code=201, anchor=dict(envelope))


@pytest.fixture
def anchor_client(migrated_engine, session_factory):
    database = Database(migrated_engine, session_factory)
    with TestClient(create_app(database)) as client:
        yield client


def _login(client: TestClient, username: str) -> None:
    client.cookies.clear()
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "Demo123!"},
    )
    assert response.status_code == 200


def _seed(session_factory) -> uuid.UUID:
    with session_factory.begin() as session:
        LedgerRepository().append_many(
            session,
            uuid.UUID("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"),
            [
                (
                    "FINANCING_REQUEST",
                    {
                        "invoiceNumber": "INV-SECRET",
                        "amount": "999.99",
                        "credential": "secret-token",
                    },
                )
            ],
        )
        return session.scalar(select(AnchorOutboxModel.anchor_id))


def test_anchor_routes_require_auditor_role(anchor_client, session_factory):
    anchor_id = _seed(session_factory)
    assert anchor_client.get("/api/v1/anchors").status_code == 401

    _login(anchor_client, "financier.demo")
    assert anchor_client.get("/api/v1/anchors").status_code == 403
    assert (
        anchor_client.post(f"/api/v1/anchors/{anchor_id}/retry").status_code
        == 403
    )
    assert anchor_client.post("/api/v1/anchor-dispatches").status_code == 403


def test_auditor_reads_payload_free_anchor_contract(anchor_client, session_factory):
    anchor_id = _seed(session_factory)
    _login(anchor_client, "auditor.demo")

    response = anchor_client.get(f"/api/v1/anchors/{anchor_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["anchor_id"] == str(anchor_id)
    assert body["status"] == "pending"
    assert body["attempt_count"] == 0
    assert set(body) == {
        "anchor_id",
        "event_id",
        "subject_id",
        "event_hash",
        "chain_head_hash",
        "recorded_at",
        "schema_version",
        "model_version",
        "policy_version",
        "circuit_version",
        "proof_sha256",
        "status",
        "attempt_count",
        "next_attempt_at",
        "last_error_code",
        "anchored_at",
    }
    assert "INV-SECRET" not in response.text
    assert "999.99" not in response.text
    assert "secret-token" not in response.text


def test_auditor_retries_failed_anchor_and_manually_dispatches_once(
    anchor_client,
    session_factory,
):
    anchor_id = _seed(session_factory)
    with session_factory.begin() as session:
        row = session.get(AnchorOutboxModel, anchor_id)
        row.status = "permanent_failed"
        row.last_error_code = "ANCHOR_CONFLICT"
        row.next_attempt_at = datetime.now(timezone.utc) + timedelta(days=1)

    _login(anchor_client, "auditor.demo")
    retried = anchor_client.post(f"/api/v1/anchors/{anchor_id}/retry")
    assert retried.status_code == 200
    assert retried.json()["status"] == "pending"
    assert retried.json()["last_error_code"] is None

    anchor_client.app.state.anchor_dispatch_service.gateway = SuccessfulGateway()
    dispatched = anchor_client.post("/api/v1/anchor-dispatches", json={"limit": 10})
    assert dispatched.status_code == 200
    assert dispatched.json() == {
        "claimed": 1,
        "anchored": 1,
        "retryable": 0,
        "permanent_failed": 0,
    }

    with session_factory() as session:
        assert session.get(AnchorOutboxModel, anchor_id).status == "anchored"


def test_auditor_cannot_clear_a_live_pending_dispatch_lease(
    anchor_client,
    session_factory,
):
    anchor_id = _seed(session_factory)
    lease_token = uuid.uuid4()
    with session_factory.begin() as session:
        row = session.get(AnchorOutboxModel, anchor_id)
        row.lease_token = lease_token
        row.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)

    _login(anchor_client, "auditor.demo")
    response = anchor_client.post(f"/api/v1/anchors/{anchor_id}/retry")

    assert response.status_code == 409
    with session_factory() as session:
        row = session.get(AnchorOutboxModel, anchor_id)
        assert row.status == "pending"
        assert row.lease_token == lease_token
