from __future__ import annotations

import hashlib
import json
import math
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.repositories.outcomes import OutcomeRepository
from app.services.outcome_calibration import (
    CalibrationCandidate,
    canonical_calibration_float,
)


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
    if (
        candidate.positive_count + candidate.negative_count
        != candidate.sample_count
        or candidate.distinct_score_count > candidate.sample_count
    ):
        return ActivationDecision(False, "count_evidence_mismatch", scope)
    if scope not in {"controlled_demo", "external_verified"}:
        return ActivationDecision(False, "unsupported_deployment_scope", scope)
    if artifact_integrity != "verified":
        return ActivationDecision(False, "artifact_unverified", scope)
    try:
        actual_sha256 = hashlib.sha256(candidate.artifact_bytes).hexdigest()
        if actual_sha256 != candidate.artifact_sha256:
            raise ValueError("candidate artifact hash mismatch")
        artifact = json.loads(candidate.artifact_bytes)
        if not isinstance(artifact, dict) or artifact != candidate.artifact:
            raise ValueError("candidate artifact snapshot mismatch")
    except (TypeError, ValueError, json.JSONDecodeError):
        return ActivationDecision(False, "artifact_unverified", scope)
    schema = artifact.get("artifact_schema")
    if schema == "daibm.platt-calibration.v2":
        return ActivationDecision(False, "legacy_artifact_not_activatable", scope)
    if schema != "daibm.platt-calibration.v3":
        return ActivationDecision(False, "unsupported_artifact_schema", scope)
    try:
        _validate_v3_lineage(
            artifact,
            candidate.artifact_bytes,
            deployment_scope=scope,
            expected_dataset_sha256=candidate.dataset_sha256,
        )
        dataset = artifact["dataset"]
        validation = artifact["validation"]
        coefficients = artifact["coefficients"]
        if (
            candidate.artifact_schema != artifact["artifact_schema"]
            or candidate.deployment_scope != artifact["deployment"]["scope"]
            or candidate.outcome_ids != tuple(dataset["outcome_ids"])
            or candidate.correction_heads
            != tuple(sorted(dataset["correction_heads"].items()))
            or candidate.configuration
            != tuple(sorted(artifact["configuration"].items()))
            or candidate.sample_count != dataset["sample_count"]
            or candidate.positive_count != dataset["positive_count"]
            or candidate.negative_count != dataset["negative_count"]
            or candidate.distinct_score_count != dataset["distinct_score_count"]
            or candidate.fold_assignment_sha256
            != validation["fold_assignment_sha256"]
            or candidate.status != artifact["status"]
            or candidate.metrics_before != validation["metrics_before"]
            or candidate.metrics_after != validation["metrics_after"]
            or candidate.slope != coefficients["slope"]
            or candidate.intercept != coefficients["intercept"]
        ):
            raise ValueError("candidate evidence contradicts artifact")
    except (KeyError, TypeError, ValueError):
        return ActivationDecision(False, "artifact_unverified", scope)
    if len(provenances) != candidate.sample_count:
        return ActivationDecision(False, "count_evidence_mismatch", scope)
    if candidate.distinct_score_count < 2:
        return ActivationDecision(False, "insufficient_distinct_scores", scope)
    metric_values = (
        candidate.metrics_before.get("brier_score"),
        candidate.metrics_before.get("log_loss"),
        candidate.metrics_after.get("brier_score"),
        candidate.metrics_after.get("log_loss"),
    )
    if not all(
        isinstance(value, (int, float)) and math.isfinite(float(value))
        for value in metric_values
    ):
        return ActivationDecision(False, "metrics_nonfinite", scope)
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


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _require_uuid(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("identifier is not a string")
    parsed = uuid.UUID(value)
    if value != str(parsed):
        raise ValueError("identifier is not canonical")
    return value


def _require_exact_fields(value: object, expected: set[str], name: str) -> dict:
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError(f"{name} fields are not canonical")
    return value


def _require_metric_block(value: object) -> None:
    if not isinstance(value, dict) or set(value) != {"brier_score", "log_loss"}:
        raise ValueError("calibration metric lineage is incomplete")
    if not all(_is_canonical_float(item) and item >= 0.0 for item in value.values()):
        raise ValueError("calibration metrics are invalid")
    if float(value["brier_score"]) > 1.0:
        raise ValueError("calibration Brier score is invalid")


def _is_canonical_float(value: object) -> bool:
    if type(value) is not float:
        return False
    try:
        return value == canonical_calibration_float(value)
    except ValueError:
        return False


def _validate_v3_lineage(
    artifact: dict[str, object],
    artifact_bytes: bytes,
    *,
    deployment_scope: str,
    expected_dataset_sha256: str,
) -> None:
    if _canonical_bytes(artifact) != artifact_bytes:
        raise ValueError("v3 calibration artifact is not canonical")
    _require_exact_fields(
        artifact,
        {
            "artifact_schema",
            "coefficients",
            "configuration",
            "dataset",
            "deployment",
            "diagnostics",
            "limitations",
            "model_family",
            "status",
            "training_input",
            "validation",
        },
        "calibration artifact",
    )
    if artifact.get("artifact_schema") != "daibm.platt-calibration.v3":
        raise ValueError("calibration artifact schema is invalid")
    if artifact.get("model_family") != "platt_logistic_calibration":
        raise ValueError("calibration model family is invalid")
    if artifact.get("status") != "eligible_candidate":
        raise ValueError("calibration artifact status is invalid")
    if artifact.get("training_input") != "logit(original_risk_score)":
        raise ValueError("calibration training input is invalid")
    coefficients = _require_exact_fields(
        artifact.get("coefficients"), {"intercept", "slope"}, "coefficient"
    )
    if not all(_is_canonical_float(value) for value in coefficients.values()):
        raise ValueError("calibration coefficients are invalid")
    configuration = _require_exact_fields(
        artifact.get("configuration"),
        {"epochs", "l2_penalty", "learning_rate", "probability_epsilon"},
        "configuration",
    )
    if (
        type(configuration["epochs"]) is not int
        or configuration["epochs"] < 1
        or not all(
            _is_canonical_float(configuration[field])
            for field in ("l2_penalty", "learning_rate", "probability_epsilon")
        )
        or float(configuration["l2_penalty"]) < 0.0
        or float(configuration["learning_rate"]) <= 0.0
        or not 0.0 < float(configuration["probability_epsilon"]) < 0.5
    ):
        raise ValueError("calibration configuration is invalid")
    deployment = _require_exact_fields(
        artifact.get("deployment"),
        {"gate_policy", "initial_status", "scope"},
        "deployment",
    )
    if (
        deployment.get("scope") != deployment_scope
        or deployment_scope not in {"controlled_demo", "external_verified"}
        or deployment.get("gate_policy") != "fixed_v1"
        or deployment.get("initial_status") != "not_deployed"
    ):
        raise ValueError("calibration artifact deployment scope mismatch")
    dataset = _require_exact_fields(
        artifact.get("dataset"),
        {
            "correction_heads",
            "distinct_score_count",
            "negative_count",
            "outcome_ids",
            "positive_count",
            "sample_count",
            "sha256",
        },
        "dataset",
    )
    if dataset.get("sha256") != expected_dataset_sha256:
        raise ValueError("calibration artifact dataset lineage mismatch")
    outcome_ids = dataset.get("outcome_ids")
    if not isinstance(outcome_ids, list) or not outcome_ids:
        raise ValueError("calibration outcome lineage is missing")
    canonical_outcome_ids = [_require_uuid(value) for value in outcome_ids]
    if canonical_outcome_ids != sorted(canonical_outcome_ids) or len(
        set(canonical_outcome_ids)
    ) != len(canonical_outcome_ids):
        raise ValueError("calibration outcome lineage is not canonical")
    sample_count = dataset.get("sample_count")
    positive_count = dataset.get("positive_count")
    negative_count = dataset.get("negative_count")
    if (
        type(sample_count) is not int
        or type(positive_count) is not int
        or type(negative_count) is not int
        or sample_count != len(canonical_outcome_ids)
        or sample_count < 20
        or positive_count < 5
        or negative_count < 5
        or positive_count + negative_count != sample_count
    ):
        raise ValueError("calibration count lineage is inconsistent")
    distinct_score_count = dataset.get("distinct_score_count")
    if (
        type(distinct_score_count) is not int
        or distinct_score_count < 1
        or distinct_score_count > sample_count
    ):
        raise ValueError("calibration distinct-score lineage is invalid")
    correction_heads = dataset.get("correction_heads")
    if not isinstance(correction_heads, dict) or set(correction_heads) != set(
        canonical_outcome_ids
    ):
        raise ValueError("calibration correction-head lineage is incomplete")
    for correction_head in correction_heads.values():
        if correction_head is not None:
            _require_uuid(correction_head)
    expected_limitations = ["activation_gate_required"]
    if distinct_score_count < 2:
        expected_limitations.insert(0, "insufficient_distinct_scores")
    if artifact.get("limitations") != expected_limitations:
        raise ValueError("calibration limitations are inconsistent")

    validation = _require_exact_fields(
        artifact.get("validation"),
        {
            "fold_assignment_sha256",
            "fold_assignments",
            "folds",
            "method",
            "metrics_after",
            "metrics_before",
        },
        "validation",
    )
    if validation.get("method") != "deterministic_stratified_5_fold_oof":
        raise ValueError("calibration validation method is invalid")
    assignments = validation.get("fold_assignments")
    if not isinstance(assignments, dict) or set(assignments) != set(
        canonical_outcome_ids
    ):
        raise ValueError("calibration fold assignments are incomplete")
    if not all(type(fold) is int and 0 <= fold < 5 for fold in assignments.values()):
        raise ValueError("calibration fold assignments are invalid")
    expected_fold_hash = hashlib.sha256(_canonical_bytes(assignments)).hexdigest()
    if validation.get("fold_assignment_sha256") != expected_fold_hash:
        raise ValueError("calibration fold assignment hash mismatch")
    if set(assignments.values()) != set(range(5)):
        raise ValueError("calibration fold assignment is incomplete")
    folds = validation.get("folds")
    if not isinstance(folds, list) or len(folds) != 5:
        raise ValueError("calibration fold evidence is incomplete")
    for fold_number, fold in enumerate(folds):
        fold = _require_exact_fields(
            fold,
            {"fold", "held_out_outcome_ids", "training_outcome_ids"},
            "fold evidence",
        )
        if fold.get("fold") != fold_number:
            raise ValueError("calibration fold evidence is not canonical")
        held_out = fold.get("held_out_outcome_ids")
        training = fold.get("training_outcome_ids")
        if not isinstance(held_out, list) or not isinstance(training, list):
            raise ValueError("calibration fold evidence is malformed")
        expected_held_out = [
            outcome_id
            for outcome_id in canonical_outcome_ids
            if assignments[outcome_id] == fold_number
        ]
        expected_training = [
            outcome_id
            for outcome_id in canonical_outcome_ids
            if assignments[outcome_id] != fold_number
        ]
        if (
            held_out != expected_held_out
            or training != expected_training
            or not held_out
        ):
            raise ValueError("calibration fold evidence is inconsistent")
    _require_metric_block(validation.get("metrics_before"))
    _require_metric_block(validation.get("metrics_after"))
    diagnostics = _require_exact_fields(
        artifact.get("diagnostics"), {"final_fit"}, "diagnostics"
    )
    final_fit = _require_exact_fields(
        diagnostics.get("final_fit"), {"metrics"}, "final-fit diagnostics"
    )
    _require_metric_block(final_fit.get("metrics"))


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
        if not isinstance(artifact, dict):
            raise ValueError("calibration artifact root must be an object")
        schema = artifact.get("artifact_schema")
        if schema == "daibm.platt-calibration.v2":
            if deployment_scope != "controlled_demo":
                raise ValueError("legacy v2 calibration is controlled-demo only")
        elif schema == "daibm.platt-calibration.v3":
            _validate_v3_lineage(
                artifact,
                artifact_bytes,
                deployment_scope=deployment_scope,
                expected_dataset_sha256=expected_dataset_sha256,
            )
        else:
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
