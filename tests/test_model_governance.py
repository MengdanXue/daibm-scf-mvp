"""Risk-intelligence governance: model registry, outcome review, supersession, scope, audit."""

from __future__ import annotations

import dataclasses
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from facility_fixtures import confirmed_cash_rows
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.domain.governance import (
    ScopeResult,
    calibration_registry_status,
    check_scope,
    scope_matrix,
)
from app.models import FinancingRequestModel, LedgerEventModel
from app.models_facility import FinancingFacilityModel
from app.models_governance import CalibrationJobModel, CalibrationRunObservationModel
from app.models_model_governance import (
    ModelRegistryEventModel,
    OutcomeReviewEventModel,
)
from app.models_outcome import ActualOutcomeModel, CalibrationRunModel
from app.repositories.outcomes import OutcomeRepository
from app.schemas_outcome import OutcomeCorrectionCreate, OutcomeSupersedeCreate
from app.services.calibration_jobs import CalibrationJobService
from app.services.identity import IdentityService
from app.services.model_registry import (
    ModelRegistryService,
    RegistryConflict,
    RegistryForbidden,
)
from app.services.outcomes import BUSINESS_BASELINE_ENGINE, OutcomeConflict, OutcomeService
from app.services.workflow import ApplicationNotFound, WorkflowService
from test_workflow_service import demo_users, draft_payload


START = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)


def _users(session_factory):
    identity = IdentityService(session_factory, clock=lambda: START)
    identity.seed_demo_accounts()
    return {
        name: identity.login(f"{name}.demo", "Demo123!").user
        for name in ("auditor", "financier", "risk", "supplier")
    }


def _seed_outcomes(
    session_factory,
    users,
    *,
    count: int,
    start: datetime,
    scope: str = "controlled_demo",
    score_for=lambda index: 0.03 + 0.09 * (index % 10),
    default_for=lambda index: index % 10 >= 5,
    loss_for=lambda defaulted: Decimal("100.00") if defaulted else Decimal("0.00"),
) -> list[uuid.UUID]:
    """Closed facilities with outcomes; the database performs eligibility review."""

    outcome_ids: list[uuid.UUID] = []
    provenance = "CONTROLLED_DEMO" if scope == "controlled_demo" else "EXTERNAL_VERIFIED"
    with session_factory.begin() as session:
        for index in range(count):
            recorded_at = start + timedelta(minutes=index)
            request_id, facility_id, outcome_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
            score = score_for(index)
            defaulted = default_for(index)
            session.add(
                FinancingRequestModel(
                    request_id=request_id, created_at=recorded_at, updated_at=recorded_at,
                    applicant_id=f"GOV-{scope}-{start:%H%M}-{index:03d}",
                    assessment_scope=scope, amount=Decimal("1000.00"), term_days=30,
                    features={}, risk_score=score, raw_risk_score=score,
                    decision="approved", status="audited", version=1,
                    risk_assessment_id=uuid.uuid4(), risk_engine_version=BUSINESS_BASELINE_ENGINE,
                    risk_input_sha256=uuid.uuid4().hex * 2, risk_assessed_at=recorded_at,
                )
            )
            session.flush()
            session.add(
                FinancingFacilityModel(
                    facility_id=facility_id, request_id=request_id,
                    principal=Decimal("1000.00"), outstanding_amount=Decimal("0.00"),
                    currency="CNY", status="closed", version=1, current_schedule_version=1,
                    closure_reason="repaid", created_by_user_id=users["financier"].user_id,
                    created_at=recorded_at, updated_at=recorded_at, closed_at=recorded_at,
                )
            )
            session.flush()
            session.add_all(confirmed_cash_rows(facility_id, users["financier"].user_id, recorded_at))
            session.flush()
            request = session.get(FinancingRequestModel, request_id)
            session.add(
                ActualOutcomeModel(
                    outcome_id=outcome_id, facility_id=facility_id, request_id=request_id,
                    risk_assessment_id=request.risk_assessment_id, model_version_id=None,
                    submitted_by_user_id=users["auditor"].user_id,
                    idempotency_key=uuid.uuid4(), request_sha256=uuid.uuid4().hex * 2,
                    defaulted=defaulted, days_past_due=30 if defaulted else 0,
                    loss_amount=loss_for(defaulted), observed_at=recorded_at,
                    evidence_sha256=uuid.uuid4().hex * 2, provenance=provenance,
                    original_risk_score=score, risk_engine_version=BUSINESS_BASELINE_ENGINE,
                    risk_input_sha256=request.risk_input_sha256, recorded_at=recorded_at,
                )
            )
            session.flush()
            session.add(
                CalibrationJobModel(
                    job_id=uuid.uuid4(), deployment_scope=scope, trigger_type="outcome_submitted",
                    trigger_outcome_id=outcome_id, trigger_correction_id=None,
                    idempotency_key=uuid.uuid4(), status="queued", attempt_count=0,
                    created_at=recorded_at,
                )
            )
            outcome_ids.append(outcome_id)
    return outcome_ids


