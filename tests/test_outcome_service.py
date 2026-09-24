from __future__ import annotations

from facility_fixtures import confirmed_cash_rows

import hashlib
import json
import math
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from threading import Barrier

import pytest
from sqlalchemy import event, func, select, text

from app.identity import AuthenticatedUser
from app.models import FinancingRequestModel, LedgerEventModel
from app.models_facility import FinancingFacilityModel
from app.models_governance import (
    CalibrationJobModel,
    CalibrationRunObservationModel,
    OutcomeCorrectionModel,
)
from app.models_lifecycle import (
    FacilityDefaultModel,
    FacilityDelinquencyModel,
    FacilityWriteOffModel,
)
from app.models_outcome import ActualOutcomeModel, CalibrationRunModel
from app.models_model_governance import RiskModelVersionModel, RiskModelVersionTransitionModel
from app.models_research import (
    DatasetVersionModel,
    GraphSnapshotModel,
    ModelRunModel,
    ModelVersionModel,
    RiskAssessmentModel,
)
from app.repositories.outcomes import OutcomeRepository
from app.schemas_outcome import ActualOutcomeCreate, OutcomeCorrectionCreate
from app.services.adaptive_risk import AdaptiveRiskInferenceService
from app.services.identity import IdentityService
from app.services.outcome_calibration import (
    CalibrationCandidate,
    CalibrationObservation,
    CalibrationTrainingConfig,
    build_calibration_candidate,
    write_candidate_artifact,
)
from app.services.outcomes import (
    ForbiddenOutcome,
    OutcomeConflict,
    OutcomeService,
)


NOW = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)


def _seed_closed_facility(
    session_factory,
    *,
    scope: str = "controlled_demo",
    lifecycle: str = "repaid",
    status: str = "closed",
) -> tuple[uuid.UUID, AuthenticatedUser]:
    identity = IdentityService(session_factory, clock=lambda: NOW)
    identity.seed_demo_accounts()
    auditor = identity.login("auditor.demo", "Demo123!").user
    financier = identity.login("financier.demo", "Demo123!").user
    risk = identity.login("risk.demo", "Demo123!").user
    ids = {name: uuid.uuid4() for name in (
        "dataset", "snapshot", "model_run", "model_version", "assessment",
        "request", "facility",
    )}
    semantic_version = str(uuid.uuid4())
    with session_factory.begin() as session:
        session.add(DatasetVersionModel(
            dataset_version_id=ids["dataset"], name="outcome-test",
            version=str(uuid.uuid4()), generation_seed=1, schema_version="v1",
            manifest={}, content_sha256=uuid.uuid4().hex * 2, created_at=NOW,
        ))
        session.add(GraphSnapshotModel(
            graph_snapshot_id=ids["snapshot"], dataset_version_id=ids["dataset"],
            synthetic_scenario_id=None, scenario_revision=None, overlay_sha256=None,
            anchor_month=12, window_start_month=1, window_end_month=12,
            feature_schema_version="v1", normalization_id="norm-v1",
            node_ordering_sha256="1" * 64, adjacency_sha256="2" * 64,
            feature_sha256="3" * 64, content_sha256=uuid.uuid4().hex * 2,
            storage_locator="memory://snapshot", created_at=NOW,
        ))
        session.add(ModelRunModel(
            model_run_id=ids["model_run"], model_family="tgnn", run_seed=1,
            dataset_version_id=ids["dataset"], configuration={}, status="completed",
            started_at=NOW, ended_at=NOW, metrics={},
        ))
        session.add(ModelVersionModel(
            model_version_id=ids["model_version"], model_name="tgnn-test",
            semantic_version=semantic_version, model_family="tgnn",
            source_run_id=ids["model_run"], dataset_version_id=ids["dataset"],
            feature_schema_version="v1", inference_format="onnx",
            artifact_locator="memory://model", checkpoint_sha256="4" * 64,
            metrics={}, lifecycle_status="candidate", deployment_slot=None,
            created_at=NOW,
        ))
        session.add(RiskAssessmentModel(
            risk_assessment_id=ids["assessment"], enterprise_id="E0001",
            graph_snapshot_id=ids["snapshot"], model_version_id=ids["model_version"],
            input_sha256="5" * 64, risk_score=0.70, band="HIGH",
            explanations=[], inferred_at=NOW,
        ))
        session.add(FinancingRequestModel(
            request_id=ids["request"], created_at=NOW, updated_at=NOW,
            applicant_id="E0001", assessment_scope=scope,
            amount=Decimal("1000.00"), term_days=30, features={}, risk_score=0.70,
            decision="approved", status="audited", version=1,
            risk_assessment_id=ids["assessment"],
            risk_engine_version=f"tgnn-test@{semantic_version}",
            risk_input_sha256="5" * 64, risk_assessed_at=NOW,
        ))
        closure_reason = (
            lifecycle
            if lifecycle in {"repaid", "settled_after_default"}
            else "written_off"
        )
        session.add(FinancingFacilityModel(
            facility_id=ids["facility"], request_id=ids["request"],
            principal=Decimal("1000.00"), outstanding_amount=Decimal("0.00"),
            currency="CNY", status=status, version=7,
            current_schedule_version=1,
            closure_reason=closure_reason if status == "closed" else None,
            created_by_user_id=financier.user_id, created_at=NOW, updated_at=NOW,
            closed_at=NOW if status == "closed" else None,
        ))
        session.add_all(confirmed_cash_rows(
            ids["facility"], financier.user_id, NOW,
            "300.00" if lifecycle == "written_off" else "1000.00",
        ))
        if lifecycle in {"settled_after_default", "written_off"}:
            session.add(FacilityDelinquencyModel(
                delinquency_id=uuid.uuid4(), facility_id=ids["facility"],
                marked_by_user_id=financier.user_id, days_past_due=45,
                reason_code="PAYMENT_OVERDUE", comment="Verified delinquency",
                evidence_sha256="6" * 64, recorded_at=NOW,
            ))
            session.add(FacilityDefaultModel(
                default_id=uuid.uuid4(), facility_id=ids["facility"],
                declared_by_user_id=risk.user_id, defaulted_at=NOW,
                days_past_due=30, reason_code="PAYMENT_DEFAULT",
                comment="Governed default", evidence_sha256="7" * 64,
                recorded_at=NOW,
            ))
            if lifecycle == "written_off":
                session.add(FacilityWriteOffModel(
                    writeoff_id=uuid.uuid4(), facility_id=ids["facility"],
                    amount=Decimal("700.00"), auditor_user_id=auditor.user_id,
                    reason_code="UNCOLLECTIBLE", comment="Approved write-off",
                    evidence_sha256="8" * 64, recorded_at=NOW,
                ))
    return ids["facility"], auditor


