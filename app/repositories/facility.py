from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import false, select, true
from sqlalchemy.orm import Session

from app.models import FinancingRequestModel
from app.models_facility import (
    FacilityActionModel,
    FinancingFacilityModel,
    InstallmentModel,
    PaymentModel,
)
from app.models_lifecycle import (
    FacilityDefaultModel,
    FacilityDelinquencyModel,
    FacilityRestructureModel,
    FacilityWriteOffModel,
)
from app.models_identity import UserModel


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

    def get_visible_for_share(
        self,
        session: Session,
        facility_id: uuid.UUID,
        *,
        role: str,
        organization_id: uuid.UUID | None,
    ) -> FinancingFacilityModel | None:
        statement = (
            self._visible_facilities_statement(
                role=role,
                organization_id=organization_id,
            )
            .where(FinancingFacilityModel.facility_id == facility_id)
            .with_for_update(read=True, of=FinancingFacilityModel)
        )
        return session.scalar(statement)

    def list_visible_for_share(
        self,
        session: Session,
        *,
        role: str,
        organization_id: uuid.UUID | None,
        limit: int,
        offset: int,
    ) -> list[FinancingFacilityModel]:
        statement = (
            self._visible_facilities_statement(
                role=role,
                organization_id=organization_id,
            )
            .order_by(
                FinancingFacilityModel.updated_at.desc(),
                FinancingFacilityModel.facility_id.asc(),
            )
            .limit(limit)
            .offset(offset)
            .with_for_update(read=True, of=FinancingFacilityModel)
        )
        return list(session.scalars(statement))

    @staticmethod
    def _visible_facilities_statement(
        *,
        role: str,
        organization_id: uuid.UUID | None,
    ):
        statement = (
            select(FinancingFacilityModel)
            .join(
                FinancingRequestModel,
                FinancingRequestModel.request_id
                == FinancingFacilityModel.request_id,
            )
            .join(
                UserModel,
                UserModel.user_id == FinancingFacilityModel.created_by_user_id,
            )
        )
        if role == "auditor":
            visibility = true()
        elif organization_id is None:
            visibility = false()
        elif role == "supplier":
            visibility = (
                FinancingRequestModel.supplier_organization_id == organization_id
            )
        elif role == "core_enterprise":
            visibility = (
                FinancingRequestModel.core_enterprise_organization_id
                == organization_id
            )
        elif role in {"financier", "risk_manager"}:
            visibility = UserModel.organization_id == organization_id
        else:
            visibility = false()
        return statement.where(visibility)

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
                .order_by(
                    InstallmentModel.schedule_version,
                    InstallmentModel.sequence,
                )
            )
        )

    def list_installments_batch(
        self,
        session: Session,
        facility_ids: Sequence[uuid.UUID],
    ) -> list[InstallmentModel]:
        if not facility_ids:
            return []
        return list(
            session.scalars(
                select(InstallmentModel)
                .where(InstallmentModel.facility_id.in_(facility_ids))
                .order_by(
                    InstallmentModel.facility_id,
                    InstallmentModel.schedule_version,
                    InstallmentModel.sequence,
                )
            )
        )

    def list_delinquencies(
        self,
        session: Session,
        facility_id: uuid.UUID,
    ) -> list[FacilityDelinquencyModel]:
        return list(
            session.scalars(
                select(FacilityDelinquencyModel)
                .where(FacilityDelinquencyModel.facility_id == facility_id)
                .order_by(
                    FacilityDelinquencyModel.recorded_at,
                    FacilityDelinquencyModel.delinquency_id,
                )
            )
        )

    def list_delinquencies_batch(
        self,
        session: Session,
        facility_ids: Sequence[uuid.UUID],
    ) -> list[FacilityDelinquencyModel]:
        if not facility_ids:
            return []
        return list(
            session.scalars(
                select(FacilityDelinquencyModel)
                .where(FacilityDelinquencyModel.facility_id.in_(facility_ids))
                .order_by(
                    FacilityDelinquencyModel.facility_id,
                    FacilityDelinquencyModel.recorded_at,
                    FacilityDelinquencyModel.delinquency_id,
                )
            )
        )

    def list_restructures(
        self,
        session: Session,
        facility_id: uuid.UUID,
    ) -> list[FacilityRestructureModel]:
        return list(
            session.scalars(
                select(FacilityRestructureModel)
                .where(FacilityRestructureModel.facility_id == facility_id)
                .order_by(
                    FacilityRestructureModel.recorded_at,
                    FacilityRestructureModel.restructure_id,
                )
            )
        )

    def list_restructures_batch(
        self,
        session: Session,
        facility_ids: Sequence[uuid.UUID],
    ) -> list[FacilityRestructureModel]:
        if not facility_ids:
            return []
        return list(
            session.scalars(
                select(FacilityRestructureModel)
                .where(FacilityRestructureModel.facility_id.in_(facility_ids))
                .order_by(
                    FacilityRestructureModel.facility_id,
                    FacilityRestructureModel.recorded_at,
                    FacilityRestructureModel.restructure_id,
                )
            )
        )

    def get_default(
        self,
        session: Session,
        facility_id: uuid.UUID,
    ) -> FacilityDefaultModel | None:
        return session.scalar(
            select(FacilityDefaultModel).where(
                FacilityDefaultModel.facility_id == facility_id
            )
        )

    def list_defaults_batch(
        self,
        session: Session,
        facility_ids: Sequence[uuid.UUID],
    ) -> list[FacilityDefaultModel]:
        if not facility_ids:
            return []
        return list(
            session.scalars(
                select(FacilityDefaultModel)
                .where(FacilityDefaultModel.facility_id.in_(facility_ids))
                .order_by(FacilityDefaultModel.facility_id)
            )
        )

    def get_writeoff(
        self,
        session: Session,
        facility_id: uuid.UUID,
    ) -> FacilityWriteOffModel | None:
        return session.scalar(
            select(FacilityWriteOffModel).where(
                FacilityWriteOffModel.facility_id == facility_id
            )
        )

    def list_writeoffs_batch(
        self,
        session: Session,
        facility_ids: Sequence[uuid.UUID],
    ) -> list[FacilityWriteOffModel]:
        if not facility_ids:
            return []
        return list(
            session.scalars(
                select(FacilityWriteOffModel)
                .where(FacilityWriteOffModel.facility_id.in_(facility_ids))
                .order_by(FacilityWriteOffModel.facility_id)
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

    def list_payments_batch(
        self,
        session: Session,
        facility_ids: Sequence[uuid.UUID],
    ) -> list[PaymentModel]:
        if not facility_ids:
            return []
        return list(
            session.scalars(
                select(PaymentModel)
                .where(PaymentModel.facility_id.in_(facility_ids))
                .order_by(
                    PaymentModel.facility_id,
                    PaymentModel.submitted_at,
                    PaymentModel.payment_id,
                )
            )
        )


__all__ = ["FacilityRepository"]
