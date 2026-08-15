from fastapi import APIRouter, HTTPException, Request

from app.schemas_research import ResearchInferenceRequest
from app.services.research_inference import (
    ResearchModelUnavailable,
    ResearchResourceNotFound,
)


router = APIRouter(prefix="/api/research", tags=["research"])


def _unavailable() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            "code": "research_model_unavailable",
            "message": "Promoted research model is unavailable",
        },
    )


@router.get("/status")
def research_status(request: Request):
    try:
        return request.app.state.research_service.status()
    except ResearchModelUnavailable as error:
        raise _unavailable() from error


@router.post("/inference")
def research_inference(payload: ResearchInferenceRequest, request: Request):
    try:
        assessment = request.app.state.research_service.assess(
            enterprise_id=payload.enterprise_id,
            graph_snapshot_id=payload.graph_snapshot_id,
            model_version_id=payload.model_version_id,
        )
    except ResearchResourceNotFound as error:
        raise HTTPException(
            status_code=404,
            detail="Research model or snapshot not found",
        ) from error
    except ResearchModelUnavailable as error:
        raise _unavailable() from error
    return request.app.state.research_service.response(assessment)
