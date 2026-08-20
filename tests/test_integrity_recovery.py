from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import ResearchSettings
from app.database import Database
from app.main import create_app
from app.models import LedgerEventModel
from app.models_research import IntegrityIncidentModel
from app.service import FinancingService
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
