"""Phase 2.1 model registry: versions, lifecycle, activation, rollback, audit."""

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
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.domain.model_registry import ALLOWED_TRANSITIONS, ModelVersionStatus, model_id_for
from app.models import FinancingRequestModel, LedgerEventModel
from app.models_model_governance import (
    RiskDecisionRecordModel,
    RiskModelVersionModel,
    RiskModelVersionTransitionModel,
)
from app.models_outcome import CalibrationRunModel
from app.repositories.model_registry import ModelRegistryRepository
from app.services.adaptive_risk import AdaptiveRiskInferenceService
from app.services.calibration_jobs import CalibrationJobService
from app.services.model_registry import (
    ModelRegistryService,
    RegistryConflict,
    RegistryForbidden,
)
from app.services.outcomes import AWAITING_PROMOTION, OutcomeService
from test_model_governance import START, _assess, _seed_outcomes, _users
from test_workflow_service import draft_payload

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "alembic" / "versions" / "20260926_0017_model_version_registry.py"


def _registry_stack(session_factory, tmp_path, *, auto_promotion: bool):
    users = _users(session_factory)
    clock = [START + timedelta(days=30)]
    outcomes = OutcomeService(
        session_factory,
        artifact_root=tmp_path,
        clock=lambda: clock[0],
        auto_promotion=auto_promotion,
    )
    worker = CalibrationJobService(session_factory, outcome_service=outcomes, clock=lambda: clock[0])

    def drain():
        while worker.process_next("registry-test-worker"):
            pass

    registry = ModelRegistryService(
        session_factory, outcome_service=outcomes, clock=lambda: clock[0]
    )
    return users, outcomes, drain, registry, clock


@pytest.fixture
def auto_stack(session_factory, tmp_path):
    return _registry_stack(session_factory, tmp_path, auto_promotion=True)


@pytest.fixture
def manual_stack(session_factory, tmp_path):
    return _registry_stack(session_factory, tmp_path, auto_promotion=False)


def _versions(session_factory, scope="controlled_demo") -> list[RiskModelVersionModel]:
    with session_factory() as session:
        return ModelRegistryRepository().list_versions(session, scope=scope)[::-1]


def _transitions(session_factory, version_id) -> list[RiskModelVersionTransitionModel]:
    with session_factory() as session:
        return ModelRegistryRepository().list_transitions(session, version_ids=[version_id])


def _path(transitions) -> list[tuple[str | None, str]]:
    return [(item.from_status, item.to_status) for item in transitions]


def _two_active_versions(session_factory, stack):
    users, _, drain, _, clock = stack
    _seed_outcomes(session_factory, users, count=40, start=START)
    drain()
    clock[0] = START + timedelta(days=60)
    _seed_outcomes(session_factory, users, count=10, start=START + timedelta(days=40))
    drain()
    first, second = _versions(session_factory)
    return first, second


def _inference(session_factory, scope="controlled_demo"):
    with session_factory() as session:
        return AdaptiveRiskInferenceService().assess(session, 0.4, scope)


# --- Domain / migration contract ------------------------------------------------


