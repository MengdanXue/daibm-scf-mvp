import pytest
from fastapi.testclient import TestClient

from app.database import Database
from app.main import create_app


@pytest.fixture
def workflow_client(migrated_engine, session_factory):
    database = Database(migrated_engine, session_factory)
    with TestClient(create_app(database)) as client:
        yield client


def login(client: TestClient, username: str) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "Demo123!"},
    )
    assert response.status_code == 200


def logout(client: TestClient) -> None:
    assert client.post("/api/v1/auth/logout").status_code == 204


def application_payload() -> dict:
    return {
        "core_enterprise_organization_code": "CORE-001",
        "contract_number": "SCF-API-2026-001",
        "invoice_number": "INV-API-2026-001",
        "amount": 1_200_000,
        "term_days": 90,
        "payment_delay_days": 18,
        "counterparty_risk": 0.58,
        "invoice_mismatch": True,
        "relationship_months": 18,
        "transactions_last_30d": 12,
    }


def test_five_authenticated_roles_complete_the_versioned_api_journey(
    workflow_client,
):
    login(workflow_client, "supplier.demo")
    created = workflow_client.post(
        "/api/v1/applications", json=application_payload()
    )
    assert created.status_code == 201
    application = created.json()
    request_id = application["request_id"]
    assert application["status"] == "draft"
    assert application["allowed_actions"] == ["update", "submit"]
    supplier_tasks = workflow_client.get("/api/v1/tasks").json()
    assert supplier_tasks["count"] == 1
    submitted = workflow_client.post(
        f"/api/v1/applications/{request_id}/submit",
        json={"version": application["version"]},
    )
    assert submitted.status_code == 200
    assert submitted.json()["status"] == "submitted"
    logout(workflow_client)

    login(workflow_client, "core.demo")
    core_tasks = workflow_client.get("/api/v1/tasks").json()
    assert core_tasks["count"] == 1
    confirmed = workflow_client.post(
        f"/api/v1/applications/{request_id}/trade-confirmation",
        json={
            "version": submitted.json()["version"],
            "confirmed": True,
            "comment": "Contract and invoice confirmed",
        },
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "trade_confirmed"
    logout(workflow_client)

    login(workflow_client, "financier.demo")
    assessed = workflow_client.post(
        f"/api/v1/applications/{request_id}/risk-assessment",
        json={"version": confirmed.json()["version"]},
    )
    assert assessed.status_code == 200
    assert assessed.json()["status"] == "risk_assessed"
    assert assessed.json()["risk_score"] is not None
    decided = workflow_client.post(
        f"/api/v1/applications/{request_id}/decision",
        json={
            "version": assessed.json()["version"],
            "decision": "manual_review",
            "comment": "Additional verification required",
        },
    )
    assert decided.status_code == 200
    assert decided.json()["decision"] == "manual_review"
    logout(workflow_client)

    login(workflow_client, "risk.demo")
    controlled = workflow_client.post(
        f"/api/v1/applications/{request_id}/control-action",
        json={
            "version": decided.json()["version"],
            "comment": "Enhanced validation assigned",
        },
    )
    assert controlled.status_code == 200
    assert controlled.json()["status"] == "controlled"
    logout(workflow_client)

    login(workflow_client, "auditor.demo")
    audited = workflow_client.post(
        f"/api/v1/applications/{request_id}/audit-review",
        json={
            "version": controlled.json()["version"],
            "comment": "Workflow and ledger verified",
        },
    )
    assert audited.status_code == 200
    final = audited.json()
    assert final["status"] == "audited"
    assert final["version"] == 7
    assert len(final["timeline"]) == 7
    assert workflow_client.get("/api/ledger/verify").json()["valid"] is True


def test_workflow_api_rejects_wrong_role_and_stale_version(workflow_client):
    login(workflow_client, "supplier.demo")
    created = workflow_client.post(
        "/api/v1/applications", json=application_payload()
    ).json()
    request_id = created["request_id"]

    forbidden = workflow_client.post(
        f"/api/v1/applications/{request_id}/trade-confirmation",
        json={"version": 1, "confirmed": True, "comment": "forged"},
    )
    assert forbidden.status_code == 403
    assert forbidden.json()["detail"]["code"] == "forbidden_role"

    first_submit = workflow_client.post(
        f"/api/v1/applications/{request_id}/submit", json={"version": 1}
    )
    assert first_submit.status_code == 200
    stale = workflow_client.post(
        f"/api/v1/applications/{request_id}/submit", json={"version": 1}
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "stale_application"


def test_workflow_api_hides_data_from_unauthenticated_callers(workflow_client):
    for path in (
        "/api/v1/tasks",
        "/api/v1/dashboard",
        "/api/v1/applications",
    ):
        response = workflow_client.get(path)
        assert response.status_code == 401
        assert response.json()["detail"]["code"] == "authentication_required"


def test_unknown_application_uses_structured_non_leaking_404(workflow_client):
    login(workflow_client, "auditor.demo")

    response = workflow_client.get(
        "/api/v1/applications/00000000-0000-0000-0000-000000000000"
    )

    assert response.status_code == 404
    assert response.json() == {
        "detail": {
            "code": "application_not_found",
            "message": "Application not found",
        }
    }
