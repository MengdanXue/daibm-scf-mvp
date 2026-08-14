from fastapi.testclient import TestClient

from app.main import create_app


def test_demo_creates_closed_loop_and_valid_ledger(tmp_path):
    app = create_app(tmp_path / "api.db")
    with TestClient(app) as client:
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

        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"

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
