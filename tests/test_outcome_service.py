from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select

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
from app.models_research import (
    DatasetVersionModel,
    GraphSnapshotModel,
    ModelRunModel,
    ModelVersionModel,
    RiskAssessmentModel,
)
from app.repositories.outcomes import OutcomeRepository
from app.schemas_outcome import ActualOutcomeCreate, OutcomeCorrectionCreate
from app.services.identity import IdentityService
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
            currency="CNY", status="closed", version=7,
            current_schedule_version=1,
            closure_reason=closure_reason,
            created_by_user_id=financier.user_id, created_at=NOW, updated_at=NOW,
            closed_at=NOW,
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
    return run_id


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
    with session_factory.begin() as session:
        facility = session.get(FinancingFacilityModel, facility_id)
        facility.status = "active"
    with pytest.raises(OutcomeConflict, match="closed facility"):
        service.submit(facility_id, _submission(), auditor)


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
