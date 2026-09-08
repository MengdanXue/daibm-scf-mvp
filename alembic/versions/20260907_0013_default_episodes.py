"""Preserve schedule-specific default episodes and exact principal conservation."""
from alembic import op
import sqlalchemy as sa

revision = "20260907_0013"
down_revision = "20260907_0012"
branch_labels = None
depends_on = None

_CONSERVATION = """f.principal <> f.outstanding_amount
        + COALESCE((SELECT sum(p.amount) FROM facility_payments p
                    WHERE p.facility_id = f.facility_id AND p.status = 'confirmed'), 0)
        + COALESCE((SELECT sum(w.amount) FROM facility_writeoffs w
                    WHERE w.facility_id = f.facility_id), 0)
"""


def upgrade() -> None:
    op.execute("LOCK TABLE financing_facilities, facility_defaults, facility_payments, facility_writeoffs IN ACCESS EXCLUSIVE MODE")
    inconsistent = op.get_bind().scalar(sa.text(
        "SELECT f.facility_id FROM financing_facilities f WHERE " + _CONSERVATION + " LIMIT 1"
    ))
    if inconsistent is not None:
        raise RuntimeError(
            f"Cannot upgrade: principal conservation failed for facility {inconsistent}; "
            "investigate confirmed cash, outstanding balance and writeoff history. "
            "No historical balances were repaired."
        )
    op.add_column("facility_defaults", sa.Column("schedule_version", sa.Integer(), nullable=True))
    # Historical code never allowed a defaulted facility to restructure. Only new
    # metadata is backfilled; the original event and its identifier stay intact.
    op.execute("ALTER TABLE facility_defaults DISABLE TRIGGER trg_facility_defaults_immutable")
    op.execute("UPDATE facility_defaults d SET schedule_version = f.current_schedule_version FROM financing_facilities f WHERE f.facility_id = d.facility_id")
    op.execute("ALTER TABLE facility_defaults ENABLE TRIGGER trg_facility_defaults_immutable")
    op.alter_column("facility_defaults", "schedule_version", nullable=False, server_default="1")
    op.drop_constraint("facility_defaults_facility_id_key", "facility_defaults", type_="unique")
    op.create_unique_constraint("uq_facility_defaults_schedule", "facility_defaults", ["facility_id", "schedule_version"])
    op.create_check_constraint("ck_facility_defaults_schedule", "facility_defaults", "schedule_version >= 1")
    # Touch the parent before cash changes: this both serializes direct SQL writers
    # and forces stale REPEATABLE READ snapshots to fail serialization. A lock-only
    # approach would not invalidate such a snapshot. No monetary values change.
    op.execute("""
        CREATE FUNCTION serialize_facility_money() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE ids uuid[]; fid uuid;
        BEGIN
            IF TG_OP = 'INSERT' THEN ids := ARRAY[NEW.facility_id];
            ELSIF TG_OP = 'DELETE' THEN ids := ARRAY[OLD.facility_id];
            ELSE ids := ARRAY[OLD.facility_id, NEW.facility_id]; END IF;
            FOR fid IN SELECT DISTINCT unnest(ids) ORDER BY 1 LOOP
                UPDATE financing_facilities SET outstanding_amount = outstanding_amount
                WHERE facility_id = fid;
            END LOOP;
            IF TG_OP = 'DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
        END $$
    """)
    op.execute("""
        CREATE FUNCTION check_facility_money() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE ids uuid[]; fid uuid;
        BEGIN
            IF TG_OP = 'INSERT' THEN ids := ARRAY[NEW.facility_id];
            ELSIF TG_OP = 'DELETE' THEN ids := ARRAY[OLD.facility_id];
            ELSE ids := ARRAY[OLD.facility_id, NEW.facility_id]; END IF;
            FOR fid IN SELECT DISTINCT unnest(ids) ORDER BY 1 LOOP
                IF EXISTS (SELECT 1 FROM financing_facilities f
                           WHERE f.facility_id = fid AND (""" + _CONSERVATION + """)) THEN
                    RAISE EXCEPTION 'facility % principal conservation failed', fid
                        USING ERRCODE = '23514';
                END IF;
            END LOOP;
            RETURN NULL;
        END $$
    """)
    for table in ("facility_payments", "facility_writeoffs"):
        op.execute(f"CREATE TRIGGER trg_{table}_money_lock BEFORE INSERT OR UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION serialize_facility_money()")
    for table in ("financing_facilities", "facility_payments", "facility_writeoffs"):
        op.execute(f"CREATE CONSTRAINT TRIGGER trg_{table}_money_conservation AFTER INSERT OR UPDATE OR DELETE ON {table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_facility_money()")


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("LOCK TABLE financing_facilities, facility_defaults, facility_payments, facility_writeoffs IN ACCESS EXCLUSIVE MODE"))
    if bind.scalar(sa.text("SELECT EXISTS (SELECT 1 FROM facility_defaults GROUP BY facility_id HAVING count(*) > 1)")):
        raise RuntimeError("Cannot downgrade: multiple default episodes would lose history")
    if bind.scalar(sa.text("SELECT EXISTS (SELECT 1 FROM facility_defaults d JOIN financing_facilities f USING (facility_id) WHERE d.schedule_version <> f.current_schedule_version)")):
        raise RuntimeError("Cannot downgrade: post-default restructure would lose episode attribution")
    for table in ("financing_facilities", "facility_payments", "facility_writeoffs"):
        op.execute(f"DROP TRIGGER trg_{table}_money_conservation ON {table}")
    for table in ("facility_payments", "facility_writeoffs"):
        op.execute(f"DROP TRIGGER trg_{table}_money_lock ON {table}")
    op.execute("DROP FUNCTION check_facility_money()")
    op.execute("DROP FUNCTION serialize_facility_money()")
    op.drop_constraint("ck_facility_defaults_schedule", "facility_defaults", type_="check")
    op.drop_constraint("uq_facility_defaults_schedule", "facility_defaults", type_="unique")
    op.create_unique_constraint("facility_defaults_facility_id_key", "facility_defaults", ["facility_id"])
    op.drop_column("facility_defaults", "schedule_version")
