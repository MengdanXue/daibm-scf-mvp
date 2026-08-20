from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.config import ResearchSettings
from app.database import Database
from app.main import create_app
from app.models import LedgerEventModel
from app.models_research import PolicyDecisionModel, RiskAssessmentModel
from app.repositories.ledger import LedgerRepository


@pytest.fixture
def decision_client(migrated_engine, session_factory, login_user):
    application = create_app(
        Database(migrated_engine, session_factory),
        research_settings=ResearchSettings.from_env({}),
    )
    with TestClient(application) as client:
        login_user(client, "financier.demo")
        yield client


def inference_payload(client):
    status = client.get("/api/research/status").json()
    return {
        "enterprise_id": "E0001",
        "graph_snapshot_id": status["graph"]["graph_snapshot_id"],
        "model_version_id": status["model"]["model_version_id"],
    }


def test_research_inference_persists_assessment_policy_and_three_events_atomically(
    decision_client,
    session_factory,
):
    response = decision_client.post(
        "/api/research/inference",
        json=inference_payload(decision_client),
    )

    assert response.status_code == 200
    trace = response.json()
    assert trace["persisted"] is True
    assert trace["policy_decision"]["policy_version"] == "scf-risk-policy-v0.4"
    assert trace["policy_decision"]["thresholds"] == [0.4, 0.75]
    assert [event["event_type"] for event in trace["ledger_events"]] == [
        "MODEL_INFERENCE_COMPLETED",
        "RISK_POLICY_TRIGGERED",
        "CONTROL_ACTION_REQUESTED",
    ]
    assert trace["lineage"]["checkpoint_sha256"] == (
        "47de64d9cea91abe492771a4b0c6d3694d4bfffd6e7b4834dac0d5ae617c4cab"
    )

    with session_factory() as session:
        assert session.scalar(
            select(func.count()).select_from(RiskAssessmentModel)
        ) == 1
        assert session.scalar(
            select(func.count()).select_from(PolicyDecisionModel)
        ) == 1
        assert session.scalar(
            select(func.count()).select_from(LedgerEventModel)
        ) == 3

    fetched = decision_client.get(
        f"/api/research/assessments/{trace['risk_assessment_id']}/trace"
    )
    assert fetched.status_code == 200
    assert fetched.json()["risk_assessment_id"] == trace["risk_assessment_id"]
    assert fetched.json()["policy_decision"] == trace["policy_decision"]


def test_research_persistence_rolls_back_when_third_ledger_event_fails(
    decision_client,
    session_factory,
    monkeypatch,
):
    repository = decision_client.app.state.research_decision_service.ledger_repository
    original = repository._build_event
    calls = 0

    def fail_on_third(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError("forced research ledger failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(repository, "_build_event", fail_on_third)
    with pytest.raises(RuntimeError, match="forced research ledger failure"):
        decision_client.post(
            "/api/research/inference",
            json=inference_payload(decision_client),
        )

    with session_factory() as session:
        assert session.scalar(
            select(func.count()).select_from(RiskAssessmentModel)
        ) == 0
        assert session.scalar(
            select(func.count()).select_from(PolicyDecisionModel)
        ) == 0
        assert session.scalar(
            select(func.count()).select_from(LedgerEventModel)
        ) == 0


def test_postgresql_audit_adapter_rejects_non_global_stream(session_factory):
    repository = LedgerRepository()

    with pytest.raises(ValueError, match="global"):
        with session_factory.begin() as session:
            repository.append_many(
                session,
                uuid4(),
                [("MODEL_INFERENCE_COMPLETED", {"score": 0.5})],
                stream_id="enterprise:E0001",
            )
