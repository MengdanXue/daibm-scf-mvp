from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import SQLAlchemyError

from app.api import facility_router
from app.api.anchors import router as anchor_router
from app.api.outcomes import router as outcome_router
from app.api.governance import router as governance_router
from app.api.risk_ops import router as risk_ops_router
from app.api.auth import router as auth_router
from app.api.enterprise import router as enterprise_router
from app.api.dependencies import require_roles
from app.api.research import router as research_router
from app.api.workflow import router as workflow_router
from app.config import (
    FabricGatewaySettings,
    PostgresSettings,
    ResearchSettings,
    ZkpProverSettings,
)
from app.database import Database
from app.demo_dataset import DemoDatasetSeeder
from app.identity import AuthenticationRequired
from app.ops.metrics import Heartbeats, RequestMetrics
from app.ops.service import OpsService
from app.schemas import FinancingRequestCreate, IntegrityRecoveryRequest
from app.service import FinancingService
from app.services.identity import IdentityService
from app.services.facility import FacilityService
from app.services.workflow import WorkflowService
from app.services.invoice_proof import HttpInvoiceLimitProver
from app.services.research_inference import ResearchInferenceService
from app.services.research_decision import ResearchDecisionService
from app.services.research_scenario import ResearchScenarioService
from app.services.integrity import (
    IntegrityError,
    IntegrityService,
    NoIntegrityViolation,
)
from app.services.anchor_dispatch import AnchorDispatchService, FabricGatewayClient
from app.services.model_registry import ModelRegistryService
from app.services.outcome_governance import OutcomeGovernanceService
from app.services.risk_insight import RiskInsightService
from app.services.risk_operations import (
    RiskAlertService,
    RiskDetectionService,
    RiskRuleService,
    seed_default_rules,
)
from app.services.risk_tasks import RiskTaskService
from app.services.outcomes import OutcomeService
from app.services.calibration_jobs import CalibrationJobService
from app.services.config_center import ConfigService
from app.services.organizations import OrganizationService
from app.services.permissions import PermissionDenied
from app.services.security import SecuritySettings, record_security_event

STATIC_DIR = Path(__file__).resolve().parent / "static"
VERSION_FILE = Path(__file__).resolve().parents[1] / "VERSION"
# The release version; the Docker image copies VERSION next to app/.
APP_VERSION = VERSION_FILE.read_text(encoding="utf-8").strip() if VERSION_FILE.exists() else "1.0.0"


def _flag(name: str, *, default: str) -> bool:
    raw = os.environ.get(name, default).strip().lower()
    if raw not in {"true", "false"}:
        raise ValueError(f"{name} must be true or false")
    return raw == "true"


def _monitor_interval() -> float:
    """RISK_MONITOR_INTERVAL_SECONDS: how often the rules scan recorded data."""

    value = float(os.environ.get("RISK_MONITOR_INTERVAL_SECONDS", "60"))
    if value < 5:
        raise ValueError("RISK_MONITOR_INTERVAL_SECONDS must be at least 5")
    return value


async def _run_risk_monitor(
    detection: RiskDetectionService,
    stop_event: asyncio.Event,
    interval: Callable[[], float],
    heartbeat: Callable[[], None] | None = None,
) -> None:
    """Periodic rule scan inside the existing process; no queue, no scheduler.

    The interval is re-read every cycle so the configuration center applies
    without a restart."""

    while not stop_event.is_set():
        if heartbeat is not None:
            heartbeat()
        try:
            await asyncio.to_thread(detection.scan)
        except Exception:
            # A transient database error must not end the monitor.
            pass
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval())
        except TimeoutError:
            pass


def _auto_promotion_enabled() -> bool:
    """CALIBRATION_AUTO_PROMOTION=false holds validated candidates for an auditor."""

    return _flag("CALIBRATION_AUTO_PROMOTION", default="true")