def _submission(**changes) -> ActualOutcomeCreate:
    values = {
        "idempotency_key": uuid.uuid4(),
        "observed_at": NOW,
        "evidence_sha256": "a" * 64,
        "provenance": "CONTROLLED_DEMO",
    }
    values.update(changes)
    return ActualOutcomeCreate.model_validate(values)


def _correction(action: str, **changes) -> OutcomeCorrectionCreate:
    values = {
        "idempotency_key": uuid.uuid4(), "action": action,
        "reason_code": "INCONSISTENT_LIFECYCLE",
        "comment": "Verified against immutable lifecycle evidence",
        "evidence_sha256": "b" * 64,
    }
    values.update(changes)
    return OutcomeCorrectionCreate.model_validate(values)


def _active_run_with_membership(session_factory, outcome_id: uuid.UUID, scope: str):
    run_id = uuid.uuid4()
    with session_factory.begin() as session:
        session.add(CalibrationRunModel(
            calibration_run_id=run_id, trigger_outcome_id=None, trigger_job_id=None,
            dataset_sha256=uuid.uuid4().hex * 2, sample_count=20,
            positive_count=10, negative_count=10,
            metrics_before={"brier": 0.2}, metrics_after={"brier": 0.1},
            configuration={}, status="eligible_candidate",
            artifact_locator="memory://artifact", artifact_sha256="c" * 64,
            artifact_schema="daibm.platt-calibration.v3", failure_code=None,
            deployment_status="active", deployment_scope=scope,
            activation_mode="automatic", activated_at=NOW, deactivated_at=None,
            previous_active_run_id=None, activation_reason="oof_improved",
            started_at=NOW, completed_at=NOW,
        ))
        session.flush()
        session.add(CalibrationRunObservationModel(
            calibration_run_id=run_id, outcome_id=outcome_id,
            correction_head_id=None,
        ))
        _register_active_version(session, session.get(CalibrationRunModel, run_id))
    return run_id


def _register_active_version(session, run: CalibrationRunModel) -> None:
    """Fixture runs are inserted ACTIVE, so their registry version is too."""

    version_id = uuid.uuid4()
    evaluation = {"source": "test_fixture", "passed": True}
    session.add(RiskModelVersionModel(
        id=version_id, model_id=f"calibration:{run.deployment_scope}",
        version=1 + (session.scalar(
            select(func.count()).select_from(RiskModelVersionModel).where(
                RiskModelVersionModel.scope == run.deployment_scope
            )
        ) or 0),
        model_type="platt_calibration", calibration_run_id=run.calibration_run_id,
        artifact_path=run.artifact_locator, artifact_hash=run.artifact_sha256,
        scope=run.deployment_scope, training_dataset_version=run.dataset_sha256,
        metrics={}, evaluation=evaluation, evaluation_passed=True, evaluated_at=NOW,
        status="ACTIVE", status_sequence=1, created_by="test_fixture",
        created_at=NOW, activated_at=NOW, promotion_reason=run.activation_reason,
    ))
    session.flush()
    session.add(RiskModelVersionTransitionModel(
        model_version_id=version_id, from_status=None, to_status="ACTIVE",
        status_sequence=1, reason="test_fixture", actor_label="test_fixture",
        evaluation_metrics=evaluation, artifact_hash=run.artifact_sha256,
    ))


def _candidate_run_with_membership(
    session_factory,
    outcome_id: uuid.UUID,
    *,
    scope: str = "controlled_demo",
) -> tuple[uuid.UUID, CalibrationCandidate]:
    run_id = uuid.uuid4()
    dataset_sha256 = uuid.uuid4().hex * 2
    metrics_before = {"brier_score": 0.25, "log_loss": 0.70}
    metrics_after = {"brier_score": 0.20, "log_loss": 0.60}
    candidate = CalibrationCandidate(
        dataset_sha256=dataset_sha256,
        sample_count=30,
        positive_count=5,
        negative_count=25,
        status="eligible_candidate",
        slope=1.0,
        intercept=0.0,
        metrics_before=metrics_before,
        metrics_after=metrics_after,
        artifact={},
        artifact_bytes=b"{}",
    )
    with session_factory.begin() as session:
        session.add(
            CalibrationRunModel(
                calibration_run_id=run_id,
                trigger_outcome_id=None,
                trigger_job_id=None,
                dataset_sha256=dataset_sha256,
                sample_count=30,
                positive_count=5,
                negative_count=25,
                metrics_before=metrics_before,
                metrics_after=metrics_after,
                configuration={},
                status="eligible_candidate",
                artifact_locator="memory://candidate",
                artifact_sha256="c" * 64,
                artifact_schema="daibm.platt-calibration.v4",
                failure_code=None,
                deployment_status="not_deployed",
                deployment_scope=scope,
                activation_mode=None,
                activated_at=None,
                deactivated_at=None,
                previous_active_run_id=None,
                activation_reason="not_evaluated",
                started_at=NOW,
                completed_at=NOW,
            )
        )
        session.flush()
        session.add(
            CalibrationRunObservationModel(
                calibration_run_id=run_id,
                outcome_id=outcome_id,
                correction_head_id=None,
            )
        )
    return run_id, candidate


