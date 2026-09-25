"""Phase 2.2 outcome data governance: review lifecycle, eligibility, snapshots, lineage."""

from __future__ import annotations

import importlib.util
import uuid
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import event, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from app.domain.governance import OutcomeReviewStatus
from app.domain.outcome_governance import (
    ELIGIBILITY_POLICY,
    REVIEW_TRANSITIONS,
    snapshot_hash,
    training_failure_reason,
)
from app.models import LedgerEventModel
from app.models_governance import CalibrationJobModel, CalibrationRunObservationModel
from app.models_model_governance import (
    OutcomeReviewEventModel,
    RiskDecisionRecordModel,
    RiskModelVersionModel,
    TrainingDatasetSnapshotItemModel,
    TrainingDatasetSnapshotModel,
)
from app.models_outcome import CalibrationRunModel
from app.schemas_outcome import (
    OutcomeCorrectionCreate,
    OutcomeReviewCreate,
    OutcomeSupersedeCreate,
)
from app.services.calibration_jobs import CalibrationJobService
from app.services.model_registry import ModelRegistryService
from app.services.outcome_eligibility import OutcomeEligibilityService, exclusion_reason
from app.services.outcome_governance import OutcomeGovernanceService
from app.services.outcomes import ForbiddenOutcome, OutcomeConflict, OutcomeService
from test_model_governance import START, _assess, _seed_outcomes, _users

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "alembic" / "versions" / "20260927_0018_outcome_data_governance.py"


def _stack(session_factory, tmp_path, *, manual_review: bool = False):
    users = _users(session_factory)
    clock = [START + timedelta(days=30)]
    outcomes = OutcomeService(
        session_factory,
        artifact_root=tmp_path,
        clock=lambda: clock[0],
        manual_review=manual_review,
    )
    worker = CalibrationJobService(session_factory, outcome_service=outcomes, clock=lambda: clock[0])

    def drain():
        while worker.process_next("outcome-governance-worker"):
            pass

    governance = OutcomeGovernanceService(session_factory, outcome_service=outcomes)
    registry = ModelRegistryService(session_factory, outcome_service=outcomes)
    return users, outcomes, drain, governance, registry, clock


@pytest.fixture
def stack(session_factory, tmp_path):
    return _stack(session_factory, tmp_path)


@pytest.fixture
def manual_stack(session_factory, tmp_path):
    return _stack(session_factory, tmp_path, manual_review=True)


def _manual_factory(session_factory):
    """A session factory whose transactions run the review trigger in manual mode."""

    factory = sessionmaker(bind=session_factory.kw["bind"], expire_on_commit=False)

    @event.listens_for(factory, "after_begin")
    def _manual(session, transaction, connection):
        connection.exec_driver_sql("SELECT set_config('daibm.outcome_review_mode', 'manual', true)")

    return factory


def _history(session_factory, outcome_id):
    with session_factory() as session:
        return list(
            session.scalars(
                select(OutcomeReviewEventModel)
                .where(OutcomeReviewEventModel.outcome_id == outcome_id)
                .order_by(OutcomeReviewEventModel.event_id)
            )
        )


def _status(session_factory, outcome_id) -> str:
    return _history(session_factory, outcome_id)[-1].status


def _snapshots(session_factory) -> list[TrainingDatasetSnapshotModel]:
    with session_factory() as session:
        return list(
            session.scalars(
                select(TrainingDatasetSnapshotModel).order_by(
                    TrainingDatasetSnapshotModel.created_at
                )
            )
        )


def _runs(session_factory) -> list[CalibrationRunModel]:
    with session_factory() as session:
        return list(
            session.scalars(select(CalibrationRunModel).order_by(CalibrationRunModel.completed_at))
        )


def _review(decision, comment, reason_code=None) -> OutcomeReviewCreate:
    return OutcomeReviewCreate(decision=decision, comment=comment, reason_code=reason_code)


# --- 1. Review lifecycle ---------------------------------------------------------


