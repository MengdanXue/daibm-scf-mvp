from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from threading import Event, Thread

import pytest
from sqlalchemy import func, select

from app.identity import AuthenticatedUser
from app.models import FinancingRequestModel
from app.models_facility import FinancingFacilityModel
from app.models_governance import (
    CalibrationJobModel,
    CalibrationRunObservationModel,
    OutcomeCorrectionModel,
)
from app.models_outcome import ActualOutcomeModel, CalibrationRunModel
from app.repositories.outcomes import OutcomeRepository
from app.services.calibration_jobs import CalibrationJobService
from app.services.identity import IdentityService
from app.services.outcome_calibration import publish_candidate_artifact
from app.services.outcomes import OutcomeService


NOW = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)


def _seed_jobs(
    session_factory,
    *,
    controlled_count: int,
    external_count: int = 0,
    identical_scores: bool = False,
) -> tuple[list[uuid.UUID], list[uuid.UUID], AuthenticatedUser]:
    identity = IdentityService(session_factory, clock=lambda: NOW)
    identity.seed_demo_accounts()
    auditor = identity.login("auditor.demo", "Demo123!").user
    financier = identity.login("financier.demo", "Demo123!").user
    controlled_ids: list[uuid.UUID] = []
    external_ids: list[uuid.UUID] = []
    rows: list[object] = []
    jobs: list[CalibrationJobModel] = []
    sequence = 0
    for scope, count, target in (
        ("controlled_demo", controlled_count, controlled_ids),
        ("external_verified", external_count, external_ids),
    ):
        for local_index in range(count):
            sequence += 1
            request_id = uuid.uuid4()
            facility_id = uuid.uuid4()
            outcome_id = uuid.uuid4()
            assessment_id = uuid.uuid4()
            target.append(outcome_id)
            score = 0.5 if identical_scores else 0.03 + 0.94 * local_index / max(count - 1, 1)
            defaulted = local_index >= count // 2
            recorded_at = NOW + timedelta(microseconds=sequence)
            rows.extend(
                [
                    FinancingRequestModel(
                        request_id=request_id,
                        created_at=recorded_at,
                        updated_at=recorded_at,
                        applicant_id=f"JOB-{scope}-{local_index:03d}",
                        assessment_scope=scope,
                        amount=Decimal("1000.00"),
                        term_days=30,
                        features={},
                        risk_score=score,
                        raw_risk_score=score,
                        calibration_run_id=None,
                        calibration_fallback_code=None,
                        decision="approved",
                        status="audited",
                        version=1,
                        risk_assessment_id=assessment_id,
                        risk_engine_version="job-test@v1",
                        risk_input_sha256=f"{sequence + 100:064x}",
                        risk_assessed_at=recorded_at,
                    ),
                    FinancingFacilityModel(
                        facility_id=facility_id,
                        request_id=request_id,
                        principal=Decimal("1000.00"),
                        outstanding_amount=Decimal("0.00"),
                        currency="CNY",
                        status="closed",
                        version=1,
                        current_schedule_version=1,
                        closure_reason="repaid",
                        created_by_user_id=financier.user_id,
                        created_at=recorded_at,
                        updated_at=recorded_at,
                        closed_at=recorded_at,
                    ),
                    ActualOutcomeModel(
                        outcome_id=outcome_id,
                        facility_id=facility_id,
                        request_id=request_id,
                        risk_assessment_id=assessment_id,
                        model_version_id=None,
                        submitted_by_user_id=auditor.user_id,
                        idempotency_key=uuid.uuid4(),
                        request_sha256=f"{sequence + 200:064x}",
                        defaulted=defaulted,
                        days_past_due=30 if defaulted else 0,
                        loss_amount=(
                            Decimal("100.00") if defaulted else Decimal("0.00")
                        ),
                        observed_at=recorded_at,
                        evidence_sha256=f"{sequence + 300:064x}",
                        provenance=(
                            "CONTROLLED_DEMO"
                            if scope == "controlled_demo"
                            else "EXTERNAL_VERIFIED"
                        ),
                        original_risk_score=score,
                        risk_engine_version="job-test@v1",
                        risk_input_sha256=f"{sequence + 100:064x}",
                        recorded_at=recorded_at,
                    ),
                ]
            )
            jobs.append(
                CalibrationJobModel(
                    job_id=uuid.uuid4(),
                    deployment_scope=scope,
                    trigger_type="outcome_submitted",
                    trigger_outcome_id=outcome_id,
                    trigger_correction_id=None,
                    idempotency_key=uuid.uuid4(),
                    status="queued",
                    attempt_count=0,
                    lease_owner=None,
                    leased_until=None,
                    failure_code=None,
                    result_run_id=None,
                    created_at=recorded_at,
                    started_at=None,
                    completed_at=None,
                )
            )
    with session_factory.begin() as session:
        session.add_all(rows)
        session.flush()
        session.add_all(jobs)
    return controlled_ids, external_ids, auditor


