from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
from threading import Barrier

import pytest
from sqlalchemy import func, select

from app.models import FinancingRequestModel, LedgerEventModel
from app.models_facility import FinancingFacilityModel
from app.models_outcome import ActualOutcomeModel, CalibrationRunModel
from app.models_research import (
    DatasetVersionModel,
    GraphSnapshotModel,
    ModelRunModel,
    ModelVersionModel,
    RiskAssessmentModel,
)
from app.schemas_outcome import ActualOutcomeCreate
from app.schemas_workflow import ApplicationDraftCreate
from app.services.identity import IdentityService
from app.services.outcomes import (
    ForbiddenOutcome,
    OutcomeConflict,
    OutcomeService,
)
from app.services.workflow import WorkflowService


NOW = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)


def _seed_closed_facility(session_factory) -> tuple[uuid.UUID, object]:
    identity = IdentityService(session_factory, clock=lambda: NOW)
    identity.seed_demo_accounts()
    auditor = identity.login("auditor.demo", "Demo123!").user
    financier = identity.login("financier.demo", "Demo123!").user
    dataset_id = uuid.uuid4()
    snapshot_id = uuid.uuid4()
    model_run_id = uuid.uuid4()
    model_version_id = uuid.uuid4()
    model_semantic_version = str(uuid.uuid4())
    assessment_id = uuid.uuid4()
    request_id = uuid.uuid4()
    facility_id = uuid.uuid4()
    with session_factory.begin() as session:
        session.add(
            DatasetVersionModel(
                dataset_version_id=dataset_id,
                name="outcome-test",
                version=str(uuid.uuid4()),
                generation_seed=1,
                schema_version="v1",
                manifest={},
                content_sha256=uuid.uuid4().hex * 2,
                created_at=NOW,
            )
        )
        session.add(
            GraphSnapshotModel(
                graph_snapshot_id=snapshot_id,
                dataset_version_id=dataset_id,
                synthetic_scenario_id=None,
                scenario_revision=None,
                overlay_sha256=None,
                anchor_month=12,
                window_start_month=1,
                window_end_month=12,
                feature_schema_version="v1",
                normalization_id="norm-v1",
                node_ordering_sha256="1" * 64,
                adjacency_sha256="2" * 64,
                feature_sha256="3" * 64,
                content_sha256=uuid.uuid4().hex * 2,
                storage_locator="memory://snapshot",
                created_at=NOW,
            )
        )
        session.add(
            ModelRunModel(
                model_run_id=model_run_id,
                model_family="tgnn",
                run_seed=1,
                dataset_version_id=dataset_id,
                configuration={},
                status="completed",
                started_at=NOW,
                ended_at=NOW,
                metrics={},
            )
        )
        session.add(
            ModelVersionModel(
                model_version_id=model_version_id,
                model_name="tgnn-test",
                semantic_version=model_semantic_version,
                model_family="tgnn",
                source_run_id=model_run_id,
                dataset_version_id=dataset_id,
                feature_schema_version="v1",
                inference_format="onnx",
                artifact_locator="memory://model",
                checkpoint_sha256="4" * 64,
                metrics={},
                lifecycle_status="candidate",
                deployment_slot=None,
                created_at=NOW,
            )
        )
        session.add(
            RiskAssessmentModel(
                risk_assessment_id=assessment_id,
                enterprise_id="E0001",
                graph_snapshot_id=snapshot_id,
                model_version_id=model_version_id,
                input_sha256="5" * 64,
                risk_score=0.70,
                band="HIGH",
                explanations=[],
                inferred_at=NOW,
            )
        )
        session.add(
            FinancingRequestModel(
                request_id=request_id,
                created_at=NOW,
                updated_at=NOW,
                applicant_id="E0001",
                amount=Decimal("1000.00"),
                term_days=30,
                features={},
                risk_score=0.70,
                decision="approved",
                status="audited",
                version=1,
                risk_assessment_id=assessment_id,
                risk_engine_version=f"tgnn-test@{model_semantic_version}",
                risk_input_sha256="5" * 64,
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
                version=7,
                created_by_user_id=financier.user_id,
                created_at=NOW,
                updated_at=NOW,
                closed_at=NOW,
            )
        )
    return facility_id, auditor


