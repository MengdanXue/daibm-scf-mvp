from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import ResearchSettings
from app.database import Database
from app.main import create_app


@pytest.fixture
def research_client(migrated_engine, session_factory):
    database = Database(
        engine=migrated_engine,
        session_factory=session_factory,
    )
    application = create_app(
        database,
        research_settings=ResearchSettings(
            reference_dir=Path("artifacts/reference"),
            required=True,
        ),
    )
    with TestClient(application) as client:
        yield client


def test_research_status_exposes_registered_lineage(research_client):
    response = research_client.get("/api/research/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ready"
    assert payload["data_provenance"] == "SYNTHETIC_DEMO"
    assert payload["dataset"]["name"] == "synthetic-scf-v1"
    assert payload["dataset"]["enterprise_count"] == 500
    assert payload["graph"]["anchor_month"] == 21
    assert payload["graph"]["window"] == [10, 21]
    assert payload["model"]["family"] == "tgnn"
    assert payload["model"]["lifecycle_status"] == "promoted"
    assert payload["model"]["deployment_slot"] == "default"
    assert payload["model"]["real_inference"] is True


def test_research_inference_runs_promoted_onnx_with_traceable_persistence(
    research_client,
):
    status = research_client.get("/api/research/status").json()

    response = research_client.post(
        "/api/research/inference",
        json={
            "enterprise_id": "E0001",
            "graph_snapshot_id": status["graph"]["graph_snapshot_id"],
            "model_version_id": status["model"]["model_version_id"],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["enterprise_id"] == "E0001"
    assert 0.0 <= payload["risk_score"] <= 1.0
    assert payload["inference_engine"] == "onnxruntime-cpu"
    assert payload["real_model_inference"] is True
    assert payload["synthetic_data"] is True
    assert payload["persisted"] is True
    assert payload["policy_decision"]["decision"] in {
        "NORMAL",
        "ADDITIONAL_CHECK",
        "FINANCING_REVIEW",
    }
    assert len(payload["ledger_events"]) == 3
    assert len(payload["input_sha256"]) == 64
    assert len(payload["explanations"]) == 3


def test_research_inference_rejects_unknown_model(research_client):
    status = research_client.get("/api/research/status").json()

    response = research_client.post(
        "/api/research/inference",
        json={
            "enterprise_id": "E0001",
            "graph_snapshot_id": status["graph"]["graph_snapshot_id"],
            "model_version_id": str(uuid4()),
        },
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Research model or snapshot not found"}


def test_optional_missing_artifact_keeps_engineering_health_and_returns_503(
    migrated_engine,
    session_factory,
    tmp_path,
):
    database = Database(
        engine=migrated_engine,
        session_factory=session_factory,
    )
    application = create_app(
        database,
        research_settings=ResearchSettings(
            reference_dir=tmp_path / "missing",
            required=False,
        ),
    )
    with TestClient(application) as client:
        health = client.get("/api/health")
        inference = client.post(
            "/api/research/inference",
            json={
                "enterprise_id": "E0001",
                "graph_snapshot_id": str(uuid4()),
                "model_version_id": str(uuid4()),
            },
        )

    assert health.status_code == 200
    assert health.json()["research_core"]["status"] == "unavailable"
    assert inference.status_code == 503
    assert inference.json() == {
        "detail": {
            "code": "research_model_unavailable",
            "message": "Promoted research model is unavailable",
        }
    }
    assert str(tmp_path) not in inference.text
