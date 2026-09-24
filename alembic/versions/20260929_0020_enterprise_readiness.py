"""Enterprise readiness: tenant ownership, audit grants, login security, config center.

- ``organizations.status`` (active / suspended); users of a suspended
  organization cannot sign in.
- ``financing_facilities.organization_id``: the lending organization that owns
  the facility. Backfilled from the creating user's organization (the rule the
  application already used), then NOT NULL. Alerts, tasks and outcomes are
  owned through it.
- ``audit_grants``: which organizations an auditor may see. Revocation is an
  append-only fact (``revoked_at``), never a delete.
- ``users``: failed-login counter and lock-out time; ``user_sessions``:
  ``last_seen_at`` for idle expiry.
- ``security_events`` (immutable): sign-in success/failure/lock-out, logout,
  session expiry, permission denials and administrative actions.
- ``system_config`` (append-only versions): lightweight configuration center
  with author, reason and rollback lineage.
"""

from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260929_0020"
down_revision: str | None = "20260928_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SECURITY_EVENTS = (
    "LOGIN_SUCCESS",
    "LOGIN_FAILURE",
    "LOGIN_LOCKED",
    "LOGOUT",
    "SESSION_EXPIRED",
    "PERMISSION_DENIED",
    "ADMIN_ACTION",
)
_LEDGER_TYPES = ("ADMIN_ACTION_RECORDED", "CONFIG_VERSIONED")


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

    # --- Tenancy -------------------------------------------------------------
    op.add_column(
        "organizations",
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
    )
    op.create_check_constraint(
        "ck_organizations_status", "organizations", "status IN ('active', 'suspended')"
    )
    op.add_column(
        "financing_facilities",
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.organization_id", ondelete="RESTRICT"),
        ),
    )
    bind.execute(
        sa.text(
            "UPDATE financing_facilities f SET organization_id = u.organization_id "
            "FROM users u WHERE u.user_id = f.created_by_user_id"
        )
    )
    # The backfill queues the facility table's deferred integrity triggers;
    # run them now (they still validate every row) so ALTER TABLE may proceed.
    bind.execute(sa.text("SET CONSTRAINTS ALL IMMEDIATE"))
    op.alter_column("financing_facilities", "organization_id", nullable=False)
    op.create_index(
        "ix_financing_facilities_organization_id", "financing_facilities", ["organization_id"]
    )
    op.execute(
        """
        CREATE FUNCTION reject_facility_owner_change() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.organization_id IS DISTINCT FROM OLD.organization_id THEN
                RAISE EXCEPTION 'facility ownership is immutable'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_financing_facilities_owner BEFORE UPDATE ON financing_facilities "
        "FOR EACH ROW EXECUTE FUNCTION reject_facility_owner_change()"
    )
    op.execute(
        """
        CREATE FUNCTION default_facility_owner() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            -- The lender is the creating user's organization unless stated.
            IF NEW.organization_id IS NULL THEN
                SELECT organization_id INTO NEW.organization_id FROM users
                WHERE user_id = NEW.created_by_user_id;
            END IF;
            RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_financing_facilities_default_owner BEFORE INSERT ON financing_facilities "
        "FOR EACH ROW EXECUTE FUNCTION default_facility_owner()"
    )

    op.create_table(
        "audit_grants",
        sa.Column("grant_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "auditor_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.user_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.organization_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "granted_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.user_id", ondelete="RESTRICT"),
        ),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column(
            "revoked_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.user_id", ondelete="RESTRICT"),
        ),
        sa.CheckConstraint(
            "(revoked_at IS NULL) = (revoked_by_user_id IS NULL)",
            name="ck_audit_grants_revocation",
        ),
    )
    op.create_index(
        "uq_audit_grants_active",
        "audit_grants",
        ["auditor_user_id", "organization_id"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    for column in ("organization_id", "granted_by_user_id", "revoked_by_user_id"):
        op.create_index(f"ix_audit_grants_{column}", "audit_grants", [column])
    op.execute(
        """
        CREATE FUNCTION guard_audit_grant() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' OR OLD.revoked_at IS NOT NULL
               OR NEW.grant_id <> OLD.grant_id OR NEW.auditor_user_id <> OLD.auditor_user_id
               OR NEW.organization_id <> OLD.organization_id
               OR NEW.granted_at <> OLD.granted_at OR NEW.reason <> OLD.reason
               OR NEW.granted_by_user_id IS DISTINCT FROM OLD.granted_by_user_id THEN
                RAISE EXCEPTION 'audit grants are append-only; only a revocation may be recorded'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_audit_grants_guard BEFORE UPDATE OR DELETE ON audit_grants "
        "FOR EACH ROW EXECUTE FUNCTION guard_audit_grant()"
    )
    # Existing auditors keep exactly what they saw: every lending organization.
    bind.execute(
        sa.text(
            "INSERT INTO audit_grants (grant_id, auditor_user_id, organization_id, reason, granted_at) "
            "SELECT gen_random_uuid(), a.user_id, o.organization_id, 'migration_existing_scope', now() "
            "FROM users a CROSS JOIN organizations o "
            "WHERE a.role = 'auditor' AND o.organization_type = 'financier'"
        )
    )

    # --- Login security ------------------------------------------------------
    op.add_column(
        "users",
        sa.Column("failed_login_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("users", sa.Column("locked_until", sa.DateTime(timezone=True)))
    op.create_check_constraint(
        "ck_users_failed_login_count", "users", "failed_login_count >= 0"
    )
    op.add_column("user_sessions", sa.Column("last_seen_at", sa.DateTime(timezone=True)))
    bind.execute(sa.text("UPDATE user_sessions SET last_seen_at = created_at"))
    op.alter_column("user_sessions", "last_seen_at", nullable=False)

    op.create_table(
        "security_events",
        sa.Column("event_id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.user_id", ondelete="RESTRICT"),
        ),
        sa.Column("username", sa.Text()),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.organization_id", ondelete="RESTRICT"),
        ),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("resource_type", sa.Text()),
        sa.Column("resource_id", sa.Text()),
        sa.Column("detail", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("client_ip", sa.Text()),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.CheckConstraint(_in("event_type", _SECURITY_EVENTS), name="ck_security_events_type"),
    )
    for column in ("user_id", "organization_id", "recorded_at"):
        op.create_index(f"ix_security_events_{column}", "security_events", [column])
    op.create_index(
        "ix_security_events_type_recorded_at", "security_events", ["event_type", "recorded_at"]
    )
    op.execute(
        "CREATE TRIGGER trg_security_events_immutable BEFORE UPDATE OR DELETE ON security_events "
        "FOR EACH ROW EXECUTE FUNCTION reject_governed_history_mutation()"
    )

    # --- Configuration center ------------------------------------------------
    op.create_table(
        "system_config",
        sa.Column("config_key", sa.Text(), primary_key=True),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("value", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.user_id", ondelete="RESTRICT"),
        ),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("change_reason", sa.Text(), nullable=False),
        sa.Column("rollback_of_version", sa.Integer()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.CheckConstraint("version >= 1", name="ck_system_config_version"),
        sa.CheckConstraint(
            "rollback_of_version IS NULL OR rollback_of_version < version",
            name="ck_system_config_rollback",
        ),
    )
    op.create_index(
        "ix_system_config_created_by_user_id", "system_config", ["created_by_user_id"]
    )
    op.execute(
        "CREATE TRIGGER trg_system_config_immutable BEFORE UPDATE OR DELETE ON system_config "
        "FOR EACH ROW EXECUTE FUNCTION reject_governed_history_mutation()"
    )

    previous_ledger = _ledger_check(bind)
    assert "'RISK_RULE_VERSIONED'" in previous_ledger
    op.drop_constraint("ck_ledger_events_event_type", "ledger_events", type_="check")
    op.execute(
        "ALTER TABLE ledger_events ADD CONSTRAINT ck_ledger_events_event_type "
        + previous_ledger.replace(
            "'RISK_RULE_VERSIONED'::text",
            "'RISK_RULE_VERSIONED'::text, "
            + ", ".join(f"'{name}'::text" for name in _LEDGER_TYPES),
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    blocked = bind.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM security_events) OR EXISTS (SELECT 1 FROM system_config) "
            "OR EXISTS (SELECT 1 FROM audit_grants WHERE reason <> 'migration_existing_scope' "
            "OR revoked_at IS NOT NULL) "
            "OR EXISTS (SELECT 1 FROM organizations WHERE status <> 'active')"
        )
    )
    if blocked:
        raise RuntimeError(
            "Cannot downgrade while enterprise history from revision 20260929_0020 exists"
        )
    current = _ledger_check(bind)
    op.drop_constraint("ck_ledger_events_event_type", "ledger_events", type_="check")
    op.execute(
        "ALTER TABLE ledger_events ADD CONSTRAINT ck_ledger_events_event_type "
        + current.replace(", " + ", ".join(f"'{name}'::text" for name in _LEDGER_TYPES), "")
    )
    op.execute("DROP TRIGGER trg_system_config_immutable ON system_config")
    op.drop_table("system_config")
    op.execute("DROP TRIGGER trg_security_events_immutable ON security_events")
    op.drop_table("security_events")
    op.drop_column("user_sessions", "last_seen_at")
    op.drop_constraint("ck_users_failed_login_count", "users", type_="check")
    op.drop_column("users", "locked_until")
    op.drop_column("users", "failed_login_count")
    op.execute("DROP TRIGGER trg_audit_grants_guard ON audit_grants")
    op.execute("DROP FUNCTION guard_audit_grant()")
    op.drop_table("audit_grants")
    op.execute("DROP TRIGGER trg_financing_facilities_owner ON financing_facilities")
    op.execute("DROP FUNCTION reject_facility_owner_change()")
    op.execute("DROP TRIGGER trg_financing_facilities_default_owner ON financing_facilities")
    op.execute("DROP FUNCTION default_facility_owner()")
    op.drop_index("ix_financing_facilities_organization_id", table_name="financing_facilities")
    op.drop_column("financing_facilities", "organization_id")
    op.drop_constraint("ck_organizations_status", "organizations", type_="check")
    op.drop_column("organizations", "status")
