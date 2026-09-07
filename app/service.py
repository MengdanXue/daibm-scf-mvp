from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.ledger import canonical_timestamp
from app.models import FinancingRequestModel
from app.repositories import FinancingRequestRepository, LedgerRepository
from app.repositories.ledger import LedgerEventSpec
from app.risk import BAND_DECISIONS, DECISION_CONTROLS, RiskResult, assess, band_for_score
from app.schemas import FinancingRequestCreate
from app.services.adaptive_risk import AdaptiveRiskInferenceService

__all__ = ["FinancingService"]

DECISIONS = {
    band: (decision, DECISION_CONTROLS[decision]) for band, decision in BAND_DECISIONS.items()
}

CENT = Decimal("0.01")

DEMO_SCENARIOS = (
    FinancingRequestCreate(
        applicant_id="supplier-stable-01",
        amount=450_000,
        term_days=60,
        payment_delay_days=2,
        counterparty_risk=0.12,
        invoice_mismatch=False,
        relationship_months=48,
        transactions_last_30d=8,
    ),
    FinancingRequestCreate(
        applicant_id="supplier-review-02",
        amount=1_800_000,
        term_days=90,
        payment_delay_days=18,
        counterparty_risk=0.48,
        invoice_mismatch=False,
        relationship_months=10,
        transactions_last_30d=19,
    ),
    FinancingRequestCreate(
        applicant_id="supplier-risk-03",
        amount=4_200_000,
        term_days=120,
        payment_delay_days=52,
        counterparty_risk=0.87,
        invoice_mismatch=True,
        relationship_months=2,
        transactions_last_30d=45,
    ),
)


class FinancingService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        financing_repository: FinancingRequestRepository | None = None,
        ledger_repository: LedgerRepository | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.financing_repository = financing_repository or FinancingRequestRepository()
        self.ledger_repository = ledger_repository or LedgerRepository()

    def create_request(
        self,
        request: FinancingRequestCreate,
    ) -> dict[str, Any]:
        with self.session_factory.begin() as session:
            model = self._create_request_in_session(session, request)
        return self._request_to_dict(model)

    def get_request(self, request_id: str | uuid.UUID) -> dict[str, Any]:
        try:
            normalized_id = uuid.UUID(str(request_id))
        except ValueError as error:
            raise KeyError(str(request_id)) from error
        with self.session_factory() as session:
            model = self.financing_repository.get(session, normalized_id)
            if model is None:
                raise KeyError(str(request_id))
            return self._request_to_dict(model)

    def list_requests(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            return [
                self._request_to_dict(model)
                for model in self.financing_repository.list_recent(session, limit)
            ]

    def list_ledger(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            return self.ledger_repository.list_recent(session, limit)

    def verify_ledger(self) -> dict[str, Any]:
        with self.session_factory() as session:
            return self.ledger_repository.verify(session)

    def dashboard(self) -> dict[str, Any]:
        with self.session_factory() as session:
            total, average, decision_counts = self.financing_repository.dashboard_aggregates(
                session
            )
            verification = self.ledger_repository.verify(session)
        return {
            "request_count": total,
            "average_risk_score": round(average, 4),
            "decision_counts": decision_counts,
            "ledger_valid": verification["valid"],
            "ledger_event_count": verification["event_count"],
        }

    def seed_demo(self) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            if self.financing_repository.exists_any(session):
                return [
                    self._request_to_dict(model)
                    for model in self.financing_repository.list_recent(session, 50)
                ]

        with self.session_factory.begin() as session:
            models = [
                self._create_request_in_session(session, scenario) for scenario in DEMO_SCENARIOS
            ]
        return [self._request_to_dict(model) for model in models]

    def reset_demo(self) -> list[dict[str, Any]]:
        with self.session_factory.begin() as session:
            self.ledger_repository.clear_demo_data(session)
            models = [
                self._create_request_in_session(session, scenario) for scenario in DEMO_SCENARIOS
            ]
        return [self._request_to_dict(model) for model in models]

    def tamper_demo_ledger(self) -> dict[str, Any]:
        with self.session_factory() as session:
            has_requests = self.financing_repository.exists_any(session)
        if not has_requests:
            self.seed_demo()

        verification = self.verify_ledger()
        if not verification["valid"]:
            return verification

        with self.session_factory.begin() as session:
            self.ledger_repository.tamper_first_risk_event(session)
        return self.verify_ledger()

    def _create_request_in_session(
        self,
        session: Session,
        request: FinancingRequestCreate,
    ) -> FinancingRequestModel:
        canonical_amount = Decimal(str(request.amount)).quantize(CENT)
        normalized_request = request.model_copy(update={"amount": float(canonical_amount)})
        result = assess(normalized_request)
        # This legacy public/demo workflow has no external-verification authority.
        assessment_scope = "controlled_demo"
        adaptive = AdaptiveRiskInferenceService().assess(session, result.score, assessment_scope)
        result = replace(
            result, score=adaptive.final_score, band=band_for_score(adaptive.final_score)
        )
        decision, control_action = DECISIONS[result.band]
        created_at = datetime.now(timezone.utc)
        model = FinancingRequestModel(
            request_id=uuid.uuid4(),
            created_at=created_at,
            updated_at=created_at,
            applicant_id=normalized_request.applicant_id,
            amount=canonical_amount,
            term_days=normalized_request.term_days,
            features=normalized_request.model_dump(),
            risk_score=result.score,
            raw_risk_score=adaptive.raw_score,
            assessment_scope=assessment_scope,
            calibration_run_id=uuid.UUID(adaptive.calibration_run_id)
            if adaptive.calibration_run_id
            else None,
            calibration_fallback_code=adaptive.fallback_code,
            decision=decision,
            explanations=result.contributions,
            control_action=control_action,
            status="audited",
            version=1,
        )
        self.financing_repository.add(session, model)
        events = self._event_specs(normalized_request, result, decision, control_action)
        events[1][1].update(
            {
                "assessment_scope": assessment_scope,
                "raw_score": adaptive.raw_score,
                "calibration_run_id": adaptive.calibration_run_id,
                "calibration_fallback_code": adaptive.fallback_code,
                "attempted_calibration_run_id": adaptive.attempted_calibration_run_id,
            }
        )
        self.ledger_repository.append_many(
            session,
            model.request_id,
            events,
        )
        return model

    @staticmethod
    def _event_specs(
        request: FinancingRequestCreate,
        result: RiskResult,
        decision: str,
        control_action: str,
    ) -> list[LedgerEventSpec]:
        return [
            (
                "FINANCING_REQUEST",
                {
                    "applicant_id": request.applicant_id,
                    "amount": request.amount,
                    "term_days": request.term_days,
                },
            ),
            (
                "RISK_ASSESSMENT",
                {
                    "score": result.score,
                    "band": result.band,
                    "top_contributions": result.contributions[:3],
                    "model": "transparent_logistic_baseline_v0.1",
                },
            ),
            (
                "FINANCING_DECISION",
                {"decision": decision, "risk_score": result.score},
            ),
            (
                "CONTROL_ACTION",
                {"action": control_action, "source": "risk_decision_loop"},
            ),
        ]

    @staticmethod
    def _request_to_dict(model: FinancingRequestModel) -> dict[str, Any]:
        return {
            "request_id": str(model.request_id),
            "created_at": canonical_timestamp(model.created_at),
            "applicant_id": model.applicant_id,
            "amount": float(model.amount),
            "term_days": model.term_days,
            "features": model.features,
            "risk_score": model.risk_score,
            "decision": model.decision,
            "explanations": model.explanations,
            "control_action": model.control_action,
        }