def create_app(
    database: Database | None = None,
    *,
    research_settings: ResearchSettings | None = None,
    calibration_artifact_dir: Path | None = None,
    calibration_worker_enabled: bool = True,
    maintenance_mode: bool = False,
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
    research_scenario_service = ResearchScenarioService(
        research_service,
        research_decision_service,
    )
    integrity_service = IntegrityService(active_database.session_factory)
    _monitor_interval()  # validate the environment default at startup
    config_service = ConfigService(active_database.session_factory)
    request_metrics = RequestMetrics()
    heartbeats = Heartbeats()
    identity_service = IdentityService(
        active_database.session_factory,
        settings_provider=lambda: SecuritySettings.from_env().with_overrides(config_service.values()),
    )
    organization_service = OrganizationService(active_database.session_factory)

    def monitor_interval() -> float:
        return float(config_service.get("risk.monitor_interval_seconds"))

    ops_service = OpsService(
        active_database.session_factory,
        metrics=request_metrics,
        heartbeats=heartbeats,
        monitor_interval=monitor_interval,
    )
    prover_settings = ZkpProverSettings.from_env()
    workflow_service = WorkflowService(
        active_database.session_factory,
        invoice_prover=HttpInvoiceLimitProver(prover_settings),
        proof_required=prover_settings.required,
    )
    facility_service = FacilityService(active_database.session_factory)
    anchor_dispatch_service = AnchorDispatchService(
        active_database.session_factory,
        FabricGatewayClient(FabricGatewaySettings.from_env()),
    )
    outcome_service = OutcomeService(
        active_database.session_factory,
        artifact_root=(
            calibration_artifact_dir
            or Path(__file__).resolve().parents[1]
            / "artifacts"
            / "candidates"
            / "calibration"
        ),
        auto_promotion=_auto_promotion_enabled(),
        manual_review=_flag("OUTCOME_MANUAL_REVIEW", default="false"),
        policy_provider=config_service.values,
    )
    outcome_governance_service = OutcomeGovernanceService(
        active_database.session_factory, outcome_service=outcome_service
    )
    risk_rule_service = RiskRuleService(active_database.session_factory)
    risk_detection_service = RiskDetectionService(active_database.session_factory)
    risk_alert_service = RiskAlertService(active_database.session_factory)
    risk_task_service = RiskTaskService(active_database.session_factory)
    risk_insight_service = RiskInsightService(
        active_database.session_factory, facility_service=facility_service
    )
    model_registry_service = ModelRegistryService(
        active_database.session_factory, outcome_service=outcome_service
    )
    calibration_job_service = CalibrationJobService(
        active_database.session_factory,
        outcome_service=outcome_service,
        heartbeat=lambda: heartbeats.beat("calibration_worker"),
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        application.state.database = active_database
        application.state.service = service
        application.state.research_service = research_service
        application.state.research_decision_service = research_decision_service
        application.state.research_scenario_service = research_scenario_service
        application.state.integrity_service = integrity_service
        application.state.identity_service = identity_service
        application.state.workflow_service = workflow_service
        application.state.facility_service = facility_service
        application.state.anchor_dispatch_service = anchor_dispatch_service
        application.state.outcome_service = outcome_service
        application.state.calibration_job_service = calibration_job_service
        application.state.model_registry_service = model_registry_service
        application.state.outcome_governance_service = outcome_governance_service
        application.state.risk_rule_service = risk_rule_service
        application.state.risk_detection_service = risk_detection_service
        application.state.risk_alert_service = risk_alert_service
        application.state.risk_task_service = risk_task_service
        application.state.risk_insight_service = risk_insight_service
        application.state.config_service = config_service
        application.state.organization_service = organization_service
        application.state.ops_service = ops_service
        application.state.request_metrics = request_metrics
        application.state.heartbeats = heartbeats
        if maintenance_mode:
            # The original 8010 maintenance entrypoint must not create demo
            # identities, register missing artifacts, or settle deployments.
            research_service.initialize_existing()
        else:
            identity_service.seed_demo_accounts()
            research_service.initialize()
            outcome_service.reconcile_deployments()
            with active_database.session_factory.begin() as session:
                seed_default_rules(session)
            if _flag("DAIBM_DEMO_DATASET", default="false"):
                # Once, through the business services, before any worker runs.
                DemoDatasetSeeder(
                    active_database.session_factory,
                    artifact_root=outcome_service.artifact_root,
                ).seed()
        stop_event: asyncio.Event | None = None
        worker_task: asyncio.Task[None] | None = None
        monitor_task: asyncio.Task[None] | None = None
        workers_enabled = calibration_worker_enabled and not maintenance_mode
        heartbeats.enable("calibration_worker", workers_enabled)
        heartbeats.enable("risk_monitor", workers_enabled)
        if workers_enabled:
            stop_event = asyncio.Event()
            worker_task = asyncio.create_task(calibration_job_service.run(stop_event))
            monitor_task = asyncio.create_task(
                _run_risk_monitor(
                    risk_detection_service,
                    stop_event,
                    monitor_interval,
                    lambda: heartbeats.beat("risk_monitor"),
                )
            )
        try:
            yield
        finally:
            if stop_event is not None and worker_task is not None:
                stop_event.set()
                await worker_task
            if monitor_task is not None:
                await monitor_task
            if owns_database:
                active_database.dispose()

    application = FastAPI(
        title="DAIBM-SCF Minimal MVP",
        version=APP_VERSION,
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
    application.include_router(auth_router)
    application.include_router(workflow_router)
    application.include_router(facility_router)
    application.include_router(anchor_router)
    application.include_router(outcome_router)
    application.include_router(governance_router)
    application.include_router(risk_ops_router)
    application.include_router(enterprise_router)

    def _audit_denial(request: Request, status: int) -> None:
        """Every 403 becomes a PERMISSION_DENIED security event, whichever router raised it."""

        state = request.app.state
        try:
            user = state.identity_service.authenticate(request.cookies.get("daibm_session"))
        except AuthenticationRequired:
            user = None
        route = request.scope.get("route")
        with state.database.session_factory.begin() as session:
            record_security_event(
                session,
                "PERMISSION_DENIED",
                f"{request.method} {getattr(route, 'path', request.url.path)}",
                user_id=user.user_id if user else None,
                username=user.username if user else None,
                organization_id=user.organization_id if user else None,
                resource_type="http",
                resource_id=request.url.path[:300],
                detail={"status": status, "role": user.role if user else None},
                client_ip=request.client.host if request.client else None,
            )

    @application.middleware("http")
    async def enterprise_middleware(request: Request, call_next):
        started = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            route = request.scope.get("route")
            request_metrics.record(
                request.method,
                getattr(route, "path", "unmatched"),
                status,
                time.perf_counter() - started,
            )
            if status == 403:
                try:
                    await asyncio.to_thread(_audit_denial, request, status)
                except Exception:
                    # Auditing must not turn a denial into a server error.
                    pass

    @application.exception_handler(PermissionDenied)
    async def permission_denied(_request: Request, error: PermissionDenied):
        return JSONResponse(
            status_code=403,
            content={"detail": {"code": "forbidden_role", "message": str(error)}},
        )

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
        if not verification["valid"]:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "ledger_integrity_failed",
                    "message": (
                        "Audit ledger integrity verification failed"
                    ),
                },
            )
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
    def dashboard(
        request: Request,
        _user=Depends(require_roles("financier", "auditor")),
    ):
        return request.app.state.service.dashboard(_user)

    @application.post("/api/requests", status_code=201)
    def create_financing_request(
        payload: FinancingRequestCreate,
        request: Request,
        _user=Depends(require_roles("financier", "auditor")),
    ):
        return request.app.state.service.create_request(payload, _user)

    @application.get("/api/requests")
    def list_financing_requests(
        request: Request,
        limit: int = Query(50, ge=1, le=200),
        _user=Depends(require_roles("financier", "auditor")),
    ):
        return request.app.state.service.list_requests(limit=limit, viewer=_user)

    @application.get("/api/requests/{request_id}")
    def get_financing_request(
        request_id: str,
        request: Request,
        _user=Depends(require_roles("financier", "auditor")),
    ):
        try:
            return request.app.state.service.get_request(request_id, _user)
        except KeyError as error:
            raise HTTPException(
                status_code=404,
                detail="Request not found",
            ) from error

    @application.get("/api/ledger")
    def ledger(
        request: Request,
        limit: int = Query(100, ge=1, le=500),
        _user=Depends(require_roles("auditor")),
    ):
        return request.app.state.service.list_ledger(limit=limit)

    @application.get("/api/ledger/verify")
    def verify_ledger(
        request: Request,
        _user=Depends(require_roles("auditor")),
    ):
        return request.app.state.service.verify_ledger()

    @application.post("/api/demo/seed")
    def seed_demo(
        request: Request,
        _user=Depends(require_roles("auditor")),
    ):
        return request.app.state.service.seed_demo()

    @application.post("/api/demo/reset")
    def reset_demo(
        request: Request,
        _user=Depends(require_roles("auditor")),
    ):
        raise HTTPException(
            status_code=410,
            detail={
                "code": "demo_reset_retired",
                "message": "Destructive demo reset is retired; governed history is preserved.",
            },
        )

    @application.post("/api/demo/tamper")
    def tamper_demo(
        request: Request,
        _user=Depends(require_roles("auditor")),
    ):
        verification = request.app.state.service.tamper_demo_ledger()
        if verification["valid"]:
            return verification
        try:
            incident = request.app.state.integrity_service.detect()
        except NoIntegrityViolation:
            return verification
        return {
            **verification,
            "integrity_incident_id": str(incident.integrity_incident_id),
            "recovery_status": incident.recovery_status,
        }

    @application.post("/api/demo/recover")
    def recover_demo(
        payload: IntegrityRecoveryRequest,
        request: Request,
        _user=Depends(require_roles("auditor")),
    ):
        try:
            return request.app.state.integrity_service.recover(
                payload.incident_id,
                payload.operator,
            )
        except IntegrityError as error:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "integrity_recovery_failed",
                    "message": str(error),
                },
            ) from error

    return application


app = create_app()
