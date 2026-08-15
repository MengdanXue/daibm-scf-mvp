from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.domain.research import PolicyDecision, RiskAssessment


class PolicyEngine:
    version = "scf-risk-policy-v0.4"
    low_threshold = 0.40
    high_threshold = 0.75

    def evaluate(self, assessment: RiskAssessment) -> PolicyDecision:
        if assessment.risk_score < self.low_threshold:
            decision = "NORMAL"
            action = "normal_monitoring"
            reason = "risk_below_low_threshold"
        elif assessment.risk_score < self.high_threshold:
            decision = "ADDITIONAL_CHECK"
            action = "additional_verification"
            reason = "risk_between_policy_thresholds"
        else:
            decision = "FINANCING_REVIEW"
            action = "financing_review"
            reason = "risk_at_or_above_high_threshold"
        return PolicyDecision(
            policy_decision_id=uuid4(),
            risk_assessment_id=assessment.risk_assessment_id,
            policy_version=self.version,
            decision=decision,
            low_threshold=self.low_threshold,
            high_threshold=self.high_threshold,
            reason_codes=(reason,),
            permitted_action=action,
            created_at=datetime.now(timezone.utc),
            source_assessment=assessment,
        )
