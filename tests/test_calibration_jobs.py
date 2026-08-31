from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from threading import Event, Thread

import pytest
from sqlalchemy import func, select, text

from app.identity import AuthenticatedUser
from app.models import FinancingRequestModel, LedgerEventModel
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
from app.schemas_outcome import ActualOutcomeCreate


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


def test_expired_third_attempt_is_atomically_terminalized(session_factory):
    _seed_jobs(session_factory, controlled_count=1)
    repository = OutcomeRepository()

    for attempt, worker_id in enumerate(("worker-a", "worker-b", "worker-c"), 1):
        claim_time = NOW + timedelta(minutes=attempt * 6)
        with session_factory.begin() as session:
            claimed = repository.claim_next_job(
                session,
                worker_id=worker_id,
                now=claim_time,
                lease_until=claim_time + timedelta(minutes=5),
            )
            assert claimed is not None
            assert claimed.attempt_count == attempt

    with session_factory.begin() as session:
        assert repository.claim_next_job(
            session,
            worker_id="worker-d",
            now=NOW + timedelta(minutes=24),
            lease_until=NOW + timedelta(minutes=29),
        ) is None

    job = _jobs(session_factory)[0]
    assert job.status == "failed"
    assert job.failure_code == "calibration_lease_exhausted"
    assert job.attempt_count == 3
    assert job.lease_owner is None and job.leased_until is None


def test_stale_worker_cannot_activate_after_lease_is_reclaimed(
    session_factory,
    tmp_path,
):
    _seed_jobs(session_factory, controlled_count=20)
    repository = OutcomeRepository()

    def publish_after_reclaim(staged):
        artifact = publish_candidate_artifact(staged)
        with session_factory.begin() as session:
            replacement = repository.claim_next_job(
                session,
                worker_id="worker-b",
                now=NOW + timedelta(minutes=6),
                lease_until=NOW + timedelta(minutes=11),
            )
            assert replacement is not None
            assert replacement.attempt_count == 2
        return artifact

    outcome_service = OutcomeService(
        session_factory, artifact_root=tmp_path, clock=lambda: NOW
    )
    worker = CalibrationJobService(
        session_factory,
        outcome_service=outcome_service,
        artifact_publisher=publish_after_reclaim,
        clock=lambda: NOW,
    )

    assert worker.process_next("worker-a") is True

    job = _jobs(session_factory)[0]
    with session_factory() as session:
        run = session.scalar(select(CalibrationRunModel))
    assert (job.status, job.lease_owner, job.attempt_count) == (
        "running",
        "worker-b",
        2,
    )
    assert run.deployment_status == "not_deployed"


def test_activation_rejects_when_exact_eligible_snapshot_changes_during_publish(
    session_factory,
    tmp_path,
):
    controlled_ids, _, auditor = _seed_jobs(session_factory, controlled_count=21)
    excluded_id = controlled_ids[0]
    with session_factory.begin() as session:
        session.add(
            OutcomeCorrectionModel(
                correction_id=uuid.uuid4(),
                outcome_id=excluded_id,
                action="EXCLUDE",
                reason_code="BAD_EVIDENCE",
                comment="exclude before training",
                evidence_sha256="a" * 64,
                auditor_user_id=auditor.user_id,
                idempotency_key=uuid.uuid4(),
                request_sha256="b" * 64,
                recorded_at=NOW + timedelta(minutes=1),
            )
        )

    def publish_then_reinstate(staged):
        artifact = publish_candidate_artifact(staged)
        with session_factory.begin() as session:
            session.add(
                OutcomeCorrectionModel(
                    correction_id=uuid.uuid4(),
                    outcome_id=excluded_id,
                    action="REINSTATE",
                    reason_code="EVIDENCE_VERIFIED",
                    comment="eligible set changed during publication",
                    evidence_sha256="c" * 64,
                    auditor_user_id=auditor.user_id,
                    idempotency_key=uuid.uuid4(),
                    request_sha256="d" * 64,
                    recorded_at=NOW + timedelta(minutes=2),
                )
            )
        return artifact

    outcome_service = OutcomeService(
        session_factory,
        artifact_root=tmp_path,
        clock=lambda: NOW + timedelta(minutes=3),
    )
    worker = CalibrationJobService(
        session_factory,
        outcome_service=outcome_service,
        artifact_publisher=publish_then_reinstate,
        clock=lambda: NOW + timedelta(minutes=3),
    )

    assert worker.process_next("worker-a") is True

    job = _jobs(session_factory)[0]
    with session_factory() as session:
        run = session.get(CalibrationRunModel, job.result_run_id)
    assert job.status == "completed"
    assert run.deployment_status == "rejected"
    assert run.activation_reason == "dataset_snapshot_changed"


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


