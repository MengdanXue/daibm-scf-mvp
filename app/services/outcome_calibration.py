from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass(frozen=True)
class CalibrationObservation:
    outcome_id: str
    facility_id: str
    request_id: str
    risk_assessment_id: str
    model_version_id: str | None
    risk_engine_version: str
    risk_input_sha256: str
    evidence_sha256: str
    original_score: float
    defaulted: bool
    observed_at: str
    provenance: str


@dataclass(frozen=True)
class CalibrationTrainingConfig:
    epochs: int = 800
    learning_rate: float = 0.05
    l2_penalty: float = 0.001
    probability_epsilon: float = 1e-6

    def __post_init__(self) -> None:
        if self.epochs < 1:
            raise ValueError("epochs must be positive")
        if not math.isfinite(self.learning_rate) or self.learning_rate <= 0:
            raise ValueError("learning rate must be positive and finite")
        if not math.isfinite(self.l2_penalty) or self.l2_penalty < 0:
            raise ValueError("L2 penalty must be non-negative and finite")
        if not 0 < self.probability_epsilon < 0.5:
            raise ValueError("probability epsilon must be between zero and 0.5")


@dataclass(frozen=True)
class CalibrationCandidate:
    dataset_sha256: str
    sample_count: int
    positive_count: int
    negative_count: int
    status: str
    slope: float
    intercept: float
    metrics_before: dict[str, float]
    metrics_after: dict[str, float]
    artifact: dict[str, object]
    artifact_bytes: bytes


@dataclass(frozen=True)
class CalibrationArtifact:
    path: Path
    sha256: str


@dataclass(frozen=True)
class StagedCalibrationArtifact:
    path: Path
    staged_path: Path | None
    sha256: str


@dataclass(frozen=True)
class CalibrationDatasetSummary:
    dataset_sha256: str
    sample_count: int
    positive_count: int
    negative_count: int
    metrics_before: dict[str, float] | None


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sigmoid(values: np.ndarray) -> np.ndarray:
    bounded = np.clip(values, -40.0, 40.0)
    return 1.0 / (1.0 + np.exp(-bounded))


def _metrics(labels: np.ndarray, probabilities: np.ndarray, epsilon: float) -> dict[str, float]:
    bounded = np.clip(probabilities, epsilon, 1.0 - epsilon)
    return {
        "brier_score": float(np.mean(np.square(bounded - labels))),
        "log_loss": float(
            -np.mean(
                labels * np.log(bounded)
                + (1.0 - labels) * np.log(1.0 - bounded)
            )
        ),
    }


def _observation_payload(item: CalibrationObservation) -> dict[str, object]:
    return {
        "defaulted": item.defaulted,
        "evidence_sha256": item.evidence_sha256,
        "facility_id": item.facility_id,
        "model_version_id": item.model_version_id,
        "risk_engine_version": item.risk_engine_version,
        "observed_at": item.observed_at,
        "original_score": item.original_score,
        "outcome_id": item.outcome_id,
        "provenance": item.provenance,
        "request_id": item.request_id,
        "risk_assessment_id": item.risk_assessment_id,
        "risk_input_sha256": item.risk_input_sha256,
    }


def _prepare_dataset(
    observations: tuple[CalibrationObservation, ...],
    config: CalibrationTrainingConfig,
) -> tuple[
    tuple[CalibrationObservation, ...],
    np.ndarray,
    np.ndarray,
    CalibrationDatasetSummary,
]:
    if not observations:
        raise ValueError("at least one calibration observation is required")
    ordered = tuple(sorted(observations, key=lambda item: item.outcome_id))
    if len({item.outcome_id for item in ordered}) != len(ordered):
        raise ValueError("calibration outcome identifiers must be unique")
    for item in ordered:
        if not math.isfinite(item.original_score) or not 0 <= item.original_score <= 1:
            raise ValueError("original score must be finite and between zero and one")
    raw_scores = np.asarray([item.original_score for item in ordered], dtype=np.float64)
    labels = np.asarray([int(item.defaulted) for item in ordered], dtype=np.float64)
    bounded_scores = np.clip(
        raw_scores,
        config.probability_epsilon,
        1.0 - config.probability_epsilon,
    )
    sample_count = len(ordered)
    positive_count = int(labels.sum())
    summary = CalibrationDatasetSummary(
        dataset_sha256=hashlib.sha256(
            _canonical_bytes([_observation_payload(item) for item in ordered])
        ).hexdigest(),
        sample_count=sample_count,
        positive_count=positive_count,
        negative_count=sample_count - positive_count,
        metrics_before=_metrics(labels, bounded_scores, config.probability_epsilon),
    )
    return ordered, labels, bounded_scores, summary


