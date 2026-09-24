"""Tenant isolation completion: application lender and organization-owned models.

- ``financing_requests.lender_organization_id``: the financier organization an
  application is addressed to. Workflow applications must have one; it can
  change only while the application is a draft. Only that organization's
  financiers and risk managers see and act on the application, and a facility
  can only be opened by it.
- ``organization_id`` on ``calibration_jobs``, ``training_dataset_snapshots``,
  ``calibration_runs`` and ``risk_model_versions``. Each organization trains on
  its own outcomes and has its own ACTIVE model per scope. The database fills
  the owner from the lineage (outcome -> facility, job -> snapshot -> run ->
  version) and rejects any row whose lineage belongs to another organization:
  snapshot items, run observations, decision records and recorded outcomes
  may only reference a model, run or snapshot of their own organization.

Backfill assigns historical rows from their own lineage. A historical run or
snapshot whose outcomes belong to more than one organization cannot be
attributed to one tenant; the upgrade refuses instead of guessing. The
governance triggers of the pipeline tables are suspended only for the owner
backfill so that it does not record synthetic state changes; no status, hash
or history value is modified.
"""

from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260930_0021"
down_revision: str | None = "20260929_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PIPELINE = (
    "calibration_jobs",
    "training_dataset_snapshots",
    "calibration_runs",
    "risk_model_versions",
)


def _refuse_if(bind, sql: str, message: str) -> None:
    count = bind.execute(sa.text(sql)).scalar_one()
    if count:
        raise RuntimeError(f"{message} ({count} row(s)); resolve them before upgrading")


