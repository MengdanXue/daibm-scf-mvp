from __future__ import annotations

import csv
import logging
from pathlib import Path

import matplotlib.image as mpimg
import numpy as np
import pytest

from research.experiments import figures
from research.experiments.figures import render_figures
from research.experiments.report import write_appendix
from research.experiments.runner import render_evidence_outputs
from research.training.metrics import evaluate_binary_predictions


FIGURE_NAMES = {
    "roc.png",
    "precision-recall.png",
    "calibration.png",
    "threshold-sensitivity.png",
    "confusion-matrices.png",
}
METRIC_COLUMNS = [
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
]
THRESHOLD_COLUMNS = [
    "seed",
    "model",
    "threshold",
    *METRIC_COLUMNS[2:],
]


def _fake_verified_pack() -> dict[str, object]:
    labels = np.asarray([0, 0, 1, 1, 0, 1, 0, 1, 0, 1], dtype=np.uint8)
    seeds: list[dict[str, object]] = []
    for offset, seed in enumerate(range(20260815, 20260820)):
        shift = (offset - 2) * 0.015
        probabilities = {
            "tgnn": np.clip(
                np.asarray([0.08, 0.25, 0.72, 0.84, 0.38, 0.65, 0.44, 0.77, 0.18, 0.59])
                + shift,
                0,
                1,
            ),
            "xgboost": np.clip(
                np.asarray([0.15, 0.32, 0.62, 0.76, 0.48, 0.58, 0.51, 0.68, 0.27, 0.55])
                - shift,
                0,
                1,
            ),
        }
        seeds.append(
            {
                "seed": seed,
                "dataset_sha256": f"{seed:064x}"[-64:],
                "labels": labels.tolist(),
                "probabilities": {
                    model: values.tolist() for model, values in probabilities.items()
                },
                "metrics": {
                    model: evaluate_binary_predictions(labels, values, threshold=0.5)
                    for model, values in probabilities.items()
                },
                "artifact_sha256": {
                    "tgnn": f"{seed + 100:064x}"[-64:],
                    "xgboost": f"{seed + 200:064x}"[-64:],
                },
            }
        )
    return {
        "status": "verified",
        "provenance": "2026_EXPLORATORY_SENSITIVITY",
        "manifest": {"reporting_threshold": 0.5},
        "seeds": seeds,
    }


@pytest.mark.filterwarnings("error")
def test_figures_have_required_names_pixels_opaque_background_and_nonempty_content(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    files = render_figures(_fake_verified_pack(), tmp_path)

    assert {path.name for path in files} == FIGURE_NAMES
    for path in files:
        pixels = mpimg.imread(path)
        assert pixels.shape == (736, 1152, 4)
        assert path.stat().st_size > 10_000
        assert np.isfinite(pixels).all()
        assert np.allclose(pixels[0, 0], [1.0, 1.0, 1.0, 1.0])
        assert float(np.std(pixels[:, :, :3])) > 0.02
    assert not [record for record in caplog.records if record.levelno >= logging.WARNING]


def test_report_writes_stable_finite_source_tables(tmp_path: Path) -> None:
    write_appendix(_fake_verified_pack(), tmp_path)

    metrics_path = tmp_path / "seed-metrics.csv"
    thresholds_path = tmp_path / "threshold-sensitivity.csv"
    with metrics_path.open(newline="", encoding="utf-8") as handle:
        metrics = list(csv.DictReader(handle))
    with thresholds_path.open(newline="", encoding="utf-8") as handle:
        thresholds = list(csv.DictReader(handle))

    assert list(metrics[0]) == METRIC_COLUMNS
    assert list(thresholds[0]) == THRESHOLD_COLUMNS
    assert len(metrics) == 10
    assert len(thresholds) == 100
    for row in metrics + thresholds:
        numeric = [
            float(value)
            for name, value in row.items()
            if name not in {"model", "dataset_sha256", "artifact_sha256"}
        ]
        assert np.isfinite(numeric).all()
        assert len(row["dataset_sha256"]) == 64
        assert len(row["artifact_sha256"]) == 64


def test_appendix_states_exploratory_statistical_and_accessibility_boundaries(
    tmp_path: Path,
) -> None:
    appendix = write_appendix(_fake_verified_pack(), tmp_path)
    text = appendix.read_text(encoding="utf-8")

    assert appendix.name == "research-appendix.md"
    assert "2026_EXPLORATORY_SENSITIVITY" in text
    assert "Synthetic five-seed sensitivity" in text
    assert "five seeds" in text
    assert "n=5 descriptive t interval" in text
    assert "Seed variability" in text
    assert "does not reproduce the original thesis" in text
    assert "Alt description" in text
    assert all(name in text for name in FIGURE_NAMES)
    assert "statistically significant" not in text.lower()
    assert "best threshold" not in text.lower()


@pytest.mark.filterwarnings("error")
def test_runner_output_step_keeps_figures_tables_and_appendix_together(
    tmp_path: Path,
) -> None:
    files = render_evidence_outputs(_fake_verified_pack(), tmp_path)

    assert {path.name for path in files} == {
        *FIGURE_NAMES,
        "seed-metrics.csv",
        "threshold-sensitivity.csv",
        "research-appendix.md",
    }
    assert all(path.parent == tmp_path and path.is_file() for path in files)


def test_text_boundary_audit_rejects_a_label_outside_the_canvas() -> None:
    figure = figures.plt.figure(figsize=(2, 2))
    figure.text(-0.10, 0.50, "clipped")

    with pytest.raises(ValueError, match="outside the figure canvas"):
        figures.assert_text_within_figure(figure)

    figures.plt.close(figure)
