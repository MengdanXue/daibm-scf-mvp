from __future__ import annotations

import hashlib
import secrets
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session, sessionmaker

from app.identity import (
    AuthenticatedUser,
    AuthenticationRequired,
    InvalidCredentials,
    LoginResult,
    hash_password,
    verify_password,
)
from app.models_identity import OrganizationModel, UserModel, UserSessionModel
from app.repositories.identity import IdentityRepository


DEMO_PASSWORD = "Demo123!"
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
    ) -> None:
        self.session_factory = session_factory
        self.repository = repository or IdentityRepository()
        self.clock = clock or (lambda: datetime.now(timezone.utc))

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

            for username, display_name, role, organization_code in DEMO_USERS:
                if self.repository.get_user_by_username(session, username):
                    continue
                password_hash, password_salt = hash_password(DEMO_PASSWORD)
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

    def login(self, username: str, password: str) -> LoginResult:
        normalized_username = username.strip().lower()
        with self.session_factory.begin() as session:
            identity = self.repository.get_user_by_username(
                session, normalized_username
            )
            if identity is None:
                hash_password(password, bytes(16))
                raise InvalidCredentials("Invalid username or password")
            user, organization = identity
            if not user.is_active or not verify_password(
                password, user.password_salt, user.password_hash
            ):
                raise InvalidCredentials("Invalid username or password")

            now = self.clock()
            expires_at = now + timedelta(hours=12)
            token = secrets.token_urlsafe(32)
            session.add(
                UserSessionModel(
                    session_id=uuid.uuid4(),
                    user_id=user.user_id,
                    token_hash=_token_digest(token),
                    created_at=now,
                    expires_at=expires_at,
                )
            )
            return LoginResult(
                user=self._to_identity(user, organization),
                token=token,
                expires_at=expires_at,
            )

    def authenticate(self, token: str | None) -> AuthenticatedUser:
        if not token:
            raise AuthenticationRequired("Authentication required")
        token_hash = _token_digest(token)
        authenticated_user: AuthenticatedUser | None = None
        with self.session_factory.begin() as session:
            identity = self.repository.get_identity_by_token_hash(
                session, token_hash
            )
            if identity is not None:
                active_session, user, organization = identity
                if active_session.expires_at <= self.clock() or not user.is_active:
                    self.repository.delete_session(session, token_hash)
                else:
                    authenticated_user = self._to_identity(user, organization)
        if authenticated_user is None:
            raise AuthenticationRequired("Authentication required")
        return authenticated_user

    def logout(self, token: str | None) -> None:
        if not token:
            return
        with self.session_factory.begin() as session:
            self.repository.delete_session(session, _token_digest(token))

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
