from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError

from app.api.dependencies import current_user
from app.domain.facility import state_machine_definition
from app.identity import AuthenticatedUser
from app.schemas_facility import (
    CreateFacilityRequest,
    DeclareDefaultRequest,
    DecisionPaymentRequest,
    LifecycleDecisionRequest,
    MarkOverdueRequest,
    RecordRecoveryRequest,
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


class FacilityRecoveryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recovery_id: str
    amount: str
    applied_to: Literal["outstanding", "written_off"]
    source: str
    recovery_reference: str
    evidence_sha256: str
    recorded_by_user_id: str
    recorded_at: str


class FacilityLifecycleDecisionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_id: str
    decision_type: Literal["disposal_opened", "disposal_closed", "recovery_started"]
    schedule_version: int
    decided_by_user_id: str
    reason_code: str
    comment: str
    evidence_sha256: str
    recorded_at: str


class FacilityContractVersionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: int
    origin: Literal["origination", "restructure", "migration_backfill"]
    principal: str
    outstanding_at_start: str | None
    currency: str
    schedule: list[dict[str, Any]]
    superseded_schedule: list[dict[str, Any]] | None
    restructure_id: str | None
    terms_sha256: str
    recorded_at: str


class FacilityStatusTransitionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_status: str | None
    to_status: str
    trigger_action: str
    actor_role: str | None
    resulting_version: int
    reason_code: str | None
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
    recovery_collected_amount: str = Field(
        description="Recovery cash from third-party sources applied to the live balance."
    )
    post_writeoff_recovery_amount: str = Field(
        description="Recovery cash collected on the claim after it was written off."
    )
    net_loss: str = Field(
        description="Written-off amount less recoveries collected after write-off."
    )
    arrears_amount: str = Field(
        description="Unpaid amount of current-schedule installments already past due."
    )
    settlement_classification: (
        Literal["NORMAL_SETTLED", "SETTLED_AFTER_DEFAULT", "WRITTEN_OFF"] | None
    )
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
    recoveries: list[FacilityRecoveryResponse]
    lifecycle_decisions: list[FacilityLifecycleDecisionResponse]
    contract_versions: list[FacilityContractVersionResponse]
    status_history: list[FacilityStatusTransitionResponse]


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


@router.get("/state-machine")
def facility_state_machine(user: CurrentUser) -> dict[str, Any]:
    """Stages, entry conditions, role actions and every legal status change."""

    return state_machine_definition()


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
    "/{facility_id}/open-disposal",
    response_model=FacilityResponse,
)
def open_disposal(
    facility_id: str,
    payload: LifecycleDecisionRequest,
    user: CurrentUser,
    request: Request,
):
    return _execute(
        lambda: request.app.state.facility_service.open_disposal(
            facility_id,
            payload,
            user,
        )
    )


@router.post(
    "/{facility_id}/close-disposal",
    response_model=FacilityResponse,
)
def close_disposal(
    facility_id: str,
    payload: LifecycleDecisionRequest,
    user: CurrentUser,
    request: Request,
):
    return _execute(
        lambda: request.app.state.facility_service.close_disposal(
            facility_id,
            payload,
            user,
        )
    )


@router.post(
    "/{facility_id}/start-recovery",
    response_model=FacilityResponse,
)
def start_recovery(
    facility_id: str,
    payload: LifecycleDecisionRequest,
    user: CurrentUser,
    request: Request,
):
    return _execute(
        lambda: request.app.state.facility_service.start_recovery(
            facility_id,
            payload,
            user,
        )
    )


@router.post(
    "/{facility_id}/recoveries",
    response_model=FacilityResponse,
)
def record_recovery(
    facility_id: str,
    payload: RecordRecoveryRequest,
    user: CurrentUser,
    request: Request,
):
    return _execute(
        lambda: request.app.state.facility_service.record_recovery(
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
