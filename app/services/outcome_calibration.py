from __future__ import annotations

import hashlib
import math
import os
import re
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import cast

import numpy as np

from app.canonical import canonical_bytes


def canonical_calibration_float(value: object) -> float:
    """Return the platform-independent 15-significant-digit numeric boundary."""

    if type(value) not in (int, float) or not math.isfinite(float(cast(int | float, value))):
        raise ValueError("calibration number must be finite")
    canonical = float(format(float(cast(int | float, value)), ".15g"))
    return 0.0 if canonical == 0.0 else canonical


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
    correction_head_id: str | None = None


@dataclass(frozen=True)
class CalibrationTrainingConfig:
    epochs: int = 800
    learning_rate: float = 0.05
    l2_penalty: float = 0.001
    probability_epsilon: float = 1e-6

    def __post_init__(self) -> None:
        if type(self.epochs) is not int or self.epochs < 1:
            raise ValueError("epochs must be positive")
        try:
            learning_rate = canonical_calibration_float(self.learning_rate)
        except ValueError as error:
            raise ValueError("learning rate must be positive and finite") from error
        if learning_rate <= 0:
            raise ValueError("learning rate must be positive and finite")
        try:
            l2_penalty = canonical_calibration_float(self.l2_penalty)
        except ValueError as error:
            raise ValueError("L2 penalty must be non-negative and finite") from error
        if l2_penalty < 0:
            raise ValueError("L2 penalty must be non-negative and finite")
        try:
            probability_epsilon = canonical_calibration_float(self.probability_epsilon)
        except ValueError as error:
            raise ValueError("probability epsilon must be between zero and 0.5") from error
        if not 0 < probability_epsilon < 0.5:
            raise ValueError("probability epsilon must be between zero and 0.5")
        object.__setattr__(self, "learning_rate", learning_rate)
        object.__setattr__(self, "l2_penalty", l2_penalty)
        object.__setattr__(self, "probability_epsilon", probability_epsilon)


def canonical_training_configuration(
    config: CalibrationTrainingConfig,
) -> dict[str, float | int]:
    """Return the one canonical configuration persisted in artifacts and runs."""

    return {
        "epochs": config.epochs,
        "l2_penalty": canonical_calibration_float(config.l2_penalty),
        "learning_rate": canonical_calibration_float(config.learning_rate),
        "probability_epsilon": canonical_calibration_float(config.probability_epsilon),
    }


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
    distinct_score_count: int = 0
    fold_assignment_sha256: str = ""
    artifact_sha256: str = ""
    outcome_ids: tuple[str, ...] = ()
    correction_heads: tuple[tuple[str, str | None], ...] = ()
    configuration: tuple[tuple[str, object], ...] = ()
    artifact_schema: str = ""
    deployment_scope: str = ""


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
    """A dataset summary whose pre-calibration metrics are known.

    Splitting this from the metric-less fallback keeps ``metrics_before``
    non-optional everywhere it is read. It was previously widened to
    ``| None`` purely to let ``OutcomeService._fallback_summary`` reuse the
    type, which made every read a latent AttributeError -- inside a block
    that swallows exceptions into a generic "training failed" code.
    """

    dataset_sha256: str
    sample_count: int
    positive_count: int
    negative_count: int
    metrics_before: dict[str, float]


@dataclass(frozen=True)
class UnmeasuredCalibrationDataset:
    """A dataset counted but never scored, recorded when training failed."""

    dataset_sha256: str
    sample_count: int
    positive_count: int
    negative_count: int
    metrics_before: None = None


def _serialized_metrics(metrics: dict[str, float]) -> dict[str, float]:
    return {key: canonical_calibration_float(value) for key, value in metrics.items()}


def _sigmoid(values: np.ndarray) -> np.ndarray:
    bounded = np.clip(values, -40.0, 40.0)
    return 1.0 / (1.0 + np.exp(-bounded))


def _metrics(labels: np.ndarray, probabilities: np.ndarray, epsilon: float) -> dict[str, float]:
    if labels.ndim != 1 or probabilities.ndim != 1 or labels.shape != probabilities.shape:
        raise ValueError("labels and probabilities must be equal-length vectors")
    if len(labels) == 0:
        raise ValueError("at least one probability is required")
    if not math.isfinite(epsilon) or not 0 < epsilon < 0.5:
        raise ValueError("probability epsilon must be finite and between zero and 0.5")
    if not np.all(np.isfinite(labels)) or not np.all(np.isin(labels, (0.0, 1.0))):
        raise ValueError("labels must be finite binary values")
    if (
        not np.all(np.isfinite(probabilities))
        or np.any(probabilities < 0.0)
        or np.any(probabilities > 1.0)
    ):
        raise ValueError("probabilities must be finite and between zero and one")
    bounded = np.clip(probabilities, epsilon, 1.0 - epsilon)
    metrics = {
        "brier_score": float(np.mean(np.square(bounded - labels))),
        "log_loss": float(
            -np.mean(labels * np.log(bounded) + (1.0 - labels) * np.log(1.0 - bounded))
        ),
    }
    if not all(math.isfinite(value) for value in metrics.values()):
        raise RuntimeError("calibration metrics are not finite")
    return metrics


