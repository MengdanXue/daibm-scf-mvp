"""Risk-intelligence governance API: model registry, outcome review, decisions."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError

from app.api.dependencies import current_user, require_roles
from app.domain.governance import scope_matrix
from app.identity import AuthenticatedUser
from app.schemas_outcome import OutcomeSupersedeCreate
from app.services.model_registry import (
    RegistryConflict,
    RegistryForbidden,
    RegistryNotFound,
)
from app.services.outcomes import ForbiddenOutcome, OutcomeConflict, OutcomeNotFound
from app.services.workflow import ApplicationNotFound

router = APIRouter(tags=["model-governance"])
CurrentUser = Annotated[AuthenticatedUser, Depends(current_user)]
CurrentAuditor = Annotated[AuthenticatedUser, Depends(require_roles("auditor"))]


class RetireModelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,63}$")


def _execute(operation: Callable[[], Any]) -> Any:
    try:
        return operation()
    except (RegistryNotFound, OutcomeNotFound, ApplicationNotFound) as error:
        raise HTTPException(
            status_code=404, detail={"code": "not_found", "message": "Resource not found"}
        ) from error
    except (RegistryForbidden, ForbiddenOutcome) as error:
        raise HTTPException(
            status_code=403, detail={"code": "forbidden_role", "message": str(error)}
        ) from error
    except (RegistryConflict, OutcomeConflict) as error:
        raise HTTPException(
            status_code=409, detail={"code": "governance_conflict", "message": str(error)}
        ) from error
    except IntegrityError as error:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "governance_conflict",
                "message": "Command conflicts with governed history",
            },
        ) from error


class ActivateVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=8, max_length=500)


class RollbackVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,63}$")


class RegisterVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    calibration_run_id: str


ScopeFilter = Query(None, pattern=r"^(controlled_demo|external_verified|mixed)$")


@router.get("/api/v1/model-versions")
def list_model_versions(user: CurrentUser, request: Request, scope: str | None = ScopeFilter):
    return _execute(
        lambda: request.app.state.model_registry_service.list_versions(user, scope=scope)
    )


@router.post("/api/v1/model-versions", status_code=201)
def register_model_version(
    payload: RegisterVersionRequest, user: CurrentAuditor, request: Request
):
    return _execute(
        lambda: request.app.state.model_registry_service.register_version(
            payload.calibration_run_id, user
        )
    )


@router.get("/api/v1/model-versions/activation-history")
def model_activation_history(
    user: CurrentUser, request: Request, scope: str | None = ScopeFilter
):
    return _execute(
        lambda: request.app.state.model_registry_service.activation_history(user, scope=scope)
    )


@router.get("/api/v1/model-versions/{version_id}")
def get_model_version(version_id: str, user: CurrentUser, request: Request):
    return _execute(
        lambda: request.app.state.model_registry_service.get_version(version_id, user)
    )


@router.post("/api/v1/model-versions/{version_id}/activate")
def activate_model_version(
    version_id: str, payload: ActivateVersionRequest, user: CurrentAuditor, request: Request
):
    return _execute(
        lambda: request.app.state.model_registry_service.activate_version(
            version_id, user, reason=payload.reason
        )
    )


@router.post("/api/v1/model-versions/{version_id}/rollback")
def rollback_model_version(
    version_id: str, payload: RollbackVersionRequest, user: CurrentAuditor, request: Request
):
    return _execute(
        lambda: request.app.state.model_registry_service.rollback_version(
            version_id, user, reason_code=payload.reason_code
        )
    )


@router.get("/api/v1/model-registry")
def list_models(
    user: CurrentUser,
    request: Request,
    scope: str | None = Query(None, pattern=r"^(controlled_demo|external_verified|mixed)$"),
):
    return _execute(lambda: request.app.state.model_registry_service.list_models(user, scope=scope))


@router.get("/api/v1/model-registry/scope-compatibility")
def get_scope_compatibility(user: CurrentUser) -> list[dict[str, str]]:
    return scope_matrix()


@router.get("/api/v1/model-registry/{kind}/{model_id}")
def get_model(kind: str, model_id: str, user: CurrentUser, request: Request):
    return _execute(
        lambda: request.app.state.model_registry_service.get_model(kind, model_id, user)
    )


@router.post("/api/v1/model-registry/calibration/{model_id}/retire")
def retire_model(
    model_id: str, payload: RetireModelRequest, user: CurrentAuditor, request: Request
):
    return _execute(
        lambda: request.app.state.model_registry_service.retire(
            model_id, user, reason_code=payload.reason_code
        )
    )


@router.get("/api/v1/outcome-governance/summary")
def outcome_governance_summary(user: CurrentUser, request: Request):
    return _execute(lambda: request.app.state.outcome_service.governance_summary(user))


@router.get("/api/v1/outcomes/{outcome_id}/lineage")
def outcome_lineage(outcome_id: str, user: CurrentAuditor, request: Request):
    return _execute(lambda: request.app.state.outcome_service.lineage(outcome_id, user))


@router.post("/api/v1/outcomes/{outcome_id}/supersede", status_code=201)
def supersede_outcome(
    outcome_id: str,
    payload: OutcomeSupersedeCreate,
    user: CurrentAuditor,
    request: Request,
):
    return _execute(
        lambda: request.app.state.outcome_service.supersede(outcome_id, payload, user)
    )


@router.get("/api/v1/applications/{request_id}/risk-decisions")
def risk_decisions(request_id: str, user: CurrentUser, request: Request):
    return _execute(
        lambda: request.app.state.workflow_service.risk_decisions(request_id, user)
    )


__all__ = ["router"]