@pytest.fixture
def governance(session_factory, tmp_path):
    users = _users(session_factory)
    clock_value = [START + timedelta(days=30)]
    outcome_service = OutcomeService(
        session_factory, artifact_root=tmp_path, clock=lambda: clock_value[0]
    )
    worker = CalibrationJobService(
        session_factory, outcome_service=outcome_service, clock=lambda: clock_value[0]
    )

    def drain():
        while worker.process_next("governance-test-worker"):
            pass

    registry = ModelRegistryService(session_factory, clock=lambda: clock_value[0])
    return users, outcome_service, worker, drain, registry, clock_value


def _runs(session_factory, scope="controlled_demo"):
    with session_factory() as session:
        return list(
            session.scalars(
                select(CalibrationRunModel)
                .where(CalibrationRunModel.deployment_scope == scope)
                .order_by(CalibrationRunModel.completed_at)
            )
        )


def _status_history(session_factory, run_id):
    with session_factory() as session:
        return [
            (row.from_status, row.to_status, row.reason)
            for row in session.scalars(
                select(ModelRegistryEventModel)
                .where(
                    ModelRegistryEventModel.model_id == run_id,
                    ModelRegistryEventModel.event_type == "STATUS_CHANGED",
                )
                .order_by(ModelRegistryEventModel.event_id)
            )
        ]


def _review_history(session_factory, outcome_id):
    with session_factory() as session:
        return [
            (row.status, row.reason_code)
            for row in session.scalars(
                select(OutcomeReviewEventModel)
                .where(OutcomeReviewEventModel.outcome_id == outcome_id)
                .order_by(OutcomeReviewEventModel.event_id)
            )
        ]


def _two_versions(session_factory, governance):
    users, _, _, drain, _, clock = governance
    _seed_outcomes(session_factory, users, count=40, start=START)
    drain()
    clock[0] = START + timedelta(days=60)
    _seed_outcomes(session_factory, users, count=10, start=START + timedelta(days=40))
    drain()
    runs = [run for run in _runs(session_factory) if run.deployment_status in ("active", "superseded")]
    assert len(runs) == 2, [(run.deployment_status, run.activation_reason) for run in _runs(session_factory)]
    return runs


# --- 1-4, 8, 9: Model registry ------------------------------------------------------


def test_trained_model_is_registered_with_full_lineage(session_factory, governance):
    users, _, _, drain, registry, _ = governance
    _seed_outcomes(session_factory, users, count=40, start=START)
    drain()
    (run,) = [run for run in _runs(session_factory) if run.deployment_status == "active"]
    listed = registry.list_models(users["auditor"], scope="controlled_demo")
    entry = next(item for item in listed["models"] if item["model_id"] == str(run.calibration_run_id))
    for field in (
        "model_id", "version", "model_type", "artifact_path", "artifact_hash", "created_time",
        "creator", "training_scope", "training_dataset_version", "evaluation_metrics", "status",
    ):
        assert entry[field] is not None, field
    assert entry["model_type"] == "platt_calibration"
    assert entry["version"] == "platt-controlled_demo-v1"
    assert entry["creator"] == "auditor.demo"
    assert entry["training_dataset_version"] == run.dataset_sha256
    assert entry["status"] == "ACTIVE"
    assert listed["active_by_scope"] == {"controlled_demo": str(run.calibration_run_id)}
    assert _status_history(session_factory, run.calibration_run_id) == [
        (None, "EVALUATING", "eligible_candidate"),
        ("EVALUATING", "CANDIDATE", "independent_validation_passed"),
        ("CANDIDATE", "ACTIVE", "gate_passed"),
    ]
    with session_factory() as session:
        pipeline = {
            row.event_type: row
            for row in session.scalars(
                select(ModelRegistryEventModel).where(
                    ModelRegistryEventModel.model_id == run.calibration_run_id,
                    ModelRegistryEventModel.event_type != "STATUS_CHANGED",
                )
            )
        }
    assert set(pipeline) == {"REGISTERED", "VALIDATED", "PROMOTION_DECISION"}
    for row in pipeline.values():
        assert row.dataset_sha256 == run.dataset_sha256
        assert row.sample_count == run.sample_count == 40
        assert row.deployment_scope == "controlled_demo"
        assert row.artifact_sha256 == run.artifact_sha256
    assert pipeline["PROMOTION_DECISION"].reason == "promoted"


