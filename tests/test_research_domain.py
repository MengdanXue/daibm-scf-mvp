from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from app.domain.research import RiskAssessment
from app.services.policy import PolicyEngine


def assessment(score: float) -> RiskAssessment:
    return RiskAssessment(
        risk_assessment_id=uuid4(),
        enterprise_id="E0001",
        graph_snapshot_id=uuid4(),
        model_version_id=uuid4(),
        input_sha256="a" * 64,
        risk_score=score,
        band="unassigned",
        explanations=(),
        inferred_at=datetime(2026, 8, 15, tzinfo=timezone.utc),
    )


@pytest.mark.parametrize(
    ("score", "decision", "action"),
    [
        (0.0, "NORMAL", "normal_monitoring"),
        (0.399999, "NORMAL", "normal_monitoring"),
        (0.40, "ADDITIONAL_CHECK", "additional_verification"),
        (0.749999, "ADDITIONAL_CHECK", "additional_verification"),
        (0.75, "FINANCING_REVIEW", "financing_review"),
        (1.0, "FINANCING_REVIEW", "financing_review"),
    ],
)
def test_policy_engine_uses_registered_boundaries(score, decision, action):
    result = PolicyEngine().evaluate(assessment(score))

    assert result.decision == decision
    assert result.permitted_action == action
    assert result.policy_version == "scf-risk-policy-v0.4"
    assert result.low_threshold == 0.40
    assert result.high_threshold == 0.75
    assert result.risk_assessment_id == result.source_assessment.risk_assessment_id


@pytest.mark.parametrize("score", [-0.001, 1.001])
def test_risk_assessment_rejects_scores_outside_probability_range(score):
    with pytest.raises(ValueError, match="between 0 and 1"):
        assessment(score)


def test_research_domain_values_are_immutable():
    value = assessment(0.2)

    with pytest.raises(FrozenInstanceError):
        value.risk_score = 0.9

    assert isinstance(value.risk_assessment_id, UUID)