def _governance_counts(session_factory) -> tuple[int, int, int]:
    with session_factory() as session:
        return (
            session.scalar(select(func.count()).select_from(OutcomeCorrectionModel)),
            session.scalar(select(func.count()).select_from(CalibrationJobModel)),
            session.scalar(select(func.count()).select_from(LedgerEventModel)),
        )


def _write_candidate_artifact(tmp_path, run_id, dataset_sha256, outcome_id):
    artifact = {
        "artifact_schema": "daibm.platt-calibration.v2",
        "coefficients": {"slope": 1.0, "intercept": 0.0},
        "configuration": {"probability_epsilon": 0.000001},
        "dataset": {
            "sha256": dataset_sha256,
            "outcome_ids": [str(outcome_id)],
        },
    }
    artifact_bytes = json.dumps(
        artifact, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    artifact_sha256 = hashlib.sha256(artifact_bytes).hexdigest()
    path = tmp_path / f"{run_id}.json"
    path.write_bytes(artifact_bytes)
    return path, artifact_sha256


def test_submission_derives_written_off_facts_and_queues_job_atomically(
    session_factory, tmp_path
):
    facility_id, auditor = _seed_closed_facility(
        session_factory, lifecycle="written_off"
    )
    service = OutcomeService(session_factory, artifact_root=tmp_path, clock=lambda: NOW)

    result = service.submit(facility_id, _submission(), auditor)

    assert result["outcome"]["defaulted"] is True
    assert result["outcome"]["days_past_due"] == 45
    assert result["outcome"]["loss_amount"] == "700.00"
    assert result["outcome"]["effective_training_eligible"] is True
    assert result["calibration_job"]["status"] == "queued"
    assert result["calibration_job"]["deployment_scope"] == "controlled_demo"
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ActualOutcomeModel)) == 1
        assert session.scalar(select(func.count()).select_from(CalibrationJobModel)) == 1
        assert session.scalar(select(func.count()).select_from(CalibrationRunModel)) == 0


def test_submission_repaid_facts_replay_and_conflicting_key(session_factory, tmp_path):
    facility_id, auditor = _seed_closed_facility(session_factory)
    service = OutcomeService(session_factory, artifact_root=tmp_path, clock=lambda: NOW)
    payload = _submission()
    first = service.submit(facility_id, payload, auditor)

    assert first["outcome"]["defaulted"] is False
    assert first["outcome"]["days_past_due"] == 0
    assert first["outcome"]["loss_amount"] == "0.00"
    assert service.submit(facility_id, payload, auditor) == first
    with pytest.raises(OutcomeConflict, match="already has"):
        service.submit(
            facility_id,
            _submission(
                observed_at=payload.observed_at,
                evidence_sha256=payload.evidence_sha256,
                provenance=payload.provenance,
            ),
            auditor,
        )
    with pytest.raises(OutcomeConflict, match="Idempotency key"):
        service.submit(
            facility_id,
            _submission(
                idempotency_key=payload.idempotency_key,
                evidence_sha256="d" * 64,
            ),
            auditor,
        )


def test_settled_after_default_preserves_default_lineage_with_zero_loss(
    session_factory, tmp_path
):
    facility_id, auditor = _seed_closed_facility(
        session_factory, lifecycle="settled_after_default"
    )
    result = OutcomeService(
        session_factory, artifact_root=tmp_path, clock=lambda: NOW
    ).submit(facility_id, _submission(), auditor)

    assert result["outcome"]["defaulted"] is True
    assert result["outcome"]["days_past_due"] == 45
    assert result["outcome"]["loss_amount"] == "0.00"


def test_submission_requires_exact_scope_and_closed_governed_snapshot(
    session_factory, tmp_path
):
    facility_id, auditor = _seed_closed_facility(session_factory)
    service = OutcomeService(session_factory, artifact_root=tmp_path, clock=lambda: NOW)
    with pytest.raises(OutcomeConflict, match="provenance"):
        service.submit(
            facility_id, _submission(provenance="EXTERNAL_VERIFIED"), auditor
        )
    # PostgreSQL rejects reopening a closed facility, so the unclosed case is a
    # facility seeded while still active rather than a rewritten closed one.
    with pytest.raises(Exception, match="illegal facility status transition closed -> active"):
        with session_factory.begin() as session:
            session.get(FinancingFacilityModel, facility_id).status = "active"
    active_id, _ = _seed_closed_facility(session_factory, status="active")
    with pytest.raises(OutcomeConflict, match="closed facility"):
        service.submit(active_id, _submission(), auditor)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("risk_input_sha256", None, "prediction lineage is incomplete"),
        ("risk_engine_version", "tampered@engine", "engine lineage disagrees"),
    ),
)
def test_submission_rejects_incomplete_or_inconsistent_prediction_lineage(
    session_factory, tmp_path, field, value, message
):
    facility_id, auditor = _seed_closed_facility(session_factory)
    with session_factory.begin() as session:
        facility = session.get(FinancingFacilityModel, facility_id)
        application = session.get(FinancingRequestModel, facility.request_id)
        setattr(application, field, value)

    with pytest.raises(OutcomeConflict, match=message):
        OutcomeService(
            session_factory, artifact_root=tmp_path, clock=lambda: NOW
        ).submit(facility_id, _submission(), auditor)