def test_promotion_rests_on_an_independent_holdout_not_the_training_fit(
    session_factory, governance
):
    users, _, _, drain, _, _ = governance
    _seed_outcomes(session_factory, users, count=40, start=START)
    drain()
    (run,) = _runs(session_factory)
    with session_factory() as session:
        validated = session.scalar(
            select(ModelRegistryEventModel).where(
                ModelRegistryEventModel.model_id == run.calibration_run_id,
                ModelRegistryEventModel.event_type == "VALIDATED",
            )
        )
    evidence = validated.metrics
    assert validated.reason == "validation_passed"
    assert evidence["independent"] and evidence["disjoint"] and evidence["chronological"]
    assert evidence["training_count"] + evidence["validation_count"] == 40
    assert evidence["training_cutoff"] < evidence["validation_start"]
    assert evidence["validation_metrics_after"] == run.metrics_after
    assert "training_fit" not in evidence


def test_database_rejects_promotion_without_a_validation_record(session_factory, governance):
    users, _, _, drain, _, _ = governance
    _seed_outcomes(session_factory, users, count=40, start=START)
    drain()
    run = _runs(session_factory)[0]
    with session_factory.begin() as session:
        session.execute(text("TRUNCATE calibration_runs CASCADE"))
    now = datetime.now(timezone.utc)
    run_id = uuid.uuid4()
    with session_factory.begin() as session:
        session.add(
            CalibrationRunModel(
                calibration_run_id=run_id, dataset_sha256=run.dataset_sha256, sample_count=40,
                positive_count=20, negative_count=20, metrics_before={}, metrics_after={},
                configuration={}, status="eligible_candidate",
                artifact_locator=run.artifact_locator, artifact_sha256=run.artifact_sha256,
                artifact_schema=run.artifact_schema, deployment_status="not_deployed",
                deployment_scope="controlled_demo", activation_reason="not_evaluated",
                started_at=now, completed_at=now,
            )
        )
    with pytest.raises(DBAPIError, match="promotion requires a passed independent validation"):
        with session_factory.begin() as session:
            stored = session.get(CalibrationRunModel, run_id)
            stored.deployment_status = "active"
            stored.activation_mode = "automatic"
            stored.activated_at = now
    with session_factory() as session:
        assert session.get(CalibrationRunModel, run_id).deployment_status == "not_deployed"


def test_candidate_without_independent_holdout_is_never_promoted(
    session_factory, governance, monkeypatch
):
    users, _, _, drain, _, _ = governance
    import app.services.outcomes as outcomes_module

    original = outcomes_module.independent_validation_evidence

    def leaky(candidate):
        # Simulate a holdout that overlaps the training partition.
        return {**original(candidate), "disjoint": False, "independent": False}

    monkeypatch.setattr(outcomes_module, "independent_validation_evidence", leaky)
    _seed_outcomes(session_factory, users, count=40, start=START)
    drain()
    runs = _runs(session_factory)
    assert runs and all(run.deployment_status == "rejected" for run in runs)
    assert {run.activation_reason for run in runs} == {"validation_not_independent"}
    for run in runs:
        assert _status_history(session_factory, run.calibration_run_id)[-1][1] == "REJECTED"


