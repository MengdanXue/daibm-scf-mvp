import hashlib
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from app.config import ResearchSettings
from app.database import Database
from app.main import create_app
from app.models import LedgerEventModel
from app.models_research import (
    DatasetVersionModel,
    GraphSnapshotModel,
    PolicyDecisionModel,
    RiskAssessmentModel,
    SyntheticScenarioModel,
)
from app.repositories.research import ResearchRepository
from research.data.generator import generate_dataset


@pytest.fixture
def scenario_client(migrated_engine, session_factory, login_user):
    application = create_app(
        Database(migrated_engine, session_factory),
        research_settings=ResearchSettings.from_env({}),
    )
    with TestClient(application) as client:
        login_user(client, "auditor.demo")
        yield client


def test_risk_injection_versions_inputs_rebuilds_graph_and_reruns_model(
    scenario_client,
    session_factory,
):
    original_hash = hashlib.sha256(generate_dataset().canonical_bytes()).hexdigest()

    response = scenario_client.post(
        "/api/research/scenarios/E0001/inject-risk",
        json={},
    )

    assert response.status_code == 200
    result = response.json()
    assert result["scenario"]["revision"] == 1
    assert result["scenario"]["provenance"] == "SIMULATED"
    assert result["before"]["real_model_inference"] is True
    assert result["after"]["real_model_inference"] is True
    assert result["before"]["input_sha256"] != result["after"]["input_sha256"]
    assert result["before"]["graph_snapshot_id"] != result["after"][
        "graph_snapshot_id"
    ]
    assert result["after"]["risk_score"] > result["before"]["risk_score"]
    assert result["after"]["policy_decision"]["decision"] == "FINANCING_REVIEW"
    assert result["scenario"]["relationship_direction"] == "supplier_to_customer"
    assert result["scenario"]["affected_enterprise_ids"][0] == "E0001"
    assert len(result["scenario"]["affected_enterprise_ids"]) > 1
    assert {item["feature"] for item in result["changed_inputs"]} >= {
        "liquidity_ratio",
        "leverage_ratio",
        "overdue_ratio",
    }
    assert hashlib.sha256(generate_dataset().canonical_bytes()).hexdigest() == original_hash

    with session_factory() as session:
        assert session.scalar(
            select(func.count()).select_from(DatasetVersionModel)
        ) == 1
        assert session.scalar(
            select(func.count()).select_from(SyntheticScenarioModel)
        ) == 2
        assert session.scalar(
            select(func.count()).select_from(GraphSnapshotModel)
        ) == 2
        assert session.scalar(
            select(func.count()).select_from(RiskAssessmentModel)
        ) == 2
        assert session.scalar(
            select(func.count()).select_from(PolicyDecisionModel)
        ) == 2
        assert session.scalar(
            select(func.count()).select_from(LedgerEventModel)
        ) == 7
        event_types = set(
            session.scalars(select(LedgerEventModel.event_type))
        )
        scenario_snapshot = session.get(
            GraphSnapshotModel,
            UUID(result["after"]["graph_snapshot_id"]),
        )
        after_events = list(
            session.scalars(
                select(LedgerEventModel).where(
                    LedgerEventModel.entity_id
                    == UUID(result["after"]["risk_assessment_id"])
                )
            )
        )
    assert scenario_snapshot is not None
    after_lineage = result["after"]["lineage"]
    assert after_lineage["graph_snapshot_sha256"] == (
        scenario_snapshot.content_sha256
    )
    assert after_lineage["graph_snapshot_sha256"] != result["before"][
        "lineage"
    ]["graph_snapshot_sha256"]
    assert after_lineage["synthetic_scenario_id"] == result["scenario"][
        "synthetic_scenario_id"
    ]
    assert after_lineage["scenario_revision"] == result["scenario"]["revision"]
    assert after_lineage["overlay_sha256"] == result["scenario"][
        "overlay_sha256"
    ]
    assert len(after_events) == 3
    assert all(
        event.payload["graph_snapshot_sha256"]
        == scenario_snapshot.content_sha256
        for event in after_events
    )
    fetched_trace = scenario_client.get(
        "/api/research/assessments/"
        + result["after"]["risk_assessment_id"]
        + "/trace"
    ).json()
    assert fetched_trace["lineage"] == after_lineage
    assert "SIMULATED_RISK_INJECTED" in event_types
    assert scenario_client.get("/api/ledger/verify").json()["valid"] is True


def test_risk_injection_rejects_caller_supplied_score(scenario_client):
    response = scenario_client.post(
        "/api/research/scenarios/E0001/inject-risk",
        json={"risk_score": 0.9999},
    )

    assert response.status_code == 422


def test_scenario_reset_selects_registered_revision_zero(scenario_client):
    scenario_client.post(
        "/api/research/scenarios/E0001/inject-risk",
        json={},
    )

    response = scenario_client.post("/api/research/scenarios/reset")

    assert response.status_code == 200
    assert response.json()["scenario"]["name"] == "reference"
    assert response.json()["scenario"]["revision"] == 0
    assert response.json()["graph"]["anchor_month"] == 21


def test_scenario_revision_allocator_holds_dataset_name_advisory_lock(
    scenario_client,
    session_factory,
):
    repository = ResearchRepository()
    dataset_id = scenario_client.app.state.research_service.identities[
        "dataset_version_id"
    ]
    scenario_name = "risk-demo:E0001"
    lock_key = repository.scenario_lock_key(dataset_id, scenario_name)

    with session_factory.begin() as first:
        assert repository.next_scenario_revision(
            first, dataset_id, scenario_name
        ) == 1
        with session_factory.begin() as competing:
            acquired = competing.scalar(
                text("SELECT pg_try_advisory_xact_lock(:lock_key)"),
                {"lock_key": lock_key},
            )
        assert acquired is False

    with session_factory.begin() as after_commit:
        assert after_commit.scalar(
            text("SELECT pg_try_advisory_xact_lock(:lock_key)"),
            {"lock_key": lock_key},
        ) is True