def _jobs(session_factory, scope: str = "controlled_demo") -> list[CalibrationJobModel]:
    with session_factory() as session:
        return list(
            session.scalars(
                select(CalibrationJobModel)
                .where(CalibrationJobModel.deployment_scope == scope)
                .order_by(CalibrationJobModel.created_at, CalibrationJobModel.job_id)
            )
        )


def test_real_postgresql_workers_skip_locked_and_claim_distinct_jobs(session_factory):
    _seed_jobs(session_factory, controlled_count=2)
    repository = OutcomeRepository()
    first = session_factory()
    second = session_factory()
    try:
        first.begin()
        first_job = repository.claim_next_job(
            first,
            worker_id="worker-a",
            now=NOW,
            lease_until=NOW + timedelta(minutes=5),
        )
        first.flush()

        second.begin()
        second_job = repository.claim_next_job(
            second,
            worker_id="worker-b",
            now=NOW,
            lease_until=NOW + timedelta(minutes=5),
        )

        assert first_job is not None and second_job is not None
        assert first_job.job_id != second_job.job_id
        assert first_job.attempt_count == second_job.attempt_count == 1
    finally:
        first.rollback()
        second.rollback()
        first.close()
        second.close()


def test_claim_recovers_only_expired_lease_and_stops_at_three_attempts(session_factory):
    _seed_jobs(session_factory, controlled_count=1)
    repository = OutcomeRepository()

    with session_factory.begin() as session:
        claimed = repository.claim_next_job(
            session,
            worker_id="worker-a",
            now=NOW,
            lease_until=NOW + timedelta(minutes=5),
        )
        assert claimed is not None

    with session_factory.begin() as session:
        assert repository.claim_next_job(
            session,
            worker_id="worker-b",
            now=NOW + timedelta(minutes=4),
            lease_until=NOW + timedelta(minutes=9),
        ) is None

    with session_factory.begin() as session:
        reclaimed = repository.claim_next_job(
            session,
            worker_id="worker-b",
            now=NOW + timedelta(minutes=6),
            lease_until=NOW + timedelta(minutes=11),
        )
        assert reclaimed is not None
        assert reclaimed.attempt_count == 2
        repository.fail_or_retry_job(
            session,
            job_id=reclaimed.job_id,
            worker_id="worker-b",
            now=NOW + timedelta(minutes=7),
            failure_code="calibration_infrastructure_failure",
        )

    with session_factory.begin() as session:
        third = repository.claim_next_job(
            session,
            worker_id="worker-c",
            now=NOW + timedelta(minutes=8),
            lease_until=NOW + timedelta(minutes=13),
        )
        assert third is not None and third.attempt_count == 3
        terminal = repository.fail_or_retry_job(
            session,
            job_id=third.job_id,
            worker_id="worker-c",
            now=NOW + timedelta(minutes=9),
            failure_code="calibration_infrastructure_failure",
        )
        assert terminal.status == "failed"

    job = _jobs(session_factory)[0]
    assert (job.status, job.attempt_count, job.failure_code) == (
        "failed",
        3,
        "calibration_infrastructure_failure",
    )