def _observation_payload(item: CalibrationObservation) -> dict[str, object]:
    return {
        "correction_head_id": item.correction_head_id,
        "defaulted": item.defaulted,
        "evidence_sha256": item.evidence_sha256,
        "facility_id": item.facility_id,
        "model_version_id": item.model_version_id,
        "risk_engine_version": item.risk_engine_version,
        "observed_at": _canonical_observed_at(item.observed_at),
        "original_score": item.original_score,
        "outcome_id": item.outcome_id,
        "provenance": item.provenance,
        "request_id": item.request_id,
        "risk_assessment_id": item.risk_assessment_id,
        "risk_input_sha256": item.risk_input_sha256,
    }


_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_SUPPORTED_PROVENANCES = {"CONTROLLED_DEMO", "EXTERNAL_VERIFIED"}


def _validate_uuid(value: str, field: str) -> None:
    try:
        parsed = uuid.UUID(value)
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError(f"{field} must be a UUID") from error
    if value != str(parsed):
        raise ValueError(f"{field} must use canonical UUID format")


def _canonical_observed_at(value: str) -> str:
    try:
        observed_at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError("observed timestamp must be ISO-8601") from error
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("observed timestamp must be timezone-aware")
    return observed_at.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _validate_observations(
    observations: tuple[CalibrationObservation, ...],
) -> tuple[CalibrationObservation, ...]:
    if not observations:
        raise ValueError("at least one calibration observation is required")
    ordered = tuple(sorted(observations, key=lambda item: item.outcome_id))
    if len({item.outcome_id for item in ordered}) != len(ordered):
        raise ValueError("calibration outcome identifiers must be unique")
    for item in ordered:
        _validate_uuid(item.outcome_id, "outcome identifier")
        _validate_uuid(item.facility_id, "facility identifier")
        _validate_uuid(item.request_id, "request identifier")
        _validate_uuid(item.risk_assessment_id, "risk assessment identifier")
        if item.model_version_id is not None:
            _validate_uuid(item.model_version_id, "model version identifier")
        if item.correction_head_id is not None:
            _validate_uuid(item.correction_head_id, "correction head identifier")
        if not _SHA256_PATTERN.fullmatch(item.risk_input_sha256):
            raise ValueError("risk input SHA-256 must be lowercase hexadecimal")
        if not _SHA256_PATTERN.fullmatch(item.evidence_sha256):
            raise ValueError("evidence SHA-256 must be lowercase hexadecimal")
        if not item.risk_engine_version.strip():
            raise ValueError("risk engine version must not be empty")
        if type(item.defaulted) is not bool:
            raise ValueError("defaulted label must be boolean")
        if item.provenance not in _SUPPORTED_PROVENANCES:
            raise ValueError("calibration provenance is unsupported")
        _canonical_observed_at(item.observed_at)
        if not math.isfinite(item.original_score) or not 0 <= item.original_score <= 1:
            raise ValueError("original score must be finite and between zero and one")
    return ordered


def assign_stratified_folds(
    observations: tuple[CalibrationObservation, ...],
    folds: int = 5,
) -> dict[str, int]:
    """Assign canonical, label-stratified folds without input-order dependence."""

    if folds != 5:
        raise ValueError("calibration validation requires exactly five folds")
    ordered = _validate_observations(observations)
    if len(ordered) < 20:
        raise ValueError("fold assignment requires at least 20 observations")
    class_counts = {
        label: sum(row.defaulted is label for row in ordered) for label in (False, True)
    }
    if min(class_counts.values()) < folds:
        raise ValueError("fold assignment requires five observations per class")
    assignment: dict[str, int] = {}
    for label in (False, True):
        label_rows = sorted(
            (row for row in ordered if row.defaulted is label),
            key=lambda row: (_canonical_observed_at(row.observed_at), row.outcome_id),
        )
        for index, row in enumerate(label_rows):
            assignment[row.outcome_id] = index % folds
    if set(assignment.values()) != set(range(folds)):
        raise ValueError("every calibration fold must be nonempty")
    return assignment


def _prepare_dataset(
    observations: tuple[CalibrationObservation, ...],
    config: CalibrationTrainingConfig,
) -> tuple[
    tuple[CalibrationObservation, ...],
    np.ndarray,
    np.ndarray,
    CalibrationDatasetSummary,
]:
    ordered = _validate_observations(observations)
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
            canonical_bytes([_observation_payload(item) for item in ordered])
        ).hexdigest(),
        sample_count=sample_count,
        positive_count=positive_count,
        negative_count=sample_count - positive_count,
        metrics_before=_metrics(labels, bounded_scores, config.probability_epsilon),
    )
    return ordered, labels, bounded_scores, summary


