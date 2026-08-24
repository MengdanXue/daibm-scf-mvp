from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.api.dependencies import require_roles
from app.identity import AuthenticatedUser


Auditor = Annotated[AuthenticatedUser, Depends(require_roles("auditor"))]
router = APIRouter(prefix="/api/v1", tags=["anchors"])


class AnchorOutboxResponse(BaseModel):
    anchor_id: UUID
    event_id: UUID
    subject_id: UUID
    event_hash: str
    chain_head_hash: str
    recorded_at: datetime
    schema_version: int
    model_version: str | None
    policy_version: str | None
    circuit_version: str | None
    proof_sha256: str | None
    status: str
    attempt_count: int
    next_attempt_at: datetime
    last_error_code: str | None
    anchored_at: datetime | None


class DispatchRequest(BaseModel):
    limit: int = Field(default=20, ge=1, le=100)


@router.get("/anchors", response_model=list[AnchorOutboxResponse])
def list_anchors(
    _user: Auditor,
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
):
    return request.app.state.anchor_dispatch_service.list_outbox(limit=limit)


@router.get("/anchors/{anchor_id}", response_model=AnchorOutboxResponse)
def get_anchor(anchor_id: UUID, _user: Auditor, request: Request):
    try:
        return request.app.state.anchor_dispatch_service.get_outbox(anchor_id)
    except KeyError as error:
        raise HTTPException(
            status_code=404,
            detail={"code": "anchor_not_found", "message": "Anchor not found"},
        ) from error


@router.post("/anchors/{anchor_id}/retry", response_model=AnchorOutboxResponse)
def retry_anchor(anchor_id: UUID, _user: Auditor, request: Request):
    try:
        return request.app.state.anchor_dispatch_service.retry(anchor_id)
    except KeyError as error:
        raise HTTPException(
            status_code=404,
            detail={"code": "anchor_not_found", "message": "Anchor not found"},
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "anchor_not_retryable",
                "message": "Only permanently failed anchors can be retried",
            },
        ) from error


@router.post("/anchor-dispatches")
def dispatch_anchors(payload: DispatchRequest, _user: Auditor, request: Request):
    return request.app.state.anchor_dispatch_service.dispatch_batch(limit=payload.limit)


__all__ = ["router"]