def test_reconciler_never_activates_candidate_linked_to_failed_job(
    session_factory,
    tmp_path,
):
    _seed_jobs(session_factory, controlled_count=20)

    def publish_then_crash(staged):
        publish_candidate_artifact(staged)
        raise OSError("worker crashed after publication")

    outcome_service = OutcomeService(
        session_factory, artifact_root=tmp_path, clock=lambda: NOW
    )
    worker = CalibrationJobService(
        session_factory,
        outcome_service=outcome_service,
        artifact_publisher=publish_then_crash,
        clock=lambda: NOW,
    )
    assert worker.process_next("worker-a") is True
    job = _jobs(session_factory)[0]

    with session_factory.begin() as session:
        stored = session.get(CalibrationJobModel, job.job_id)
        stored.status = "failed"
        stored.lease_owner = None
        stored.leased_until = None
        stored.failure_code = "calibration_infrastructure_failure"
        stored.started_at = NOW
        stored.completed_at = NOW + timedelta(minutes=1)
        run_id = session.scalar(
            select(CalibrationRunModel.calibration_run_id).where(
                CalibrationRunModel.trigger_job_id == job.job_id
            )
        )

    outcome_service.reconcile_deployments()

    with session_factory() as session:
        run = session.get(CalibrationRunModel, run_id)
    assert run.deployment_status == "activation_failed"
    assert run.activation_reason == "job_failed"


def test_failed_dataset_is_not_reused_by_later_job(session_factory, tmp_path):
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
    assert worker.process_next("worker-b") is True

    jobs = _jobs(session_factory)[:2]
    with session_factory() as session:
        runs = list(
            session.scalars(
                select(CalibrationRunModel).order_by(
                    CalibrationRunModel.calibration_run_id
                )
            )
        )
    assert len(runs) == 2
    assert jobs[0].result_run_id != jobs[1].result_run_id
    assert {run.status for run in runs} == {"failed"}


def test_invalidated_dataset_is_retrained_as_a_distinct_safe_run(
    session_factory,
    tmp_path,
):
    _seed_jobs(session_factory, controlled_count=20)
    outcome_service = OutcomeService(
        session_factory, artifact_root=tmp_path, clock=lambda: NOW
    )
    worker = CalibrationJobService(
        session_factory, outcome_service=outcome_service, clock=lambda: NOW
    )

    assert worker.process_next("worker-a") is True
    first_job = _jobs(session_factory)[0]
    with session_factory.begin() as session:
        first = session.get(CalibrationRunModel, first_job.result_run_id)
        assert first.deployment_status == "active"
        first.deployment_status = "invalidated"
        first.deactivated_at = NOW + timedelta(minutes=1)
        first.activation_reason = "outcome_excluded"

    assert worker.process_next("worker-b") is True

    second_job = _jobs(session_factory)[1]
    with session_factory() as session:
        first = session.get(CalibrationRunModel, first_job.result_run_id)
        second = session.get(CalibrationRunModel, second_job.result_run_id)
        run_count = session.scalar(select(func.count()).select_from(CalibrationRunModel))
    assert run_count == 2
    assert second.calibration_run_id != first.calibration_run_id
    assert second.dataset_sha256 == first.dataset_sha256
    assert second.deployment_status == "active"


def test_terminal_publication_failure_settles_job_and_run_together(
    session_factory,
    tmp_path,
):
    _seed_jobs(session_factory, controlled_count=20)

    def unavailable(_staged):
        raise OSError("artifact store unavailable")

    outcome_service = OutcomeService(
        session_factory, artifact_root=tmp_path, clock=lambda: NOW
    )
    worker = CalibrationJobService(
        session_factory,
        outcome_service=outcome_service,
        artifact_publisher=unavailable,
        clock=lambda: NOW,
    )

    assert worker.process_next("worker-a") is True
    assert worker.process_next("worker-b") is True
    assert worker.process_next("worker-c") is True

    job = _jobs(session_factory)[0]
    with session_factory() as session:
        run = session.scalar(
            select(CalibrationRunModel).where(
                CalibrationRunModel.trigger_job_id == job.job_id
            )
        )
        failure_event = session.scalar(
            select(LedgerEventModel)
            .where(LedgerEventModel.event_type == "CALIBRATION_CANDIDATE_FAILED")
            .order_by(LedgerEventModel.id.desc())
        )
    assert (job.status, job.failure_code, job.attempt_count) == (
        "failed",
        "calibration_infrastructure_failure",
        3,
    )
    assert (run.status, run.deployment_status, run.failure_code) == (
        "failed",
        "activation_failed",
        "artifact_write_failed",
    )
    assert failure_event.entity_id == run.calibration_run_id


