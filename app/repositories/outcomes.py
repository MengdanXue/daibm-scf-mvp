from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import or_, select, text
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

    def acquire_training_lock(self, session: Session) -> None:
        session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": self.CALIBRATION_LOCK_KEY},
        )

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

    def get_run_for_update(
        self,
        session: Session,
        run_id: uuid.UUID,
    ) -> CalibrationRunModel | None:
        return session.scalar(
            select(CalibrationRunModel)
            .where(CalibrationRunModel.calibration_run_id == run_id)
            .with_for_update()
        )

    def get_active_run(
        self,
        session: Session,
        *,
        for_update: bool = False,
    ) -> CalibrationRunModel | None:
        statement = select(CalibrationRunModel).where(
            CalibrationRunModel.deployment_status == "active"
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