def test_scope_advisory_lock_serializes_same_scope_but_not_other_scope(session_factory):
    repository = OutcomeRepository()
    first = session_factory()
    same = session_factory()
    other = session_factory()
    same_acquired = Event()
    other_acquired = Event()
    try:
        first.begin()
        repository.acquire_scope_lock(first, scope="controlled_demo")

        def acquire(session, scope: str, signal: Event) -> None:
            with session.begin():
                repository.acquire_scope_lock(session, scope=scope)
                signal.set()

        same_thread = Thread(
            target=acquire,
            args=(same, "controlled_demo", same_acquired),
        )
        other_thread = Thread(
            target=acquire,
            args=(other, "external_verified", other_acquired),
        )
        same_thread.start()
        other_thread.start()
        assert other_acquired.wait(timeout=2)
        assert not same_acquired.wait(timeout=0.2)

        first.commit()
        same_thread.join(timeout=2)
        other_thread.join(timeout=2)
        assert same_acquired.is_set()
        assert not same_thread.is_alive() and not other_thread.is_alive()
    finally:
        if first.in_transaction():
            first.rollback()
        first.close()
        same.close()
        other.close()


def test_process_next_uses_exact_scope_membership_heads_and_reuses_dataset(
    session_factory,
    tmp_path,
):
    controlled_ids, external_ids, auditor = _seed_jobs(
        session_factory,
        controlled_count=22,
        external_count=1,
    )
    excluded_id = controlled_ids[0]
    reinstated_id = controlled_ids[1]
    exclude_id = uuid.uuid4()
    reinstate_id = uuid.uuid4()
    with session_factory.begin() as session:
        session.add_all(
            [
                OutcomeCorrectionModel(
                    correction_id=exclude_id,
                    outcome_id=excluded_id,
                    action="EXCLUDE",
                    reason_code="BAD_EVIDENCE",
                    comment="Excluded",
                    evidence_sha256="d" * 64,
                    auditor_user_id=auditor.user_id,
                    idempotency_key=uuid.uuid4(),
                    request_sha256="e" * 64,
                    recorded_at=NOW + timedelta(minutes=1),
                ),
                OutcomeCorrectionModel(
                    correction_id=uuid.uuid4(),
                    outcome_id=reinstated_id,
                    action="EXCLUDE",
                    reason_code="BAD_EVIDENCE",
                    comment="Excluded then corrected",
                    evidence_sha256="f" * 64,
                    auditor_user_id=auditor.user_id,
                    idempotency_key=uuid.uuid4(),
                    request_sha256="1" * 64,
                    recorded_at=NOW + timedelta(minutes=1),
                ),
                OutcomeCorrectionModel(
                    correction_id=reinstate_id,
                    outcome_id=reinstated_id,
                    action="REINSTATE",
                    reason_code="EVIDENCE_VERIFIED",
                    comment="Reinstated",
                    evidence_sha256="2" * 64,
                    auditor_user_id=auditor.user_id,
                    idempotency_key=uuid.uuid4(),
                    request_sha256="3" * 64,
                    recorded_at=NOW + timedelta(minutes=2),
                ),
            ]
        )

    outcome_service = OutcomeService(
        session_factory,
        artifact_root=tmp_path,
        clock=lambda: NOW + timedelta(minutes=3),
    )
    worker = CalibrationJobService(
        session_factory,
        outcome_service=outcome_service,
        clock=lambda: NOW + timedelta(minutes=3),
    )

    assert worker.process_next("worker-a") is True
    first_job = _jobs(session_factory)[0]
    assert first_job.status == "completed"
    assert first_job.result_run_id is not None
    with session_factory() as session:
        run = session.get(CalibrationRunModel, first_job.result_run_id)
        membership = list(
            session.scalars(
                select(CalibrationRunObservationModel)
                .where(
                    CalibrationRunObservationModel.calibration_run_id
                    == first_job.result_run_id
                )
                .order_by(CalibrationRunObservationModel.outcome_id)
            )
        )
        run_count = session.scalar(select(func.count()).select_from(CalibrationRunModel))
    member_ids = {row.outcome_id for row in membership}
    assert excluded_id not in member_ids
    assert external_ids[0] not in member_ids
    assert member_ids == set(controlled_ids) - {excluded_id}
    assert next(
        row for row in membership if row.outcome_id == reinstated_id
    ).correction_head_id == reinstate_id
    assert run_count == 1
    assert run.artifact_schema == "daibm.platt-calibration.v3"
    assert Path(run.artifact_locator).is_file()

    assert worker.process_next("worker-b") is True
    second_job = _jobs(session_factory)[1]
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(CalibrationRunModel)) == 1
    assert second_job.status == "completed"
    assert second_job.result_run_id == first_job.result_run_id

    safe = outcome_service.get_run(first_job.result_run_id, auditor)
    assert safe["artifact_schema"] == "daibm.platt-calibration.v3"
    assert safe["fold_assignment_sha256"]
    assert safe["eligible_count"] == 21
    assert safe["excluded_count"] == 1
    assert "artifact_locator" not in safe