def test_domain_transitions_equal_the_frozen_database_list():
    spec = importlib.util.spec_from_file_location("model_version_registry_0017", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert {(S(a), S(b)) for a, b in module._ALLOWED} == set(ALLOWED_TRANSITIONS)
    assert set(module._STATUSES) == {status.value for status in ModelVersionStatus}


S = ModelVersionStatus


# --- 1. Registration ---------------------------------------------------------------


def test_trained_artifact_is_registered_as_a_full_model_version(session_factory, auto_stack):
    users, _, drain, _, _ = auto_stack
    _seed_outcomes(session_factory, users, count=40, start=START)
    drain()
    (version,) = _versions(session_factory)
    with session_factory() as session:
        run = session.get(CalibrationRunModel, version.calibration_run_id)
    assert version.model_id == model_id_for("controlled_demo") == "calibration:controlled_demo"
    assert version.version == 1
    assert version.model_type == "platt_calibration"
    assert version.artifact_path == run.artifact_locator
    assert version.artifact_hash == run.artifact_sha256
    assert version.scope == "controlled_demo"
    assert version.training_dataset_version == run.dataset_sha256
    assert version.metrics["sample_count"] == 40
    assert version.metrics["holdout_after"] == run.metrics_after
    assert version.created_by == "system:calibration-worker"
    assert version.created_at is not None
    assert version.status == "ACTIVE"
    assert version.evaluation_passed is True
    assert version.evaluation["independent"] is True
    assert version.evaluation["artifact_integrity"] == "verified"


def test_manual_registration_evaluates_an_unregistered_artifact(session_factory, manual_stack):
    users, _, _, registry, _ = manual_stack
    run_id = uuid.uuid4()
    with session_factory.begin() as session:
        session.add(CalibrationRunModel(
            calibration_run_id=run_id, trigger_outcome_id=None, trigger_job_id=None,
            dataset_sha256="d" * 64, sample_count=20, positive_count=10, negative_count=10,
            metrics_before={"brier": 0.2}, metrics_after={"brier": 0.1}, configuration={},
            status="eligible_candidate", artifact_locator="/nonexistent/artifact.json",
            artifact_sha256="e" * 64, artifact_schema="daibm.platt-calibration.v3",
            failure_code=None, deployment_status="not_deployed",
            deployment_scope="controlled_demo", activation_mode=None,
            activation_reason="not_evaluated", started_at=START, completed_at=START,
        ))
    listing = registry.list_versions(users["auditor"])
    assert listing["unregistered_artifacts"] == [str(run_id)]
    with pytest.raises(RegistryForbidden):
        registry.register_version(run_id, users["risk"])

    registered = registry.register_version(run_id, users["auditor"])
    assert registered["created_by"] == "auditor.demo"
    assert registered["status"] == "REJECTED"
    assert registered["evaluation_passed"] is False
    assert [(t["from_status"], t["to_status"]) for t in registered["transitions"]] == [
        (None, "DRAFT"), ("DRAFT", "EVALUATING"), ("EVALUATING", "REJECTED"),
    ]
    assert registered["transitions"][0]["reason"] == "manual_registration"
    assert registered["artifact_check"]["consistent"] is False
    with pytest.raises(RegistryConflict, match="already registered"):
        registry.register_version(run_id, users["auditor"])


# --- 2. State transitions ------------------------------------------------------------


def test_lifecycle_walks_draft_evaluating_candidate_active(session_factory, auto_stack):
    users, _, drain, _, _ = auto_stack
    _seed_outcomes(session_factory, users, count=40, start=START)
    drain()
    (version,) = _versions(session_factory)
    transitions = _transitions(session_factory, version.id)
    assert _path(transitions) == [
        (None, "DRAFT"),
        ("DRAFT", "EVALUATING"),
        ("EVALUATING", "CANDIDATE"),
        ("CANDIDATE", "ACTIVE"),
    ]
    assert [item.status_sequence for item in transitions] == [1, 2, 3, 4]


def test_database_rejects_illegal_and_unaudited_transitions(session_factory, manual_stack):
    users, _, drain, _, _ = manual_stack
    _seed_outcomes(session_factory, users, count=40, start=START)
    drain()
    (version,) = _versions(session_factory)
    assert version.status == "CANDIDATE"
    # Illegal pair: CANDIDATE -> ROLLED_BACK is not in the state machine.
    with pytest.raises(DBAPIError, match="illegal model version transition"):
        with session_factory.begin() as session:
            session.execute(
                text("SELECT transition_model_version(:id, 'ROLLED_BACK', 'x', NULL)"),
                {"id": version.id},
            )
    # A raw status write without an audited transition row is refused at commit.
    with pytest.raises(DBAPIError, match="no audited transition"):
        with session_factory.begin() as session:
            session.execute(
                text(
                    "UPDATE risk_model_versions SET status = 'REJECTED', "
                    "status_sequence = status_sequence + 1 WHERE id = :id"
                ),
                {"id": version.id},
            )
    assert _versions(session_factory)[0].status == "CANDIDATE"


# --- 3. ACTIVE uniqueness ------------------------------------------------------------


def test_version_switch_keeps_exactly_one_active_per_scope(session_factory, auto_stack):
    first, second = _two_active_versions(session_factory, auto_stack)
    assert (first.version, first.status) == (1, "RETIRED")
    assert (second.version, second.status) == (2, "ACTIVE")
    assert second.previous_active_version_id == first.id
    assert _path(_transitions(session_factory, first.id))[-1] == ("ACTIVE", "RETIRED")
    assert _transitions(session_factory, first.id)[-1].reason == "superseded_by_new_activation"
    with session_factory() as session:
        assert session.scalar(
            select(sa.func.count()).select_from(RiskModelVersionModel).where(
                RiskModelVersionModel.scope == "controlled_demo",
                RiskModelVersionModel.status == "ACTIVE",
            )
        ) == 1
    # The database itself refuses a second ACTIVE version in the scope.
    with pytest.raises(DBAPIError, match="uq_risk_model_versions_active_scope"):
        with session_factory.begin() as session:
            session.execute(
                text("SELECT transition_model_version(:id, 'ACTIVE', 'force', '{}'::jsonb)"),
                {"id": first.id},
            )


# --- 4. Activation needs evaluation and a verified hash -------------------------------


def test_candidate_waits_for_auditor_promotion_with_verified_hash(session_factory, manual_stack):
    users, _, drain, registry, _ = manual_stack
    _seed_outcomes(session_factory, users, count=40, start=START)
    drain()
    (version,) = _versions(session_factory)
    with session_factory() as session:
        run = session.get(CalibrationRunModel, version.calibration_run_id)
    assert version.status == "CANDIDATE"
    assert (run.deployment_status, run.activation_reason) == ("not_deployed", AWAITING_PROMOTION)
    # Nothing ACTIVE yet: inference stays on the baseline.
    assert _inference(session_factory).calibration_run_id is None

    with pytest.raises(RegistryForbidden):
        registry.activate_version(version.id, users["risk"], reason="Promote after review")
    activated = registry.activate_version(
        version.id, users["auditor"], reason="Holdout Brier improved; reviewed by audit"
    )
    assert activated["status"] == "ACTIVE"
    assert activated["promotion_reason"] == "Holdout Brier improved; reviewed by audit"
    promotion = activated["transitions"][-1]
    assert (promotion["from_status"], promotion["to_status"]) == ("CANDIDATE", "ACTIVE")
    assert promotion["actor"] == "auditor.demo"
    assert promotion["actor_user_id"] == str(users["auditor"].user_id)
    assert promotion["reason"] == "Holdout Brier improved; reviewed by audit"
    assert promotion["artifact_hash"] == version.artifact_hash
    assert promotion["evaluation_metrics"]["artifact_sha256_verified"] == version.artifact_hash
    assert promotion["evaluation_metrics"]["evaluation"] == version.evaluation
    with session_factory() as session:
        run = session.get(CalibrationRunModel, version.calibration_run_id)
        ledger = session.scalars(
            select(LedgerEventModel).where(
                LedgerEventModel.event_type == "CALIBRATION_MANUALLY_ACTIVATED"
            )
        ).all()
    assert (run.deployment_status, run.activation_mode) == ("active", "manual_promotion")
    assert len(ledger) == 1 and ledger[0].payload["model_version_id"] == str(version.id)

    applied = _inference(session_factory)
    assert applied.model_version_id == str(version.id)
    assert applied.calibration_run_id == str(version.calibration_run_id)
    with pytest.raises(RegistryConflict, match="not CANDIDATE"):
        registry.activate_version(version.id, users["auditor"], reason="Promote it again")


def test_activation_refuses_a_tampered_artifact(session_factory, manual_stack):
    users, _, drain, registry, _ = manual_stack
    _seed_outcomes(session_factory, users, count=40, start=START)
    drain()
    (version,) = _versions(session_factory)
    artifact = Path(version.artifact_path)
    artifact.write_bytes(artifact.read_bytes() + b" ")
    with pytest.raises(RegistryConflict, match="hash does not match"):
        registry.activate_version(version.id, users["auditor"], reason="Promote tampered one")
    assert _versions(session_factory)[0].status == "CANDIDATE"
    assert len(_transitions(session_factory, version.id)) == 3
    assert registry.get_version(version.id, users["auditor"])["artifact_check"]["consistent"] is False


def test_activation_requires_a_passed_evaluation(session_factory, manual_stack):
    users, _, drain, _, _ = manual_stack
    _seed_outcomes(session_factory, users, count=40, start=START)
    drain()
    (version,) = _versions(session_factory)
    # An unevaluated DRAFT over the same kind of artifact.
    run_id = uuid.uuid4()
    with session_factory.begin() as session:
        source = session.get(CalibrationRunModel, version.calibration_run_id)
        session.add(CalibrationRunModel(
            calibration_run_id=run_id, trigger_outcome_id=None, trigger_job_id=None,
            dataset_sha256="f" * 64, sample_count=41, positive_count=20, negative_count=21,
            metrics_before=source.metrics_before, metrics_after=source.metrics_after,
            configuration={}, status="eligible_candidate",
            artifact_locator=source.artifact_locator, artifact_sha256=source.artifact_sha256,
            artifact_schema=source.artifact_schema, failure_code=None,
            deployment_status="not_deployed", deployment_scope="controlled_demo",
            activation_mode=None, activation_reason=AWAITING_PROMOTION,
            started_at=START, completed_at=START,
        ))
        session.flush()
        draft = ModelRegistryRepository().register(
            session, session.get(CalibrationRunModel, run_id),
            created_by="test", created_by_user_id=None,
        )
        draft_id = draft.id
    with pytest.raises(DBAPIError, match="illegal model version transition DRAFT -> ACTIVE"):
        with session_factory.begin() as session:
            session.execute(
                text("SELECT transition_model_version(:id, 'ACTIVE', 'skip', '{}'::jsonb)"),
                {"id": draft_id},
            )
    # Even a CANDIDATE row is refused when no evaluation was recorded.
    with pytest.raises(DBAPIError, match="ck_risk_model_versions_evaluated_contract"):
        with session_factory.begin() as session:
            for target in ("EVALUATING", "CANDIDATE", "ACTIVE"):
                session.execute(
                    text("SELECT transition_model_version(:id, :to, 'skip', '{}'::jsonb)"),
                    {"id": draft_id, "to": target},
                )


# --- 5. Rollback -----------------------------------------------------------------------


def test_rollback_restores_the_previous_active_version(session_factory, auto_stack):
    users, _, _, registry, _ = auto_stack
    first, second = _two_active_versions(session_factory, auto_stack)
    with pytest.raises(RegistryConflict, match="not ACTIVE"):
        registry.rollback_version(first.id, users["auditor"], reason_code="BAD_DRIFT")
    with pytest.raises(RegistryForbidden):
        registry.rollback_version(second.id, users["risk"], reason_code="BAD_DRIFT")

    restored = registry.rollback_version(second.id, users["auditor"], reason_code="BAD_DRIFT")
    assert restored["id"] == str(first.id)
    assert restored["status"] == "ACTIVE"
    first_now, second_now = _versions(session_factory)
    assert (first_now.status, second_now.status) == ("ACTIVE", "ROLLED_BACK")
    rolled = _transitions(session_factory, second.id)[-1]
    assert (rolled.from_status, rolled.to_status, rolled.reason) == (
        "ACTIVE", "ROLLED_BACK", "manual_rollback",
    )
    assert rolled.actor_user_id == users["auditor"].user_id
    back = _transitions(session_factory, first.id)[-1]
    assert (back.from_status, back.to_status) == ("RETIRED", "ACTIVE")
    assert back.reason == "manual_rollback:BAD_DRIFT"
    assert back.evaluation_metrics == first.evaluation
    assert _inference(session_factory).model_version_id == str(first.id)
    # The restored first version has no predecessor to roll back to.
    with pytest.raises(RegistryConflict, match="no rollback predecessor"):
        registry.rollback_version(first.id, users["auditor"], reason_code="BAD_DRIFT")

    history = registry.activation_history(users["auditor"], scope="controlled_demo")
    assert [(h["version_label"], h["from_status"], h["to_status"]) for h in history[:2]] == [
        ("calibration:controlled_demo@v1", "RETIRED", "ACTIVE"),
        ("calibration:controlled_demo@v2", "ACTIVE", "ROLLED_BACK"),
    ]
    assert all(h["to_status"] == "ACTIVE" or h["from_status"] == "ACTIVE" for h in history)


# --- 6. Delete protection and immutability ---------------------------------------------


def test_active_version_cannot_be_deleted_and_history_is_immutable(session_factory, auto_stack):
    users, _, drain, _, _ = auto_stack
    _seed_outcomes(session_factory, users, count=40, start=START)
    drain()
    (version,) = _versions(session_factory)
    with pytest.raises(DBAPIError, match="ACTIVE model version cannot be deleted"):
        with session_factory.begin() as session:
            session.execute(
                text("DELETE FROM risk_model_versions WHERE id = :id"), {"id": version.id}
            )
    with pytest.raises(DBAPIError, match="identity and artifact are immutable"):
        with session_factory.begin() as session:
            session.execute(
                text("UPDATE risk_model_versions SET artifact_hash = :h WHERE id = :id"),
                {"h": "0" * 64, "id": version.id},
            )
    with pytest.raises(DBAPIError, match="evaluation is immutable"):
        with session_factory.begin() as session:
            session.execute(
                text("UPDATE risk_model_versions SET evaluation = '{}'::jsonb WHERE id = :id"),
                {"id": version.id},
            )
    for statement in (
        "UPDATE risk_model_version_transitions SET reason = 'edited' WHERE model_version_id = :id",
        "DELETE FROM risk_model_version_transitions WHERE model_version_id = :id",
    ):
        with pytest.raises(DBAPIError):
            with session_factory.begin() as session:
                session.execute(text(statement), {"id": version.id})
    assert len(_transitions(session_factory, version.id)) == 4


# --- 7. Audit records --------------------------------------------------------------------


def test_every_status_change_records_from_to_actor_time_and_reason(session_factory, auto_stack):
    users, _, _, registry, _ = auto_stack
    first, second = _two_active_versions(session_factory, auto_stack)
    registry.rollback_version(second.id, users["auditor"], reason_code="BAD_DRIFT")
    with session_factory() as session:
        rows = session.scalars(select(RiskModelVersionTransitionModel)).all()
        versions = {item.id: item for item in session.scalars(select(RiskModelVersionModel))}
    assert rows
    for row in rows:
        assert row.to_status and row.reason and row.actor_label and row.recorded_at
        assert row.artifact_hash == versions[row.model_version_id].artifact_hash
        if row.to_status == "ACTIVE":
            assert row.evaluation_metrics
    # Every version's current status equals the last audited transition.
    for version in versions.values():
        last = max(
            (row for row in rows if row.model_version_id == version.id),
            key=lambda row: row.status_sequence,
        )
        assert (last.to_status, last.status_sequence) == (version.status, version.status_sequence)
    worker_rows = [row for row in rows if row.actor_user_id is None]
    assert {row.actor_label for row in worker_rows} == {"system:calibration-worker"}


def test_risk_decision_records_the_model_version_used(session_factory, auto_stack):
    users, _, drain, _, _ = auto_stack
    _seed_outcomes(session_factory, users, count=40, start=START)
    drain()
    (version,) = _versions(session_factory)
    service, workflow_users, assessed = _assess(session_factory, scope="controlled_demo")
    with session_factory() as session:
        record = session.scalar(select(RiskDecisionRecordModel))
    assert record.model_version_id == version.id
    assert record.calibration_run_id == version.calibration_run_id
    (serialized,) = service.risk_decisions(assessed["request_id"], workflow_users["financier"])
    assert serialized["model_version_id"] == str(version.id)
    # An incompatible request records the attempted version, not an applied one.
    _assess_external(session_factory, service, workflow_users)
    with session_factory() as session:
        attempted = session.scalar(
            select(RiskDecisionRecordModel).where(
                RiskDecisionRecordModel.request_scope == "external_verified"
            )
        )
    assert attempted.calibration_run_id is None
    assert attempted.model_version_id == version.id
    assert attempted.scope_result == "reject"


def _assess_external(session_factory, service, users):
    payload = draft_payload().model_copy(
        update={"contract_number": "SCF-2026-002", "invoice_number": "INV-2026-002"}
    )
    draft = service.create_draft(payload, users["supplier"])
    with session_factory.begin() as session:
        session.get(
            FinancingRequestModel, uuid.UUID(draft["request_id"])
        ).assessment_scope = "external_verified"
    submitted = service.submit(draft["request_id"], 1, users["supplier"])
    confirmed = service.confirm_trade(
        draft["request_id"], submitted["version"], confirmed=True, comment="Verified",
        user=users["core_enterprise"], confirmed_payable_amount=Decimal("1500000.00"),
    )
    return service.assess_risk(draft["request_id"], confirmed["version"], users["financier"])


# --- 8. Recovery after restart -------------------------------------------------------------


def test_registry_state_survives_a_restart(session_factory, auto_stack, tmp_path):
    users, _, _, registry, _ = auto_stack
    first, second = _two_active_versions(session_factory, auto_stack)
    registry.rollback_version(second.id, users["auditor"], reason_code="BAD_DRIFT")
    before = (
        registry.list_versions(users["auditor"]),
        registry.activation_history(users["auditor"]),
        registry.get_version(first.id, users["auditor"]),
    )
    restarted_outcomes = OutcomeService(session_factory, artifact_root=tmp_path)
    restarted_outcomes.reconcile_deployments()
    restarted = ModelRegistryService(session_factory, outcome_service=restarted_outcomes)
    after = (
        restarted.list_versions(users["auditor"]),
        restarted.activation_history(users["auditor"]),
        restarted.get_version(first.id, users["auditor"]),
    )
    assert after == before
    assert after[0]["active_by_scope"]["controlled_demo"]["id"] == str(first.id)
    assert _inference(session_factory).model_version_id == str(first.id)


def test_migration_backfills_existing_artifacts_as_versions(isolated_postgres_engine):
    engine = isolated_postgres_engine
    config = Config(str(ROOT / "alembic.ini"))
    run_id = uuid.uuid4()
    with engine.connect() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "20260925_0016")
        # Raw SQL: the ORM already knows columns added by later revisions.
        connection.execute(
            text(
                "INSERT INTO calibration_runs (calibration_run_id, dataset_sha256, sample_count, "
                "positive_count, negative_count, metrics_before, metrics_after, configuration, "
                "status, artifact_locator, artifact_sha256, artifact_schema, deployment_status, "
                "deployment_scope, activation_mode, activation_reason, activated_at, "
                "started_at, completed_at) VALUES (:id, :dataset, 30, 10, 20, "
                "'{\"brier\": 0.2}', '{\"brier\": 0.1}', '{}', 'eligible_candidate', "
                "'/artifacts/a.json', :artifact, 'daibm.platt-calibration.v3', 'active', "
                "'controlled_demo', 'automatic', 'oof_improved', :at, :at, :at)"
            ),
            {"id": run_id, "dataset": "a" * 64, "artifact": "b" * 64, "at": START},
        )
        connection.commit()
        command.upgrade(config, "20260926_0017")
        row = connection.execute(
            text(
                "SELECT v.status, v.version, v.evaluation_passed, t.reason, t.to_status "
                "FROM risk_model_versions v JOIN risk_model_version_transitions t "
                "ON t.model_version_id = v.id WHERE v.calibration_run_id = :id"
            ),
            {"id": run_id},
        ).one()
        assert tuple(row) == ("ACTIVE", 1, True, "migration_backfill", "ACTIVE")
        connection.commit()
        # Backfill-only history can be downgraded and re-applied.
        command.downgrade(config, "20260925_0016")
        command.upgrade(config, "20260926_0017")


