import pytest
from fastapi.testclient import TestClient

from app.database import Database
from app.main import create_app


@pytest.fixture
def auth_client(migrated_engine, session_factory):
    database = Database(
        engine=migrated_engine,
        session_factory=session_factory,
    )
    with TestClient(create_app(database)) as client:
        yield client


def login(client: TestClient, username: str) -> dict:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "Demo123!"},
    )
    assert response.status_code == 200
    return response.json()


def test_demo_account_metadata_is_public_without_password_material(auth_client):
    response = auth_client.get("/api/v1/auth/demo-accounts")

    assert response.status_code == 200
    assert len(response.json()) == 5
    assert {account["role"] for account in response.json()} == {
        "supplier",
        "core_enterprise",
        "financier",
        "risk_manager",
        "auditor",
    }
    assert "password" not in response.text.lower()
    assert "hash" not in response.text.lower()


def test_login_sets_http_only_strict_cookie_and_me_restores_identity(auth_client):
    payload = login(auth_client, "supplier.demo")

    cookie = auth_client.cookies.get("daibm_session")
    assert cookie
    set_cookie = auth_client.request(
        "POST",
        "/api/v1/auth/login",
        json={"username": "supplier.demo", "password": "Demo123!"},
    ).headers["set-cookie"]
    assert "HttpOnly" in set_cookie
    assert "SameSite=strict" in set_cookie
    assert payload["user"]["role"] == "supplier"
    assert payload["user"]["organization_code"] == "SUPPLIER-001"

    me = auth_client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["username"] == "supplier.demo"


def test_public_session_probe_avoids_expected_unauthorized_browser_noise(
    auth_client,
):
    anonymous = auth_client.get("/api/v1/auth/session")
    assert anonymous.status_code == 200
    assert anonymous.json() == {"authenticated": False, "user": None}

    login(auth_client, "core.demo")
    authenticated = auth_client.get("/api/v1/auth/session")
    assert authenticated.status_code == 200
    assert authenticated.json()["authenticated"] is True
    assert authenticated.json()["user"]["username"] == "core.demo"


def test_bad_login_has_generic_error_and_does_not_set_cookie(auth_client):
    response = auth_client.post(
        "/api/v1/auth/login",
        json={"username": "missing.demo", "password": "wrong"},
    )

    assert response.status_code == 401
    assert response.json() == {
        "detail": {
            "code": "invalid_credentials",
            "message": "Invalid username or password",
        }
    }
    assert auth_client.cookies.get("daibm_session") is None


def test_logout_clears_cookie_and_invalidates_session(auth_client):
    login(auth_client, "auditor.demo")

    response = auth_client.post("/api/v1/auth/logout")

    assert response.status_code == 204
    assert auth_client.cookies.get("daibm_session") is None
    assert auth_client.get("/api/v1/auth/me").status_code == 401


def test_legacy_business_api_requires_authentication_and_role(auth_client):
    unauthenticated = auth_client.get("/api/requests")
    assert unauthenticated.status_code == 401
    assert unauthenticated.json()["detail"]["code"] == "authentication_required"

    login(auth_client, "supplier.demo")
    assert auth_client.get("/api/requests").status_code == 403

    auth_client.post("/api/v1/auth/logout")
    login(auth_client, "financier.demo")
    assert auth_client.get("/api/requests").status_code == 200


def test_research_and_ledger_use_distinct_role_permissions(auth_client):
    login(auth_client, "supplier.demo")
    assert auth_client.get("/api/research/status").status_code == 403
    assert auth_client.get("/api/ledger/verify").status_code == 403

    auth_client.post("/api/v1/auth/logout")
    login(auth_client, "financier.demo")
    assert auth_client.get("/api/research/status").status_code == 200
    assert auth_client.get("/api/ledger/verify").status_code == 403

    auth_client.post("/api/v1/auth/logout")
    login(auth_client, "auditor.demo")
    assert auth_client.get("/api/research/status").status_code == 200
    assert auth_client.get("/api/ledger/verify").status_code == 200
    assert auth_client.post("/api/demo/reset").status_code == 200
