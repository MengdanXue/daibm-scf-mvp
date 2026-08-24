from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Sequence, TypedDict

import numpy as np
from scipy import stats

from research.training.metrics import evaluate_binary_predictions


@dataclass(frozen=True)
class SeedEvidence:
    seed: int
    dataset_sha256: str
    labels: tuple[int, ...]
    probabilities: Mapping[str, tuple[float, ...]]
    metrics: Mapping[str, Mapping[str, Any]]
    artifact_sha256: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "labels", tuple(self.labels))
        object.__setattr__(
            self,
            "probabilities",
            MappingProxyType(
                {
                    model: tuple(values)
                    for model, values in self.probabilities.items()
                }
            ),
        )
        object.__setattr__(
            self,
            "metrics",
            MappingProxyType(
                {
                    model: _freeze_value(values)
                    for model, values in self.metrics.items()
                }
            ),
        )
        object.__setattr__(
            self,
            "artifact_sha256",
            MappingProxyType(dict(self.artifact_sha256)),
        )


class MetricSummary(TypedDict):
    n: int
    mean: float
    sample_sd: float
    ci95_low: float
    ci95_high: float


_SUMMARY_METRICS = ("roc_auc", "pr_auc", "brier_score")
_REPORTING_THRESHOLDS = (
    0.10,
    0.20,
    0.30,
    0.40,
    0.50,
    0.60,
    0.70,
    0.75,
    0.80,
    0.90,
)


def _freeze_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {key: _freeze_value(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_value(item) for item in value)
    return value


def t_confidence_interval(
    values: Sequence[float], confidence: float = 0.95
) -> tuple[float, float]:
    sample = np.asarray(values, dtype=np.float64).reshape(-1)
    if sample.size < 2 or not np.all(np.isfinite(sample)):
        raise ValueError("at least two finite observations are required")
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be between zero and one")
    mean = float(sample.mean())
    quantile = stats.t.ppf((1.0 + confidence) / 2.0, sample.size - 1)
    margin = float(quantile * stats.sem(sample))
    return mean - margin, mean + margin


def summarize_runs(
    runs: Sequence[SeedEvidence],
) -> dict[str, dict[str, MetricSummary]]:
    if len(runs) < 2:
        raise ValueError("at least two runs are required")

    models = tuple(runs[0].metrics)
    expected_models = set(models)
    for run in runs:
        if set(run.metrics) != expected_models:
            raise ValueError("every run must report the same models")

    summaries: dict[str, dict[str, MetricSummary]] = {}
    for model in models:
        model_summary: dict[str, MetricSummary] = {}
        for metric in _SUMMARY_METRICS:
            values: list[float] = []
            for run in runs:
                if metric not in run.metrics[model]:
                    raise ValueError(
                        f"{model}.{metric} is a required metric"
                    )
                try:
                    value = float(run.metrics[model][metric])
                except (TypeError, ValueError) as error:
                    raise ValueError(
                        f"{model}.{metric} must be numeric"
                    ) from error
                if not np.isfinite(value):
                    raise ValueError(f"{model}.{metric} must be finite")
                values.append(value)
            low, high = t_confidence_interval(values)
            model_summary[metric] = {
                "n": len(values),
                "mean": float(np.mean(values)),
                "sample_sd": float(np.std(values, ddof=1)),
                "ci95_low": low,
                "ci95_high": high,
            }
        summaries[model] = model_summary
    return summaries


def threshold_rows(
    labels: np.ndarray, probabilities: np.ndarray
) -> list[dict[str, Any]]:
    return [
        {
            "threshold": threshold,
            **evaluate_binary_predictions(
                labels, probabilities, threshold=threshold
            ),
        }
        for threshold in _REPORTING_THRESHOLDS
    ]