def _fit_platt(
    scores: np.ndarray,
    labels: np.ndarray,
    config: CalibrationTrainingConfig,
    *,
    training_outcome_ids: tuple[str, ...] = (),
) -> tuple[float, float]:
    # Outcome identities are an audit seam only; fitting remains numeric and
    # deterministic. Production callers provide the exact training partition.
    del training_outcome_ids
    if len(scores) != len(labels) or len(scores) == 0:
        raise ValueError("calibration fit requires equal non-empty vectors")
    if set(labels.tolist()) != {0.0, 1.0}:
        raise ValueError("calibration fit requires both outcome classes")
    bounded = np.clip(
        scores,
        config.probability_epsilon,
        1.0 - config.probability_epsilon,
    )
    logits = np.log(bounded / (1.0 - bounded))
    slope = 1.0
    intercept = 0.0
    for _ in range(config.epochs):
        residual = _sigmoid(slope * logits + intercept) - labels
        slope -= config.learning_rate * (
            float(np.mean(residual * logits)) + config.l2_penalty * slope
        )
        intercept -= config.learning_rate * float(np.mean(residual))
    if not math.isfinite(slope) or not math.isfinite(intercept):
        raise RuntimeError("calibration fitting produced non-finite coefficients")
    return slope, intercept


def _apply_coefficients(
    scores: np.ndarray,
    slope: float,
    intercept: float,
    epsilon: float,
) -> np.ndarray:
    bounded = np.clip(scores, epsilon, 1.0 - epsilon)
    logits = np.log(bounded / (1.0 - bounded))
    probabilities = _sigmoid(slope * logits + intercept)
    if not np.all(np.isfinite(probabilities)):
        raise RuntimeError("calibration prediction produced non-finite values")
    return probabilities


def _deployment_scope(observations: tuple[CalibrationObservation, ...]) -> str:
    provenances = {row.provenance for row in observations}
    if provenances == {"CONTROLLED_DEMO"}:
        return "controlled_demo"
    if provenances == {"EXTERNAL_VERIFIED"}:
        return "external_verified"
    return "mixed"


def summarize_calibration_observations(
    observations: tuple[CalibrationObservation, ...],
    *,
    config: CalibrationTrainingConfig | None = None,
) -> CalibrationDatasetSummary:
    active_config = config or CalibrationTrainingConfig()
    return _prepare_dataset(observations, active_config)[3]


TEMPORAL_POLICY = {
    "training_fraction": 0.7,
    "minimum_training_count": 20,
    "minimum_validation_count": 10,
    "minimum_distinct_training_scores": 5,
    "minimum_training_score_span": 0.05,
    "minimum_class_count_per_partition": 2,
    "metric_tolerance": 1e-12,
    "boundary_selection": "nearest_size_only_earlier_tie",
}


def temporal_partition(
    observations: tuple[CalibrationObservation, ...],
) -> tuple[tuple[CalibrationObservation, ...], tuple[CalibrationObservation, ...]]:
    ordered = tuple(
        sorted(
            _validate_observations(observations),
            key=lambda row: (_canonical_observed_at(row.observed_at), row.outcome_id),
        )
    )
    boundaries = [
        index
        for index in range(20, len(ordered) - 9)
        if _canonical_observed_at(ordered[index - 1].observed_at)
        < _canonical_observed_at(ordered[index].observed_at)
    ]
    if not boundaries:
        raise ValueError("temporal_insufficient_partition_sizes")
    # Integer arithmetic makes ties exact; labels and score quality are not consulted.
    boundary = min(boundaries, key=lambda index: (abs(10 * index - 7 * len(ordered)), index))
    training, validation = ordered[:boundary], ordered[boundary:]
    scores = [row.original_score for row in training]
    if len(set(scores)) < 5:
        raise ValueError("temporal_insufficient_distinct_scores")
    if Decimal(str(max(scores))) - Decimal(str(min(scores))) < Decimal("0.05"):
        raise ValueError("temporal_insufficient_score_span")
    for name, rows in (("training", training), ("validation", validation)):
        if min(sum(row.defaulted is label for row in rows) for label in (False, True)) < 2:
            raise ValueError(f"temporal_insufficient_{name}_class_support")
    return training, validation


def partition_summary(rows: tuple[CalibrationObservation, ...]) -> dict[str, int | float]:
    scores = [row.original_score for row in rows]
    return {
        "sample_count": len(rows),
        "positive_count": sum(row.defaulted for row in rows),
        "negative_count": sum(not row.defaulted for row in rows),
        "distinct_score_count": len(set(scores)),
        "score_span": canonical_calibration_float(max(scores) - min(scores)),
    }


