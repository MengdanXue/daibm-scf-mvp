from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import ResearchSettings
from app.database import Database
from app.main import create_app
from app.models import FinancingRequestModel, LedgerEventModel
from app.models_outcome import CalibrationRunModel
from app.repositories.ledger import LedgerRepository
from app.models_research import IntegrityIncidentModel
from app.service import DEMO_SCENARIOS, FinancingService
from app.services.integrity import (
    IntegrityService,
    RecoverySourceMismatch,
)


def _event_state(session_factory) -> list[dict[str, object]]:
    with session_factory() as session:
        return [
            {
                "id": event.id,
                "created_at": event.created_at,
                "stream_id": event.stream_id,
                "event_type": event.event_type,
                "entity_id": event.entity_id,
                "payload": event.payload,
                "previous_hash": event.previous_hash,
                "event_hash": event.event_hash,
            }
            for event in session.scalars(
                select(LedgerEventModel).order_by(LedgerEventModel.id.asc())
            )
        ]


def test_detection_commits_unresolved_incident_before_recovery(session_factory):
    financing = FinancingService(session_factory)
    integrity = IntegrityService(session_factory)
    financing.seed_demo()
    financing.tamper_demo_ledger()

    incident = integrity.detect()

    assert incident.recovery_status == "unresolved"
    assert incident.expected_hash != incident.actual_hash
    assert incident.corrupted_payload["demo_tampered"] is True
    with session_factory() as session:
        persisted = session.get(
            IntegrityIncidentModel,
            incident.integrity_incident_id,
        )
        verification = integrity.ledger_repository.verify(session)
    assert persisted is not None
    assert persisted.recovery_status == "unresolved"
    assert verification["valid"] is False


def test_bad_trusted_source_leaves_incident_unresolved_and_ledger_invalid(
    session_factory,
):
    financing = FinancingService(session_factory)
    financing.seed_demo()
    financing.tamper_demo_ledger()
    detector = IntegrityService(session_factory)
    incident = detector.detect()

    def bad_source(_session, _event):
        return {"score": 0.01, "band": "low", "model": "wrong"}

    recovery = IntegrityService(
        session_factory,
        trusted_payload_resolver=bad_source,
    )
    with pytest.raises(RecoverySourceMismatch):
        recovery.recover(incident.integrity_incident_id, "test-operator")

    with session_factory() as session:
        persisted = session.get(
            IntegrityIncidentModel,
            incident.integrity_incident_id,
        )
        verification = recovery.ledger_repository.verify(session)
        event_count = len(_event_state(session_factory))
    assert persisted is not None
    assert persisted.recovery_status == "unresolved"
    assert persisted.operator is None
    assert verification["valid"] is False
    assert event_count == 12


def test_recovery_preserves_original_history_and_appends_evidence(session_factory):
    financing = FinancingService(session_factory)
    integrity = IntegrityService(session_factory)
    financing.seed_demo()
    original = _event_state(session_factory)
    financing.tamper_demo_ledger()
    incident = integrity.detect()

    result = integrity.recover(
        incident.integrity_incident_id,
        "defense-demo",
    )

    recovered = _event_state(session_factory)
    assert recovered[:12] == original
    assert [event["event_type"] for event in recovered[12:]] == [
        "INTEGRITY_VIOLATION_DETECTED",
        "LEDGER_RECOVERY_COMPLETED",
    ]
    assert result["verification"]["valid"] is True
    assert result["incident"]["recovery_status"] == "recovered"
    assert result["incident"]["operator"] == "defense-demo"
    with session_factory() as session:
        persisted = session.get(
            IntegrityIncidentModel,
            incident.integrity_incident_id,
        )
    assert persisted is not None
    assert persisted.recovery_status == "recovered"
    assert persisted.recovered_at is not None


def test_tamper_and_recover_api_exposes_durable_incident(
    session_factory,
    migrated_engine,
    login_user,
):
    application = create_app(
        Database(migrated_engine, session_factory),
        research_settings=ResearchSettings.from_env({}),
    )
    with TestClient(application) as client:
        login_user(client, "auditor.demo")
        client.post("/api/demo/seed")

        tampered = client.post("/api/demo/tamper")
        assert tampered.status_code == 200
        assert tampered.json()["valid"] is False
        incident_id = tampered.json()["integrity_incident_id"]

        recovered = client.post(
            "/api/demo/recover",
            json={
                "incident_id": incident_id,
                "operator": "defense-demo",
            },
        )

    assert recovered.status_code == 200
    assert recovered.json()["verification"]["valid"] is True
    assert recovered.json()["incident"]["integrity_incident_id"] == incident_id
    assert recovered.json()["appended_event_ids"] == [13, 14]
    assert uuid.UUID(incident_id)


class HistoricalRiskWriter(LedgerRepository):
    """Create historical four-field evidence before sealing, never rewrite a hash."""

    def append_many(self, session, entity_id, events, **kwargs):
        historical = [
            (kind, {key: payload[key] for key in ("score", "band", "top_contributions", "model")})
            if kind == "RISK_ASSESSMENT"
            else (kind, payload)
            for kind, payload in events
        ]
        return super().append_many(session, entity_id, historical, **kwargs)


