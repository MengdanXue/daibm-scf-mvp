from __future__ import annotations

import uuid

from sqlalchemy import ColumnElement, func, select, true
from sqlalchemy.orm import Session

from app.models import FinancingRequestModel

__all__ = ["FinancingRequestRepository"]


class FinancingRequestRepository:
    def add(
        self,
        session: Session,
        request: FinancingRequestModel,
    ) -> FinancingRequestModel:
        session.add(request)
        session.flush()
        return request

    def get(
        self,
        session: Session,
        request_id: uuid.UUID,
    ) -> FinancingRequestModel | None:
        return session.get(FinancingRequestModel, request_id)

    def list_recent(
        self,
        session: Session,
        limit: int = 50,
        *,
        visibility: ColumnElement[bool] | None = None,
    ) -> list[FinancingRequestModel]:
        statement = (
            select(FinancingRequestModel)
            .where(visibility if visibility is not None else true())
            .order_by(FinancingRequestModel.created_at.desc())
            .limit(limit)
        )
        return list(session.scalars(statement))

    def exists_any(self, session: Session) -> bool:
        statement = select(FinancingRequestModel.request_id).limit(1)
        return session.scalar(statement) is not None

    def dashboard_aggregates(
        self,
        session: Session,
        *,
        visibility: ColumnElement[bool] | None = None,
    ) -> tuple[int, float, dict[str, int]]:
        condition = visibility if visibility is not None else true()
        total = session.scalar(
            select(func.count()).select_from(FinancingRequestModel).where(condition)
        ) or 0
        average = session.scalar(
            select(func.avg(FinancingRequestModel.risk_score)).where(condition)
        ) or 0.0
        rows = session.execute(
            select(
                FinancingRequestModel.decision,
                func.count(),
            )
            .where(condition)
            .group_by(FinancingRequestModel.decision)
        )
        decision_counts = {
            decision: count
            for decision, count in rows
        }
        return int(total), float(average), decision_counts
