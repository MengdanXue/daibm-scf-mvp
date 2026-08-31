from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.orm import Session

from app.models_facility import FinancingFacilityModel
from app.models_governance import (
    CalibrationJobModel,
    CalibrationRunObservationModel,
    OutcomeCorrectionModel,
)
from app.models_outcome import ActualOutcomeModel, CalibrationRunModel


class OutcomeRepository:
    CALIBRATION_LOCK_KEY = 0x43414C494252
    DEPLOYABLE_SCOPES = {"controlled_demo", "external_verified"}

    def acquire_training_lock(self, session: Session) -> None:
        session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": self.CALIBRATION_LOCK_KEY},
        )

    def acquire_scope_lock(self, session: Session, *, scope: str) -> None:
        if scope not in self.DEPLOYABLE_SCOPES:
            raise ValueError("scope must be a deployable calibration scope")
        session.execute(
            text(
                "SELECT pg_advisory_xact_lock("
                "hashtextextended(:scope_key, 0))"
            ),
            {"scope_key": f"calibration:{scope}"},
        )

    def try_acquire_scope_lock(self, session: Session, *, scope: str) -> bool:
        if scope not in self.DEPLOYABLE_SCOPES:
            raise ValueError("scope must be a deployable calibration scope")
        return bool(
            session.scalar(
                text(
                    "SELECT pg_try_advisory_xact_lock("
                    "hashtextextended(:scope_key, 0))"
                ),
                {"scope_key": f"calibration:{scope}"},
            )
        )

    def claim_next_job(
        self,
        session: Session,
        *,
        worker_id: str,
        now: datetime,
        lease_until: datetime,
    ) -> CalibrationJobModel | None:
        if not worker_id.strip():
            raise ValueError("worker_id must not be blank")
        if (
            now.tzinfo is None
            or now.utcoffset() is None
            or lease_until.tzinfo is None
            or lease_until.utcoffset() is None
            or lease_until <= now
        ):
            raise ValueError("job lease timestamps must be aware and increasing")
        exhausted = session.scalar(
            select(CalibrationJobModel)
            .where(
                CalibrationJobModel.status == "running",
                CalibrationJobModel.leased_until <= now,
                CalibrationJobModel.attempt_count == 3,
            )
            .order_by(CalibrationJobModel.created_at, CalibrationJobModel.job_id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if exhausted is not None:
            abandoned_run = self.get_run_by_job(session, exhausted.job_id)
            if (
                abandoned_run is not None
                and abandoned_run.deployment_status == "not_deployed"
            ):
                abandoned_run.deployment_status = "activation_failed"
                abandoned_run.activation_reason = "job_failed"
            exhausted.status = "failed"
            exhausted.lease_owner = None
            exhausted.leased_until = None
            exhausted.completed_at = now
            exhausted.failure_code = "calibration_lease_exhausted"
            exhausted.result_run_id = None
            session.flush()
        job = session.scalar(
            select(CalibrationJobModel)
            .where(
                or_(
                    CalibrationJobModel.status == "queued",
                    and_(
                        CalibrationJobModel.status == "running",
                        CalibrationJobModel.leased_until <= now,
                    ),
                ),
                CalibrationJobModel.attempt_count < 3,
            )
            .order_by(CalibrationJobModel.created_at, CalibrationJobModel.job_id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if job is None:
            return None
        job.status = "running"
        job.attempt_count += 1
        job.lease_owner = worker_id
        job.leased_until = lease_until
        job.started_at = now
        job.completed_at = None
        job.failure_code = None
        job.result_run_id = None
        session.flush()
        return job

    def complete_job(
        self,
        session: Session,
        *,
        job_id: uuid.UUID,
        worker_id: str,
        result_run_id: uuid.UUID,
        now: datetime,
    ) -> CalibrationJobModel:
        job = self.get_job_for_update(session, job_id)
        self._require_job_owner(job, worker_id)
        job.status = "completed"
        job.lease_owner = None
        job.leased_until = None
        job.failure_code = None
        job.result_run_id = result_run_id
        job.completed_at = now
        session.flush()
        return job

    def renew_job_lease(
        self,
        session: Session,
        *,
        job_id: uuid.UUID,
        worker_id: str,
        now: datetime,
        lease_until: datetime,
    ) -> CalibrationJobModel:
        if lease_until <= now:
            raise ValueError("renewed lease must end after now")
        job = self.get_job_for_update(session, job_id)
        self._require_job_owner(job, worker_id)
        if job.leased_until is None or job.leased_until <= now:
            raise RuntimeError("calibration job lease expired")
        job.leased_until = lease_until
        session.flush()
        return job

    def release_claim_without_attempt(
        self,
        session: Session,
        *,
        job_id: uuid.UUID,
        worker_id: str,
    ) -> CalibrationJobModel:
        job = self.get_job_for_update(session, job_id)
        self._require_job_owner(job, worker_id)
        if job.attempt_count < 1:
            raise RuntimeError("claimed calibration job has no attempt to release")
        job.status = "queued"
        job.attempt_count -= 1
        job.lease_owner = None
        job.leased_until = None
        job.started_at = None
        job.completed_at = None
        job.failure_code = None
        job.result_run_id = None
        session.flush()
        return job

    def fail_or_retry_job(
        self,
        session: Session,
        *,
        job_id: uuid.UUID,
        worker_id: str,
        now: datetime,
        failure_code: str,
        retryable: bool = True,
    ) -> CalibrationJobModel:
        if not failure_code or not failure_code.replace("_", "a").isalnum():
            raise ValueError("failure_code must use stable lowercase code syntax")
        job = self.get_job_for_update(session, job_id)
        self._require_job_owner(job, worker_id)
        job.lease_owner = None
        job.leased_until = None
        job.result_run_id = None
        if retryable and job.attempt_count < 3:
            job.status = "queued"
            job.started_at = None
            job.completed_at = None
            job.failure_code = None
        else:
            job.status = "failed"
            job.completed_at = now
            job.failure_code = failure_code
        session.flush()
        return job

    def get_job(
        self,
        session: Session,
        job_id: uuid.UUID,
    ) -> CalibrationJobModel | None:
        return session.get(CalibrationJobModel, job_id)

    def get_job_for_update(
        self,
        session: Session,
        job_id: uuid.UUID,
    ) -> CalibrationJobModel | None:
        return session.scalar(
            select(CalibrationJobModel)
            .where(CalibrationJobModel.job_id == job_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    @staticmethod
    def _require_job_owner(
        job: CalibrationJobModel | None,
        worker_id: str,
    ) -> None:
        if job is None:
            raise RuntimeError("claimed calibration job is missing")
        if job.status != "running" or job.lease_owner != worker_id:
            raise RuntimeError("calibration job lease ownership changed")

    def get_facility_for_update(
        self,
        session: Session,
        facility_id: uuid.UUID,
    ) -> FinancingFacilityModel | None:
        return session.scalar(
            select(FinancingFacilityModel)
            .where(FinancingFacilityModel.facility_id == facility_id)
            .with_for_update()
        )

    def get_outcome(
        self,
        session: Session,
        outcome_id: uuid.UUID,
    ) -> ActualOutcomeModel | None:
        return session.get(ActualOutcomeModel, outcome_id)

    def get_outcome_for_update(
        self,
        session: Session,
        outcome_id: uuid.UUID,
    ) -> ActualOutcomeModel | None:
        return session.scalar(
            select(ActualOutcomeModel)
            .where(ActualOutcomeModel.outcome_id == outcome_id)
            .with_for_update()
        )

    def get_by_facility(
        self,
        session: Session,
        facility_id: uuid.UUID,
    ) -> ActualOutcomeModel | None:
        return session.scalar(
            select(ActualOutcomeModel).where(
                ActualOutcomeModel.facility_id == facility_id
            )
        )

    def get_by_idempotency_key(
        self,
        session: Session,
        idempotency_key: uuid.UUID,
    ) -> ActualOutcomeModel | None:
        return session.scalar(
            select(ActualOutcomeModel).where(
                ActualOutcomeModel.idempotency_key == idempotency_key
            )
        )

    def get_correction_by_idempotency_key(
        self,
        session: Session,
        idempotency_key: uuid.UUID,
    ) -> OutcomeCorrectionModel | None:
        return session.scalar(
            select(OutcomeCorrectionModel).where(
                OutcomeCorrectionModel.idempotency_key == idempotency_key
            )
        )

    def get_correction_head(
        self,
        session: Session,
        outcome_id: uuid.UUID,
        *,
        for_update: bool = False,
    ) -> OutcomeCorrectionModel | None:
        statement = (
            select(OutcomeCorrectionModel)
            .where(OutcomeCorrectionModel.outcome_id == outcome_id)
            .order_by(
                OutcomeCorrectionModel.recorded_at.desc(),
                OutcomeCorrectionModel.correction_id.desc(),
            )
            .limit(1)
        )
        if for_update:
            statement = statement.with_for_update()
        return session.scalar(statement)

    def list_corrections(
        self,
        session: Session,
        outcome_id: uuid.UUID,
    ) -> list[OutcomeCorrectionModel]:
        return list(
            session.scalars(
                select(OutcomeCorrectionModel)
                .where(OutcomeCorrectionModel.outcome_id == outcome_id)
                .order_by(
                    OutcomeCorrectionModel.recorded_at.asc(),
                    OutcomeCorrectionModel.correction_id.asc(),
                )
            )
        )

    def list_eligible_outcomes(
        self,
        session: Session,
        *,
        scope: str,
    ) -> list[ActualOutcomeModel]:
        provenance = self._provenance_for_scope(scope)
        latest_action = (
            select(OutcomeCorrectionModel.action)
            .where(
                OutcomeCorrectionModel.outcome_id == ActualOutcomeModel.outcome_id
            )
            .order_by(
                OutcomeCorrectionModel.recorded_at.desc(),
                OutcomeCorrectionModel.correction_id.desc(),
            )
            .limit(1)
            .correlate(ActualOutcomeModel)
            .scalar_subquery()
        )
        return list(
            session.scalars(
                select(ActualOutcomeModel)
                .where(
                    ActualOutcomeModel.provenance == provenance,
                    or_(latest_action.is_(None), latest_action == "REINSTATE"),
                )
                .order_by(ActualOutcomeModel.outcome_id)
            )
        )

    def list_eligible_outcomes_with_heads(
        self,
        session: Session,
        *,
        scope: str,
    ) -> list[tuple[ActualOutcomeModel, uuid.UUID | None]]:
        provenance = self._provenance_for_scope(scope)
        latest_id = (
            select(OutcomeCorrectionModel.correction_id)
            .where(
                OutcomeCorrectionModel.outcome_id == ActualOutcomeModel.outcome_id
            )
            .order_by(
                OutcomeCorrectionModel.recorded_at.desc(),
                OutcomeCorrectionModel.correction_id.desc(),
            )
            .limit(1)
            .correlate(ActualOutcomeModel)
            .scalar_subquery()
        )
        latest_action = (
            select(OutcomeCorrectionModel.action)
            .where(
                OutcomeCorrectionModel.outcome_id == ActualOutcomeModel.outcome_id
            )
            .order_by(
                OutcomeCorrectionModel.recorded_at.desc(),
                OutcomeCorrectionModel.correction_id.desc(),
            )
            .limit(1)
            .correlate(ActualOutcomeModel)
            .scalar_subquery()
        )
        return [
            (outcome, correction_head_id)
            for outcome, correction_head_id in session.execute(
                select(ActualOutcomeModel, latest_id.label("correction_head_id"))
                .where(
                    ActualOutcomeModel.provenance == provenance,
                    or_(latest_action.is_(None), latest_action == "REINSTATE"),
                )
                .order_by(ActualOutcomeModel.outcome_id)
            )
        ]

    def count_excluded_outcomes(
        self,
        session: Session,
        *,
        scope: str,
    ) -> int:
        provenance = self._provenance_for_scope(scope)
        latest_action = (
            select(OutcomeCorrectionModel.action)
            .where(
                OutcomeCorrectionModel.outcome_id == ActualOutcomeModel.outcome_id
            )
            .order_by(
                OutcomeCorrectionModel.recorded_at.desc(),
                OutcomeCorrectionModel.correction_id.desc(),
            )
            .limit(1)
            .correlate(ActualOutcomeModel)
            .scalar_subquery()
        )
        return int(
            session.scalar(
                select(func.count())
                .select_from(ActualOutcomeModel)
                .where(
                    ActualOutcomeModel.provenance == provenance,
                    latest_action == "EXCLUDE",
                )
            )
            or 0
        )

    def run_membership_is_eligible(
        self,
        session: Session,
        run_id: uuid.UUID,
        *,
        scope: str,
    ) -> bool:
        provenance = self._provenance_for_scope(scope)
        latest_action = (
            select(OutcomeCorrectionModel.action)
            .where(
                OutcomeCorrectionModel.outcome_id == ActualOutcomeModel.outcome_id
            )
            .order_by(
                OutcomeCorrectionModel.recorded_at.desc(),
                OutcomeCorrectionModel.correction_id.desc(),
            )
            .limit(1)
            .correlate(ActualOutcomeModel)
            .scalar_subquery()
        )
        membership_count, invalid_count = session.execute(
            select(
                func.count(),
                func.count().filter(
                    or_(
                        ActualOutcomeModel.provenance != provenance,
                        latest_action == "EXCLUDE",
                    )
                ),
            )
            .select_from(CalibrationRunObservationModel)
            .join(
                ActualOutcomeModel,
                ActualOutcomeModel.outcome_id
                == CalibrationRunObservationModel.outcome_id,
            )
            .where(CalibrationRunObservationModel.calibration_run_id == run_id)
        ).one()
        return membership_count > 0 and invalid_count == 0

    def invalidate_active_runs_containing(
        self,
        session: Session,
        outcome_id: uuid.UUID,
        *,
        scope: str,
        now: datetime,
    ) -> list[CalibrationRunModel]:
        self._provenance_for_scope(scope)
        runs = list(
            session.scalars(
                select(CalibrationRunModel)
                .join(
                    CalibrationRunObservationModel,
                    CalibrationRunObservationModel.calibration_run_id
                    == CalibrationRunModel.calibration_run_id,
                )
                .where(
                    CalibrationRunObservationModel.outcome_id == outcome_id,
                    CalibrationRunModel.deployment_scope == scope,
                    CalibrationRunModel.deployment_status == "active",
                )
                .order_by(CalibrationRunModel.calibration_run_id)
                .with_for_update(of=CalibrationRunModel)
            )
        )
        for run in runs:
            run.deployment_status = "invalidated"
            run.deactivated_at = now
            run.activation_reason = "outcome_excluded"
        return runs

    def list_invalidated_runs_containing(
        self,
        session: Session,
        outcome_id: uuid.UUID,
        *,
        scope: str,
    ) -> list[CalibrationRunModel]:
        self._provenance_for_scope(scope)
        return list(
            session.scalars(
                select(CalibrationRunModel)
                .join(
                    CalibrationRunObservationModel,
                    CalibrationRunObservationModel.calibration_run_id
                    == CalibrationRunModel.calibration_run_id,
                )
                .where(
                    CalibrationRunObservationModel.outcome_id == outcome_id,
                    CalibrationRunModel.deployment_scope == scope,
                    CalibrationRunModel.deployment_status == "invalidated",
                    CalibrationRunModel.activation_reason == "outcome_excluded",
                )
                .order_by(CalibrationRunModel.calibration_run_id)
            )
        )

    def get_job_by_outcome(
        self,
        session: Session,
        outcome_id: uuid.UUID,
    ) -> CalibrationJobModel | None:
        return session.scalar(
            select(CalibrationJobModel).where(
                CalibrationJobModel.trigger_outcome_id == outcome_id
            )
        )

    def get_job_by_correction(
        self,
        session: Session,
        correction_id: uuid.UUID,
    ) -> CalibrationJobModel | None:
        return session.scalar(
            select(CalibrationJobModel).where(
                CalibrationJobModel.trigger_correction_id == correction_id
            )
        )

    def get_run_by_outcome(
        self,
        session: Session,
        outcome_id: uuid.UUID,
    ) -> CalibrationRunModel | None:
        return session.scalar(
            select(CalibrationRunModel).where(
                CalibrationRunModel.trigger_outcome_id == outcome_id
            )
        )

    def get_run(
        self,
        session: Session,
        run_id: uuid.UUID,
    ) -> CalibrationRunModel | None:
        return session.get(CalibrationRunModel, run_id)

    def get_run_by_job(
        self,
        session: Session,
        job_id: uuid.UUID,
    ) -> CalibrationRunModel | None:
        return session.scalar(
            select(CalibrationRunModel).where(
                CalibrationRunModel.trigger_job_id == job_id
            )
        )

    def get_reusable_run_by_dataset(
        self,
        session: Session,
        dataset_sha256: str,
        *,
        scope: str,
    ) -> CalibrationRunModel | None:
        self._provenance_for_scope(scope)
        return session.scalar(
            select(CalibrationRunModel).where(
                CalibrationRunModel.dataset_sha256 == dataset_sha256,
                CalibrationRunModel.deployment_scope == scope,
                CalibrationRunModel.status == "eligible_candidate",
                CalibrationRunModel.deployment_status.in_(
                    ("not_deployed", "active")
                ),
            )
            .order_by(
                CalibrationRunModel.completed_at.desc(),
                CalibrationRunModel.calibration_run_id.desc(),
            )
            .limit(1)
        )

    def run_membership_matches_eligible_snapshot(
        self,
        session: Session,
        run_id: uuid.UUID,
        *,
        scope: str,
    ) -> bool:
        current = {
            (outcome.outcome_id, correction_head_id)
            for outcome, correction_head_id in self.list_eligible_outcomes_with_heads(
                session,
                scope=scope,
            )
        }
        persisted = {
            (row.outcome_id, row.correction_head_id)
            for row in self.list_run_membership(session, run_id)
        }
        return bool(current) and current == persisted

    def list_run_membership(
        self,
        session: Session,
        run_id: uuid.UUID,
    ) -> list[CalibrationRunObservationModel]:
        return list(
            session.scalars(
                select(CalibrationRunObservationModel)
                .where(
                    CalibrationRunObservationModel.calibration_run_id == run_id
                )
                .order_by(CalibrationRunObservationModel.outcome_id)
            )
        )

    def get_run_for_update(
        self,
        session: Session,
        run_id: uuid.UUID,
    ) -> CalibrationRunModel | None:
        return session.scalar(
            select(CalibrationRunModel)
            .where(CalibrationRunModel.calibration_run_id == run_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def get_active_run(
        self,
        session: Session,
        *,
        scope: str | None = None,
        for_update: bool = False,
    ) -> CalibrationRunModel | None:
        statement = select(CalibrationRunModel).where(
            CalibrationRunModel.deployment_status == "active"
        )
        if scope is not None:
            self._provenance_for_scope(scope)
            statement = statement.where(
                CalibrationRunModel.deployment_scope == scope
            )
        if for_update:
            statement = statement.with_for_update()
        return session.scalar(statement)

    def list_pending_deployment_runs(
        self,
        session: Session,
    ) -> list[CalibrationRunModel]:
        return list(
            session.scalars(
                select(CalibrationRunModel)
                .where(
                    CalibrationRunModel.status.in_(
                        ("exploratory_candidate", "eligible_candidate")
                    ),
                    CalibrationRunModel.deployment_status == "not_deployed",
                    CalibrationRunModel.activation_reason == "not_evaluated",
                )
                .order_by(
                    CalibrationRunModel.completed_at,
                    CalibrationRunModel.calibration_run_id,
                )
            )
        )

    def list_outcomes(
        self,
        session: Session,
        *,
        limit: int,
        offset: int,
    ) -> list[ActualOutcomeModel]:
        return list(
            session.scalars(
                select(ActualOutcomeModel)
                .order_by(ActualOutcomeModel.recorded_at.desc(), ActualOutcomeModel.outcome_id)
                .limit(limit)
                .offset(offset)
            )
        )

    def list_outcomes_with_eligibility(
        self,
        session: Session,
        *,
        limit: int,
        offset: int,
    ) -> list[tuple[ActualOutcomeModel, bool]]:
        latest_action = (
            select(OutcomeCorrectionModel.action)
            .where(
                OutcomeCorrectionModel.outcome_id == ActualOutcomeModel.outcome_id
            )
            .order_by(
                OutcomeCorrectionModel.recorded_at.desc(),
                OutcomeCorrectionModel.correction_id.desc(),
            )
            .limit(1)
            .correlate(ActualOutcomeModel)
            .scalar_subquery()
        )
        statement = (
            select(
                ActualOutcomeModel,
                or_(latest_action.is_(None), latest_action == "REINSTATE").label(
                    "effective_training_eligible"
                ),
            )
            .order_by(
                ActualOutcomeModel.recorded_at.desc(),
                ActualOutcomeModel.outcome_id,
            )
            .limit(limit)
            .offset(offset)
        )
        return [
            (outcome, bool(eligible))
            for outcome, eligible in session.execute(statement)
        ]

    def list_all_outcomes(self, session: Session) -> list[ActualOutcomeModel]:
        return list(
            session.scalars(
                select(ActualOutcomeModel).order_by(ActualOutcomeModel.outcome_id)
            )
        )

    def list_runs(
        self,
        session: Session,
        *,
        limit: int,
        offset: int,
    ) -> list[CalibrationRunModel]:
        return list(
            session.scalars(
                select(CalibrationRunModel)
                .order_by(
                    CalibrationRunModel.completed_at.desc(),
                    CalibrationRunModel.calibration_run_id,
                )
                .limit(limit)
                .offset(offset)
            )
        )

    @staticmethod
    def add_outcome(
        session: Session,
        outcome: ActualOutcomeModel,
    ) -> ActualOutcomeModel:
        session.add(outcome)
        session.flush()
        return outcome

    @staticmethod
    def add_correction(
        session: Session,
        correction: OutcomeCorrectionModel,
    ) -> OutcomeCorrectionModel:
        session.add(correction)
        session.flush()
        return correction

    @staticmethod
    def add_job(
        session: Session,
        job: CalibrationJobModel,
    ) -> CalibrationJobModel:
        session.add(job)
        session.flush()
        return job

    @staticmethod
    def add_run(
        session: Session,
        run: CalibrationRunModel,
    ) -> CalibrationRunModel:
        session.add(run)
        session.flush()
        return run

    @staticmethod
    def add_run_observations(
        session: Session,
        observations: list[CalibrationRunObservationModel],
    ) -> None:
        session.add_all(observations)
        session.flush()

    @staticmethod
    def _provenance_for_scope(scope: str) -> str:
        mapping = {
            "controlled_demo": "CONTROLLED_DEMO",
            "external_verified": "EXTERNAL_VERIFIED",
        }
        try:
            return mapping[scope]
        except KeyError as error:
            raise ValueError("scope must be a deployable calibration scope") from error


__all__ = ["OutcomeRepository"]
