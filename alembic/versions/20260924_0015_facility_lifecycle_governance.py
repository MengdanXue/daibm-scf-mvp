"""Govern the facility lifecycle: disposal, recovery, contract versions and transitions.

Adds the risk-disposal, recovery and recovered-after-default stages, an
append-only status-transition history, immutable contract versions, governed
disposal/recovery decisions, and recovery cash. PostgreSQL itself now rejects
an illegal status jump and any status change without an audited transition.

Existing rows are never rewritten. The backfill only appends: one
``migration_baseline`` transition per existing facility and one
``migration_backfill`` contract version per existing schedule version, built
from installment contract terms (sequence, due date, amount), which historical
code never mutated.
"""

from __future__ import annotations

import hashlib
import json
from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260924_0015"
down_revision: str | None = "20260908_0014"
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
)
_LIFECYCLE_EVENT_TYPES = (
    "FACILITY_OVERDUE_CURED",
    "FACILITY_DISPOSAL_OPENED",
    "FACILITY_DISPOSAL_CLOSED",
    "FACILITY_RECOVERY_STARTED",
    "FACILITY_RECOVERY_RECORDED",
    "FACILITY_RECOVERED",
)

_PREVIOUS_STATUSES = (
    "ready_for_disbursement",
    "disbursed",
    "active",
    "overdue",
    "restructured",
    "defaulted",
    "repaid",
    "written_off",
    "closed",
)
_STATUSES = (
    "ready_for_disbursement",
    "disbursed",
    "active",
    "overdue",
    "in_disposal",
    "restructured",
    "defaulted",
    "in_recovery",
    "repaid",
    "recovered",
    "written_off",
    "closed",
)
_NEW_STATUSES = ("in_disposal", "in_recovery", "recovered")

_PREVIOUS_ACTIONS = (
    "create",
    "initiate_disbursement",
    "confirm_disbursement",
    "submit_payment",
    "confirm_payment",
    "reject_payment",
    "confirm_final_payment",
    "mark_overdue",
    "restructure",
    "declare_default",
    "write_off",
    "close",
)
_NEW_ACTIONS = (
    "open_disposal",
    "close_disposal",
    "start_recovery",
    "record_recovery",
    "record_final_recovery",
)

# Frozen copy of ``app.domain.facility.allowed_status_changes()`` at this
# revision. ``tests/test_facility_lifecycle_governance.py`` pins the equality.
_ALLOWED_STATUS_CHANGES = (
    ("active", "overdue"),
    ("active", "repaid"),
    ("defaulted", "in_recovery"),
    ("defaulted", "recovered"),
    ("defaulted", "restructured"),
    ("disbursed", "active"),
    ("in_disposal", "active"),
    ("in_disposal", "defaulted"),
    ("in_disposal", "recovered"),
    ("in_disposal", "repaid"),
    ("in_disposal", "restructured"),
    ("in_recovery", "recovered"),
    ("in_recovery", "written_off"),
    ("overdue", "active"),
    ("overdue", "in_disposal"),
    ("overdue", "recovered"),
    ("overdue", "repaid"),
    ("overdue", "restructured"),
    ("ready_for_disbursement", "disbursed"),
    ("recovered", "closed"),
    ("repaid", "closed"),
    ("restructured", "overdue"),
    ("restructured", "recovered"),
    ("restructured", "repaid"),
    ("written_off", "closed"),
)

_HISTORY_TABLES = (
    "facility_status_transitions",
    "facility_contract_versions",
    "facility_lifecycle_decisions",
    "facility_recoveries",
)

_PREVIOUS_CONSERVATION = """f.principal <> f.outstanding_amount
        + COALESCE((SELECT sum(p.amount) FROM facility_payments p
                    WHERE p.facility_id = f.facility_id AND p.status = 'confirmed'), 0)
        + COALESCE((SELECT sum(w.amount) FROM facility_writeoffs w
                    WHERE w.facility_id = f.facility_id), 0)
"""
_CONSERVATION = """f.principal <> f.outstanding_amount
        + COALESCE((SELECT sum(p.amount) FROM facility_payments p
                    WHERE p.facility_id = f.facility_id AND p.status = 'confirmed'), 0)
        + COALESCE((SELECT sum(r.amount) FROM facility_recoveries r
                    WHERE r.facility_id = f.facility_id AND r.applied_to = 'outstanding'), 0)
        + COALESCE((SELECT sum(w.amount) FROM facility_writeoffs w
                    WHERE w.facility_id = f.facility_id), 0)
"""


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN (" + ", ".join(f"'{value}'" for value in values) + ")"


