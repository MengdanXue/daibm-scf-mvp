from dataclasses import FrozenInstanceError, replace

import numpy as np
import pytest

from research.experiments.sensitivity import (
    SeedEvidence,
    summarize_runs,
    t_confidence_interval,
    threshold_rows,
)


def _seed_evidence(seed: int, roc_auc: float) -> SeedEvidence:
    return SeedEvidence(
        seed=seed,
        dataset_sha256=f"dataset-{seed}",
        labels=(0, 1),
        probabilities={
            "tgnn": (0.2, 0.8),
            "xgboost": (0.3, 0.7),
        },
        metrics={
            "tgnn": {
                "roc_auc": roc_auc,
                "pr_auc": roc_auc - 0.1,
                "brier_score": 1.0 - roc_auc,
                "f1": 0.5,
                "confusion_matrix": [[1, 0], [0, 1]],
            },
            "xgboost": {
                "roc_auc": roc_auc - 0.05,
                "pr_auc": roc_auc - 0.15,
                "brier_score": 1.05 - roc_auc,
                "f1": 0.4,
                "confusion_matrix": [[1, 0], [0, 1]],
            },
        },
        artifact_sha256={"tgnn": "a" * 64, "xgboost": "b" * 64},
    )


def test_summary_uses_sample_sd_and_t_interval():
    runs = [
        _seed_evidence(seed, roc_auc=value)
        for seed, value in enumerate((0.60, 0.62, 0.64, 0.66, 0.68), 1)
    ]

    summary = summarize_runs(runs)["tgnn"]["roc_auc"]

    assert summary["n"] == 5
    assert summary["mean"] == pytest.approx(0.64)
    assert summary["sample_sd"] == pytest.approx(0.0316227766)
    assert summary["ci95_low"] == pytest.approx(0.6007351368)
    assert summary["ci95_high"] == pytest.approx(0.6792648632)


def test_summary_only_reports_metrics_with_valid_cross_seed_intervals():
    runs = [_seed_evidence(1, 0.60), _seed_evidence(2, 0.70)]

    summary = summarize_runs(runs)

    assert set(summary) == {"tgnn", "xgboost"}
    assert set(summary["tgnn"]) == {"roc_auc", "pr_auc", "brier_score"}


def test_threshold_rows_are_fixed_and_do_not_select_an_optimum():
    rows = threshold_rows(
        np.array([0, 1, 1, 0]),
        np.array([0.35, 0.45, 0.70, 0.80]),
    )

    assert [row["threshold"] for row in rows] == [
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
    ]
    assert all(
        not ({"selected", "best", "optimal"} & row.keys()) for row in rows
    )
    by_threshold = {row["threshold"]: row for row in rows}
    assert by_threshold[0.40]["confusion_matrix"] == [[1, 1], [0, 2]]
    assert by_threshold[0.40]["precision"] == pytest.approx(2 / 3)
    assert by_threshold[0.40]["recall"] == pytest.approx(1.0)
    assert by_threshold[0.40]["f1"] == pytest.approx(0.8)
    assert by_threshold[0.50]["confusion_matrix"] == [[1, 1], [1, 1]]
    assert by_threshold[0.50]["precision"] == pytest.approx(0.5)
    assert by_threshold[0.50]["recall"] == pytest.approx(0.5)
    assert by_threshold[0.50]["f1"] == pytest.approx(0.5)
    assert by_threshold[0.75]["confusion_matrix"] == [[1, 1], [2, 0]]
    assert by_threshold[0.75]["precision"] == pytest.approx(0.0)
    assert by_threshold[0.75]["recall"] == pytest.approx(0.0)
    assert by_threshold[0.75]["f1"] == pytest.approx(0.0)


@pytest.mark.parametrize(
    "values",
    ([0.5], [0.5, float("nan")], [0.5, float("inf")]),
)
def test_t_interval_rejects_fewer_than_two_finite_observations(values):
    with pytest.raises(ValueError, match="finite observations"):
        t_confidence_interval(values)


@pytest.mark.parametrize("confidence", (0.0, 1.0, -0.1, 1.1))
def test_t_interval_rejects_invalid_confidence(confidence):
    with pytest.raises(ValueError, match="confidence"):
        t_confidence_interval([0.5, 0.6], confidence=confidence)


def test_seed_evidence_is_immutable():
    evidence = _seed_evidence(1, 0.60)

    with pytest.raises(FrozenInstanceError):
        evidence.seed = 2
    with pytest.raises(TypeError):
        evidence.probabilities["tgnn"] = (0.1, 0.9)
    with pytest.raises(TypeError):
        evidence.metrics["tgnn"]["roc_auc"] = 0.99
    with pytest.raises(TypeError):
        evidence.metrics["tgnn"]["confusion_matrix"][0][0] = 99
    with pytest.raises(TypeError):
        evidence.artifact_sha256["tgnn"] = "c" * 64


def test_summary_rejects_a_missing_required_metric():
    first = _seed_evidence(1, 0.60)
    second = _seed_evidence(2, 0.70)
    incomplete = {
        model: dict(metrics) for model, metrics in second.metrics.items()
    }
    incomplete["tgnn"].pop("pr_auc")

    with pytest.raises(ValueError, match="tgnn.*pr_auc"):
        summarize_runs([first, replace(second, metrics=incomplete)])


def test_summary_rejects_a_non_numeric_required_metric():
    first = _seed_evidence(1, 0.60)
    second = _seed_evidence(2, 0.70)
    invalid = {model: dict(metrics) for model, metrics in second.metrics.items()}
    invalid["tgnn"]["roc_auc"] = "not-numeric"

    with pytest.raises(ValueError, match="tgnn.*roc_auc.*numeric"):
        summarize_runs([first, replace(second, metrics=invalid)])