def build_calibration_candidate(
    observations: tuple[CalibrationObservation, ...],
    *,
    config: CalibrationTrainingConfig | None = None,
) -> CalibrationCandidate:
    """Fit only the earlier partition and evaluate only later unseen observations."""
    active_config = config or CalibrationTrainingConfig()
    ordered, _, _, summary = _prepare_dataset(observations, active_config)
    training, validation = temporal_partition(ordered)
    train_scores = np.asarray([row.original_score for row in training])
    train_labels = np.asarray([int(row.defaulted) for row in training])
    validation_scores = np.asarray([row.original_score for row in validation])
    validation_labels = np.asarray([int(row.defaulted) for row in validation])
    slope, intercept = _fit_platt(
        train_scores,
        train_labels,
        active_config,
        training_outcome_ids=tuple(row.outcome_id for row in training),
    )
    slope = canonical_calibration_float(slope)
    intercept = canonical_calibration_float(intercept)
    probabilities = _apply_coefficients(
        validation_scores, slope, intercept, active_config.probability_epsilon
    )
    before = _serialized_metrics(
        _metrics(validation_labels, validation_scores, active_config.probability_epsilon)
    )
    after = _serialized_metrics(
        _metrics(validation_labels, probabilities, active_config.probability_epsilon)
    )
    training_metrics = _serialized_metrics(
        _metrics(
            train_labels,
            _apply_coefficients(train_scores, slope, intercept, active_config.probability_epsilon),
            active_config.probability_epsilon,
        )
    )
    evidence = {
        "method": "chronological_holdout_70_30",
        "policy": dict(TEMPORAL_POLICY),
        "training_outcome_ids": [row.outcome_id for row in training],
        "validation_outcome_ids": [row.outcome_id for row in validation],
        "training_cutoff": _canonical_observed_at(training[-1].observed_at),
        "validation_start": _canonical_observed_at(validation[0].observed_at),
        "training_summary": partition_summary(training),
        "validation_summary": partition_summary(validation),
        "metrics_before": before,
        "metrics_after": after,
    }
    artifact: dict[str, object] = {
        "artifact_schema": "daibm.platt-calibration.v4",
        "coefficients": {"intercept": intercept, "slope": slope},
        "configuration": canonical_training_configuration(active_config),
        "dataset": {
            "correction_heads": {row.outcome_id: row.correction_head_id for row in ordered},
            "distinct_score_count": len(set(row.original_score for row in ordered)),
            "negative_count": summary.negative_count,
            "positive_count": summary.positive_count,
            "sample_count": summary.sample_count,
            "outcome_ids": [row.outcome_id for row in ordered],
            "sha256": summary.dataset_sha256,
            "observations": [_observation_payload(row) for row in ordered],
        },
        "deployment": {
            "gate_policy": "temporal_v1",
            "initial_status": "not_deployed",
            "scope": _deployment_scope(ordered),
            "external_verified_basis": "human_declaration_not_cryptographic_provenance",
        },
        "diagnostics": {"training_fit": {"metrics": training_metrics}},
        "limitations": ["activation_gate_required"],
        "model_family": "platt_logistic_calibration",
        "status": "eligible_candidate",
        "training_input": "logit(original_risk_score)",
        "validation": evidence,
    }
    artifact_bytes = canonical_bytes(artifact)
    return CalibrationCandidate(
        dataset_sha256=summary.dataset_sha256,
        sample_count=summary.sample_count,
        positive_count=summary.positive_count,
        negative_count=summary.negative_count,
        status="eligible_candidate",
        slope=slope,
        intercept=intercept,
        metrics_before=dict(before),
        metrics_after=dict(after),
        artifact=artifact,
        artifact_bytes=artifact_bytes,
        distinct_score_count=len(set(row.original_score for row in ordered)),
        artifact_sha256=hashlib.sha256(artifact_bytes).hexdigest(),
        outcome_ids=tuple(row.outcome_id for row in ordered),
        correction_heads=tuple((row.outcome_id, row.correction_head_id) for row in ordered),
        configuration=tuple(sorted(canonical_training_configuration(active_config).items())),
        artifact_schema="daibm.platt-calibration.v4",
        deployment_scope=_deployment_scope(ordered),
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
    "canonical_calibration_float",
    "canonical_training_configuration",
    "StagedCalibrationArtifact",
    "assign_stratified_folds",
    "build_calibration_candidate",
    "summarize_calibration_observations",
    "stage_candidate_artifact",
    "publish_candidate_artifact",
    "discard_staged_artifact",
    "recover_candidate_artifact",
    "verify_candidate_artifact",
    "write_candidate_artifact",
]
