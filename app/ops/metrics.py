"""In-process request metrics; no external collector required."""

from __future__ import annotations

import threading
import time
from collections import Counter
from typing import Any


class RequestMetrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.started_at = time.time()
        self.requests = 0
        self.errors = 0
        self.by_status: Counter[str] = Counter()
        self.by_route: Counter[str] = Counter()
        self.duration_seconds = 0.0

    def record(self, method: str, route: str, status: int, duration: float) -> None:
        with self._lock:
            self.requests += 1
            if status >= 500:
                self.errors += 1
            self.by_status[f"{status // 100}xx"] += 1
            self.by_route[f"{method} {route}"] += 1
            self.duration_seconds += duration

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "uptime_seconds": round(time.time() - self.started_at, 1),
                "request_count": self.requests,
                "error_count": self.errors,
                "by_status": dict(self.by_status),
                "top_routes": dict(self.by_route.most_common(15)),
                "mean_duration_ms": round(1000 * self.duration_seconds / self.requests, 2)
                if self.requests
                else 0.0,
            }


class Heartbeats:
    """Last-alive timestamps of in-process workers."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._beats: dict[str, float] = {}
        self._enabled: dict[str, bool] = {}

    def enable(self, name: str, enabled: bool) -> None:
        with self._lock:
            self._enabled[name] = enabled

    def beat(self, name: str) -> None:
        with self._lock:
            self._beats[name] = time.time()

    def status(self, name: str, *, stale_after: float) -> dict[str, Any]:
        with self._lock:
            enabled = self._enabled.get(name, False)
            last = self._beats.get(name)
        age = None if last is None else round(time.time() - last, 1)
        alive = enabled and age is not None and age <= stale_after
        return {"enabled": enabled, "last_heartbeat_age_seconds": age, "alive": alive}


__all__ = ["Heartbeats", "RequestMetrics"]
