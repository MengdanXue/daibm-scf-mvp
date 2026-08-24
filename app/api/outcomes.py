from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import IntegrityError

from app.api.dependencies import require_roles
from app.identity import AuthenticatedUser
from app.schemas_outcome import ActualOutcomeCreate
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


class ActualOutcomeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome_id: str
    facility_id: str
    request_id: str
    risk_assessment_id: str
    model_version_id: str | None
    risk_engine_version: str
    defaulted: bool
    days_past_due: int
    loss_amount: str
    observed_at: str
    evidence_sha256: str
    provenance: str
    original_risk_score: float
    risk_input_sha256: str
    recorded_at: str


class CalibrationRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    calibration_run_id: str
    trigger_outcome_id: str
    dataset_sha256: str
    sample_count: int
    positive_count: int
    negative_count: int
    metrics_before: dict[str, float] | None
    metrics_after: dict[str, float] | None
    configuration: dict[str, float | int]
    status: str
    artifact_sha256: str | None
    artifact_integrity: str
    failure_code: str | None
    promotion_status: str
    started_at: str
    completed_at: str


class OutcomeSubmissionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: ActualOutcomeResponse
    calibration_run: CalibrationRunResponse


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
    return _execute(
        lambda: request.app.state.outcome_service.submit(facility_id, payload, user)
    )


@router.get("/api/v1/outcomes", response_model=list[ActualOutcomeResponse])
def list_actual_outcomes(
    user: CurrentAuditor,
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    return request.app.state.outcome_service.list_outcomes(
        user, limit=limit, offset=offset
    )


@router.get("/api/v1/outcomes/{outcome_id}", response_model=ActualOutcomeResponse)
def get_actual_outcome(
    outcome_id: str,
    user: CurrentAuditor,
    request: Request,
):
    return _execute(
        lambda: request.app.state.outcome_service.get_outcome(outcome_id, user)
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
    return request.app.state.outcome_service.list_runs(
        user, limit=limit, offset=offset
    )


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


__all__ = ["router"]
