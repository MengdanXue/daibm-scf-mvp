from __future__ import annotations

import hashlib
import secrets
import uuid

from sqlalchemy import delete, text
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session, sessionmaker

from app.identity import (
    AccountLocked,
    AuthenticatedUser,
    AuthenticationRequired,
    InvalidCredentials,
    LoginResult,
    hash_password,
    verify_password,
)
from app.models_identity import OrganizationModel, UserModel, UserSessionModel
from app.repositories.identity import IdentityRepository
from app.services.security import (
    SecuritySettings,
    SettingsProvider,
    check_password_policy,
    demo_password_from_env,
    record_security_event,
)

DEMO_ORGANIZATIONS = (
    ("SUPPLIER-001", "ООО «Северный поставщик»", "supplier"),
    ("CORE-001", "АО «Якорная компания»", "core_enterprise"),
    ("BANK-001", "Банк «Развитие»", "financier"),
    ("AUDIT-001", "Аудиторская служба", "auditor"),
)
DEMO_USERS = (
    ("supplier.demo", "Поставщик · Анна", "supplier", "SUPPLIER-001"),
    ("core.demo", "Якорная компания · Михаил", "core_enterprise", "CORE-001"),
    ("financier.demo", "Кредитный аналитик · Елена", "financier", "BANK-001"),
    ("risk.demo", "Риск-менеджер · Павел", "risk_manager", "BANK-001"),
    ("auditor.demo", "Аудитор · Ирина", "auditor", "AUDIT-001"),
    ("admin.demo", "Администратор · Ольга", "admin", "BANK-001"),
)


