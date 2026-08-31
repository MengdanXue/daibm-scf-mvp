from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.database import Database
from app.identity import hash_password
from app.main import create_app
from app.models import FinancingRequestModel
from app.models_identity import OrganizationModel, UserModel


@pytest.fixture
def facility_client(migrated_engine, session_factory):
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


def _application(
    session_factory,
    *,
    status: str = "audited",
    decision: str = "approved",
    amount: str = "1000.00",
) -> uuid.UUID:
    request_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    with session_factory.begin() as session:
        users = {
            user.username: user
            for user in session.scalars(select(UserModel)).all()
        }
        organizations = {
            organization.organization_code: organization
            for organization in session.scalars(select(OrganizationModel)).all()
        }
        session.add(
            FinancingRequestModel(
                request_id=request_id,
                created_at=now,
                updated_at=now,
                applicant_id="supplier.demo",
                amount=Decimal(amount),
                term_days=90,
                features={},
                risk_score=0.1,
                decision=decision,
                explanations=[],
                status=status,
                version=7,
                created_by_user_id=users["supplier.demo"].user_id,
                supplier_organization_id=organizations[
                    "SUPPLIER-001"
                ].organization_id,
                core_enterprise_organization_id=organizations[
                    "CORE-001"
                ].organization_id,
            )
        )
    return request_id


def _create_payload(
    request_id: uuid.UUID,
    *,
    key: uuid.UUID | None = None,
    due_date: str = "2026-01-01",
) -> dict:
    return {
        "request_id": str(request_id),
        "principal": "1000.00",
        "currency": "RUB",
        "version": 1,
        "idempotency_key": str(key or uuid.uuid4()),
        "installments": [
            {"sequence": 1, "due_date": due_date, "amount": "1000.00"}
        ],
    }


def _command(version: int, *, key: uuid.UUID | None = None) -> dict:
    return {
        "version": version,
        "idempotency_key": str(key or uuid.uuid4()),
    }


def _create_facility(client, session_factory) -> dict:
    request_id = _application(session_factory)
    _login(client, "financier.demo")
    response = client.post(
        "/api/v1/facilities",
        json=_create_payload(request_id),
    )
    assert response.status_code == 201
    return response.json()


def test_facility_routes_require_authentication(
    facility_client,
):
    for method, path, kwargs in (
        ("get", "/api/v1/facilities", {}),
        (
            "post",
            "/api/v1/facilities",
            {"json": _create_payload(uuid.uuid4())},
        ),
    ):
        response = getattr(facility_client, method)(path, **kwargs)
        assert response.status_code == 401
        assert response.json()["detail"]["code"] == "authentication_required"


def test_only_financier_can_create_and_money_remains_strings(
    facility_client,
    session_factory,
):
    request_id = _application(session_factory)
    payload = _create_payload(request_id)
    _login(facility_client, "supplier.demo")
    forbidden = facility_client.post("/api/v1/facilities", json=payload)
    assert forbidden.status_code == 403
    assert forbidden.json()["detail"]["code"] == "forbidden_role"

    _login(facility_client, "financier.demo")
    created = facility_client.post("/api/v1/facilities", json=payload)
    assert created.status_code == 201
    body = created.json()
    assert body["principal"] == "1000.00"
    assert body["outstanding_amount"] == "1000.00"
    assert body["installments"][0]["amount"] == "1000.00"
    assert body["installments"][0]["paid_amount"] == "0.00"


