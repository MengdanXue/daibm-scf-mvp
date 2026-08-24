from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models_facility import (
    FacilityActionModel,
    FinancingFacilityModel,
    InstallmentModel,
    PaymentModel,
)


class FacilityRepository:
    def add(
        self,
        session: Session,
        facility: FinancingFacilityModel,
    ) -> FinancingFacilityModel:
        session.add(facility)
        session.flush()
        return facility

    def get_for_update(
        self,
        session: Session,
        facility_id: uuid.UUID,
    ) -> FinancingFacilityModel | None:
        statement = (
            select(FinancingFacilityModel)
            .where(FinancingFacilityModel.facility_id == facility_id)
            .with_for_update()
        )
        return session.scalar(statement)

    def get_by_request(
        self,
        session: Session,
        request_id: uuid.UUID,
    ) -> FinancingFacilityModel | None:
        return session.scalar(
            select(FinancingFacilityModel).where(
                FinancingFacilityModel.request_id == request_id
            )
        )

    def find_action(
        self,
        session: Session,
        key: uuid.UUID,
    ) -> FacilityActionModel | None:
        return session.scalar(
            select(FacilityActionModel).where(
                FacilityActionModel.idempotency_key == key
            )
        )

    def list_installments(
        self,
        session: Session,
        facility_id: uuid.UUID,
    ) -> list[InstallmentModel]:
        return list(
            session.scalars(
                select(InstallmentModel)
                .where(InstallmentModel.facility_id == facility_id)
                .order_by(InstallmentModel.sequence)
            )
        )

    def list_payments(
        self,
        session: Session,
        facility_id: uuid.UUID,
    ) -> list[PaymentModel]:
        return list(
            session.scalars(
                select(PaymentModel)
                .where(PaymentModel.facility_id == facility_id)
                .order_by(PaymentModel.submitted_at, PaymentModel.payment_id)
            )
        )


__all__ = ["FacilityRepository"]
