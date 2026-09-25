"""Enterprise administration API: organizations, users, audit grants, security
events, configuration center, rule rollback and operations (health, metrics)."""

from __future__ import annotations

import hmac
import os
from collections.abc import Callable
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field

from app.api.dependencies import current_user
from app.identity import AuthenticatedUser, AuthenticationRequired
from app.services.config_center import ConfigConflict, ConfigNotFound
from app.services.organizations import OrganizationConflict, OrganizationNotFound
from app.services.permissions import PERMISSIONS, PermissionDenied, PermissionService
from app.services.risk_operations import RiskOpsConflict, RiskOpsForbidden, RiskOpsNotFound
from app.services.security import WeakPassword

router = APIRouter(tags=["enterprise"])
CurrentUser = Annotated[AuthenticatedUser, Depends(current_user)]
Reason = Annotated[str, Field(min_length=2, max_length=500)]


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateOrganization(_Body):
    code: Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9-]{2,31}$")]
    name: Annotated[str, Field(min_length=2, max_length=200)]
    organization_type: Literal["supplier", "core_enterprise", "financier", "auditor"]


class OrganizationStatus(_Body):
    status: Literal["active", "suspended"]
    reason: Reason


class CreateUser(_Body):
    username: Annotated[str, Field(pattern=r"^[a-z][a-z0-9._-]{2,63}$")]
    display_name: Annotated[str, Field(min_length=2, max_length=200)]
    role: Literal["supplier", "core_enterprise", "financier", "risk_manager", "auditor", "admin"]
    organization_id: str
    password: Annotated[str, Field(min_length=1, max_length=200)]


class UserActive(_Body):
    active: bool
    reason: Reason


class Grant(_Body):
    auditor_user_id: str
    organization_id: str
    reason: Reason


class Revoke(_Body):
    reason: Reason


class SetConfig(_Body):
    value: bool | int
    reason: Reason
    expected_version: int = Field(ge=0)


class RollbackConfig(_Body):
    to_version: int = Field(ge=1)
    reason: Reason
    expected_version: int = Field(ge=1)


class RollbackRule(_Body):
    to_version: int = Field(ge=1)
    change_reason: Reason
    expected_version: int = Field(ge=1)


def _execute(operation: Callable[[], Any]) -> Any:
    try:
        return operation()
    except (PermissionDenied, RiskOpsForbidden) as error:
        raise HTTPException(403, {"code": "forbidden_role", "message": str(error)}) from error
    except (OrganizationNotFound, ConfigNotFound, RiskOpsNotFound) as error:
        raise HTTPException(404, {"code": "not_found", "message": "Resource not found"}) from error
    except WeakPassword as error:
        raise HTTPException(422, {"code": "weak_password", "message": str(error)}) from error
    except (OrganizationConflict, ConfigConflict, RiskOpsConflict) as error:
        raise HTTPException(409, {"code": "conflict", "message": str(error)}) from error


def _svc(request: Request, name: str) -> Any:
    return getattr(request.app.state, name)


# --- Organizations and users ---------------------------------------------------------


@router.get("/api/v1/admin/organizations")
def list_organizations(user: CurrentUser, request: Request):
    return _execute(lambda: _svc(request, "organization_service").list_organizations(user))


@router.post("/api/v1/admin/organizations", status_code=201)
def create_organization(body: CreateOrganization, user: CurrentUser, request: Request):
    return _execute(
        lambda: _svc(request, "organization_service").create_organization(
            user, code=body.code, name=body.name, organization_type=body.organization_type
        )
    )


@router.post("/api/v1/admin/organizations/{organization_id}/status")
def organization_status(organization_id: str, body: OrganizationStatus, user: CurrentUser, request: Request):
    return _execute(
        lambda: _svc(request, "organization_service").set_organization_status(
            user, organization_id, status=body.status, reason=body.reason
        )
    )


@router.get("/api/v1/admin/users")
def list_users(user: CurrentUser, request: Request):
    return _execute(lambda: _svc(request, "organization_service").list_users(user))


@router.post("/api/v1/admin/users", status_code=201)
def create_user(body: CreateUser, user: CurrentUser, request: Request):
    return _execute(
        lambda: _svc(request, "organization_service").create_user(
            user,
            username=body.username,
            display_name=body.display_name,
            role=body.role,
            organization_id=body.organization_id,
            password=body.password,
        )
    )


