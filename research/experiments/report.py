from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from research.experiments.sensitivity import SeedEvidence, summarize_runs, threshold_rows


METRIC_COLUMNS = (
    "seed",
    "model",
    "roc_auc",
    "pr_auc",
    "f1",
    "precision",
    "recall",
    "brier_score",
    "tn",
    "fp",
    "fn",
    "tp",
    "dataset_sha256",
    "artifact_sha256",
)
THRESHOLD_COLUMNS = (
    "seed",
    "model",
    "threshold",
    *METRIC_COLUMNS[2:],
)


def verified_seeds(pack: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    if pack.get("status") != "verified":
        raise ValueError("figure input must be a verified sensitivity pack")
    if pack.get("provenance") != "2026_EXPLORATORY_SENSITIVITY":
        raise ValueError("sensitivity provenance is invalid")
    seeds = pack.get("seeds")
    if not isinstance(seeds, Sequence) or isinstance(seeds, (str, bytes)) or len(seeds) < 2:
        raise ValueError("at least two verified seeds are required")
    return tuple(seeds)


def _matrix_cells(metrics: Mapping[str, Any]) -> dict[str, int]:
    matrix = np.asarray(metrics["confusion_matrix"], dtype=np.int64)
    if matrix.shape != (2, 2) or np.any(matrix < 0):
        raise ValueError("confusion matrix must be a finite non-negative 2 by 2 table")
    return {
        "tn": int(matrix[0, 0]),
        "fp": int(matrix[0, 1]),
        "fn": int(matrix[1, 0]),
        "tp": int(matrix[1, 1]),
    }


def metric_rows(pack: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for evidence in verified_seeds(pack):
        for model in sorted(evidence["metrics"]):
            metrics = evidence["metrics"][model]
            row = {
                "seed": int(evidence["seed"]),
                "model": model,
                "roc_auc": float(metrics["roc_auc"]),
                "pr_auc": float(metrics["pr_auc"]),
                "f1": float(metrics["f1"]),
                "precision": float(metrics["precision"]),
                "recall": float(metrics["recall"]),
                "brier_score": float(metrics["brier_score"]),
                **_matrix_cells(metrics),
                "dataset_sha256": str(evidence["dataset_sha256"]),
                "artifact_sha256": str(evidence["artifact_sha256"][model]),
            }
            if not np.isfinite([row[name] for name in METRIC_COLUMNS[2:8]]).all():
                raise ValueError("metric table contains non-finite values")
            rows.append(row)
    return rows


def sensitivity_rows(pack: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for evidence in verified_seeds(pack):
        labels = np.asarray(evidence["labels"], dtype=np.uint8)
        for model in sorted(evidence["probabilities"]):
            probabilities = np.asarray(evidence["probabilities"][model], dtype=np.float64)
            for metrics in threshold_rows(labels, probabilities):
                rows.append(
                    {
                        "seed": int(evidence["seed"]),
                        "model": model,
                        "threshold": float(metrics["threshold"]),
                        "roc_auc": float(metrics["roc_auc"]),
                        "pr_auc": float(metrics["pr_auc"]),
                        "f1": float(metrics["f1"]),
                        "precision": float(metrics["precision"]),
                        "recall": float(metrics["recall"]),
                        "brier_score": float(metrics["brier_score"]),
                        **_matrix_cells(metrics),
                        "dataset_sha256": str(evidence["dataset_sha256"]),
                        "artifact_sha256": str(evidence["artifact_sha256"][model]),
                    }
                )
    return rows


def _write_csv(path: Path, columns: tuple[str, ...], rows: list[dict[str, Any]]) -> Path:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return path


def _records(pack: Mapping[str, Any]) -> list[SeedEvidence]:
    return [
        SeedEvidence(
            seed=int(evidence["seed"]),
            dataset_sha256=str(evidence["dataset_sha256"]),
            labels=tuple(int(value) for value in evidence["labels"]),
            probabilities={
                model: tuple(float(value) for value in values)
                for model, values in evidence["probabilities"].items()
            },
            metrics=evidence["metrics"],
            artifact_sha256=evidence["artifact_sha256"],
        )
        for evidence in verified_seeds(pack)
    ]


def _calibration_gap(pack: Mapping[str, Any], model: str) -> float:
    edges = np.linspace(0, 1, 6)
    gaps: list[float] = []
    for evidence in verified_seeds(pack):
        labels = np.asarray(evidence["labels"], dtype=np.uint8)
        probabilities = np.asarray(
            evidence["probabilities"][model], dtype=np.float64
        )
        assignments = np.searchsorted(edges, probabilities, side="right") - 1
        assignments = np.minimum(assignments, edges.size - 2)
        for index in range(edges.size - 1):
            selected = assignments == index
            if np.any(selected):
                gaps.append(
                    abs(
                        float(np.mean(probabilities[selected]))
                        - float(np.mean(labels[selected]))
                    )
                )
    return float(np.mean(gaps))


def _aggregate_confusion(
    pack: Mapping[str, Any], model: str
) -> tuple[int, int, int, int]:
    matrix = np.sum(
        [
            np.asarray(evidence["metrics"][model]["confusion_matrix"], dtype=int)
            for evidence in verified_seeds(pack)
        ],
        axis=0,
    )
    return (
        int(matrix[0, 0]),
        int(matrix[0, 1]),
        int(matrix[1, 0]),
        int(matrix[1, 1]),
    )


def _figure_descriptions(
    pack: Mapping[str, Any],
    summary: Mapping[str, Mapping[str, Mapping[str, float]]],
) -> dict[str, str]:
    tgnn_roc = summary["tgnn"]["roc_auc"]
    xgboost_roc = summary["xgboost"]["roc_auc"]
    tgnn_roc_boundary = (
        "is near chance with high seed variability"
        if abs(tgnn_roc["mean"] - 0.5) <= 0.10
        and tgnn_roc["sample_sd"] >= 0.05
        else "must be interpreted with its reported seed variability"
    )
    xgboost_stability = (
        "XGBoost is slightly steadier"
        if xgboost_roc["sample_sd"] < tgnn_roc["sample_sd"]
        else "XGBoost is not steadier across these seeds"
    )
    tgnn_pr = summary["tgnn"]["pr_auc"]["mean"]
    xgboost_pr = summary["xgboost"]["pr_auc"]["mean"]
    pr_boundary = (
        "Both mean PR-AUC values are low"
        if max(tgnn_pr, xgboost_pr) < 0.30
        else "The mean PR-AUC values are reported without a release threshold"
    )
    _, _, xgboost_fn, xgboost_tp = _aggregate_confusion(pack, "xgboost")
    positive_total = xgboost_fn + xgboost_tp
    xgboost_recall = xgboost_tp / positive_total if positive_total else 0.0
    recall_boundary = "very low; " if xgboost_recall < 0.20 else ""
    tgnn_gap = _calibration_gap(pack, "tgnn")
    xgboost_gap = _calibration_gap(pack, "xgboost")
    return {
        "roc.png": (
            "ROC curves with individual seed traces and descriptive bands. "
            f"TGNN mean ROC-AUC is {tgnn_roc['mean']:.3f} (sample SD "
            f"{tgnn_roc['sample_sd']:.3f}) and {tgnn_roc_boundary}; XGBoost "
            f"mean ROC-AUC is {xgboost_roc['mean']:.3f} (sample SD "
            f"{xgboost_roc['sample_sd']:.3f}). {xgboost_stability}."
        ),
        "precision-recall.png": (
            "Precision–recall curves retain individual seed traces and the "
            f"prevalence reference. {pr_boundary}: TGNN {tgnn_pr:.3f}, "
            f"XGBoost {xgboost_pr:.3f}."
        ),
        "calibration.png": (
            "Observed event rate is plotted against the actual mean prediction "
            "within each fixed bin; empty bins remain gaps. The mean absolute "
            f"fixed-bin calibration gap is {tgnn_gap:.3f} for TGNN and "
            f"{xgboost_gap:.3f} for XGBoost, so calibration deviation remains "
            "visible rather than being smoothed away."
        ),
        "threshold-sensitivity.png": (
            "Mean F1 is shown over the complete predeclared threshold grid with "
            "seed-variability bands. No threshold is selected or described as "
            "optimal."
        ),
        "confusion-matrices.png": (
            "Aggregate threshold-0.50 confusion counts are directly labelled. "
            f"XGBoost positive-class recall is {xgboost_recall:.3f} "
            f"({recall_boundary}aggregate FN={xgboost_fn}, TP={xgboost_tp}), "
            "which is a key exception to any broad performance claim."
        ),
    }


def write_appendix(pack: Mapping[str, Any], output: str | Path) -> Path:
    destination = Path(output)
    destination.mkdir(parents=True, exist_ok=True)
    seeds = verified_seeds(pack)
    _write_csv(destination / "seed-metrics.csv", METRIC_COLUMNS, metric_rows(pack))
    _write_csv(
        destination / "threshold-sensitivity.csv",
        THRESHOLD_COLUMNS,
        sensitivity_rows(pack),
    )
    summary = summarize_runs(_records(pack))
    seed_wording = "five seeds" if len(seeds) == 5 else f"{len(seeds)} seeds"
    subtitle = (
        "Synthetic five-seed sensitivity"
        if len(seeds) == 5
        else f"Synthetic {len(seeds)}-seed sensitivity"
    )
    lines = [
        "# Exploratory research appendix",
        "",
        "**Provenance:** `2026_EXPLORATORY_SENSITIVITY`",
        "",
        f"**Figure subtitle:** {subtitle}",
        "",
        f"This package reports {seed_wording}. It does not reproduce the original thesis "
        "and must not be interpreted as evidence from real enterprises.",
        "",
        f"Intervals are labelled **Seed variability** and reported as an **n={len(seeds)} descriptive "
        "t interval**. They describe variation across "
        "synthetic reruns; they are not hypothesis tests or generalization claims.",
        "",
        "## Metric summary",
        "",
        "Values are mean ± sample SD with a descriptive 95% t interval.",
        "",
        "| Model | Metric | Mean ± sample SD | Descriptive 95% t interval |",
        "|---|---|---:|---:|",
    ]
    for model in sorted(summary):
        for metric in ("roc_auc", "pr_auc", "brier_score"):
            values = summary[model][metric]
            lines.append(
                f"| {model} | {metric} | {values['mean']:.4f} ± "
                f"{values['sample_sd']:.4f} | [{values['ci95_low']:.4f}, "
                f"{values['ci95_high']:.4f}] |"
            )
    descriptions = _figure_descriptions(pack, summary)
    lines.extend(["", "## Figures and accessible descriptions", ""])
    for name, description in descriptions.items():
        lines.extend([f"### `{name}`", "", f"**Alt description:** {description}", ""])
    lines.extend(
        [
            "## Underlying data",
            "",
            "`seed-metrics.csv` preserves per-seed model metrics and dataset/model artifact hashes. "
            "`threshold-sensitivity.csv` preserves every predeclared threshold row. Missing values "
            "are not imputed and no smoothing or threshold selection is applied.",
            "",
        ]
    )
    path = destination / "research-appendix.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
