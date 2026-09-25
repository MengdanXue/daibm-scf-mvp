"""Lightweight configuration center: versioned, audited, reversible system parameters.

Values start from their environment defaults; every change is a new immutable
``system_config`` version with author and reason, a ledger entry and a security
event. Rolling back writes a *new* version carrying an old value, so history
is never rewritten. Risk thresholds and alert rules live in the versioned
``risk_rules`` (Phase 3) and are managed from the same page.
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.identity import AuthenticatedUser
from app.ledger import canonical_timestamp
from app.models_enterprise import SystemConfigModel
from app.repositories.ledger import LedgerRepository
from app.services.organizations import record_admin_action
from app.services.permissions import PermissionService


@dataclass(frozen=True)
class ConfigDefinition:
    kind: str  # "int" or "bool"
    env: str
    default: Any
    minimum: int | None = None
    maximum: int | None = None
    description: str = ""


DEFINITIONS: dict[str, ConfigDefinition] = {
    "security.login_max_failures": ConfigDefinition(
        "int", "DAIBM_LOGIN_MAX_FAILURES", 5, 1, 20, "连续登录失败多少次后锁定账号"
    ),
    "security.lockout_minutes": ConfigDefinition(
        "int", "DAIBM_LOCKOUT_MINUTES", 15, 1, 1440, "账号锁定时长（分钟）"
    ),
    "security.session_idle_minutes": ConfigDefinition(
        "int", "DAIBM_SESSION_IDLE_MINUTES", 30, 1, 1440, "会话空闲超时（分钟）"
    ),
    "security.session_absolute_hours": ConfigDefinition(
        "int", "DAIBM_SESSION_ABSOLUTE_HOURS", 12, 1, 72, "会话最长有效期（小时）"
    ),
    "risk.monitor_interval_seconds": ConfigDefinition(
        "int", "RISK_MONITOR_INTERVAL_SECONDS", 60, 5, 3600, "风险规则周期检测间隔（秒）"
    ),
    "outcome.manual_review": ConfigDefinition(
        "bool", "OUTCOME_MANUAL_REVIEW", False, description="业务结果是否需要人工审核后才可训练"
    ),
    "calibration.auto_promotion": ConfigDefinition(
        "bool", "CALIBRATION_AUTO_PROMOTION", True, description="通过评估的候选模型是否自动激活"
    ),
}


class ConfigError(Exception):
    pass


class ConfigNotFound(ConfigError):
    pass


class ConfigConflict(ConfigError):
    pass


def _env_default(definition: ConfigDefinition) -> Any:
    raw = os.environ.get(definition.env)
    if raw is None or raw == "":
        return definition.default
    if definition.kind == "bool":
        return raw.strip().lower() == "true"
    return int(raw)


def validate(key: str, value: Any) -> Any:
    definition = DEFINITIONS.get(key)
    if definition is None:
        raise ConfigNotFound(key)
    if definition.kind == "bool":
        if not isinstance(value, bool):
            raise ConfigConflict(f"{key} must be true or false")
        return value
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigConflict(f"{key} must be an integer")
    if definition.minimum is not None and value < definition.minimum:
        raise ConfigConflict(f"{key} must be at least {definition.minimum}")
    if definition.maximum is not None and value > definition.maximum:
        raise ConfigConflict(f"{key} must be at most {definition.maximum}")
    return value


class ConfigService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        ledger_repository: LedgerRepository | None = None,
        cache_seconds: float = 5.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.session_factory = session_factory
        self.ledger = ledger_repository or LedgerRepository()
        self.cache_seconds = cache_seconds
        self.monotonic = monotonic
        self._lock = threading.Lock()
        self._cache: tuple[float, dict[str, Any]] | None = None

    # --- Runtime reads ---------------------------------------------------------

    def values(self) -> dict[str, Any]:
        """Effective values (latest version, else environment default), briefly cached."""

        with self._lock:
            if self._cache and self.monotonic() - self._cache[0] < self.cache_seconds:
                return dict(self._cache[1])
        try:
            with self.session_factory() as session:
                latest = self._latest(session)
        except Exception:
            # Configuration must never take the application down.
            latest = {}
        effective = {
            key: (latest[key].value if key in latest else _env_default(definition))
            for key, definition in DEFINITIONS.items()
        }
        with self._lock:
            self._cache = (self.monotonic(), effective)
        return dict(effective)

    def get(self, key: str) -> Any:
        return self.values()[key]

    def invalidate(self) -> None:
        with self._lock:
            self._cache = None

    # --- Administration ----------------------------------------------------------

    def list_config(self, user: AuthenticatedUser) -> dict[str, Any]:
        PermissionService.require(user, "config:read")
        with self.session_factory() as session:
            latest = self._latest(session)
            history = list(
                session.scalars(
                    select(SystemConfigModel).order_by(
                        SystemConfigModel.config_key, SystemConfigModel.version.desc()
                    )
                )
            )
        return {
            "items": [
                {
                    "key": key,
                    "kind": definition.kind,
                    "description": definition.description,
                    "minimum": definition.minimum,
                    "maximum": definition.maximum,
                    "value": latest[key].value if key in latest else _env_default(definition),
                    "version": latest[key].version if key in latest else 0,
                    "source": "config_center" if key in latest else f"env:{definition.env}",
                    "updated_by": latest[key].created_by if key in latest else None,
                    "updated_at": canonical_timestamp(latest[key].created_at) if key in latest else None,
                }
                for key, definition in DEFINITIONS.items()
            ],
            "history": [self._serialize(item) for item in history],
        }

    def set_value(
        self, user: AuthenticatedUser, key: str, *, value: Any, reason: str, expected_version: int
    ) -> dict[str, Any]:
        PermissionService.require(user, "config:write")
        value = validate(key, value)
        return self._write(user, key, value=value, reason=reason, expected_version=expected_version)

    def rollback(
        self, user: AuthenticatedUser, key: str, *, to_version: int, reason: str, expected_version: int
    ) -> dict[str, Any]:
        PermissionService.require(user, "config:write")
        with self.session_factory() as session:
            target = session.get(SystemConfigModel, (key, to_version))
            if target is None:
                raise ConfigNotFound(f"{key} v{to_version}")
            value = target.value
        return self._write(
            user, key, value=value, reason=reason, expected_version=expected_version,
            rollback_of=to_version,
        )

    def _write(
        self,
        user: AuthenticatedUser,
        key: str,
        *,
        value: Any,
        reason: str,
        expected_version: int,
        rollback_of: int | None = None,
    ) -> dict[str, Any]:
        if key not in DEFINITIONS:
            raise ConfigNotFound(key)
        with self.session_factory.begin() as session:
            session.execute(select(func.pg_advisory_xact_lock(func.hashtext(f"config:{key}"))))
            current = session.scalar(
                select(func.max(SystemConfigModel.version)).where(SystemConfigModel.config_key == key)
            ) or 0
            if current != expected_version:
                raise ConfigConflict("Configuration changed since it was read; refresh and retry")
            previous = session.get(SystemConfigModel, (key, current)) if current else None
            row = SystemConfigModel(
                config_key=key,
                version=current + 1,
                value=value,
                created_by_user_id=user.user_id,
                created_by=user.username,
                change_reason=reason,
                rollback_of_version=rollback_of,
            )
            session.add(row)
            session.flush()
            detail = {
                "key": key,
                "version": row.version,
                "value": value,
                "previous_value": previous.value if previous else _env_default(DEFINITIONS[key]),
                "reason": reason,
                "rollback_of_version": rollback_of,
            }
            record_admin_action(
                session, self.ledger, user, "config_rolled_back" if rollback_of else "config_changed",
                resource_type="config", resource_id=key, detail=detail,
            )
            self.ledger.append_many(
                session,
                uuid.uuid5(uuid.NAMESPACE_URL, f"daibm-scf:config:{key}"),
                [("CONFIG_VERSIONED", detail)],
            )
            result = self._serialize(row)
        self.invalidate()
        return result

    @staticmethod
    def _latest(session: Session) -> dict[str, SystemConfigModel]:
        latest = (
            select(SystemConfigModel.config_key, func.max(SystemConfigModel.version).label("version"))
            .group_by(SystemConfigModel.config_key)
            .subquery()
        )
        return {
            row.config_key: row
            for row in session.scalars(
                select(SystemConfigModel).join(
                    latest,
                    (latest.c.config_key == SystemConfigModel.config_key)
                    & (latest.c.version == SystemConfigModel.version),
                )
            )
        }

    @staticmethod
    def _serialize(row: SystemConfigModel) -> dict[str, Any]:
        return {
            "key": row.config_key,
            "version": row.version,
            "value": row.value,
            "created_by": row.created_by,
            "change_reason": row.change_reason,
            "rollback_of_version": row.rollback_of_version,
            "created_at": canonical_timestamp(row.created_at),
        }


__all__ = ["ConfigConflict", "ConfigNotFound", "ConfigService", "DEFINITIONS", "validate"]