def test_version_switch_keeps_one_active_and_audits_each_change(session_factory, governance):
    first, second = _two_versions(session_factory, governance)
    assert (first.deployment_status, second.deployment_status) == ("superseded", "active")
    assert second.previous_active_run_id == first.calibration_run_id
    assert _status_history(session_factory, first.calibration_run_id)[-1] == (
        "ACTIVE", "RETIRED", "superseded_by_promotion",
    )
    with session_factory() as session:
        assert session.scalar(
            select(func.count()).select_from(CalibrationRunModel).where(
                CalibrationRunModel.deployment_status == "active",
                CalibrationRunModel.deployment_scope == "controlled_demo",
            )
        ) == 1
        activation_events = list(
            session.scalars(
                select(LedgerEventModel.event_type).where(
                    LedgerEventModel.event_type == "CALIBRATION_AUTO_ACTIVATED"
                )
            )
        )
    assert len(activation_events) == 2
    with pytest.raises(DBAPIError):
        with session_factory.begin() as session:
            session.get(CalibrationRunModel, first.calibration_run_id).deployment_status = "active"
            stale = session.get(CalibrationRunModel, first.calibration_run_id)
            stale.activation_mode = "manual_rollback"
            stale.deactivated_at = None


def test_rollback_restores_predecessor_and_records_actor(session_factory, governance):
    users, outcome_service, _, _, registry, _ = governance
    first, second = _two_versions(session_factory, governance)
    outcome_service.rollback(second.calibration_run_id, users["auditor"], scope="controlled_demo")
    detail = registry.get_model("calibration", second.calibration_run_id, users["auditor"])
    assert detail["status"] == "ROLLED_BACK"
    restored = registry.get_model("calibration", first.calibration_run_id, users["auditor"])
    assert restored["status"] == "ACTIVE"
    rolled = [event for event in detail["events"] if event["to_status"] == "ROLLED_BACK"]
    assert rolled == [
        {**rolled[0], "from_status": "ACTIVE", "reason": "manual_rollback", "actor": "auditor.demo"}
    ]
    assert [event for event in restored["events"] if event["to_status"] == "ACTIVE"][-1]["actor"] == (
        "auditor.demo"
    )


def test_active_model_cannot_be_deleted_or_retired_but_others_can_retire(
    session_factory, governance
):
    users, _, _, _, registry, _ = governance
    first, second = _two_versions(session_factory, governance)
    with pytest.raises(DBAPIError, match="ACTIVE model cannot be deleted"):
        with session_factory.begin() as session:
            session.execute(
                text("DELETE FROM calibration_runs WHERE calibration_run_id = :id"),
                {"id": second.calibration_run_id},
            )
    with pytest.raises(RegistryConflict, match="ACTIVE model cannot be retired"):
        registry.retire(second.calibration_run_id, users["auditor"], reason_code="OBSOLETE")
    with pytest.raises(RegistryForbidden):
        registry.retire(first.calibration_run_id, users["risk"], reason_code="OBSOLETE")
    retired = registry.retire(first.calibration_run_id, users["auditor"], reason_code="OBSOLETE_BASELINE")
    assert retired["status"] == "RETIRED"
    assert retired["retirement_reason"] == "OBSOLETE_BASELINE"
    assert retired["can_retire"] is False
    with pytest.raises(RegistryConflict, match="already retired"):
        registry.retire(first.calibration_run_id, users["auditor"], reason_code="AGAIN")
    with session_factory() as session:
        assert session.scalar(
            select(func.count()).select_from(LedgerEventModel).where(
                LedgerEventModel.event_type == "CALIBRATION_MODEL_RETIRED"
            )
        ) == 1


def test_registry_artifact_hash_consistency_and_tamper_detection(session_factory, governance):
    users, _, _, drain, registry, _ = governance
    _seed_outcomes(session_factory, users, count=40, start=START)
    drain()
    (run,) = _runs(session_factory)
    detail = registry.get_model("calibration", run.calibration_run_id, users["auditor"])
    assert detail["artifact_check"] == {
        "expected_sha256": run.artifact_sha256,
        "actual_sha256": run.artifact_sha256,
        "consistent": True,
    }
    assert detail["artifact_hash"] == run.artifact_sha256
    Path(run.artifact_locator).write_bytes(Path(run.artifact_locator).read_bytes() + b" ")
    tampered = registry.get_model("calibration", run.calibration_run_id, users["auditor"])
    assert tampered["artifact_check"]["consistent"] is False


