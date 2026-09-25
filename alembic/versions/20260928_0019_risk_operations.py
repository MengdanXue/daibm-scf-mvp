"""Risk operations: versioned rules, alert center, task center and an admin role.

- ``risk_rules`` is append-only: every change is a new version with its
  threshold, severity, enabled flag, effective time, author and reason.
- ``risk_alerts`` are raised by rules over recorded business data, once per
  (rule, source record). Status follows OPEN -> ASSIGNED -> PROCESSING ->
  RESOLVED -> CLOSED (a reviewer may send RESOLVED back to PROCESSING). The
  database rejects illegal transitions, changes to an alert's identity and
  any status change without an audited ``risk_alert_events`` row.
- ``risk_tasks`` follow OPEN -> IN_PROGRESS -> COMPLETED (or CANCELLED) with
  the same audit guarantees in ``risk_task_events``; results can be uploaded
  as immutable ``risk_task_attachments``.
- ``users.role`` gains ``admin`` (views everything, configures rules).
"""

from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260928_0019"
down_revision: str | None = "20260927_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen copies of app.domain.risk_operations.
_ALERT_STATUSES = ("OPEN", "ASSIGNED", "PROCESSING", "RESOLVED", "CLOSED")
_ALERT_TRANSITIONS = (
    ("OPEN", "ASSIGNED"),
    ("ASSIGNED", "PROCESSING"),
    ("PROCESSING", "RESOLVED"),
    ("RESOLVED", "CLOSED"),
    ("RESOLVED", "PROCESSING"),
)
_TASK_STATUSES = ("OPEN", "IN_PROGRESS", "COMPLETED", "CANCELLED")
_TASK_TRANSITIONS = (
    ("OPEN", "IN_PROGRESS"),
    ("OPEN", "CANCELLED"),
    ("IN_PROGRESS", "COMPLETED"),
    ("IN_PROGRESS", "CANCELLED"),
)
_RISK_TYPES = ("MODEL_SCORE", "OVERDUE", "REPAYMENT_ANOMALY", "LIFECYCLE", "DATA_QUALITY")
_SEVERITIES = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
_TASK_TYPES = ("INVESTIGATION", "COLLECTION", "DISPOSAL_REVIEW", "DATA_FIX", "OTHER")
_DEFAULT_RULES = (
    ("MODEL_SCORE_THRESHOLD", "MODEL_SCORE", "0.6000", "HIGH", "风险评分达到或超过阈值"),
    ("OVERDUE_DAYS", "OVERDUE", "30.0000", "HIGH", "逾期事件；逾期天数达到阈值升级为高风险"),
    ("REPAYMENT_ANOMALY", "REPAYMENT_ANOMALY", "2.0000", "MEDIUM", "被拒绝的还款达到阈值次数"),
    ("LIFECYCLE_ANOMALY", "LIFECYCLE", None, "CRITICAL", "进入违约或风险处置"),
    ("DATA_QUALITY", "DATA_QUALITY", None, "LOW", "业务结果因数据质量被审核拒绝"),
)
_OLD_ROLES = ("supplier", "core_enterprise", "financier", "risk_manager", "auditor")
_LEDGER_TYPES = (
    "RISK_ALERT_CREATED",
    "RISK_ALERT_TRANSITIONED",
    "RISK_TASK_RECORDED",
    "RISK_RULE_VERSIONED",
)
_ALERT_ACTIONS = (
    "CREATED", "ASSIGNED", "REASSIGNED", "STARTED", "RESOLVED", "CLOSED", "REOPENED", "COMMENTED",
)
_TASK_ACTIONS = (
    "CREATED", "REASSIGNED", "DUE_CHANGED", "NOTE_ADDED", "STARTED", "RESULT_ATTACHED",
    "COMPLETED", "CANCELLED",
)


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN (" + ", ".join(f"'{value}'" for value in values) + ")"


def _ledger_check(bind) -> str:
    return bind.scalar(
        sa.text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conname = 'ck_ledger_events_event_type'"
        )
    )


def _uuid(name: str, target: str | None = None, *, nullable: bool = True) -> sa.Column:
    args = [sa.ForeignKey(target, ondelete="RESTRICT")] if target else []
    return sa.Column(name, postgresql.UUID(as_uuid=True), *args, nullable=nullable)