def _payload(**changes) -> ActualOutcomeCreate:
    values = {
        "idempotency_key": uuid.uuid4(),
        "defaulted": False,
        "days_past_due": 0,
        "loss_amount": "0.00",
        "observed_at": "2026-08-24T12:00:00Z",
        "evidence_sha256": "a" * 64,
        "provenance": "CONTROLLED_DEMO",
    }
    values.update(changes)
    return ActualOutcomeCreate.model_validate(values)


def test_closed_facility_outcome_creates_exploratory_candidate_with_full_lineage(
    session_factory,
    tmp_path,
):
    facility_id, auditor = _seed_closed_facility(session_factory)
    service = OutcomeService(session_factory, artifact_root=tmp_path, clock=lambda: NOW)

    result = service.submit(facility_id, _payload(), auditor)

    outcome = result["outcome"]
    run = result["calibration_run"]
    assert outcome["facility_id"] == str(facility_id)
    assert outcome["request_id"]
    assert outcome["risk_assessment_id"]
    assert outcome["model_version_id"]
    assert outcome["original_risk_score"] == pytest.approx(0.70)
    assert outcome["risk_input_sha256"] == "5" * 64
    assert run["status"] == "exploratory_candidate"
    assert run["sample_count"] == 1
    assert run["positive_count"] == 0
    assert run["artifact_integrity"] == "verified"
    assert run["artifact_sha256"]
    assert "artifact_locator" not in run
    assert run["deployment_status"] == "rejected"
    assert run["deployment_scope"] == "controlled_demo"
    assert run["activation_reason"] == "training_not_eligible"
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ActualOutcomeModel)) == 1
        assert session.scalar(select(func.count()).select_from(CalibrationRunModel)) == 1
        event_types = set(session.scalars(select(LedgerEventModel.event_type)))
    assert {"ACTUAL_OUTCOME_RECORDED", "CALIBRATION_CANDIDATE_TRAINED"}.issubset(
        event_types
    )
    assert "CALIBRATION_AUTO_REJECTED" in event_types