def test_registry_state_survives_a_restart(session_factory, governance, tmp_path):
    users, outcome_service, _, _, registry, _ = governance
    first, second = _two_versions(session_factory, governance)
    outcome_service.rollback(second.calibration_run_id, users["auditor"], scope="controlled_demo")
    before = registry.list_models(users["auditor"], scope="controlled_demo")

    restarted_outcomes = OutcomeService(session_factory, artifact_root=tmp_path)
    restarted_outcomes.reconcile_deployments()
    restarted = ModelRegistryService(session_factory).list_models(
        users["auditor"], scope="controlled_demo"
    )
    assert restarted == before
    assert restarted["active_by_scope"] == {"controlled_demo": str(first.calibration_run_id)}


def test_database_and_python_registry_projections_agree(session_factory):
    now = datetime.now(timezone.utc)
    cases = [
        (status, deployment, rolled, retired)
        for status in ("exploratory_candidate", "eligible_candidate", "failed")
        for deployment in (
            "not_deployed", "active", "superseded", "rejected", "activation_failed", "invalidated"
        )
        for rolled in (None, now)
        for retired in (None, now)
    ]
    with session_factory() as session:
        for status, deployment, rolled, retired in cases:
            database = session.scalar(
                text("SELECT calibration_registry_status(:s, :d, :r, :t)"),
                {"s": status, "d": deployment, "r": rolled, "t": retired},
            )
            assert database == calibration_registry_status(
                status=status, deployment_status=deployment, rolled_back_at=rolled, retired_at=retired
            ).value, (status, deployment, rolled, retired)


# --- 5, 6: Outcome review, correction and supersession ------------------------------


def test_every_outcome_is_reviewed_before_it_can_train(session_factory, governance):
    users, _, _, drain, _, _ = governance
    good = _seed_outcomes(session_factory, users, count=40, start=START)
    inconsistent = _seed_outcomes(
        session_factory, users, count=1, start=START + timedelta(days=5),
        default_for=lambda index: False, loss_for=lambda defaulted: Decimal("50.00"),
    )[0]
    extreme = _seed_outcomes(
        session_factory, users, count=1, start=START + timedelta(days=6),
        score_for=lambda index: 1.0,
    )[0]
    assert _review_history(session_factory, good[0])[:3] == [
        ("CREATED", None), ("REVIEWING", None), ("ELIGIBLE", None),
    ]
    assert _review_history(session_factory, inconsistent)[-1] == ("REJECTED", "BUSINESS_INCONSISTENT")
    assert _review_history(session_factory, extreme)[-1] == ("REJECTED", "DATA_QUALITY_INSUFFICIENT")
    drain()
    runs = _runs(session_factory)
    with session_factory() as session:
        members = {
            row.outcome_id
            for row in session.scalars(
                select(CalibrationRunObservationModel).where(
                    CalibrationRunObservationModel.calibration_run_id.in_(
                        [run.calibration_run_id for run in runs]
                    )
                )
            )
        }
    assert inconsistent not in members and extreme not in members
    assert set(good) <= members
    assert _review_history(session_factory, good[0])[-1][0] == "TRAINING_USED"


def test_scope_mismatched_outcome_is_rejected_by_review(session_factory, governance):
    users, *_ = governance
    (outcome_id,) = _seed_outcomes(session_factory, users, count=1, start=START)
    with session_factory.begin() as session:
        request_id = session.get(ActualOutcomeModel, outcome_id).request_id
        # A drifted request scope on re-review exposes the provenance mismatch.
        session.get(FinancingRequestModel, request_id).assessment_scope = "external_verified"
    with session_factory() as session:
        assert session.scalar(
            text("SELECT outcome_eligibility_reason(:id)"), {"id": outcome_id}
        ) == "SCOPE_MISMATCH"


