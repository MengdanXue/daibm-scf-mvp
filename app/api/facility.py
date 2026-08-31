from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import IntegrityError

from app.api.dependencies import current_user
from app.identity import AuthenticatedUser
from app.schemas_facility import (
    CreateFacilityRequest,
    DecisionPaymentRequest,
    MarkOverdueRequest,
    SubmitPaymentRequest,
    VersionedFacilityCommand,
)
from app.services.facility import (
    FacilityConflict,
    FacilityNotFound,
    ForbiddenFacility,
)


router = APIRouter(prefix="/api/v1/facilities", tags=["financing-facilities"])
CurrentUser = Annotated[AuthenticatedUser, Depends(current_user)]


class FacilityInstallmentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    installment_id: str
    sequence: int
    due_date: str
    amount: str
    paid_amount: str
    status: str


class FacilityPaymentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payment_id: str
    installment_id: str
    amount: str
    payment_reference: str
    status: str
    submitted_at: str
    decided_at: str | None
    decision_comment: str | None


class FacilityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    facility_id: str
    request_id: str
    principal: str
    outstanding_amount: str
    currency: str
    status: str
    version: int
    disbursement_reference: str | None
    disbursement_evidence_sha256: str | None
    created_at: str
    updated_at: str
    disbursement_initiated_at: str | None
    disbursed_at: str | None
    repaid_at: str | None
    closed_at: str | None
    allowed_actions: list[str]
    installments: list[FacilityInstallmentResponse]
    payments: list[FacilityPaymentResponse]


def _execute(operation: Callable[[], Any]) -> Any:
    try:
        return operation()
    except FacilityNotFound as error:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "facility_not_found",
                "message": "Facility not found",
            },
        ) from error
    except ForbiddenFacility as error:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "forbidden_role",
                "message": "Current role cannot perform this action",
            },
        ) from error
    except FacilityConflict as error:
        precondition_failed = str(error).startswith(
            "Facility creation requires"
        )
        raise HTTPException(
            status_code=409,
            detail={
                "code": (
                    "facility_precondition_failed"
                    if precondition_failed
                    else "facility_conflict"
                ),
                "message": str(error),
            },
        ) from error
    except IntegrityError as error:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "facility_conflict",
                "message": "Facility command conflicts with persisted state",
            },
        ) from error


@router.post("", response_model=FacilityResponse, status_code=201)
def create_facility(
    payload: CreateFacilityRequest,
    user: CurrentUser,
    request: Request,
):
    return _execute(
        lambda: request.app.state.facility_service.create(payload, user)
    )


@router.get("", response_model=list[FacilityResponse])
def list_facilities(
    user: CurrentUser,
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    return request.app.state.facility_service.list_for_user(
        user,
        limit=limit,
        offset=offset,
    )


@router.get("/{facility_id}", response_model=FacilityResponse)
def get_facility(
    facility_id: str,
    user: CurrentUser,
    request: Request,
):
    return _execute(
        lambda: request.app.state.facility_service.get(facility_id, user)
    )


@router.post(
    "/{facility_id}/initiate-disbursement",
    response_model=FacilityResponse,
)
def initiate_disbursement(
    facility_id: str,
    payload: VersionedFacilityCommand,
    user: CurrentUser,
    request: Request,
):
    return _execute(
        lambda: request.app.state.facility_service.initiate_disbursement(
            facility_id,
            payload,
            user,
        )
    )


@router.post(
    "/{facility_id}/confirm-disbursement",
    response_model=FacilityResponse,
)
def confirm_disbursement(
    facility_id: str,
    payload: VersionedFacilityCommand,
    user: CurrentUser,
    request: Request,
):
    return _execute(
        lambda: request.app.state.facility_service.confirm_disbursement(
            facility_id,
            payload,
            user,
        )
    )


@router.post(
    "/{facility_id}/payments",
    response_model=FacilityResponse,
)
def submit_payment(
    facility_id: str,
    payload: SubmitPaymentRequest,
    user: CurrentUser,
    request: Request,
):
    return _execute(
        lambda: request.app.state.facility_service.submit_payment(
            facility_id,
            payload,
            user,
        )
    )


@router.post(
    "/{facility_id}/payments/{payment_id}/decision",
    response_model=FacilityResponse,
)
def decide_payment(
    facility_id: str,
    payment_id: str,
    payload: DecisionPaymentRequest,
    user: CurrentUser,
    request: Request,
):
    return _execute(
        lambda: request.app.state.facility_service.decide_payment(
            facility_id,
            payment_id,
            payload,
            user,
        )
    )


@router.post(
    "/{facility_id}/mark-overdue",
    response_model=FacilityResponse,
)
def mark_overdue(
    facility_id: str,
    payload: MarkOverdueRequest,
    user: CurrentUser,
    request: Request,
):
    return _execute(
        lambda: request.app.state.facility_service.mark_overdue(
            facility_id,
            payload.installment_id,
            payload,
            user,
        )
    )


@router.post(
    "/{facility_id}/close",
    response_model=FacilityResponse,
)
def close_facility(
    facility_id: str,
    payload: VersionedFacilityCommand,
    user: CurrentUser,
    request: Request,
):
    return _execute(
        lambda: request.app.state.facility_service.close(
            facility_id,
            payload,
            user,
        )
    )


__all__ = ["FacilityResponse", "router"]
