from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import IntegrityError

from app.api.dependencies import require_roles
from app.identity import AuthenticatedUser
from app.schemas_outcome import (
    ActualOutcomeCreate,
    ActualOutcomePreviewResponse,
    ActualOutcomeResponse,
    CalibrationJobResponse,
    CalibrationRollbackRequest,
    CorrectionSubmissionResponse,
    OutcomeCorrectionCreate,
    OutcomeCorrectionResponse,
    OutcomeSubmissionResponse,
)
from app.services.outcomes import (
    ForbiddenOutcome,
    OutcomeConflict,
    OutcomeNotFound,
)


router = APIRouter(tags=["actual-outcomes"])
CurrentAuditor = Annotated[
    AuthenticatedUser,
    Depends(require_roles("auditor")),
]


class CalibrationRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    calibration_run_id: str
    trigger_outcome_id: str | None
    dataset_sha256: str
    sample_count: int
    positive_count: int
    negative_count: int
    metrics_before: dict[str, float] | None
    metrics_after: dict[str, float] | None
    oof_metrics_before: dict[str, float] | None
    oof_metrics_after: dict[str, float] | None
    temporal_validation: dict[str, Any] | None
    validation_policy: dict[str, Any] | None
    configuration: dict[str, float | int]
    status: str
    artifact_sha256: str | None
    artifact_schema: str | None
    artifact_integrity: str
    fold_assignment_sha256: str | None
    eligible_count: int
    excluded_count: int
    failure_code: str | None
    failure_reason: str | None
    dataset_snapshot_id: str | None
    deployment_status: str
    deployment_scope: str
    activation_mode: str | None
    activation_reason: str
    activated_at: str | None
    deactivated_at: str | None
    previous_active_run_id: str | None
    started_at: str
    completed_at: str


def _execute(operation: Callable[[], Any]) -> Any:
    try:
        return operation()
    except OutcomeNotFound as error:
        raise HTTPException(
            status_code=404,
            detail={"code": "outcome_not_found", "message": "Resource not found"},
        ) from error
    except ForbiddenOutcome as error:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "forbidden_role",
                "message": "Current role cannot access actual outcomes",
            },
        ) from error
    except OutcomeConflict as error:
        raise HTTPException(
            status_code=409,
            detail={"code": "outcome_conflict", "message": str(error)},
        ) from error
    except IntegrityError as error:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "outcome_conflict",
                "message": "Outcome conflicts with persisted immutable state",
            },
        ) from error


@router.post(
    "/api/v1/facilities/{facility_id}/actual-outcome",
    response_model=OutcomeSubmissionResponse,
    status_code=201,
)
def submit_actual_outcome(
    facility_id: str,
    payload: ActualOutcomeCreate,
    user: CurrentAuditor,
    request: Request,
):
    return _execute(lambda: request.app.state.outcome_service.submit(facility_id, payload, user))


@router.get(
    "/api/v1/facilities/{facility_id}/actual-outcome-preview",
    response_model=ActualOutcomePreviewResponse,
)
def preview_actual_outcome(
    facility_id: str,
    user: CurrentAuditor,
    request: Request,
):
    return _execute(lambda: request.app.state.outcome_service.preview(facility_id, user))


@router.get("/api/v1/outcomes", response_model=list[ActualOutcomeResponse])
def list_actual_outcomes(
    user: CurrentAuditor,
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    include_superseded: bool = Query(False),
):
    return request.app.state.outcome_service.list_outcomes(
        user, limit=limit, offset=offset, include_superseded=include_superseded
    )


@router.get("/api/v1/outcomes/{outcome_id}", response_model=ActualOutcomeResponse)
def get_actual_outcome(
    outcome_id: str,
    user: CurrentAuditor,
    request: Request,
):
    return _execute(lambda: request.app.state.outcome_service.get_outcome(outcome_id, user))


@router.get(
    "/api/v1/outcomes/{outcome_id}/corrections",
    response_model=list[OutcomeCorrectionResponse],
)
def list_outcome_corrections(
    outcome_id: str,
    user: CurrentAuditor,
    request: Request,
):
    return _execute(lambda: request.app.state.outcome_service.list_corrections(outcome_id, user))


@router.post(
    "/api/v1/outcomes/{outcome_id}/corrections",
    response_model=CorrectionSubmissionResponse,
    status_code=201,
)
def create_outcome_correction(
    outcome_id: str,
    payload: OutcomeCorrectionCreate,
    user: CurrentAuditor,
    request: Request,
):
    return _execute(
        lambda: request.app.state.outcome_service.create_correction(outcome_id, payload, user)
    )


@router.get(
    "/api/v1/calibration-runs",
    response_model=list[CalibrationRunResponse],
)
def list_calibration_runs(
    user: CurrentAuditor,
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    return request.app.state.outcome_service.list_runs(user, limit=limit, offset=offset)


@router.get(
    "/api/v1/calibration-runs/{run_id}",
    response_model=CalibrationRunResponse,
)
def get_calibration_run(
    run_id: str,
    user: CurrentAuditor,
    request: Request,
):
    return _execute(lambda: request.app.state.outcome_service.get_run(run_id, user))


@router.get(
    "/api/v1/calibration-jobs/{job_id}",
    response_model=CalibrationJobResponse,
)
def get_calibration_job(
    job_id: str,
    user: CurrentAuditor,
    request: Request,
):
    return _execute(lambda: request.app.state.outcome_service.get_job(job_id, user))


@router.get(
    "/api/v1/calibration-deployments/active",
    response_model=CalibrationRunResponse,
)
def get_active_calibration_deployment(
    user: CurrentAuditor,
    request: Request,
    scope: Literal["controlled_demo", "external_verified"],
):
    return _execute(
        lambda: request.app.state.outcome_service.get_active_deployment(user, scope=scope)
    )


@router.post(
    "/api/v1/calibration-deployments/rollback",
    response_model=CalibrationRunResponse,
)
def rollback_calibration_deployment(
    payload: CalibrationRollbackRequest,
    user: CurrentAuditor,
    request: Request,
):
    return _execute(
        lambda: request.app.state.outcome_service.rollback(
            payload.expected_active_run_id,
            user,
            scope=payload.deployment_scope,
        )
    )


__all__ = ["router"]
