"""Security settings, password policy and the security event log.

Nothing secret lives in code: the demo password, database password and metrics
token come from the environment (see ``DEPLOYMENT_GUIDE.md``). Runtime limits
(lock-out, session idle time) start from the environment and can be changed,
versioned and rolled back in the configuration center.
"""

from __future__ import annotations

import os
import re
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.models_enterprise import SecurityEventModel


class WeakPassword(ValueError):
    pass


PASSWORD_RULES = (
    (re.compile(r".{12,}"), "at least 12 characters"),
    (re.compile(r"[a-z]"), "a lower-case letter"),
    (re.compile(r"[A-Z]"), "an upper-case letter"),
    (re.compile(r"[0-9]"), "a digit"),
    (re.compile(r"[^A-Za-z0-9]"), "a symbol"),
)


def check_password_policy(password: str, *, username: str | None = None) -> None:
    missing = [label for pattern, label in PASSWORD_RULES if not pattern.search(password)]
    if username and username.split(".")[0].lower() in password.lower():
        missing.append("no part of the username")
    if missing:
        raise WeakPassword("Password needs " + ", ".join(missing))


def _int(values: Mapping[str, str], name: str, default: int, minimum: int) -> int:
    raw = values.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


@dataclass(frozen=True)
class SecuritySettings:
    login_max_failures: int = 5
    lockout_minutes: int = 15
    session_idle_minutes: int = 30
    session_absolute_hours: int = 12
    cookie_secure: bool = False
    cookie_samesite: str = "strict"

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "SecuritySettings":
        values = os.environ if environ is None else environ
        secure = values.get("DAIBM_COOKIE_SECURE", "false").strip().lower()
        if secure not in {"true", "false"}:
            raise ValueError("DAIBM_COOKIE_SECURE must be true or false")
        samesite = values.get("DAIBM_COOKIE_SAMESITE", "strict").strip().lower()
        if samesite not in {"strict", "lax"}:
            raise ValueError("DAIBM_COOKIE_SAMESITE must be strict or lax")
        return cls(
            login_max_failures=_int(values, "DAIBM_LOGIN_MAX_FAILURES", 5, 1),
            lockout_minutes=_int(values, "DAIBM_LOCKOUT_MINUTES", 15, 1),
            session_idle_minutes=_int(values, "DAIBM_SESSION_IDLE_MINUTES", 30, 1),
            session_absolute_hours=_int(values, "DAIBM_SESSION_ABSOLUTE_HOURS", 12, 1),
            cookie_secure=secure == "true",
            cookie_samesite=samesite,
        )

    def with_overrides(self, overrides: Mapping[str, Any]) -> "SecuritySettings":
        fields = {
            "security.login_max_failures": "login_max_failures",
            "security.lockout_minutes": "lockout_minutes",
            "security.session_idle_minutes": "session_idle_minutes",
            "security.session_absolute_hours": "session_absolute_hours",
        }
        values = {
            attribute: int(overrides[key]) for key, attribute in fields.items() if key in overrides
        }
        return SecuritySettings(**{**self.__dict__, **values})


SettingsProvider = Callable[[], SecuritySettings]


def demo_password_from_env(environ: Mapping[str, str] | None = None) -> str | None:
    """The demo accounts' password, only ever from ``DAIBM_DEMO_PASSWORD``."""

    values = os.environ if environ is None else environ
    password = values.get("DAIBM_DEMO_PASSWORD", "").strip()
    if not password:
        return None
    if values.get("DAIBM_ENV", "development").strip().lower() == "production":
        check_password_policy(password)
    elif len(password) < 8:
        raise WeakPassword("DAIBM_DEMO_PASSWORD needs at least 8 characters")
    return password


def record_security_event(
    session: Session,
    event_type: str,
    action: str,
    *,
    user_id: uuid.UUID | None = None,
    username: str | None = None,
    organization_id: uuid.UUID | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    detail: dict[str, Any] | None = None,
    client_ip: str | None = None,
) -> None:
    session.add(
        SecurityEventModel(
            event_type=event_type,
            action=action,
            user_id=user_id,
            username=username,
            organization_id=organization_id,
            resource_type=resource_type,
            resource_id=resource_id,
            detail=detail or {},
            client_ip=client_ip,
        )
    )


__all__ = [
    "SecuritySettings",
    "WeakPassword",
    "check_password_policy",
    "demo_password_from_env",
    "record_security_event",
]