def test_job_insert_failure_rolls_back_outcome_and_ledger(session_factory, tmp_path):
    facility_id, auditor = _seed_closed_facility(session_factory)

    class FailingJobRepository(OutcomeRepository):
        def add_job(self, session, job):
            raise RuntimeError("job unavailable")

    service = OutcomeService(
        session_factory, artifact_root=tmp_path, repository=FailingJobRepository(),
        clock=lambda: NOW,
    )
    with pytest.raises(RuntimeError, match="job unavailable"):
        service.submit(facility_id, _submission(), auditor)
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ActualOutcomeModel)) == 0
        assert session.scalar(select(func.count()).select_from(LedgerEventModel)) == 0


def test_exclude_is_append_only_invalidates_only_exact_membership_and_queues(
    session_factory, tmp_path
):
    facility_id, auditor = _seed_closed_facility(session_factory)
    service = OutcomeService(session_factory, artifact_root=tmp_path, clock=lambda: NOW)
    submitted = service.submit(facility_id, _submission(), auditor)
    outcome_id = uuid.UUID(submitted["outcome"]["outcome_id"])
    immutable_facts = {
        key: submitted["outcome"][key]
        for key in ("defaulted", "days_past_due", "loss_amount")
    }
    affected = _active_run_with_membership(session_factory, outcome_id, "controlled_demo")
    other_scope = _active_run_with_membership(
        session_factory, outcome_id, "external_verified"
    )

    result = service.create_correction(outcome_id, _correction("EXCLUDE"), auditor)

    assert result["effective_training_eligible"] is False
    assert result["invalidated_run_ids"] == [str(affected)]
    assert result["calibration_job"]["trigger_type"] == "correction_exclude"
    with session_factory() as session:
        assert session.get(ActualOutcomeModel, outcome_id) is not None
        persisted = session.get(ActualOutcomeModel, outcome_id)
        assert {
            "defaulted": persisted.defaulted,
            "days_past_due": persisted.days_past_due,
            "loss_amount": f"{persisted.loss_amount:.2f}",
        } == immutable_facts
        assert session.get(CalibrationRunModel, affected).deployment_status == "invalidated"
        assert session.get(CalibrationRunModel, other_scope).deployment_status == "active"
        assert session.scalar(select(func.count()).select_from(OutcomeCorrectionModel)) == 1


def test_exclude_uses_immutable_outcome_scope_when_request_scope_drifted(
    session_factory, tmp_path
):
    facility_id, auditor = _seed_closed_facility(session_factory)
    service = OutcomeService(session_factory, artifact_root=tmp_path, clock=lambda: NOW)
    outcome_id = uuid.UUID(
        service.submit(facility_id, _submission(), auditor)["outcome"]["outcome_id"]
    )
    controlled_run = _active_run_with_membership(
        session_factory, outcome_id, "controlled_demo"
    )
    external_run = _active_run_with_membership(
        session_factory, outcome_id, "external_verified"
    )
    with session_factory.begin() as session:
        facility = session.get(FinancingFacilityModel, facility_id)
        application = session.get(FinancingRequestModel, facility.request_id)
        application.assessment_scope = "external_verified"

    result = service.create_correction(
        outcome_id, _correction("EXCLUDE"), auditor
    )

    assert result["invalidated_run_ids"] == [str(controlled_run)]
    assert result["calibration_job"]["deployment_scope"] == "controlled_demo"
    with session_factory() as session:
        assert session.get(CalibrationRunModel, controlled_run).deployment_status == "invalidated"
        assert session.get(CalibrationRunModel, external_run).deployment_status == "active"
        event = session.scalar(
            select(LedgerEventModel)
            .where(LedgerEventModel.event_type == "OUTCOME_TRAINING_EXCLUDED")
            .order_by(LedgerEventModel.id.desc())
        )
    assert event.payload["deployment_scope"] == "controlled_demo"


