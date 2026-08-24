from __future__ import annotations

import json
import math
import hashlib

import pytest

from app.services.outcome_calibration import (
    CalibrationObservation,
    CalibrationTrainingConfig,
    build_calibration_candidate,
    verify_candidate_artifact,
    write_candidate_artifact,
)


def _observation(index: int, score: float, defaulted: bool) -> CalibrationObservation:
    suffix = f"{index:064x}"
    return CalibrationObservation(
        outcome_id=f"00000000-0000-0000-0000-{index:012d}",
        facility_id=f"10000000-0000-0000-0000-{index:012d}",
        request_id=f"20000000-0000-0000-0000-{index:012d}",
        risk_assessment_id=f"30000000-0000-0000-0000-{index:012d}",
        model_version_id=f"40000000-0000-0000-0000-{index:012d}",
        risk_input_sha256=suffix,
        evidence_sha256=f"{index + 1:064x}",
        original_score=score,
        defaulted=defaulted,
        observed_at=f"2026-08-{index + 1:02d}T00:00:00.000000+00:00",
        provenance="CONTROLLED_DEMO",
    )


def test_candidate_is_deterministic_and_reports_independently_checked_baseline_metrics():
    observations = (
        _observation(1, 0.2, False),
        _observation(2, 0.4, False),
        _observation(3, 0.6, True),
        _observation(4, 0.8, True),
    )
    config = CalibrationTrainingConfig(epochs=800, learning_rate=0.05)

    first = build_calibration_candidate(observations, config=config)
    second = build_calibration_candidate(tuple(reversed(observations)), config=config)

    assert first == second
    assert first.sample_count == 4
    assert first.positive_count == 2
    assert first.negative_count == 2
    assert first.status == "exploratory_candidate"
    assert first.metrics_before["brier_score"] == pytest.approx(0.10)
    assert first.metrics_before["log_loss"] == pytest.approx(
        -(math.log(0.8) + math.log(0.6) + math.log(0.6) + math.log(0.8))
        / 4
    )
    assert first.metrics_after["brier_score"] < first.metrics_before["brier_score"]
    assert first.metrics_after["log_loss"] < first.metrics_before["log_loss"]
    assert first.artifact["candidate_only"] is True
    assert first.artifact["promotion_status"] == "not_promoted"
    assert first.artifact["training_input"] == "logit(original_risk_score)"
    assert json.dumps(
        first.artifact, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8") == first.artifact_bytes


def test_candidate_is_eligible_only_with_twenty_samples_and_five_in_each_class():
    observations = tuple(
        _observation(index, 0.1 + index / 25, index >= 15)
        for index in range(20)
    )

    candidate = build_calibration_candidate(observations)

    assert candidate.sample_count == 20
    assert candidate.positive_count == 5
    assert candidate.negative_count == 15
    assert candidate.status == "eligible_candidate"


def test_single_class_data_stays_finite_and_is_explicitly_exploratory():
    candidate = build_calibration_candidate(
        tuple(_observation(index, 0.2 + index / 20, False) for index in range(5))
    )

    assert candidate.status == "exploratory_candidate"
    assert candidate.positive_count == 0
    assert candidate.artifact["limitations"] == [
        "small_sample",
        "single_class",
        "candidate_not_used_for_inference",
    ]
    assert math.isfinite(candidate.slope)
    assert math.isfinite(candidate.intercept)
    assert all(math.isfinite(value) for value in candidate.metrics_after.values())


@pytest.mark.parametrize("score", (-0.01, 1.01, float("nan"), float("inf")))
def test_candidate_rejects_invalid_original_scores(score):
    with pytest.raises(ValueError, match="original score"):
        build_calibration_candidate((_observation(1, score, False),))


def test_candidate_artifact_is_atomically_written_and_verified(tmp_path):
    candidate = build_calibration_candidate(
        (_observation(1, 0.2, False), _observation(2, 0.8, True))
    )
    expected_sha256 = hashlib.sha256(candidate.artifact_bytes).hexdigest()

    first = write_candidate_artifact(tmp_path, candidate)
    second = write_candidate_artifact(tmp_path, candidate)

    assert first == second
    assert first.sha256 == expected_sha256
    assert first.path == tmp_path / f"{expected_sha256}.json"
    assert first.path.read_bytes() == candidate.artifact_bytes
    assert verify_candidate_artifact(first.path, first.sha256) == "verified"
    assert list(tmp_path.iterdir()) == [first.path]


def test_candidate_artifact_verifier_reports_missing_and_mismatch(tmp_path):
    missing = tmp_path / f"{'a' * 64}.json"
    assert verify_candidate_artifact(missing, "a" * 64) == "missing"

    missing.write_bytes(b"tampered")
    assert verify_candidate_artifact(missing, "a" * 64) == "mismatch"
