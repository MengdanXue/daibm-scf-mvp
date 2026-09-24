"""Outcome data governance: audited review lifecycle and training dataset snapshots.

- ``outcome_review_events`` gains ``from_status``, ``actor_role`` and
  ``comment``. A BEFORE INSERT trigger fills from/role for every new event and
  rejects transitions outside the review state machine. Existing rows are not
  touched (their new columns stay NULL).
- ``review_outcome`` honours ``daibm.outcome_review_mode = 'manual'``: an
  outcome that passes the rules then waits in REVIEWING for a reviewer.
- ``training_dataset_snapshots`` / ``training_dataset_snapshot_items`` freeze
  what a calibration run was trained on: included outcomes (with correction
  heads) and excluded outcomes with their reason, plus a dataset hash. Both
  are immutable. Calibration runs and model versions reference the snapshot;
  the reference can be set once and never changed.
- Calibration jobs may be triggered by a manual review (``outcome_reviewed``);
  ledger events gain ``ACTUAL_OUTCOME_REVIEWED``.

Backfill only appends: each existing run with training membership gets a
``migration_backfill`` snapshot of exactly that membership (exclusions were not
recorded before this revision, so none are claimed).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260927_0018"
down_revision: str | None = "20260926_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen copy of ``app.domain.outcome_governance.REVIEW_TRANSITIONS``.
_REVIEW_TRANSITIONS = (
    (None, "CREATED"),
    ("CREATED", "REVIEWING"),
    ("CREATED", "REJECTED"),
    ("REVIEWING", "ELIGIBLE"),
    ("REVIEWING", "REJECTED"),
    ("ELIGIBLE", "TRAINING_USED"),
    ("ELIGIBLE", "REJECTED"),
    ("ELIGIBLE", "REVIEWING"),
    ("TRAINING_USED", "TRAINING_USED"),
    ("TRAINING_USED", "REJECTED"),
    ("TRAINING_USED", "REVIEWING"),
    ("REJECTED", "REVIEWING"),
    ("REJECTED", "REJECTED"),
)
_OLD_REJECTION_REASONS = (
    "SCOPE_MISMATCH",
    "DATA_QUALITY_INSUFFICIENT",
    "BUSINESS_INCONSISTENT",
    "BUSINESS_EXCEPTION",
    "MANUAL_CORRECTION",
    "SUPERSEDED_BY_CORRECTION",
)
_NEW_REJECTION_REASONS = _OLD_REJECTION_REASONS + ("QUALITY_ANOMALY",)
_OLD_TRIGGER_TYPES = ("outcome_submitted", "correction_exclude", "correction_reinstate")
_NEW_TRIGGER_TYPES = _OLD_TRIGGER_TYPES + ("outcome_reviewed",)
_JOB_CONTRACT = (
    "attempt_count BETWEEN 0 AND 3 AND "
    "((trigger_type IN ({outcome_types}) AND trigger_outcome_id IS NOT NULL "
    "AND trigger_correction_id IS NULL) OR "
    "(trigger_type IN ('correction_exclude', 'correction_reinstate') "
    "AND trigger_outcome_id IS NULL AND trigger_correction_id IS NOT NULL)) AND "
    "((status = 'queued' AND attempt_count BETWEEN 0 AND 2 "
    "AND lease_owner IS NULL AND leased_until IS NULL "
    "AND started_at IS NULL AND completed_at IS NULL "
    "AND failure_code IS NULL AND result_run_id IS NULL) OR "
    "(status = 'running' AND attempt_count BETWEEN 1 AND 3 "
    "AND lease_owner IS NOT NULL "
    "AND btrim(lease_owner) <> '' AND leased_until IS NOT NULL "
    "AND started_at IS NOT NULL AND leased_until > started_at "
    "AND completed_at IS NULL AND failure_code IS NULL "
    "AND result_run_id IS NULL) OR "
    "(status = 'completed' AND attempt_count BETWEEN 1 AND 3 "
    "AND lease_owner IS NULL "
    "AND leased_until IS NULL AND started_at IS NOT NULL "
    "AND completed_at IS NOT NULL AND completed_at >= started_at "
    "AND failure_code IS NULL AND result_run_id IS NOT NULL) OR "
    "(status = 'failed' AND attempt_count BETWEEN 1 AND 3 "
    "AND lease_owner IS NULL AND leased_until IS NULL "
    "AND started_at IS NOT NULL AND completed_at IS NOT NULL "
    "AND completed_at >= started_at "
    "AND failure_code IS NOT NULL "
    "AND failure_code ~ '^[a-z][a-z0-9_]{{2,63}}$' "
    "AND result_run_id IS NULL))"
)
_BACKFILL_POLICY = "migration_backfill_v1"

_OLD_REVIEW_OUTCOME = """
    CREATE OR REPLACE FUNCTION review_outcome(p_outcome_id uuid, p_actor uuid) RETURNS void
    LANGUAGE plpgsql AS $$
    DECLARE v_reason text := outcome_eligibility_reason(p_outcome_id);
    BEGIN
        INSERT INTO outcome_review_events (outcome_id, status, reason_code, actor_user_id)
        VALUES (p_outcome_id, 'REVIEWING', NULL, p_actor);
        INSERT INTO outcome_review_events (outcome_id, status, reason_code, actor_user_id)
        VALUES (p_outcome_id,
                CASE WHEN v_reason IS NULL THEN 'ELIGIBLE' ELSE 'REJECTED' END,
                v_reason, p_actor);
    END $$
