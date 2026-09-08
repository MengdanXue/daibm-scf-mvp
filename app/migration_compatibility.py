"""Frozen PostgreSQL contracts for the colliding historical revision 0010.

This is migration infrastructure, not ORM metadata. Do not update the frozen
contracts when changing application models or adding subsequent migrations.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any

import sqlalchemy as sa


ROOT = Path(__file__).resolve().parents[1]
TABLES = (
    "financing_requests",
    "financing_facilities",
    "facility_installments",
    "facility_actions",
    "calibration_runs",
    "ledger_events",
    "facility_delinquencies",
    "facility_restructures",
    "facility_defaults",
    "facility_writeoffs",
    "outcome_corrections",
    "calibration_jobs",
    "calibration_run_observations",
)
PROOF_NAMES = {
    "confirmed_payable_amount",
    "ck_financing_requests_confirmed_payable_amount",
    "ck_financing_requests_payable_covers_amount",
}
RECOVERY_NAMES = {
    "calibration_runs_dataset_sha256_key",
    "ix_calibration_runs_scope_dataset",
}


def schema_contract(connection: sa.Connection) -> dict[str, str]:
    """Fingerprint column types/defaults, exact constraints/indexes and triggers.

    Proof and recovery are separate contracts because they differ independently
    between the historical branches. PostgreSQL deparses all expressions; no
    revision label or mere table-existence check is trusted as proof of shape.
    """
    result = {}
    proof: list[Any] = []
    recovery: list[Any] = []
    for table in TABLES:
        oid = connection.scalar(sa.text("SELECT to_regclass(:table)"), {"table": table})
        if oid is None:
            result[table] = "absent"
            continue
        records: list[Any] = []
        queries = {
            "column": "SELECT attname, format_type(atttypid, atttypmod), attnotnull, pg_get_expr(adbin, adrelid) FROM pg_attribute LEFT JOIN pg_attrdef ON adrelid = attrelid AND adnum = attnum WHERE attrelid = to_regclass(:table) AND attnum > 0 AND NOT attisdropped ORDER BY attname",
            "constraint": "SELECT conname, pg_get_constraintdef(oid), convalidated FROM pg_constraint WHERE conrelid = to_regclass(:table) ORDER BY conname",
            "index": "SELECT indexrelid::regclass::text, pg_get_indexdef(indexrelid), indisvalid, indisready FROM pg_index WHERE indrelid = to_regclass(:table) ORDER BY indexrelid::regclass::text",
            "trigger": "SELECT tgname, pg_get_triggerdef(oid), tgenabled, pg_get_functiondef(tgfoid) FROM pg_trigger WHERE tgrelid = to_regclass(:table) AND NOT tgisinternal ORDER BY tgname",
        }
        for kind, query in queries.items():
            for row in connection.execute(sa.text(query), {"table": table}):
                record = [kind, *row]
                if table == "financing_requests" and row[0] in PROOF_NAMES:
                    proof.append(record)
                elif table == "calibration_runs" and row[0] in RECOVERY_NAMES:
                    recovery.append(record)
                else:
                    records.append(record)
        result[table] = _digest(records)
    result["proof"] = _digest(proof) if proof else "absent"
    result["recovery"] = _digest(recovery)
    return result


def _digest(records: list[Any]) -> str:
    return hashlib.sha256(
        json.dumps(records, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def _contracts() -> dict[str, dict[str, str]]:
    return json.loads((ROOT / "alembic/historical_0010_contract.json").read_text(encoding="utf-8"))


def _reject(detail: str) -> None:
    raise RuntimeError(
        "incompatible historical schema: " + detail + ". Stop; do not stamp or drop objects. "
        "Back up and inspect a clone using docs/runbooks/0010-compatibility.md."
    )


def inspect_historical_shape(connection: sa.Connection) -> tuple[str, bool, bool]:
    connection.execute(sa.text("SELECT pg_advisory_xact_lock(202609070012)"))
    existing = set(sa.inspect(connection).get_table_names())
    locked = [name for name in TABLES if name in existing]
    if locked:
        connection.execute(sa.text("LOCK TABLE " + ", ".join(locked) + " IN ACCESS EXCLUSIVE MODE"))
    actual = schema_contract(connection)
    contracts = _contracts()
    proof = actual["proof"] != "absent"
    if proof and actual["proof"] != contracts["proof"]["proof"]:
        _reject("partial or modified payable proof contract")
    recovery = actual["recovery"] == contracts["recovery"]["recovery"]
    if not recovery and actual["recovery"] != contracts["baseline"]["recovery"]:
        _reject("partial or modified calibration recovery contract")
    for shape in ("baseline", "lifecycle"):
        if all(actual[name] == contracts[shape][name] for name in TABLES):
            return shape, proof, recovery
    mismatches = [name for name in TABLES if actual[name] != contracts["lifecycle"][name]]
    _reject("unrecognized columns, constraints, indexes or triggers: " + ", ".join(mismatches))
    raise AssertionError("unreachable")


def require_baseline(connection: sa.Connection) -> None:
    if inspect_historical_shape(connection) != ("baseline", False, False):
        _reject("revision 0009 must have the intact baseline schema")


def reconcile_lifecycle(connection: sa.Connection) -> None:
    shape, proof, recovery = inspect_historical_shape(connection)
    if recovery:
        _reject("revision 0010 unexpectedly has 0011 recovery objects")
    if shape == "baseline":
        if not proof:
            _reject("revision 0010 matches neither historical implementation")
        path = ROOT / "alembic/versions/20260824_0010_lifecycle_corrections_scope.py"
        spec = importlib.util.spec_from_file_location("historical_lifecycle_0010", path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.upgrade_lifecycle_schema()


def proof_already_present(connection: sa.Connection) -> bool:
    shape, proof, recovery = inspect_historical_shape(connection)
    if shape != "lifecycle" or not recovery:
        _reject("revision 0011 must have the intact lifecycle and recovery contracts")
    return proof