def test_expired_third_attempt_makes_its_prepared_run_non_deployable(
    session_factory,
    tmp_path,
):
    _seed_jobs(session_factory, controlled_count=20)

    def unavailable(_staged):
        raise OSError("artifact store unavailable")

    outcome_service = OutcomeService(
        session_factory, artifact_root=tmp_path, clock=lambda: NOW
    )
    worker = CalibrationJobService(
        session_factory,
        outcome_service=outcome_service,
        artifact_publisher=unavailable,
        clock=lambda: NOW,
    )
    assert worker.process_next("worker-a") is True
    assert worker.process_next("worker-b") is True

    abandoned = worker._claim("worker-c")
    assert abandoned is not None
    prepared = worker._prepare_run(abandoned)
    assert prepared.staged is not None

    repository = OutcomeRepository()
    with session_factory.begin() as session:
        assert repository.claim_next_job(
            session,
            worker_id="worker-d",
            now=NOW + timedelta(minutes=6),
            lease_until=NOW + timedelta(minutes=11),
        ) is not None  # the next queued job remains processable

    job = _jobs(session_factory)[0]
    with session_factory() as session:
        run = session.get(CalibrationRunModel, prepared.run_id)
    assert (job.status, job.failure_code, job.attempt_count) == (
        "failed",
        "calibration_lease_exhausted",
        3,
    )
    assert (run.deployment_status, run.activation_reason) == (
        "activation_failed",
        "job_failed",
    )


def test_activation_rechecks_fresh_time_after_waiting_for_scope_lock(
    session_factory,
    tmp_path,
):
    _seed_jobs(session_factory, controlled_count=20)
    current_time = [NOW + timedelta(minutes=4)]
    outcome_service = OutcomeService(
        session_factory,
        artifact_root=tmp_path,
        clock=lambda: current_time[0],
    )
    worker = CalibrationJobService(
        session_factory,
        outcome_service=outcome_service,
        clock=lambda: NOW,
    )
    claim = worker._claim("worker-a")
    assert claim is not None
    prepared = worker._prepare_run(claim)
    worker._renew(claim)
    publish_candidate_artifact(prepared.staged)
    candidate = prepared.candidate or worker._reload_candidate(prepared.run_id)
    provenances = worker._run_provenances(prepared.run_id)

    blocker = session_factory()
    blocker.begin()
    worker.repository.acquire_scope_lock(blocker, scope=claim.deployment_scope)
    started = Event()
    errors: list[Exception] = []

    def activate() -> None:
        started.set()
        try:
            outcome_service._complete_claimed_deployment(
                job_id=claim.job_id,
                worker_id=claim.worker_id,
                run_id=prepared.run_id,
                candidate=candidate,
                provenances=provenances,
                now=current_time[0],
            )
        except Exception as error:
            errors.append(error)

    thread = Thread(target=activate)
    thread.start()
    assert started.wait(timeout=2)
    assert thread.is_alive()
    current_time[0] = NOW + timedelta(minutes=6)
    blocker.commit()
    thread.join(timeout=3)
    blocker.close()

    assert not thread.is_alive()
    assert errors and "lease expired" in str(errors[0])
    job = _jobs(session_factory)[0]
    with session_factory() as session:
        run = session.get(CalibrationRunModel, prepared.run_id)
    assert job.status == "running"
    assert run.deployment_status == "not_deployed"


def test_prepare_obeys_scope_then_job_lock_order_without_deadlock(
    session_factory,
    tmp_path,
):
    _seed_jobs(session_factory, controlled_count=20)
    entered_scope_lock = Event()

    class SignallingRepository(OutcomeRepository):
        def acquire_scope_lock(self, session, *, scope):
            entered_scope_lock.set()
            return super().acquire_scope_lock(session, scope=scope)

    repository = SignallingRepository()
    outcome_service = OutcomeService(
        session_factory,
        artifact_root=tmp_path,
        repository=repository,
        clock=lambda: NOW,
    )
    worker = CalibrationJobService(
        session_factory,
        outcome_service=outcome_service,
        repository=repository,
        clock=lambda: NOW,
    )
    claim = worker._claim("worker-a")
    assert claim is not None

    blocker = session_factory()
    blocker.begin()
    repository.acquire_scope_lock(blocker, scope=claim.deployment_scope)
    entered_scope_lock.clear()
    errors: list[Exception] = []

    def prepare() -> None:
        try:
            worker._prepare_run(claim)
        except Exception as error:
            errors.append(error)

    thread = Thread(target=prepare)
    thread.start()
    assert entered_scope_lock.wait(timeout=2)
    blocker.execute(text("SET LOCAL lock_timeout = '400ms'"))
    locked_job = repository.get_job_for_update(blocker, claim.job_id)
    assert locked_job is not None
    blocker.commit()
    thread.join(timeout=5)
    blocker.close()

    assert not thread.is_alive()
    assert errors == []