def _status_guard(table: str, key: str, transitions, immutable: Sequence[str]) -> None:
    allowed = " OR ".join(
        f"(OLD.status = '{a}' AND NEW.status = '{b}')" for a, b in transitions
    )
    changed = " OR ".join(f"NEW.{column} IS DISTINCT FROM OLD.{column}" for column in immutable)
    op.execute(
        f"""
        CREATE FUNCTION guard_{table}() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.status IS DISTINCT FROM OLD.status AND NOT ({allowed}) THEN
                RAISE EXCEPTION 'illegal {table} transition % -> %', OLD.status, NEW.status
                    USING ERRCODE = '23514';
            END IF;
            IF {changed} THEN
                RAISE EXCEPTION '{table} identity is immutable'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            IF NEW.version <> OLD.version + 1 THEN
                RAISE EXCEPTION '{table} version must advance by one'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END $$
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION require_{table}_audit() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM {table.rstrip('s')}_events e
                           WHERE e.{key} = NEW.{key} AND e.resulting_version = NEW.version
                             AND e.to_status = NEW.status) THEN
                RAISE EXCEPTION '{table} % version % has no audit event', NEW.{key}, NEW.version
                    USING ERRCODE = '23514';
            END IF;
            RETURN NULL;
        END $$
        """
    )
    op.execute(
        f"CREATE TRIGGER trg_{table}_guard BEFORE UPDATE ON {table} "
        f"FOR EACH ROW EXECUTE FUNCTION guard_{table}()"
    )
    op.execute(
        f"CREATE CONSTRAINT TRIGGER trg_{table}_audit AFTER INSERT OR UPDATE ON {table} "
        f"DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION require_{table}_audit()"
    )
    op.execute(
        f"CREATE TRIGGER trg_{table}_no_delete BEFORE DELETE ON {table} "
        "FOR EACH ROW EXECUTE FUNCTION reject_governed_history_mutation()"
    )