@router.post("/api/v1/admin/users/{user_id}/active")
def user_active(user_id: str, body: UserActive, user: CurrentUser, request: Request):
    return _execute(
        lambda: _svc(request, "organization_service").set_user_active(
            user, user_id, active=body.active, reason=body.reason
        )
    )


@router.get("/api/v1/admin/audit-grants")
def list_grants(user: CurrentUser, request: Request):
    return _execute(lambda: _svc(request, "organization_service").list_grants(user))


@router.post("/api/v1/admin/audit-grants", status_code=201)
def grant_audit(body: Grant, user: CurrentUser, request: Request):
    return _execute(
        lambda: _svc(request, "organization_service").grant_audit(
            user, auditor_user_id=body.auditor_user_id, organization_id=body.organization_id, reason=body.reason
        )
    )


@router.post("/api/v1/admin/audit-grants/{grant_id}/revoke")
def revoke_audit(grant_id: str, body: Revoke, user: CurrentUser, request: Request):
    return _execute(
        lambda: _svc(request, "organization_service").revoke_audit(user, grant_id, reason=body.reason)
    )


@router.get("/api/v1/admin/security-events")
def security_events(
    user: CurrentUser,
    request: Request,
    event_type: str | None = Query(None, pattern=r"^[A-Z_]{3,40}$"),
    limit: int = Query(200, ge=1, le=1000),
):
    return _execute(
        lambda: _svc(request, "organization_service").security_events(user, event_type=event_type, limit=limit)
    )


@router.get("/api/v1/admin/permissions")
def permission_matrix(user: CurrentUser):
    """The single role -> action table the platform enforces (readable by any signed-in user)."""

    return {action: sorted(roles) for action, roles in sorted(PERMISSIONS.items())}


# --- Configuration center -------------------------------------------------------------


@router.get("/api/v1/admin/config")
def list_config(user: CurrentUser, request: Request):
    return _execute(lambda: _svc(request, "config_service").list_config(user))


@router.post("/api/v1/admin/config/{key}")
def set_config(key: str, body: SetConfig, user: CurrentUser, request: Request):
    return _execute(
        lambda: _svc(request, "config_service").set_value(
            user, key, value=body.value, reason=body.reason, expected_version=body.expected_version
        )
    )


@router.post("/api/v1/admin/config/{key}/rollback")
def rollback_config(key: str, body: RollbackConfig, user: CurrentUser, request: Request):
    return _execute(
        lambda: _svc(request, "config_service").rollback(
            user, key, to_version=body.to_version, reason=body.reason, expected_version=body.expected_version
        )
    )


@router.post("/api/v1/risk/rules/{rule_key}/rollback", status_code=201)
def rollback_rule(rule_key: str, body: RollbackRule, user: CurrentUser, request: Request):
    return _execute(
        lambda: _svc(request, "risk_rule_service").rollback_rule(
            rule_key, user, to_version=body.to_version, change_reason=body.change_reason,
            expected_version=body.expected_version,
        )
    )


# --- Operations ---------------------------------------------------------------------------


@router.get("/api/v1/ops/health")
def ops_health(request: Request):
    """Readiness for load balancers and Docker: no details beyond component state."""

    health = _svc(request, "ops_service").health()
    return JSONResponse(health, status_code=200 if health["status"] != "down" else 503)


def _metrics_allowed(request: Request) -> bool:
    token = os.environ.get("DAIBM_METRICS_TOKEN", "")
    header = request.headers.get("authorization", "")
    if token and hmac.compare_digest(header, f"Bearer {token}"):
        return True
    try:
        user = request.app.state.identity_service.authenticate(request.cookies.get("daibm_session"))
    except AuthenticationRequired:
        return False
    if not PermissionService.allowed(user, "ops:read"):
        raise HTTPException(403, {"code": "forbidden_role", "message": "Only administrators read metrics"})
    return True


@router.get("/api/v1/ops/metrics")
def ops_metrics(request: Request):
    if not _metrics_allowed(request):
        raise HTTPException(401, {"code": "authentication_required", "message": "Authentication required"})
    return _svc(request, "ops_service").metrics_snapshot()


@router.get("/metrics", response_class=PlainTextResponse)
def prometheus_metrics(request: Request) -> Response:
    if not _metrics_allowed(request):
        raise HTTPException(401, {"code": "authentication_required", "message": "Authentication required"})
    return PlainTextResponse(_svc(request, "ops_service").prometheus(), media_type="text/plain; version=0.0.4")


__all__ = ["router"]