def test_twentieth_supported_outcome_automatically_activates_verified_calibration(
    session_factory,
    tmp_path,
):
    service = OutcomeService(session_factory, artifact_root=tmp_path, clock=lambda: NOW)
    result = None
    auditor = None
    for index in range(20):
        facility_id, auditor = _seed_closed_facility(session_factory)
        result = service.submit(
            facility_id,
            _payload(
                defaulted=index < 5,
                days_past_due=90 if index < 5 else 0,
                loss_amount="100.00" if index < 5 else "0.00",
                evidence_sha256=f"{index + 1:064x}",
            ),
            auditor,
        )

    assert result is not None and auditor is not None
    run = result["calibration_run"]
    assert run["sample_count"] == 20
    assert run["positive_count"] == 5
    assert run["negative_count"] == 15
    assert run["deployment_status"] == "active"
    assert run["deployment_scope"] == "controlled_demo"
    assert run["activation_mode"] == "automatic"
    assert run["activation_reason"] == "gate_passed"
    assert run["activated_at"] == "2026-08-24T12:00:00.000000+00:00"
    assert run["previous_active_run_id"] is None
    assert service.get_active_deployment(auditor) == run

    with session_factory() as session:
        active_count = session.scalar(
            select(func.count())
            .select_from(CalibrationRunModel)
            .where(CalibrationRunModel.deployment_status == "active")
        )
        event_types = list(session.scalars(select(LedgerEventModel.event_type)))
    assert active_count == 1
    assert event_types.count("CALIBRATION_AUTO_ACTIVATED") == 1

    identity = IdentityService(session_factory)
    supplier = identity.login("supplier.demo", "Demo123!").user
    core = identity.login("core.demo", "Demo123!").user
    financier = identity.login("financier.demo", "Demo123!").user
    workflow = WorkflowService(session_factory)
    draft = workflow.create_draft(
        ApplicationDraftCreate(
            core_enterprise_organization_code="CORE-001",
            contract_number="SCF-AFTER-AUTO-CALIBRATION",
            invoice_number="INV-AFTER-AUTO-CALIBRATION",
            amount=850_000,
            term_days=90,
            payment_delay_days=20,
            counterparty_risk=0.55,
            invoice_mismatch=True,
            relationship_months=18,
            transactions_last_30d=12,
        ),
        supplier,
    )
    submitted = workflow.submit(draft["request_id"], draft["version"], supplier)
    confirmed = workflow.confirm_trade(
        draft["request_id"],
        submitted["version"],
        confirmed=True,
        comment="Verified after calibration activation",
        user=core,
    )
    assessed = workflow.assess_risk(
        draft["request_id"],
        confirmed["version"],
        financier,
    )

    assert assessed["raw_risk_score"] != assessed["risk_score"]
    assert (
        assessed["risk_evidence"]["calibration_run_id"]
        == run["calibration_run_id"]
    )
    assert (
        assessed["risk_evidence"]["calibration_deployment_scope"]
        == "controlled_demo"
    )
    with session_factory() as session:
        applied_events = list(
            session.scalars(
                select(LedgerEventModel.event_type).where(
                    LedgerEventModel.event_type == "RISK_CALIBRATION_APPLIED"
                )
            )
        )
    assert applied_events == ["RISK_CALIBRATION_APPLIED"]


def test_rollback_requires_expected_active_version_and_restores_only_its_predecessor(
    session_factory,
    tmp_path,
):
    service = OutcomeService(session_factory, artifact_root=tmp_path, clock=lambda: NOW)
    auditor = None
    active = None
    for index in range(21):
        facility_id, auditor = _seed_closed_facility(session_factory)
        result = service.submit(
            facility_id,
            _payload(
                defaulted=index < 5,
                days_past_due=90 if index < 5 else 0,
                loss_amount="100.00" if index < 5 else "0.00",
                evidence_sha256=f"{index + 100:064x}",
            ),
            auditor,
        )
        if result["calibration_run"]["deployment_status"] == "active":
            active = result["calibration_run"]

    assert auditor is not None and active is not None
    assert active["previous_active_run_id"] is not None
    with pytest.raises(OutcomeConflict, match="active calibration changed"):
        service.rollback(uuid.uuid4(), auditor)

    restored = service.rollback(active["calibration_run_id"], auditor)

    assert restored["calibration_run_id"] == active["previous_active_run_id"]
    assert restored["deployment_status"] == "active"
    assert restored["activation_mode"] == "manual_rollback"
    with session_factory() as session:
        current = session.get(
            CalibrationRunModel,
            uuid.UUID(active["calibration_run_id"]),
        )
        event_types = list(session.scalars(select(LedgerEventModel.event_type)))
    assert current is not None and current.deployment_status == "superseded"
    assert event_types.count("CALIBRATION_ROLLED_BACK") == 1


def test_identical_retry_is_idempotent_but_conflicting_reuse_is_rejected(
    session_factory,
    tmp_path,
):
    facility_id, auditor = _seed_closed_facility(session_factory)
    service = OutcomeService(session_factory, artifact_root=tmp_path, clock=lambda: NOW)
    payload = _payload()

    first = service.submit(facility_id, payload, auditor)
    second = service.submit(facility_id, payload, auditor)

    assert first == second
    with pytest.raises(OutcomeConflict, match="different outcome semantics"):
        service.submit(
            facility_id,
            _payload(
                idempotency_key=payload.idempotency_key,
                days_past_due=1,
            ),
            auditor,
        )