def test_superseding_correction_keeps_original_and_removes_its_eligibility(
    session_factory, governance
):
    users, outcome_service, _, drain, _, clock = governance
    outcome_ids = _seed_outcomes(session_factory, users, count=40, start=START)
    drain()
    (active,) = _runs(session_factory)
    original_id = outcome_ids[3]
    with session_factory() as session:
        original_bytes = session.execute(
            text("SELECT to_jsonb(o) FROM actual_outcomes o WHERE outcome_id = :id"),
            {"id": original_id},
        ).scalar_one()
    clock[0] = START + timedelta(days=31)
    result = outcome_service.supersede(
        original_id,
        OutcomeSupersedeCreate(
            idempotency_key=uuid.uuid4(), defaulted=True, days_past_due=45,
            loss_amount="250.00", observed_at=START + timedelta(days=31),
            evidence_sha256="c" * 64, reason_code="LATE_DEFAULT_EVIDENCE",
            comment="Collections file shows the default was missed",
        ),
        users["auditor"],
    )
    corrected = result["outcome"]
    assert corrected["revision"] == 2
    assert corrected["supersedes_outcome_id"] == str(original_id)
    assert corrected["review_status"] == "ELIGIBLE"
    with session_factory() as session:
        assert session.execute(
            text("SELECT to_jsonb(o) FROM actual_outcomes o WHERE outcome_id = :id"),
            {"id": original_id},
        ).scalar_one() == original_bytes
        eligible = {row.outcome_id for row in OutcomeRepository().list_eligible_outcomes(
            session, scope="controlled_demo"
        )}
        invalidated = session.get(CalibrationRunModel, active.calibration_run_id)
    assert original_id not in eligible
    assert uuid.UUID(corrected["outcome_id"]) in eligible
    assert _review_history(session_factory, original_id)[-1] == ("REJECTED", "SUPERSEDED_BY_CORRECTION")
    assert invalidated.deployment_status == "invalidated"

    listed = outcome_service.list_outcomes(users["auditor"], limit=200)
    assert str(original_id) not in {row["outcome_id"] for row in listed}
    assert corrected["outcome_id"] in {row["outcome_id"] for row in listed}
    everything = outcome_service.list_outcomes(users["auditor"], limit=200, include_superseded=True)
    assert str(original_id) in {row["outcome_id"] for row in everything}

    lineage = outcome_service.lineage(original_id, users["auditor"])
    assert lineage["effective_outcome_id"] == corrected["outcome_id"]
    assert [item["is_effective"] for item in lineage["revisions"]] == [False, True]
    assert lineage["revisions"][0]["review_history"][-1]["reason_code"] == "SUPERSEDED_BY_CORRECTION"

    with pytest.raises(OutcomeConflict, match="effective outcome revision"):
        outcome_service.supersede(
            original_id,
            OutcomeSupersedeCreate(
                idempotency_key=uuid.uuid4(), defaulted=False, days_past_due=0,
                loss_amount="0.00", observed_at=START + timedelta(days=31),
                evidence_sha256="d" * 64, reason_code="SECOND_TRY", comment="Stale target",
            ),
            users["auditor"],
        )
    with pytest.raises(OutcomeConflict, match="Superseded outcomes cannot be corrected"):
        outcome_service.create_correction(
            original_id,
            OutcomeCorrectionCreate(
                idempotency_key=uuid.uuid4(), action="EXCLUDE", reason_code="BAD_EVIDENCE",
                comment="Old revision", evidence_sha256="e" * 64,
            ),
            users["auditor"],
        )
    with session_factory() as session:
        event = session.scalar(
            select(LedgerEventModel).where(LedgerEventModel.event_type == "ACTUAL_OUTCOME_SUPERSEDED")
        )
    assert event.payload["superseded_outcome_id"] == str(original_id)


def test_exclusion_correction_rejects_and_reinstatement_rereviews(session_factory, governance):
    users, outcome_service, *_ = governance
    (outcome_id,) = _seed_outcomes(session_factory, users, count=1, start=START)
    for action in ("EXCLUDE", "REINSTATE"):
        outcome_service.create_correction(
            outcome_id,
            OutcomeCorrectionCreate(
                idempotency_key=uuid.uuid4(), action=action, reason_code="EVIDENCE_REVIEW",
                comment=f"{action} after review", evidence_sha256="f" * 64,
            ),
            users["auditor"],
        )
    assert _review_history(session_factory, outcome_id)[-4:] == [
        ("ELIGIBLE", None),
        ("REJECTED", "MANUAL_CORRECTION"),
        ("REVIEWING", None),
        ("ELIGIBLE", None),
    ]


