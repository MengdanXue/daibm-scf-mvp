from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path

from app.services.outcome_calibration import CalibrationCandidate


@dataclass(frozen=True)
class ActivationDecision:
    activate: bool
    reason: str
    deployment_scope: str


@dataclass(frozen=True)
class ActiveCalibration:
    run_id: str
    artifact_sha256: str
    slope: float
    intercept: float
    probability_epsilon: float
    deployment_scope: str


def _deployment_scope(provenances: tuple[str, ...]) -> str:
    normalized = set(provenances)
    if normalized == {"EXTERNAL_VERIFIED"}:
        return "external_verified"
    if normalized == {"CONTROLLED_DEMO"}:
        return "controlled_demo"
    return "mixed"


def evaluate_activation_gate(
    candidate: CalibrationCandidate,
    *,
    artifact_integrity: str,
    provenances: tuple[str, ...],
) -> ActivationDecision:
    scope = _deployment_scope(provenances)
    if candidate.status != "eligible_candidate":
        return ActivationDecision(False, "training_not_eligible", scope)
    if candidate.sample_count < 20:
        return ActivationDecision(False, "insufficient_samples", scope)
    if candidate.positive_count < 5:
        return ActivationDecision(False, "insufficient_positive_support", scope)
    if candidate.negative_count < 5:
        return ActivationDecision(False, "insufficient_negative_support", scope)
    if artifact_integrity != "verified":
        return ActivationDecision(False, "artifact_unverified", scope)
    if candidate.metrics_after["brier_score"] > candidate.metrics_before["brier_score"]:
        return ActivationDecision(False, "brier_regression", scope)
    if candidate.metrics_after["log_loss"] > candidate.metrics_before["log_loss"]:
        return ActivationDecision(False, "log_loss_regression", scope)
    return ActivationDecision(True, "gate_passed", scope)


def load_verified_calibration(
    path: Path,
    *,
    expected_sha256: str,
    run_id: str,
    deployment_scope: str,
) -> ActiveCalibration:
    try:
        artifact_bytes = Path(path).read_bytes()
        if hashlib.sha256(artifact_bytes).hexdigest() != expected_sha256:
            raise ValueError("calibration artifact hash mismatch")
        artifact = json.loads(artifact_bytes)
        if artifact.get("artifact_schema") != "daibm.platt-calibration.v2":
            raise ValueError("calibration artifact schema is not deployable")
        coefficients = artifact["coefficients"]
        configuration = artifact["configuration"]
        slope = float(coefficients["slope"])
        intercept = float(coefficients["intercept"])
        epsilon = float(configuration["probability_epsilon"])
        if not math.isfinite(slope) or not math.isfinite(intercept):
            raise ValueError("calibration artifact coefficients are not finite")
        if not 0 < epsilon < 0.5:
            raise ValueError("calibration artifact epsilon is invalid")
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("calibration artifact is invalid") from error
    return ActiveCalibration(
        run_id=run_id,
        artifact_sha256=expected_sha256,
        slope=slope,
        intercept=intercept,
        probability_epsilon=epsilon,
        deployment_scope=deployment_scope,
    )


def apply_platt_calibration(
    baseline_probability: float,
    calibration: ActiveCalibration,
) -> float:
    if (
        not math.isfinite(baseline_probability)
        or baseline_probability < 0
        or baseline_probability > 1
    ):
        raise ValueError("baseline probability must be finite and between zero and one")
    epsilon = calibration.probability_epsilon
    bounded = min(1.0 - epsilon, max(epsilon, baseline_probability))
    logit = math.log(bounded / (1.0 - bounded))
    transformed = calibration.slope * logit + calibration.intercept
    transformed = min(40.0, max(-40.0, transformed))
    return 1.0 / (1.0 + math.exp(-transformed))


__all__ = [
    "ActivationDecision",
    "ActiveCalibration",
    "apply_platt_calibration",
    "evaluate_activation_gate",
    "load_verified_calibration",
]