def _check_money_function(conservation: str) -> str:
    return (
        """
        CREATE OR REPLACE FUNCTION check_facility_money() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE ids uuid[]; fid uuid;
        BEGIN
            IF TG_OP = 'INSERT' THEN ids := ARRAY[NEW.facility_id];
            ELSIF TG_OP = 'DELETE' THEN ids := ARRAY[OLD.facility_id];
            ELSE ids := ARRAY[OLD.facility_id, NEW.facility_id]; END IF;
            FOR fid IN SELECT DISTINCT unnest(ids) ORDER BY 1 LOOP
                IF EXISTS (SELECT 1 FROM financing_facilities f
                           WHERE f.facility_id = fid AND ("""
        + conservation
        + """)) THEN
                    RAISE EXCEPTION 'facility % principal conservation failed', fid
                        USING ERRCODE = '23514';
                END IF;
            END LOOP;
            RETURN NULL;
        END $$
        """
    )


def _terms_sha256(material: dict) -> str:
    encoded = json.dumps(
        material, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "LOCK TABLE financing_facilities, facility_installments, facility_actions, "
            "facility_payments, facility_writeoffs, ledger_events IN ACCESS EXCLUSIVE MODE"
        )
    )

    op.drop_constraint("ck_financing_facilities_status", "financing_facilities", type_="check")
    op.create_check_constraint(
        "ck_financing_facilities_status", "financing_facilities", _in("status", _STATUSES)
    )
    op.drop_constraint("ck_facility_actions_action_type", "facility_actions", type_="check")
    op.create_check_constraint(
        "ck_facility_actions_action_type",
        "facility_actions",
        _in("action_type", _PREVIOUS_ACTIONS + _NEW_ACTIONS),
    )
    op.drop_constraint("ck_ledger_events_event_type", "ledger_events", type_="check")
    op.create_check_constraint(
        "ck_ledger_events_event_type",
        "ledger_events",
        _in("event_type", _PREVIOUS_EVENT_TYPES + _LIFECYCLE_EVENT_TYPES),
    )

    op.create_table(
        "facility_status_transitions",
        sa.Column("transition_id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column(
            "facility_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("financing_facilities.facility_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("from_status", sa.Text()),
        sa.Column("to_status", sa.Text(), nullable=False),
        sa.Column("trigger_action", sa.Text(), nullable=False),
        sa.Column(
            "actor_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.user_id", ondelete="RESTRICT"),
        ),
        sa.Column("actor_role", sa.Text()),
        sa.Column("resulting_version", sa.Integer(), nullable=False),
        sa.Column("reason_code", sa.Text()),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "from_status IS NULL OR " + _in("from_status", _STATUSES),
            name="ck_facility_status_transitions_from",
        ),
        sa.CheckConstraint(_in("to_status", _STATUSES), name="ck_facility_status_transitions_to"),
        sa.CheckConstraint(
            "from_status IS DISTINCT FROM to_status",
            name="ck_facility_status_transitions_changes_status",
        ),
        sa.CheckConstraint(
            "resulting_version >= 1", name="ck_facility_status_transitions_version"
        ),
        sa.CheckConstraint(
            "(actor_user_id IS NULL) = (trigger_action = 'migration_baseline')",
            name="ck_facility_status_transitions_actor",
        ),
    )
    op.create_index(
        "ix_facility_status_transitions_facility_id",
        "facility_status_transitions",
        ["facility_id", "transition_id"],
    )
    op.create_index(
        "ix_facility_status_transitions_actor_user_id",
        "facility_status_transitions",
        ["actor_user_id"],
    )
    op.create_index(
        "ix_facility_status_transitions_recorded_at",
        "facility_status_transitions",
        ["recorded_at"],
    )

    op.create_table(
        "facility_contract_versions",
        sa.Column("contract_version_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "facility_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("financing_facilities.facility_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("contract_version", sa.Integer(), nullable=False),
        sa.Column("origin", sa.Text(), nullable=False),
        sa.Column("principal", sa.Numeric(14, 2), nullable=False),
        sa.Column("outstanding_at_start", sa.Numeric(14, 2)),
        sa.Column("currency", sa.Text(), nullable=False),
        sa.Column("schedule", postgresql.JSONB(), nullable=False),
        sa.Column("superseded_schedule", postgresql.JSONB()),
        sa.Column(
            "restructure_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("facility_restructures.restructure_id", ondelete="RESTRICT"),
        ),
        sa.Column("terms_sha256", sa.Text(), nullable=False),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.user_id", ondelete="RESTRICT"),
        ),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "facility_id", "contract_version", name="uq_facility_contract_versions_version"
        ),
        sa.CheckConstraint("contract_version >= 1", name="ck_facility_contract_versions_version"),
        sa.CheckConstraint(
            "origin IN ('origination', 'restructure', 'migration_backfill')",
            name="ck_facility_contract_versions_origin",
        ),
        sa.CheckConstraint(
            "(origin = 'restructure') = (restructure_id IS NOT NULL)",
            name="ck_facility_contract_versions_restructure_link",
        ),
        sa.CheckConstraint(
            "outstanding_at_start IS NULL OR outstanding_at_start > 0",
            name="ck_facility_contract_versions_outstanding",
        ),
        sa.CheckConstraint(
            "terms_sha256 ~ '^[0-9a-f]{64}$'", name="ck_facility_contract_versions_hash"
        ),
    )
    op.create_index(
        "ix_facility_contract_versions_restructure_id",
        "facility_contract_versions",
        ["restructure_id"],
    )
    op.create_index(
        "ix_facility_contract_versions_created_by_user_id",
        "facility_contract_versions",
        ["created_by_user_id"],
    )
    op.create_index(
        "ix_facility_contract_versions_recorded_at",
        "facility_contract_versions",
        ["recorded_at"],
    )

    op.create_table(
        "facility_lifecycle_decisions",
        sa.Column("decision_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "facility_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("financing_facilities.facility_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("decision_type", sa.Text(), nullable=False),
        sa.Column("schedule_version", sa.Integer(), nullable=False),
        sa.Column(
            "decided_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.user_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("reason_code", sa.Text(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("evidence_sha256", sa.Text(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "decision_type IN ('disposal_opened', 'disposal_closed', 'recovery_started')",
            name="ck_facility_lifecycle_decisions_type",
        ),
        sa.CheckConstraint(
            "schedule_version >= 1", name="ck_facility_lifecycle_decisions_schedule"
        ),
        sa.CheckConstraint(
            "evidence_sha256 ~ '^[0-9a-f]{64}$'", name="ck_facility_lifecycle_decisions_hash"
        ),
    )
    op.create_index(
        "ix_facility_lifecycle_decisions_facility_id",
        "facility_lifecycle_decisions",
        ["facility_id", "recorded_at"],
    )
    op.create_index(
        "ix_facility_lifecycle_decisions_decided_by_user_id",
        "facility_lifecycle_decisions",
        ["decided_by_user_id"],
    )
    op.create_index(
        "ix_facility_lifecycle_decisions_recorded_at",
        "facility_lifecycle_decisions",
        ["recorded_at"],
    )

    op.create_table(
        "facility_recoveries",
        sa.Column("recovery_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "facility_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("financing_facilities.facility_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("applied_to", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("recovery_reference", sa.Text(), nullable=False),
        sa.Column("evidence_sha256", sa.Text(), nullable=False),
        sa.Column(
            "recorded_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.user_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount > 0", name="ck_facility_recoveries_amount"),
        sa.CheckConstraint(
            "applied_to IN ('outstanding', 'written_off')",
            name="ck_facility_recoveries_applied_to",
        ),
        sa.CheckConstraint(
            "source IN ('GUARANTOR', 'COLLATERAL', 'CORE_ENTERPRISE_BUYBACK', "
            "'LEGAL_ENFORCEMENT', 'COLLECTION_AGENCY', 'INSURANCE', 'OTHER')",
            name="ck_facility_recoveries_source",
        ),
        sa.CheckConstraint(
            "evidence_sha256 ~ '^[0-9a-f]{64}$'", name="ck_facility_recoveries_hash"
        ),
        sa.UniqueConstraint(
            "facility_id",
            "recovery_reference",
            name="uq_facility_recoveries_facility_reference",
        ),
    )
    op.create_index(
        "ix_facility_recoveries_facility_id", "facility_recoveries", ["facility_id", "recorded_at"]
    )
    op.create_index(
        "ix_facility_recoveries_recorded_by_user_id",
        "facility_recoveries",
        ["recorded_by_user_id"],
    )
    op.create_index("ix_facility_recoveries_recorded_at", "facility_recoveries", ["recorded_at"])

    # Append-only history: no row in these tables, nor in the command log, may
    # be rewritten or deleted.
    for table_name in _HISTORY_TABLES + ("facility_actions",):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_immutable
            BEFORE UPDATE OR DELETE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION reject_governed_history_mutation()
            """
        )

    # Recovery cash applied to the live balance joins principal conservation.
    op.execute(_check_money_function(_CONSERVATION))
    op.execute(
        "CREATE TRIGGER trg_facility_recoveries_money_lock BEFORE INSERT OR UPDATE OR DELETE "
        "ON facility_recoveries FOR EACH ROW EXECUTE FUNCTION serialize_facility_money()"
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER trg_facility_recoveries_money_conservation AFTER INSERT OR "
        "UPDATE OR DELETE ON facility_recoveries DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "EXECUTE FUNCTION check_facility_money()"
    )

    # Contract terms of an installment are part of a contract version and never
    # change; a superseded installment is frozen entirely.
    op.execute(
        """
        CREATE FUNCTION guard_installment_contract_terms() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'facility installments are contract history and cannot be deleted'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            IF NEW.installment_id <> OLD.installment_id
               OR NEW.facility_id <> OLD.facility_id
               OR NEW.sequence <> OLD.sequence
               OR NEW.schedule_version <> OLD.schedule_version
               OR NEW.due_date <> OLD.due_date
               OR NEW.amount <> OLD.amount
               OR NEW.created_at <> OLD.created_at THEN
                RAISE EXCEPTION 'installment contract terms are immutable'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            IF OLD.status = 'superseded' AND (
                NEW.status <> 'superseded' OR NEW.paid_amount <> OLD.paid_amount
            ) THEN
                RAISE EXCEPTION 'superseded installments are frozen contract history'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_facility_installments_contract_terms BEFORE UPDATE OR DELETE "
        "ON facility_installments FOR EACH ROW EXECUTE FUNCTION guard_installment_contract_terms()"
    )

    allowed_pairs = ", ".join(
        f"('{source}', '{target}')" for source, target in _ALLOWED_STATUS_CHANGES
    )
    op.execute(
        f"""
        CREATE FUNCTION guard_facility_status_transition() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.status IS DISTINCT FROM OLD.status AND NOT EXISTS (
                SELECT 1 FROM (VALUES {allowed_pairs}) AS allowed(from_status, to_status)
                WHERE allowed.from_status = OLD.status AND allowed.to_status = NEW.status
            ) THEN
                RAISE EXCEPTION 'illegal facility status transition % -> %', OLD.status, NEW.status
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_financing_facilities_status_guard BEFORE UPDATE OF status "
        "ON financing_facilities FOR EACH ROW EXECUTE FUNCTION guard_facility_status_transition()"
    )
    op.execute(
        """
        CREATE FUNCTION require_facility_transition_audit() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.status IS DISTINCT FROM OLD.status AND NOT EXISTS (
                SELECT 1 FROM facility_status_transitions t
                WHERE t.facility_id = NEW.facility_id
                  AND t.from_status = OLD.status
                  AND t.to_status = NEW.status
                  AND t.resulting_version = NEW.version
            ) THEN
                RAISE EXCEPTION 'facility % status change % -> % has no audited transition',
                    NEW.facility_id, OLD.status, NEW.status
                    USING ERRCODE = '23514';
            END IF;
            RETURN NULL;
        END $$
        """
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER trg_financing_facilities_transition_audit AFTER UPDATE "
        "ON financing_facilities DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "EXECUTE FUNCTION require_facility_transition_audit()"
    )

    # Append-only backfill of history that predates this revision.
    bind.execute(
        sa.text(
            "INSERT INTO facility_status_transitions "
            "(facility_id, from_status, to_status, trigger_action, actor_user_id, actor_role, "
            "resulting_version, reason_code, recorded_at) "
            "SELECT facility_id, NULL, status, 'migration_baseline', NULL, NULL, version, "
            "'STATE_BEFORE_TRANSITION_HISTORY', now() FROM financing_facilities "
            "ORDER BY created_at, facility_id"
        )
    )
    facilities = bind.execute(
        sa.text("SELECT facility_id, principal, currency FROM financing_facilities")
    ).all()
    for facility_id, principal, currency in facilities:
        rows = bind.execute(
            sa.text(
                "SELECT schedule_version, sequence, due_date, amount FROM facility_installments "
                "WHERE facility_id = :facility_id ORDER BY schedule_version, sequence"
            ),
            {"facility_id": facility_id},
        ).all()
        versions: dict[int, list[dict[str, str | int]]] = {}
        for schedule_version, sequence, due_date, amount in rows:
            versions.setdefault(schedule_version, []).append(
                {"sequence": sequence, "due_date": due_date.isoformat(), "amount": f"{amount:.2f}"}
            )
        for schedule_version, schedule in sorted(versions.items()):
            material = {
                "facility_id": str(facility_id),
                "contract_version": schedule_version,
                "principal": f"{principal:.2f}",
                "outstanding_at_start": None,
                "currency": currency,
                "schedule": schedule,
            }
            bind.execute(
                sa.text(
                    "INSERT INTO facility_contract_versions (contract_version_id, facility_id, "
                    "contract_version, origin, principal, outstanding_at_start, currency, schedule, "
                    "superseded_schedule, restructure_id, terms_sha256, created_by_user_id, "
                    "recorded_at) VALUES (gen_random_uuid(), :facility_id, :version, "
                    "'migration_backfill', :principal, NULL, :currency, CAST(:schedule AS jsonb), "
                    "NULL, NULL, :terms_sha256, NULL, now())"
                ),
                {
                    "facility_id": facility_id,
                    "version": schedule_version,
                    "principal": principal,
                    "currency": currency,
                    "schedule": json.dumps(schedule),
                    "terms_sha256": _terms_sha256(material),
                },
            )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "LOCK TABLE financing_facilities, facility_installments, facility_actions, "
            "facility_payments, facility_writeoffs, ledger_events, "
            + ", ".join(_HISTORY_TABLES)
            + " IN ACCESS EXCLUSIVE MODE"
        )
    )
    blocked = bind.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM financing_facilities WHERE "
            + _in("status", _NEW_STATUSES)
            + ") OR EXISTS (SELECT 1 FROM facility_actions WHERE "
            + _in("action_type", _NEW_ACTIONS)
            + ") OR EXISTS (SELECT 1 FROM ledger_events WHERE "
            + _in("event_type", _LIFECYCLE_EVENT_TYPES)
            + ") OR EXISTS (SELECT 1 FROM facility_recoveries)"
            " OR EXISTS (SELECT 1 FROM facility_lifecycle_decisions)"
            " OR EXISTS (SELECT 1 FROM facility_contract_versions WHERE origin <> 'migration_backfill')"
            " OR EXISTS (SELECT 1 FROM facility_status_transitions "
            "WHERE trigger_action <> 'migration_baseline')"
        )
    )
    if blocked:
        raise RuntimeError(
            "Cannot downgrade while governed lifecycle history from revision 20260924_0015 exists"
        )

    op.execute("DROP TRIGGER trg_financing_facilities_transition_audit ON financing_facilities")
    op.execute("DROP FUNCTION require_facility_transition_audit()")
    op.execute("DROP TRIGGER trg_financing_facilities_status_guard ON financing_facilities")
    op.execute("DROP FUNCTION guard_facility_status_transition()")
    op.execute("DROP TRIGGER trg_facility_installments_contract_terms ON facility_installments")
    op.execute("DROP FUNCTION guard_installment_contract_terms()")
    op.execute("DROP TRIGGER trg_facility_recoveries_money_conservation ON facility_recoveries")
    op.execute("DROP TRIGGER trg_facility_recoveries_money_lock ON facility_recoveries")
    op.execute(_check_money_function(_PREVIOUS_CONSERVATION))
    for table_name in _HISTORY_TABLES + ("facility_actions",):
        op.execute(f"DROP TRIGGER trg_{table_name}_immutable ON {table_name}")
    for table_name in reversed(_HISTORY_TABLES):
        op.drop_table(table_name)

    op.drop_constraint("ck_ledger_events_event_type", "ledger_events", type_="check")
    op.create_check_constraint(
        "ck_ledger_events_event_type", "ledger_events", _in("event_type", _PREVIOUS_EVENT_TYPES)
    )
    op.drop_constraint("ck_facility_actions_action_type", "facility_actions", type_="check")
    op.create_check_constraint(
        "ck_facility_actions_action_type", "facility_actions", _in("action_type", _PREVIOUS_ACTIONS)
    )
    op.drop_constraint("ck_financing_facilities_status", "financing_facilities", type_="check")
    op.create_check_constraint(
        "ck_financing_facilities_status",
        "financing_facilities",
        _in("status", _PREVIOUS_STATUSES),
    )