def summarize_calibration_observations(
    observations: tuple[CalibrationObservation, ...],
    *,
    config: CalibrationTrainingConfig | None = None,
) -> CalibrationDatasetSummary:
    active_config = config or CalibrationTrainingConfig()
    return _prepare_dataset(observations, active_config)[3]


def build_calibration_candidate(
    observations: tuple[CalibrationObservation, ...],
    *,
    config: CalibrationTrainingConfig | None = None,
) -> CalibrationCandidate:
    """Fit a deterministic Platt calibration layer for governed deployment."""

    active_config = config or CalibrationTrainingConfig()
    ordered, labels, bounded_scores, summary = _prepare_dataset(
        observations, active_config
    )
    logits = np.log(bounded_scores / (1.0 - bounded_scores))

    slope = 1.0
    intercept = 0.0
    for _ in range(active_config.epochs):
        probabilities = _sigmoid(slope * logits + intercept)
        residual = probabilities - labels
        slope_gradient = float(np.mean(residual * logits)) + (
            active_config.l2_penalty * slope
        )
        intercept_gradient = float(np.mean(residual))
        slope -= active_config.learning_rate * slope_gradient
        intercept -= active_config.learning_rate * intercept_gradient

    calibrated = _sigmoid(slope * logits + intercept)
    before = summary.metrics_before
    after = _metrics(labels, calibrated, active_config.probability_epsilon)
    if not all(
        math.isfinite(value)
        for value in (slope, intercept, *before.values(), *after.values())
    ):
        raise RuntimeError("calibration training produced non-finite values")

    sample_count = summary.sample_count
    positive_count = summary.positive_count
    negative_count = summary.negative_count
    eligible = sample_count >= 20 and positive_count >= 5 and negative_count >= 5
    limitations: list[str] = []
    if sample_count < 20:
        limitations.append("small_sample")
    if positive_count == 0 or negative_count == 0:
        limitations.append("single_class")
    elif positive_count < 5 or negative_count < 5:
        limitations.append("insufficient_class_support")
    limitations.append("activation_gate_required")

    dataset_sha256 = summary.dataset_sha256
    artifact: dict[str, object] = {
        "artifact_schema": "daibm.platt-calibration.v2",
        "coefficients": {
            "intercept": intercept,
            "slope": slope,
        },
        "configuration": {
            "epochs": active_config.epochs,
            "l2_penalty": active_config.l2_penalty,
            "learning_rate": active_config.learning_rate,
            "probability_epsilon": active_config.probability_epsilon,
        },
        "dataset": {
            "negative_count": negative_count,
            "outcome_ids": [item.outcome_id for item in ordered],
            "positive_count": positive_count,
            "sample_count": sample_count,
            "sha256": dataset_sha256,
        },
        "deployment": {
            "gate_policy": "fixed_v1",
            "initial_status": "not_deployed",
        },
        "limitations": limitations,
        "metrics": {
            "after": after,
            "before": before,
        },
        "model_family": "platt_logistic_calibration",
        "status": (
            "eligible_candidate" if eligible else "exploratory_candidate"
        ),
        "training_input": "logit(original_risk_score)",
    }
    artifact_bytes = _canonical_bytes(artifact)
    return CalibrationCandidate(
        dataset_sha256=dataset_sha256,
        sample_count=sample_count,
        positive_count=positive_count,
        negative_count=negative_count,
        status=str(artifact["status"]),
        slope=slope,
        intercept=intercept,
        metrics_before=before,
        metrics_after=after,
        artifact=artifact,
        artifact_bytes=artifact_bytes,
    )


