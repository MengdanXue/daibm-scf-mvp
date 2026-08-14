from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import database_path
from .schemas import FinancingRequestCreate
from .service import FinancingService


STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app(db_path: Path | None = None) -> FastAPI:
    service = FinancingService(db_path or database_path())

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        service.initialize()
        application.state.service = service
        yield

    application = FastAPI(
        title="DAIBM-SCF Minimal MVP",
        version="0.2.0",
        description="Scenario demonstrator for an auditable supply-chain finance risk loop.",
        lifespan=lifespan,
    )
    application.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @application.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    @application.get("/api/health")
    def health(request: Request):
        verification = request.app.state.service.ledger.verify()
        return {"status": "ok", "ledger": verification}

    @application.get("/api/dashboard")
    def dashboard(request: Request):
        return request.app.state.service.dashboard()

    @application.post("/api/requests", status_code=201)
    def create_financing_request(payload: FinancingRequestCreate, request: Request):
        return request.app.state.service.create_request(payload)

    @application.get("/api/requests")
    def list_financing_requests(request: Request, limit: int = Query(50, ge=1, le=200)):
        return request.app.state.service.list_requests(limit=limit)

    @application.get("/api/requests/{request_id}")
    def get_financing_request(request_id: str, request: Request):
        try:
            return request.app.state.service.get_request(request_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Request not found") from error

    @application.get("/api/ledger")
    def ledger(request: Request, limit: int = Query(100, ge=1, le=500)):
        return request.app.state.service.ledger.list(limit=limit)

    @application.get("/api/ledger/verify")
    def verify_ledger(request: Request):
        return request.app.state.service.ledger.verify()

    @application.post("/api/demo/seed")
    def seed_demo(request: Request):
        return request.app.state.service.seed_demo()

    @application.post("/api/demo/reset")
    def reset_demo(request: Request):
        return request.app.state.service.reset_demo()

    return application


app = create_app()