def test_governance_summary_counts_effective_rejected_and_superseded(session_factory, governance):
    users, outcome_service, *_ = governance
    good = _seed_outcomes(session_factory, users, count=3, start=START)
    _seed_outcomes(
        session_factory, users, count=1, start=START + timedelta(days=5),
        default_for=lambda index: False, loss_for=lambda defaulted: Decimal("50.00"),
    )
    outcome_service.supersede(
        good[0],
        OutcomeSupersedeCreate(
            idempotency_key=uuid.uuid4(), defaulted=False, days_past_due=0, loss_amount="0.00",
            observed_at=START + timedelta(days=1), evidence_sha256="1" * 64,
            reason_code="OBSERVATION_DATE", comment="Corrected observation time",
        ),
        users["auditor"],
    )
    summary = outcome_service.governance_summary(users["risk"])["scopes"]["controlled_demo"]
    assert summary["total_records"] == 5
    assert summary["superseded_records"] == 1
    assert summary["effective_outcomes"] == 4
    assert summary["training_eligible"] == 3
    assert summary["rejected_reasons"] == {"BUSINESS_INCONSISTENT": 1}


# --- 7: Scope compatibility ---------------------------------------------------------


@pytest.mark.parametrize(
    ("model_scope", "request_scope", "result"),
    (
        ("controlled_demo", "controlled_demo", ScopeResult.ALLOW),
        ("external_verified", "external_verified", ScopeResult.ALLOW),
        ("mixed", "mixed", ScopeResult.ALLOW_WITH_WARNING),
        ("controlled_demo", "external_verified", ScopeResult.REJECT),
        ("external_verified", "controlled_demo", ScopeResult.REJECT),
        ("mixed", "controlled_demo", ScopeResult.REJECT),
        ("controlled_demo", "mixed", ScopeResult.REJECT),
        ("unknown", "controlled_demo", ScopeResult.REJECT),
    ),
)
def test_scope_compatibility_matrix(model_scope, request_scope, result):
    decision = check_scope(model_scope, request_scope)
    assert decision.result == result
    assert decision.reason
    assert decision.permits_model == (result != ScopeResult.REJECT)
    assert len(scope_matrix()) == 9


def _assess(session_factory, *, scope: str):
    users = demo_users(session_factory)
    service = WorkflowService(session_factory)
    draft = service.create_draft(draft_payload(), users["supplier"])
    if scope != "controlled_demo":
        with session_factory.begin() as session:
            session.get(FinancingRequestModel, uuid.UUID(draft["request_id"])).assessment_scope = scope
    submitted = service.submit(draft["request_id"], 1, users["supplier"])
    confirmed = service.confirm_trade(
        draft["request_id"], submitted["version"], confirmed=True, comment="Verified",
        user=users["core_enterprise"], confirmed_payable_amount=Decimal("1500000.00"),
    )
    assessed = service.assess_risk(draft["request_id"], confirmed["version"], users["financier"])
    return service, users, assessed


def test_scope_mismatch_is_rejected_recorded_and_audited(session_factory, governance):
    users, _, _, drain, _, _ = governance
    _seed_outcomes(session_factory, users, count=40, start=START)
    drain()
    (active,) = _runs(session_factory)
    service, workflow_users, assessed = _assess(session_factory, scope="external_verified")
    assert assessed["risk_score"] == assessed["raw_risk_score"]
    assert assessed["risk_evidence"]["calibration_fallback_code"] == "calibration_scope_mismatch"
    (record,) = service.risk_decisions(assessed["request_id"], workflow_users["auditor"])
    assert record["scope_result"] == "reject"
    assert record["scope_reason"] == "demo_model_cannot_serve_verified_request"
    assert record["model_scope"] == "controlled_demo"
    assert record["calibration_run_id"] is None
    assert record["attempted_calibration_run_id"] == str(active.calibration_run_id)
    assert record["calibration_ledger_event"]["event_type"] == "RISK_CALIBRATION_FALLBACK"
    with session_factory() as session:
        fallback = session.scalar(
            select(LedgerEventModel).where(
                LedgerEventModel.event_type == "RISK_CALIBRATION_FALLBACK",
                LedgerEventModel.entity_id == uuid.UUID(assessed["request_id"]),
            )
        )
    assert fallback.payload["scope_result"] == "reject"
    assert fallback.payload["scope_reason"] == "demo_model_cannot_serve_verified_request"