def stage_candidate_artifact(
    root: Path,
    candidate: CalibrationCandidate,
) -> StagedCalibrationArtifact:
    """Durably stage candidate bytes without exposing a committed artifact."""

    artifact_root = Path(root)
    artifact_root.mkdir(parents=True, exist_ok=True)
    artifact_sha256 = hashlib.sha256(candidate.artifact_bytes).hexdigest()
    destination = artifact_root / f"{artifact_sha256}.json"
    if destination.is_file() and destination.read_bytes() == candidate.artifact_bytes:
        return StagedCalibrationArtifact(
            path=destination,
            staged_path=None,
            sha256=artifact_sha256,
        )

    staged_path = artifact_root / f".pending-{artifact_sha256}.json"
    if staged_path.is_file() and staged_path.read_bytes() == candidate.artifact_bytes:
        return StagedCalibrationArtifact(
            path=destination,
            staged_path=staged_path,
            sha256=artifact_sha256,
        )
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=".calibration-",
            suffix=".tmp",
            dir=artifact_root,
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(candidate.artifact_bytes)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, staged_path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return StagedCalibrationArtifact(
        path=destination,
        staged_path=staged_path,
        sha256=artifact_sha256,
    )


def publish_candidate_artifact(
    staged: StagedCalibrationArtifact,
) -> CalibrationArtifact:
    if staged.staged_path is not None:
        if verify_candidate_artifact(staged.staged_path, staged.sha256) != "verified":
            raise RuntimeError("staged calibration artifact hash mismatch")
        if verify_candidate_artifact(staged.path, staged.sha256) == "verified":
            staged.staged_path.unlink(missing_ok=True)
        else:
            os.replace(staged.staged_path, staged.path)
    if verify_candidate_artifact(staged.path, staged.sha256) != "verified":
        raise RuntimeError("published calibration artifact hash mismatch")
    return CalibrationArtifact(path=staged.path, sha256=staged.sha256)


def discard_staged_artifact(staged: StagedCalibrationArtifact | None) -> None:
    if staged is not None and staged.staged_path is not None:
        staged.staged_path.unlink(missing_ok=True)


def recover_candidate_artifact(path: Path, expected_sha256: str) -> str:
    integrity = verify_candidate_artifact(path, expected_sha256)
    if integrity != "missing":
        return integrity
    staged_path = Path(path).parent / f".pending-{expected_sha256}.json"
    if verify_candidate_artifact(staged_path, expected_sha256) != "verified":
        return verify_candidate_artifact(path, expected_sha256)
    try:
        os.replace(staged_path, path)
    except FileNotFoundError:
        # Another auditor read may have atomically completed the same recovery.
        return verify_candidate_artifact(path, expected_sha256)
    return verify_candidate_artifact(path, expected_sha256)


def write_candidate_artifact(
    root: Path,
    candidate: CalibrationCandidate,
) -> CalibrationArtifact:
    """Stage then atomically publish a candidate outside a DB transaction."""

    return publish_candidate_artifact(stage_candidate_artifact(root, candidate))


def verify_candidate_artifact(path: Path, expected_sha256: str) -> str:
    artifact_path = Path(path)
    if not artifact_path.is_file():
        return "missing"
    actual = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    return "verified" if actual == expected_sha256 else "mismatch"


__all__ = [
    "CalibrationArtifact",
    "CalibrationCandidate",
    "CalibrationDatasetSummary",
    "CalibrationObservation",
    "CalibrationTrainingConfig",
    "StagedCalibrationArtifact",
    "build_calibration_candidate",
    "summarize_calibration_observations",
    "stage_candidate_artifact",
    "publish_candidate_artifact",
    "discard_staged_artifact",
    "recover_candidate_artifact",
    "verify_candidate_artifact",
    "write_candidate_artifact",
]