def test_deterministic_gate_rejection_completes_without_retry(session_factory, tmp_path):
    _seed_jobs(session_factory, controlled_count=20, identical_scores=True)
    outcome_service = OutcomeService(
        session_factory, artifact_root=tmp_path, clock=lambda: NOW
    )
    worker = CalibrationJobService(
        session_factory, outcome_service=outcome_service, clock=lambda: NOW
    )

    assert worker.process_next("worker-a") is True

    job = _jobs(session_factory)[0]
    with session_factory() as session:
        run = session.get(CalibrationRunModel, job.result_run_id)
    assert (job.status, job.attempt_count, job.failure_code) == ("completed", 1, None)
    assert run.deployment_status == "rejected"
    assert run.activation_reason == "insufficient_distinct_scores"


def test_infrastructure_failure_retries_exactly_three_times_without_exception_text(
    session_factory,
    tmp_path,
):
    _seed_jobs(session_factory, controlled_count=20)

    def fail_training(*_args, **_kwargs):
        raise RuntimeError("C:\\secret\\candidate.json database password=unsafe")

    outcome_service = OutcomeService(
        session_factory,
        artifact_root=tmp_path,
        trainer=fail_training,
        clock=lambda: NOW,
    )
    worker = CalibrationJobService(
        session_factory, outcome_service=outcome_service, clock=lambda: NOW
    )

    assert worker.process_next("worker-a") is True
    assert (_jobs(session_factory)[0].status, _jobs(session_factory)[0].attempt_count) == (
        "queued",
        1,
    )
    assert worker.process_next("worker-a") is True
    assert (_jobs(session_factory)[0].status, _jobs(session_factory)[0].attempt_count) == (
        "queued",
        2,
    )
    assert worker.process_next("worker-a") is True

    job = _jobs(session_factory)[0]
    assert (job.status, job.attempt_count, job.failure_code) == (
        "failed",
        3,
        "calibration_infrastructure_failure",
    )
    assert "secret" not in str(job.failure_code)
    assert worker.process_next("worker-a") is True  # another queued job, not the failed one


def test_deterministic_training_rejection_records_run_and_completes_without_retry(
    session_factory,
    tmp_path,
):
    _seed_jobs(session_factory, controlled_count=4)

    def reject_data(*_args, **_kwargs):
        raise ValueError("not enough governed class support")

    outcome_service = OutcomeService(
        session_factory,
        artifact_root=tmp_path,
        trainer=reject_data,
        clock=lambda: NOW,
    )
    worker = CalibrationJobService(
        session_factory, outcome_service=outcome_service, clock=lambda: NOW
    )

    assert worker.process_next("worker-a") is True

    job = _jobs(session_factory)[0]
    with session_factory() as session:
        run = session.get(CalibrationRunModel, job.result_run_id)
        membership_count = session.scalar(
            select(func.count())
            .select_from(CalibrationRunObservationModel)
            .where(
                CalibrationRunObservationModel.calibration_run_id
                == job.result_run_id
            )
        )
    assert (job.status, job.attempt_count, job.failure_code) == ("completed", 1, None)
    assert (run.status, run.failure_code, membership_count) == (
        "failed",
        "calibration_data_rejected",
        4,
    )


