"""Model version registry: governed lifecycle over calibration artifacts.

``risk_model_versions`` is the registry of record. Each version references one
calibration artifact (``calibration_runs``), which keeps storing the artifact,
its lineage and deployment fields. Every status change of a version goes
through ``transition_model_version`` and is recorded in the append-only
``risk_model_version_transitions`` with from/to status, actor, time, reason,
evaluation metrics and artifact hash. PostgreSQL enforces legal transitions,
one ACTIVE version per scope, an evaluation before ACTIVE, an audited
transition for every status change, and that an ACTIVE version is never
deleted. Artifact deployment changes made by the existing calibration code
are mirrored into the registry by a trigger, so no path can bypass it.

Backfill only appends: every existing artifact is registered with the status
its deployment implies; ACTIVE and previously active artifacts carry their
recorded activation metrics as evaluation (``legacy_activation_record``).
"""

from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260926_0017"
down_revision: str | None = "20260925_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATUSES = ("DRAFT", "EVALUATING", "CANDIDATE", "ACTIVE", "ROLLED_BACK", "RETIRED", "REJECTED")
# Frozen copy of ``app.domain.model_registry.ALLOWED_TRANSITIONS``.
_ALLOWED = (
    ("DRAFT", "EVALUATING"),
    ("DRAFT", "REJECTED"),
    ("DRAFT", "RETIRED"),
    ("EVALUATING", "CANDIDATE"),
    ("EVALUATING", "REJECTED"),
    ("EVALUATING", "RETIRED"),
    ("CANDIDATE", "ACTIVE"),
    ("CANDIDATE", "REJECTED"),
    ("CANDIDATE", "RETIRED"),
    ("ACTIVE", "ROLLED_BACK"),
    ("ACTIVE", "RETIRED"),
    ("ROLLED_BACK", "RETIRED"),
    ("RETIRED", "ACTIVE"),
    ("REJECTED", "RETIRED"),
)
_PREVIOUS_ACTIVATION_MODES = ("automatic", "manual_rollback")


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN (" + ", ".join(f"'{value}'" for value in values) + ")"