def test_exclude_and_activation_share_scope_lock_and_leave_no_active_member(
    session_factory, tmp_path, monkeypatch
):
    facility_id, auditor = _seed_closed_facility(session_factory)
    submitted = OutcomeService(session_factory, artifact_root=tmp_path, clock=lambda: NOW).submit(
        facility_id, _submission(), auditor
    )
    outcome_id = uuid.UUID(submitted["outcome"]["outcome_id"])
    run_id, candidate = _candidate_run_with_membership(session_factory, outcome_id)
    rendezvous = Barrier(2)

    class CoordinatedRepository(OutcomeRepository):
        def _rendezvous(self, session):
            session.execute(text("SET LOCAL lock_timeout = '5s'"))
            rendezvous.wait(timeout=5)

        def acquire_training_lock(self, session):
            self._rendezvous(session)
            return super().acquire_training_lock(session)

        def acquire_scope_lock(self, session, *, scope):
            self._rendezvous(session)
            return super().acquire_scope_lock(session, scope=scope)

    repository = CoordinatedRepository()
    service = OutcomeService(
        session_factory,
        artifact_root=tmp_path,
        repository=repository,
        clock=lambda: NOW,
    )
    monkeypatch.setattr(
        "app.services.outcomes.load_verified_calibration",
        lambda *_args, **_kwargs: object(),
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        activation = pool.submit(
            service._evaluate_deployment,
            run_id,
            candidate,
            provenances=("CONTROLLED_DEMO",) * 20,
        )
        exclusion = pool.submit(
            service.create_correction,
            outcome_id,
            _correction("EXCLUDE"),
            auditor,
        )
        activation.result(timeout=10)
        exclusion.result(timeout=10)

    with session_factory() as session:
        run = session.get(CalibrationRunModel, run_id)
        active_members = session.scalar(
            select(func.count())
            .select_from(CalibrationRunModel)
            .join(CalibrationRunObservationModel)
            .where(
                CalibrationRunObservationModel.outcome_id == outcome_id,
                CalibrationRunModel.deployment_status == "active",
            )
        )
        inference = AdaptiveRiskInferenceService().assess(
            session, 0.61, "controlled_demo", run.organization_id
        )
    assert run.deployment_status in {"invalidated", "rejected"}
    assert active_members == 0
    assert inference.final_score == 0.61
    assert inference.calibration_run_id is None


def test_correction_replay_noop_conflict_and_reinstate(session_factory, tmp_path):
    facility_id, auditor = _seed_closed_facility(session_factory)
    service = OutcomeService(session_factory, artifact_root=tmp_path, clock=lambda: NOW)
    outcome_id = service.submit(facility_id, _submission(), auditor)["outcome"]["outcome_id"]
    exclude = _correction("EXCLUDE")
    first = service.create_correction(outcome_id, exclude, auditor)
    assert service.create_correction(outcome_id, exclude, auditor) == first
    with pytest.raises(OutcomeConflict, match="different correction semantics"):
        service.create_correction(
            outcome_id,
            _correction("REINSTATE", idempotency_key=exclude.idempotency_key),
            auditor,
        )
    with pytest.raises(OutcomeConflict, match="already excluded"):
        service.create_correction(outcome_id, _correction("EXCLUDE"), auditor)
    reinstated = service.create_correction(
        outcome_id, _correction("REINSTATE", reason_code="LIFECYCLE_VERIFIED"), auditor
    )
    assert reinstated["effective_training_eligible"] is True
    assert reinstated["invalidated_run_ids"] == []
    assert reinstated["calibration_job"]["trigger_type"] == "correction_reinstate"


def test_reinstate_revalidates_prediction_lineage(session_factory, tmp_path):
    facility_id, auditor = _seed_closed_facility(session_factory)
    service = OutcomeService(session_factory, artifact_root=tmp_path, clock=lambda: NOW)
    outcome_id = service.submit(facility_id, _submission(), auditor)["outcome"]["outcome_id"]
    service.create_correction(outcome_id, _correction("EXCLUDE"), auditor)
    with session_factory.begin() as session:
        facility = session.get(FinancingFacilityModel, facility_id)
        application = session.get(FinancingRequestModel, facility.request_id)
        application.risk_engine_version = "tampered@lineage"

    with pytest.raises(OutcomeConflict, match="engine lineage"):
        service.create_correction(
            outcome_id,
            _correction("REINSTATE", reason_code="LIFECYCLE_VERIFIED"),
            auditor,
        )
    assert len(service.list_corrections(outcome_id, auditor)) == 1


def test_reinstate_rejects_changed_assessment_identity_and_rolls_back(
    session_factory, tmp_path
):
    facility_id, auditor = _seed_closed_facility(session_factory)
    service = OutcomeService(session_factory, artifact_root=tmp_path, clock=lambda: NOW)
    outcome_id = uuid.UUID(
        service.submit(facility_id, _submission(), auditor)["outcome"]["outcome_id"]
    )
    service.create_correction(outcome_id, _correction("EXCLUDE"), auditor)
    with session_factory.begin() as session:
        facility = session.get(FinancingFacilityModel, facility_id)
        application = session.get(FinancingRequestModel, facility.request_id)
        application.risk_assessment_id = uuid.uuid4()
    before = _governance_counts(session_factory)

    with pytest.raises(OutcomeConflict, match="assessment identity"):
        service.create_correction(
            outcome_id,
            _correction("REINSTATE", reason_code="LIFECYCLE_VERIFIED"),
            auditor,
        )

    assert _governance_counts(session_factory) == before


def test_reinstate_rejects_changed_baseline_assessment_identity(
    session_factory, tmp_path
):
    facility_id, auditor = _seed_closed_facility(session_factory)
    first_assessment_id = uuid.uuid4()
    with session_factory.begin() as session:
        facility = session.get(FinancingFacilityModel, facility_id)
        application = session.get(FinancingRequestModel, facility.request_id)
        application.risk_assessment_id = first_assessment_id
        application.risk_engine_version = "transparent_logistic_baseline_v0.1"
    service = OutcomeService(session_factory, artifact_root=tmp_path, clock=lambda: NOW)
    outcome_id = uuid.UUID(
        service.submit(facility_id, _submission(), auditor)["outcome"]["outcome_id"]
    )
    service.create_correction(outcome_id, _correction("EXCLUDE"), auditor)
    with session_factory.begin() as session:
        facility = session.get(FinancingFacilityModel, facility_id)
        application = session.get(FinancingRequestModel, facility.request_id)
        application.risk_assessment_id = uuid.uuid4()
    before = _governance_counts(session_factory)

    with pytest.raises(OutcomeConflict, match="assessment identity"):
        service.create_correction(
            outcome_id,
            _correction("REINSTATE", reason_code="LIFECYCLE_VERIFIED"),
            auditor,
        )

    assert _governance_counts(session_factory) == before


def test_correction_job_failure_rolls_back_append_and_invalidation(
    session_factory, tmp_path
):
    facility_id, auditor = _seed_closed_facility(session_factory)
    normal = OutcomeService(session_factory, artifact_root=tmp_path, clock=lambda: NOW)
    outcome_id = uuid.UUID(
        normal.submit(facility_id, _submission(), auditor)["outcome"]["outcome_id"]
    )
    run_id = _active_run_with_membership(session_factory, outcome_id, "controlled_demo")

    class FailingJobRepository(OutcomeRepository):
        def add_job(self, session, job):
            raise RuntimeError("job unavailable")

    service = OutcomeService(
        session_factory, artifact_root=tmp_path, repository=FailingJobRepository(),
        clock=lambda: NOW,
    )
    with pytest.raises(RuntimeError, match="job unavailable"):
        service.create_correction(outcome_id, _correction("EXCLUDE"), auditor)
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(OutcomeCorrectionModel)) == 0
        assert session.get(CalibrationRunModel, run_id).deployment_status == "active"


