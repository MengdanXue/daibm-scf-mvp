from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

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
from app.services.identity import IdentityService
from app.services.outcomes import (
    ForbiddenOutcome,
    OutcomeConflict,
    OutcomeService,
)


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
                semantic_version=str(uuid.uuid4()),
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
                risk_engine_version="tgnn-test",
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
    assert run["promotion_status"] == "not_promoted"
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ActualOutcomeModel)) == 1
        assert session.scalar(select(func.count()).select_from(CalibrationRunModel)) == 1
        event_types = set(session.scalars(select(LedgerEventModel.event_type)))
    assert {"ACTUAL_OUTCOME_RECORDED", "CALIBRATION_CANDIDATE_TRAINED"}.issubset(
        event_types
    )


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
        "promotion_status": "not_promoted",
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