def _ledger_check(bind) -> str:
    return bind.scalar(
        sa.text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conname = 'ck_ledger_events_event_type'"
        )
    )


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text("LOCK TABLE calibration_runs, ledger_events, risk_decision_records IN ACCESS EXCLUSIVE MODE")
    )
    previous_ledger = _ledger_check(bind)
    assert previous_ledger is not None and "'CALIBRATION_MODEL_RETIRED'" in previous_ledger
    op.drop_constraint("ck_ledger_events_event_type", "ledger_events", type_="check")
    op.execute(
        "ALTER TABLE ledger_events ADD CONSTRAINT ck_ledger_events_event_type "
        + previous_ledger.replace(
            "'CALIBRATION_MODEL_RETIRED'::text",
            "'CALIBRATION_MODEL_RETIRED'::text, 'CALIBRATION_MANUALLY_ACTIVATED'::text",
        )
    )
    op.drop_constraint("ck_calibration_runs_activation_mode", "calibration_runs", type_="check")
    op.create_check_constraint(
        "ck_calibration_runs_activation_mode",
        "calibration_runs",
        "activation_mode IS NULL OR "
        + _in("activation_mode", _PREVIOUS_ACTIVATION_MODES + ("manual_promotion",)),
    )

    op.create_table(
        "risk_model_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("model_id", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("model_type", sa.Text(), nullable=False),
        sa.Column(
            "calibration_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("calibration_runs.calibration_run_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("artifact_path", sa.Text(), nullable=False),
        sa.Column("artifact_hash", sa.Text(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("training_dataset_version", sa.Text(), nullable=False),
        sa.Column("metrics", postgresql.JSONB(), nullable=False),
        sa.Column("evaluation", postgresql.JSONB()),
        sa.Column("evaluation_passed", sa.Boolean()),
        sa.Column("evaluated_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("status_sequence", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.user_id", ondelete="RESTRICT"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True)),
        sa.Column("deactivated_at", sa.DateTime(timezone=True)),
        sa.Column("promotion_reason", sa.Text()),
        sa.Column(
            "previous_active_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("risk_model_versions.id", ondelete="RESTRICT"),
        ),
        sa.UniqueConstraint("model_id", "version", name="uq_risk_model_versions_model_version"),
        sa.UniqueConstraint("calibration_run_id", name="uq_risk_model_versions_artifact"),
        sa.CheckConstraint(_in("status", _STATUSES), name="ck_risk_model_versions_status"),
        sa.CheckConstraint(
            "model_type IN ('platt_calibration')", name="ck_risk_model_versions_type"
        ),
        sa.CheckConstraint(
            "scope IN ('controlled_demo', 'external_verified', 'mixed')",
            name="ck_risk_model_versions_scope",
        ),
        sa.CheckConstraint(
            "artifact_hash ~ '^[0-9a-f]{64}$' AND training_dataset_version ~ '^[0-9a-f]{64}$'",
            name="ck_risk_model_versions_hashes",
        ),
        sa.CheckConstraint(
            "version >= 1 AND status_sequence >= 1", name="ck_risk_model_versions_counters"
        ),
        sa.CheckConstraint(
            "(evaluation IS NULL) = (evaluation_passed IS NULL) "
            "AND (evaluation IS NULL) = (evaluated_at IS NULL)",
            name="ck_risk_model_versions_evaluation",
        ),
        sa.CheckConstraint(
            "status <> 'ACTIVE' OR (evaluation_passed IS TRUE AND activated_at IS NOT NULL "
            "AND promotion_reason IS NOT NULL)",
            name="ck_risk_model_versions_active_contract",
        ),
        sa.CheckConstraint(
            "status NOT IN ('CANDIDATE', 'ACTIVE') OR evaluation_passed IS TRUE",
            name="ck_risk_model_versions_evaluated_contract",
        ),
    )
    op.create_index(
        "uq_risk_model_versions_active_scope",
        "risk_model_versions",
        ["scope"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    op.create_index("ix_risk_model_versions_status", "risk_model_versions", ["status"])
    op.create_index(
        "ix_risk_model_versions_created_by_user_id", "risk_model_versions", ["created_by_user_id"]
    )
    op.create_index(
        "ix_risk_model_versions_previous_active_version_id",
        "risk_model_versions",
        ["previous_active_version_id"],
    )

    op.create_table(
        "risk_model_version_transitions",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column(
            "model_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("risk_model_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("from_status", sa.Text()),
        sa.Column("to_status", sa.Text(), nullable=False),
        sa.Column("status_sequence", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "actor_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.user_id", ondelete="RESTRICT"),
        ),
        sa.Column("actor_label", sa.Text(), nullable=False),
        sa.Column("evaluation_metrics", postgresql.JSONB()),
        sa.Column("artifact_hash", sa.Text(), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.CheckConstraint(
            "from_status IS NULL OR " + _in("from_status", _STATUSES),
            name="ck_risk_model_version_transitions_from",
        ),
        sa.CheckConstraint(_in("to_status", _STATUSES), name="ck_risk_model_version_transitions_to"),
        sa.CheckConstraint(
            "from_status IS DISTINCT FROM to_status",
            name="ck_risk_model_version_transitions_changes",
        ),
        sa.CheckConstraint(
            "to_status <> 'ACTIVE' OR evaluation_metrics IS NOT NULL",
            name="ck_risk_model_version_transitions_activation_evidence",
        ),
        sa.UniqueConstraint(
            "model_version_id",
            "status_sequence",
            name="uq_risk_model_version_transitions_sequence",
        ),
    )
    op.create_index(
        "ix_risk_model_version_transitions_actor_user_id",
        "risk_model_version_transitions",
        ["actor_user_id"],
    )
    op.create_index(
        "ix_risk_model_version_transitions_recorded_at",
        "risk_model_version_transitions",
        ["recorded_at"],
    )
    op.execute(
        "CREATE TRIGGER trg_risk_model_version_transitions_immutable BEFORE UPDATE OR DELETE "
        "ON risk_model_version_transitions FOR EACH ROW "
        "EXECUTE FUNCTION reject_governed_history_mutation()"
    )

    op.add_column(
        "risk_decision_records",
        sa.Column(
            "model_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("risk_model_versions.id", ondelete="RESTRICT"),
        ),
    )
    op.create_index(
        "ix_risk_decision_records_model_version_id", "risk_decision_records", ["model_version_id"]
    )

    # --- Functions ------------------------------------------------------------
    op.execute(
        """
        CREATE FUNCTION governance_actor_label() RETURNS text LANGUAGE sql STABLE AS $$
            SELECT COALESCE(
                (SELECT username FROM users WHERE user_id = governance_actor()),
                'system:calibration-worker')
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION transition_model_version(
            p_version_id uuid, p_to text, p_reason text, p_metrics jsonb
        ) RETURNS void LANGUAGE plpgsql AS $$
        DECLARE v risk_model_versions%ROWTYPE;
        BEGIN
            SELECT * INTO v FROM risk_model_versions WHERE id = p_version_id FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'model version % does not exist', p_version_id USING ERRCODE = '23503';
            END IF;
            IF v.status = p_to THEN RETURN; END IF;
            UPDATE risk_model_versions SET
                status = p_to,
                status_sequence = v.status_sequence + 1,
                activated_at = CASE WHEN p_to = 'ACTIVE' THEN clock_timestamp() ELSE activated_at END,
                deactivated_at = CASE WHEN p_to = 'ACTIVE' THEN NULL
                    WHEN v.status = 'ACTIVE' THEN clock_timestamp() ELSE deactivated_at END,
                promotion_reason = CASE WHEN p_to = 'ACTIVE' THEN p_reason ELSE promotion_reason END
            WHERE id = p_version_id;
            INSERT INTO risk_model_version_transitions (model_version_id, from_status, to_status,
                status_sequence, reason, actor_user_id, actor_label, evaluation_metrics, artifact_hash)
            VALUES (p_version_id, v.status, p_to, v.status_sequence + 1, p_reason,
                governance_actor(), governance_actor_label(),
                COALESCE(p_metrics, CASE WHEN p_to = 'ACTIVE' THEN v.evaluation END),
                v.artifact_hash);
        END $$
        """
    )
    allowed = ", ".join(f"('{a}', '{b}')" for a, b in _ALLOWED)
    op.execute(
        f"""
        CREATE FUNCTION guard_model_version_status() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.status IS DISTINCT FROM OLD.status THEN
                IF NOT EXISTS (SELECT 1 FROM (VALUES {allowed}) AS a(f, t)
                               WHERE a.f = OLD.status AND a.t = NEW.status) THEN
                    RAISE EXCEPTION 'illegal model version transition % -> %', OLD.status, NEW.status
                        USING ERRCODE = '23514';
                END IF;
                IF OLD.status = 'RETIRED' AND NEW.status = 'ACTIVE' AND OLD.activated_at IS NULL THEN
                    RAISE EXCEPTION 'only a previously ACTIVE version can be restored by rollback'
                        USING ERRCODE = '23514';
                END IF;
            END IF;
            IF NEW.model_id <> OLD.model_id OR NEW.version <> OLD.version
               OR NEW.calibration_run_id <> OLD.calibration_run_id
               OR NEW.artifact_path <> OLD.artifact_path OR NEW.artifact_hash <> OLD.artifact_hash
               OR NEW.scope <> OLD.scope OR NEW.created_at <> OLD.created_at THEN
                RAISE EXCEPTION 'model version identity and artifact are immutable'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            IF OLD.evaluation IS NOT NULL AND NEW.evaluation IS DISTINCT FROM OLD.evaluation THEN
                RAISE EXCEPTION 'model version evaluation is immutable once recorded'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION require_model_version_transition_audit() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.status IS DISTINCT FROM OLD.status AND NOT EXISTS (
                SELECT 1 FROM risk_model_version_transitions t
                WHERE t.model_version_id = NEW.id AND t.to_status = NEW.status
                  AND t.from_status = OLD.status AND t.status_sequence = NEW.status_sequence)
            THEN
                RAISE EXCEPTION 'model version % status change % -> % has no audited transition',
                    NEW.id, OLD.status, NEW.status USING ERRCODE = '23514';
            END IF;
            RETURN NULL;
        END $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION reject_active_model_version_delete() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.status = 'ACTIVE' THEN
                RAISE EXCEPTION 'an ACTIVE model version cannot be deleted; roll back first'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN OLD;
        END $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION sync_model_version_from_artifact() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            v risk_model_versions%ROWTYPE;
            target text;
            why text;
        BEGIN
            SELECT * INTO v FROM risk_model_versions WHERE calibration_run_id = NEW.calibration_run_id;
            IF NOT FOUND THEN RETURN NULL; END IF;
            IF NEW.deployment_status = 'active' THEN
                target := 'ACTIVE'; why := NEW.activation_reason;
            ELSIF NEW.retired_at IS NOT NULL THEN
                target := 'RETIRED'; why := NEW.retirement_reason;
            ELSIF NEW.rolled_back_at IS NOT NULL THEN
                target := 'ROLLED_BACK'; why := 'manual_rollback';
            ELSIF NEW.deployment_status = 'superseded' THEN
                target := 'RETIRED'; why := 'superseded_by_new_activation';
            ELSIF NEW.deployment_status = 'invalidated' THEN
                target := 'RETIRED'; why := 'invalidated_by_outcome_correction';
            ELSIF NEW.deployment_status IN ('rejected', 'activation_failed') THEN
                target := 'REJECTED'; why := NEW.activation_reason;
            ELSE
                RETURN NULL;
            END IF;
            IF target = 'ACTIVE' THEN
                UPDATE risk_model_versions SET previous_active_version_id = (
                    SELECT id FROM risk_model_versions WHERE calibration_run_id = NEW.previous_active_run_id)
                WHERE id = v.id AND NEW.activation_mode <> 'manual_rollback';
            END IF;
            PERFORM transition_model_version(v.id, target, why, NULL);
            RETURN NULL;
        END $$
        """
    )

    # --- Backfill (append-only) -------------------------------------------------
    bind.execute(
        sa.text(
            """
            INSERT INTO risk_model_versions (id, model_id, version, model_type, calibration_run_id,
                artifact_path, artifact_hash, scope, training_dataset_version, metrics, evaluation,
                evaluation_passed, evaluated_at, status, status_sequence, created_by,
                created_by_user_id, created_at, activated_at, deactivated_at, promotion_reason)
            SELECT gen_random_uuid(), 'calibration:' || r.deployment_scope,
                row_number() OVER (PARTITION BY r.deployment_scope ORDER BY r.completed_at, r.calibration_run_id),
                'platt_calibration', r.calibration_run_id, r.artifact_locator, r.artifact_sha256,
                r.deployment_scope, r.dataset_sha256,
                jsonb_build_object('holdout_before', r.metrics_before, 'holdout_after', r.metrics_after,
                    'sample_count', r.sample_count),
                CASE WHEN r.activated_at IS NOT NULL THEN jsonb_build_object(
                    'source', 'legacy_activation_record', 'activation_reason', r.activation_reason,
                    'metrics_before', r.metrics_before, 'metrics_after', r.metrics_after) END,
                CASE WHEN r.activated_at IS NOT NULL THEN true END,
                r.activated_at,
                calibration_registry_status(r.status, r.deployment_status, r.rolled_back_at, r.retired_at),
                1, 'migration_backfill', NULL, r.completed_at, r.activated_at, r.deactivated_at,
                CASE WHEN r.activated_at IS NOT NULL THEN r.activation_reason END
            FROM calibration_runs r
            WHERE r.artifact_locator IS NOT NULL AND r.artifact_sha256 IS NOT NULL
            """
        )
    )
    bind.execute(
        sa.text(
            "UPDATE risk_model_versions v SET previous_active_version_id = p.id "
            "FROM calibration_runs r JOIN risk_model_versions p "
            "ON p.calibration_run_id = r.previous_active_run_id "
            "WHERE v.calibration_run_id = r.calibration_run_id"
        )
    )
    bind.execute(
        sa.text(
            "INSERT INTO risk_model_version_transitions (model_version_id, from_status, to_status, "
            "status_sequence, reason, actor_label, evaluation_metrics, artifact_hash, recorded_at) "
            "SELECT id, NULL, status, 1, 'migration_backfill', 'migration_backfill', evaluation, "
            "artifact_hash, created_at FROM risk_model_versions ORDER BY created_at, id"
        )
    )

    # --- Triggers (after backfill) ---------------------------------------------
    op.execute(
        "CREATE TRIGGER trg_risk_model_versions_guard BEFORE UPDATE ON risk_model_versions "
        "FOR EACH ROW EXECUTE FUNCTION guard_model_version_status()"
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER trg_risk_model_versions_transition_audit AFTER UPDATE "
        "ON risk_model_versions DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "EXECUTE FUNCTION require_model_version_transition_audit()"
    )
    op.execute(
        "CREATE TRIGGER trg_risk_model_versions_active_delete BEFORE DELETE ON risk_model_versions "
        "FOR EACH ROW EXECUTE FUNCTION reject_active_model_version_delete()"
    )
    op.execute(
        "CREATE TRIGGER trg_calibration_runs_sync_model_version AFTER UPDATE ON calibration_runs "
        "FOR EACH ROW EXECUTE FUNCTION sync_model_version_from_artifact()"
    )


def downgrade() -> None:
    bind = op.get_bind()
    blocked = bind.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM risk_model_version_transitions "
            "WHERE reason <> 'migration_backfill') "
            "OR EXISTS (SELECT 1 FROM risk_model_versions WHERE created_by <> 'migration_backfill') "
            "OR EXISTS (SELECT 1 FROM calibration_runs WHERE activation_mode = 'manual_promotion') "
            "OR EXISTS (SELECT 1 FROM risk_decision_records WHERE model_version_id IS NOT NULL) "
            "OR EXISTS (SELECT 1 FROM ledger_events WHERE event_type = 'CALIBRATION_MANUALLY_ACTIVATED')"
        )
    )
    if blocked:
        raise RuntimeError(
            "Cannot downgrade while model version history from revision 20260926_0017 exists"
        )
    op.execute("DROP TRIGGER trg_calibration_runs_sync_model_version ON calibration_runs")
    op.drop_index("ix_risk_decision_records_model_version_id", table_name="risk_decision_records")
    op.drop_column("risk_decision_records", "model_version_id")
    op.execute("DROP TRIGGER trg_risk_model_version_transitions_immutable ON risk_model_version_transitions")
    op.drop_table("risk_model_version_transitions")
    op.drop_table("risk_model_versions")
    for name in (
        "sync_model_version_from_artifact()",
        "reject_active_model_version_delete()",
        "require_model_version_transition_audit()",
        "guard_model_version_status()",
        "transition_model_version(uuid, text, text, jsonb)",
        "governance_actor_label()",
    ):
        op.execute(f"DROP FUNCTION {name}")
    op.drop_constraint("ck_calibration_runs_activation_mode", "calibration_runs", type_="check")
    op.create_check_constraint(
        "ck_calibration_runs_activation_mode",
        "calibration_runs",
        "activation_mode IS NULL OR " + _in("activation_mode", _PREVIOUS_ACTIVATION_MODES),
    )
    current = _ledger_check(bind)
    op.drop_constraint("ck_ledger_events_event_type", "ledger_events", type_="check")
    op.execute(
        "ALTER TABLE ledger_events ADD CONSTRAINT ck_ledger_events_event_type "
        + current.replace(", 'CALIBRATION_MANUALLY_ACTIVATED'::text", "")
    )
