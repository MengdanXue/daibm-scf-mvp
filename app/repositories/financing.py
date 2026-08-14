from __future__ import annotations

import uuid

from sqlalchemy import func, select
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

    def list(
        self,
        session: Session,
        limit: int = 50,
    ) -> list[FinancingRequestModel]:
        statement = (
            select(FinancingRequestModel)
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
    ) -> tuple[int, float, dict[str, int]]:
        total = session.scalar(
            select(func.count()).select_from(FinancingRequestModel)
        ) or 0
        average = session.scalar(
            select(func.avg(FinancingRequestModel.risk_score))
        ) or 0.0
        rows = session.execute(
            select(
                FinancingRequestModel.decision,
                func.count(),
            ).group_by(FinancingRequestModel.decision)
        )
        decision_counts = {
            decision: count
            for decision, count in rows
        }
        return int(total), float(average), decision_counts
