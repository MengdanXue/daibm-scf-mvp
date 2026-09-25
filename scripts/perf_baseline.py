"""Performance baseline: 1000 facilities, 5000 outcomes, 10000 audit events.

Builds the dataset in a fresh database (created and migrated here), then
measures the operations named in the release plan: risk dashboard, risk
detail, alert scan and model lookup, plus the outcome reads that scale with
the outcome count. For every operation it records the median and worst
latency over repeated runs and the number of SQL statements issued.

Usage::

    python scripts/perf_baseline.py --admin-url postgresql+psycopg://u:p@host:port/postgres \
        [--out docs/performance/perf_baseline.json]

The live part of the portfolio (disbursed and overdue facilities) is created
through the facility services; the closed history (facilities carrying
outcome revision chains) is written in bulk with the same rows the services
would leave behind, so the database's integrity triggers still check it.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import create_engine, event, func, select, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.models import FinancingRequestModel, LedgerEventModel  # noqa: E402
from app.models_facility import FinancingFacilityModel, InstallmentModel, PaymentModel  # noqa: E402
from app.models_governance import CalibrationJobModel  # noqa: E402
from app.models_outcome import ActualOutcomeModel  # noqa: E402
from app.repositories.ledger import LedgerRepository  # noqa: E402
from app.schemas_facility import (  # noqa: E402
    CreateFacilityRequest,
    MarkOverdueRequest,
    VersionedFacilityCommand,
)
from app.services.adaptive_risk import AdaptiveRiskInferenceService  # noqa: E402
from app.services.calibration_jobs import CalibrationJobService  # noqa: E402
from app.services.facility import FacilityService  # noqa: E402
from app.services.identity import IdentityService  # noqa: E402
from app.services.model_registry import ModelRegistryService  # noqa: E402
from app.services.outcome_governance import OutcomeGovernanceService  # noqa: E402
from app.services.outcomes import OutcomeService  # noqa: E402
from app.services.risk_insight import RiskInsightService  # noqa: E402
from app.services.risk_operations import RiskDetectionService, seed_default_rules  # noqa: E402

FACILITIES = 1000
CLOSED = 800
OVERDUE = 50
OUTCOMES = 5000
AUDIT_EVENTS = 10000
PASSWORD = "Perf-Baseline-2026!"
ENGINE = "transparent_logistic_baseline_v0.1"


def _hex(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_URL, seed).hex * 2


def build(factory: sessionmaker[Session], artifacts: Path, log: Callable[[str], None]) -> dict[str, Any]:
    identity = IdentityService(factory, demo_password=PASSWORD)
    identity.seed_demo_accounts()
    users = {name: identity.login(f"{name}.demo", PASSWORD).user
             for name in ("supplier", "core", "financier", "risk", "auditor", "admin")}
    with factory.begin() as session:
        seed_default_rules(session)
    lender = users["financier"].organization_id
    start = datetime.now(timezone.utc) - timedelta(days=400)

    # Closed history: facilities, cash, outcome revision chains (5000 records).
    log("closed history")
    per_facility = [OUTCOMES // CLOSED + (1 if index < OUTCOMES % CLOSED else 0) for index in range(CLOSED)]
    chain_heads: list[uuid.UUID] = []
    for batch_start in range(0, CLOSED, 100):
        with factory.begin() as session:
            for index in range(batch_start, min(batch_start + 100, CLOSED)):
                at = start + timedelta(hours=6 * index)
                score = round(0.05 + 0.6 * (index % 20) / 19, 4)
                defaulted = index % 20 >= 13 or index % 9 == 0
                request_id, facility_id, installment_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
                session.add(FinancingRequestModel(
                    request_id=request_id, created_at=at, updated_at=at, applicant_id=f"PERF-{index:04d}",
                    assessment_scope="controlled_demo", amount=Decimal("1000.00"), term_days=90,
                    features={}, risk_score=score, raw_risk_score=score, decision="approved",
                    status="audited", version=1, risk_assessment_id=uuid.uuid4(),
                    risk_engine_version=ENGINE, risk_input_sha256=_hex(f"input:{index}"),
                    risk_assessed_at=at, lender_organization_id=lender,
                ))
                session.flush()
                session.add(FinancingFacilityModel(
                    facility_id=facility_id, request_id=request_id, principal=Decimal("1000.00"),
                    outstanding_amount=Decimal("0.00"), currency="CNY", status="closed", version=1,
                    current_schedule_version=1, closure_reason="repaid",
                    created_by_user_id=users["financier"].user_id, organization_id=lender,
                    created_at=at, updated_at=at, closed_at=at,
                ))
                session.flush()
                session.add_all([
                    InstallmentModel(
                        installment_id=installment_id, facility_id=facility_id, sequence=1,
                        schedule_version=1, due_date=at.date(), amount=Decimal("1000.00"),
                        paid_amount=Decimal("1000.00"), status="paid", created_at=at, updated_at=at,
                    ),
                    PaymentModel(
                        payment_id=uuid.uuid4(), facility_id=facility_id, installment_id=installment_id,
                        submitted_by_user_id=users["financier"].user_id, amount=Decimal("1000.00"),
                        payment_reference=f"perf-{index}", status="confirmed", submitted_at=at,
                        decided_at=at, decided_by_user_id=users["financier"].user_id,
                        decision_comment="Baseline cash",
                    ),
                ])
                session.flush()
                request = session.get(FinancingRequestModel, request_id)
                previous: uuid.UUID | None = None
                for revision in range(1, per_facility[index] + 1):
                    outcome_id = uuid.uuid4()
                    session.add(ActualOutcomeModel(
                        outcome_id=outcome_id, facility_id=facility_id, request_id=request_id,
                        risk_assessment_id=request.risk_assessment_id, model_version_id=None,
                        submitted_by_user_id=users["auditor"].user_id, idempotency_key=uuid.uuid4(),
                        request_sha256=_hex(f"req:{outcome_id}"), defaulted=defaulted,
                        days_past_due=30 if defaulted else 0,
                        loss_amount=Decimal("100.00") if defaulted else Decimal("0.00"),
                        observed_at=at + timedelta(minutes=revision), evidence_sha256=_hex(f"ev:{outcome_id}"),
                        provenance="CONTROLLED_DEMO", original_risk_score=score,
                        risk_engine_version=ENGINE, risk_input_sha256=request.risk_input_sha256,
                        recorded_at=at + timedelta(minutes=revision), revision=revision,
                        supersedes_outcome_id=previous,
                        correction_reason_code=None if previous is None else "DATA_CORRECTION",
                        correction_comment=None if previous is None else "Corrected observation",
                    ))
                    session.flush()
                    previous = outcome_id
                assert previous is not None
                chain_heads.append(previous)

    # Live portfolio through the services: disbursed, some overdue.
    log("live portfolio")
    facilities = FacilityService(factory)
    live_ids: list[str] = []
    for index in range(FACILITIES - CLOSED):
        request_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        with factory.begin() as session:
            session.add(FinancingRequestModel(
                request_id=request_id, created_at=now, updated_at=now, applicant_id="SUPPLIER-001",
                amount=Decimal("1000.00"), term_days=90, features={}, risk_score=0.2, decision="approved",
                explanations=[], status="audited", version=7, created_by_user_id=users["supplier"].user_id,
                supplier_organization_id=users["supplier"].organization_id,
                core_enterprise_organization_id=users["core"].organization_id,
                lender_organization_id=lender,
            ))
        overdue = index < OVERDUE
        first_due = date.today() - timedelta(days=40) if overdue else date.today() + timedelta(days=30)
        facility = facilities.create(CreateFacilityRequest.model_validate({
            "request_id": str(request_id), "principal": "1000.00", "currency": "CNY", "version": 1,
            "idempotency_key": str(uuid.uuid4()),
            "installments": [
                {"sequence": 1, "due_date": first_due.isoformat(), "amount": "500.00"},
                {"sequence": 2, "due_date": (date.today() + timedelta(days=60)).isoformat(), "amount": "500.00"},
            ],
        }), users["financier"])
        for step in (facilities.initiate_disbursement, facilities.confirm_disbursement):
            facility = step(facility["facility_id"], VersionedFacilityCommand(
                version=facility["version"], idempotency_key=uuid.uuid4()), users["financier"])
        if overdue:
            installment = facility["installments"][0]["installment_id"]
            facility = facilities.mark_overdue(facility["facility_id"], installment, MarkOverdueRequest(
                installment_id=installment, days_past_due=40, evidence_sha256=_hex(f"od:{index}"),
                version=facility["version"], idempotency_key=uuid.uuid4()), users["financier"])
        live_ids.append(facility["facility_id"])

    # Audit trail: a hash-chained ledger of at least 10000 events.
    log("audit events")
    ledger = LedgerRepository()
    with factory() as session:
        present = session.scalar(select(func.count()).select_from(LedgerEventModel)) or 0
    remaining = max(0, AUDIT_EVENTS - present)
    for batch in range(0, remaining, 500):
        with factory.begin() as session:
            for offset in range(batch, min(batch + 500, remaining)):
                head = chain_heads[offset % len(chain_heads)]
                ledger.append_many(session, head, [(
                    "ACTUAL_OUTCOME_RECORDED" if offset < len(chain_heads) else "ACTUAL_OUTCOME_SUPERSEDED",
                    {"outcome_id": str(head), "baseline_event": offset},
                )])

    # One ACTIVE model trained on the portfolio's effective outcomes.
    log("model training")
    with factory.begin() as session:
        session.add(CalibrationJobModel(
            job_id=uuid.uuid4(), deployment_scope="controlled_demo", trigger_type="outcome_submitted",
            trigger_outcome_id=chain_heads[-1], trigger_correction_id=None, idempotency_key=uuid.uuid4(),
            status="queued", attempt_count=0, created_at=datetime.now(timezone.utc),
        ))
    outcomes = OutcomeService(factory, artifact_root=artifacts)
    worker = CalibrationJobService(factory, outcome_service=outcomes)
    while worker.process_next("perf-baseline"):
        pass
    with factory() as session:
        counts = {
            "facilities": session.scalar(select(func.count()).select_from(FinancingFacilityModel)),
            "outcomes": session.scalar(select(func.count()).select_from(ActualOutcomeModel)),
            "audit_events": session.scalar(select(func.count()).select_from(LedgerEventModel)),
            "active_models": session.scalar(text("SELECT count(*) FROM risk_model_versions WHERE status='ACTIVE'")),
        }
    return {"users": users, "live": live_ids, "outcomes": outcomes, "counts": counts}


def measure(engine, fn: Callable[[], Any], repeat: int, *, warm_up: bool = True) -> dict[str, Any]:
    statements = [0]

    def count(*_args):
        statements[0] += 1

    event.listen(engine, "before_cursor_execute", count)
    try:
        if warm_up:
            fn()
        samples, queries = [], []
        for _ in range(repeat):
            statements[0] = 0
            started = time.perf_counter()
            fn()
            samples.append((time.perf_counter() - started) * 1000)
            queries.append(statements[0])
    finally:
        event.remove(engine, "before_cursor_execute", count)
    return {
        "median_ms": round(statistics.median(samples), 1),
        "max_ms": round(max(samples), 1),
        "queries": max(queries),
    }


def run(admin_url: str, out: Path | None, repeat: int) -> dict[str, Any]:
    name = f"integration_test_perf_{uuid.uuid4().hex[:8]}"
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    url = make_url(admin_url).set(database=name)
    engine = create_engine(url)
    try:
        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(ROOT / "alembic"))
        with engine.connect() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            connection.commit()
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with tempfile.TemporaryDirectory() as artifacts:
            began = time.perf_counter()
            data = build(factory, Path(artifacts), lambda message: print(f"[perf] {message}", file=sys.stderr))
            build_seconds = round(time.perf_counter() - began, 1)
            users = data["users"]
            insight = RiskInsightService(factory, facility_service=FacilityService(factory))
            detection = RiskDetectionService(factory)
            registry = ModelRegistryService(factory, outcome_service=data["outcomes"])
            governance = OutcomeGovernanceService(factory, outcome_service=data["outcomes"])
            lender = users["financier"].organization_id
            first_scan = measure(engine, detection.scan, 1, warm_up=False)

            def model_lookup() -> None:
                with factory() as session:
                    result = AdaptiveRiskInferenceService().assess(session, 0.4, "controlled_demo", lender)
                    assert result.calibration_run_id is not None

            results = {
                "dashboard (risk_manager)": measure(engine, lambda: insight.dashboard(users["risk"]), repeat),
                "dashboard (admin)": measure(engine, lambda: insight.dashboard(users["admin"]), repeat),
                "risk detail (overdue facility)": measure(
                    engine, lambda: insight.facility_detail(data["live"][0], users["risk"]), repeat),
                "alert scan (first, creates alerts)": first_scan,
                "alert scan (steady state)": measure(engine, detection.scan, repeat),
                "model lookup (decision-time ACTIVE model)": measure(engine, model_lookup, repeat),
                "model registry list": measure(engine, lambda: registry.list_versions(users["auditor"]), repeat),
                "outcome list (200 rows)": measure(
                    engine, lambda: data["outcomes"].list_outcomes(users["auditor"], limit=200), repeat),
                "eligibility preview (all outcomes)": measure(
                    engine, lambda: governance.eligibility_preview(users["auditor"], scope="controlled_demo"), repeat),
            }
        report = {
            "dataset": data["counts"],
            "build_seconds": build_seconds,
            "repeat": repeat,
            "postgres": engine.connect().execute(text("SHOW server_version")).scalar_one(),
            "results": results,
        }
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--admin-url", required=True)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--repeat", type=int, default=5)
    args = parser.parse_args()
    report = run(args.admin_url, args.out, args.repeat)
    print(json.dumps(report, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
