"""Risk operations API: dashboard, alert center, task center, rules and risk detail."""

from __future__ import annotations

import base64
import binascii
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.exc import IntegrityError

from app.api.dependencies import current_user
from app.identity import AuthenticatedUser
from app.services.risk_operations import RiskOpsConflict, RiskOpsForbidden, RiskOpsNotFound

router = APIRouter(tags=["risk-operations"])
CurrentUser = Annotated[AuthenticatedUser, Depends(current_user)]
Text500 = Annotated[str, Field(min_length=2, max_length=500)]


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VersionedBody(_Body):
    version: int = Field(ge=1)


class AssignAlert(VersionedBody):
    owner_user_id: str
    comment: str | None = Field(default=None, max_length=500)


class CommentBody(VersionedBody):
    comment: Text500


class OptionalComment(VersionedBody):
    comment: str | None = Field(default=None, max_length=500)


class ResolveAlert(VersionedBody):
    resolution: Text500


class CreateTask(_Body):
    title: Annotated[str, Field(min_length=2, max_length=200)]
    task_type: Literal["INVESTIGATION", "COLLECTION", "DISPOSAL_REVIEW", "DATA_FIX", "OTHER"]
    description: Text500
    assignee_user_id: str
    due_at: datetime
    alert_id: str | None = None
    facility_id: str | None = None

    @field_validator("due_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("due_at must be timezone-aware")
        return value


class ReassignTask(VersionedBody):
    assignee_user_id: str
    comment: str | None = Field(default=None, max_length=500)


class DueTask(VersionedBody):
    due_at: datetime
    comment: str | None = Field(default=None, max_length=500)

    @field_validator("due_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("due_at must be timezone-aware")
        return value


class NoteTask(VersionedBody):
    note: Text500


class TaskResult(VersionedBody):
    """A processing result file, sent base64-encoded (at most 2 MiB decoded)."""

    filename: Annotated[str, Field(min_length=1, max_length=200)]
    content_type: Annotated[str, Field(min_length=3, max_length=120)] = "application/octet-stream"
    content_base64: Annotated[str, Field(min_length=4, max_length=2_900_000)]


class CompleteTask(VersionedBody):
    result_summary: Text500


class ChangeRule(_Body):
    expected_version: int = Field(ge=1)
    threshold: Decimal | None = Field(default=None, ge=0, le=Decimal("100000"))
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    enabled: bool
    effective_from: datetime | None = None
    change_reason: Text500


def _execute(operation: Callable[[], Any]) -> Any:
    try:
        return operation()
    except RiskOpsNotFound as error:
        raise HTTPException(404, {"code": "not_found", "message": "Resource not found"}) from error
    except RiskOpsForbidden as error:
        raise HTTPException(403, {"code": "forbidden_role", "message": str(error)}) from error
    except RiskOpsConflict as error:
        raise HTTPException(409, {"code": "risk_ops_conflict", "message": str(error)}) from error
    except IntegrityError as error:
        raise HTTPException(
            409, {"code": "risk_ops_conflict", "message": "Change conflicts with governed history"}
        ) from error


def _svc(request: Request, name: str) -> Any:
    return getattr(request.app.state, name)


# --- Dashboard and detail ----------------------------------------------------------


@router.get("/api/v1/risk/dashboard")
def risk_dashboard(user: CurrentUser, request: Request):
    return _execute(lambda: _svc(request, "risk_insight_service").dashboard(user))


@router.get("/api/v1/risk/facilities")
def risk_facilities(user: CurrentUser, request: Request):
    return _execute(lambda: _svc(request, "risk_insight_service").facilities(user))


@router.get("/api/v1/risk/facilities/{facility_id}")
def risk_facility_detail(facility_id: str, user: CurrentUser, request: Request):
    return _execute(lambda: _svc(request, "risk_insight_service").facility_detail(facility_id, user))


# --- Alerts ------------------------------------------------------------------------------


@router.post("/api/v1/risk/alerts/scan")
def scan_alerts(user: CurrentUser, request: Request):
    return _execute(lambda: _svc(request, "risk_detection_service").scan_as(user))


@router.get("/api/v1/risk/alerts")
def list_alerts(
    user: CurrentUser,
    request: Request,
    status: str | None = Query(None, pattern=r"^(OPEN|ASSIGNED|PROCESSING|RESOLVED|CLOSED)$"),
    severity: str | None = Query(None, pattern=r"^(LOW|MEDIUM|HIGH|CRITICAL)$"),
    facility_id: str | None = None,
):
    return _execute(
        lambda: _svc(request, "risk_alert_service").list_alerts(
            user, status=status, severity=severity, facility_id=facility_id
        )
    )


@router.get("/api/v1/risk/alerts/{alert_id}")
def get_alert(alert_id: str, user: CurrentUser, request: Request):
    return _execute(lambda: _svc(request, "risk_alert_service").get_alert(alert_id, user))


@router.post("/api/v1/risk/alerts/{alert_id}/assign")
def assign_alert(alert_id: str, body: AssignAlert, user: CurrentUser, request: Request):
    return _execute(
        lambda: _svc(request, "risk_alert_service").assign(
            alert_id, user, owner_user_id=body.owner_user_id, version=body.version, comment=body.comment
        )
    )


@router.post("/api/v1/risk/alerts/{alert_id}/start")
def start_alert(alert_id: str, body: OptionalComment, user: CurrentUser, request: Request):
    return _execute(
        lambda: _svc(request, "risk_alert_service").start(
            alert_id, user, version=body.version, comment=body.comment
        )
    )


@router.post("/api/v1/risk/alerts/{alert_id}/resolve")
def resolve_alert(alert_id: str, body: ResolveAlert, user: CurrentUser, request: Request):
    return _execute(
        lambda: _svc(request, "risk_alert_service").resolve(
            alert_id, user, version=body.version, resolution=body.resolution
        )
    )


@router.post("/api/v1/risk/alerts/{alert_id}/close")
def close_alert(alert_id: str, body: CommentBody, user: CurrentUser, request: Request):
    return _execute(
        lambda: _svc(request, "risk_alert_service").close(
            alert_id, user, version=body.version, comment=body.comment
        )
    )


@router.post("/api/v1/risk/alerts/{alert_id}/reopen")
def reopen_alert(alert_id: str, body: CommentBody, user: CurrentUser, request: Request):
    return _execute(
        lambda: _svc(request, "risk_alert_service").reopen(
            alert_id, user, version=body.version, comment=body.comment
        )
    )


@router.post("/api/v1/risk/alerts/{alert_id}/comments")
def comment_alert(alert_id: str, body: CommentBody, user: CurrentUser, request: Request):
    return _execute(
        lambda: _svc(request, "risk_alert_service").comment(
            alert_id, user, version=body.version, comment=body.comment
        )
    )


# --- Tasks -------------------------------------------------------------------------------


@router.get("/api/v1/risk/assignees")
def task_assignees(user: CurrentUser, request: Request):
    return _execute(lambda: _svc(request, "risk_task_service").assignees(user))


@router.get("/api/v1/risk/tasks")
def list_tasks(
    user: CurrentUser,
    request: Request,
    view: str = Query("mine", pattern=r"^(mine|pending|completed|all)$"),
):
    return _execute(lambda: _svc(request, "risk_task_service").list_tasks(user, view=view))


@router.post("/api/v1/risk/tasks", status_code=201)
def create_task(body: CreateTask, user: CurrentUser, request: Request):
    return _execute(
        lambda: _svc(request, "risk_task_service").create(
            user,
            title=body.title,
            task_type=body.task_type,
            description=body.description,
            assignee_user_id=body.assignee_user_id,
            due_at=body.due_at,
            alert_id=body.alert_id,
            facility_id=body.facility_id,
        )
    )


@router.get("/api/v1/risk/tasks/{task_id}")
def get_task(task_id: str, user: CurrentUser, request: Request):
    return _execute(lambda: _svc(request, "risk_task_service").get_task(task_id, user))


@router.post("/api/v1/risk/tasks/{task_id}/reassign")
def reassign_task(task_id: str, body: ReassignTask, user: CurrentUser, request: Request):
    return _execute(
        lambda: _svc(request, "risk_task_service").reassign(
            task_id, user, version=body.version, assignee_user_id=body.assignee_user_id, comment=body.comment
        )
    )


@router.post("/api/v1/risk/tasks/{task_id}/due")
def due_task(task_id: str, body: DueTask, user: CurrentUser, request: Request):
    return _execute(
        lambda: _svc(request, "risk_task_service").set_due(
            task_id, user, version=body.version, due_at=body.due_at, comment=body.comment
        )
    )


@router.post("/api/v1/risk/tasks/{task_id}/notes")
def note_task(task_id: str, body: NoteTask, user: CurrentUser, request: Request):
    return _execute(
        lambda: _svc(request, "risk_task_service").add_note(task_id, user, version=body.version, note=body.note)
    )


@router.post("/api/v1/risk/tasks/{task_id}/start")
def start_task(task_id: str, body: VersionedBody, user: CurrentUser, request: Request):
    return _execute(lambda: _svc(request, "risk_task_service").start(task_id, user, version=body.version))


@router.post("/api/v1/risk/tasks/{task_id}/result")
def attach_task_result(task_id: str, body: TaskResult, user: CurrentUser, request: Request):
    try:
        content = base64.b64decode(body.content_base64, validate=True)
    except (binascii.Error, ValueError) as error:
        raise HTTPException(422, {"code": "invalid_file", "message": "content_base64 is not valid base64"}) from error
    return _execute(
        lambda: _svc(request, "risk_task_service").attach_result(
            task_id,
            user,
            version=body.version,
            filename=body.filename,
            content_type=body.content_type,
            content=content,
        )
    )


@router.get("/api/v1/risk/tasks/{task_id}/attachments/{attachment_id}")
def download_task_attachment(task_id: str, attachment_id: str, user: CurrentUser, request: Request):
    filename, content_type, content = _execute(
        lambda: _svc(request, "risk_task_service").attachment(task_id, attachment_id, user)
    )
    safe = "".join(ch for ch in filename if ch.isalnum() or ch in "._-") or "result"
    return Response(
        content=content,
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{safe}"'},
    )


@router.post("/api/v1/risk/tasks/{task_id}/complete")
def complete_task(task_id: str, body: CompleteTask, user: CurrentUser, request: Request):
    return _execute(
        lambda: _svc(request, "risk_task_service").complete(
            task_id, user, version=body.version, result_summary=body.result_summary
        )
    )


@router.post("/api/v1/risk/tasks/{task_id}/cancel")
def cancel_task(task_id: str, body: CommentBody, user: CurrentUser, request: Request):
    return _execute(
        lambda: _svc(request, "risk_task_service").cancel(task_id, user, version=body.version, comment=body.comment)
    )


# --- Rules --------------------------------------------------------------------------------


@router.get("/api/v1/risk/rules")
def list_rules(user: CurrentUser, request: Request):
    return _execute(lambda: _svc(request, "risk_rule_service").list_rules(user))


@router.post("/api/v1/risk/rules/{rule_key}/versions", status_code=201)
def change_rule(rule_key: str, body: ChangeRule, user: CurrentUser, request: Request):
    return _execute(
        lambda: _svc(request, "risk_rule_service").change_rule(
            rule_key,
            user,
            threshold=body.threshold,
            severity=body.severity,
            enabled=body.enabled,
            effective_from=body.effective_from,
            change_reason=body.change_reason,
            expected_version=body.expected_version,
        )
    )


__all__ = ["router"]