def test_startup_reconciliation_finishes_a_committed_pending_deployment(
    session_factory,
    tmp_path,
):
    facility_id, auditor = _seed_closed_facility(session_factory)
    service = OutcomeService(session_factory, artifact_root=tmp_path, clock=lambda: NOW)
    created = service.submit(facility_id, _payload(), auditor)
    run_id = uuid.UUID(created["calibration_run"]["calibration_run_id"])
    with session_factory.begin() as session:
        run = session.get(CalibrationRunModel, run_id)
        assert run is not None
        run.deployment_status = "not_deployed"
        run.activation_reason = "not_evaluated"

    service.reconcile_deployments()

    with session_factory() as session:
        reconciled = session.get(CalibrationRunModel, run_id)
        rejection_count = session.scalar(
            select(func.count())
            .select_from(LedgerEventModel)
            .where(LedgerEventModel.event_type == "CALIBRATION_AUTO_REJECTED")
        )
    assert reconciled is not None
    assert reconciled.deployment_status == "rejected"
    assert reconciled.activation_reason == "training_not_eligible"
    assert rejection_count == 2


def test_idempotency_normalizes_equivalent_money_and_timestamp_representations(
    session_factory,
    tmp_path,
):
    facility_id, auditor = _seed_closed_facility(session_factory)
    service = OutcomeService(session_factory, artifact_root=tmp_path, clock=lambda: NOW)
    key = uuid.uuid4()

    first = service.submit(
        facility_id,
        _payload(
            idempotency_key=key,
            loss_amount="1",
            observed_at="2026-08-24T12:00:00Z",
        ),
        auditor,
    )
    replay = service.submit(
        facility_id,
        _payload(
            idempotency_key=key,
            loss_amount="1.00",
            observed_at="2026-08-24T14:30:00+02:30",
        ),
        auditor,
    )

    assert replay == first


def test_submission_requires_auditor_closed_zero_balance_lineage_and_loss_cap(
    session_factory,
    tmp_path,
):
    facility_id, auditor = _seed_closed_facility(session_factory)
    service = OutcomeService(session_factory, artifact_root=tmp_path, clock=lambda: NOW)
    non_auditor = IdentityService(session_factory).login(
        "financier.demo", "Demo123!"
    ).user

    with pytest.raises(ForbiddenOutcome):
        service.submit(facility_id, _payload(), non_auditor)
    with pytest.raises(OutcomeConflict, match="loss amount cannot exceed principal"):
        service.submit(facility_id, _payload(loss_amount="1000.01"), auditor)

    with session_factory.begin() as session:
        facility = session.get(FinancingFacilityModel, facility_id)
        facility.status = "repaid"
    with pytest.raises(OutcomeConflict, match="closed facility"):
        service.submit(facility_id, _payload(), auditor)


@pytest.mark.parametrize(
    "missing_field",
    ("risk_input_sha256", "risk_engine_version", "risk_assessed_at"),
)
def test_submission_rejects_incomplete_request_side_prediction_lineage(
    session_factory,
    tmp_path,
    missing_field,
):
    facility_id, auditor = _seed_closed_facility(session_factory)
    with session_factory.begin() as session:
        facility = session.get(FinancingFacilityModel, facility_id)
        application = session.get(FinancingRequestModel, facility.request_id)
        setattr(application, missing_field, None)
    service = OutcomeService(session_factory, artifact_root=tmp_path, clock=lambda: NOW)

    with pytest.raises(OutcomeConflict, match="lineage"):
        service.submit(facility_id, _payload(), auditor)


def test_submission_rejects_request_engine_version_that_disagrees_with_model(
    session_factory,
    tmp_path,
):
    facility_id, auditor = _seed_closed_facility(session_factory)
    with session_factory.begin() as session:
        facility = session.get(FinancingFacilityModel, facility_id)
        application = session.get(FinancingRequestModel, facility.request_id)
        application.risk_engine_version = "unrelated-engine-v999"
    service = OutcomeService(session_factory, artifact_root=tmp_path, clock=lambda: NOW)

    with pytest.raises(OutcomeConflict, match="engine lineage"):
        service.submit(facility_id, _payload(), auditor)