def test_corrections_are_auditor_only_and_concurrent_actions_do_not_deadlock(
    session_factory, tmp_path
):
    facility_id, auditor = _seed_closed_facility(session_factory)
    service = OutcomeService(session_factory, artifact_root=tmp_path, clock=lambda: NOW)
    outcome_id = service.submit(facility_id, _submission(), auditor)["outcome"]["outcome_id"]
    financier = IdentityService(session_factory).login(
        "financier.demo", "Demo123!"
    ).user
    with pytest.raises(ForbiddenOutcome):
        service.create_correction(outcome_id, _correction("EXCLUDE"), financier)

    commands = (_correction("EXCLUDE"), _correction("REINSTATE"))
    def apply(command):
        try:
            return service.create_correction(outcome_id, command, auditor)
        except OutcomeConflict as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(apply, commands))
    assert all(not isinstance(item, Exception) or isinstance(item, OutcomeConflict) for item in results)
    history = service.list_corrections(outcome_id, auditor)
    assert 1 <= len(history) <= 2
    assert [item["action"] for item in history] in (
        ["EXCLUDE"],
        ["EXCLUDE", "REINSTATE"],
    )


def test_preview_returns_only_server_derived_facts(session_factory, tmp_path):
    facility_id, auditor = _seed_closed_facility(
        session_factory, scope="external_verified", lifecycle="written_off"
    )
    service = OutcomeService(session_factory, artifact_root=tmp_path, clock=lambda: NOW)

    preview = service.preview(facility_id, auditor)

    assert preview == {
        "facility_id": str(facility_id), "defaulted": True,
        "days_past_due": 45, "loss_amount": "700.00",
        "closure_reason": "written_off",
        "closed_at": "2026-08-24T12:00:00.000000+00:00",
        "expected_provenance": "EXTERNAL_VERIFIED",
        "deployment_scope": "external_verified",
    }


