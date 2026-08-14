from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .db import connect, initialize
from .ledger import Ledger
from .risk import assess
from .schemas import FinancingRequestCreate


DECISIONS = {
    "low": ("approved", "standard_monitoring"),
    "medium": ("manual_review", "request_documents_and_enhanced_validation"),
    "high": ("rejected", "suspend_auto_approval_and_enhanced_validation"),
}


class FinancingService:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.ledger = Ledger(db_path)

    def initialize(self) -> None:
        initialize(self.db_path)

    def create_request(self, request: FinancingRequestCreate) -> dict[str, Any]:
        request_id = str(uuid.uuid4())
        created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        result = assess(request)
        decision, control_action = DECISIONS[result.band]
        features = request.model_dump()
        explanations = result.contributions

        with connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO financing_requests
                    (request_id, created_at, applicant_id, amount, term_days,
                     features_json, risk_score, decision, explanation_json, control_action)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    created_at,
                    request.applicant_id,
                    request.amount,
                    request.term_days,
                    json.dumps(features, ensure_ascii=False, sort_keys=True),
                    result.score,
                    decision,
                    json.dumps(explanations, ensure_ascii=False),
                    control_action,
                ),
            )

        self.ledger.append(
            "FINANCING_REQUEST",
            request_id,
            {
                "applicant_id": request.applicant_id,
                "amount": request.amount,
                "term_days": request.term_days,
            },
        )
        self.ledger.append(
            "RISK_ASSESSMENT",
            request_id,
            {
                "score": result.score,
                "band": result.band,
                "top_contributions": explanations[:3],
                "model": "transparent_logistic_baseline_v0.1",
            },
        )
        self.ledger.append(
            "FINANCING_DECISION",
            request_id,
            {"decision": decision, "risk_score": result.score},
        )
        self.ledger.append(
            "CONTROL_ACTION",
            request_id,
            {"action": control_action, "source": "risk_decision_loop"},
        )
        return self.get_request(request_id)

    def get_request(self, request_id: str) -> dict[str, Any]:
        with connect(self.db_path) as connection:
            row = connection.execute(
                "SELECT * FROM financing_requests WHERE request_id = ?", (request_id,)
            ).fetchone()
        if row is None:
            raise KeyError(request_id)
        return self._row_to_request(row)

    def list_requests(self, limit: int = 50) -> list[dict[str, Any]]:
        with connect(self.db_path) as connection:
            rows = connection.execute(
                "SELECT * FROM financing_requests ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_request(row) for row in rows]

    def dashboard(self) -> dict[str, Any]:
        with connect(self.db_path) as connection:
            total = connection.execute(
                "SELECT COUNT(*) AS count FROM financing_requests"
            ).fetchone()["count"]
            average = connection.execute(
                "SELECT COALESCE(AVG(risk_score), 0) AS value FROM financing_requests"
            ).fetchone()["value"]
            rows = connection.execute(
                "SELECT decision, COUNT(*) AS count FROM financing_requests GROUP BY decision"
            ).fetchall()
        decision_counts = {row["decision"]: row["count"] for row in rows}
        verification = self.ledger.verify()
        return {
            "request_count": total,
            "average_risk_score": round(float(average), 4),
            "decision_counts": decision_counts,
            "ledger_valid": verification["valid"],
            "ledger_event_count": verification["event_count"],
        }

    def seed_demo(self) -> list[dict[str, Any]]:
        if self.list_requests(limit=1):
            return self.list_requests(limit=50)
        scenarios = [
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
        ]
        return [self.create_request(scenario) for scenario in scenarios]

    def reset_demo(self) -> list[dict[str, Any]]:
        """Reset only the local synthetic demo store and rebuild the three scenarios."""
        with connect(self.db_path) as connection:
            connection.execute("DELETE FROM ledger_events")
            connection.execute("DELETE FROM financing_requests")
        return self.seed_demo()

    @staticmethod
    def _row_to_request(row) -> dict[str, Any]:
        features = json.loads(row["features_json"])
        return {
            "request_id": row["request_id"],
            "created_at": row["created_at"],
            "applicant_id": row["applicant_id"],
            "amount": row["amount"],
            "term_days": row["term_days"],
            "features": features,
            "risk_score": row["risk_score"],
            "decision": row["decision"],
            "explanations": json.loads(row["explanation_json"]),
            "control_action": row["control_action"],
        }
