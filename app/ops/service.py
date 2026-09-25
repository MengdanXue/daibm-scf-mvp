"""Health and metrics from the running process and the database."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.models_facility import FinancingFacilityModel
from app.models_identity import OrganizationModel, UserModel, UserSessionModel
from app.models_risk_ops import RiskAlertModel, RiskTaskModel
from app.ops.metrics import Heartbeats, RequestMetrics

ROOT = Path(__file__).resolve().parents[2]


def _head_revision() -> str | None:
    try:
        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(ROOT / "alembic"))
        return ScriptDirectory.from_config(config).get_current_head()
    except Exception:
        return None


class OpsService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        metrics: RequestMetrics,
        heartbeats: Heartbeats,
        monitor_interval: Callable[[], float],
    ) -> None:
        self.session_factory = session_factory
        self.metrics = metrics
        self.heartbeats = heartbeats
        self.monitor_interval = monitor_interval
        self.head_revision = _head_revision()

    def health(self) -> dict[str, Any]:
        started = time.perf_counter()
        database: dict[str, Any]
        try:
            with self.session_factory() as session:
                session.execute(text("SELECT 1"))
                revision = session.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            database = {
                "ok": revision == self.head_revision,
                "latency_ms": round(1000 * (time.perf_counter() - started), 2),
                "revision": revision,
                "expected_revision": self.head_revision,
            }
        except Exception as error:
            database = {"ok": False, "error": type(error).__name__}
        workers = {
            "calibration_worker": self.heartbeats.status("calibration_worker", stale_after=60),
            "risk_monitor": self.heartbeats.status(
                "risk_monitor", stale_after=3 * self.monitor_interval() + 30
            ),
        }
        workers_ok = all(item["alive"] for item in workers.values() if item["enabled"])
        status = "ok" if database["ok"] and workers_ok else "degraded" if database["ok"] else "down"
        return {
            "status": status,
            "application": {"ok": True, "uptime_seconds": self.metrics.snapshot()["uptime_seconds"]},
            "database": database,
            "workers": workers,
        }

    def business_metrics(self) -> dict[str, Any]:
        with self.session_factory() as session:
            def grouped(model, column) -> dict[str, int]:
                return {
                    str(key): int(count)
                    for key, count in session.execute(select(column, func.count()).group_by(column))
                }

            return {
                "facilities_by_status": grouped(FinancingFacilityModel, FinancingFacilityModel.status),
                "alerts_by_status": grouped(RiskAlertModel, RiskAlertModel.status),
                "tasks_by_status": grouped(RiskTaskModel, RiskTaskModel.status),
                "organizations": int(session.scalar(select(func.count()).select_from(OrganizationModel)) or 0),
                "users": int(session.scalar(select(func.count()).select_from(UserModel)) or 0),
                "active_sessions": int(session.scalar(select(func.count()).select_from(UserSessionModel)) or 0),
            }

    def metrics_snapshot(self) -> dict[str, Any]:
        health = self.health()
        return {
            "business": self.business_metrics(),
            "system": {**self.metrics.snapshot(), "workers": health["workers"], "status": health["status"]},
        }

    def prometheus(self) -> str:
        snapshot = self.metrics_snapshot()
        lines = [
            "# TYPE daibm_requests_total counter",
            f"daibm_requests_total {snapshot['system']['request_count']}",
            "# TYPE daibm_request_errors_total counter",
            f"daibm_request_errors_total {snapshot['system']['error_count']}",
        ]
        for name, key in (
            ("daibm_facilities", "facilities_by_status"),
            ("daibm_alerts", "alerts_by_status"),
            ("daibm_tasks", "tasks_by_status"),
        ):
            lines.append(f"# TYPE {name} gauge")
            lines.extend(
                f'{name}{{status="{status}"}} {count}'
                for status, count in sorted(snapshot["business"][key].items())
            )
        lines.append("# TYPE daibm_worker_alive gauge")
        lines.extend(
            f'daibm_worker_alive{{worker="{worker}"}} {int(bool(state["alive"]))}'
            for worker, state in snapshot["system"]["workers"].items()
        )
        return "\n".join(lines) + "\n"


__all__ = ["OpsService"]