def _seed_recoverable_request(session_factory, tmp_path, mode):
    if mode in {"calibrated", "fallback"}:
        from tests.test_calibration_jobs import _seed_jobs, NOW
        from app.services.calibration_jobs import CalibrationJobService
        from app.services.outcomes import OutcomeService

        _seed_jobs(
            session_factory,
            controlled_count=40 if mode == "calibrated" else 0,
            external_count=40 if mode == "fallback" else 0,
        )
        outcomes = OutcomeService(session_factory, artifact_root=tmp_path, clock=lambda: NOW)
        worker = CalibrationJobService(session_factory, outcome_service=outcomes, clock=lambda: NOW)
        assert worker.process_next("recovery-fixture")
        with session_factory() as session:
            assert (
                session.scalar(
                    select(CalibrationRunModel).where(
                        CalibrationRunModel.deployment_status == "active"
                    )
                )
                is not None
            )
    financing = FinancingService(
        session_factory, ledger_repository=(HistoricalRiskWriter() if mode == "legacy" else None)
    )
    request = financing.create_request(DEMO_SCENARIOS[0])
    return financing, uuid.UUID(request["request_id"])


@pytest.mark.parametrize("mode", ["legacy", "new", "calibrated", "fallback"])
def test_recovery_keeps_exact_historical_and_new_payload_bytes_without_current_inference(
    session_factory,
    tmp_path,
    monkeypatch,
    mode,
):
    financing, request_id = _seed_recoverable_request(session_factory, tmp_path, mode)
    original = _event_state(session_factory)
    risk = next(row["payload"] for row in original if row["event_type"] == "RISK_ASSESSMENT")
    if mode == "calibrated":
        assert risk["calibration_run_id"] is not None
        assert risk["raw_score"] != risk["score"]
    if mode == "fallback":
        assert risk["calibration_fallback_code"] == "calibration_scope_mismatch"
        assert risk["attempted_calibration_run_id"] is not None
    if mode in {"calibrated", "fallback"}:
        # Replace the active deployment identity after the risk event was sealed.
        with session_factory.begin() as session:
            active = session.scalar(
                select(CalibrationRunModel).where(CalibrationRunModel.deployment_status == "active")
            )
            values = {
                column.key: getattr(active, column.key) for column in active.__table__.columns
            }
            active.deployment_status = "superseded"
            active.deactivated_at = active.activated_at
            session.flush()
            values.update(
                calibration_run_id=uuid.uuid4(),
                trigger_outcome_id=None,
                trigger_job_id=None,
                previous_active_run_id=active.calibration_run_id,
            )
            session.add(CalibrationRunModel(**values))

    def forbid_current_inference(*args, **kwargs):
        raise AssertionError("Recovery must not run current calibration inference")

    monkeypatch.setattr(
        "app.services.adaptive_risk.AdaptiveRiskInferenceService.assess", forbid_current_inference
    )
    financing.tamper_demo_ledger()
    integrity = IntegrityService(session_factory)
    incident = integrity.detect()
    result = integrity.recover(incident.integrity_incident_id, "recovery-test")
    restored = _event_state(session_factory)
    assert restored[: len(original)] == original
    assert result["verification"]["valid"] is True
    with session_factory() as session:
        assert session.get(FinancingRequestModel, request_id).risk_score == risk["score"]


@pytest.mark.parametrize(
    "mutation",
    [
        "projection_score",
        "projection_raw_score",
        "projection_features",
        "projection_scope",
        "lineage",
        "attempted_lineage",
        "extra_field",
        "wrong_tamper_score",
    ],
)
def test_recovery_rejects_tampered_projection_or_unregistered_lineage_without_writes(
    session_factory,
    tmp_path,
    mutation,
):
    mode = "legacy" if mutation == "wrong_tamper_score" else "fallback"
    financing, request_id = _seed_recoverable_request(session_factory, tmp_path, mode)
    financing.tamper_demo_ledger()
    with session_factory.begin() as session:
        request = session.get(FinancingRequestModel, request_id)
        event = session.scalar(
            select(LedgerEventModel).where(
                LedgerEventModel.entity_id == request_id,
                LedgerEventModel.event_type == "RISK_ASSESSMENT",
            )
        )
        if mutation == "projection_score":
            request.risk_score = 0.123
        elif mutation == "projection_raw_score":
            request.raw_risk_score = 0.123
        elif mutation == "projection_features":
            request.features = {"counterparty_risk": "attacker"}
        elif mutation == "projection_scope":
            request.assessment_scope = "external_verified"
        else:
            payload = dict(event.payload)
            key, value = {
                "lineage": ("calibration_run_id", str(uuid.uuid4())),
                "attempted_lineage": ("attempted_calibration_run_id", str(uuid.uuid4())),
                "extra_field": ("unregistered", "attacker"),
                "wrong_tamper_score": ("score", 0.8888),
            }[mutation]
            payload[key] = value
            event.payload = payload
    corrupted = _event_state(session_factory)
    integrity = IntegrityService(session_factory)
    incident = integrity.detect()
    with pytest.raises(RecoverySourceMismatch):
        integrity.recover(incident.integrity_incident_id, "recovery-test")
    assert _event_state(session_factory) == corrupted
    with session_factory() as session:
        assert (
            session.get(IntegrityIncidentModel, incident.integrity_incident_id).recovery_status
            == "unresolved"
        )