"""


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN (" + ", ".join(f"'{value}'" for value in values) + ")"


def _ledger_check(bind) -> str:
    return bind.scalar(
        sa.text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conname = 'ck_ledger_events_event_type'"
        )
    )


def _replace_job_constraints(trigger_types: Sequence[str]) -> None:
    outcome_types = ("outcome_submitted",) + (
        ("outcome_reviewed",) if "outcome_reviewed" in trigger_types else ()
    )
    op.drop_constraint("ck_calibration_jobs_trigger_type", "calibration_jobs", type_="check")
    op.create_check_constraint(
        "ck_calibration_jobs_trigger_type", "calibration_jobs", _in("trigger_type", trigger_types)
    )
    op.drop_constraint("ck_calibration_jobs_contract", "calibration_jobs", type_="check")
    op.create_check_constraint(
        "ck_calibration_jobs_contract",
        "calibration_jobs",
        _JOB_CONTRACT.format(
            outcome_types=", ".join(f"'{value}'" for value in outcome_types)
        ),
    )


def _backfill_hash(scope: str, included: list[tuple[str, str | None]]) -> str:
    manifest = json.dumps(
        {
            "scope": scope,
            "policy": _BACKFILL_POLICY,
            "included": sorted([outcome_id, head] for outcome_id, head in included),
            "excluded": [],
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    return hashlib.sha256(manifest).hexdigest()


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "LOCK TABLE outcome_review_events, calibration_jobs, calibration_runs, "
            "risk_model_versions, ledger_events IN ACCESS EXCLUSIVE MODE"
        )
    )

    # --- Review lifecycle audit ------------------------------------------------
    op.add_column("outcome_review_events", sa.Column("from_status", sa.Text()))
    op.add_column("outcome_review_events", sa.Column("actor_role", sa.Text()))
    op.add_column("outcome_review_events", sa.Column("comment", sa.Text()))
    op.drop_constraint(
        "ck_outcome_review_events_rejection_reason", "outcome_review_events", type_="check"
    )
    op.create_check_constraint(
        "ck_outcome_review_events_rejection_reason",
        "outcome_review_events",
        "status <> 'REJECTED' OR " + _in("reason_code", _NEW_REJECTION_REASONS),
    )
    op.create_check_constraint(
        "ck_outcome_review_events_from_status",
        "outcome_review_events",
        "from_status IS NULL OR from_status IN "
        "('CREATED', 'REVIEWING', 'ELIGIBLE', 'TRAINING_USED', 'REJECTED')",
    )
    allowed = " OR ".join(
        f"(v_from IS NULL AND NEW.status = '{to}')"
        if source is None
        else f"(v_from = '{source}' AND NEW.status = '{to}')"
        for source, to in _REVIEW_TRANSITIONS
    )
    op.execute(
        f"""
        CREATE FUNCTION audit_outcome_review_event() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE v_from text;
        BEGIN
            SELECT status INTO v_from FROM outcome_review_events
            WHERE outcome_id = NEW.outcome_id ORDER BY event_id DESC LIMIT 1;
            IF NOT ({allowed}) THEN
                RAISE EXCEPTION 'illegal outcome review transition % -> %',
                    COALESCE(v_from, 'NONE'), NEW.status USING ERRCODE = '23514';
            END IF;
            NEW.from_status := v_from;
            NEW.actor_role := COALESCE(
                (SELECT role FROM users WHERE user_id = NEW.actor_user_id), 'system');
            RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_outcome_review_events_audit BEFORE INSERT ON outcome_review_events "
        "FOR EACH ROW EXECUTE FUNCTION audit_outcome_review_event()"
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION review_outcome(p_outcome_id uuid, p_actor uuid) RETURNS void
        LANGUAGE plpgsql AS $$
        DECLARE
            v_reason text := outcome_eligibility_reason(p_outcome_id);
            v_current text;
        BEGIN
            SELECT status INTO v_current FROM outcome_review_events
            WHERE outcome_id = p_outcome_id ORDER BY event_id DESC LIMIT 1;
            IF v_current IS DISTINCT FROM 'REVIEWING' THEN
                INSERT INTO outcome_review_events (outcome_id, status, actor_user_id, comment)
                VALUES (p_outcome_id, 'REVIEWING', p_actor, 'eligibility_rules_started');
            END IF;
            IF v_reason IS NOT NULL THEN
                INSERT INTO outcome_review_events
                    (outcome_id, status, reason_code, actor_user_id, comment)
                VALUES (p_outcome_id, 'REJECTED', v_reason, NULL, 'eligibility_rules_failed');
            ELSIF current_setting('daibm.outcome_review_mode', true) = 'manual' THEN
                NULL;  -- Rules passed; a reviewer decides.
            ELSE
                INSERT INTO outcome_review_events (outcome_id, status, actor_user_id, comment)
                VALUES (p_outcome_id, 'ELIGIBLE', NULL, 'eligibility_rules_passed');
            END IF;
        END $$
        """
    )

    # --- Jobs and ledger ----------------------------------------------------------
    _replace_job_constraints(_NEW_TRIGGER_TYPES)
    previous_ledger = _ledger_check(bind)
    assert "'CALIBRATION_MANUALLY_ACTIVATED'" in previous_ledger
    op.drop_constraint("ck_ledger_events_event_type", "ledger_events", type_="check")
    op.execute(
        "ALTER TABLE ledger_events ADD CONSTRAINT ck_ledger_events_event_type "
        + previous_ledger.replace(
            "'CALIBRATION_MANUALLY_ACTIVATED'::text",
            "'CALIBRATION_MANUALLY_ACTIVATED'::text, 'ACTUAL_OUTCOME_REVIEWED'::text",
        )
    )

    # --- Training dataset snapshots -------------------------------------------------
    op.create_table(
        "training_dataset_snapshots",
        sa.Column("snapshot_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("deployment_scope", sa.Text(), nullable=False),
        sa.Column("dataset_hash", sa.Text(), nullable=False),
        sa.Column("eligibility_policy", sa.Text(), nullable=False),
        sa.Column("included_count", sa.Integer(), nullable=False),
        sa.Column("excluded_count", sa.Integer(), nullable=False),
        sa.Column("exclusion_summary", postgresql.JSONB(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column(
            "trigger_job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("calibration_jobs.job_id", ondelete="RESTRICT"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "deployment_scope", "dataset_hash", name="uq_training_dataset_snapshots_hash"
        ),
        sa.CheckConstraint(
            "deployment_scope IN ('controlled_demo', 'external_verified', 'mixed')",
            name="ck_training_dataset_snapshots_scope",
        ),
        sa.CheckConstraint(
            "dataset_hash ~ '^[0-9a-f]{64}$'", name="ck_training_dataset_snapshots_hash"
        ),
        sa.CheckConstraint(
            "included_count >= 0 AND excluded_count >= 0",
            name="ck_training_dataset_snapshots_counts",
        ),
        sa.CheckConstraint(
            "source IN ('calibration_job', 'migration_backfill')",
            name="ck_training_dataset_snapshots_source",
        ),
    )
    op.create_index(
        "ix_training_dataset_snapshots_trigger_job_id",
        "training_dataset_snapshots",
        ["trigger_job_id"],
    )
    op.create_index(
        "ix_training_dataset_snapshots_created_at", "training_dataset_snapshots", ["created_at"]
    )
    op.create_table(
        "training_dataset_snapshot_items",
        sa.Column(
            "snapshot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("training_dataset_snapshots.snapshot_id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column(
            "outcome_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("actual_outcomes.outcome_id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("included", sa.Boolean(), nullable=False),
        sa.Column(
            "correction_head_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("outcome_corrections.correction_id", ondelete="RESTRICT"),
        ),
        sa.Column("review_status", sa.Text()),
        sa.Column("exclusion_reason", sa.Text()),
        sa.CheckConstraint(
            "included = (exclusion_reason IS NULL)",
            name="ck_training_dataset_snapshot_items_reason",
        ),
    )
    op.create_index(
        "ix_training_dataset_snapshot_items_outcome_id",
        "training_dataset_snapshot_items",
        ["outcome_id"],
    )
    op.create_index(
        "ix_training_dataset_snapshot_items_correction_head_id",
        "training_dataset_snapshot_items",
        ["correction_head_id"],
    )
    for table in ("training_dataset_snapshots", "training_dataset_snapshot_items"):
        op.execute(
            f"CREATE TRIGGER trg_{table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_governed_history_mutation()"
        )
    for table in ("calibration_runs", "risk_model_versions"):
        op.add_column(
            table,
            sa.Column(
                "dataset_snapshot_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("training_dataset_snapshots.snapshot_id", ondelete="RESTRICT"),
            ),
        )
        op.create_index(f"ix_{table}_dataset_snapshot_id", table, ["dataset_snapshot_id"])
    op.execute(
        """
        CREATE FUNCTION reject_dataset_snapshot_relink() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.dataset_snapshot_id IS NOT NULL
               AND NEW.dataset_snapshot_id IS DISTINCT FROM OLD.dataset_snapshot_id THEN
                RAISE EXCEPTION 'training dataset snapshot of % cannot change', TG_TABLE_NAME
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END $$
        """
    )

    # --- Backfill (append-only) --------------------------------------------------
    rows = bind.execute(
        sa.text(
            "SELECT r.calibration_run_id, r.deployment_scope, r.completed_at, "
            "m.outcome_id, m.correction_head_id "
            "FROM calibration_runs r JOIN calibration_run_observations m USING (calibration_run_id) "
            "ORDER BY r.completed_at, r.calibration_run_id, m.outcome_id"
        )
    ).all()
    runs: dict[uuid.UUID, dict] = {}
    for run_id, scope, completed_at, outcome_id, head in rows:
        run = runs.setdefault(
            run_id, {"scope": scope, "completed_at": completed_at, "items": []}
        )
        run["items"].append((str(outcome_id), str(head) if head is not None else None))
    by_hash: dict[tuple[str, str], uuid.UUID] = {}
    links: list[tuple[uuid.UUID, uuid.UUID]] = []
    for run_id, run in runs.items():
        digest = _backfill_hash(run["scope"], run["items"])
        key = (run["scope"], digest)
        if key not in by_hash:
            snapshot_id = uuid.uuid4()
            by_hash[key] = snapshot_id
            bind.execute(
                sa.text(
                    "INSERT INTO training_dataset_snapshots (snapshot_id, deployment_scope, "
                    "dataset_hash, eligibility_policy, included_count, excluded_count, "
                    "exclusion_summary, source, created_by, created_at) VALUES (:id, :scope, "
                    ":hash, :policy, :n, 0, '{}'::jsonb, 'migration_backfill', "
                    "'migration_backfill', :created_at)"
                ),
                {
                    "id": snapshot_id,
                    "scope": run["scope"],
                    "hash": digest,
                    "policy": _BACKFILL_POLICY,
                    "n": len(run["items"]),
                    "created_at": run["completed_at"],
                },
            )
            for outcome_id, head in run["items"]:
                bind.execute(
                    sa.text(
                        "INSERT INTO training_dataset_snapshot_items (snapshot_id, outcome_id, "
                        "included, correction_head_id) VALUES (:id, :outcome, true, :head)"
                    ),
                    {"id": snapshot_id, "outcome": outcome_id, "head": head},
                )
        links.append((run_id, by_hash[key]))
    if links:
        # Only the new column is written; deployment triggers stay out of it.
        op.execute(
            "ALTER TABLE calibration_runs DISABLE TRIGGER trg_calibration_runs_sync_model_version"
        )
        for run_id, snapshot_id in links:
            bind.execute(
                sa.text(
                    "UPDATE calibration_runs SET dataset_snapshot_id = :s "
                    "WHERE calibration_run_id = :r"
                ),
                {"s": snapshot_id, "r": run_id},
            )
            bind.execute(
                sa.text(
                    "UPDATE risk_model_versions SET dataset_snapshot_id = :s "
                    "WHERE calibration_run_id = :r"
                ),
                {"s": snapshot_id, "r": run_id},
            )
        # Fire the deferred checks queued by these updates before altering the table.
        op.execute("SET CONSTRAINTS ALL IMMEDIATE")
        op.execute(
            "ALTER TABLE calibration_runs ENABLE TRIGGER trg_calibration_runs_sync_model_version"
        )
        op.execute("SET CONSTRAINTS ALL DEFERRED")
    for table in ("calibration_runs", "risk_model_versions"):
        op.execute(
            f"CREATE TRIGGER trg_{table}_snapshot_link BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_dataset_snapshot_relink()"
        )


def downgrade() -> None:
    bind = op.get_bind()
    blocked = bind.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM training_dataset_snapshots "
            "WHERE source <> 'migration_backfill') "
            "OR EXISTS (SELECT 1 FROM outcome_review_events WHERE actor_role IS NOT NULL) "
            "OR EXISTS (SELECT 1 FROM calibration_jobs WHERE trigger_type = 'outcome_reviewed') "
            "OR EXISTS (SELECT 1 FROM ledger_events WHERE event_type = 'ACTUAL_OUTCOME_REVIEWED')"
        )
    )
    if blocked:
        raise RuntimeError(
            "Cannot downgrade while outcome governance history from revision 20260927_0018 exists"
        )
    for table in ("calibration_runs", "risk_model_versions"):
        op.execute(f"DROP TRIGGER trg_{table}_snapshot_link ON {table}")
        op.drop_index(f"ix_{table}_dataset_snapshot_id", table_name=table)
        op.drop_column(table, "dataset_snapshot_id")
    op.execute("DROP FUNCTION reject_dataset_snapshot_relink()")
    for table in ("training_dataset_snapshot_items", "training_dataset_snapshots"):
        op.execute(f"DROP TRIGGER trg_{table}_immutable ON {table}")
        op.drop_table(table)
    current = _ledger_check(bind)
    op.drop_constraint("ck_ledger_events_event_type", "ledger_events", type_="check")
    op.execute(
        "ALTER TABLE ledger_events ADD CONSTRAINT ck_ledger_events_event_type "
        + current.replace(", 'ACTUAL_OUTCOME_REVIEWED'::text", "")
    )
    _replace_job_constraints(_OLD_TRIGGER_TYPES)
    op.execute(_OLD_REVIEW_OUTCOME)
    op.execute("DROP TRIGGER trg_outcome_review_events_audit ON outcome_review_events")
    op.execute("DROP FUNCTION audit_outcome_review_event()")
    op.drop_constraint(
        "ck_outcome_review_events_from_status", "outcome_review_events", type_="check"
    )
    op.drop_constraint(
        "ck_outcome_review_events_rejection_reason", "outcome_review_events", type_="check"
    )
    op.create_check_constraint(
        "ck_outcome_review_events_rejection_reason",
        "outcome_review_events",
        "status <> 'REJECTED' OR " + _in("reason_code", _OLD_REJECTION_REASONS),
    )
    for column in ("comment", "actor_role", "from_status"):
        op.drop_column("outcome_review_events", column)
