"""Govern risk models and outcome data: registry, review, supersession, decision audit.

- ``calibration_runs`` gains governed rollback/retirement fields. A registry
  projection (DRAFT, EVALUATING, CANDIDATE, ACTIVE, ROLLED_BACK, RETIRED,
  REJECTED) is written to the append-only ``model_registry_events`` by
  triggers, so no code path can change a model's state without an event.
- Automatic promotion to ACTIVE requires a passed independent-validation event;
  an ACTIVE model cannot be deleted.
- ``actual_outcomes`` supports superseding corrections: a new revision
  references its predecessor, which is kept and loses training eligibility.
- ``outcome_review_events`` records CREATED -> REVIEWING -> ELIGIBLE ->
  TRAINING_USED or REJECTED. Eligibility rules run in PostgreSQL on every new
  outcome and every correction.
- ``risk_decision_records`` keeps one immutable record per business risk
  assessment: input, model, artifact hash, scope decision, output and actor.

Backfill only appends. Existing outcomes keep exactly their current training
eligibility (``LEGACY_ACCEPTED_BEFORE_REVIEW`` or the existing exclusion).
"""

from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260925_0016"
down_revision: str | None = "20260924_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_PREVIOUS_EVENT_TYPES = (
    "FINANCING_REQUEST",
    "RISK_ASSESSMENT",
    "FINANCING_DECISION",
    "CONTROL_ACTION",
    "MODEL_INFERENCE_COMPLETED",
    "RISK_POLICY_TRIGGERED",
    "CONTROL_ACTION_REQUESTED",
    "SIMULATED_RISK_INJECTED",
    "INTEGRITY_VIOLATION_DETECTED",
    "LEDGER_RECOVERY_COMPLETED",
    "APPLICATION_DRAFT_CREATED",
    "APPLICATION_UPDATED",
    "APPLICATION_SUBMITTED",
    "TRADE_CONFIRMED",
    "TRADE_RETURNED",
    "AUDIT_REVIEW_COMPLETED",
    "FACILITY_CREATED",
    "DISBURSEMENT_INITIATED",
    "DISBURSEMENT_CONFIRMED",
    "REPAYMENT_SUBMITTED",
    "REPAYMENT_CONFIRMED",
    "REPAYMENT_REJECTED",
    "FACILITY_MARKED_OVERDUE",
    "FACILITY_REPAID",
    "FACILITY_CLOSED",
    "ACTUAL_OUTCOME_RECORDED",
    "CALIBRATION_CANDIDATE_TRAINED",
    "CALIBRATION_CANDIDATE_FAILED",
    "CALIBRATION_AUTO_ACTIVATED",
    "CALIBRATION_AUTO_REJECTED",
    "CALIBRATION_ROLLED_BACK",
    "RISK_CALIBRATION_APPLIED",
    "RISK_CALIBRATION_FALLBACK",
    "FACILITY_RESTRUCTURED",
    "FACILITY_DEFAULTED",
    "FACILITY_WRITTEN_OFF",
    "OUTCOME_TRAINING_EXCLUDED",
    "OUTCOME_TRAINING_REINSTATED",
    "CALIBRATION_DEPLOYMENT_INVALIDATED",
    "FACILITY_OVERDUE_CURED",
    "FACILITY_DISPOSAL_OPENED",
    "FACILITY_DISPOSAL_CLOSED",
    "FACILITY_RECOVERY_STARTED",
    "FACILITY_RECOVERY_RECORDED",
    "FACILITY_RECOVERED",
)
_GOVERNANCE_EVENT_TYPES = ("ACTUAL_OUTCOME_SUPERSEDED", "CALIBRATION_MODEL_RETIRED")

