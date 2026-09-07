from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError

from app.api.dependencies import current_user
from app.identity import AuthenticatedUser
from app.schemas_facility import (
    CreateFacilityRequest,
    DeclareDefaultRequest,
    DecisionPaymentRequest,
    MarkOverdueRequest,
    RestructureFacilityRequest,
    SubmitPaymentRequest,
    VersionedFacilityCommand,
    WriteOffRequest,
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
    schedule_version: int
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


class FacilityDelinquencyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    delinquency_id: str
    marked_by_user_id: str
    days_past_due: int
    reason_code: str
    comment: str
    evidence_sha256: str
    recorded_at: str


class FacilityRestructureResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    restructure_id: str
    restructured_by_user_id: str
    old_schedule_version: int
    new_schedule_version: int
    reason_code: str
    comment: str
    evidence_sha256: str
    recorded_at: str


class FacilityDefaultResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_id: str
    schedule_version: int
    declared_by_user_id: str
    defaulted_at: str
    days_past_due: int
    reason_code: str
    comment: str
    evidence_sha256: str
    recorded_at: str


class FacilityWriteOffResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    writeoff_id: str
    auditor_user_id: str
    amount: str
    reason_code: str
    comment: str
    evidence_sha256: str
    recorded_at: str


class FacilityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    facility_id: str
    request_id: str
    principal: str
    outstanding_amount: str
    outstanding_balance: str = Field(
        description="Principal balance still outstanding on the facility."
    )
    recovered_amount: str = Field(
        description="Cumulative confirmed principal cash repayments across all schedules."
    )
    written_off_amount: str = Field(
        description="Principal formally written off on the facility."
    )
    realized_loss: str = Field(
        description="Realized principal loss, equal to the written-off amount."
    )
    settlement_classification: Literal["NORMAL_SETTLED", "WRITTEN_OFF"] | None
    currency: str
    status: str
    version: int
    current_schedule_version: int
    closure_reason: str | None
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
    delinquencies: list[FacilityDelinquencyResponse]
    restructures: list[FacilityRestructureResponse]
    default_event: FacilityDefaultResponse | None
    default_history: list[FacilityDefaultResponse]
    writeoff_event: FacilityWriteOffResponse | None


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
    "/{facility_id}/restructure",
    response_model=FacilityResponse,
)
def restructure_facility(
    facility_id: str,
    payload: RestructureFacilityRequest,
    user: CurrentUser,
    request: Request,
):
    return _execute(
        lambda: request.app.state.facility_service.restructure(
            facility_id,
            payload,
            user,
        )
    )


@router.post(
    "/{facility_id}/declare-default",
    response_model=FacilityResponse,
)
def declare_facility_default(
    facility_id: str,
    payload: DeclareDefaultRequest,
    user: CurrentUser,
    request: Request,
):
    return _execute(
        lambda: request.app.state.facility_service.declare_default(
            facility_id,
            payload,
            user,
        )
    )


@router.post(
    "/{facility_id}/write-off",
    response_model=FacilityResponse,
)
def write_off_facility(
    facility_id: str,
    payload: WriteOffRequest,
    user: CurrentUser,
    request: Request,
):
    return _execute(
        lambda: request.app.state.facility_service.write_off(
            facility_id,
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
