"""Opt-in, manifest-bound dispatch of NEW synthetic maintenance anchors only.

This does not change the application, public API, or normal global dispatcher.
The operator must pin the local container/database/gateway and freeze other
writers. Never send ``anchor_ids`` to the existing global dispatch API: that
API does not implement filtering. Private manifests and baselines stay local.

CLI: --allow-synthetic-dispatch, with one JSON object on stdin:
  create: action, run_id, subject_ids, baseline_anchor_ids
  dispatch: action, manifest, baseline_anchor_ids, optional limit
Only app.config and app.database are used; app.main is never imported/started.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
import os
import re
import sys
from types import MappingProxyType
from uuid import UUID, uuid4

from sqlalchemy import or_, select, text

from app.ledger import canonical_json
from app.models import FinancingRequestModel, LedgerEventModel
from app.models_advanced import AnchorOutboxModel
from app.repositories.anchors import AnchorOutboxRepository
from app.services.anchor_dispatch import AnchorDispatchService, FabricGatewayClient


SCHEMA = "daibm.synthetic-dispatch.v1"
_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_MAX_TARGETS = 100


def _digest(value):
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _ids(values, *, label, nonempty=False):
    if values is None or isinstance(values, (str, bytes, dict)):
        raise ValueError(f"{label} must be an explicit collection of UUIDs")
    try:
        result = tuple(UUID(str(value)) for value in values)
    except (TypeError, ValueError, AttributeError) as error:
        raise ValueError(f"{label} must contain valid UUIDs") from error
    if len(result) != len(set(result)) or (nonempty and not result):
        raise ValueError(f"{label} must be unique and nonempty when selecting targets")
    return result


def baseline_digest(baseline_anchor_ids):
    ids = _ids(baseline_anchor_ids, label="baseline_anchor_ids")
    return _digest(sorted(str(value) for value in ids))


def _marker(run_id):
    if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
        raise ValueError("run_id must be an explicit safe maintenance identifier")
    return f"SYNTHETIC-{run_id}-"


def _envelope_digest(row):
    return _digest(AnchorOutboxRepository.envelope(row))


def _validate_application(application, *, marker, expected=None):
    if application is None:
        raise ValueError("Synthetic application does not exist")
    for field in ("contract_number", "invoice_number"):
        value = getattr(application, field)
        if not isinstance(value, str) or not value.startswith(marker) or len(value) <= len(marker):
            raise ValueError("Both application identifiers must carry this run's SYNTHETIC marker")
        if expected is not None and value != getattr(expected, field):
            raise ValueError("Synthetic application identity changed after manifest creation")
    if application.assessment_scope != "controlled_demo":
        raise ValueError("Maintenance dispatch requires a controlled_demo synthetic application")


def _validate_ledger_link(session, row):
    event = session.get(LedgerEventModel, row.ledger_event_id)
    if event is None or event.entity_id != row.subject_id or event.event_hash != row.event_hash:
        raise ValueError("Anchor does not match its persisted ledger event")


def create_manifest(session, *, run_id, subject_ids, baseline_anchor_ids):
    """Read only: bind exact synthetic applications and all their current anchors."""
    if session.new or session.dirty or session.deleted:
        raise ValueError("Manifest creation requires a clean session and already persisted data")
    marker = _marker(run_id)
    subjects = _ids(subject_ids, label="subject_ids", nonempty=True)
    baseline = _ids(baseline_anchor_ids, label="baseline_anchor_ids")
    if len(subjects) > _MAX_TARGETS:
        raise ValueError("Too many synthetic applications for one manifest")
    applications = list(session.scalars(select(FinancingRequestModel).where(
        FinancingRequestModel.request_id.in_(subjects)
    )))
    if {row.request_id for row in applications} != set(subjects):
        raise ValueError("A selected synthetic application does not exist")
    for application in applications:
        _validate_application(application, marker=marker)
    anchors = list(session.scalars(select(AnchorOutboxModel).where(
        AnchorOutboxModel.subject_id.in_(subjects)
    )))
    if not anchors or len(anchors) > _MAX_TARGETS:
        raise ValueError("A manifest must contain between 1 and 100 exact anchors")
    if {row.subject_id for row in anchors} != set(subjects):
        raise ValueError("Every selected application must already have a persisted anchor")
    if set(baseline) & {row.anchor_id for row in anchors}:
        raise ValueError("Historical baseline anchors must never enter a synthetic manifest")
    for row in anchors:
        _validate_ledger_link(session, row)
    return {
        "schema": SCHEMA,
        "run_id": run_id,
        "synthetic_marker": marker,
        "baseline_ids_sha256": baseline_digest(baseline),
        "subjects": [{
            "application_id": str(row.request_id),
            "contract_number": row.contract_number,
            "invoice_number": row.invoice_number,
        } for row in sorted(applications, key=lambda value: str(value.request_id))],
        "anchors": [{
            "anchor_id": str(row.anchor_id),
            "subject_id": str(row.subject_id),
            "event_hash": row.event_hash,
            "envelope_sha256": _envelope_digest(row),
        } for row in sorted(anchors, key=lambda value: str(value.anchor_id))],
    }


@dataclass(frozen=True)
class _Subject:
    application_id: UUID
    contract_number: str
    invoice_number: str


@dataclass(frozen=True)
class _Anchor:
    anchor_id: UUID
    subject_id: UUID
    event_hash: str
    envelope_sha256: str


def _parse_manifest(manifest, baseline_anchor_ids):
    baseline = frozenset(_ids(baseline_anchor_ids, label="baseline_anchor_ids"))
    if not isinstance(manifest, dict) or set(manifest) != {
        "schema", "run_id", "synthetic_marker", "baseline_ids_sha256", "subjects", "anchors",
    }:
        raise ValueError("Invalid synthetic manifest structure")
    marker = _marker(manifest["run_id"])
    if (manifest["schema"] != SCHEMA or manifest["synthetic_marker"] != marker
            or manifest["baseline_ids_sha256"] != baseline_digest(baseline)):
        raise ValueError("Manifest schema, run marker or maintenance baseline does not match")
    subjects = {}
    if not isinstance(manifest["subjects"], list) or not 1 <= len(manifest["subjects"]) <= _MAX_TARGETS:
        raise ValueError("Manifest must contain explicit synthetic applications")
    for item in manifest["subjects"]:
        if not isinstance(item, dict) or set(item) != {"application_id", "contract_number", "invoice_number"}:
            raise ValueError("Invalid manifest application")
        subject_id = _ids([item["application_id"]], label="application_id")[0]
        if subject_id in subjects:
            raise ValueError("Duplicate manifest application")
        for field in ("contract_number", "invoice_number"):
            value = item[field]
            if not isinstance(value, str) or not value.startswith(marker) or len(value) <= len(marker):
                raise ValueError("Manifest application does not carry the selected synthetic marker")
        subjects[subject_id] = _Subject(subject_id, item["contract_number"], item["invoice_number"])
    anchors = {}
    if not isinstance(manifest["anchors"], list) or not 1 <= len(manifest["anchors"]) <= _MAX_TARGETS:
        raise ValueError("Manifest anchor whitelist must be nonempty and bounded")
    for item in manifest["anchors"]:
        if not isinstance(item, dict) or set(item) != {"anchor_id", "subject_id", "event_hash", "envelope_sha256"}:
            raise ValueError("Invalid manifest anchor")
        anchor_id = _ids([item["anchor_id"]], label="anchor_id")[0]
        subject_id = _ids([item["subject_id"]], label="subject_id")[0]
        if anchor_id in anchors or anchor_id in baseline or subject_id not in subjects:
            raise ValueError("Duplicate, historical, or out-of-scope manifest anchor")
        if any(not isinstance(item[field], str) or not _SHA256.fullmatch(item[field])
               for field in ("event_hash", "envelope_sha256")):
            raise ValueError("Manifest anchor hashes must be canonical SHA-256 values")
        anchors[anchor_id] = _Anchor(anchor_id, subject_id, item["event_hash"], item["envelope_sha256"])
    if {value.subject_id for value in anchors.values()} != set(subjects):
        raise ValueError("Every synthetic application must have an explicit manifest anchor")
    return marker, MappingProxyType(subjects), MappingProxyType(anchors)


class SyntheticAnchorRepository(AnchorOutboxRepository):
    """Only the claim selection is specialized; production dispatch is reused.

    The short lease/claim block mirrors AnchorOutboxRepository.claim_batch.
    Never call the global claim first and filter its returned rows afterwards.
    Settlement also fails closed on lost leases instead of reporting success.
    """

    def __init__(self, manifest, *, baseline_anchor_ids):
        self.marker, self.subjects, self.anchors = _parse_manifest(manifest, baseline_anchor_ids)
        self.claimed_anchor_ids = []

    def _validate_row(self, session, row):
        expected = self.anchors.get(row.anchor_id)
        if (expected is None or row.subject_id != expected.subject_id
                or row.event_hash != expected.event_hash
                or _envelope_digest(row) != expected.envelope_sha256):
            raise ValueError("Persisted anchor no longer matches the synthetic manifest")
        _validate_ledger_link(session, row)
        _validate_application(session.get(FinancingRequestModel, row.subject_id),
                              marker=self.marker, expected=self.subjects[row.subject_id])

    def validate_scope(self, session):
        applications = list(session.scalars(select(FinancingRequestModel).where(
            FinancingRequestModel.request_id.in_(tuple(self.subjects))
        )))
        if {row.request_id for row in applications} != set(self.subjects):
            raise ValueError("A manifest application is missing")
        for application in applications:
            _validate_application(application, marker=self.marker,
                                  expected=self.subjects[application.request_id])
        rows = list(session.scalars(select(AnchorOutboxModel).where(
            AnchorOutboxModel.subject_id.in_(tuple(self.subjects))
        )))
        if {row.anchor_id for row in rows} != set(self.anchors):
            raise ValueError("Manifest must match the exact persisted anchor set of its applications")
        for row in rows:
            self._validate_row(session, row)

    def claim_batch(self, session, *, now, limit, lease_duration):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("claim limit must be between 1 and 100")
        if not isinstance(lease_duration, timedelta) or lease_duration <= timedelta(0):
            raise ValueError("lease duration must be positive")
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Maintenance claim requires an aware timestamp")
        statement = (
            select(AnchorOutboxModel)
            .where(
                AnchorOutboxModel.anchor_id.in_(tuple(self.anchors)),
                AnchorOutboxModel.status == "pending",
                AnchorOutboxModel.next_attempt_at <= now,
                or_(AnchorOutboxModel.lease_token.is_(None),
                    AnchorOutboxModel.lease_expires_at <= now),
            )
            .order_by(AnchorOutboxModel.next_attempt_at.asc(),
                      AnchorOutboxModel.created_at.asc(), AnchorOutboxModel.anchor_id.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        rows = list(session.scalars(statement))
        # Validate all locked selections before making even a lease-field change.
        for row in rows:
            self._validate_row(session, row)
        for row in rows:
            row.lease_token = uuid4()
            row.lease_expires_at = now + lease_duration
            row.attempt_count += 1
            row.last_attempt_at = now
            row.updated_at = now
        session.flush()
        self.claimed_anchor_ids.extend(str(row.anchor_id) for row in rows)
        return rows

    def _leased_row(self, session, anchor_id, lease_token):
        if anchor_id not in self.anchors:
            raise ValueError("Cannot settle an anchor outside the synthetic manifest")
        row = super()._leased_row(session, anchor_id, lease_token)
        if row is None:
            raise RuntimeError("Synthetic dispatch lease was lost; no acceptance success may be reported")
        self._validate_row(session, row)
        return row


def dispatch_manifest(session_factory, gateway, manifest, *, baseline_anchor_ids,
                      clock=None, limit=100):
    """Dispatch only this manifest; never advance or retry historical pending work."""
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("dispatch limit must be between 1 and 100")
    repository = SyntheticAnchorRepository(manifest, baseline_anchor_ids=baseline_anchor_ids)
    # The caller's JSON object may be reused; pin the evidence identity before I/O.
    manifest_hash = _digest(manifest)
    run_id = manifest["run_id"]
    with session_factory() as session:
        repository.validate_scope(session)
    service = AnchorDispatchService(session_factory, gateway, repository=repository, clock=clock)
    summary = service.dispatch_batch(limit=limit)
    with session_factory() as session:
        repository.validate_scope(session)
    return {
        **summary,
        "run_id": run_id,
        "manifest_sha256": manifest_hash,
        "dispatched_anchor_ids": repository.claimed_anchor_ids,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-synthetic-dispatch", action="store_true", required=True)
    parser.parse_args(argv)
    raw = sys.stdin.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError("Maintenance request is too large")
    payload = json.loads(raw)
    if not isinstance(payload, dict) or payload.get("action") not in {"create", "dispatch"}:
        raise ValueError("action must be create or dispatch")
    action = payload["action"]
    required = {"action", "baseline_anchor_ids"}
    required |= {"run_id", "subject_ids"} if action == "create" else {"manifest"}
    optional = set() if action == "create" else {"limit"}
    if not required <= set(payload) or set(payload) - required - optional:
        raise ValueError("Missing or unsupported maintenance request fields")
    # Avoid silently selecting the demo defaults when called outside its pinned container.
    needed_environment = {"POSTGRES_HOST", "POSTGRES_DB", "POSTGRES_USER"}
    if action == "dispatch":
        needed_environment.add("FABRIC_GATEWAY_URL")
    if any(not os.environ.get(key) for key in needed_environment):
        raise ValueError("Explicit database and dispatch gateway configuration is required")
    from app.config import FabricGatewaySettings, PostgresSettings
    from app.database import Database

    database = Database.create(PostgresSettings.from_env().sqlalchemy_url)
    try:
        if action == "create":
            with database.session_factory.begin() as session:
                session.execute(text("SET TRANSACTION READ ONLY"))
                result = create_manifest(session, run_id=payload["run_id"],
                                         subject_ids=payload["subject_ids"],
                                         baseline_anchor_ids=payload["baseline_anchor_ids"])
        else:
            result = dispatch_manifest(
                database.session_factory, FabricGatewayClient(FabricGatewaySettings.from_env()),
                payload["manifest"], baseline_anchor_ids=payload["baseline_anchor_ids"],
                limit=payload.get("limit", 100),
            )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return result
    finally:
        database.dispose()


if __name__ == "__main__":
    main()
