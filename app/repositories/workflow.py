from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.workflow import Role, Status
from app.identity import AuthenticatedUser
from app.models import FinancingRequestModel
from app.services.permissions import PermissionService
from app.models_identity import OrganizationModel, UserModel
from app.models_workflow import WorkflowActionModel


class WorkflowRepository:
    def add_application(
        self,
        session: Session,
        application: FinancingRequestModel,
    ) -> FinancingRequestModel:
        session.add(application)
        session.flush()
        return application

    def get_application(
        self,
        session: Session,
        request_id: uuid.UUID,
        *,
        for_update: bool = False,
    ) -> FinancingRequestModel | None:
        statement = select(FinancingRequestModel).where(
            FinancingRequestModel.request_id == request_id
        )
        if for_update:
            statement = statement.with_for_update()
        return session.scalar(statement)

    def get_by_invoice_claim(
        self,
        session: Session,
        invoice_claim_sha256: str,
        *,
        excluding_request_id: uuid.UUID | None = None,
    ) -> FinancingRequestModel | None:
        statement = select(FinancingRequestModel).where(
            FinancingRequestModel.invoice_claim_sha256 == invoice_claim_sha256
        )
        if excluding_request_id is not None:
            statement = statement.where(
                FinancingRequestModel.request_id != excluding_request_id
            )
        return session.scalar(statement)

    def list_for_user(
        self,
        session: Session,
        user: AuthenticatedUser,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[FinancingRequestModel]:
        statement = select(FinancingRequestModel)
        role = Role(user.role)
        if role == Role.SUPPLIER:
            statement = statement.where(
                FinancingRequestModel.supplier_organization_id
                == user.organization_id
            )
        elif role == Role.CORE_ENTERPRISE:
            statement = statement.where(
                FinancingRequestModel.core_enterprise_organization_id
                == user.organization_id
            )
        elif role == Role.FINANCIER:
            statement = statement.where(
                FinancingRequestModel.lender_organization_id == user.organization_id,
                FinancingRequestModel.status.in_(
                    (
                        Status.TRADE_CONFIRMED.value,
                        Status.RISK_ASSESSED.value,
                        Status.APPROVED.value,
                        Status.MANUAL_REVIEW.value,
                        Status.REJECTED.value,
                        Status.CONTROLLED.value,
                        Status.AUDITED.value,
                    )
                )
            )
        elif role == Role.RISK_MANAGER:
            statement = statement.where(
                FinancingRequestModel.lender_organization_id == user.organization_id,
                FinancingRequestModel.status.in_(
                    (
                        Status.APPROVED.value,
                        Status.MANUAL_REVIEW.value,
                        Status.REJECTED.value,
                        Status.CONTROLLED.value,
                        Status.AUDITED.value,
                    )
                )
            )
        elif role == Role.AUDITOR:
            statement = statement.where(
                PermissionService.organization_filter(
                    user, FinancingRequestModel.lender_organization_id
                )
            )
        elif role != Role.ADMIN:
            return []
        statement = (
            statement.order_by(FinancingRequestModel.updated_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(session.scalars(statement))

    def list_timeline(
        self,
        session: Session,
        request_id: uuid.UUID,
    ) -> list[tuple[WorkflowActionModel, UserModel, OrganizationModel]]:
        rows = session.execute(
            select(WorkflowActionModel, UserModel, OrganizationModel)
            .join(UserModel, WorkflowActionModel.actor_user_id == UserModel.user_id)
            .join(
                OrganizationModel,
                UserModel.organization_id == OrganizationModel.organization_id,
            )
            .where(WorkflowActionModel.request_id == request_id)
            .order_by(WorkflowActionModel.action_id)
        )
        return [(row[0], row[1], row[2]) for row in rows]
