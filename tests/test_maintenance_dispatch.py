"""Scoped maintenance dispatch tests: use only the isolated PostgreSQL fixtures."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import uuid

import pytest
from sqlalchemy import select

from app.models import FinancingRequestModel
from app.models_advanced import AnchorOutboxModel
from app.repositories.ledger import LedgerRepository
from app.services.anchor_dispatch import GatewayRequestError, GatewayResult
from scripts import maintenance_dispatch as maintenance


RUN_ID = "20260920-test"


class Gateway:
    def __init__(self, status=201):
        self.status = status
        self.sent = []

    def create_anchor(self, envelope):
        self.sent.append(deepcopy(envelope))
        if self.status >= 400:
            raise GatewayRequestError(
                status_code=self.status,
                code="FABRIC_UNAVAILABLE" if self.status == 503 else "ANCHOR_CONFLICT",
                message="Synthetic test response",
            )
        return GatewayResult(status_code=self.status, anchor=dict(envelope))


def seed(session_factory, *, run_id=RUN_ID, count=2, marker=True):
    subject_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    prefix = f"SYNTHETIC-{run_id}-" if marker else "HISTORICAL-"
    with session_factory.begin() as session:
        session.add(FinancingRequestModel(
            request_id=subject_id, created_at=now, updated_at=now,
            applicant_id="maintenance-test", amount=Decimal("100.00"), term_days=30,
            features={}, status="draft", assessment_scope="controlled_demo",
            contract_number=prefix + subject_id.hex,
            invoice_number=prefix + subject_id.hex,
        ))
        session.flush()
        events = LedgerRepository().append_many(session, subject_id, [
            ("FINANCING_REQUEST", {"synthetic": True, "sequence": i})
            for i in range(count)
        ])
        anchor_ids = list(session.scalars(select(AnchorOutboxModel.anchor_id).where(
            AnchorOutboxModel.ledger_event_id.in_([event.id for event in events])
        )))
    return subject_id, anchor_ids


def rows(session_factory, ids=None):
    with session_factory() as session:
        query = select(AnchorOutboxModel)
        if ids is not None:
            query = query.where(AnchorOutboxModel.anchor_id.in_(ids))
        return {
            str(row.anchor_id): {
                column.name: getattr(row, column.name)
                for column in AnchorOutboxModel.__table__.columns
            }
            for row in session.scalars(query)
        }


def manifest_for(session_factory, subject_id, baseline=()):
    with session_factory() as session:
        return maintenance.create_manifest(
            session, run_id=RUN_ID, subject_ids=[subject_id], baseline_anchor_ids=baseline,
        )


def test_only_allowlisted_synthetic_rows_are_claimed_and_old_49_rows_are_unchanged(session_factory):
    _, old_ids = seed(session_factory, count=49, marker=False)
    subject, new_ids = seed(session_factory)
    before = rows(session_factory, old_ids)
    manifest = manifest_for(session_factory, subject, old_ids)
    gateway = Gateway()
    result = maintenance.dispatch_manifest(
        session_factory, gateway, manifest, baseline_anchor_ids=old_ids,
    )
    assert {key: result[key] for key in ("claimed", "anchored", "retryable", "permanent_failed")} == {
        "claimed": 2, "anchored": 2, "retryable": 0, "permanent_failed": 0,
    }
    assert set(result["dispatched_anchor_ids"]) == {str(value) for value in new_ids}
    assert {item["anchorId"] for item in gateway.sent} == {str(value) for value in new_ids}
    assert rows(session_factory, old_ids) == before
    assert all(row["status"] == "anchored" for row in rows(session_factory, new_ids).values())
    repeated = maintenance.dispatch_manifest(
        session_factory, gateway, manifest, baseline_anchor_ids=old_ids,
    )
    assert repeated["claimed"] == 0 and len(gateway.sent) == 2
    assert rows(session_factory, old_ids) == before


@pytest.mark.parametrize("bad_input", ["empty_subjects", "missing_subject", "marker", "invoice", "baseline"])
def test_manifest_creation_fails_closed_without_mutation(session_factory, bad_input):
    subject, ids = seed(session_factory, marker=bad_input != "marker")
    if bad_input == "invoice":
        with session_factory.begin() as session:
            session.get(FinancingRequestModel, subject).invoice_number = "HISTORICAL-INVOICE"
    before = rows(session_factory)
    subjects = [] if bad_input == "empty_subjects" else [uuid.uuid4() if bad_input == "missing_subject" else subject]
    baseline = ids if bad_input == "baseline" else []
    with session_factory() as session, pytest.raises(ValueError):
        maintenance.create_manifest(
            session, run_id=RUN_ID, subject_ids=subjects, baseline_anchor_ids=baseline,
        )
    assert rows(session_factory) == before


@pytest.mark.parametrize("corruption", ["empty", "missing", "subject", "hash", "envelope", "baseline", "run", "omit"])
def test_dispatch_rejects_bad_manifest_before_any_write_or_gateway_call(session_factory, corruption):
    _, old_ids = seed(session_factory, count=1, marker=False)
    subject, _ = seed(session_factory)
    manifest = manifest_for(session_factory, subject, old_ids)
    baseline = old_ids
    if corruption == "empty":
        manifest["anchors"] = []
    elif corruption == "missing":
        manifest["anchors"][0]["anchor_id"] = str(uuid.uuid4())
    elif corruption == "subject":
        manifest["anchors"][0]["subject_id"] = str(uuid.uuid4())
    elif corruption == "hash":
        manifest["anchors"][0]["event_hash"] = "a" * 64
    elif corruption == "envelope":
        manifest["anchors"][0]["envelope_sha256"] = "b" * 64
    elif corruption == "baseline":
        baseline = []
    elif corruption == "run":
        manifest["run_id"] = "different-run"
    else:
        manifest["anchors"].pop()
    before = rows(session_factory)
    gateway = Gateway()
    with pytest.raises(ValueError):
        maintenance.dispatch_manifest(
            session_factory, gateway, manifest, baseline_anchor_ids=baseline,
        )
    assert not gateway.sent and rows(session_factory) == before


def test_manifest_cannot_relabel_a_historical_anchor_as_new(session_factory):
    subject, ids = seed(session_factory)
    manifest = manifest_for(session_factory, subject)
    # Replacing the supplied baseline hash cannot bypass intersection validation.
    manifest["baseline_ids_sha256"] = maintenance.baseline_digest(ids)
    before = rows(session_factory)
    with pytest.raises(ValueError):
        maintenance.dispatch_manifest(
            session_factory, Gateway(), manifest, baseline_anchor_ids=ids,
        )
    assert rows(session_factory) == before


def test_scoped_claim_skip_locked_lease_due_and_rollback(session_factory):
    _, old_ids = seed(session_factory, count=1, marker=False)
    subject, ids = seed(session_factory, count=3)
    now = datetime.now(timezone.utc) + timedelta(minutes=1)
    with session_factory.begin() as session:
        session.get(AnchorOutboxModel, ids[1]).next_attempt_at = now + timedelta(minutes=1)
        leased = session.get(AnchorOutboxModel, ids[2])
        leased.lease_token = uuid.uuid4()
        leased.lease_expires_at = now + timedelta(minutes=1)
    manifest = manifest_for(session_factory, subject, old_ids)
    repository = maintenance.SyntheticAnchorRepository(manifest, baseline_anchor_ids=old_ids)
    before = rows(session_factory)
    with session_factory() as first, session_factory() as second:
        first.begin()
        second.begin()
        try:
            claimed = repository.claim_batch(first, now=now, limit=3, lease_duration=timedelta(seconds=30))
            assert [row.anchor_id for row in claimed] == [ids[0]]
            assert repository.claim_batch(second, now=now, limit=3, lease_duration=timedelta(seconds=30)) == []
        finally:
            second.rollback()
            first.rollback()
    assert rows(session_factory) == before


def test_outage_backoff_and_recovery_remain_scoped_to_new_anchor(session_factory):
    _, old_ids = seed(session_factory, count=49, marker=False)
    subject, ids = seed(session_factory, count=1)
    manifest = manifest_for(session_factory, subject, old_ids)
    before = rows(session_factory, old_ids)
    now = [datetime.now(timezone.utc) + timedelta(minutes=1)]
    gateway = Gateway(503)
    first = maintenance.dispatch_manifest(
        session_factory, gateway, manifest, baseline_anchor_ids=old_ids, clock=lambda: now[0],
    )
    assert first["retryable"] == 1
    state = rows(session_factory, ids)[str(ids[0])]
    assert state["status"] == "pending" and state["attempt_count"] == 1
    assert state["next_attempt_at"] == now[0] + timedelta(seconds=2)
    assert rows(session_factory, old_ids) == before
    premature = maintenance.dispatch_manifest(
        session_factory, gateway, manifest, baseline_anchor_ids=old_ids, clock=lambda: now[0],
    )
    assert premature["claimed"] == 0
    now[0] += timedelta(seconds=2)
    gateway.status = 201
    recovered = maintenance.dispatch_manifest(
        session_factory, gateway, manifest, baseline_anchor_ids=old_ids, clock=lambda: now[0],
    )
    assert recovered["anchored"] == 1
    assert rows(session_factory, ids)[str(ids[0])]["attempt_count"] == 2
    assert rows(session_factory, old_ids) == before


def test_lost_lease_is_not_reported_as_a_success(session_factory):
    subject, ids = seed(session_factory, count=1)
    manifest = manifest_for(session_factory, subject)

    class LeaseStealingGateway(Gateway):
        def create_anchor(self, envelope):
            with session_factory.begin() as session:
                row = session.get(AnchorOutboxModel, ids[0])
                row.lease_token = uuid.uuid4()
            return super().create_anchor(envelope)

    with pytest.raises(RuntimeError, match="lease"):
        maintenance.dispatch_manifest(
            session_factory, LeaseStealingGateway(), manifest, baseline_anchor_ids=[],
        )
    assert rows(session_factory, ids)[str(ids[0])]["status"] == "pending"


@pytest.mark.parametrize("failure", ["conflict", "wrong_envelope"])
def test_original_terminal_failure_semantics_are_preserved_without_touching_history(session_factory, failure):
    _, old_ids = seed(session_factory, count=2, marker=False)
    subject, ids = seed(session_factory, count=1)
    manifest = manifest_for(session_factory, subject, old_ids)
    before = rows(session_factory, old_ids)

    class WrongEnvelopeGateway(Gateway):
        def create_anchor(self, envelope):
            return GatewayResult(status_code=201, anchor={**envelope, "eventHash": "b" * 64})

    gateway = Gateway(409) if failure == "conflict" else WrongEnvelopeGateway()
    result = maintenance.dispatch_manifest(
        session_factory, gateway, manifest, baseline_anchor_ids=old_ids,
    )
    assert result["permanent_failed"] == 1 and result["anchored"] == 0
    target = rows(session_factory, ids)[str(ids[0])]
    assert target["status"] == "permanent_failed" and target["attempt_count"] == 1
    assert target["last_error_code"] == (
        "ANCHOR_CONFLICT" if failure == "conflict" else "GATEWAY_PROTOCOL_ERROR"
    )
    assert rows(session_factory, old_ids) == before
    assert maintenance.dispatch_manifest(
        session_factory, Gateway(), manifest, baseline_anchor_ids=old_ids,
    )["claimed"] == 0  # The maintenance adapter does not auto-retry permanent failures.


def test_manifest_validation_is_pinned_before_gateway_io(session_factory):
    subject, ids = seed(session_factory, count=1)
    manifest = manifest_for(session_factory, subject)

    class MutatingCallerGateway(Gateway):
        def create_anchor(self, envelope):
            manifest["run_id"] = "altered-by-caller"
            manifest["anchors"][0]["event_hash"] = "b" * 64
            return super().create_anchor(envelope)

    original = deepcopy(manifest)
    result = maintenance.dispatch_manifest(
        session_factory, MutatingCallerGateway(), manifest, baseline_anchor_ids=[],
    )
    assert result["run_id"] == RUN_ID
    assert result["manifest_sha256"] == maintenance._digest(original)
    assert result["anchored"] == 1
    assert rows(session_factory, ids)[str(ids[0])]["status"] == "anchored"


def test_manifest_create_does_not_autoflush_pending_session_changes(session_factory):
    subject, _ = seed(session_factory)
    with session_factory() as session:
        row = session.get(FinancingRequestModel, subject)
        row.contract_number = f"SYNTHETIC-{RUN_ID}-pending-edit"
        with pytest.raises(ValueError, match="clean session"):
            maintenance.create_manifest(
                session, run_id=RUN_ID, subject_ids=[subject], baseline_anchor_ids=[],
            )
        session.rollback()
    with session_factory() as session:
        assert session.get(FinancingRequestModel, subject).contract_number != f"SYNTHETIC-{RUN_ID}-pending-edit"


@pytest.mark.parametrize("bad_limit", [0, 101, True])
def test_invalid_dispatch_limit_is_rejected_before_claim(session_factory, bad_limit):
    subject, _ = seed(session_factory)
    manifest = manifest_for(session_factory, subject)
    before = rows(session_factory)
    gateway = Gateway()
    with pytest.raises(ValueError):
        maintenance.dispatch_manifest(
            session_factory, gateway, manifest, baseline_anchor_ids=[], limit=bad_limit,
        )
    assert not gateway.sent and rows(session_factory) == before


def test_cli_requires_opt_in_and_rejects_unknown_action_before_database(monkeypatch):
    with pytest.raises(SystemExit):
        maintenance.main([])
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(json.dumps({"action": "global"})))
    with pytest.raises(ValueError, match="action"):
        maintenance.main(["--allow-synthetic-dispatch"])