def test_review_transitions_equal_the_frozen_database_list():
    spec = importlib.util.spec_from_file_location("outcome_data_governance_0018", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    frozen = {
        (OutcomeReviewStatus(a) if a else None, OutcomeReviewStatus(b))
        for a, b in module._REVIEW_TRANSITIONS
    }
    assert frozen == set(REVIEW_TRANSITIONS)


def test_every_review_event_records_from_to_operator_role_time_and_reason(
    session_factory, stack
):
    users, *_ = stack
    (outcome_id,) = _seed_outcomes(session_factory, users, count=1, start=START)
    events = _history(session_factory, outcome_id)
    assert [(e.from_status, e.status) for e in events] == [
        (None, "CREATED"),
        ("CREATED", "REVIEWING"),
        ("REVIEWING", "ELIGIBLE"),
    ]
    created, reviewing, eligible = events
    assert (created.actor_user_id, created.actor_role) == (users["auditor"].user_id, "auditor")
    assert (reviewing.actor_role, reviewing.comment) == ("auditor", "eligibility_rules_started")
    # The automatic decision is the rule engine's, not the submitter's.
    assert (eligible.actor_user_id, eligible.actor_role) == (None, "system")
    assert eligible.comment == "eligibility_rules_passed"
    assert all(e.recorded_at is not None for e in events)


def test_database_rejects_illegal_review_transitions_and_history_edits(session_factory, stack):
    users, *_ = stack
    (outcome_id,) = _seed_outcomes(session_factory, users, count=1, start=START)
    with pytest.raises(DBAPIError, match="illegal outcome review transition ELIGIBLE -> CREATED"):
        with session_factory.begin() as session:
            session.add(OutcomeReviewEventModel(outcome_id=outcome_id, status="CREATED"))
    for statement in (
        "UPDATE outcome_review_events SET comment = 'edited' WHERE outcome_id = :id",
        "DELETE FROM outcome_review_events WHERE outcome_id = :id",
        "UPDATE actual_outcomes SET loss_amount = 1 WHERE outcome_id = :id",
        "DELETE FROM actual_outcomes WHERE outcome_id = :id",
    ):
        with pytest.raises(DBAPIError):
            with session_factory.begin() as session:
                session.execute(text(statement), {"id": outcome_id})
    assert len(_history(session_factory, outcome_id)) == 3


def test_manual_review_mode_holds_outcomes_until_a_reviewer_approves(
    session_factory, manual_stack
):
    users, outcomes, drain, governance, _, _ = manual_stack
    outcome_ids = _seed_outcomes(_manual_factory(session_factory), users, count=40, start=START)
    assert {_status(session_factory, item) for item in outcome_ids} == {"REVIEWING"}
    queue = governance.review_queue(users["risk"])
    assert {item["outcome_id"] for item in queue} == {str(item) for item in outcome_ids}

    # Nothing is reviewed yet, so training has nothing to read and says so.
    drain()
    with session_factory() as session:
        failures = {job.failure_code for job in session.scalars(select(CalibrationJobModel))}
    assert failures == {"no_eligible_outcomes"}
    assert _runs(session_factory) == []

    with pytest.raises(ForbiddenOutcome):
        outcomes.review(outcome_ids[0], _review("APPROVE", "looks right"), users["supplier"])
    approved = outcomes.review(
        outcome_ids[0], _review("APPROVE", "Evidence checked against ledger"), users["risk"]
    )
    assert approved["review_status"] == "ELIGIBLE"
    assert approved["calibration_job_id"] is not None
    last = approved["review_history"][-1]
    assert (last["from_status"], last["to_status"], last["operator"], last["role"]) == (
        "REVIEWING", "ELIGIBLE", "risk.demo", "risk_manager",
    )
    assert last["comment"] == "Evidence checked against ledger" and last["recorded_at"]
    with pytest.raises(OutcomeConflict, match="Only a REVIEWING outcome"):
        outcomes.review(outcome_ids[0], _review("APPROVE", "again please"), users["risk"])

    for outcome_id in outcome_ids[1:]:
        outcomes.review(outcome_id, _review("APPROVE", "Batch review ok"), users["auditor"])
    drain()
    (run,) = [run for run in _runs(session_factory) if run.status != "failed"]
    with session_factory() as session:
        snapshot = session.get(TrainingDatasetSnapshotModel, run.dataset_snapshot_id)
        ledger = session.scalars(
            select(LedgerEventModel).where(LedgerEventModel.event_type == "ACTUAL_OUTCOME_REVIEWED")
        ).all()
    assert snapshot.included_count == 40 and snapshot.excluded_count == 0
    assert len(ledger) == 40


def test_reviewer_rejection_keeps_the_outcome_out_of_training(session_factory, stack):
    users, outcomes, drain, governance, _, _ = stack
    outcome_ids = _seed_outcomes(session_factory, users, count=41, start=START)
    rejected_id = outcome_ids[7]
    with pytest.raises(Exception):
        _review("REJECT", "no reason code")
    result = outcomes.review(
        rejected_id,
        _review("REJECT", "Scanned contract does not match", "QUALITY_ANOMALY"),
        users["auditor"],
    )
    assert (result["review_status"], result["review_reason"]) == ("REJECTED", "QUALITY_ANOMALY")
    event_row = _history(session_factory, rejected_id)[-1]
    assert (event_row.from_status, event_row.actor_role, event_row.comment) == (
        "ELIGIBLE", "auditor", "Scanned contract does not match",
    )
    drain()
    (run,) = _runs(session_factory)
    with session_factory() as session:
        members = set(
            session.scalars(
                select(CalibrationRunObservationModel.outcome_id).where(
                    CalibrationRunObservationModel.calibration_run_id == run.calibration_run_id
                )
            )
        )
    assert rejected_id not in members and len(members) == 40
    detail = governance.get_snapshot(run.dataset_snapshot_id, users["auditor"])
    excluded = {row["outcome_id"]: row["exclusion_reason"] for row in detail["outcomes"] if not row["included"]}
    assert excluded == {str(rejected_id): "QUALITY_ANOMALY"}
    assert detail["exclusion_summary"] == {"QUALITY_ANOMALY": 1}
    # Trained outcomes are corrected through the correction path, not by review.
    with pytest.raises(OutcomeConflict, match="use a correction"):
        outcomes.review(
            outcome_ids[0], _review("REJECT", "late doubt", "QUALITY_ANOMALY"), users["auditor"]
        )


# --- 2. Training eligibility -----------------------------------------------------------


def test_exclusion_reason_order_covers_every_condition():
    base = dict(head_action=None, review_status="ELIGIBLE", review_reason=None, rule_reason=None, effective=True)
    assert exclusion_reason(**base) is None
    assert exclusion_reason(**{**base, "review_status": "TRAINING_USED"}) is None
    assert exclusion_reason(**{**base, "effective": False}) == "SUPERSEDED_BY_CORRECTION"
    assert exclusion_reason(**{**base, "head_action": "EXCLUDE"}) == "EXCLUDED_BY_CORRECTION"
    assert exclusion_reason(**{**base, "review_status": "REVIEWING"}) == "REVIEW_PENDING"
    assert exclusion_reason(
        **{**base, "review_status": "REJECTED", "review_reason": "QUALITY_ANOMALY"}
    ) == "QUALITY_ANOMALY"
    for rule in ("SCOPE_MISMATCH", "BUSINESS_EXCEPTION", "DATA_QUALITY_INSUFFICIENT", "BUSINESS_INCONSISTENT"):
        assert exclusion_reason(**{**base, "rule_reason": rule}) == rule


def test_eligibility_service_judges_each_outcome_with_a_reason(session_factory, stack):
    users, outcomes, _, governance, _, clock = stack
    ids = _seed_outcomes(
        session_factory, users, count=6, start=START,
        # Index 5: not defaulted yet a loss -> business inconsistent.
        loss_for=lambda defaulted: Decimal("0.00"),
    )
    inconsistent = _seed_outcomes(
        session_factory, users, count=1, start=START + timedelta(days=1),
        default_for=lambda index: False, loss_for=lambda defaulted: Decimal("5.00"),
    )[0]
    pending = _seed_outcomes(
        _manual_factory(session_factory), users, count=1, start=START + timedelta(days=2)
    )[0]
    clock[0] = START + timedelta(days=31)
    corrected = outcomes.supersede(
        ids[0],
        OutcomeSupersedeCreate(
            idempotency_key=uuid.uuid4(), defaulted=True, days_past_due=40, loss_amount="80.00",
            observed_at=START + timedelta(days=31), evidence_sha256="c" * 64,
            reason_code="LATE_DEFAULT_EVIDENCE", comment="Default confirmed later",
        ),
        users["auditor"],
    )["outcome"]["outcome_id"]
    outcomes.create_correction(
        ids[1],
        OutcomeCorrectionCreate(
            idempotency_key=uuid.uuid4(), action="EXCLUDE", reason_code="EVIDENCE_REVIEW",
            comment="Evidence withdrawn", evidence_sha256="d" * 64,
        ),
        users["auditor"],
    )
    outcomes.review(ids[2], _review("REJECT", "Duplicate invoice", "QUALITY_ANOMALY"), users["risk"])

    preview = governance.eligibility_preview(users["auditor"], scope="controlled_demo")
    reasons = {row["outcome_id"]: row["exclusion_reason"] for row in preview["outcomes"]}
    assert reasons == {
        str(ids[0]): "SUPERSEDED_BY_CORRECTION",
        corrected: None,
        str(ids[1]): "EXCLUDED_BY_CORRECTION",
        str(ids[2]): "QUALITY_ANOMALY",
        str(ids[3]): None,
        str(ids[4]): None,
        str(ids[5]): None,
        str(inconsistent): "BUSINESS_INCONSISTENT",
        str(pending): "REVIEW_PENDING",
    }
    assert preview["included_count"] == 4
    # The deployment check and the service agree on what is eligible.
    service = OutcomeEligibilityService()
    with session_factory() as session:
        included = {item.outcome.outcome_id for item in service.assess(session, scope="controlled_demo").included}
        legacy = {row.outcome_id for row in outcomes.repository.list_eligible_outcomes(session, scope="controlled_demo")}
    assert included == legacy


def test_training_reads_only_through_the_eligibility_service():
    source = (ROOT / "app" / "services" / "calibration_jobs.py").read_text(encoding="utf-8")
    assert "eligibility.create_snapshot(" in source
    assert "eligibility.training_observations(" in source
    for forbidden in ("list_eligible_outcomes", "ActualOutcomeModel", "select("):
        assert forbidden not in source, forbidden
    offenders = [
        path.relative_to(ROOT)
        for path in (ROOT / "app").rglob("*.py")
        if "list_eligible_outcomes_with_heads" in path.read_text(encoding="utf-8")
        and path.name != "outcomes.py"
    ]
    assert offenders == []


# --- 3. Snapshots and pipeline ------------------------------------------------------------


def test_training_run_and_model_version_reference_an_immutable_hashed_snapshot(
    session_factory, stack
):
    users, outcomes, drain, governance, registry, _ = stack
    _seed_outcomes(session_factory, users, count=40, start=START)
    drain()
    (run,) = _runs(session_factory)
    with session_factory() as session:
        snapshot = session.get(TrainingDatasetSnapshotModel, run.dataset_snapshot_id)
        items = list(
            session.scalars(
                select(TrainingDatasetSnapshotItemModel).where(
                    TrainingDatasetSnapshotItemModel.snapshot_id == snapshot.snapshot_id
                )
            )
        )
        members = {
            (row.outcome_id, row.correction_head_id)
            for row in session.scalars(
                select(CalibrationRunObservationModel).where(
                    CalibrationRunObservationModel.calibration_run_id == run.calibration_run_id
                )
            )
        }
        version = session.scalar(
            select(RiskModelVersionModel).where(
                RiskModelVersionModel.calibration_run_id == run.calibration_run_id
            )
        )
    assert version.dataset_snapshot_id == snapshot.snapshot_id
    assert {(i.outcome_id, i.correction_head_id) for i in items if i.included} == members
    assert snapshot.dataset_hash == snapshot_hash(
        scope="controlled_demo",
        policy=ELIGIBILITY_POLICY,
        included=[(str(i.outcome_id), None) for i in items if i.included],
        excluded=[(str(i.outcome_id), i.exclusion_reason) for i in items if not i.included],
    )
    assert (snapshot.source, snapshot.created_by) == ("calibration_job", "system:calibration-worker")
    # Same eligible state -> the same snapshot, never a silent new dataset.
    with session_factory.begin() as session:
        again = outcomes.eligibility.create_snapshot(
            session, scope="controlled_demo", created_by="test", trigger_job_id=None
        )
        assert again.snapshot_id == snapshot.snapshot_id
    for statement in (
        "UPDATE training_dataset_snapshots SET included_count = 0",
        "DELETE FROM training_dataset_snapshot_items",
        "UPDATE calibration_runs SET dataset_snapshot_id = NULL",
        "UPDATE risk_model_versions SET dataset_snapshot_id = NULL",
    ):
        with pytest.raises(DBAPIError):
            with session_factory.begin() as session:
                session.execute(text(statement))

    detail = registry.get_version(version.id, users["auditor"])
    assert detail["dataset_snapshot_id"] == str(snapshot.snapshot_id)
    assert (detail["training_outcome_count"], detail["excluded_outcome_count"]) == (40, 0)
    assert detail["exclusion_reasons"] == {}
    listed = governance.list_snapshots(users["financier"], scope="controlled_demo")
    assert listed[0]["model_versions"][0]["label"] == "calibration:controlled_demo@v1"
    assert listed[0]["training_runs"][0]["calibration_run_id"] == str(run.calibration_run_id)


@pytest.mark.parametrize(
    ("count", "score_for", "reason"),
    [
        (12, lambda index: 0.03 + 0.09 * (index % 10), "insufficient_samples"),
        (40, lambda index: 0.25, "no_risk_variance"),
    ],
)
def test_insufficient_data_fails_with_an_explicit_reason(
    session_factory, stack, count, score_for, reason
):
    users, outcomes, drain, governance, _, _ = stack
    _seed_outcomes(session_factory, users, count=count, start=START, score_for=score_for)
    drain()
    runs = _runs(session_factory)
    assert runs and all(run.status == "failed" and run.artifact_sha256 is None for run in runs)
    served = outcomes.get_run(runs[-1].calibration_run_id, users["auditor"])
    assert served["failure_reason"] == reason
    assert served["dataset_snapshot_id"] == str(runs[-1].dataset_snapshot_id)
    usage = governance.get_snapshot(runs[-1].dataset_snapshot_id, users["auditor"])
    assert usage["training_runs"][-1]["failure_reason"] == reason
    assert usage["model_versions"] == []
    with session_factory() as session:
        assert session.scalar(select(sa.func.count()).select_from(RiskModelVersionModel)) == 0


def test_failure_reasons_never_report_an_improvement():
    for code in ("brier_regression", "log_loss_regression", "no_metric_improvement"):
        assert training_failure_reason(failure_code=None, activation_reason=code) == "no_holdout_improvement"
    assert training_failure_reason(failure_code=None, activation_reason="gate_passed") is None
    assert training_failure_reason(
        failure_code=None, activation_reason="deployment_scope_mismatch"
    ) == "scope_mismatch"
    assert training_failure_reason(
        failure_code="no_eligible_outcomes", activation_reason=None
    ) == "no_eligible_outcomes"


# --- 4. Lineage ------------------------------------------------------------------------------


def test_decision_lineage_reaches_the_training_outcomes(session_factory, stack):
    users, _, drain, governance, _, _ = stack
    outcome_ids = _seed_outcomes(session_factory, users, count=40, start=START)
    drain()
    service, workflow_users, assessed = _assess(session_factory, scope="controlled_demo")
    (decision,) = service.risk_decisions(assessed["request_id"], workflow_users["financier"])
    assert decision["model_version_label"] == "calibration:controlled_demo@v1"
    lineage = governance.decision_lineage(decision["decision_record_id"], users["auditor"])
    assert lineage["decision"]["applied"] is True
    assert lineage["model_version"]["label"] == "calibration:controlled_demo@v1"
    assert lineage["dataset_snapshot"]["snapshot_id"] == decision["dataset_snapshot_id"]
    assert set(lineage["training_outcome_ids"]) == {str(item) for item in outcome_ids}
    with session_factory() as session:
        assert session.scalar(select(sa.func.count()).select_from(RiskDecisionRecordModel)) == 1


def test_correction_chain_keeps_originals_and_defaults_to_effective(session_factory, stack):
    users, outcomes, _, governance, _, clock = stack
    (original,) = _seed_outcomes(session_factory, users, count=1, start=START)
    clock[0] = START + timedelta(days=31)
    corrected = outcomes.supersede(
        original,
        OutcomeSupersedeCreate(
            idempotency_key=uuid.uuid4(), defaulted=True, days_past_due=40, loss_amount="80.00",
            observed_at=START + timedelta(days=31), evidence_sha256="c" * 64,
            reason_code="LATE_DEFAULT_EVIDENCE", comment="Default confirmed later",
        ),
        users["auditor"],
    )["outcome"]["outcome_id"]
    listed = outcomes.list_outcomes(users["auditor"])
    assert [row["outcome_id"] for row in listed] == [corrected]
    chain = outcomes.lineage(original, users["auditor"])
    assert [row["outcome"]["outcome_id"] for row in chain["revisions"]] == [str(original), corrected]
    history = governance.review_history(original, users["auditor"])["history"]
    assert history[-1]["to_status"] == "REJECTED"
    assert history[-1]["reason_code"] == "SUPERSEDED_BY_CORRECTION"
    overview = governance.overview(users["risk"])
    assert (overview["total_records"], overview["superseding_revisions"], overview["corrections"]) == (2, 1, 1)


# --- 5. Migration ------------------------------------------------------------------------------


def test_migration_backfills_a_snapshot_for_existing_training_runs(isolated_postgres_engine):
    engine = isolated_postgres_engine
    config = Config(str(ROOT / "alembic.ini"))
    run_id, outcome_id = uuid.uuid4(), uuid.uuid4()
    with engine.connect() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "20260926_0017")
        for table in ("actual_outcomes", "calibration_run_observations"):
            connection.execute(text(f"ALTER TABLE {table} DISABLE TRIGGER ALL"))
        connection.execute(
            text(
                "INSERT INTO actual_outcomes (outcome_id, facility_id, request_id, "
                "risk_assessment_id, submitted_by_user_id, idempotency_key, request_sha256, "
                "defaulted, days_past_due, loss_amount, observed_at, evidence_sha256, provenance, "
                "original_risk_score, risk_engine_version, risk_input_sha256, recorded_at) VALUES "
                "(:id, :f, :r, :a, :u, :k, :h, false, 0, 0, now(), :h, 'CONTROLLED_DEMO', 0.2, "
                "'baseline', :h, now())"
            ),
            {"id": outcome_id, "f": uuid.uuid4(), "r": uuid.uuid4(), "a": uuid.uuid4(),
             "u": uuid.uuid4(), "k": uuid.uuid4(), "h": "1" * 64},
        )
        connection.execute(
            text(
                "INSERT INTO calibration_runs (calibration_run_id, dataset_sha256, sample_count, "
                "positive_count, negative_count, configuration, status, failure_code, "
                "deployment_status, deployment_scope, activation_reason, started_at, completed_at) "
                "VALUES (:id, :h, 1, 0, 1, '{}', 'failed', 'test_rejection', 'not_deployed', "
                "'controlled_demo', 'test_rejection', now(), now())"
            ),
            {"id": run_id, "h": "2" * 64},
        )
        connection.execute(
            text(
                "INSERT INTO calibration_run_observations (calibration_run_id, outcome_id) "
                "VALUES (:r, :o)"
            ),
            {"r": run_id, "o": outcome_id},
        )
        for table in ("actual_outcomes", "calibration_run_observations"):
            connection.execute(text(f"ALTER TABLE {table} ENABLE TRIGGER ALL"))
        connection.commit()
        command.upgrade(config, "20260927_0018")
        row = connection.execute(
            text(
                "SELECT s.source, s.included_count, s.excluded_count, s.dataset_hash, "
                "i.outcome_id, i.included FROM calibration_runs r "
                "JOIN training_dataset_snapshots s ON s.snapshot_id = r.dataset_snapshot_id "
                "JOIN training_dataset_snapshot_items i ON i.snapshot_id = s.snapshot_id "
                "WHERE r.calibration_run_id = :id"
            ),
            {"id": run_id},
        ).one()
        assert row[:3] == ("migration_backfill", 1, 0)
        assert row[3] == snapshot_hash(
            scope="controlled_demo", policy="migration_backfill_v1",
            included=[(str(outcome_id), None)], excluded=[],
        )
        assert (row[4], row[5]) == (outcome_id, True)
        connection.commit()
        command.downgrade(config, "20260926_0017")
        command.upgrade(config, "20260927_0018")