def test_list_outcomes_fetches_latest_eligibility_in_one_constant_query(
    session_factory, tmp_path
):
    service = OutcomeService(session_factory, artifact_root=tmp_path, clock=lambda: NOW)
    auditor = None
    outcome_ids = []
    for _index in range(2):
        facility_id, auditor = _seed_closed_facility(session_factory)
        outcome_ids.append(
            service.submit(facility_id, _submission(), auditor)["outcome"]["outcome_id"]
        )
    service.create_correction(outcome_ids[0], _correction("EXCLUDE"), auditor)

    engine = session_factory.kw["bind"]
    statements: list[str] = []

    def record_select(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    def measured_list() -> tuple[list[dict], int]:
        statements.clear()
        event.listen(engine, "before_cursor_execute", record_select)
        try:
            result = service.list_outcomes(auditor, limit=200)
        finally:
            event.remove(engine, "before_cursor_execute", record_select)
        return result, len(statements)

    first_page, small_query_count = measured_list()
    for _index in range(4):
        facility_id, auditor = _seed_closed_facility(session_factory)
        service.submit(facility_id, _submission(), auditor)
    larger_page, large_query_count = measured_list()

    eligibility = {
        item["outcome_id"]: item["effective_training_eligible"]
        for item in first_page
    }
    assert eligibility[outcome_ids[0]] is False
    assert eligibility[outcome_ids[1]] is True
    assert len(larger_page) == 6
    assert small_query_count == large_query_count == 1


def test_seeded_deployment_rollback_preserves_expected_version_contract(session_factory, tmp_path):
    facility_id, auditor = _seed_closed_facility(session_factory)
    service = OutcomeService(session_factory, artifact_root=tmp_path, clock=lambda: NOW)
    outcome_id = uuid.UUID(
        service.submit(facility_id, _submission(), auditor)["outcome"]["outcome_id"]
    )
    active_id = _active_run_with_membership(session_factory, outcome_id, "controlled_demo")
    predecessor_id = uuid.uuid4()
    predecessor_hash = uuid.uuid4().hex * 2
    predecessor_path, predecessor_artifact_hash = _write_candidate_artifact(
        tmp_path, predecessor_id, predecessor_hash, outcome_id
    )
    with session_factory.begin() as session:
        session.add(
            CalibrationRunModel(
                calibration_run_id=predecessor_id,
                trigger_outcome_id=None,
                trigger_job_id=None,
                dataset_sha256=predecessor_hash,
                sample_count=19,
                positive_count=5,
                negative_count=14,
                metrics_before={"brier": 0.2},
                metrics_after={"brier": 0.1},
                configuration={},
                status="eligible_candidate",
                artifact_locator=str(predecessor_path),
                artifact_sha256=predecessor_artifact_hash,
                artifact_schema="daibm.platt-calibration.v2",
                failure_code=None,
                deployment_status="superseded",
                deployment_scope="controlled_demo",
                activation_mode="automatic",
                activated_at=NOW,
                deactivated_at=NOW,
                previous_active_run_id=None,
                activation_reason="gate_passed",
                started_at=NOW,
                completed_at=NOW,
            )
        )
        session.get(CalibrationRunModel, active_id).previous_active_run_id = predecessor_id
        session.flush()
        session.add(
            CalibrationRunObservationModel(
                calibration_run_id=predecessor_id, outcome_id=outcome_id, correction_head_id=None
            )
        )

    with pytest.raises(OutcomeConflict, match="active calibration changed"):
        service.rollback(uuid.uuid4(), auditor, scope="controlled_demo")
    restored = service.rollback(active_id, auditor, scope="controlled_demo")

    assert restored["calibration_run_id"] == str(predecessor_id)
    assert restored["deployment_status"] == "active"
    assert restored["activation_mode"] == "manual_rollback"
    with session_factory() as session:
        assert session.get(CalibrationRunModel, active_id).deployment_status == "superseded"
        count = session.scalar(
            select(func.count())
            .select_from(LedgerEventModel)
            .where(LedgerEventModel.event_type == "CALIBRATION_ROLLED_BACK")
        )
    assert count == 1


def test_startup_reconciliation_finishes_direct_seeded_pending_run(
    session_factory, tmp_path
):
    facility_id, auditor = _seed_closed_facility(session_factory)
    service = OutcomeService(session_factory, artifact_root=tmp_path, clock=lambda: NOW)
    outcome_id = uuid.UUID(
        service.submit(facility_id, _submission(), auditor)["outcome"]["outcome_id"]
    )
    run_id, candidate = _candidate_run_with_membership(session_factory, outcome_id)
    path, artifact_sha256 = _write_candidate_artifact(
        tmp_path, run_id, candidate.dataset_sha256, outcome_id
    )
    with session_factory.begin() as session:
        run = session.get(CalibrationRunModel, run_id)
        run.status = "exploratory_candidate"
        run.artifact_locator = str(path)
        run.artifact_sha256 = artifact_sha256

    service.reconcile_deployments()

    with session_factory() as session:
        reconciled = session.get(CalibrationRunModel, run_id)
        count = session.scalar(
            select(func.count()).select_from(LedgerEventModel).where(
                LedgerEventModel.event_type == "CALIBRATION_AUTO_REJECTED"
            )
        )
    assert reconciled.deployment_status == "rejected"
    assert reconciled.activation_reason == "training_not_eligible"
    assert count == 1


def _seed_pending_v4_run(
    session_factory,
    tmp_path,
    *,
    training_config: CalibrationTrainingConfig | None = None,
):
    facility_id, auditor = _seed_closed_facility(session_factory)
    service = OutcomeService(
        session_factory,
        artifact_root=tmp_path,
        clock=lambda: NOW,
        training_config=training_config,
    )
    first_outcome_id = uuid.UUID(
        service.submit(facility_id, _submission(), auditor)["outcome"]["outcome_id"]
    )
    with session_factory() as session:
        first = session.get(ActualOutcomeModel, first_outcome_id)
        first_facility = session.get(FinancingFacilityModel, first.facility_id)
        model_version_id = first.model_version_id
        creator_id = first_facility.created_by_user_id
    with session_factory.begin() as session:
        for index in range(1, 40):
            request_id = uuid.uuid4()
            facility_id = uuid.uuid4()
            risk_assessment_id = uuid.uuid4()
            session.add(
                FinancingRequestModel(
                    request_id=request_id,
                    created_at=NOW,
                    updated_at=NOW,
                    applicant_id=f"E{index:04d}",
                    assessment_scope="controlled_demo",
                    amount=Decimal("1000.00"),
                    term_days=30,
                    features={},
                    risk_score=0.05 + (index % 10) * 0.09,
                    decision="approved",
                    status="audited",
                    version=1,
                    risk_assessment_id=risk_assessment_id,
                    risk_engine_version="tgnn-test@v3-recovery",
                    risk_input_sha256=f"{index + 20:064x}",
                    risk_assessed_at=NOW,
                )
            )
            session.add(
                FinancingFacilityModel(
                    facility_id=facility_id,
                    request_id=request_id,
                    principal=Decimal("1000.00"),
                    outstanding_amount=Decimal("0.00"),
                    currency="CNY",
                    status="closed",
                    version=1,
                    current_schedule_version=1,
                    closure_reason="repaid",
                    created_by_user_id=creator_id,
                    created_at=NOW,
                    updated_at=NOW,
                    closed_at=NOW,
                )
            )
            session.add_all(confirmed_cash_rows(facility_id, creator_id, NOW))
            session.add(
                ActualOutcomeModel(
                    outcome_id=uuid.uuid4(),
                    facility_id=facility_id,
                    request_id=request_id,
                    risk_assessment_id=risk_assessment_id,
                    model_version_id=model_version_id,
                    submitted_by_user_id=auditor.user_id,
                    idempotency_key=uuid.uuid4(),
                    request_sha256=f"{index + 40:064x}",
                    defaulted=index % 10 >= 5,
                    days_past_due=30 if index % 10 >= 5 else 0,
                    loss_amount=Decimal("100.00") if index % 10 >= 5 else Decimal("0.00"),
                    observed_at=NOW + timedelta(minutes=index),
                    evidence_sha256=f"{index + 60:064x}",
                    provenance="CONTROLLED_DEMO",
                    original_risk_score=0.05 + (index % 10) * 0.09,
                    risk_engine_version="tgnn-test@v3-recovery",
                    risk_input_sha256=f"{index + 20:064x}",
                    recorded_at=NOW,
                )
            )
    with session_factory() as session:
        outcomes = list(
            session.scalars(select(ActualOutcomeModel).order_by(ActualOutcomeModel.outcome_id))
        )
        observations = tuple(
            CalibrationObservation(
                outcome_id=str(row.outcome_id),
                facility_id=str(row.facility_id),
                request_id=str(row.request_id),
                risk_assessment_id=str(row.risk_assessment_id),
                model_version_id=(str(row.model_version_id) if row.model_version_id else None),
                risk_engine_version=row.risk_engine_version,
                risk_input_sha256=row.risk_input_sha256,
                evidence_sha256=row.evidence_sha256,
                original_score=row.original_risk_score,
                defaulted=row.defaulted,
                observed_at=row.observed_at.isoformat(timespec="microseconds"),
                provenance=row.provenance,
                correction_head_id=None,
            )
            for row in outcomes
        )
    candidate = build_calibration_candidate(
        observations,
        config=service.training_config,
    )
    published = write_candidate_artifact(tmp_path, candidate)
    run_id = uuid.uuid4()
    with session_factory.begin() as session:
        session.add(
            CalibrationRunModel(
                calibration_run_id=run_id,
                trigger_outcome_id=None,
                trigger_job_id=None,
                dataset_sha256=candidate.dataset_sha256,
                sample_count=candidate.sample_count,
                positive_count=candidate.positive_count,
                negative_count=candidate.negative_count,
                metrics_before=dict(candidate.metrics_before),
                metrics_after=dict(candidate.metrics_after),
                configuration=service._configuration(),
                status=candidate.status,
                artifact_locator=str(published.path),
                artifact_sha256=published.sha256,
                artifact_schema=candidate.artifact_schema,
                failure_code=None,
                deployment_status="not_deployed",
                deployment_scope="controlled_demo",
                activation_mode=None,
                activated_at=None,
                deactivated_at=None,
                previous_active_run_id=None,
                activation_reason="not_evaluated",
                started_at=NOW,
                completed_at=NOW,
            )
        )
        session.flush()
        session.add_all(
            [
                CalibrationRunObservationModel(
                    calibration_run_id=run_id,
                    outcome_id=row.outcome_id,
                    correction_head_id=None,
                )
                for row in outcomes
            ]
        )
    return service, run_id


def test_startup_reconciliation_activates_an_eligible_verified_v4_run(
    session_factory,
    tmp_path,
):
    service, run_id = _seed_pending_v4_run(session_factory, tmp_path)

    service.reconcile_deployments()

    with session_factory() as session:
        reconciled = session.get(CalibrationRunModel, run_id)
    assert reconciled.deployment_status == "active"
    assert reconciled.activation_reason == "gate_passed"


def test_high_precision_config_build_load_gate_persist_recover_round_trip(
    session_factory,
    tmp_path,
):
    config = CalibrationTrainingConfig(
        learning_rate=math.nextafter(0.05, 1.0),
        l2_penalty=math.nextafter(0.001, 1.0),
        probability_epsilon=math.nextafter(1e-6, 1.0),
    )
    service, run_id = _seed_pending_v4_run(
        session_factory,
        tmp_path,
        training_config=config,
    )

    service.reconcile_deployments()

    with session_factory() as session:
        reconciled = session.get(CalibrationRunModel, run_id)
        artifact = json.loads(Path(reconciled.artifact_locator).read_text(encoding="utf-8"))
    assert reconciled.configuration == artifact["configuration"]
    assert (
        reconciled.deployment_status,
        reconciled.activation_reason,
    ) == ("active", "gate_passed")


def test_startup_reconciliation_rejects_v4_db_metrics_that_contradict_artifact(
    session_factory,
    tmp_path,
):
    service, run_id = _seed_pending_v4_run(session_factory, tmp_path)
    with session_factory.begin() as session:
        session.get(CalibrationRunModel, run_id).metrics_after = {
            "brier_score": 0.0,
            "log_loss": 0.0,
        }

    service.reconcile_deployments()

    with session_factory() as session:
        reconciled = session.get(CalibrationRunModel, run_id)
    assert reconciled.deployment_status == "rejected"
    assert reconciled.activation_reason == "artifact_unverified"


def test_concurrent_unrecoverable_artifact_reads_are_side_effect_free(
    session_factory, tmp_path, monkeypatch
):
    facility_id, auditor = _seed_closed_facility(session_factory)
    service = OutcomeService(session_factory, artifact_root=tmp_path, clock=lambda: NOW)
    outcome_id = uuid.UUID(
        service.submit(facility_id, _submission(), auditor)["outcome"]["outcome_id"]
    )
    run_id, _candidate = _candidate_run_with_membership(session_factory, outcome_id)
    with session_factory.begin() as session:
        session.get(CalibrationRunModel, run_id).trigger_outcome_id = outcome_id
    barrier = Barrier(2)

    def missing(*_args, **_kwargs):
        barrier.wait(timeout=10)
        return "missing"

    monkeypatch.setattr("app.services.outcomes.recover_candidate_artifact", missing)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(lambda _index: service.get_run(run_id, auditor), range(2))
        )

    assert {item["status"] for item in results} == {"eligible_candidate"}
    assert {item["artifact_integrity"] for item in results} == {"missing"}
    with session_factory() as session:
        stored = session.get(CalibrationRunModel, run_id)
        count = session.scalar(
            select(func.count()).select_from(LedgerEventModel).where(
                LedgerEventModel.event_type == "CALIBRATION_CANDIDATE_FAILED"
            )
        )
    assert stored.status == "eligible_candidate"
    assert stored.failure_code is None
    assert count == 0


def test_seeded_artifact_lineage_mismatch_rejects_deployment(
    session_factory, tmp_path
):
    facility_id, auditor = _seed_closed_facility(session_factory)
    service = OutcomeService(session_factory, artifact_root=tmp_path, clock=lambda: NOW)
    outcome_id = uuid.UUID(
        service.submit(facility_id, _submission(), auditor)["outcome"]["outcome_id"]
    )
    run_id, candidate = _candidate_run_with_membership(session_factory, outcome_id)
    path, artifact_sha256 = _write_candidate_artifact(
        tmp_path, run_id, "f" * 64, outcome_id
    )
    with session_factory.begin() as session:
        run = session.get(CalibrationRunModel, run_id)
        run.artifact_locator = str(path)
        run.artifact_sha256 = artifact_sha256

    service._evaluate_deployment(
        run_id, candidate, provenances=("CONTROLLED_DEMO",)
    )

    with session_factory() as session:
        rejected = session.get(CalibrationRunModel, run_id)
    assert rejected.deployment_status == "rejected"
    assert rejected.activation_reason == "artifact_unverified"
