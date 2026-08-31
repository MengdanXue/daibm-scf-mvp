from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import ResearchSettings
from app.database import Database
from app.main import create_app
from app.models import FinancingRequestModel
from app.models_facility import FinancingFacilityModel
from app.models_research import (
    DatasetVersionModel,
    GraphSnapshotModel,
    ModelRunModel,
    ModelVersionModel,
    RiskAssessmentModel,
)


ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def outcome_client(migrated_engine, session_factory, tmp_path):
    database = Database(migrated_engine, session_factory)
    application = create_app(
        database,
        research_settings=ResearchSettings(
            reference_dir=ROOT / "artifacts" / "reference",
            required=True,
        ),
        calibration_artifact_dir=tmp_path,
    )
    with TestClient(application) as client:
        yield client


def _seed_closed_facility(session_factory) -> uuid.UUID:
    from app.models_identity import UserModel

    with session_factory() as session:
        financier_id = session.query(UserModel.user_id).filter_by(
            username="financier.demo"
        ).scalar()
    dataset_id = uuid.uuid4()
    snapshot_id = uuid.uuid4()
    model_run_id = uuid.uuid4()
    model_version_id = uuid.uuid4()
    model_semantic_version = str(uuid.uuid4())
    assessment_id = uuid.uuid4()
    request_id = uuid.uuid4()
    facility_id = uuid.uuid4()
    with session_factory.begin() as session:
        session.add(
            DatasetVersionModel(
                dataset_version_id=dataset_id,
                name="outcome-api",
                version=str(uuid.uuid4()),
                generation_seed=1,
                schema_version="v1",
                manifest={},
                content_sha256=uuid.uuid4().hex * 2,
                created_at=NOW,
            )
        )
        session.add(
            GraphSnapshotModel(
                graph_snapshot_id=snapshot_id,
                dataset_version_id=dataset_id,
                synthetic_scenario_id=None,
                scenario_revision=None,
                overlay_sha256=None,
                anchor_month=12,
                window_start_month=1,
                window_end_month=12,
                feature_schema_version="v1",
                normalization_id="norm-v1",
                node_ordering_sha256="1" * 64,
                adjacency_sha256="2" * 64,
                feature_sha256="3" * 64,
                content_sha256=uuid.uuid4().hex * 2,
                storage_locator="memory://snapshot",
                created_at=NOW,
            )
        )
        session.add(
            ModelRunModel(
                model_run_id=model_run_id,
                model_family="tgnn",
                run_seed=1,
                dataset_version_id=dataset_id,
                configuration={},
                status="completed",
                started_at=NOW,
                ended_at=NOW,
                metrics={},
            )
        )
        session.add(
            ModelVersionModel(
                model_version_id=model_version_id,
                model_name="tgnn-api",
                semantic_version=model_semantic_version,
                model_family="tgnn",
                source_run_id=model_run_id,
                dataset_version_id=dataset_id,
                feature_schema_version="v1",
                inference_format="onnx",
                artifact_locator="memory://model",
                checkpoint_sha256="4" * 64,
                metrics={},
                lifecycle_status="candidate",
                deployment_slot=None,
                created_at=NOW,
            )
        )
        session.add(
            RiskAssessmentModel(
                risk_assessment_id=assessment_id,
                enterprise_id="E0001",
                graph_snapshot_id=snapshot_id,
                model_version_id=model_version_id,
                input_sha256="5" * 64,
                risk_score=0.70,
                band="HIGH",
                explanations=[],
                inferred_at=NOW,
            )
        )
        session.add(
            FinancingRequestModel(
                request_id=request_id,
                created_at=NOW,
                updated_at=NOW,
                applicant_id="E0001",
                assessment_scope="controlled_demo",
                amount=Decimal("1000.00"),
                term_days=30,
                features={},
                risk_score=0.70,
                decision="approved",
                status="audited",
                version=1,
                risk_assessment_id=assessment_id,
                risk_engine_version=f"tgnn-api@{model_semantic_version}",
                risk_input_sha256="5" * 64,
                risk_assessed_at=NOW,
            )
        )
        session.add(
            FinancingFacilityModel(
                facility_id=facility_id,
                request_id=request_id,
                principal=Decimal("1000.00"),
                outstanding_amount=Decimal("0.00"),
                currency="CNY",
                status="closed",
                version=7,
                current_schedule_version=1,
                closure_reason="repaid",
                created_by_user_id=financier_id,
                created_at=NOW,
                updated_at=NOW,
                closed_at=NOW,
            )
        )
    return facility_id


def _login(client: TestClient, username: str) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "Demo123!"},
    )
    assert response.status_code == 200


def _payload(key: uuid.UUID | None = None) -> dict:
    return {
        "idempotency_key": str(key or uuid.uuid4()),
        "observed_at": "2026-08-24T12:00:00Z",
        "evidence_sha256": "a" * 64,
        "provenance": "CONTROLLED_DEMO",
    }