def test_publication_failure_is_reconciled_after_restart_without_duplicate_run(
    session_factory,
    tmp_path,
):
    _seed_jobs(session_factory, controlled_count=20)
    publish_attempts = 0

    def unavailable_once(staged):
        nonlocal publish_attempts
        publish_attempts += 1
        if publish_attempts == 1:
            raise OSError("filesystem unavailable")
        return publish_candidate_artifact(staged)

    outcome_service = OutcomeService(
        session_factory, artifact_root=tmp_path, clock=lambda: NOW
    )
    first_worker = CalibrationJobService(
        session_factory,
        outcome_service=outcome_service,
        artifact_publisher=unavailable_once,
        clock=lambda: NOW,
    )

    assert first_worker.process_next("worker-a") is True
    first_job = _jobs(session_factory)[0]
    assert (first_job.status, first_job.attempt_count) == ("queued", 1)
    with session_factory() as session:
        run_id = session.scalar(select(CalibrationRunModel.calibration_run_id))
        assert session.scalar(select(func.count()).select_from(CalibrationRunModel)) == 1

    restarted = CalibrationJobService(
        session_factory,
        outcome_service=outcome_service,
        artifact_publisher=unavailable_once,
        clock=lambda: NOW,
    )
    assert restarted.process_next("worker-b") is True

    recovered = _jobs(session_factory)[0]
    with session_factory() as session:
        run = session.get(CalibrationRunModel, run_id)
        assert session.scalar(select(func.count()).select_from(CalibrationRunModel)) == 1
    assert (recovered.status, recovered.attempt_count, recovered.result_run_id) == (
        "completed",
        2,
        run_id,
    )
    assert Path(run.artifact_locator).is_file()


def test_database_rollback_discards_uncommitted_staged_artifact(
    session_factory,
    tmp_path,
):
    _seed_jobs(session_factory, controlled_count=20)

    class FailingLedger:
        def append_many(self, *_args, **_kwargs):
            raise RuntimeError("ledger transaction unavailable")

    outcome_service = OutcomeService(
        session_factory,
        artifact_root=tmp_path,
        ledger_repository=FailingLedger(),
        clock=lambda: NOW,
    )
    worker = CalibrationJobService(
        session_factory, outcome_service=outcome_service, clock=lambda: NOW
    )

    assert worker.process_next("worker-a") is True

    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(CalibrationRunModel)) == 0
    assert list(tmp_path.glob(".pending-*.json")) == []
    assert (_jobs(session_factory)[0].status, _jobs(session_factory)[0].attempt_count) == (
        "queued",
        1,
    )


def test_run_waits_interruptibly_only_when_idle():
    class ProbeWorker(CalibrationJobService):
        def __init__(self):
            self.calls = 0

        def process_next(self, worker_id: str) -> bool:
            assert worker_id
            self.calls += 1
            return False

    async def scenario() -> int:
        worker = ProbeWorker()
        worker.idle_interval = 60.0
        stop_event = asyncio.Event()
        task = asyncio.create_task(worker.run(stop_event))
        while worker.calls == 0:
            await asyncio.sleep(0)
        stop_event.set()
        await asyncio.wait_for(task, timeout=1)
        return worker.calls

    assert asyncio.run(scenario()) == 1


def test_run_survives_transient_claim_failure_and_remains_stoppable():
    class ProbeWorker(CalibrationJobService):
        def __init__(self):
            self.calls = 0
            self.idle_interval = 0.01

        def process_next(self, worker_id: str) -> bool:
            assert worker_id
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("database temporarily unavailable")
            return False

    async def scenario() -> int:
        worker = ProbeWorker()
        stop_event = asyncio.Event()
        task = asyncio.create_task(worker.run(stop_event))
        while worker.calls < 2 and not task.done():
            await asyncio.sleep(0.01)
        assert not task.done()
        stop_event.set()
        await asyncio.wait_for(task, timeout=1)
        return worker.calls

    assert asyncio.run(scenario()) >= 2