# --- 6. API -----------------------------------------------------------------------------------


def test_outcome_governance_api(migrated_engine, session_factory, manual_stack):
    from app.database import Database
    from app.main import create_app

    users, _, drain, _, _, _ = manual_stack
    outcome_ids = _seed_outcomes(_manual_factory(session_factory), users, count=2, start=START)
    app = create_app(Database(migrated_engine, session_factory), calibration_worker_enabled=False)
    with TestClient(app) as client:
        assert client.get("/api/v1/outcome-governance/overview").status_code == 401
        client.post("/api/v1/auth/login", json={"username": "risk.demo", "password": "Demo123!"})
        overview = client.get("/api/v1/outcome-governance/overview").json()
        assert (overview["total_records"], overview["reviewing"], overview["eligible"]) == (2, 2, 0)
        queue = client.get("/api/v1/outcome-governance/review-queue").json()
        assert {row["outcome_id"] for row in queue} == {str(item) for item in outcome_ids}
        bad = client.post(
            f"/api/v1/outcomes/{outcome_ids[0]}/review", json={"decision": "REJECT", "comment": "no code"}
        )
        assert bad.status_code == 422
        approved = client.post(
            f"/api/v1/outcomes/{outcome_ids[0]}/review",
            json={"decision": "APPROVE", "comment": "Checked evidence"},
        )
        assert approved.status_code == 200, approved.text
        rejected = client.post(
            f"/api/v1/outcomes/{outcome_ids[1]}/review",
            json={"decision": "REJECT", "reason_code": "QUALITY_ANOMALY", "comment": "Mismatch"},
        )
        assert rejected.json()["review_status"] == "REJECTED"
        again = client.post(
            f"/api/v1/outcomes/{outcome_ids[1]}/review",
            json={"decision": "APPROVE", "comment": "Changed mind"},
        )
        assert again.status_code == 409
        history = client.get(f"/api/v1/outcomes/{outcome_ids[0]}/review-history").json()
        assert history["history"][-1]["operator"] == "risk.demo"
        preview = client.get(
            "/api/v1/outcome-governance/eligibility", params={"scope": "controlled_demo"}
        ).json()
        assert (preview["included_count"], preview["exclusion_summary"]) == (1, {"QUALITY_ANOMALY": 1})
        assert client.get("/api/v1/dataset-snapshots").json() == []
        assert client.get(f"/api/v1/dataset-snapshots/{uuid.uuid4()}").status_code == 404
        assert client.get(f"/api/v1/risk-decisions/{uuid.uuid4()}/lineage").status_code == 404
        client.cookies.clear()
        client.post("/api/v1/auth/login", json={"username": "supplier.demo", "password": "Demo123!"})
        assert client.get("/api/v1/outcome-governance/overview").status_code == 403
        assert client.get("/api/v1/dataset-snapshots").status_code == 403
        forbidden = client.post(
            f"/api/v1/outcomes/{outcome_ids[0]}/review",
            json={"decision": "APPROVE", "comment": "Supplier try"},
        )
        assert forbidden.status_code == 403
    drain()
