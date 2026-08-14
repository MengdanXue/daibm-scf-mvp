import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from app.database import Database
from app.main import create_app


@pytest.fixture
def client(migrated_engine, session_factory):
    database = Database(
        engine=migrated_engine,
        session_factory=session_factory,
    )
    application = create_app(database)
    with TestClient(application) as test_client:
        yield test_client


def test_health_reports_postgresql_and_ledger(client):
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "database": {
            "backend": "postgresql",
            "reachable": True,
        },
        "ledger": {
            "valid": True,
            "event_count": 0,
            "head_hash": "GENESIS",
        },
    }


def test_demo_creates_closed_loop_and_valid_ledger(client):
    response = client.post("/api/demo/seed")
    assert response.status_code == 200
    assert len(response.json()) == 3

    requests = client.get("/api/requests").json()
    assert {item["decision"] for item in requests} == {
        "approved",
        "manual_review",
        "rejected",
    }

    ledger = client.get("/api/ledger/verify").json()
    assert ledger["valid"] is True
    assert ledger["event_count"] == 12

    tampered = client.post("/api/demo/tamper")
    assert tampered.status_code == 200
    assert tampered.json()["valid"] is False
    assert tampered.json()["reason"] == "event_hash_mismatch"

    reset = client.post("/api/demo/reset")
    assert reset.status_code == 200
    assert len(reset.json()) == 3
    repaired = client.get("/api/ledger/verify").json()
    assert repaired["valid"] is True
    assert repaired["event_count"] == 12


def test_missing_request_returns_404(client):
    response = client.get("/api/requests/not-a-uuid")

    assert response.status_code == 404
    assert response.json() == {"detail": "Request not found"}


def test_request_rejects_fractional_cent_amount(client):
    response = client.post(
        "/api/requests",
        json={
            "applicant_id": "fractional-cent",
            "amount": 450000.001,
            "term_days": 60,
            "payment_delay_days": 2,
            "counterparty_risk": 0.12,
            "invoice_mismatch": False,
            "relationship_months": 48,
            "transactions_last_30d": 8,
        },
    )

    assert response.status_code == 422
    assert client.get("/api/requests").json() == []


def test_health_hides_database_exception_details(client, monkeypatch):
    def unavailable():
        raise SQLAlchemyError(
            "postgresql://user:secret@internal-host/research"
        )

    monkeypatch.setattr(client.app.state.database, "is_reachable", unavailable)

    response = client.get("/api/health")

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "database_unavailable",
            "message": "PostgreSQL is unavailable",
        }
    }
    assert "secret" not in response.text
    assert "internal-host" not in response.text