@pytest.mark.parametrize("failure", (OSError("disk"), RuntimeError("collision")))
def test_artifact_failure_is_persisted_and_audited_without_losing_outcome(
    session_factory,
    tmp_path,
    failure,
):
    facility_id, auditor = _seed_closed_facility(session_factory)

    def fail_writer(*_args, **_kwargs):
        raise failure

    service = OutcomeService(
        session_factory,
        artifact_root=tmp_path,
        artifact_writer=fail_writer,
        clock=lambda: NOW,
    )

    result = service.submit(facility_id, _payload(), auditor)

    assert result["calibration_run"] == {
        **result["calibration_run"],
        "status": "failed",
        "failure_code": "artifact_write_failed",
        "artifact_sha256": None,
        "artifact_integrity": "not_applicable",
        "deployment_status": "not_deployed",
        "activation_reason": "artifact_write_failed",
    }
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ActualOutcomeModel)) == 1
        failed = session.scalar(select(CalibrationRunModel))
        assert failed is not None and failed.status == "failed"
        event_types = set(session.scalars(select(LedgerEventModel.event_type)))
    assert "CALIBRATION_CANDIDATE_FAILED" in event_types


def test_training_failure_is_persisted_without_retrying_or_losing_outcome(
    session_factory,
    tmp_path,
    monkeypatch,
):
    facility_id, auditor = _seed_closed_facility(session_factory)
    calls = 0

    def fail_trainer(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise RuntimeError("training internals must not leak")

    monkeypatch.setattr(
        "app.services.outcomes.build_calibration_candidate",
        fail_trainer,
    )

    service = OutcomeService(
        session_factory,
        artifact_root=tmp_path,
        trainer=fail_trainer,
        clock=lambda: NOW,
    )

    result = service.submit(facility_id, _payload(), auditor)

    assert calls == 1
    assert result["calibration_run"]["status"] == "failed"
    assert result["calibration_run"]["failure_code"] == "candidate_training_failed"
    assert result["calibration_run"]["sample_count"] == 1
    assert result["calibration_run"]["metrics_before"] is not None
    assert result["calibration_run"]["metrics_after"] is None


def test_dataset_summary_type_error_is_persisted_as_failed_run(
    session_factory,
    tmp_path,
    monkeypatch,
):
    facility_id, auditor = _seed_closed_facility(session_factory)

    def fail_summary(*_args, **_kwargs):
        raise TypeError("unexpected numpy adapter failure")

    monkeypatch.setattr(
        "app.services.outcomes.summarize_calibration_observations",
        fail_summary,
    )
    service = OutcomeService(session_factory, artifact_root=tmp_path, clock=lambda: NOW)

    result = service.submit(facility_id, _payload(), auditor)

    assert result["calibration_run"]["status"] == "failed"
    assert result["calibration_run"]["failure_code"] == "candidate_training_failed"
    assert result["calibration_run"]["sample_count"] == 1
    assert result["calibration_run"]["metrics_before"] is None
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ActualOutcomeModel)) == 1
        assert session.scalar(select(func.count()).select_from(CalibrationRunModel)) == 1


def test_late_transaction_failure_removes_staged_and_published_artifacts(
    session_factory,
    tmp_path,
):
    facility_id, auditor = _seed_closed_facility(session_factory)

    class FailingLedger:
        def append_many(self, *_args, **_kwargs):
            raise RuntimeError("ledger unavailable")

    service = OutcomeService(
        session_factory,
        artifact_root=tmp_path,
        ledger_repository=FailingLedger(),
        clock=lambda: NOW,
    )

    with pytest.raises(RuntimeError, match="ledger unavailable"):
        service.submit(facility_id, _payload(), auditor)

    assert list(tmp_path.glob("*")) == []
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ActualOutcomeModel)) == 0
        assert session.scalar(select(func.count()).select_from(CalibrationRunModel)) == 0


