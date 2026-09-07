from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from sqlalchemy.orm import Session, sessionmaker

from app.models_governance import CalibrationJobModel, CalibrationRunObservationModel
from app.models_outcome import CalibrationRunModel
from app.repositories.outcomes import OutcomeRepository
from app.services.outcome_calibration import (
    CalibrationCandidate,
    CalibrationObservation,
    CalibrationArtifact,
    TEMPORAL_POLICY,
    StagedCalibrationArtifact,
    discard_staged_artifact,
    publish_candidate_artifact,
    summarize_calibration_observations,
)
from app.services.outcomes import OutcomeService


ArtifactPublisher = Callable[[StagedCalibrationArtifact], CalibrationArtifact]
FailureSettlement = Literal["contended", "stale", "lost", "retry", "terminal"]


class DeterministicCalibrationRejection(Exception):
    """A stable data rejection that must not consume infrastructure retries."""


class CalibrationClaimDeferred(Exception):
    """A scope-contended/expired claim returned without consuming an attempt."""

    def __init__(self, reason: Literal["contended", "stale"]) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class ClaimedJob:
    job_id: uuid.UUID
    deployment_scope: str
    worker_id: str


@dataclass(frozen=True)
class PreparedRun:
    run_id: uuid.UUID
    candidate: CalibrationCandidate | None
    staged: StagedCalibrationArtifact | None
    owned_by_job: bool


