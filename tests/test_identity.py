import hashlib
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.identity import (
    AuthenticationRequired,
    InvalidCredentials,
    hash_password,
    verify_password,
)
from app.models_identity import OrganizationModel, UserModel, UserSessionModel
from app.services.identity import IdentityService


class MutableClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now


def seeded_service(session_factory):
    clock = MutableClock()
    service = IdentityService(session_factory, clock=clock)
    service.seed_demo_accounts()
    return service, clock


def test_password_hash_uses_unique_salts_and_rejects_wrong_password():
    first_hash, first_salt = hash_password("Demo123!")
    second_hash, second_salt = hash_password("Demo123!")

    assert first_hash != "Demo123!"
    assert first_salt != second_salt
    assert first_hash != second_hash
    assert verify_password("Demo123!", first_salt, first_hash) is True
    assert verify_password("wrong-password", first_salt, first_hash) is False


def test_demo_account_seed_is_idempotent_and_assigns_every_role(session_factory):
    service, _ = seeded_service(session_factory)

    service.seed_demo_accounts()

    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(UserModel)) == 6
        assert (
            session.scalar(select(func.count()).select_from(OrganizationModel))
            == 4
        )
        roles = set(session.scalars(select(UserModel.role)))
    assert roles == {
        "supplier",
        "core_enterprise",
        "financier",
        "risk_manager",
        "auditor",
        "admin",
    }


def test_login_stores_only_token_digest_and_expires_in_twelve_hours(
    session_factory,
):
    service, clock = seeded_service(session_factory)

    result = service.login("supplier.demo", "Demo123!")

    assert result.user.role == "supplier"
    assert result.expires_at == clock.now + timedelta(hours=12)
    assert len(result.token) >= 40
    with session_factory() as session:
        stored = session.scalar(select(UserSessionModel))
        assert stored is not None
        assert stored.token_hash == hashlib.sha256(
            result.token.encode("utf-8")
        ).hexdigest()
        assert result.token not in stored.token_hash


@pytest.mark.parametrize(
    ("username", "password"),
    (("missing.demo", "Demo123!"), ("supplier.demo", "wrong-password")),
)
def test_login_uses_same_error_for_unknown_user_and_wrong_password(
    session_factory,
    username,
    password,
):
    service, _ = seeded_service(session_factory)

    with pytest.raises(InvalidCredentials, match="Invalid username or password"):
        service.login(username, password)


def test_logout_invalidates_the_server_side_session(session_factory):
    service, _ = seeded_service(session_factory)
    login = service.login("auditor.demo", "Demo123!")
    assert service.authenticate(login.token).role == "auditor"

    service.logout(login.token)

    with pytest.raises(AuthenticationRequired):
        service.authenticate(login.token)
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(UserSessionModel)) == 0


def test_expired_session_is_rejected_and_removed(session_factory):
    service, clock = seeded_service(session_factory)
    login = service.login("core.demo", "Demo123!")
    clock.now += timedelta(hours=13)

    with pytest.raises(AuthenticationRequired):
        service.authenticate(login.token)

    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(UserSessionModel)) == 0