_NEW_TABLES = ("model_registry_events", "outcome_review_events", "risk_decision_records")


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN (" + ", ".join(f"'{value}'" for value in values) + ")"


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "LOCK TABLE calibration_runs, actual_outcomes, outcome_corrections, "
            "calibration_run_observations, model_versions, ledger_events "
            "IN ACCESS EXCLUSIVE MODE"
        )
    )
    op.drop_constraint("ck_ledger_events_event_type", "ledger_events", type_="check")
    op.create_check_constraint(
        "ck_ledger_events_event_type",
        "ledger_events",
        _in("event_type", _PREVIOUS_EVENT_TYPES + _GOVERNANCE_EVENT_TYPES),
    )

    # --- Model registry -----------------------------------------------------
    op.add_column("calibration_runs", sa.Column("rolled_back_at", sa.DateTime(timezone=True)))
    op.add_column("calibration_runs", sa.Column("retired_at", sa.DateTime(timezone=True)))
    op.add_column(
        "calibration_runs",
        sa.Column(
            "retired_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.user_id", ondelete="RESTRICT"),
        ),
    )
    op.add_column("calibration_runs", sa.Column("retirement_reason", sa.Text()))
    op.create_index(
        "ix_calibration_runs_retired_by_user_id", "calibration_runs", ["retired_by_user_id"]
    )
    op.create_check_constraint(
        "ck_calibration_runs_registry_contract",
        "calibration_runs",
        "(retired_at IS NULL) = (retired_by_user_id IS NULL) "
        "AND (retired_at IS NULL) = (retirement_reason IS NULL) "
        "AND (retired_at IS NULL OR deployment_status <> 'active') "
        "AND (rolled_back_at IS NULL OR deployment_status <> 'active')",
    )

    op.create_table(
        "model_registry_events",
        sa.Column("event_id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("model_kind", sa.Text(), nullable=False),
        sa.Column("model_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("from_status", sa.Text()),
        sa.Column("to_status", sa.Text()),
        sa.Column("deployment_scope", sa.Text()),
        sa.Column("dataset_sha256", sa.Text()),
        sa.Column("sample_count", sa.Integer()),
        sa.Column("metrics", postgresql.JSONB()),
        sa.Column("artifact_sha256", sa.Text()),
        sa.Column("reason", sa.Text()),
        sa.Column(
            "actor_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.user_id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.CheckConstraint(
            "model_kind IN ('calibration', 'research')", name="ck_model_registry_events_kind"
        ),
        sa.CheckConstraint(
            "event_type IN ('REGISTERED', 'STATUS_CHANGED', 'VALIDATED', 'PROMOTION_DECISION')",
            name="ck_model_registry_events_type",
        ),
        sa.CheckConstraint(
            "(event_type = 'STATUS_CHANGED') = (to_status IS NOT NULL)",
            name="ck_model_registry_events_status",
        ),
        sa.CheckConstraint(
            "to_status IS NULL OR to_status IN ('DRAFT', 'EVALUATING', 'CANDIDATE', "
            "'ACTIVE', 'ROLLED_BACK', 'RETIRED', 'REJECTED')",
            name="ck_model_registry_events_to_status",
        ),
    )
    op.create_index(
        "ix_model_registry_events_model",
        "model_registry_events",
        ["model_kind", "model_id", "event_id"],
    )
    op.create_index(
        "ix_model_registry_events_actor_user_id", "model_registry_events", ["actor_user_id"]
    )
    op.create_index("ix_model_registry_events_recorded_at", "model_registry_events", ["recorded_at"])

    op.execute(
        """
        CREATE FUNCTION calibration_registry_status(
            p_status text, p_deployment_status text,
            p_rolled_back_at timestamptz, p_retired_at timestamptz
        ) RETURNS text LANGUAGE sql IMMUTABLE AS $$
            SELECT CASE
                WHEN p_deployment_status = 'active' THEN 'ACTIVE'
                WHEN p_retired_at IS NOT NULL THEN 'RETIRED'
                WHEN p_rolled_back_at IS NOT NULL THEN 'ROLLED_BACK'
                WHEN p_deployment_status IN ('superseded', 'invalidated') THEN 'RETIRED'
                WHEN p_status = 'failed'
                     OR p_deployment_status IN ('rejected', 'activation_failed') THEN 'REJECTED'
                WHEN p_status = 'exploratory_candidate' THEN 'DRAFT'
                ELSE 'EVALUATING'
            END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION governance_actor() RETURNS uuid LANGUAGE sql STABLE AS $$
            SELECT NULLIF(current_setting('daibm.actor_user_id', true), '')::uuid
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION log_calibration_registry_event() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            v_status text := calibration_registry_status(
                NEW.status, NEW.deployment_status, NEW.rolled_back_at, NEW.retired_at);
            v_last text;
            v_reason text;
        BEGIN
            IF TG_OP = 'INSERT' THEN
                INSERT INTO model_registry_events (model_kind, model_id, event_type,
                    deployment_scope, dataset_sha256, sample_count, metrics,
                    artifact_sha256, reason, actor_user_id)
                VALUES ('calibration', NEW.calibration_run_id, 'REGISTERED',
                    NEW.deployment_scope, NEW.dataset_sha256, NEW.sample_count,
                    jsonb_build_object('before', NEW.metrics_before, 'after', NEW.metrics_after),
                    NEW.artifact_sha256, COALESCE(NEW.failure_code, NEW.status),
                    governance_actor());
            END IF;
            SELECT to_status INTO v_last FROM model_registry_events
            WHERE model_kind = 'calibration' AND model_id = NEW.calibration_run_id
              AND event_type = 'STATUS_CHANGED'
            ORDER BY event_id DESC LIMIT 1;
            IF v_last IS DISTINCT FROM v_status THEN
                v_reason := CASE v_status
                    WHEN 'REJECTED' THEN COALESCE(NEW.failure_code, NEW.activation_reason)
                    WHEN 'RETIRED' THEN COALESCE(NEW.retirement_reason,
                        CASE NEW.deployment_status
                            WHEN 'invalidated' THEN 'invalidated_by_outcome_correction'
                            ELSE 'superseded_by_promotion' END)
                    WHEN 'ROLLED_BACK' THEN 'manual_rollback'
                    WHEN 'ACTIVE' THEN NEW.activation_reason
                    ELSE NEW.status
                END;
                INSERT INTO model_registry_events (model_kind, model_id, event_type,
                    from_status, to_status, deployment_scope, dataset_sha256,
                    sample_count, artifact_sha256, reason, actor_user_id)
                VALUES ('calibration', NEW.calibration_run_id, 'STATUS_CHANGED',
                    v_last, v_status, NEW.deployment_scope, NEW.dataset_sha256,
                    NEW.sample_count, NEW.artifact_sha256, v_reason, governance_actor());
            END IF;
            RETURN NULL;
        END $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION require_validated_promotion() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.deployment_status = 'active'
               AND OLD.deployment_status IS DISTINCT FROM 'active'
               AND NEW.activation_mode = 'automatic'
               AND NOT EXISTS (
                   SELECT 1 FROM model_registry_events e
                   WHERE e.model_kind = 'calibration' AND e.model_id = NEW.calibration_run_id
                     AND e.event_type = 'VALIDATED' AND e.reason = 'validation_passed')
            THEN
                RAISE EXCEPTION 'calibration % promotion requires a passed independent validation',
                    NEW.calibration_run_id USING ERRCODE = '23514';
            END IF;
            RETURN NULL;
        END $$
        """
    )
    for table_name, active_condition in (
        ("calibration_runs", "OLD.deployment_status = 'active'"),
        ("model_versions", "OLD.deployment_slot IS NOT NULL"),
    ):
        op.execute(
            f"""
            CREATE FUNCTION reject_active_{table_name}_delete() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                IF {active_condition} THEN
                    RAISE EXCEPTION 'an ACTIVE model cannot be deleted; retire or roll back first'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                RETURN OLD;
            END $$
            """
        )

    # Baseline registry history for models that predate this revision.
    bind.execute(
        sa.text(
            "INSERT INTO model_registry_events (model_kind, model_id, event_type, "
            "deployment_scope, dataset_sha256, sample_count, metrics, artifact_sha256, reason) "
            "SELECT 'calibration', calibration_run_id, 'REGISTERED', deployment_scope, "
            "dataset_sha256, sample_count, jsonb_build_object('before', metrics_before, "
            "'after', metrics_after), artifact_sha256, 'migration_baseline' "
            "FROM calibration_runs ORDER BY completed_at, calibration_run_id"
        )
    )
    bind.execute(
        sa.text(
            "INSERT INTO model_registry_events (model_kind, model_id, event_type, to_status, "
            "deployment_scope, dataset_sha256, sample_count, artifact_sha256, reason) "
            "SELECT 'calibration', calibration_run_id, 'STATUS_CHANGED', "
            "calibration_registry_status(status, deployment_status, NULL, NULL), "
            "deployment_scope, dataset_sha256, sample_count, artifact_sha256, "
            "'migration_baseline' FROM calibration_runs ORDER BY completed_at, calibration_run_id"
        )
    )
    op.execute(
        "CREATE TRIGGER trg_calibration_runs_registry_log AFTER INSERT OR UPDATE "
        "ON calibration_runs FOR EACH ROW EXECUTE FUNCTION log_calibration_registry_event()"
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER trg_calibration_runs_validated_promotion AFTER UPDATE "
        "ON calibration_runs DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "EXECUTE FUNCTION require_validated_promotion()"
    )
    for table_name in ("calibration_runs", "model_versions"):
        op.execute(
            f"CREATE TRIGGER trg_{table_name}_active_delete BEFORE DELETE ON {table_name} "
            f"FOR EACH ROW EXECUTE FUNCTION reject_active_{table_name}_delete()"
        )

    # --- Superseding outcome corrections ------------------------------------
    op.add_column(
        "actual_outcomes",
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "actual_outcomes",
        sa.Column(
            "supersedes_outcome_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("actual_outcomes.outcome_id", ondelete="RESTRICT"),
        ),
    )
    op.add_column("actual_outcomes", sa.Column("correction_reason_code", sa.Text()))
    op.add_column("actual_outcomes", sa.Column("correction_comment", sa.Text()))
    for column in ("facility_id", "request_id", "risk_assessment_id"):
        op.drop_constraint(f"actual_outcomes_{column}_key", "actual_outcomes", type_="unique")
        op.create_unique_constraint(
            f"uq_actual_outcomes_{column}_revision", "actual_outcomes", [column, "revision"]
        )
    op.create_unique_constraint(
        "uq_actual_outcomes_supersedes_outcome_id", "actual_outcomes", ["supersedes_outcome_id"]
    )
    op.create_check_constraint(
        "ck_actual_outcomes_revision_chain",
        "actual_outcomes",
        "revision >= 1 AND (revision = 1) = (supersedes_outcome_id IS NULL) "
        "AND (revision = 1) = (correction_reason_code IS NULL) "
        "AND (revision = 1) = (correction_comment IS NULL)",
    )
    op.execute(
        """
        CREATE FUNCTION check_outcome_supersession() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE predecessor actual_outcomes%ROWTYPE;
        BEGIN
            IF NEW.supersedes_outcome_id IS NULL THEN RETURN NEW; END IF;
            SELECT * INTO predecessor FROM actual_outcomes
            WHERE outcome_id = NEW.supersedes_outcome_id FOR UPDATE;
            IF NOT FOUND
               OR predecessor.facility_id <> NEW.facility_id
               OR predecessor.request_id <> NEW.request_id
               OR predecessor.risk_assessment_id <> NEW.risk_assessment_id
               OR predecessor.provenance <> NEW.provenance
               OR predecessor.revision + 1 <> NEW.revision THEN
                RAISE EXCEPTION 'superseding outcome must continue its predecessor lineage'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_actual_outcomes_supersession BEFORE INSERT ON actual_outcomes "
        "FOR EACH ROW EXECUTE FUNCTION check_outcome_supersession()"
    )

    # --- Outcome review lifecycle -------------------------------------------
    op.create_table(
        "outcome_review_events",
        sa.Column("event_id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column(
            "outcome_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("actual_outcomes.outcome_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("reason_code", sa.Text()),
        sa.Column(
            "actor_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.user_id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "calibration_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("calibration_runs.calibration_run_id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.CheckConstraint(
            "status IN ('CREATED', 'REVIEWING', 'ELIGIBLE', 'TRAINING_USED', 'REJECTED')",
            name="ck_outcome_review_events_status",
        ),
        sa.CheckConstraint(
            "status <> 'REJECTED' OR reason_code IN ("
            "'SCOPE_MISMATCH', 'DATA_QUALITY_INSUFFICIENT', 'BUSINESS_INCONSISTENT', "
            "'BUSINESS_EXCEPTION', 'MANUAL_CORRECTION', 'SUPERSEDED_BY_CORRECTION')",
            name="ck_outcome_review_events_rejection_reason",
        ),
        sa.CheckConstraint(
            "(status = 'TRAINING_USED') = (calibration_run_id IS NOT NULL)",
            name="ck_outcome_review_events_training_run",
        ),
    )
    op.create_index(
        "ix_outcome_review_events_outcome_id", "outcome_review_events", ["outcome_id", "event_id"]
    )
    op.create_index(
        "ix_outcome_review_events_actor_user_id", "outcome_review_events", ["actor_user_id"]
    )
    op.create_index(
        "ix_outcome_review_events_calibration_run_id",
        "outcome_review_events",
        ["calibration_run_id"],
    )
    op.create_index("ix_outcome_review_events_recorded_at", "outcome_review_events", ["recorded_at"])

    op.execute(
        """
        CREATE FUNCTION outcome_eligibility_reason(p_outcome_id uuid) RETURNS text
        LANGUAGE plpgsql STABLE AS $$
        DECLARE
            o actual_outcomes%ROWTYPE;
            request_scope text;
            expected_provenance text;
            facility financing_facilities%ROWTYPE;
        BEGIN
            SELECT * INTO o FROM actual_outcomes WHERE outcome_id = p_outcome_id;
            IF EXISTS (SELECT 1 FROM actual_outcomes WHERE supersedes_outcome_id = p_outcome_id) THEN
                RETURN 'SUPERSEDED_BY_CORRECTION';
            END IF;
            SELECT assessment_scope INTO request_scope FROM financing_requests
            WHERE request_id = o.request_id;
            SELECT * INTO facility FROM financing_facilities WHERE facility_id = o.facility_id;
            IF request_scope IS NULL OR facility.facility_id IS NULL
               OR facility.status <> 'closed' THEN
                RETURN 'BUSINESS_EXCEPTION';
            END IF;
            expected_provenance := CASE request_scope
                WHEN 'controlled_demo' THEN 'CONTROLLED_DEMO'
                WHEN 'external_verified' THEN 'EXTERNAL_VERIFIED' END;
            IF o.provenance IS DISTINCT FROM expected_provenance THEN
                RETURN 'SCOPE_MISMATCH';
            END IF;
            IF o.original_risk_score <= 0 OR o.original_risk_score >= 1
               OR (o.defaulted AND o.days_past_due = 0)
               OR (facility.closed_at IS NOT NULL AND o.observed_at < facility.closed_at) THEN
                RETURN 'DATA_QUALITY_INSUFFICIENT';
            END IF;
            IF (NOT o.defaulted AND o.loss_amount > 0) OR o.loss_amount > facility.principal THEN
                RETURN 'BUSINESS_INCONSISTENT';
            END IF;
            RETURN NULL;
        END $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION review_outcome(p_outcome_id uuid, p_actor uuid) RETURNS void
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
    )
    op.execute(
        """
        CREATE FUNCTION review_new_outcome() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            INSERT INTO outcome_review_events (outcome_id, status, actor_user_id)
            VALUES (NEW.outcome_id, 'CREATED', NEW.submitted_by_user_id);
            IF NEW.supersedes_outcome_id IS NOT NULL THEN
                INSERT INTO outcome_review_events (outcome_id, status, reason_code, actor_user_id)
                VALUES (NEW.supersedes_outcome_id, 'REJECTED', 'SUPERSEDED_BY_CORRECTION',
                        NEW.submitted_by_user_id);
            END IF;
            PERFORM review_outcome(NEW.outcome_id, NEW.submitted_by_user_id);
            RETURN NULL;
        END $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION review_corrected_outcome() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.action = 'EXCLUDE' THEN
                INSERT INTO outcome_review_events (outcome_id, status, reason_code, actor_user_id)
                VALUES (NEW.outcome_id, 'REJECTED', 'MANUAL_CORRECTION', NEW.auditor_user_id);
            ELSE
                PERFORM review_outcome(NEW.outcome_id, NEW.auditor_user_id);
            END IF;
            RETURN NULL;
        END $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION record_training_use() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF (SELECT status FROM outcome_review_events WHERE outcome_id = NEW.outcome_id
                ORDER BY event_id DESC LIMIT 1) IN ('ELIGIBLE', 'TRAINING_USED') THEN
                INSERT INTO outcome_review_events (outcome_id, status, calibration_run_id)
                VALUES (NEW.outcome_id, 'TRAINING_USED', NEW.calibration_run_id);
            END IF;
            RETURN NULL;
        END $$
        """
    )

    # Baseline review history: legacy outcomes keep exactly their eligibility.
    bind.execute(
        sa.text(
            "INSERT INTO outcome_review_events (outcome_id, status, reason_code, actor_user_id, "
            "recorded_at) SELECT o.outcome_id, 'CREATED', 'MIGRATION_BASELINE', u.user_id, "
            "o.recorded_at FROM actual_outcomes o LEFT JOIN users u "
            "ON u.user_id = o.submitted_by_user_id ORDER BY o.recorded_at, o.outcome_id"
        )
    )
    bind.execute(
        sa.text(
            """
            INSERT INTO outcome_review_events (outcome_id, status, reason_code, actor_user_id, recorded_at)
            SELECT o.outcome_id,
                   CASE WHEN head.action = 'EXCLUDE' THEN 'REJECTED' ELSE 'ELIGIBLE' END,
                   CASE WHEN head.action = 'EXCLUDE' THEN 'MANUAL_CORRECTION'
                        ELSE 'LEGACY_ACCEPTED_BEFORE_REVIEW' END,
                   u.user_id, o.recorded_at
            FROM actual_outcomes o
            LEFT JOIN users u ON u.user_id = o.submitted_by_user_id
            LEFT JOIN LATERAL (
                SELECT c.action FROM outcome_corrections c WHERE c.outcome_id = o.outcome_id
                ORDER BY c.recorded_at DESC, c.correction_id DESC LIMIT 1
            ) head ON true
            ORDER BY o.recorded_at, o.outcome_id
            """
        )
    )
    bind.execute(
        sa.text(
            """
            INSERT INTO outcome_review_events (outcome_id, status, calibration_run_id, recorded_at)
            SELECT m.outcome_id, 'TRAINING_USED', m.calibration_run_id, r.completed_at
            FROM calibration_run_observations m
            JOIN calibration_runs r USING (calibration_run_id)
            WHERE (SELECT status FROM outcome_review_events e WHERE e.outcome_id = m.outcome_id
                   ORDER BY event_id DESC LIMIT 1) IN ('ELIGIBLE', 'TRAINING_USED')
            ORDER BY r.completed_at, m.calibration_run_id, m.outcome_id
            """
        )
    )
    op.execute(
        "CREATE TRIGGER trg_actual_outcomes_review AFTER INSERT ON actual_outcomes "
        "FOR EACH ROW EXECUTE FUNCTION review_new_outcome()"
    )
    op.execute(
        "CREATE TRIGGER trg_outcome_corrections_review AFTER INSERT ON outcome_corrections "
        "FOR EACH ROW EXECUTE FUNCTION review_corrected_outcome()"
    )
    op.execute(
        "CREATE TRIGGER trg_calibration_run_observations_training_use AFTER INSERT "
        "ON calibration_run_observations FOR EACH ROW EXECUTE FUNCTION record_training_use()"
    )

    # --- Risk decision audit ------------------------------------------------
    op.create_table(
        "risk_decision_records",
        sa.Column("decision_record_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "request_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("financing_requests.request_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("risk_assessment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "assessed_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.user_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("actor_role", sa.Text(), nullable=False),
        sa.Column("request_scope", sa.Text(), nullable=False),
        sa.Column("input_sha256", sa.Text(), nullable=False),
        sa.Column("input_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("engine_version", sa.Text(), nullable=False),
        sa.Column("raw_score", postgresql.DOUBLE_PRECISION(), nullable=False),
        sa.Column("final_score", postgresql.DOUBLE_PRECISION(), nullable=False),
        sa.Column("band", sa.Text(), nullable=False),
        sa.Column(
            "calibration_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("calibration_runs.calibration_run_id", ondelete="RESTRICT"),
        ),
        sa.Column("calibration_artifact_sha256", sa.Text()),
        sa.Column("model_scope", sa.Text()),
        sa.Column("scope_result", sa.Text(), nullable=False),
        sa.Column("scope_reason", sa.Text(), nullable=False),
        sa.Column(
            "attempted_calibration_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("calibration_runs.calibration_run_id", ondelete="RESTRICT"),
        ),
        sa.Column("fallback_code", sa.Text()),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("risk_assessment_id", name="uq_risk_decision_records_assessment"),
        sa.CheckConstraint(
            "scope_result IN ('allow', 'allow_with_warning', 'reject', 'no_active_model')",
            name="ck_risk_decision_records_scope_result",
        ),
        sa.CheckConstraint(
            "band IN ('low', 'medium', 'high')", name="ck_risk_decision_records_band"
        ),
        sa.CheckConstraint(
            "raw_score BETWEEN 0 AND 1 AND final_score BETWEEN 0 AND 1",
            name="ck_risk_decision_records_scores",
        ),
        sa.CheckConstraint(
            "input_sha256 ~ '^[0-9a-f]{64}$' AND (calibration_artifact_sha256 IS NULL "
            "OR calibration_artifact_sha256 ~ '^[0-9a-f]{64}$')",
            name="ck_risk_decision_records_hashes",
        ),
        sa.CheckConstraint(
            "(calibration_run_id IS NULL) = (calibration_artifact_sha256 IS NULL) "
            "AND (calibration_run_id IS NULL OR scope_result IN ('allow', 'allow_with_warning'))",
            name="ck_risk_decision_records_model_contract",
        ),
    )
    for name, columns in (
        ("request_id", ["request_id", "recorded_at"]),
        ("assessed_by_user_id", ["assessed_by_user_id"]),
        ("calibration_run_id", ["calibration_run_id"]),
        ("attempted_calibration_run_id", ["attempted_calibration_run_id"]),
        ("recorded_at", ["recorded_at"]),
    ):
        op.create_index(f"ix_risk_decision_records_{name}", "risk_decision_records", columns)

    for table_name in _NEW_TABLES:
        op.execute(
            f"CREATE TRIGGER trg_{table_name}_immutable BEFORE UPDATE OR DELETE ON {table_name} "
            "FOR EACH ROW EXECUTE FUNCTION reject_governed_history_mutation()"
        )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "LOCK TABLE calibration_runs, actual_outcomes, outcome_corrections, "
            "calibration_run_observations, model_versions, ledger_events, "
            + ", ".join(_NEW_TABLES)
            + " IN ACCESS EXCLUSIVE MODE"
        )
    )
    blocked = bind.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM actual_outcomes WHERE revision > 1)"
            " OR EXISTS (SELECT 1 FROM calibration_runs WHERE retired_at IS NOT NULL"
            " OR rolled_back_at IS NOT NULL)"
            " OR EXISTS (SELECT 1 FROM risk_decision_records)"
            " OR EXISTS (SELECT 1 FROM ledger_events WHERE "
            + _in("event_type", _GOVERNANCE_EVENT_TYPES)
            + ")"
        )
    )
    if blocked:
        raise RuntimeError(
            "Cannot downgrade while model or outcome governance history from revision "
            "20260925_0016 exists"
        )
    for table_name in _NEW_TABLES:
        op.execute(f"DROP TRIGGER trg_{table_name}_immutable ON {table_name}")
    op.drop_table("risk_decision_records")
    op.execute("DROP TRIGGER trg_calibration_run_observations_training_use ON calibration_run_observations")
    op.execute("DROP TRIGGER trg_outcome_corrections_review ON outcome_corrections")
    op.execute("DROP TRIGGER trg_actual_outcomes_review ON actual_outcomes")
    op.execute("DROP FUNCTION record_training_use()")
    op.execute("DROP FUNCTION review_corrected_outcome()")
    op.execute("DROP FUNCTION review_new_outcome()")
    op.execute("DROP FUNCTION review_outcome(uuid, uuid)")
    op.execute("DROP FUNCTION outcome_eligibility_reason(uuid)")
    op.drop_table("outcome_review_events")
    op.execute("DROP TRIGGER trg_actual_outcomes_supersession ON actual_outcomes")
    op.execute("DROP FUNCTION check_outcome_supersession()")
    op.drop_constraint("ck_actual_outcomes_revision_chain", "actual_outcomes", type_="check")
    op.drop_constraint("uq_actual_outcomes_supersedes_outcome_id", "actual_outcomes", type_="unique")
    for column in ("facility_id", "request_id", "risk_assessment_id"):
        op.drop_constraint(f"uq_actual_outcomes_{column}_revision", "actual_outcomes", type_="unique")
        op.create_unique_constraint(f"actual_outcomes_{column}_key", "actual_outcomes", [column])
    for column in ("correction_comment", "correction_reason_code", "supersedes_outcome_id", "revision"):
        op.drop_column("actual_outcomes", column)

    for table_name in ("calibration_runs", "model_versions"):
        op.execute(f"DROP TRIGGER trg_{table_name}_active_delete ON {table_name}")
        op.execute(f"DROP FUNCTION reject_active_{table_name}_delete()")
    op.execute("DROP TRIGGER trg_calibration_runs_validated_promotion ON calibration_runs")
    op.execute("DROP TRIGGER trg_calibration_runs_registry_log ON calibration_runs")
    op.execute("DROP FUNCTION require_validated_promotion()")
    op.execute("DROP FUNCTION log_calibration_registry_event()")
    op.execute("DROP FUNCTION governance_actor()")
    op.execute("DROP FUNCTION calibration_registry_status(text, text, timestamptz, timestamptz)")
    op.drop_table("model_registry_events")
    op.drop_constraint("ck_calibration_runs_registry_contract", "calibration_runs", type_="check")
    op.drop_index("ix_calibration_runs_retired_by_user_id", table_name="calibration_runs")
    for column in ("retirement_reason", "retired_by_user_id", "retired_at", "rolled_back_at"):
        op.drop_column("calibration_runs", column)

    op.drop_constraint("ck_ledger_events_event_type", "ledger_events", type_="check")
    op.create_check_constraint(
        "ck_ledger_events_event_type", "ledger_events", _in("event_type", _PREVIOUS_EVENT_TYPES)
    )