def _stable_uuid(kind: str, value: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"daibm-scf:{kind}:{value}")


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class IdentityService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        repository: IdentityRepository | None = None,
        clock: Callable[[], datetime] | None = None,
        *,
        demo_password: str | None = None,
        settings_provider: SettingsProvider | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.repository = repository or IdentityRepository()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        # Never a literal: the demo password is configuration (DAIBM_DEMO_PASSWORD).
        self.demo_password = demo_password or demo_password_from_env()
        base = SecuritySettings.from_env()
        self.settings_provider: SettingsProvider = settings_provider or (lambda: base)

    def seed_demo_accounts(self) -> None:
        now = self.clock()
        with self.session_factory.begin() as session:
            organizations: dict[str, OrganizationModel] = {}
            for code, name, organization_type in DEMO_ORGANIZATIONS:
                organization = self.repository.get_organization_by_code(
                    session, code
                )
                if organization is None:
                    organization = OrganizationModel(
                        organization_id=_stable_uuid("organization", code),
                        organization_code=code,
                        name=name,
                        organization_type=organization_type,
                        created_at=now,
                    )
                    session.add(organization)
                organizations[code] = organization
            session.flush()

            supported = self._supported_roles(session)
            if self.demo_password is None:
                # Without DAIBM_DEMO_PASSWORD no demo sign-in exists at all.
                supported = set()
            for username, display_name, role, organization_code in DEMO_USERS:
                if role not in supported:
                    # A database still at an older revision (maintenance
                    # startup, migration tests) has no such role yet.
                    continue
                if self.repository.get_user_by_username(session, username):
                    continue
                assert self.demo_password is not None
                password_hash, password_salt = hash_password(self.demo_password)
                session.add(
                    UserModel(
                        user_id=_stable_uuid("user", username),
                        username=username,
                        display_name=display_name,
                        password_hash=password_hash,
                        password_salt=password_salt,
                        role=role,
                        organization_id=organizations[
                            organization_code
                        ].organization_id,
                        is_active=True,
                        created_at=now,
                    )
                )
            session.flush()
            self._seed_audit_grants(session, organizations, now)

    @staticmethod
    def _seed_audit_grants(session, organizations, now) -> None:
        """Demo auditors review every demo lending organization (schema permitting)."""

        if session.scalar(text("SELECT to_regclass('audit_grants')")) is None:
            return
        for username, _, role, _ in DEMO_USERS:
            if role != "auditor":
                continue
            for code, _, organization_type in DEMO_ORGANIZATIONS:
                if organization_type != "financier":
                    continue
                session.execute(
                    text(
                        "INSERT INTO audit_grants (grant_id, auditor_user_id, organization_id, "
                        "reason, granted_at) SELECT :grant, u.user_id, :org, 'demo_seed', :now "
                        "FROM users u WHERE u.username = :username AND NOT EXISTS ("
                        "SELECT 1 FROM audit_grants g WHERE g.auditor_user_id = u.user_id "
                        "AND g.organization_id = :org AND g.revoked_at IS NULL)"
                    ),
                    {
                        "grant": uuid.uuid4(),
                        "org": organizations[code].organization_id,
                        "now": now,
                        "username": username,
                    },
                )

    @staticmethod
    def _supported_roles(session) -> set[str]:
        definition = session.scalar(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'ck_users_role'"
            )
        )
        roles = {role for _, _, role, _ in DEMO_USERS}
        if definition is None:
            return roles
        return {role for role in roles if f"'{role}'" in definition}

    def login(
        self, username: str, password: str, *, client_ip: str | None = None
    ) -> LoginResult:
        """Sign in; failures count towards a lock-out and every attempt is audited."""

        settings = self.settings_provider()
        normalized_username = username.strip().lower()
        failure: InvalidCredentials | None = None
        result: LoginResult | None = None
        with self.session_factory.begin() as session:
            now = self.clock()
            identity = self.repository.get_user_by_username(session, normalized_username)
            if identity is None:
                hash_password(password, bytes(16))
                record_security_event(
                    session, "LOGIN_FAILURE", "login", username=normalized_username,
                    detail={"reason": "unknown_user"}, client_ip=client_ip,
                )
                failure = InvalidCredentials("Invalid username or password")
            else:
                user, organization = identity
                locked = user.locked_until is not None and user.locked_until > now
                if locked:
                    record_security_event(
                        session, "LOGIN_LOCKED", "login", user_id=user.user_id,
                        username=user.username, organization_id=user.organization_id,
                        detail={"locked_until": user.locked_until.isoformat() if user.locked_until else None},
                        client_ip=client_ip,
                    )
                    failure = AccountLocked("Account is temporarily locked")
                elif (
                    not user.is_active
                    or getattr(organization, "status", "active") != "active"
                    or not verify_password(password, user.password_salt, user.password_hash)
                ):
                    user.failed_login_count += 1
                    reason = (
                        "inactive_user" if not user.is_active
                        else "organization_suspended" if getattr(organization, "status", "active") != "active"
                        else "wrong_password"
                    )
                    event = "LOGIN_FAILURE"
                    if user.failed_login_count >= settings.login_max_failures:
                        user.locked_until = now + timedelta(minutes=settings.lockout_minutes)
                        user.failed_login_count = 0
                        event = "LOGIN_LOCKED"
                    record_security_event(
                        session, event, "login", user_id=user.user_id, username=user.username,
                        organization_id=user.organization_id,
                        detail={"reason": reason, "failures": user.failed_login_count},
                        client_ip=client_ip,
                    )
                    failure = (
                        AccountLocked("Account is temporarily locked")
                        if event == "LOGIN_LOCKED"
                        else InvalidCredentials("Invalid username or password")
                    )
                else:
                    user.failed_login_count = 0
                    user.locked_until = None
                    expires_at = now + timedelta(hours=settings.session_absolute_hours)
                    token = secrets.token_urlsafe(32)
                    session.add(
                        UserSessionModel(
                            session_id=uuid.uuid4(),
                            user_id=user.user_id,
                            token_hash=_token_digest(token),
                            created_at=now,
                            expires_at=expires_at,
                            last_seen_at=now,
                        )
                    )
                    record_security_event(
                        session, "LOGIN_SUCCESS", "login", user_id=user.user_id,
                        username=user.username, organization_id=user.organization_id,
                        client_ip=client_ip,
                    )
                    result = LoginResult(
                        user=self._to_identity(user, organization),
                        token=token,
                        expires_at=expires_at,
                    )
        # Failure bookkeeping is committed before the caller sees the error.
        if failure is not None:
            raise failure
        assert result is not None
        return result

    def authenticate(self, token: str | None) -> AuthenticatedUser:
        if not token:
            raise AuthenticationRequired("Authentication required")
        settings = self.settings_provider()
        token_hash = _token_digest(token)
        authenticated_user: AuthenticatedUser | None = None
        with self.session_factory.begin() as session:
            identity = self.repository.get_identity_by_token_hash(session, token_hash)
            if identity is not None:
                active_session, user, organization = identity
                now = self.clock()
                idle_limit = active_session.last_seen_at + timedelta(minutes=settings.session_idle_minutes)
                expired = active_session.expires_at <= now or idle_limit <= now
                if expired or not user.is_active or getattr(organization, "status", "active") != "active":
                    self.repository.delete_session(session, token_hash)
                    if expired:
                        record_security_event(
                            session, "SESSION_EXPIRED", "authenticate", user_id=user.user_id,
                            username=user.username, organization_id=user.organization_id,
                            detail={"reason": "absolute" if active_session.expires_at <= now else "idle"},
                        )
                else:
                    # Sliding idle window, written at most once a minute.
                    if now - active_session.last_seen_at > timedelta(seconds=60):
                        active_session.last_seen_at = now
                    authenticated_user = self._to_identity(user, organization)
        if authenticated_user is None:
            raise AuthenticationRequired("Authentication required")
        return authenticated_user

    def logout(self, token: str | None) -> None:
        if not token:
            return
        with self.session_factory.begin() as session:
            identity = self.repository.get_identity_by_token_hash(session, _token_digest(token))
            if identity is not None:
                _, user, _ = identity
                record_security_event(
                    session, "LOGOUT", "logout", user_id=user.user_id, username=user.username,
                    organization_id=user.organization_id,
                )
            self.repository.delete_session(session, _token_digest(token))

    def change_password(self, user: AuthenticatedUser, current: str, new: str) -> None:
        check_password_policy(new, username=user.username)
        with self.session_factory.begin() as session:
            row = session.get(UserModel, user.user_id)
            if row is None or not verify_password(current, row.password_salt, row.password_hash):
                raise InvalidCredentials("Current password is wrong")
            row.password_hash, row.password_salt = hash_password(new)
            # Every other session of this user ends with the old password.
            session.execute(
                delete(UserSessionModel).where(UserSessionModel.user_id == user.user_id)
            )
            record_security_event(
                session, "ADMIN_ACTION", "password_changed", user_id=user.user_id,
                username=user.username, organization_id=user.organization_id,
                resource_type="user", resource_id=str(user.user_id),
            )

    def core_enterprises(self) -> list[dict[str, str]]:
        with self.session_factory() as session:
            organizations = self.repository.list_organizations_by_type(
                session,
                "core_enterprise",
            )
            return [
                {
                    "organization_code": organization.organization_code,
                    "name": organization.name,
                }
                for organization in organizations
            ]

    @staticmethod
    def demo_accounts() -> list[dict[str, str]]:
        return [
            {
                "username": username,
                "display_name": display_name,
                "role": role,
                "organization_code": organization_code,
            }
            for username, display_name, role, organization_code in DEMO_USERS
        ]

    @staticmethod
    def _to_identity(
        user: UserModel,
        organization: OrganizationModel,
    ) -> AuthenticatedUser:
        return AuthenticatedUser(
            user_id=user.user_id,
            username=user.username,
            display_name=user.display_name,
            role=user.role,
            organization_id=organization.organization_id,
            organization_code=organization.organization_code,
            organization_name=organization.name,
        )
