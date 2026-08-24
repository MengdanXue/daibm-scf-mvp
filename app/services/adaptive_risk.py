from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.repositories.outcomes import OutcomeRepository
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


@dataclass(frozen=True)
class AdaptiveRiskResult:
    raw_score: float
    final_score: float
    calibration_run_id: str | None
    deployment_scope: str | None
    fallback_code: str | None
    attempted_calibration_run_id: str | None = None


class AdaptiveRiskInferenceService:
    def __init__(self, repository: OutcomeRepository | None = None) -> None:
        self.repository = repository or OutcomeRepository()

    def assess(self, session: Any, baseline_probability: float) -> AdaptiveRiskResult:
        active = self.repository.get_active_run(session)
        if active is None:
            return AdaptiveRiskResult(
                raw_score=baseline_probability,
                final_score=baseline_probability,
                calibration_run_id=None,
                deployment_scope=None,
                fallback_code=None,
            )
        if active.artifact_locator is None or active.artifact_sha256 is None:
            return self._fallback(
                baseline_probability,
                str(active.calibration_run_id),
            )
        try:
            calibration = load_verified_calibration(
                Path(active.artifact_locator),
                expected_sha256=active.artifact_sha256,
                run_id=str(active.calibration_run_id),
                deployment_scope=active.deployment_scope,
                expected_dataset_sha256=active.dataset_sha256,
            )
            final_score = apply_platt_calibration(
                baseline_probability,
                calibration,
            )
        except ValueError:
            return self._fallback(
                baseline_probability,
                str(active.calibration_run_id),
            )
        return AdaptiveRiskResult(
            raw_score=baseline_probability,
            final_score=final_score,
            calibration_run_id=str(active.calibration_run_id),
            deployment_scope=active.deployment_scope,
            fallback_code=None,
        )

    @staticmethod
    def _fallback(
        baseline_probability: float,
        attempted_run_id: str,
    ) -> AdaptiveRiskResult:
        return AdaptiveRiskResult(
            raw_score=baseline_probability,
            final_score=baseline_probability,
            calibration_run_id=None,
            deployment_scope=None,
            fallback_code="active_artifact_invalid",
            attempted_calibration_run_id=attempted_run_id,
        )


def resolve_deployment_scope(provenances: tuple[str, ...]) -> str:
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
    scope = resolve_deployment_scope(provenances)
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


def is_strictly_newer_candidate(
    candidate_sample_count: int,
    *,
    active_sample_count: int,
) -> bool:
    return candidate_sample_count > active_sample_count


def load_verified_calibration(
    path: Path,
    *,
    expected_sha256: str,
    run_id: str,
    deployment_scope: str,
    expected_dataset_sha256: str,
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
        dataset = artifact["dataset"]
        if dataset.get("sha256") != expected_dataset_sha256:
            raise ValueError("calibration artifact dataset lineage mismatch")
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
    "AdaptiveRiskInferenceService",
    "AdaptiveRiskResult",
    "apply_platt_calibration",
    "evaluate_activation_gate",
    "is_strictly_newer_candidate",
    "load_verified_calibration",
    "resolve_deployment_scope",
]
