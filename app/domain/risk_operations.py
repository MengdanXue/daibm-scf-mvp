"""Risk operations: alert and task lifecycles, rules, roles and risk classes."""

from __future__ import annotations

from enum import StrEnum


class AlertStatus(StrEnum):
    OPEN = "OPEN"
    ASSIGNED = "ASSIGNED"
    PROCESSING = "PROCESSING"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"


A = AlertStatus

# Frozen in migration 20260928_0019; tests pin the equality.
ALERT_TRANSITIONS: frozenset[tuple[AlertStatus, AlertStatus]] = frozenset(
    {
        (A.OPEN, A.ASSIGNED),
        (A.ASSIGNED, A.PROCESSING),
        (A.PROCESSING, A.RESOLVED),
        (A.RESOLVED, A.CLOSED),
        # The reviewer sends an unconvincing resolution back.
        (A.RESOLVED, A.PROCESSING),
    }
)


class TaskStatus(StrEnum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


T = TaskStatus

TASK_TRANSITIONS: frozenset[tuple[TaskStatus, TaskStatus]] = frozenset(
    {
        (T.OPEN, T.IN_PROGRESS),
        (T.OPEN, T.CANCELLED),
        (T.IN_PROGRESS, T.COMPLETED),
        (T.IN_PROGRESS, T.CANCELLED),
    }
)

RISK_TYPES = ("MODEL_SCORE", "OVERDUE", "REPAYMENT_ANOMALY", "LIFECYCLE", "DATA_QUALITY")
SEVERITIES = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
TASK_TYPES = ("INVESTIGATION", "COLLECTION", "DISPOSAL_REVIEW", "DATA_FIX", "OTHER")

# Rules seeded as version 1 by the migration. The rule keys are stable.
DEFAULT_RULES = (
    # key, risk type, threshold, severity, description
    ("MODEL_SCORE_THRESHOLD", "MODEL_SCORE", "0.6000", "HIGH", "风险评分达到或超过阈值"),
    ("OVERDUE_DAYS", "OVERDUE", "30.0000", "HIGH", "逾期事件；逾期天数达到阈值升级为高风险"),
    ("REPAYMENT_ANOMALY", "REPAYMENT_ANOMALY", "2.0000", "MEDIUM", "被拒绝的还款达到阈值次数"),
    ("LIFECYCLE_ANOMALY", "LIFECYCLE", None, "CRITICAL", "进入违约或风险处置"),
    ("DATA_QUALITY", "DATA_QUALITY", None, "LOW", "业务结果因数据质量被审核拒绝"),
)

ADMIN = "admin"
RISK_MANAGER = "risk_manager"
AUDITOR = "auditor"
FINANCIER = "financier"
ENTERPRISE_ROLES = frozenset({"supplier", "core_enterprise"})
# Roles that see every organization's risk data.
GLOBAL_ROLES = frozenset({ADMIN, AUDITOR})

DASHBOARD_ROLES = frozenset({ADMIN, RISK_MANAGER, AUDITOR, FINANCIER})
ALERT_READ_ROLES = frozenset({ADMIN, RISK_MANAGER, AUDITOR})
ALERT_ASSIGN_ROLES = frozenset({ADMIN, RISK_MANAGER})
ALERT_REVIEW_ROLES = frozenset({ADMIN, AUDITOR})
TASK_ROLES = frozenset({ADMIN, RISK_MANAGER, AUDITOR})
RULE_READ_ROLES = frozenset({ADMIN, RISK_MANAGER, AUDITOR})
RULE_WRITE_ROLES = frozenset({ADMIN})
SCAN_ROLES = frozenset({ADMIN, RISK_MANAGER})

# Portfolio risk classes shown on the dashboard.
NORMAL, WATCH, HIGH_RISK, DEFAULTED = "NORMAL", "WATCH", "HIGH_RISK", "DEFAULTED"
_DEFAULT_STATES = {"defaulted", "in_recovery", "recovered", "written_off"}


def risk_class(
    *,
    status: str,
    closure_reason: str | None,
    max_days_past_due: int,
    latest_band: str | None,
    overdue_threshold: int,
) -> str:
    """Deterministic class of one facility from its recorded lifecycle and scores."""

    if status in _DEFAULT_STATES or (status == "closed" and closure_reason in {"written_off", "settled_after_default"}):
        return DEFAULTED
    if status in {"repaid"} or (status == "closed" and closure_reason == "repaid"):
        return NORMAL
    if status == "in_disposal" or max_days_past_due >= overdue_threshold or latest_band == "high":
        return HIGH_RISK
    if status in {"overdue", "restructured"} or max_days_past_due > 0 or latest_band == "medium":
        return WATCH
    return NORMAL


__all__ = [
    "ALERT_TRANSITIONS",
    "AlertStatus",
    "DEFAULT_RULES",
    "RISK_TYPES",
    "SEVERITIES",
    "TASK_TRANSITIONS",
    "TASK_TYPES",
    "TaskStatus",
    "risk_class",
]