def _events_table(name: str, key: str, parent: str, statuses, actions) -> None:
    op.create_table(
        name,
        sa.Column("event_id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        _uuid(key, f"{parent}.{key}", nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("from_status", sa.Text()),
        sa.Column("to_status", sa.Text(), nullable=False),
        sa.Column("resulting_version", sa.Integer(), nullable=False),
        _uuid("actor_user_id", "users.user_id"),
        sa.Column("actor_role", sa.Text(), nullable=False),
        sa.Column("comment", sa.Text()),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.CheckConstraint(_in("action", actions), name=f"ck_{name}_action"),
        sa.CheckConstraint(_in("to_status", statuses), name=f"ck_{name}_to_status"),
        sa.CheckConstraint(
            "from_status IS NULL OR " + _in("from_status", statuses), name=f"ck_{name}_from_status"
        ),
        sa.UniqueConstraint(key, "resulting_version", name=f"uq_{name}_version"),
    )
    op.create_index(f"ix_{name}_actor_user_id", name, ["actor_user_id"])
    op.create_index(f"ix_{name}_recorded_at", name, ["recorded_at"])
    op.execute(
        f"CREATE TRIGGER trg_{name}_immutable BEFORE UPDATE OR DELETE ON {name} "
        "FOR EACH ROW EXECUTE FUNCTION reject_governed_history_mutation()"
    )


def upgrade() -> None:
    bind = op.get_bind()

    # --- Admin role -------------------------------------------------------------
    op.drop_constraint("ck_users_role", "users", type_="check")
    op.create_check_constraint("ck_users_role", "users", _in("role", _OLD_ROLES + ("admin",)))

    previous_ledger = _ledger_check(bind)
    assert "'ACTUAL_OUTCOME_REVIEWED'" in previous_ledger
    op.drop_constraint("ck_ledger_events_event_type", "ledger_events", type_="check")
    op.execute(
        "ALTER TABLE ledger_events ADD CONSTRAINT ck_ledger_events_event_type "
        + previous_ledger.replace(
            "'ACTUAL_OUTCOME_REVIEWED'::text",
            "'ACTUAL_OUTCOME_REVIEWED'::text, "
            + ", ".join(f"'{name}'::text" for name in _LEDGER_TYPES),
        )
    )

    # --- Rules (append-only versions) ----------------------------------------------
    op.create_table(
        "risk_rules",
        sa.Column("rule_key", sa.Text(), primary_key=True),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("risk_type", sa.Text(), nullable=False),
        sa.Column("threshold", sa.Numeric(12, 4)),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        _uuid("created_by_user_id", "users.user_id"),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("change_reason", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.CheckConstraint(_in("risk_type", _RISK_TYPES), name="ck_risk_rules_risk_type"),
        sa.CheckConstraint(_in("severity", _SEVERITIES), name="ck_risk_rules_severity"),
        sa.CheckConstraint("version >= 1", name="ck_risk_rules_version"),
        sa.CheckConstraint("threshold IS NULL OR threshold >= 0", name="ck_risk_rules_threshold"),
    )
    op.create_index("ix_risk_rules_created_by_user_id", "risk_rules", ["created_by_user_id"])
    op.execute(
        "CREATE TRIGGER trg_risk_rules_immutable BEFORE UPDATE OR DELETE ON risk_rules "
        "FOR EACH ROW EXECUTE FUNCTION reject_governed_history_mutation()"
    )
    for key, risk_type, threshold, severity, description in _DEFAULT_RULES:
        bind.execute(
            sa.text(
                "INSERT INTO risk_rules (rule_key, version, risk_type, threshold, severity, "
                "enabled, description, effective_from, created_by, change_reason) VALUES "
                "(:key, 1, :type, :threshold, :severity, true, :description, "
                "'2000-01-01T00:00:00+00:00', 'migration_seed', 'initial_rule_set')"
            ),
            {
                "key": key,
                "type": risk_type,
                "threshold": threshold,
                "severity": severity,
                "description": description,
            },
        )

    # --- Alerts ---------------------------------------------------------------------
    op.create_table(
        "risk_alerts",
        _uuid("alert_id", nullable=False),
        sa.Column("rule_key", sa.Text(), nullable=False),
        sa.Column("rule_version", sa.Integer(), nullable=False),
        sa.Column("risk_type", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("source_ref", sa.Text(), nullable=False),
        _uuid("facility_id", "financing_facilities.facility_id"),
        _uuid("request_id", "financing_requests.request_id"),
        _uuid("organization_id", "organizations.organization_id"),
        sa.Column("trigger_reason", sa.Text(), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        _uuid("owner_user_id", "users.user_id"),
        sa.Column("resolution", sa.Text()),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("alert_id"),
        sa.ForeignKeyConstraint(
            ["rule_key", "rule_version"],
            ["risk_rules.rule_key", "risk_rules.version"],
            name="fk_risk_alerts_rule",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("rule_key", "source_ref", name="uq_risk_alerts_source"),
        sa.CheckConstraint(_in("status", _ALERT_STATUSES), name="ck_risk_alerts_status"),
        sa.CheckConstraint(_in("risk_type", _RISK_TYPES), name="ck_risk_alerts_risk_type"),
        sa.CheckConstraint(_in("severity", _SEVERITIES), name="ck_risk_alerts_severity"),
        sa.CheckConstraint(
            "facility_id IS NOT NULL OR request_id IS NOT NULL", name="ck_risk_alerts_subject"
        ),
        sa.CheckConstraint(
            "(status = 'OPEN') = (owner_user_id IS NULL)", name="ck_risk_alerts_owner"
        ),
        sa.CheckConstraint(
            "(status IN ('RESOLVED', 'CLOSED')) = (resolution IS NOT NULL AND resolved_at IS NOT NULL)",
            name="ck_risk_alerts_resolution",
        ),
        sa.CheckConstraint(
            "(status = 'CLOSED') = (closed_at IS NOT NULL)", name="ck_risk_alerts_closed"
        ),
        sa.CheckConstraint("version >= 1", name="ck_risk_alerts_version"),
    )
    for column in ("facility_id", "request_id", "organization_id", "owner_user_id"):
        op.create_index(f"ix_risk_alerts_{column}", "risk_alerts", [column])
    op.create_index("ix_risk_alerts_status_created_at", "risk_alerts", ["status", "created_at"])
    _events_table("risk_alert_events", "alert_id", "risk_alerts", _ALERT_STATUSES, _ALERT_ACTIONS)
    _status_guard(
        "risk_alerts",
        "alert_id",
        _ALERT_TRANSITIONS,
        ("rule_key", "rule_version", "risk_type", "severity", "source_type", "source_ref",
         "facility_id", "request_id", "organization_id", "trigger_reason", "evidence",
         "created_at"),
    )

    # --- Tasks ------------------------------------------------------------------------
    op.create_table(
        "risk_tasks",
        _uuid("task_id", nullable=False),
        _uuid("alert_id", "risk_alerts.alert_id"),
        _uuid("facility_id", "financing_facilities.facility_id"),
        _uuid("organization_id", "organizations.organization_id"),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("task_type", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        _uuid("assignee_user_id", "users.user_id", nullable=False),
        _uuid("created_by_user_id", "users.user_id", nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("result_summary", sa.Text()),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("task_id"),
        sa.CheckConstraint(_in("status", _TASK_STATUSES), name="ck_risk_tasks_status"),
        sa.CheckConstraint(_in("task_type", _TASK_TYPES), name="ck_risk_tasks_type"),
        sa.CheckConstraint(
            "(status = 'COMPLETED') = (completed_at IS NOT NULL AND result_summary IS NOT NULL)",
            name="ck_risk_tasks_completion",
        ),
        sa.CheckConstraint("version >= 1", name="ck_risk_tasks_version"),
    )
    for column in ("alert_id", "facility_id", "organization_id", "assignee_user_id",
                   "created_by_user_id"):
        op.create_index(f"ix_risk_tasks_{column}", "risk_tasks", [column])
    op.create_index("ix_risk_tasks_status_due_at", "risk_tasks", ["status", "due_at"])
    _events_table("risk_task_events", "task_id", "risk_tasks", _TASK_STATUSES, _TASK_ACTIONS)
    _status_guard(
        "risk_tasks",
        "task_id",
        _TASK_TRANSITIONS,
        ("alert_id", "facility_id", "organization_id", "task_type", "created_by_user_id",
         "created_at"),
    )
    op.create_table(
        "risk_task_attachments",
        _uuid("attachment_id", nullable=False),
        _uuid("task_id", "risk_tasks.task_id", nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        _uuid("uploaded_by_user_id", "users.user_id", nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("attachment_id"),
        sa.CheckConstraint(
            "size_bytes BETWEEN 1 AND 2097152 AND size_bytes = octet_length(content)",
            name="ck_risk_task_attachments_size",
        ),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="ck_risk_task_attachments_sha256"),
    )
    op.create_index("ix_risk_task_attachments_task_id", "risk_task_attachments", ["task_id"])
    op.create_index(
        "ix_risk_task_attachments_uploaded_by_user_id",
        "risk_task_attachments",
        ["uploaded_by_user_id"],
    )
    op.execute(
        "CREATE TRIGGER trg_risk_task_attachments_immutable BEFORE UPDATE OR DELETE "
        "ON risk_task_attachments FOR EACH ROW EXECUTE FUNCTION reject_governed_history_mutation()"
    )


def downgrade() -> None:
    bind = op.get_bind()
    blocked = bind.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM risk_alerts) OR EXISTS (SELECT 1 FROM risk_tasks) "
            "OR EXISTS (SELECT 1 FROM risk_rules WHERE version > 1) "
            "OR EXISTS (SELECT 1 FROM users WHERE role = 'admin')"
        )
    )
    if blocked:
        raise RuntimeError(
            "Cannot downgrade while risk operations history from revision 20260928_0019 exists"
        )
    op.execute("DROP TRIGGER trg_risk_task_attachments_immutable ON risk_task_attachments")
    op.drop_table("risk_task_attachments")
    for table, events in (("risk_tasks", "risk_task_events"), ("risk_alerts", "risk_alert_events")):
        for trigger in ("guard", "audit", "no_delete"):
            op.execute(f"DROP TRIGGER trg_{table}_{trigger} ON {table}")
        op.execute(f"DROP TRIGGER trg_{events}_immutable ON {events}")
        op.drop_table(events)
        op.drop_table(table)
        op.execute(f"DROP FUNCTION guard_{table}()")
        op.execute(f"DROP FUNCTION require_{table}_audit()")
    op.execute("DROP TRIGGER trg_risk_rules_immutable ON risk_rules")
    op.drop_table("risk_rules")
    current = _ledger_check(bind)
    op.drop_constraint("ck_ledger_events_event_type", "ledger_events", type_="check")
    op.execute(
        "ALTER TABLE ledger_events ADD CONSTRAINT ck_ledger_events_event_type "
        + current.replace(
            ", " + ", ".join(f"'{name}'::text" for name in _LEDGER_TYPES), ""
        )
    )
    op.drop_constraint("ck_users_role", "users", type_="check")
    op.create_check_constraint("ck_users_role", "users", _in("role", _OLD_ROLES))