# --- Risk decision audit ------------------------------------------------------------


def test_each_assessment_keeps_an_immutable_explainable_decision_record(
    session_factory, governance
):
    users, _, _, drain, _, _ = governance
    _seed_outcomes(session_factory, users, count=40, start=START)
    drain()
    (active,) = _runs(session_factory)
    service, workflow_users, assessed = _assess(session_factory, scope="controlled_demo")
    (record,) = service.risk_decisions(assessed["request_id"], workflow_users["financier"])
    assert record["assessed_by"] == "financier.demo"
    assert record["actor_role"] == "financier"
    assert record["input_sha256"] == assessed["risk_evidence"]["input_sha256"]
    assert record["input_snapshot"] == assessed["features"]
    assert record["engine_version"] == assessed["risk_evidence"]["engine_version"]
    assert record["calibration_run_id"] == str(active.calibration_run_id)
    assert record["calibration_artifact_sha256"] == active.artifact_sha256
    assert record["scope_result"] == "allow"
    assert record["final_score"] == assessed["risk_score"]
    assert record["raw_score"] == assessed["raw_risk_score"]
    assert record["band"] in {"low", "medium", "high"}
    assert record["calibration_ledger_event"]["event_type"] == "RISK_CALIBRATION_APPLIED"
    with pytest.raises(DBAPIError, match="governed history is immutable"):
        with session_factory.begin() as session:
            session.execute(text("UPDATE risk_decision_records SET final_score = 0.01"))
    outsider = dataclasses.replace(workflow_users["supplier"], organization_id=uuid.uuid4())
    with pytest.raises(ApplicationNotFound):
        service.risk_decisions(assessed["request_id"], outsider)


# --- API ----------------------------------------------------------------------------


def test_governance_api_exposes_registry_feedback_and_decisions(
    migrated_engine, session_factory, governance
):
    from app.database import Database
    from app.main import create_app

    users, _, _, drain, _, _ = governance
    _seed_outcomes(session_factory, users, count=40, start=START)
    drain()
    (active,) = _runs(session_factory)
    with TestClient(
        create_app(Database(migrated_engine, session_factory), calibration_worker_enabled=False)
    ) as client:
        assert client.get("/api/v1/model-registry").status_code == 401
        client.post("/api/v1/auth/login", json={"username": "auditor.demo", "password": "Demo123!"})
        registry = client.get("/api/v1/model-registry").json()
        assert registry["active_by_scope"]["controlled_demo"] == str(active.calibration_run_id)
        assert {row["model_kind"] for row in registry["models"]} >= {"calibration"}
        detail = client.get(f"/api/v1/model-registry/calibration/{active.calibration_run_id}")
        assert detail.status_code == 200
        assert detail.json()["artifact_check"]["consistent"] is True
        retire_active = client.post(
            f"/api/v1/model-registry/calibration/{active.calibration_run_id}/retire",
            json={"reason_code": "OBSOLETE"},
        )
        assert retire_active.status_code == 409
        summary = client.get("/api/v1/outcome-governance/summary").json()
        assert summary["scopes"]["controlled_demo"]["training_eligible"] == 40
        matrix = client.get("/api/v1/model-registry/scope-compatibility").json()
        assert {"model_scope": "controlled_demo", "request_scope": "external_verified",
                "result": "reject", "reason": "demo_model_cannot_serve_verified_request"} in matrix
        client.cookies.clear()
        client.post("/api/v1/auth/login", json={"username": "supplier.demo", "password": "Demo123!"})
        assert client.get("/api/v1/model-registry").status_code == 403
        assert client.get("/api/v1/outcome-governance/summary").status_code == 403
