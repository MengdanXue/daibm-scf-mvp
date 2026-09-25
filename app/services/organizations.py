"""Tenant administration: organizations, their users and auditor grants."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.identity import AuthenticatedUser, hash_password
from app.ledger import canonical_timestamp
from app.models_enterprise import AuditGrantModel, SecurityEventModel
from app.models_identity import OrganizationModel, UserModel, UserSessionModel
from app.repositories.ledger import LedgerRepository
from app.services.permissions import PermissionDenied, PermissionService
from app.services.security import check_password_policy, record_security_event

ORGANIZATION_TYPES = ("supplier", "core_enterprise", "financier", "auditor")
# Which roles an organization of each type may hold.
ROLES_BY_TYPE = {
    "supplier": frozenset({"supplier"}),
    "core_enterprise": frozenset({"core_enterprise"}),
    "financier": frozenset({"financier", "risk_manager", "admin"}),
    "auditor": frozenset({"auditor"}),
}


class OrganizationError(Exception):
    pass


class OrganizationNotFound(OrganizationError):
    pass


class OrganizationConflict(OrganizationError):
    pass


def record_admin_action(
    session: Session,
    ledger: LedgerRepository,
    user: AuthenticatedUser,
    action: str,
    *,
    resource_type: str,
    resource_id: str,
    detail: dict[str, Any],
) -> None:
    """Every administrative change: a security event and a hash-chained ledger entry."""

    record_security_event(
        session,
        "ADMIN_ACTION",
        action,
        user_id=user.user_id,
        username=user.username,
        organization_id=user.organization_id,
        resource_type=resource_type,
        resource_id=resource_id,
        detail=detail,
    )
    session.flush()
    ledger.append_many(
        session,
        uuid.uuid5(uuid.NAMESPACE_URL, f"daibm-scf:{resource_type}:{resource_id}"),
        [
            (
                "ADMIN_ACTION_RECORDED",
                {
                    "action": action,
                    "resource_type": resource_type,
                    "resource_id": resource_id,
                    "actor_user_id": str(user.user_id),
                    "actor_role": user.role,
                    **detail,
                },
            )
        ],
    )


class OrganizationService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        ledger_repository: LedgerRepository | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.ledger = ledger_repository or LedgerRepository()
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    # --- Queries -------------------------------------------------------------

    def list_organizations(self, user: AuthenticatedUser) -> list[dict[str, Any]]:
        PermissionService.require(user, "organization:manage")
        with self.session_factory() as session:
            counts: dict[uuid.UUID, int] = {
                organization_id: count
                for organization_id, count in session.execute(
                    select(UserModel.organization_id, func.count()).group_by(UserModel.organization_id)
                )
            }
            return [
                self._organization(item, counts.get(item.organization_id, 0))
                for item in session.scalars(
                    select(OrganizationModel).order_by(OrganizationModel.organization_code)
                )
            ]

    def list_users(self, user: AuthenticatedUser) -> list[dict[str, Any]]:
        PermissionService.require(user, "organization:manage")
        with self.session_factory() as session:
            rows = session.execute(
                select(UserModel, OrganizationModel)
                .join(OrganizationModel, OrganizationModel.organization_id == UserModel.organization_id)
                .order_by(OrganizationModel.organization_code, UserModel.username)
            ).all()
            return [self._user(item, organization) for item, organization in rows]

    def list_grants(self, user: AuthenticatedUser) -> list[dict[str, Any]]:
        PermissionService.require(user, "organization:manage")
        with self.session_factory() as session:
            rows = session.execute(
                select(AuditGrantModel, UserModel.username, OrganizationModel.organization_code)
                .join(UserModel, UserModel.user_id == AuditGrantModel.auditor_user_id)
                .join(OrganizationModel, OrganizationModel.organization_id == AuditGrantModel.organization_id)
                .order_by(AuditGrantModel.granted_at.desc())
            ).all()
            return [
                {
                    "grant_id": str(grant.grant_id),
                    "auditor": username,
                    "organization_code": code,
                    "organization_id": str(grant.organization_id),
                    "reason": grant.reason,
                    "granted_at": canonical_timestamp(grant.granted_at),
                    "revoked_at": canonical_timestamp(grant.revoked_at) if grant.revoked_at else None,
                    "active": grant.revoked_at is None,
                }
                for grant, username, code in rows
            ]

    def security_events(
        self, user: AuthenticatedUser, *, event_type: str | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        """Admins see every event; auditors the events of their granted organizations."""

        PermissionService.require(user, "security:read")
        with self.session_factory() as session:
            scope = PermissionService.scope(session, user)
            statement = select(SecurityEventModel).order_by(SecurityEventModel.event_id.desc()).limit(limit)
            if event_type:
                statement = statement.where(SecurityEventModel.event_type == event_type)
            if not scope.everything:
                statement = statement.where(
                    PermissionService.organization_condition(SecurityEventModel.organization_id, scope)
                )
            return [
                {
                    "event_id": item.event_id,
                    "event_type": item.event_type,
                    "action": item.action,
                    "username": item.username,
                    "organization_id": str(item.organization_id) if item.organization_id else None,
                    "resource_type": item.resource_type,
                    "resource_id": item.resource_id,
                    "detail": item.detail,
                    "client_ip": item.client_ip,
                    "recorded_at": canonical_timestamp(item.recorded_at),
                }
                for item in session.scalars(statement)
            ]

    # --- Commands ----------------------------------------------------------

    def create_organization(
        self, user: AuthenticatedUser, *, code: str, name: str, organization_type: str
    ) -> dict[str, Any]:
        PermissionService.require(user, "organization:manage")
        if organization_type not in ORGANIZATION_TYPES:
            raise OrganizationConflict("unknown organization type")
        with self.session_factory.begin() as session:
            if session.scalar(
                select(OrganizationModel).where(OrganizationModel.organization_code == code)
            ):
                raise OrganizationConflict("organization code already exists")
            organization = OrganizationModel(
                organization_id=uuid.uuid4(),
                organization_code=code,
                name=name,
                organization_type=organization_type,
                created_at=self.clock(),
                status="active",
            )
            session.add(organization)
            session.flush()
            record_admin_action(
                session, self.ledger, user, "organization_created",
                resource_type="organization", resource_id=str(organization.organization_id),
                detail={"code": code, "organization_type": organization_type},
            )
            return self._organization(organization, 0)

    def set_organization_status(
        self, user: AuthenticatedUser, organization_id: str, *, status: str, reason: str
    ) -> dict[str, Any]:
        PermissionService.require(user, "organization:manage")
        if status not in {"active", "suspended"}:
            raise OrganizationConflict("status must be active or suspended")
        with self.session_factory.begin() as session:
            organization = session.get(OrganizationModel, _uuid(organization_id))
            if organization is None:
                raise OrganizationNotFound(organization_id)
            if organization.organization_id == user.organization_id and status == "suspended":
                raise OrganizationConflict("an administrator cannot suspend their own organization")
            previous = organization.status
            organization.status = status
            if status == "suspended":
                # Existing sessions end immediately.
                session.execute(
                    delete(UserSessionModel).where(
                        UserSessionModel.user_id.in_(
                            select(UserModel.user_id).where(
                                UserModel.organization_id == organization.organization_id
                            )
                        )
                    )
                )
            record_admin_action(
                session, self.ledger, user, "organization_status_changed",
                resource_type="organization", resource_id=str(organization.organization_id),
                detail={"from": previous, "to": status, "reason": reason},
            )
            count = session.scalar(
                select(func.count()).select_from(UserModel).where(
                    UserModel.organization_id == organization.organization_id
                )
            ) or 0
            return self._organization(organization, count)

    def create_user(
        self,
        user: AuthenticatedUser,
        *,
        username: str,
        display_name: str,
        role: str,
        organization_id: str,
        password: str,
    ) -> dict[str, Any]:
        PermissionService.require(user, "organization:manage")
        normalized = username.strip().lower()
        check_password_policy(password, username=normalized)
        with self.session_factory.begin() as session:
            organization = session.get(OrganizationModel, _uuid(organization_id))
            if organization is None:
                raise OrganizationNotFound(organization_id)
            if role not in ROLES_BY_TYPE.get(organization.organization_type, frozenset()):
                raise OrganizationConflict(
                    f"role {role} does not belong to a {organization.organization_type} organization"
                )
            if session.scalar(select(UserModel).where(UserModel.username == normalized)):
                raise OrganizationConflict("username already exists")
            password_hash, password_salt = hash_password(password)
            created = UserModel(
                user_id=uuid.uuid4(),
                username=normalized,
                display_name=display_name,
                password_hash=password_hash,
                password_salt=password_salt,
                role=role,
                organization_id=organization.organization_id,
                is_active=True,
                created_at=self.clock(),
            )
            session.add(created)
            session.flush()
            record_admin_action(
                session, self.ledger, user, "user_created",
                resource_type="user", resource_id=str(created.user_id),
                detail={"username": normalized, "role": role, "organization_code": organization.organization_code},
            )
            return self._user(created, organization)

    def set_user_active(
        self, user: AuthenticatedUser, user_id: str, *, active: bool, reason: str
    ) -> dict[str, Any]:
        PermissionService.require(user, "organization:manage")
        with self.session_factory.begin() as session:
            target = session.get(UserModel, _uuid(user_id))
            if target is None:
                raise OrganizationNotFound(user_id)
            if target.user_id == user.user_id and not active:
                raise OrganizationConflict("an administrator cannot deactivate themselves")
            target.is_active = active
            if not active:
                session.execute(
                    delete(UserSessionModel).where(UserSessionModel.user_id == target.user_id)
                )
            else:
                target.failed_login_count = 0
                target.locked_until = None
            record_admin_action(
                session, self.ledger, user, "user_activated" if active else "user_deactivated",
                resource_type="user", resource_id=str(target.user_id),
                detail={"username": target.username, "reason": reason},
            )
            organization = session.get(OrganizationModel, target.organization_id)
            assert organization is not None
            return self._user(target, organization)

    def grant_audit(
        self, user: AuthenticatedUser, *, auditor_user_id: str, organization_id: str, reason: str
    ) -> dict[str, Any]:
        PermissionService.require(user, "organization:manage")
        with self.session_factory.begin() as session:
            auditor = session.get(UserModel, _uuid(auditor_user_id))
            organization = session.get(OrganizationModel, _uuid(organization_id))
            if auditor is None or organization is None:
                raise OrganizationNotFound(auditor_user_id)
            if auditor.role != "auditor":
                raise OrganizationConflict("audit grants are for auditors")
            if session.scalar(
                select(AuditGrantModel).where(
                    AuditGrantModel.auditor_user_id == auditor.user_id,
                    AuditGrantModel.organization_id == organization.organization_id,
                    AuditGrantModel.revoked_at.is_(None),
                )
            ):
                raise OrganizationConflict("grant already active")
            grant = AuditGrantModel(
                grant_id=uuid.uuid4(),
                auditor_user_id=auditor.user_id,
                organization_id=organization.organization_id,
                granted_by_user_id=user.user_id,
                reason=reason,
                granted_at=self.clock(),
            )
            session.add(grant)
            session.flush()
            record_admin_action(
                session, self.ledger, user, "audit_granted",
                resource_type="audit_grant", resource_id=str(grant.grant_id),
                detail={"auditor": auditor.username, "organization_code": organization.organization_code,
                        "reason": reason},
            )
            return {"grant_id": str(grant.grant_id), "active": True}

    def revoke_audit(self, user: AuthenticatedUser, grant_id: str, *, reason: str) -> dict[str, Any]:
        PermissionService.require(user, "organization:manage")
        with self.session_factory.begin() as session:
            grant = session.get(AuditGrantModel, _uuid(grant_id))
            if grant is None:
                raise OrganizationNotFound(grant_id)
            if grant.revoked_at is not None:
                raise OrganizationConflict("grant already revoked")
            grant.revoked_at = self.clock()
            grant.revoked_by_user_id = user.user_id
            record_admin_action(
                session, self.ledger, user, "audit_revoked",
                resource_type="audit_grant", resource_id=str(grant.grant_id),
                detail={"reason": reason},
            )
            return {"grant_id": str(grant.grant_id), "active": False}

    # --- helpers -------------------------------------------------------------

    @staticmethod
    def _organization(item: OrganizationModel, users: int) -> dict[str, Any]:
        return {
            "organization_id": str(item.organization_id),
            "code": item.organization_code,
            "name": item.name,
            "type": item.organization_type,
            "status": item.status,
            "user_count": users,
            "created_at": canonical_timestamp(item.created_at),
        }

    @staticmethod
    def _user(item: UserModel, organization: OrganizationModel) -> dict[str, Any]:
        return {
            "user_id": str(item.user_id),
            "username": item.username,
            "display_name": item.display_name,
            "role": item.role,
            "organization_id": str(organization.organization_id),
            "organization_code": organization.organization_code,
            "is_active": item.is_active,
            "locked_until": canonical_timestamp(item.locked_until) if item.locked_until else None,
        }


def _uuid(value: str | uuid.UUID) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except ValueError as error:
        raise OrganizationNotFound(str(value)) from error


__all__ = [
    "OrganizationConflict",
    "OrganizationNotFound",
    "OrganizationService",
    "PermissionDenied",
    "record_admin_action",
]
