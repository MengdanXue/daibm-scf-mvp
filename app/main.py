from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import SQLAlchemyError

from app.api.research import router as research_router
from app.config import PostgresSettings, ResearchSettings
from app.database import Database
from app.schemas import FinancingRequestCreate
from app.service import FinancingService
from app.services.research_inference import ResearchInferenceService
from app.services.research_decision import ResearchDecisionService

STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app(
    database: Database | None = None,
    *,
    research_settings: ResearchSettings | None = None,
) -> FastAPI:
    owns_database = database is None
    active_database = database or Database.create(
        PostgresSettings.from_env().sqlalchemy_url
    )
    service = FinancingService(active_database.session_factory)
    active_research_settings = research_settings or ResearchSettings.from_env()
    research_service = ResearchInferenceService(
        active_database.session_factory,
        active_research_settings,
    )
    research_decision_service = ResearchDecisionService(
        active_database.session_factory,
        research_service,
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        application.state.database = active_database
        application.state.service = service
        application.state.research_service = research_service
        application.state.research_decision_service = research_decision_service
        research_service.initialize()
        yield
        if owns_database:
            active_database.dispose()

    application = FastAPI(
        title="DAIBM-SCF Minimal MVP",
        version="0.4.0",
        description=(
            "Scenario demonstrator for an auditable supply-chain finance "
            "risk loop."
        ),
        lifespan=lifespan,
    )
    application.mount(
        "/static",
        StaticFiles(directory=STATIC_DIR),
        name="static",
    )
    application.include_router(research_router)

    @application.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    @application.get("/api/health")
    def health(request: Request):
        try:
            request.app.state.database.is_reachable()
            verification = request.app.state.service.verify_ledger()
        except SQLAlchemyError as error:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "database_unavailable",
                    "message": "PostgreSQL is unavailable",
                },
            ) from error
        research_health = request.app.state.research_service.health()
        if (
            research_health["status"] != "ready"
            and research_health["required"]
        ):
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "research_model_unavailable",
                    "message": "Promoted research model is unavailable",
                },
            )
        return {
            "status": "ok",
            "database": {
                "backend": "postgresql",
                "reachable": True,
            },
            "ledger": verification,
            "research_core": research_health,
        }

    @application.get("/api/dashboard")
    def dashboard(request: Request):
        return request.app.state.service.dashboard()

    @application.post("/api/requests", status_code=201)
    def create_financing_request(
        payload: FinancingRequestCreate,
        request: Request,
    ):
        return request.app.state.service.create_request(payload)

    @application.get("/api/requests")
    def list_financing_requests(
        request: Request,
        limit: int = Query(50, ge=1, le=200),
    ):
        return request.app.state.service.list_requests(limit=limit)

    @application.get("/api/requests/{request_id}")
    def get_financing_request(request_id: str, request: Request):
        try:
            return request.app.state.service.get_request(request_id)
        except KeyError as error:
            raise HTTPException(
                status_code=404,
                detail="Request not found",
            ) from error

    @application.get("/api/ledger")
    def ledger(
        request: Request,
        limit: int = Query(100, ge=1, le=500),
    ):
        return request.app.state.service.list_ledger(limit=limit)

    @application.get("/api/ledger/verify")
    def verify_ledger(request: Request):
        return request.app.state.service.verify_ledger()

    @application.post("/api/demo/seed")
    def seed_demo(request: Request):
        return request.app.state.service.seed_demo()

    @application.post("/api/demo/reset")
    def reset_demo(request: Request):
        return request.app.state.service.reset_demo()

    @application.post("/api/demo/tamper")
    def tamper_demo(request: Request):
        return request.app.state.service.tamper_demo_ledger()

    return application


app = create_app()
