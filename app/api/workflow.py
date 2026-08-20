from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.dependencies import current_user
from app.domain.workflow import InvalidTransition
from app.identity import AuthenticatedUser
from app.schemas_workflow import (
    ApplicationDraftCreate,
    ApplicationDraftUpdate,
    CommentVersionRequest,
    FinancingDecisionRequest,
    TradeConfirmationRequest,
    VersionRequest,
)
from app.services.workflow import (
    ApplicationNotFound,
    ForbiddenWorkflow,
    StaleApplication,
)


router = APIRouter(prefix="/api/v1", tags=["financing-workflow"])
CurrentUser = Annotated[AuthenticatedUser, Depends(current_user)]


def _execute(operation: Callable[[], Any]):
    try:
        return operation()
    except ApplicationNotFound as error:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "application_not_found",
                "message": "Application not found",
            },
        ) from error
    except ForbiddenWorkflow as error:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "forbidden_role",
                "message": "Current role cannot perform this action",
            },
        ) from error
    except StaleApplication as error:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "stale_application",
                "message": "Application was changed; refresh and retry",
            },
        ) from error
    except InvalidTransition as error:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "invalid_transition",
                "message": str(error),
            },
        ) from error


@router.get("/tasks")
def tasks(user: CurrentUser, request: Request):
    items = request.app.state.workflow_service.tasks_for_user(user)
    return {"count": len(items), "items": items}


@router.get("/dashboard")
def dashboard(user: CurrentUser, request: Request):
    applications = request.app.state.workflow_service.list_for_user(
        user, limit=200
    )
    tasks = [item for item in applications if item["allowed_actions"]]
    return {
        "role": user.role,
        "application_count": len(applications),
        "task_count": len(tasks),
        "status_counts": dict(Counter(item["status"] for item in applications)),
    }


@router.post("/applications", status_code=201)
def create_application(
    payload: ApplicationDraftCreate,
    user: CurrentUser,
    request: Request,
):
    return _execute(
        lambda: request.app.state.workflow_service.create_draft(payload, user)
    )


@router.get("/applications")
def list_applications(
    user: CurrentUser,
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    return request.app.state.workflow_service.list_for_user(
        user, limit=limit, offset=offset
    )


@router.get("/applications/{request_id}")
def get_application(request_id: str, user: CurrentUser, request: Request):
    return _execute(
        lambda: request.app.state.workflow_service.get(request_id, user)
    )


@router.patch("/applications/{request_id}")
def update_application(
    request_id: str,
    payload: ApplicationDraftUpdate,
    user: CurrentUser,
    request: Request,
):
    draft = ApplicationDraftCreate.model_validate(
        payload.model_dump(exclude={"version"})
    )
    return _execute(
        lambda: request.app.state.workflow_service.update_draft(
            request_id, payload.version, draft, user
        )
    )


@router.post("/applications/{request_id}/submit")
def submit_application(
    request_id: str,
    payload: VersionRequest,
    user: CurrentUser,
    request: Request,
):
    return _execute(
        lambda: request.app.state.workflow_service.submit(
            request_id, payload.version, user
        )
    )


@router.post("/applications/{request_id}/trade-confirmation")
def confirm_trade(
    request_id: str,
    payload: TradeConfirmationRequest,
    user: CurrentUser,
    request: Request,
):
    return _execute(
        lambda: request.app.state.workflow_service.confirm_trade(
            request_id,
            payload.version,
            confirmed=payload.confirmed,
            comment=payload.comment,
            user=user,
        )
    )


@router.post("/applications/{request_id}/risk-assessment")
def assess_risk(
    request_id: str,
    payload: VersionRequest,
    user: CurrentUser,
    request: Request,
):
    return _execute(
        lambda: request.app.state.workflow_service.assess_risk(
            request_id, payload.version, user
        )
    )


@router.post("/applications/{request_id}/decision")
def decide(
    request_id: str,
    payload: FinancingDecisionRequest,
    user: CurrentUser,
    request: Request,
):
    return _execute(
        lambda: request.app.state.workflow_service.decide(
            request_id,
            payload.version,
            decision=payload.decision,
            comment=payload.comment,
            user=user,
        )
    )


@router.post("/applications/{request_id}/control-action")
def apply_control(
    request_id: str,
    payload: CommentVersionRequest,
    user: CurrentUser,
    request: Request,
):
    return _execute(
        lambda: request.app.state.workflow_service.apply_control(
            request_id,
            payload.version,
            comment=payload.comment,
            user=user,
        )
    )


@router.post("/applications/{request_id}/audit-review")
def audit(
    request_id: str,
    payload: CommentVersionRequest,
    user: CurrentUser,
    request: Request,
):
    return _execute(
        lambda: request.app.state.workflow_service.audit(
            request_id,
            payload.version,
            comment=payload.comment,
            user=user,
        )
    )