def test_post_commit_artifact_publication_failure_is_marked_and_audited(
    session_factory,
    tmp_path,
    monkeypatch,
):
    facility_id, auditor = _seed_closed_facility(session_factory)

    def fail_publish(*_args, **_kwargs):
        raise OSError("rename unavailable")

    monkeypatch.setattr(
        "app.services.outcomes.publish_candidate_artifact",
        fail_publish,
    )
    service = OutcomeService(session_factory, artifact_root=tmp_path, clock=lambda: NOW)

    result = service.submit(facility_id, _payload(), auditor)

    assert result["calibration_run"]["status"] == "failed"
    assert result["calibration_run"]["failure_code"] == "artifact_write_failed"
    assert result["calibration_run"]["artifact_integrity"] == "not_applicable"
    assert list(tmp_path.glob("*")) == []
    with session_factory() as session:
        event_types = list(session.scalars(select(LedgerEventModel.event_type)))
    assert event_types.count("CALIBRATION_CANDIDATE_FAILED") == 1


def test_failed_publication_keeps_recoverable_stage_when_failure_audit_cannot_commit(
    session_factory,
    tmp_path,
    monkeypatch,
):
    facility_id, auditor = _seed_closed_facility(session_factory)

    monkeypatch.setattr(
        "app.services.outcomes.publish_candidate_artifact",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("rename unavailable")),
    )
    service = OutcomeService(session_factory, artifact_root=tmp_path, clock=lambda: NOW)
    monkeypatch.setattr(
        service,
        "_mark_publication_failed",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("audit unavailable")),
    )

    with pytest.raises(RuntimeError, match="audit unavailable"):
        service.submit(facility_id, _payload(), auditor)

    with session_factory() as session:
        run = session.scalar(select(CalibrationRunModel))
    assert run is not None and run.status == "exploratory_candidate"
    assert list(tmp_path.glob(".pending-*.json"))


def test_unrecoverable_lazy_publication_is_durably_failed_and_audited(
    session_factory,
    tmp_path,
    monkeypatch,
):
    facility_id, auditor = _seed_closed_facility(session_factory)
    service = OutcomeService(session_factory, artifact_root=tmp_path, clock=lambda: NOW)
    created = service.submit(facility_id, _payload(), auditor)
    run_id = created["calibration_run"]["calibration_run_id"]
    monkeypatch.setattr(
        "app.services.outcomes.recover_candidate_artifact",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError("read denied")),
    )

    recovered = service.get_run(run_id, auditor)

    assert recovered["status"] == "failed"
    assert recovered["failure_code"] == "artifact_write_failed"
    assert recovered["artifact_integrity"] == "not_applicable"
    with session_factory() as session:
        event_types = list(session.scalars(select(LedgerEventModel.event_type)))
    assert event_types.count("CALIBRATION_CANDIDATE_FAILED") == 1


def test_concurrent_lazy_failures_append_one_durable_failure_event(
    session_factory,
    tmp_path,
    monkeypatch,
):
    facility_id, auditor = _seed_closed_facility(session_factory)
    service = OutcomeService(session_factory, artifact_root=tmp_path, clock=lambda: NOW)
    created = service.submit(facility_id, _payload(), auditor)
    run_id = created["calibration_run"]["calibration_run_id"]
    barrier = Barrier(2)

    def fail_recovery(*_args, **_kwargs):
        barrier.wait(timeout=10)
        return "missing"

    monkeypatch.setattr(
        "app.services.outcomes.recover_candidate_artifact",
        fail_recovery,
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(lambda _index: service.get_run(run_id, auditor), range(2))
        )

    assert {item["status"] for item in results} == {"failed"}
    with session_factory() as session:
        event_types = list(session.scalars(select(LedgerEventModel.event_type)))
    assert event_types.count("CALIBRATION_CANDIDATE_FAILED") == 1
