"""The single permission layer: role -> action, and organization -> data.

Every tenant-owned resource (facility, alert, task, outcome, dataset snapshot
items) is visible through :meth:`PermissionService.scope`:

* ``admin`` sees every organization;
* ``auditor`` sees the organizations of their active ``audit_grants``;
* ``financier`` and ``risk_manager`` see their own organization;
* ``supplier`` and ``core_enterprise`` see facilities where their organization
  is a party of the underlying application.

Resources outside the scope are reported as *not found*, so changing an id in a
request never reveals or reaches another organization's data. Role checks go
through :meth:`require`, which raises :class:`PermissionDenied` (HTTP 403); the
denial is recorded as a security event by the API layer.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import ColumnElement, false, or_, select, true
from sqlalchemy.orm import Session

from app.identity import AuthenticatedUser
from app.models import FinancingRequestModel
from app.models_enterprise import AuditGrantModel
from app.models_facility import FinancingFacilityModel

ALL_ROLES = frozenset({"supplier", "core_enterprise", "financier", "risk_manager", "auditor", "admin"})
BANK_ROLES = frozenset({"financier", "risk_manager"})
ENTERPRISE_ROLES = frozenset({"supplier", "core_enterprise"})

# Role -> action matrix. Ownership is checked separately through the scope.
PERMISSIONS: dict[str, frozenset[str]] = {
    "facility:read": ALL_ROLES,
    "dashboard:read": frozenset({"admin", "risk_manager", "auditor", "financier"}),
    "alert:read": frozenset({"admin", "risk_manager", "auditor"}),
    "alert:assign": frozenset({"admin", "risk_manager"}),
    "alert:review": frozenset({"admin", "auditor"}),
    "alert:scan": frozenset({"admin", "risk_manager"}),
    "task:use": frozenset({"admin", "risk_manager", "auditor"}),
    "rule:read": frozenset({"admin", "risk_manager", "auditor"}),
    "rule:write": frozenset({"admin"}),
    "model:read": frozenset({"admin", "auditor", "risk_manager", "financier"}),
    # Promotion re-verifies artifacts under the auditor's four-eyes duty.
    "model:change": frozenset({"auditor"}),
    "snapshot:read": frozenset({"admin", "auditor", "risk_manager", "financier"}),
    "outcome:read": frozenset({"admin", "auditor", "risk_manager"}),
    "config:read": frozenset({"admin", "auditor"}),
    "config:write": frozenset({"admin"}),
    "organization:manage": frozenset({"admin"}),
    "security:read": frozenset({"admin", "auditor"}),
    "ops:read": frozenset({"admin"}),
}


class PermissionDenied(Exception):
    def __init__(self, action: str, message: str | None = None) -> None:
        super().__init__(message or f"Current role cannot perform {action}")
        self.action = action


class ResourceNotVisible(Exception):
    """The resource does not exist or belongs to an organization out of scope."""


@dataclass(frozen=True)
class AccessScope:
    everything: bool
    organizations: frozenset[uuid.UUID]
    enterprise_organization: uuid.UUID | None = None

    def allows(self, organization_id: uuid.UUID | None) -> bool:
        return self.everything or (organization_id is not None and organization_id in self.organizations)


class PermissionService:
    """Stateless; every method takes the caller's session."""

    @staticmethod
    def require(user: AuthenticatedUser, action: str) -> None:
        if user.role not in PERMISSIONS.get(action, frozenset()):
            raise PermissionDenied(action)

    @staticmethod
    def allowed(user: AuthenticatedUser, action: str) -> bool:
        return user.role in PERMISSIONS.get(action, frozenset())

    @staticmethod
    def scope(session: Session, user: AuthenticatedUser) -> AccessScope:
        if user.role == "admin":
            return AccessScope(everything=True, organizations=frozenset())
        if user.role == "auditor":
            granted = frozenset(
                session.scalars(
                    select(AuditGrantModel.organization_id).where(
                        AuditGrantModel.auditor_user_id == user.user_id,
                        AuditGrantModel.revoked_at.is_(None),
                    )
                )
            )
            return AccessScope(everything=False, organizations=granted)
        if user.role in BANK_ROLES:
            return AccessScope(everything=False, organizations=frozenset({user.organization_id}))
        if user.role in ENTERPRISE_ROLES:
            return AccessScope(
                everything=False,
                organizations=frozenset(),
                enterprise_organization=user.organization_id,
            )
        return AccessScope(everything=False, organizations=frozenset())

    @staticmethod
    def organization_condition(column: Any, scope: AccessScope) -> ColumnElement[bool]:
        """Filter any ``organization_id`` column (alerts, tasks, facilities)."""

        if scope.everything:
            return true()
        if not scope.organizations:
            return false()
        return column.in_(sorted(scope.organizations))

    @classmethod
    def facility_condition(cls, scope: AccessScope, user_role: str) -> ColumnElement[bool]:
        """Facilities in scope; statements must join FinancingRequestModel for enterprises."""

        if scope.enterprise_organization is not None:
            if user_role == "supplier":
                return FinancingRequestModel.supplier_organization_id == scope.enterprise_organization
            return FinancingRequestModel.core_enterprise_organization_id == scope.enterprise_organization
        return cls.organization_condition(FinancingFacilityModel.organization_id, scope)

    @classmethod
    def can_view_facility(
        cls,
        session: Session,
        user: AuthenticatedUser,
        facility: FinancingFacilityModel,
        application: FinancingRequestModel | None = None,
    ) -> bool:
        scope = cls.scope(session, user)
        if scope.enterprise_organization is not None:
            application = application or session.get(FinancingRequestModel, facility.request_id)
            if application is None:
                return False
            if user.role == "supplier":
                return application.supplier_organization_id == scope.enterprise_organization
            return application.core_enterprise_organization_id == scope.enterprise_organization
        return scope.allows(facility.organization_id)

    @classmethod
    def require_facility(
        cls, session: Session, user: AuthenticatedUser, facility_id: uuid.UUID
    ) -> FinancingFacilityModel:
        facility = session.get(FinancingFacilityModel, facility_id)
        if facility is None or not cls.can_view_facility(session, user, facility):
            raise ResourceNotVisible(str(facility_id))
        return facility

    @staticmethod
    def organization_filter(user: AuthenticatedUser, column: Any) -> ColumnElement[bool]:
        """The same scope as :meth:`scope` as pure SQL (no extra round trip):
        auditor grants become a correlated subquery."""

        if user.role == "admin":
            return true()
        if user.role == "auditor":
            return column.in_(
                select(AuditGrantModel.organization_id).where(
                    AuditGrantModel.auditor_user_id == user.user_id,
                    AuditGrantModel.revoked_at.is_(None),
                )
            )
        if user.role in BANK_ROLES:
            return column == user.organization_id
        return false()

    @classmethod
    def visible_facility_ids(cls, session: Session, user: AuthenticatedUser):
        """A subquery of facility ids in the caller's scope."""

        if user.role in ENTERPRISE_ROLES:
            condition = (
                FinancingRequestModel.supplier_organization_id == user.organization_id
                if user.role == "supplier"
                else FinancingRequestModel.core_enterprise_organization_id == user.organization_id
            )
        else:
            condition = cls.organization_filter(user, FinancingFacilityModel.organization_id)
        return (
            select(FinancingFacilityModel.facility_id)
            .join(FinancingRequestModel, FinancingRequestModel.request_id == FinancingFacilityModel.request_id)
            .where(condition)
        )


def any_of(*conditions: ColumnElement[bool]) -> ColumnElement[bool]:
    return or_(*conditions)


__all__ = [
    "AccessScope",
    "PERMISSIONS",
    "PermissionDenied",
    "PermissionService",
    "ResourceNotVisible",
]