class CalibrationJobService:
    """Process durable calibration jobs without coupling them to HTTP requests."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        outcome_service: OutcomeService,
        repository: OutcomeRepository | None = None,
        artifact_publisher: ArtifactPublisher = publish_candidate_artifact,
        clock: Callable[[], datetime] | None = None,
        lease_duration: timedelta = timedelta(minutes=5),
        idle_interval: float = 0.5,
    ) -> None:
        if lease_duration <= timedelta(0):
            raise ValueError("lease_duration must be positive")
        if idle_interval <= 0:
            raise ValueError("idle_interval must be positive")
        self.session_factory = session_factory
        self.outcome_service = outcome_service
        self.repository = repository or outcome_service.repository
        self.artifact_publisher = artifact_publisher
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.lease_duration = lease_duration
        self.idle_interval = idle_interval

    def process_next(self, worker_id: str) -> bool:
        excluded_job_ids: set[uuid.UUID] = set()
        while True:
            claim = self._claim(
                worker_id,
                exclude_job_ids=tuple(excluded_job_ids),
            )
            if claim is None:
                return False
            prepared: PreparedRun | None = None
            try:
                prepared = self._prepare_run(claim)
                if prepared.staged is None:
                    self._complete(claim, prepared.run_id)
                    return True
                self._renew(claim)
                self.artifact_publisher(prepared.staged)
                candidate = prepared.candidate or self._reload_candidate(
                    prepared.run_id
                )
                provenances = self._run_provenances(prepared.run_id)
                self.outcome_service._complete_claimed_deployment(
                    job_id=claim.job_id,
                    worker_id=claim.worker_id,
                    run_id=prepared.run_id,
                    candidate=candidate,
                    provenances=provenances,
                    now=self._now(),
                )
            except DeterministicCalibrationRejection:
                settlement = self._settle_failure(
                    claim,
                    prepared=None,
                    failure_code="calibration_data_rejected",
                    retryable=False,
                )
                if settlement == "contended":
                    excluded_job_ids.add(claim.job_id)
                    continue
                if settlement == "stale":
                    return False
                if settlement == "lost":
                    return True
            except CalibrationClaimDeferred as error:
                if error.reason == "contended":
                    excluded_job_ids.add(claim.job_id)
                    continue
                return False
            except Exception:
                settlement = self._settle_infrastructure_failure(
                    claim,
                    prepared,
                )
                if settlement == "contended":
                    excluded_job_ids.add(claim.job_id)
                    continue
                if settlement == "stale":
                    return False
                if settlement == "lost":
                    return True
                if (
                    settlement == "terminal"
                    and prepared is not None
                    and prepared.staged is not None
                    and prepared.owned_by_job
                ):
                    self.outcome_service._discard_failed_publication(
                        prepared.staged
                    )
            return True

    async def run(self, stop_event: asyncio.Event) -> None:
        worker_id = f"calibration-{uuid.uuid4()}"
        while not stop_event.is_set():
            try:
                processed = await asyncio.to_thread(self.process_next, worker_id)
            except Exception:
                # Claim/record failures can be transient (for example during a
                # PostgreSQL restart). The bounded idle wait keeps the worker
                # alive without spinning or abandoning an in-flight thread.
                processed = False
            if processed:
                continue
            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=self.idle_interval,
                )
            except TimeoutError:
                pass

    def _claim(
        self,
        worker_id: str,
        *,
        exclude_job_ids: tuple[uuid.UUID, ...] = (),
    ) -> ClaimedJob | None:
        now = self._now()
        with self.session_factory.begin() as session:
            job = self.repository.claim_next_job(
                session,
                worker_id=worker_id,
                now=now,
                lease_until=now + self.lease_duration,
                exclude_job_ids=exclude_job_ids,
            )
            if job is None:
                return None
            return ClaimedJob(
                job_id=job.job_id,
                deployment_scope=job.deployment_scope,
                worker_id=worker_id,
            )

    def _prepare_run(self, claim: ClaimedJob) -> PreparedRun:
        with self.session_factory.begin() as session:
            acquired = self.repository.try_acquire_scope_lock(
                session,
                scope=claim.deployment_scope,
            )
            if not acquired:
                job = self.repository.get_job_for_update(session, claim.job_id)
                if (
                    job is not None
                    and job.status == "running"
                    and job.lease_owner == claim.worker_id
                ):
                    self.repository.release_claim_without_attempt(
                        session,
                        job_id=claim.job_id,
                        worker_id=claim.worker_id,
                    )
                session.commit()
                raise CalibrationClaimDeferred("contended")
            job = self.repository.get_job_for_update(session, claim.job_id)
            self.repository._require_job_owner(job, claim.worker_id)
            assert job is not None  # Validated by _require_job_owner.
            if job.deployment_scope != claim.deployment_scope:
                raise RuntimeError("calibration job scope changed after claim")
            fenced_now = self._now()
            if job.leased_until is None or job.leased_until <= fenced_now:
                self.repository.release_claim_without_attempt(
                    session,
                    job_id=claim.job_id,
                    worker_id=claim.worker_id,
                )
                session.commit()
                raise CalibrationClaimDeferred("stale")
            self.repository.renew_job_lease(
                session,
                job_id=claim.job_id,
                worker_id=claim.worker_id,
                now=fenced_now,
                lease_until=fenced_now + self.lease_duration,
            )

            existing_for_job = self.repository.get_run_by_job(session, claim.job_id)
            if existing_for_job is not None:
                return self._prepared_existing(
                    existing_for_job,
                    job_id=claim.job_id,
                )

            rows = self.repository.list_eligible_outcomes_with_heads(
                session,
                scope=claim.deployment_scope,
            )
            observations = tuple(
                replace(
                    self.outcome_service._observation(outcome),
                    correction_head_id=(
                        str(correction_head_id) if correction_head_id is not None else None
                    ),
                )
                for outcome, correction_head_id in rows
            )
            try:
                candidate = self.outcome_service.trainer(
                    observations,
                    config=self.outcome_service.training_config,
                )
            except ValueError as error:
                return self._persist_data_rejection(
                    session,
                    job=job,
                    observations=observations,
                    scope=claim.deployment_scope,
                    rejection_reason=str(error)
                    if str(error).startswith("temporal_")
                    else "calibration_data_rejected",
                )
            if candidate.deployment_scope != claim.deployment_scope:
                raise DeterministicCalibrationRejection

            duplicate = self.repository.get_reusable_run_by_dataset(
                session,
                candidate.dataset_sha256,
                scope=claim.deployment_scope,
            )
            if duplicate is not None:
                return self._prepared_existing(duplicate, job_id=claim.job_id)

            staged = self.outcome_service.artifact_writer(
                self.outcome_service.artifact_root,
                candidate,
            )
            return self._persist_candidate(
                session,
                job=job,
                claim=claim,
                candidate=candidate,
                staged=staged,
            )

    def _persist_candidate(
        self,
        session: Session,
        *,
        job: CalibrationJobModel,
        claim: ClaimedJob,
        candidate: CalibrationCandidate,
        staged: StagedCalibrationArtifact,
    ) -> PreparedRun:
        try:
            now = self._now()
            run = self.repository.add_run(
                session,
                CalibrationRunModel(
                    calibration_run_id=uuid.uuid4(),
                    trigger_outcome_id=job.trigger_outcome_id,
                    trigger_job_id=job.job_id,
                    dataset_sha256=candidate.dataset_sha256,
                    sample_count=candidate.sample_count,
                    positive_count=candidate.positive_count,
                    negative_count=candidate.negative_count,
                    metrics_before=dict(candidate.metrics_before),
                    metrics_after=dict(candidate.metrics_after),
                    configuration=self.outcome_service._configuration(),
                    status=candidate.status,
                    artifact_locator=str(staged.path),
                    artifact_sha256=staged.sha256,
                    artifact_schema=candidate.artifact_schema,
                    failure_code=None,
                    deployment_status="not_deployed",
                    deployment_scope=claim.deployment_scope,
                    activation_mode=None,
                    activated_at=None,
                    deactivated_at=None,
                    previous_active_run_id=None,
                    activation_reason="not_evaluated",
                    started_at=now,
                    completed_at=now,
                ),
            )
            correction_heads = dict(candidate.correction_heads)
            self.repository.add_run_observations(
                session,
                [
                    CalibrationRunObservationModel(
                        calibration_run_id=run.calibration_run_id,
                        outcome_id=uuid.UUID(outcome_id),
                        correction_head_id=(
                            uuid.UUID(correction_heads[outcome_id])
                            if correction_heads[outcome_id] is not None
                            else None
                        ),
                    )
                    for outcome_id in candidate.outcome_ids
                ],
            )
            excluded_count = self.repository.count_excluded_outcomes(
                session,
                scope=claim.deployment_scope,
            )
            self.outcome_service.ledger_repository.append_many(
                session,
                run.calibration_run_id,
                [
                    (
                        "CALIBRATION_CANDIDATE_TRAINED",
                        {
                            "calibration_run_id": str(run.calibration_run_id),
                            "dataset_sha256": run.dataset_sha256,
                            "sample_count": run.sample_count,
                            "positive_count": run.positive_count,
                            "negative_count": run.negative_count,
                            "eligible_count": run.sample_count,
                            "excluded_count": excluded_count,
                            "fold_assignment_sha256": (candidate.fold_assignment_sha256),
                            "temporal_validation": candidate.artifact.get("validation"),
                            "artifact_schema": candidate.artifact_schema,
                            "artifact_sha256": run.artifact_sha256,
                            "deployment_scope": run.deployment_scope,
                            "status": run.status,
                        },
                    )
                ],
            )
            # Commit while the staged artifact is still guarded here. If the
            # database transaction cannot commit, the invisible pending file
            # is removed before the job is retried.
            session.commit()
            return PreparedRun(
                run_id=run.calibration_run_id,
                candidate=candidate,
                staged=staged,
                owned_by_job=True,
            )
        except Exception:
            discard_staged_artifact(staged)
            raise

    @staticmethod
    def _prepared_existing(
        run: CalibrationRunModel,
        *,
        job_id: uuid.UUID,
    ) -> PreparedRun:
        if run.status == "failed":
            if run.failure_code == "calibration_data_rejected":
                return PreparedRun(
                    run_id=run.calibration_run_id,
                    candidate=None,
                    staged=None,
                    owned_by_job=run.trigger_job_id == job_id,
                )
            raise RuntimeError("existing calibration run is failed")
        if run.artifact_locator is None or run.artifact_sha256 is None:
            raise RuntimeError("existing calibration dataset is not recoverable")
        path = Path(run.artifact_locator)
        pending_path = path.parent / f".pending-{run.artifact_sha256}.json"
        return PreparedRun(
            run_id=run.calibration_run_id,
            candidate=None,
            staged=StagedCalibrationArtifact(
                path=path,
                staged_path=pending_path if pending_path.is_file() else None,
                sha256=run.artifact_sha256,
            ),
            owned_by_job=run.trigger_job_id == job_id,
        )

    def _persist_data_rejection(
        self,
        session: Session,
        *,
        job: CalibrationJobModel,
        observations: tuple[CalibrationObservation, ...],
        scope: str,
        rejection_reason: str = "calibration_data_rejected",
    ) -> PreparedRun:
        if not observations:
            raise DeterministicCalibrationRejection
        summary = summarize_calibration_observations(
            observations,
            config=self.outcome_service.training_config,
        )
        now = self._now()
        run = self.repository.add_run(
            session,
            CalibrationRunModel(
                calibration_run_id=uuid.uuid4(),
                trigger_outcome_id=job.trigger_outcome_id,
                trigger_job_id=job.job_id,
                dataset_sha256=summary.dataset_sha256,
                sample_count=summary.sample_count,
                positive_count=summary.positive_count,
                negative_count=summary.negative_count,
                metrics_before=summary.metrics_before,
                metrics_after=None,
                configuration=self.outcome_service._configuration(),
                status="failed",
                artifact_locator=None,
                artifact_sha256=None,
                artifact_schema=None,
                failure_code="calibration_data_rejected",
                deployment_status="not_deployed",
                deployment_scope=scope,
                activation_mode=None,
                activated_at=None,
                deactivated_at=None,
                previous_active_run_id=None,
                activation_reason=rejection_reason,
                started_at=now,
                completed_at=now,
            ),
        )
        self.repository.add_run_observations(
            session,
            [
                CalibrationRunObservationModel(
                    calibration_run_id=run.calibration_run_id,
                    outcome_id=uuid.UUID(observation.outcome_id),
                    correction_head_id=(
                        uuid.UUID(observation.correction_head_id)
                        if observation.correction_head_id is not None
                        else None
                    ),
                )
                for observation in observations
            ],
        )
        excluded_count = self.repository.count_excluded_outcomes(
            session,
            scope=scope,
        )
        self.outcome_service.ledger_repository.append_many(
            session,
            run.calibration_run_id,
            [
                (
                    "CALIBRATION_CANDIDATE_FAILED",
                    {
                        "calibration_run_id": str(run.calibration_run_id),
                        "dataset_sha256": run.dataset_sha256,
                        "sample_count": run.sample_count,
                        "positive_count": run.positive_count,
                        "negative_count": run.negative_count,
                        "eligible_count": run.sample_count,
                        "excluded_count": excluded_count,
                        "failure_code": run.failure_code,
                        "activation_reason": run.activation_reason,
                        "validation_policy": dict(TEMPORAL_POLICY),
                        "deployment_scope": scope,
                        "status": run.status,
                    },
                )
            ],
        )
        return PreparedRun(
            run_id=run.calibration_run_id,
            candidate=None,
            staged=None,
            owned_by_job=True,
        )

    def _reload_candidate(self, run_id: uuid.UUID) -> CalibrationCandidate:
        with self.session_factory() as session:
            run = self.repository.get_run(session, run_id)
            if run is None:
                raise RuntimeError("committed calibration run is missing")
            memberships = tuple(
                self.repository.list_run_membership(session, run_id)
            )
            return self.outcome_service._candidate_from_run(run, memberships)

    def _run_provenances(self, run_id: uuid.UUID) -> tuple[str, ...]:
        with self.session_factory() as session:
            membership = self.repository.list_run_membership(session, run_id)
            outcomes = {
                row.outcome_id: self.repository.get_outcome(session, row.outcome_id)
                for row in membership
            }
            if any(outcome is None for outcome in outcomes.values()):
                raise RuntimeError("calibration membership outcome is missing")
            return tuple(
                outcomes[row.outcome_id].provenance  # type: ignore[union-attr]
                for row in membership
            )

    def _complete(self, claim: ClaimedJob, run_id: uuid.UUID) -> None:
        with self.session_factory.begin() as session:
            acquired = self.repository.try_acquire_scope_lock(
                session,
                scope=claim.deployment_scope,
            )
            if not acquired:
                job = self.repository.get_job_for_update(session, claim.job_id)
                if (
                    job is not None
                    and job.status == "running"
                    and job.lease_owner == claim.worker_id
                ):
                    self.repository.release_claim_without_attempt(
                        session,
                        job_id=claim.job_id,
                        worker_id=claim.worker_id,
                    )
                session.commit()
                raise CalibrationClaimDeferred("contended")
            job = self.repository.get_job_for_update(session, claim.job_id)
            self.repository._require_job_owner(job, claim.worker_id)
            assert job is not None  # Validated by _require_job_owner.
            fenced_now = self._now()
            if job.leased_until is None or job.leased_until <= fenced_now:
                self.repository.release_claim_without_attempt(
                    session,
                    job_id=claim.job_id,
                    worker_id=claim.worker_id,
                )
                session.commit()
                raise CalibrationClaimDeferred("stale")
            run = self.repository.get_run_for_update(session, run_id)
            if (
                run is None
                or run.trigger_job_id != claim.job_id
                or run.status != "failed"
                or run.failure_code != "calibration_data_rejected"
            ):
                raise RuntimeError(
                    "deterministic calibration result is not owned by the job"
                )
            self.repository.complete_job(
                session,
                job_id=claim.job_id,
                worker_id=claim.worker_id,
                result_run_id=run_id,
                now=fenced_now,
            )

    def _renew(self, claim: ClaimedJob) -> None:
        now = self._now()
        with self.session_factory.begin() as session:
            self.repository.renew_job_lease(
                session,
                job_id=claim.job_id,
                worker_id=claim.worker_id,
                now=now,
                lease_until=now + self.lease_duration,
            )

    def _settle_failure(
        self,
        claim: ClaimedJob,
        prepared: PreparedRun | None,
        failure_code: str,
        *,
        retryable: bool,
    ) -> FailureSettlement:
        with self.session_factory.begin() as session:
            acquired = self.repository.try_acquire_scope_lock(
                session,
                scope=claim.deployment_scope,
            )
            if not acquired:
                job = self.repository.get_job_for_update(session, claim.job_id)
                if (
                    job is not None
                    and job.status == "running"
                    and job.lease_owner == claim.worker_id
                ):
                    self.repository.release_claim_without_attempt(
                        session,
                        job_id=claim.job_id,
                        worker_id=claim.worker_id,
                    )
                return "contended"
            job = self.repository.get_job_for_update(session, claim.job_id)
            if (
                job is None
                or job.status != "running"
                or job.lease_owner != claim.worker_id
            ):
                return "lost"
            fenced_now = self._now()
            if job.leased_until is None or job.leased_until <= fenced_now:
                self.repository.release_claim_without_attempt(
                    session,
                    job_id=claim.job_id,
                    worker_id=claim.worker_id,
                )
                return "stale"
            terminal = not retryable or job.attempt_count >= 3
            if (
                terminal
                and prepared is not None
                and prepared.staged is not None
                and prepared.owned_by_job
            ):
                run = self.repository.get_run_for_update(session, prepared.run_id)
                if run is not None and run.deployment_status != "active":
                    run.status = "failed"
                    run.artifact_locator = None
                    run.artifact_sha256 = None
                    run.metrics_after = None
                    run.failure_code = "artifact_write_failed"
                    run.deployment_status = "activation_failed"
                    run.activation_reason = "artifact_publication_failed"
                    run.completed_at = fenced_now
                    self.outcome_service.ledger_repository.append_many(
                        session,
                        run.calibration_run_id,
                        [("CALIBRATION_CANDIDATE_FAILED", {
                            "calibration_run_id": str(run.calibration_run_id),
                            "dataset_sha256": run.dataset_sha256,
                            "sample_count": run.sample_count,
                            "positive_count": run.positive_count,
                            "negative_count": run.negative_count,
                            "status": "failed",
                            "artifact_sha256": None,
                            "failure_code": "artifact_write_failed",
                            "deployment_status": run.deployment_status,
                            "activation_reason": run.activation_reason,
                        })],
                    )
            job = self.repository.fail_or_retry_job(
                session,
                job_id=claim.job_id,
                worker_id=claim.worker_id,
                now=fenced_now,
                failure_code=failure_code,
                retryable=retryable,
            )
            return "terminal" if job.status == "failed" else "retry"

    def _settle_infrastructure_failure(
        self,
        claim: ClaimedJob,
        prepared: PreparedRun | None,
    ) -> FailureSettlement:
        return self._settle_failure(
            claim,
            prepared,
            "calibration_infrastructure_failure",
            retryable=True,
        )

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("calibration worker clock must be timezone-aware")
        return value.astimezone(timezone.utc)


__all__ = ["CalibrationJobService", "DeterministicCalibrationRejection"]
