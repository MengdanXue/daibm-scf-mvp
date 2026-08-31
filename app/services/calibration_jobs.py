from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from app.models_governance import CalibrationJobModel, CalibrationRunObservationModel
from app.models_outcome import CalibrationRunModel
from app.repositories.outcomes import OutcomeRepository
from app.services.outcome_calibration import (
    CalibrationCandidate,
    CalibrationObservation,
    CalibrationArtifact,
    StagedCalibrationArtifact,
    discard_staged_artifact,
    publish_candidate_artifact,
    summarize_calibration_observations,
)
from app.services.outcomes import OutcomeService


ArtifactPublisher = Callable[[StagedCalibrationArtifact], CalibrationArtifact]


class DeterministicCalibrationRejection(Exception):
    """A stable data rejection that must not consume infrastructure retries."""


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
        claim = self._claim(worker_id)
        if claim is None:
            return False
        prepared: PreparedRun | None = None
        try:
            prepared = self._prepare_run(claim)
            if prepared.staged is None:
                self._complete(claim, prepared.run_id)
                return True
            self.artifact_publisher(prepared.staged)
            candidate = prepared.candidate or self._reload_candidate(prepared.run_id)
            provenances = self._run_provenances(prepared.run_id)
            self.outcome_service._evaluate_deployment(
                prepared.run_id,
                candidate,
                provenances=provenances,
            )
            self._complete(claim, prepared.run_id)
        except DeterministicCalibrationRejection:
            self._fail(claim, "calibration_data_rejected", retryable=False)
        except Exception:
            terminal = self._fail(
                claim,
                "calibration_infrastructure_failure",
                retryable=True,
            )
            if terminal and prepared is not None:
                self._terminalize_artifact_failure(prepared)
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

    def _claim(self, worker_id: str) -> ClaimedJob | None:
        now = self._now()
        with self.session_factory.begin() as session:
            job = self.repository.claim_next_job(
                session,
                worker_id=worker_id,
                now=now,
                lease_until=now + self.lease_duration,
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
            job = self.repository.get_job_for_update(session, claim.job_id)
            self.repository._require_job_owner(job, claim.worker_id)
            if job.deployment_scope != claim.deployment_scope:
                raise RuntimeError("calibration job scope changed after claim")
            self.repository.acquire_scope_lock(
                session,
                scope=claim.deployment_scope,
            )

            existing_for_job = self.repository.get_run_by_job(session, claim.job_id)
            if existing_for_job is not None:
                return self._prepared_existing(existing_for_job)

            rows = self.repository.list_eligible_outcomes_with_heads(
                session,
                scope=claim.deployment_scope,
            )
            observations = tuple(
                replace(
                    self.outcome_service._observation(outcome),
                    correction_head_id=(
                        str(correction_head_id)
                        if correction_head_id is not None
                        else None
                    ),
                )
                for outcome, correction_head_id in rows
            )
            try:
                candidate = self.outcome_service.trainer(
                    observations,
                    config=self.outcome_service.training_config,
                )
            except ValueError:
                return self._persist_data_rejection(
                    session,
                    job=job,
                    observations=observations,
                    scope=claim.deployment_scope,
                )
            if candidate.deployment_scope != claim.deployment_scope:
                raise DeterministicCalibrationRejection

            duplicate = self.repository.get_run_by_dataset(
                session,
                candidate.dataset_sha256,
            )
            if duplicate is not None:
                return self._prepared_existing(duplicate)

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
                            "fold_assignment_sha256": (
                                candidate.fold_assignment_sha256
                            ),
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
            )
        except Exception:
            discard_staged_artifact(staged)
            raise

    @staticmethod
    def _prepared_existing(run: CalibrationRunModel) -> PreparedRun:
        if run.status == "failed":
            return PreparedRun(
                run_id=run.calibration_run_id,
                candidate=None,
                staged=None,
            )
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
        )

    def _persist_data_rejection(
        self,
        session: Session,
        *,
        job: CalibrationJobModel,
        observations: tuple[CalibrationObservation, ...],
        scope: str,
    ) -> PreparedRun:
        if not observations:
            raise DeterministicCalibrationRejection
        summary = summarize_calibration_observations(
            observations,
            config=self.outcome_service.training_config,
        )
        duplicate = self.repository.get_run_by_dataset(
            session,
            summary.dataset_sha256,
        )
        if duplicate is not None:
            return self._prepared_existing(duplicate)
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
                activation_reason="training_not_eligible",
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
            self.repository.complete_job(
                session,
                job_id=claim.job_id,
                worker_id=claim.worker_id,
                result_run_id=run_id,
                now=self._now(),
            )

    def _fail(
        self,
        claim: ClaimedJob,
        failure_code: str,
        *,
        retryable: bool,
    ) -> bool:
        with self.session_factory.begin() as session:
            job = self.repository.fail_or_retry_job(
                session,
                job_id=claim.job_id,
                worker_id=claim.worker_id,
                now=self._now(),
                failure_code=failure_code,
                retryable=retryable,
            )
            return job.status == "failed"

    def _terminalize_artifact_failure(self, prepared: PreparedRun) -> None:
        if prepared.staged is None:
            return
        try:
            self.outcome_service._mark_publication_failed(
                prepared.run_id,
                prepared.run_id,
            )
        finally:
            self.outcome_service._discard_failed_publication(prepared.staged)

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("calibration worker clock must be timezone-aware")
        return value.astimezone(timezone.utc)


__all__ = ["CalibrationJobService", "DeterministicCalibrationRejection"]
