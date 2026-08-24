from __future__ import annotations

import hashlib
import json
import math

import pytest

from app.services.adaptive_risk import (
    ActiveCalibration,
    apply_platt_calibration,
    evaluate_activation_gate,
    load_verified_calibration,
)
from app.services.outcome_calibration import CalibrationCandidate


def _candidate(
    *,
    sample_count: int = 20,
    positive_count: int = 5,
    status: str = "eligible_candidate",
    before_brier: float = 0.24,
    after_brier: float = 0.18,
    before_log_loss: float = 0.70,
    after_log_loss: float = 0.56,
) -> CalibrationCandidate:
    artifact = {
        "artifact_schema": "daibm.platt-calibration.v2",
        "coefficients": {"intercept": -0.2, "slope": 1.3},
        "configuration": {"probability_epsilon": 1e-6},
    }
    artifact_bytes = json.dumps(
        artifact,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return CalibrationCandidate(
        dataset_sha256="1" * 64,
        sample_count=sample_count,
        positive_count=positive_count,
        negative_count=sample_count - positive_count,
        status=status,
        slope=1.3,
        intercept=-0.2,
        metrics_before={
            "brier_score": before_brier,
            "log_loss": before_log_loss,
        },
        metrics_after={
            "brier_score": after_brier,
            "log_loss": after_log_loss,
        },
        artifact=artifact,
        artifact_bytes=artifact_bytes,
    )


def test_gate_activates_only_an_integrity_verified_non_regressing_eligible_run():
    decision = evaluate_activation_gate(
        _candidate(),
        artifact_integrity="verified",
        provenances=("CONTROLLED_DEMO",) * 20,
    )

    assert decision.activate is True
    assert decision.reason == "gate_passed"
    assert decision.deployment_scope == "controlled_demo"


@pytest.mark.parametrize(
    ("candidate", "integrity", "provenances", "reason"),
    (
        (_candidate(sample_count=19, positive_count=5), "verified", ("CONTROLLED_DEMO",) * 19, "insufficient_samples"),
        (_candidate(positive_count=4), "verified", ("CONTROLLED_DEMO",) * 20, "insufficient_positive_support"),
        (_candidate(positive_count=16), "verified", ("CONTROLLED_DEMO",) * 20, "insufficient_negative_support"),
        (_candidate(status="exploratory_candidate"), "verified", ("CONTROLLED_DEMO",) * 20, "training_not_eligible"),
        (_candidate(), "mismatch", ("CONTROLLED_DEMO",) * 20, "artifact_unverified"),
        (_candidate(after_brier=0.25), "verified", ("CONTROLLED_DEMO",) * 20, "brier_regression"),
        (_candidate(after_log_loss=0.71), "verified", ("CONTROLLED_DEMO",) * 20, "log_loss_regression"),
    ),
)
def test_gate_rejects_each_unsafe_activation_boundary(
    candidate,
    integrity,
    provenances,
    reason,
):
    decision = evaluate_activation_gate(
        candidate,
        artifact_integrity=integrity,
        provenances=provenances,
    )

    assert decision.activate is False
    assert decision.reason == reason


def test_gate_never_labels_mixed_demo_data_as_externally_verified():
    decision = evaluate_activation_gate(
        _candidate(),
        artifact_integrity="verified",
        provenances=("EXTERNAL_VERIFIED",) * 19 + ("CONTROLLED_DEMO",),
    )

    assert decision.activate is True
    assert decision.deployment_scope == "mixed"


def test_verified_v2_artifact_applies_hand_checked_platt_transform(tmp_path):
    artifact = {
        "artifact_schema": "daibm.platt-calibration.v2",
        "coefficients": {"intercept": 0.0, "slope": 2.0},
        "configuration": {"probability_epsilon": 1e-6},
    }
    artifact_bytes = json.dumps(
        artifact,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    expected_sha256 = hashlib.sha256(artifact_bytes).hexdigest()
    path = tmp_path / f"{expected_sha256}.json"
    path.write_bytes(artifact_bytes)

    calibration = load_verified_calibration(
        path,
        expected_sha256=expected_sha256,
        run_id="00000000-0000-0000-0000-000000000001",
        deployment_scope="controlled_demo",
    )

    assert calibration == ActiveCalibration(
        run_id="00000000-0000-0000-0000-000000000001",
        artifact_sha256=expected_sha256,
        slope=2.0,
        intercept=0.0,
        probability_epsilon=1e-6,
        deployment_scope="controlled_demo",
    )
    assert apply_platt_calibration(0.8, calibration) == pytest.approx(
        16 / 17
    )


@pytest.mark.parametrize(
    "mutation",
    ("hash", "schema", "coefficient", "epsilon"),
)
def test_artifact_loader_rejects_corruption_or_unsafe_parameters(tmp_path, mutation):
    artifact = {
        "artifact_schema": "daibm.platt-calibration.v2",
        "coefficients": {"intercept": 0.0, "slope": 1.0},
        "configuration": {"probability_epsilon": 1e-6},
    }
    if mutation == "schema":
        artifact["artifact_schema"] = "daibm.calibration-candidate.v1"
    if mutation == "coefficient":
        artifact["coefficients"]["slope"] = "not-a-number"
    if mutation == "epsilon":
        artifact["configuration"]["probability_epsilon"] = 0.5
    artifact_bytes = json.dumps(
        artifact,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    actual_sha256 = hashlib.sha256(artifact_bytes).hexdigest()
    path = tmp_path / "artifact.json"
    path.write_bytes(artifact_bytes + (b"tampered" if mutation == "hash" else b""))

    with pytest.raises(ValueError, match="calibration artifact"):
        load_verified_calibration(
            path,
            expected_sha256=actual_sha256,
            run_id="00000000-0000-0000-0000-000000000001",
            deployment_scope="controlled_demo",
        )


@pytest.mark.parametrize("score", (-0.1, 1.1, math.nan, math.inf))
def test_platt_transform_rejects_invalid_baseline_probability(score):
    calibration = ActiveCalibration(
        run_id="run",
        artifact_sha256="1" * 64,
        slope=1.0,
        intercept=0.0,
        probability_epsilon=1e-6,
        deployment_scope="controlled_demo",
    )

    with pytest.raises(ValueError, match="baseline probability"):
        apply_platt_calibration(score, calibration)