@pytest.mark.parametrize(
    ("status", "decision"),
    (("audited", "rejected"), ("controlled", "approved")),
)
def test_rejected_or_non_audited_application_cannot_create_facility(
    facility_client,
    session_factory,
    status,
    decision,
):
    request_id = _application(
        session_factory,
        status=status,
        decision=decision,
    )
    _login(facility_client, "financier.demo")
    response = facility_client.post(
        "/api/v1/facilities",
        json=_create_payload(request_id),
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "facility_precondition_failed"


def test_list_is_paginated_and_detail_hides_another_organization(
    facility_client,
    session_factory,
):
    first = _create_facility(facility_client, session_factory)
    second = _create_facility(facility_client, session_factory)

    page = facility_client.get("/api/v1/facilities?limit=1&offset=1")
    assert page.status_code == 200
    assert len(page.json()) == 1
    assert page.json()[0]["facility_id"] in {
        first["facility_id"],
        second["facility_id"],
    }
    assert facility_client.get(
        "/api/v1/facilities?limit=0&offset=0"
    ).status_code == 422

    now = datetime.now(timezone.utc)
    password_hash, password_salt = hash_password("Demo123!")
    with session_factory.begin() as session:
        organization = OrganizationModel(
            organization_id=uuid.uuid4(),
            organization_code="BANK-OUTSIDE",
            name="Outside financier",
            organization_type="financier",
            created_at=now,
        )
        session.add(organization)
        session.add(
            UserModel(
                user_id=uuid.uuid4(),
                username="financier.outside",
                display_name="Outside financier",
                password_hash=password_hash,
                password_salt=password_salt,
                role="financier",
                organization_id=organization.organization_id,
                is_active=True,
                created_at=now,
            )
        )
    _login(facility_client, "financier.outside")
    hidden = facility_client.get(
        f"/api/v1/facilities/{first['facility_id']}"
    )
    assert hidden.status_code == 404
    assert hidden.json()["detail"]["code"] == "facility_not_found"
    assert facility_client.get("/api/v1/facilities").json() == []


def test_five_roles_enforce_and_complete_all_action_resources(
    facility_client,
    session_factory,
):
    facility = _create_facility(facility_client, session_factory)
    facility_id = facility["facility_id"]

    _login(facility_client, "core.demo")
    forbidden = facility_client.post(
        f"/api/v1/facilities/{facility_id}/initiate-disbursement",
        json=_command(facility["version"]),
    )
    assert forbidden.status_code == 403
    assert forbidden.json()["detail"]["code"] == "forbidden_role"

    _login(facility_client, "financier.demo")
    initiated = facility_client.post(
        f"/api/v1/facilities/{facility_id}/initiate-disbursement",
        json=_command(facility["version"]),
    )
    assert initiated.status_code == 200
    confirmed = facility_client.post(
        f"/api/v1/facilities/{facility_id}/confirm-disbursement",
        json=_command(initiated.json()["version"]),
    )
    assert confirmed.status_code == 200
    active = confirmed.json()
    assert "mark_overdue" in active["allowed_actions"]

    _login(facility_client, "supplier.demo")
    submitted = facility_client.post(
        f"/api/v1/facilities/{facility_id}/payments",
        json={
            **_command(active["version"]),
            "installment_id": active["installments"][0]["installment_id"],
            "amount": "1000.00",
            "payment_reference": "PAY-API-001",
        },
    )
    assert submitted.status_code == 200
    payment = submitted.json()["payments"][0]

    _login(facility_client, "financier.demo")
    overdue = facility_client.post(
        f"/api/v1/facilities/{facility_id}/mark-overdue",
        json={
            **_command(submitted.json()["version"]),
            "installment_id": active["installments"][0]["installment_id"],
            "days_past_due": 1,
            "evidence_sha256": "a" * 64,
        },
    )
    assert overdue.status_code == 200
    assert overdue.json()["status"] == "overdue"

    _login(facility_client, "financier.demo")
    decided = facility_client.post(
        f"/api/v1/facilities/{facility_id}/payments/{payment['payment_id']}/decision",
        json={
            **_command(overdue.json()["version"]),
            "decision": "confirmed",
            "comment": "Exact payment verified",
        },
    )
    assert decided.status_code == 200
    assert decided.json()["status"] == "repaid"

    _login(facility_client, "auditor.demo")
    closed = facility_client.post(
        f"/api/v1/facilities/{facility_id}/close",
        json=_command(decided.json()["version"]),
    )
    assert closed.status_code == 200
    assert closed.json()["status"] == "closed"


def test_conflict_and_idempotent_replay_have_stable_http_contract(
    facility_client,
    session_factory,
):
    request_id = _application(session_factory)
    key = uuid.uuid4()
    payload = _create_payload(request_id, key=key)
    _login(facility_client, "financier.demo")
    first = facility_client.post("/api/v1/facilities", json=payload)
    replay = facility_client.post("/api/v1/facilities", json=payload)
    assert replay.status_code == 201
    assert replay.json()["facility_id"] == first.json()["facility_id"]

    conflicting = facility_client.post(
        "/api/v1/facilities",
        json={**payload, "currency": "USD"},
    )
    assert conflicting.status_code == 409
    assert conflicting.json()["detail"]["code"] == "facility_conflict"


def test_request_validation_and_unique_action_race_use_422_and_409(
    facility_client,
    session_factory,
    monkeypatch,
):
    request_id = _application(session_factory)
    _login(facility_client, "financier.demo")
    numeric_money = facility_client.post(
        "/api/v1/facilities",
        json={**_create_payload(request_id), "principal": 1000.0},
    )
    assert numeric_money.status_code == 422

    def duplicate_action(*_args, **_kwargs):
        raise IntegrityError(
            "INSERT INTO facility_actions",
            {},
            RuntimeError("duplicate idempotency key"),
        )

    monkeypatch.setattr(
        facility_client.app.state.facility_service,
        "create",
        duplicate_action,
    )
    monkeypatch.setattr(
        facility_client._transport,
        "raise_server_exceptions",
        False,
    )
    raced = facility_client.post(
        "/api/v1/facilities",
        json=_create_payload(request_id),
    )
    assert raced.status_code == 409
    assert raced.json() == {
        "detail": {
            "code": "facility_conflict",
            "message": "Facility command conflicts with persisted state",
        }
    }


def test_facility_openapi_documents_resource_actions_and_string_money(
    facility_client,
):
    schema = facility_client.get("/openapi.json").json()
    paths = schema["paths"]
    components = schema["components"]["schemas"]
    for path in (
        "/api/v1/facilities",
        "/api/v1/facilities/{facility_id}",
        "/api/v1/facilities/{facility_id}/initiate-disbursement",
        "/api/v1/facilities/{facility_id}/confirm-disbursement",
        "/api/v1/facilities/{facility_id}/payments",
        "/api/v1/facilities/{facility_id}/payments/{payment_id}/decision",
        "/api/v1/facilities/{facility_id}/mark-overdue",
        "/api/v1/facilities/{facility_id}/close",
    ):
        assert path in paths
    assert "201" in paths["/api/v1/facilities"]["post"]["responses"]
    assert components["CreateFacilityRequest"]["properties"]["principal"] == {
        "type": "string",
        "title": "Principal",
    }
    assert components["InstallmentRequest"]["properties"]["amount"] == {
        "type": "string",
        "title": "Amount",
    }
    assert components["SubmitPaymentRequest"]["properties"]["amount"] == {
        "type": "string",
        "title": "Amount",
    }

    facility_schema = components["FacilityResponse"]
    assert facility_schema["properties"]["principal"]["type"] == "string"
    assert (
        facility_schema["properties"]["outstanding_amount"]["type"]
        == "string"
    )
    installment_schema = components["FacilityInstallmentResponse"]
    assert installment_schema["properties"]["amount"]["type"] == "string"
    assert installment_schema["properties"]["paid_amount"]["type"] == "string"
    payment_schema = components["FacilityPaymentResponse"]
    assert payment_schema["properties"]["amount"]["type"] == "string"