def test_outcome_submit_cannot_enter_after_snapshot_compare_before_activation_commit(
    session_factory,
    tmp_path,
):
    controlled_ids, _, auditor = _seed_jobs(
        session_factory,
        controlled_count=21,
    )
    spare_outcome_id = controlled_ids[-1]
    with session_factory.begin() as session:
        spare = session.get(ActualOutcomeModel, spare_outcome_id)
        spare_facility_id = spare.facility_id
        application = session.get(FinancingRequestModel, spare.request_id)
        application.risk_engine_version = "transparent_logistic_baseline_v0.1"
        session.execute(
            text("DELETE FROM calibration_jobs WHERE trigger_outcome_id = :outcome_id"),
            {"outcome_id": spare_outcome_id},
        )
        session.execute(text("ALTER TABLE actual_outcomes DISABLE TRIGGER ALL"))
        session.execute(
            text("DELETE FROM actual_outcomes WHERE outcome_id = :outcome_id"),
            {"outcome_id": spare_outcome_id},
        )
        session.execute(text("ALTER TABLE actual_outcomes ENABLE TRIGGER ALL"))

    snapshot_checked = Event()
    allow_activation = Event()

    class BlockingSnapshotRepository(OutcomeRepository):
        def run_membership_matches_eligible_snapshot(
            self, session, run_id, *, scope
        ):
            result = super().run_membership_matches_eligible_snapshot(
                session,
                run_id,
                scope=scope,
            )
            snapshot_checked.set()
            assert allow_activation.wait(timeout=5)
            return result

    repository = BlockingSnapshotRepository()
    outcome_service = OutcomeService(
        session_factory,
        artifact_root=tmp_path,
        repository=repository,
        clock=lambda: NOW + timedelta(minutes=1),
    )
    worker = CalibrationJobService(
        session_factory,
        outcome_service=outcome_service,
        repository=repository,
        clock=lambda: NOW + timedelta(minutes=1),
    )
    claim = worker._claim("worker-a")
    assert claim is not None
    prepared = worker._prepare_run(claim)
    worker._renew(claim)
    publish_candidate_artifact(prepared.staged)
    candidate = prepared.candidate or worker._reload_candidate(prepared.run_id)
    provenances = worker._run_provenances(prepared.run_id)

    activation_errors: list[Exception] = []

    def activate() -> None:
        try:
            outcome_service._complete_claimed_deployment(
                job_id=claim.job_id,
                worker_id=claim.worker_id,
                run_id=prepared.run_id,
                candidate=candidate,
                provenances=provenances,
                now=NOW + timedelta(minutes=1),
            )
        except Exception as error:
            activation_errors.append(error)

    activation = Thread(target=activate)
    activation.start()
    assert snapshot_checked.wait(timeout=3)

    submit_done = Event()
    submit_errors: list[Exception] = []

    def submit() -> None:
        try:
            outcome_service.submit(
                spare_facility_id,
                ActualOutcomeCreate(
                    idempotency_key=uuid.uuid4(),
                    observed_at=NOW + timedelta(minutes=10),
                    evidence_sha256="9" * 64,
                    provenance="CONTROLLED_DEMO",
                ),
                auditor,
            )
        except Exception as error:
            submit_errors.append(error)
        finally:
            submit_done.set()

    submission = Thread(target=submit)
    submission.start()
    assert not submit_done.wait(timeout=0.4), submit_errors
    allow_activation.set()
    activation.join(timeout=5)
    submission.join(timeout=5)

    assert activation_errors == []
    assert submit_errors == []
    assert submit_done.is_set()


