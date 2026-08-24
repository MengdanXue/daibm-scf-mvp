from __future__ import annotations

import uuid

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models_facility import FinancingFacilityModel
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
    def add_run(
        session: Session,
        run: CalibrationRunModel,
    ) -> CalibrationRunModel:
        session.add(run)
        session.flush()
        return run


__all__ = ["OutcomeRepository"]