def upgrade() -> None:
    bind = op.get_bind()

    # --- Application lender --------------------------------------------------
    op.add_column(
        "financing_requests",
        sa.Column(
            "lender_organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.organization_id", ondelete="RESTRICT"),
        ),
    )
    op.create_index(
        "ix_financing_requests_lender_organization_id",
        "financing_requests",
        ["lender_organization_id"],
    )
    # 1. the organization that opened a facility on it; 2. the organization of
    # the financier / risk manager who acted on it; 3. the only lender.
    bind.execute(
        sa.text(
            "UPDATE financing_requests r SET lender_organization_id = f.organization_id "
            "FROM financing_facilities f WHERE f.request_id = r.request_id"
        )
    )
    bind.execute(
        sa.text(
            """
            UPDATE financing_requests r SET lender_organization_id = x.organization_id
            FROM (
                SELECT a.request_id,
                       min(u.organization_id::text)::uuid AS organization_id,
                       count(DISTINCT u.organization_id) AS organizations
                FROM workflow_actions a JOIN users u ON u.user_id = a.actor_user_id
                WHERE a.actor_role IN ('financier', 'risk_manager')
                GROUP BY a.request_id
            ) x
            WHERE x.request_id = r.request_id AND x.organizations = 1
              AND r.lender_organization_id IS NULL
            """
        )
    )
    lenders = bind.execute(
        sa.text("SELECT organization_id FROM organizations WHERE organization_type = 'financier'")
    ).scalars().all()
    if len(lenders) == 1:
        bind.execute(
            sa.text(
                "UPDATE financing_requests SET lender_organization_id = :lender "
                "WHERE lender_organization_id IS NULL"
            ),
            {"lender": lenders[0]},
        )
    _refuse_if(
        bind,
        "SELECT count(*) FROM financing_requests "
        "WHERE supplier_organization_id IS NOT NULL AND lender_organization_id IS NULL",
        "workflow applications without a determinable lender organization",
    )
    _refuse_if(
        bind,
        "SELECT count(*) FROM financing_requests r JOIN financing_facilities f "
        "ON f.request_id = r.request_id WHERE f.organization_id <> r.lender_organization_id",
        "facilities owned by an organization other than their application's lender",
    )
    op.create_check_constraint(
        "ck_financing_requests_lender",
        "financing_requests",
        "supplier_organization_id IS NULL OR lender_organization_id IS NOT NULL",
    )
    op.execute(
        """
        CREATE FUNCTION guard_application_lender() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.lender_organization_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM organizations WHERE organization_id = NEW.lender_organization_id
                  AND organization_type = 'financier') THEN
                RAISE EXCEPTION 'application lender must be a financier organization'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            IF TG_OP = 'UPDATE' AND OLD.lender_organization_id IS NOT NULL
               AND NEW.lender_organization_id IS DISTINCT FROM OLD.lender_organization_id
               AND OLD.status <> 'draft' THEN
                RAISE EXCEPTION 'application lender is fixed once the application is submitted'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_financing_requests_lender BEFORE INSERT OR UPDATE "
        "ON financing_requests FOR EACH ROW EXECUTE FUNCTION guard_application_lender()"
    )
    op.execute(
        """
        CREATE FUNCTION require_facility_lender() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE lender uuid;
        BEGIN
            SELECT lender_organization_id INTO lender FROM financing_requests
            WHERE request_id = NEW.request_id;
            IF lender IS NOT NULL AND NEW.organization_id IS DISTINCT FROM lender THEN
                RAISE EXCEPTION 'a facility can only be opened by the application lender'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END $$
        """
    )
    # Runs after trg_financing_facilities_default_owner (triggers fire by name).
    op.execute(
        "CREATE TRIGGER trg_financing_facilities_lender_match BEFORE INSERT "
        "ON financing_facilities FOR EACH ROW EXECUTE FUNCTION require_facility_lender()"
    )

    # --- Organization-owned calibration pipeline --------------------------------
    for table in _PIPELINE:
        op.add_column(
            table,
            sa.Column(
                "organization_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("organizations.organization_id", ondelete="RESTRICT"),
            ),
        )
    op.execute(
        """
        CREATE FUNCTION outcome_organization(p_outcome uuid) RETURNS uuid
        LANGUAGE sql STABLE AS $$
            SELECT f.organization_id FROM actual_outcomes o
            JOIN financing_facilities f ON f.facility_id = o.facility_id
            WHERE o.outcome_id = p_outcome
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION calibration_job_organization(p_job uuid) RETURNS uuid
        LANGUAGE sql STABLE AS $$
            SELECT COALESCE(
                outcome_organization(j.trigger_outcome_id),
                (SELECT outcome_organization(c.outcome_id) FROM outcome_corrections c
                 WHERE c.correction_id = j.trigger_correction_id))
            FROM calibration_jobs j WHERE j.job_id = p_job
        $$
        """
    )

    for table in _PIPELINE:
        op.execute(f"ALTER TABLE {table} DISABLE TRIGGER USER")
    bind.execute(
        sa.text(
            "UPDATE calibration_jobs j SET organization_id = calibration_job_organization(j.job_id)"
        )
    )
    # Snapshots and runs: the single organization of their outcomes, else of
    # the job that produced them.
    bind.execute(
        sa.text(
            """
            UPDATE training_dataset_snapshots s SET organization_id = x.organization_id
            FROM (
                SELECT i.snapshot_id,
                       min(outcome_organization(i.outcome_id)::text)::uuid AS organization_id,
                       count(DISTINCT outcome_organization(i.outcome_id)) AS organizations
                FROM training_dataset_snapshot_items i GROUP BY i.snapshot_id
            ) x
            WHERE x.snapshot_id = s.snapshot_id AND x.organizations = 1
            """
        )
    )
    bind.execute(
        sa.text(
            "UPDATE training_dataset_snapshots s SET organization_id = j.organization_id "
            "FROM calibration_jobs j WHERE j.job_id = s.trigger_job_id "
            "AND s.organization_id IS NULL"
        )
    )
    bind.execute(
        sa.text(
            """
            UPDATE calibration_runs r SET organization_id = x.organization_id
            FROM (
                SELECT o.calibration_run_id,
                       min(outcome_organization(o.outcome_id)::text)::uuid AS organization_id,
                       count(DISTINCT outcome_organization(o.outcome_id)) AS organizations
                FROM calibration_run_observations o GROUP BY o.calibration_run_id
            ) x
            WHERE x.calibration_run_id = r.calibration_run_id AND x.organizations = 1
            """
        )
    )
    bind.execute(
        sa.text(
            """
            UPDATE calibration_runs r SET organization_id = COALESCE(
                (SELECT organization_id FROM calibration_jobs WHERE job_id = r.trigger_job_id),
                (SELECT organization_id FROM training_dataset_snapshots
                 WHERE snapshot_id = r.dataset_snapshot_id),
                outcome_organization(r.trigger_outcome_id))
            WHERE r.organization_id IS NULL
            """
        )
    )
    # Rows with no lineage at all belong to the only lending organization, if
    # there is exactly one.
    owners = bind.execute(
        sa.text("SELECT DISTINCT organization_id FROM financing_facilities")
    ).scalars().all()
    if len(owners) == 1:
        for table in ("training_dataset_snapshots", "calibration_runs"):
            bind.execute(
                sa.text(f"UPDATE {table} SET organization_id = :owner WHERE organization_id IS NULL"),
                {"owner": owners[0]},
            )
    bind.execute(
        sa.text(
            "UPDATE risk_model_versions v SET organization_id = r.organization_id "
            "FROM calibration_runs r WHERE r.calibration_run_id = v.calibration_run_id"
        )
    )
    for table in _PIPELINE:
        op.execute(f"ALTER TABLE {table} ENABLE TRIGGER USER")

    _refuse_if(
        bind,
        "SELECT count(*) FROM (SELECT snapshot_id FROM training_dataset_snapshot_items "
        "GROUP BY snapshot_id HAVING count(DISTINCT outcome_organization(outcome_id)) > 1) x",
        "dataset snapshots containing outcomes of more than one organization",
    )
    _refuse_if(
        bind,
        "SELECT count(*) FROM (SELECT calibration_run_id FROM calibration_run_observations "
        "GROUP BY calibration_run_id HAVING count(DISTINCT outcome_organization(outcome_id)) > 1) x",
        "calibration runs trained on outcomes of more than one organization",
    )
    for table in _PIPELINE:
        _refuse_if(
            bind,
            f"SELECT count(*) FROM {table} WHERE organization_id IS NULL",
            f"{table} rows whose organization cannot be determined",
        )
        op.alter_column(table, "organization_id", nullable=False)
        op.create_index(f"ix_{table}_organization_id", table, ["organization_id"])

    # One ACTIVE model per organization and scope.
    op.drop_index("uq_calibration_runs_active_scope", table_name="calibration_runs")
    op.create_index(
        "uq_calibration_runs_active_scope",
        "calibration_runs",
        ["organization_id", "deployment_scope"],
        unique=True,
        postgresql_where=sa.text("deployment_status = 'active'"),
    )
    op.drop_index("uq_risk_model_versions_active_scope", table_name="risk_model_versions")
    op.create_index(
        "uq_risk_model_versions_active_scope",
        "risk_model_versions",
        ["organization_id", "scope"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    op.drop_constraint(
        "uq_training_dataset_snapshots_hash", "training_dataset_snapshots", type_="unique"
    )
    op.create_unique_constraint(
        "uq_training_dataset_snapshots_hash",
        "training_dataset_snapshots",
        ["organization_id", "deployment_scope", "dataset_hash"],
    )

    # Owner defaults from lineage, lineage must agree, owner never changes.
    op.execute(
        """
        CREATE FUNCTION enforce_pipeline_owner() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE expected uuid; other uuid;
        BEGIN
            IF TG_OP = 'UPDATE' THEN
                IF NEW.organization_id IS DISTINCT FROM OLD.organization_id THEN
                    RAISE EXCEPTION '% owner is immutable', TG_TABLE_NAME
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                RETURN NEW;
            END IF;
            IF TG_TABLE_NAME = 'calibration_jobs' THEN
                expected := COALESCE(
                    outcome_organization(NEW.trigger_outcome_id),
                    (SELECT outcome_organization(c.outcome_id) FROM outcome_corrections c
                     WHERE c.correction_id = NEW.trigger_correction_id));
            ELSIF TG_TABLE_NAME = 'training_dataset_snapshots' THEN
                SELECT organization_id INTO expected FROM calibration_jobs
                WHERE job_id = NEW.trigger_job_id;
            ELSIF TG_TABLE_NAME = 'calibration_runs' THEN
                SELECT organization_id INTO expected FROM calibration_jobs
                WHERE job_id = NEW.trigger_job_id;
                SELECT organization_id INTO other FROM training_dataset_snapshots
                WHERE snapshot_id = NEW.dataset_snapshot_id;
                IF expected IS NOT NULL AND other IS NOT NULL AND expected <> other THEN
                    RAISE EXCEPTION 'calibration run job and snapshot belong to different organizations'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                expected := COALESCE(expected, other, outcome_organization(NEW.trigger_outcome_id));
            ELSIF TG_TABLE_NAME = 'risk_model_versions' THEN
                SELECT organization_id INTO expected FROM calibration_runs
                WHERE calibration_run_id = NEW.calibration_run_id;
                SELECT organization_id INTO other FROM training_dataset_snapshots
                WHERE snapshot_id = NEW.dataset_snapshot_id;
                IF other IS NOT NULL AND other IS DISTINCT FROM expected THEN
                    RAISE EXCEPTION 'model version and dataset snapshot belong to different organizations'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
            END IF;
            IF NEW.organization_id IS NULL AND expected IS NULL
               AND TG_TABLE_NAME <> 'risk_model_versions' THEN
                -- No lineage at all (operator or migration seeding): the only
                -- lending organization, if there is exactly one.
                SELECT CASE WHEN count(*) = 1 THEN min(organization_id::text)::uuid END
                INTO expected FROM organizations WHERE organization_type = 'financier';
            END IF;
            IF NEW.organization_id IS NULL THEN
                NEW.organization_id := expected;
            ELSIF expected IS NOT NULL AND NEW.organization_id <> expected THEN
                RAISE EXCEPTION '% organization differs from its lineage', TG_TABLE_NAME
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END $$
        """
    )
    for table in _PIPELINE:
        # "trg_0" sorts first so later BEFORE triggers see the resolved owner.
        op.execute(
            f"CREATE TRIGGER trg_0_{table}_owner BEFORE INSERT OR UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION enforce_pipeline_owner()"
        )

    op.execute(
        """
        CREATE FUNCTION require_same_tenant_membership() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE owner uuid;
        BEGIN
            IF TG_TABLE_NAME = 'training_dataset_snapshot_items' THEN
                SELECT organization_id INTO owner FROM training_dataset_snapshots
                WHERE snapshot_id = NEW.snapshot_id;
            ELSE
                SELECT organization_id INTO owner FROM calibration_runs
                WHERE calibration_run_id = NEW.calibration_run_id;
            END IF;
            IF outcome_organization(NEW.outcome_id) IS DISTINCT FROM owner THEN
                RAISE EXCEPTION 'training data may only contain outcomes of the owning organization'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END $$
        """
    )
    for table in ("training_dataset_snapshot_items", "calibration_run_observations"):
        op.execute(
            f"CREATE TRIGGER trg_{table}_tenant BEFORE INSERT ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION require_same_tenant_membership()"
        )

    op.execute(
        """
        CREATE FUNCTION require_tenant_model() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE tenant uuid; model_owner uuid; run_owner uuid;
        BEGIN
            IF TG_TABLE_NAME = 'risk_decision_records' THEN
                SELECT organization_id INTO tenant FROM users WHERE user_id = NEW.assessed_by_user_id;
                SELECT organization_id INTO run_owner FROM calibration_runs
                WHERE calibration_run_id = COALESCE(NEW.calibration_run_id, NEW.attempted_calibration_run_id);
            ELSE
                SELECT organization_id INTO tenant FROM financing_facilities
                WHERE facility_id = NEW.facility_id;
            END IF;
            SELECT organization_id INTO model_owner FROM risk_model_versions
            WHERE id = NEW.model_version_id;
            IF (model_owner IS NOT NULL AND model_owner IS DISTINCT FROM tenant)
               OR (run_owner IS NOT NULL AND run_owner IS DISTINCT FROM tenant) THEN
                RAISE EXCEPTION 'a decision may only use a model of its own organization'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END $$
        """
    )
    for table in ("risk_decision_records", "actual_outcomes"):
        op.execute(
            f"CREATE TRIGGER trg_{table}_tenant_model BEFORE INSERT ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION require_tenant_model()"
        )


def downgrade() -> None:
    bind = op.get_bind()
    _refuse_if(
        bind,
        "SELECT count(*) FROM (SELECT deployment_scope FROM calibration_runs "
        "WHERE deployment_status = 'active' GROUP BY deployment_scope HAVING count(*) > 1) x",
        "more than one organization has an ACTIVE calibration in the same scope",
    )
    _refuse_if(
        bind,
        "SELECT CASE WHEN count(DISTINCT lender_organization_id) > 1 THEN 1 ELSE 0 END "
        "FROM financing_requests",
        "applications are addressed to more than one lender organization",
    )
    for table in ("risk_decision_records", "actual_outcomes"):
        op.execute(f"DROP TRIGGER trg_{table}_tenant_model ON {table}")
    op.execute("DROP FUNCTION require_tenant_model()")
    for table in ("training_dataset_snapshot_items", "calibration_run_observations"):
        op.execute(f"DROP TRIGGER trg_{table}_tenant ON {table}")
    op.execute("DROP FUNCTION require_same_tenant_membership()")
    for table in _PIPELINE:
        op.execute(f"DROP TRIGGER trg_0_{table}_owner ON {table}")
    op.execute("DROP FUNCTION enforce_pipeline_owner()")
    op.drop_constraint(
        "uq_training_dataset_snapshots_hash", "training_dataset_snapshots", type_="unique"
    )
    op.create_unique_constraint(
        "uq_training_dataset_snapshots_hash",
        "training_dataset_snapshots",
        ["deployment_scope", "dataset_hash"],
    )
    op.drop_index("uq_risk_model_versions_active_scope", table_name="risk_model_versions")
    op.create_index(
        "uq_risk_model_versions_active_scope",
        "risk_model_versions",
        ["scope"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    op.drop_index("uq_calibration_runs_active_scope", table_name="calibration_runs")
    op.create_index(
        "uq_calibration_runs_active_scope",
        "calibration_runs",
        ["deployment_scope"],
        unique=True,
        postgresql_where=sa.text("deployment_status = 'active'"),
    )
    for table in _PIPELINE:
        op.drop_index(f"ix_{table}_organization_id", table_name=table)
        op.drop_column(table, "organization_id")
    op.execute("DROP FUNCTION calibration_job_organization(uuid)")
    op.execute("DROP FUNCTION outcome_organization(uuid)")

    op.execute("DROP TRIGGER trg_financing_facilities_lender_match ON financing_facilities")
    op.execute("DROP FUNCTION require_facility_lender()")
    op.execute("DROP TRIGGER trg_financing_requests_lender ON financing_requests")
    op.execute("DROP FUNCTION guard_application_lender()")
    op.drop_constraint("ck_financing_requests_lender", "financing_requests", type_="check")
    op.drop_index("ix_financing_requests_lender_organization_id", table_name="financing_requests")
    op.drop_column("financing_requests", "lender_organization_id")