# --- API -----------------------------------------------------------------------------------


def test_model_version_api(migrated_engine, session_factory, manual_stack):
    from app.database import Database
    from app.main import create_app

    users, _, drain, _, _ = manual_stack
    _seed_outcomes(session_factory, users, count=40, start=START)
    drain()
    (version,) = _versions(session_factory)
    app = create_app(Database(migrated_engine, session_factory), calibration_worker_enabled=False)
    with TestClient(app) as client:
        assert client.get("/api/v1/model-versions").status_code == 401
        client.post("/api/v1/auth/login", json={"username": "risk.demo", "password": "Demo123!"})
        listing = client.get("/api/v1/model-versions").json()
        assert [item["id"] for item in listing["candidates"]] == [str(version.id)]
        assert listing["active_by_scope"] == {}
        denied = client.post(
            f"/api/v1/model-versions/{version.id}/activate", json={"reason": "Promote this now"}
        )
        assert denied.status_code == 403
        client.cookies.clear()
        client.post("/api/v1/auth/login", json={"username": "auditor.demo", "password": "Demo123!"})
        detail = client.get(f"/api/v1/model-versions/{version.id}").json()
        assert detail["artifact_check"]["consistent"] is True and detail["can_activate"] is True
        short = client.post(f"/api/v1/model-versions/{version.id}/activate", json={"reason": "x"})
        assert short.status_code == 422
        activated = client.post(
            f"/api/v1/model-versions/{version.id}/activate",
            json={"reason": "Reviewed holdout evidence"},
        )
        assert activated.status_code == 200, activated.text
        assert activated.json()["status"] == "ACTIVE"
        history = client.get("/api/v1/model-versions/activation-history").json()
        assert history[0]["to_status"] == "ACTIVE" and history[0]["actor"] == "auditor.demo"
        no_predecessor = client.post(
            f"/api/v1/model-versions/{version.id}/rollback", json={"reason_code": "BAD_DRIFT"}
        )
        assert no_predecessor.status_code == 409
        missing = client.post(
            "/api/v1/model-versions", json={"calibration_run_id": str(uuid.uuid4())}
        )
        assert missing.status_code == 404
        again = client.post(
            "/api/v1/model-versions", json={"calibration_run_id": str(version.calibration_run_id)}
        )
        assert again.status_code == 409
        assert client.get(f"/api/v1/model-versions/{uuid.uuid4()}").status_code == 404
        client.cookies.clear()
        client.post("/api/v1/auth/login", json={"username": "supplier.demo", "password": "Demo123!"})
        assert client.get("/api/v1/model-versions").status_code == 403