def _command(version: int) -> dict:
    return {"version": version, "idempotency_key": str(uuid.uuid4())}


def test_real_business_workflow_closes_and_records_baseline_outcome(outcome_client):
    _login(outcome_client, "supplier.demo")
    created = outcome_client.post(
        "/api/v1/applications",
        json={
            "core_enterprise_organization_code": "CORE-001",
            "contract_number": "SCF-OUTCOME-E2E-001",
            "invoice_number": "INV-OUTCOME-E2E-001",
            "amount": 1000,
            "term_days": 90,
            "payment_delay_days": 5,
            "counterparty_risk": 0.2,
            "invoice_mismatch": False,
            "relationship_months": 36,
            "transactions_last_30d": 20,
        },
    ).json()
    request_id = created["request_id"]
    submitted = outcome_client.post(
        f"/api/v1/applications/{request_id}/submit",
        json={"version": created["version"]},
    ).json()

    _login(outcome_client, "core.demo")
    confirmed = outcome_client.post(
        f"/api/v1/applications/{request_id}/trade-confirmation",
        json={
            "version": submitted["version"],
            "confirmed": True,
            "comment": "Verified",
        },
    ).json()

    _login(outcome_client, "financier.demo")
    assessed = outcome_client.post(
        f"/api/v1/applications/{request_id}/risk-assessment",
        json={"version": confirmed["version"]},
    ).json()
    decided = outcome_client.post(
        f"/api/v1/applications/{request_id}/decision",
        json={
            "version": assessed["version"],
            "decision": "approved",
            "comment": "Approved",
        },
    ).json()

    _login(outcome_client, "risk.demo")
    controlled = outcome_client.post(
        f"/api/v1/applications/{request_id}/control-action",
        json={"version": decided["version"], "comment": "Controls complete"},
    ).json()

    _login(outcome_client, "auditor.demo")
    audited = outcome_client.post(
        f"/api/v1/applications/{request_id}/audit-review",
        json={"version": controlled["version"], "comment": "Audit complete"},
    ).json()

    _login(outcome_client, "financier.demo")
    facility_response = outcome_client.post(
        "/api/v1/facilities",
        json={
            **_command(1),
            "request_id": request_id,
            "principal": "1000.00",
            "currency": "RUB",
            "installments": [
                {"sequence": 1, "due_date": "2026-12-01", "amount": "1000.00"}
            ],
        },
    )
    assert facility_response.status_code == 201, facility_response.text
    facility = facility_response.json()
    facility_id = facility["facility_id"]
    initiated = outcome_client.post(
        f"/api/v1/facilities/{facility_id}/initiate-disbursement",
        json=_command(facility["version"]),
    ).json()
    active = outcome_client.post(
        f"/api/v1/facilities/{facility_id}/confirm-disbursement",
        json=_command(initiated["version"]),
    ).json()

    _login(outcome_client, "supplier.demo")
    payment_state = outcome_client.post(
        f"/api/v1/facilities/{facility_id}/payments",
        json={
            **_command(active["version"]),
            "installment_id": active["installments"][0]["installment_id"],
            "amount": "1000.00",
            "payment_reference": "PAY-OUTCOME-E2E-001",
        },
    ).json()
    payment_id = payment_state["payments"][0]["payment_id"]

    _login(outcome_client, "financier.demo")
    repaid = outcome_client.post(
        f"/api/v1/facilities/{facility_id}/payments/{payment_id}/decision",
        json={
            **_command(payment_state["version"]),
            "decision": "confirmed",
            "comment": "Payment verified",
        },
    ).json()

    _login(outcome_client, "auditor.demo")
    closed = outcome_client.post(
        f"/api/v1/facilities/{facility_id}/close",
        json=_command(repaid["version"]),
    )
    assert closed.status_code == 200, closed.text
    outcome_payload = _payload()
    outcome_payload["observed_at"] = datetime.now(timezone.utc).isoformat()

    result = outcome_client.post(
        f"/api/v1/facilities/{facility_id}/actual-outcome",
        json=outcome_payload,
    )

    assert result.status_code == 201, result.text
    body = result.json()
    assert body["outcome"]["risk_engine_version"] == (
        "transparent_logistic_baseline_v0.1"
    )
    assert body["outcome"]["model_version_id"] is None
    assert body["outcome"]["defaulted"] is False
    assert body["outcome"]["loss_amount"] == "0.00"
    assert body["calibration_job"]["status"] == "queued"


def test_outcome_routes_are_auditor_only(outcome_client, session_factory):
    facility_id = _seed_closed_facility(session_factory)
    assert outcome_client.get("/api/v1/outcomes").status_code == 401

    _login(outcome_client, "financier.demo")
    assert outcome_client.get("/api/v1/outcomes").status_code == 403
    assert outcome_client.post(
        f"/api/v1/facilities/{facility_id}/actual-outcome", json=_payload()
    ).status_code == 403


