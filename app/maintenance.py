"""Explicit no-reconciliation startup for the original 8010 maintenance window.

The container command must invoke ``uvicorn app.maintenance:app`` directly,
overriding the image's normal Alembic-and-Uvicorn CMD. This entrypoint does
not run migrations, seed identities, reconcile deployments, or start the
calibration worker. It requires an already registered reference artifact.
"""

from fastapi import FastAPI

from app.config import ResearchSettings
from app.database import Database
from app.main import create_app


def create_maintenance_app(
    database: Database | None = None,
    *,
    research_settings: ResearchSettings | None = None,
) -> FastAPI:
    return create_app(
        database,
        research_settings=research_settings,
        calibration_worker_enabled=False,
        maintenance_mode=True,
    )


app = create_maintenance_app()