def test_missing_artifact_read_does_not_bypass_worker_retry_contract(
    session_factory,
    tmp_path,
):
    controlled_ids, _, auditor = _seed_jobs(session_factory, controlled_count=20)
    correction_id = uuid.uuid4()
    jobs = _jobs(session_factory)
    with session_factory.begin() as session:
        session.add(
            OutcomeCorrectionModel(
                correction_id=correction_id,
                outcome_id=controlled_ids[0],
                action="REINSTATE",
                reason_code="EVIDENCE_VERIFIED",
                comment="correction-triggered calibration",
                evidence_sha256="7" * 64,
                auditor_user_id=auditor.user_id,
                idempotency_key=uuid.uuid4(),
                request_sha256="8" * 64,
                recorded_at=NOW + timedelta(minutes=1),
            )
        )
        job = session.get(CalibrationJobModel, jobs[0].job_id)
        job.trigger_type = "correction_reinstate"
        job.trigger_outcome_id = None
        job.trigger_correction_id = correction_id

    def unavailable(_staged):
        raise OSError("artifact store unavailable")

    outcome_service = OutcomeService(
        session_factory, artifact_root=tmp_path, clock=lambda: NOW
    )
    worker = CalibrationJobService(
        session_factory,
        outcome_service=outcome_service,
        artifact_publisher=unavailable,
        clock=lambda: NOW,
    )
    assert worker.process_next("worker-a") is True
    job = _jobs(session_factory)[0]
    with session_factory() as session:
        run = session.scalar(
            select(CalibrationRunModel).where(
                CalibrationRunModel.trigger_job_id == job.job_id
            )
        )
        run_id = run.calibration_run_id
        pending = tmp_path / f".pending-{run.artifact_sha256}.json"
    pending.unlink()

    observed = outcome_service.get_run(run_id, auditor)
    assert observed["status"] == "eligible_candidate"
    assert observed["artifact_integrity"] in {"missing", "unavailable"}
    assert _jobs(session_factory)[0].status == "queued"

    assert worker.process_next("worker-b") is True
    assert _jobs(session_factory)[0].status == "queued"
    assert worker.process_next("worker-c") is True
    terminal = _jobs(session_factory)[0]
    with session_factory() as session:
        run = session.get(CalibrationRunModel, run_id)
    assert terminal.status == "failed"
    assert terminal.attempt_count == 3
    assert run.status == "failed"
    assert terminal.result_run_id is None


def test_database_allows_distinct_runs_for_same_dataset_hash(migrated_engine):
    with migrated_engine.connect() as connection:
        unique_constraints = set(
            connection.scalars(
                text(
                    "SELECT conname FROM pg_constraint "
                    "WHERE conrelid = 'calibration_runs'::regclass "
                    "AND contype = 'u'"
                )
            )
        )
    assert "calibration_runs_dataset_sha256_key" not in unique_constraints


def test_correction_triggered_missing_artifact_read_is_safe_and_side_effect_free(
    session_factory,
    tmp_path,
):
    controlled_ids, _, auditor = _seed_jobs(session_factory, controlled_count=20)
    correction_id = uuid.uuid4()
    jobs = _jobs(session_factory)
    with session_factory.begin() as session:
        session.add(
            OutcomeCorrectionModel(
                correction_id=correction_id,
                outcome_id=controlled_ids[0],
                action="REINSTATE",
                reason_code="EVIDENCE_VERIFIED",
                comment="correction-triggered calibration",
                evidence_sha256="e" * 64,
                auditor_user_id=auditor.user_id,
                idempotency_key=uuid.uuid4(),
                request_sha256="f" * 64,
                recorded_at=NOW + timedelta(minutes=1),
            )
        )
        job = session.get(CalibrationJobModel, jobs[0].job_id)
        job.trigger_type = "correction_reinstate"
        job.trigger_outcome_id = None
        job.trigger_correction_id = correction_id

    def unavailable(staged):
        raise OSError("artifact store unavailable")

    outcome_service = OutcomeService(
        session_factory, artifact_root=tmp_path, clock=lambda: NOW
    )
    worker = CalibrationJobService(
        session_factory,
        outcome_service=outcome_service,
        artifact_publisher=unavailable,
        clock=lambda: NOW,
    )
    assert worker.process_next("worker-a") is True
    with session_factory() as session:
        run = session.scalar(
            select(CalibrationRunModel).where(
                CalibrationRunModel.trigger_job_id == jobs[0].job_id
            )
        )
        run_id = run.calibration_run_id
        artifact_sha256 = run.artifact_sha256
    pending = tmp_path / f".pending-{artifact_sha256}.json"
    pending.unlink()

    result = outcome_service.get_run(run_id, auditor)

    assert result["status"] == "eligible_candidate"
    assert result["failure_code"] is None
    assert result["artifact_integrity"] in {"missing", "unavailable"}
    with session_factory() as session:
        event = session.scalar(
            select(LedgerEventModel)
            .where(LedgerEventModel.event_type == "CALIBRATION_CANDIDATE_FAILED")
            .order_by(LedgerEventModel.id.desc())
        )
    assert event is None


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