def test_auditor_submits_and_queries_derived_outcome_and_queued_job(
    outcome_client,
    session_factory,
):
    facility_id = _seed_closed_facility(session_factory)
    _login(outcome_client, "auditor.demo")
    payload = _payload()

    created = outcome_client.post(
        f"/api/v1/facilities/{facility_id}/actual-outcome",
        json=payload,
    )

    assert created.status_code == 201
    body = created.json()
    assert body["outcome"]["facility_id"] == str(facility_id)
    assert body["outcome"]["effective_training_eligible"] is True
    assert body["calibration_job"]["status"] == "queued"
    assert body["calibration_job"]["deployment_scope"] == "controlled_demo"
    assert "artifact_locator" not in created.text
    outcome_id = body["outcome"]["outcome_id"]
    assert outcome_client.get("/api/v1/outcomes").json() == [body["outcome"]]
    assert outcome_client.get(f"/api/v1/outcomes/{outcome_id}").json() == body[
        "outcome"
    ]
    assert outcome_client.get("/api/v1/calibration-runs").json() == []
    assert (
        outcome_client.get("/api/v1/calibration-deployments/active").status_code
        == 404
    )

    replay = outcome_client.post(
        f"/api/v1/facilities/{facility_id}/actual-outcome",
        json=payload,
    )
    assert replay.status_code == 201
    assert replay.json() == body


def test_outcome_api_maps_validation_conflict_and_not_found_without_path_leaks(
    outcome_client,
    session_factory,
):
    facility_id = _seed_closed_facility(session_factory)
    _login(outcome_client, "auditor.demo")
    invalid = _payload()
    invalid["evidence_sha256"] = "A" * 64
    assert outcome_client.post(
        f"/api/v1/facilities/{facility_id}/actual-outcome", json=invalid
    ).status_code == 422

    created_payload = _payload()
    assert outcome_client.post(
        f"/api/v1/facilities/{facility_id}/actual-outcome", json=created_payload
    ).status_code == 201
    conflict = _payload(uuid.UUID(created_payload["idempotency_key"]))
    conflict["evidence_sha256"] = "b" * 64
    response = outcome_client.post(
        f"/api/v1/facilities/{facility_id}/actual-outcome", json=conflict
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "outcome_conflict"
    assert "AppData" not in response.text and "artifacts" not in response.text
    assert outcome_client.get(f"/api/v1/outcomes/{uuid.uuid4()}").status_code == 404
    assert (
        outcome_client.get(f"/api/v1/calibration-runs/{uuid.uuid4()}").status_code
        == 404
    )


def test_api_rejects_client_facts_and_exposes_preview_and_corrections(
    outcome_client, session_factory
):
    facility_id = _seed_closed_facility(session_factory)
    _login(outcome_client, "auditor.demo")
    preview = outcome_client.get(
        f"/api/v1/facilities/{facility_id}/actual-outcome-preview"
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["loss_amount"] == "0.00"
    assert "artifact" not in preview.text

    unsafe = {**_payload(), "defaulted": False}
    assert outcome_client.post(
        f"/api/v1/facilities/{facility_id}/actual-outcome", json=unsafe
    ).status_code == 422
    created = outcome_client.post(
        f"/api/v1/facilities/{facility_id}/actual-outcome", json=_payload()
    )
    assert created.status_code == 201, created.text
    outcome_id = created.json()["outcome"]["outcome_id"]
    correction = {
        "idempotency_key": str(uuid.uuid4()),
        "action": "EXCLUDE",
        "reason_code": "inconsistent_lifecycle",
        "comment": "  Verified governance discrepancy.  ",
        "evidence_sha256": "b" * 64,
    }
    response = outcome_client.post(
        f"/api/v1/outcomes/{outcome_id}/corrections", json=correction
    )
    assert response.status_code == 201, response.text
    assert response.json()["effective_training_eligible"] is False
    assert response.json()["calibration_job"]["trigger_type"] == "correction_exclude"
    history = outcome_client.get(f"/api/v1/outcomes/{outcome_id}/corrections")
    assert history.status_code == 200
    assert history.json()[0]["reason_code"] == "INCONSISTENT_LIFECYCLE"
    assert "artifact_locator" not in response.text


def test_correction_routes_are_auditor_only(outcome_client, session_factory):
    facility_id = _seed_closed_facility(session_factory)
    _login(outcome_client, "auditor.demo")
    outcome_id = outcome_client.post(
        f"/api/v1/facilities/{facility_id}/actual-outcome", json=_payload()
    ).json()["outcome"]["outcome_id"]
    _login(outcome_client, "financier.demo")
    assert outcome_client.get(
        f"/api/v1/outcomes/{outcome_id}/corrections"
    ).status_code == 403
    assert outcome_client.post(
        f"/api/v1/outcomes/{outcome_id}/corrections",
        json={
            "idempotency_key": str(uuid.uuid4()), "action": "EXCLUDE",
            "reason_code": "INCONSISTENT_LIFECYCLE", "comment": "Verified",
            "evidence_sha256": "b" * 64,
        },
    ).status_code == 403
