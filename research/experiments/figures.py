from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping

import matplotlib

matplotlib.use("Agg", force=True)

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D
from matplotlib.text import Text
from scipy import stats
from sklearn.calibration import CalibrationDisplay
from sklearn.metrics import (
    PrecisionRecallDisplay,
    RocCurveDisplay,
    precision_recall_curve,
    roc_curve,
)

from research.experiments.report import sensitivity_rows, verified_seeds


BLUE = "#0072B2"
ORANGE = "#D55E00"
MODELS = ("tgnn", "xgboost")
STYLES = {
    "tgnn": {"color": BLUE, "linestyle": "-", "marker": "o"},
    "xgboost": {"color": ORANGE, "linestyle": "--", "marker": "s"},
}
FIGURE_SIZE = (7.2, 4.6)
DPI = 160


def _validated_arrays(evidence: Mapping[str, Any], model: str) -> tuple[np.ndarray, np.ndarray]:
    labels = np.asarray(evidence["labels"], dtype=np.uint8).reshape(-1)
    probabilities = np.asarray(evidence["probabilities"][model], dtype=np.float64).reshape(-1)
    if (
        labels.size == 0
        or probabilities.size != labels.size
        or not np.isin(labels, (0, 1)).all()
        or not np.isfinite(probabilities).all()
        or not ((0 <= probabilities) & (probabilities <= 1)).all()
    ):
        raise ValueError("figure inputs must contain finite aligned binary evidence")
    if np.unique(labels).size != 2:
        raise ValueError("curve figures require both outcome classes")
    return labels, probabilities


def _interval(values: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    sample = np.asarray(values, dtype=np.float64)
    counts = np.sum(np.isfinite(sample), axis=0)
    totals = np.nansum(sample, axis=0)
    mean = np.divide(
        totals,
        counts,
        out=np.full(totals.shape, np.nan, dtype=np.float64),
        where=counts > 0,
    )
    low = np.full(mean.shape, np.nan, dtype=np.float64)
    high = np.full(mean.shape, np.nan, dtype=np.float64)
    for index in range(sample.shape[1]):
        column = sample[:, index]
        column = column[np.isfinite(column)]
        if column.size >= 2:
            margin = stats.t.ppf(0.975, column.size - 1) * stats.sem(column)
            low[index] = float(column.mean() - margin)
            high[index] = float(column.mean() + margin)
    return mean, np.clip(low, 0, 1), np.clip(high, 0, 1)


def _finite_column_mean(values: np.ndarray) -> np.ndarray:
    sample = np.asarray(values, dtype=np.float64)
    counts = np.sum(np.isfinite(sample), axis=0)
    totals = np.nansum(sample, axis=0)
    return np.divide(
        totals,
        counts,
        out=np.full(totals.shape, np.nan, dtype=np.float64),
        where=counts > 0,
    )


def calibration_points(
    labels: np.ndarray,
    probabilities: np.ndarray,
    edges: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Return mean prediction and observed rate for every fixed bin.

    Empty bins remain paired NaNs so a line renderer leaves a visible gap.
    """

    truth = np.asarray(labels, dtype=np.uint8).reshape(-1)
    scores = np.asarray(probabilities, dtype=np.float64).reshape(-1)
    boundaries = np.asarray(edges, dtype=np.float64).reshape(-1)
    if (
        truth.size == 0
        or scores.size != truth.size
        or not np.isin(truth, (0, 1)).all()
        or not np.isfinite(scores).all()
        or not ((0 <= scores) & (scores <= 1)).all()
        or boundaries.size < 2
        or not np.isfinite(boundaries).all()
        or boundaries[0] != 0
        or boundaries[-1] != 1
        or not np.all(np.diff(boundaries) > 0)
    ):
        raise ValueError("calibration inputs and fixed bin edges are invalid")
    bin_count = boundaries.size - 1
    assignments = np.searchsorted(boundaries, scores, side="right") - 1
    assignments = np.minimum(assignments, bin_count - 1)
    predicted = np.full(bin_count, np.nan, dtype=np.float64)
    observed = np.full(bin_count, np.nan, dtype=np.float64)
    for index in range(bin_count):
        selected = assignments == index
        if np.any(selected):
            predicted[index] = float(np.mean(scores[selected]))
            observed[index] = float(np.mean(truth[selected]))
    return predicted, observed


def plot_calibration_trace(
    ax: plt.Axes,
    labels: np.ndarray,
    probabilities: np.ndarray,
    edges: np.ndarray,
    *,
    name: str,
    color: str,
    linestyle: str,
) -> tuple[np.ndarray, np.ndarray, Line2D]:
    predicted, observed = calibration_points(labels, probabilities, edges)
    display = CalibrationDisplay(
        prob_true=observed,
        prob_pred=predicted,
        y_prob=np.asarray(probabilities, dtype=np.float64),
    ).plot(
        ax=ax,
        name=name,
        ref_line=False,
        color=color,
        linestyle=linestyle,
        linewidth=0.7,
        alpha=0.22,
    )
    return predicted, observed, display.line_


def summarize_calibration(
    predicted: np.ndarray,
    observed: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    mean_predicted = _finite_column_mean(predicted)
    mean_observed, low, high = _interval(observed)
    return mean_predicted, mean_observed, low, high


def _new_figure(
    title: str,
    seed_count: int,
    detail: str,
) -> tuple[plt.Figure, plt.Axes]:
    fig, ax = plt.subplots(
        figsize=FIGURE_SIZE,
        dpi=DPI,
        layout="constrained",
        facecolor="white",
    )
    ax.set_facecolor("white")
    fig.suptitle(title, fontsize=12, fontweight="bold")
    subtitle = (
        "Synthetic five-seed sensitivity"
        if seed_count == 5
        else f"Synthetic {seed_count}-seed sensitivity"
    )
    ax.set_title(f"{subtitle}\n{detail}", fontsize=8.5, color="#333333", pad=8)
    ax.grid(True, color="#D9D9D9", linewidth=0.6, alpha=0.75)
    ax.set_axisbelow(True)
    return fig, ax


def assert_text_within_figure(fig: plt.Figure) -> None:
    """Fail export when any visible text extends beyond the fixed canvas."""

    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    canvas = fig.bbox
    tolerance = 0.5
    for artist in fig.findobj(match=Text):
        if not artist.get_visible() or not artist.get_text().strip():
            continue
        bounds = artist.get_window_extent(renderer=renderer)
        if (
            bounds.x0 < canvas.x0 - tolerance
            or bounds.y0 < canvas.y0 - tolerance
            or bounds.x1 > canvas.x1 + tolerance
            or bounds.y1 > canvas.y1 + tolerance
        ):
            raise ValueError(
                f"text outside the figure canvas: {artist.get_text()!r}; "
                f"text_bounds={tuple(round(value, 2) for value in bounds.bounds)}; "
                f"canvas_bounds={tuple(round(value, 2) for value in canvas.bounds)}; "
                f"axes_bounds={[tuple(round(value, 3) for value in axis.get_position().bounds) for axis in fig.axes]}"
            )


def _save(fig: plt.Figure, path: Path) -> Path:
    assert_text_within_figure(fig)
    fig.savefig(
        path,
        dpi=DPI,
        facecolor="white",
        edgecolor="white",
        transparent=False,
        bbox_inches=None,
        metadata={"Software": "DAIBM-SCF research evidence renderer"},
    )
    plt.close(fig)
    return path


def _curve_figure(
    seeds: tuple[Mapping[str, Any], ...],
    path: Path,
    *,
    kind: str,
) -> Path:
    title = "Receiver operating characteristic" if kind == "roc" else "Precision–recall"
    fig, ax = _new_figure(
        title,
        len(seeds),
        f"Seed variability · n={len(seeds)} descriptive t interval; not a significance test",
    )
    grid = np.linspace(0, 1, 101)
    for model in MODELS:
        curves = []
        for evidence in seeds:
            labels, probabilities = _validated_arrays(evidence, model)
            raw_label = f"{model} seed {evidence['seed']}"
            if kind == "roc":
                x, y, _ = roc_curve(labels, probabilities)
                display = RocCurveDisplay(fpr=x, tpr=y)
            else:
                y, x, _ = precision_recall_curve(labels, probabilities)
                order = np.argsort(x)
                x, y = x[order], y[order]
                display = PrecisionRecallDisplay(precision=y, recall=x)
            interpolated = np.interp(grid, x, y)
            curves.append(interpolated)
            style = STYLES[model]
            curve_style = {
                "color": style["color"],
                "linestyle": style["linestyle"],
                "linewidth": 0.7,
                "alpha": 0.22,
            }
            # RocCurveDisplay exposes curve_kwargs, while the installed
            # PrecisionRecallDisplay forwards line options directly.
            if kind == "roc":
                display.plot(ax=ax, name=raw_label, curve_kwargs=curve_style)
            else:
                display.plot(ax=ax, name=raw_label, **curve_style)
            display.line_.set_label("_individual seed")
            if ax.legend_ is not None:
                ax.legend_.remove()
        mean, low, high = _interval(np.asarray(curves))
        style = STYLES[model]
        label = "TGNN" if model == "tgnn" else "XGBoost"
        ax.plot(
            grid,
            mean,
            color=style["color"],
            linestyle=style["linestyle"],
            marker=style["marker"],
            markevery=20,
            linewidth=2,
            markersize=4,
            label=label,
        )
        ax.fill_between(
            grid,
            low,
            high,
            color=style["color"],
            alpha=0.10,
            linewidth=0,
            label=f"{label} Seed variability",
        )
    if kind == "roc":
        ax.plot([0, 1], [0, 1], color="#555555", linestyle=":", linewidth=1.2, label="Chance")
        ax.set(xlabel="False-positive rate", ylabel="True-positive rate")
    else:
        prevalence = float(np.mean([np.mean(np.asarray(seed["labels"])) for seed in seeds]))
        ax.axhline(prevalence, color="#555555", linestyle=":", linewidth=1.2, label="Mean prevalence")
        ax.set(xlabel="Recall", ylabel="Precision")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.legend(
        loc="center left",
        bbox_to_anchor=(1.01, 0.5),
        fontsize=6.8,
        ncols=1,
        borderaxespad=0,
    )
    return _save(fig, path)


def _calibration_figure(seeds: tuple[Mapping[str, Any], ...], path: Path) -> Path:
    fig, ax = _new_figure(
        "Calibration by fixed probability bin",
        len(seeds),
        f"Seed variability · n={len(seeds)} descriptive t interval; empty bins remain gaps",
    )
    edges = np.linspace(0, 1, 6)
    for model in MODELS:
        predicted_by_seed = []
        observed = []
        for evidence in seeds:
            labels, probabilities = _validated_arrays(evidence, model)
            style = STYLES[model]
            predicted, rates, line = plot_calibration_trace(
                ax,
                labels,
                probabilities,
                edges,
                name=f"{model} seed {evidence['seed']}",
                color=style["color"],
                linestyle=style["linestyle"],
            )
            predicted_by_seed.append(predicted)
            observed.append(rates)
            line.set_label("_individual seed")
            if ax.legend_ is not None:
                ax.legend_.remove()
        mean_x, mean, low, high = summarize_calibration(
            np.asarray(predicted_by_seed), np.asarray(observed)
        )
        style = STYLES[model]
        label = "TGNN" if model == "tgnn" else "XGBoost"
        ax.plot(
            mean_x,
            mean,
            color=style["color"],
            linestyle=style["linestyle"],
            marker=style["marker"],
            linewidth=2,
            markersize=5,
            label=label,
        )
        ax.fill_between(
            mean_x,
            low,
            high,
            color=style["color"],
            alpha=0.10,
            linewidth=0,
            label=f"{label} Seed variability",
        )
    ax.plot(
        [0, 1],
        [0, 1],
        color="#555555",
        linestyle=":",
        linewidth=1.2,
        label="Perfect calibration",
    )
    ax.set(
        xlabel="Mean predicted probability within fixed bin",
        ylabel="Observed event rate",
        xlim=(0, 1),
        ylim=(0, 1),
    )
    ax.legend(loc="upper left", fontsize=7, ncols=2)
    return _save(fig, path)


def _threshold_figure(
    pack: Mapping[str, Any], seeds: tuple[Mapping[str, Any], ...], path: Path
) -> Path:
    fig, ax = _new_figure(
        "Threshold sensitivity (predeclared grid)",
        len(seeds),
        f"Seed variability · n={len(seeds)} descriptive t interval; no threshold selected",
    )
    rows = sensitivity_rows(pack)
    thresholds = sorted({float(row["threshold"]) for row in rows})
    for model in MODELS:
        values = np.asarray(
            [
                [float(row["f1"]) for row in rows if row["model"] == model and int(row["seed"]) == int(seed["seed"])]
                for seed in seeds
            ]
        )
        mean, low, high = _interval(values)
        style = STYLES[model]
        label = "TGNN" if model == "tgnn" else "XGBoost"
        ax.plot(thresholds, mean, color=style["color"], linestyle=style["linestyle"], marker=style["marker"], linewidth=2, markersize=4, label=label)
        ax.fill_between(thresholds, low, high, color=style["color"], alpha=0.10, linewidth=0, label=f"{label} Seed variability")
    ax.axvline(0.5, color="#555555", linestyle=":", linewidth=1.2, label="Fixed reporting threshold 0.50")
    ax.set(xlabel="Decision threshold", ylabel="F1 score", xlim=(0.08, 0.92), ylim=(0, 1))
    ax.set_xticks(thresholds)
    ax.tick_params(axis="x", labelrotation=45, labelsize=8)
    for label in ax.get_xticklabels():
        label.set_horizontalalignment("right")
    ax.legend(
        loc="center left",
        bbox_to_anchor=(1.01, 0.5),
        fontsize=6.8,
        ncols=1,
        borderaxespad=0,
    )
    return _save(fig, path)


def _confusion_figure(seeds: tuple[Mapping[str, Any], ...], path: Path) -> Path:
    fig, axes = plt.subplots(1, 2, figsize=FIGURE_SIZE, dpi=DPI, layout="constrained", facecolor="white")
    fig.suptitle("Confusion matrices at fixed threshold 0.50", fontsize=12, fontweight="bold")
    for ax, model in zip(axes, MODELS, strict=True):
        matrix = np.sum(
            [np.asarray(seed["metrics"][model]["confusion_matrix"], dtype=np.int64) for seed in seeds],
            axis=0,
        )
        ax.imshow(matrix, cmap="Blues" if model == "tgnn" else "Oranges", vmin=0, vmax=max(1, int(matrix.max())))
        for row in range(2):
            for column in range(2):
                color = "white" if matrix[row, column] > matrix.max() / 2 else "black"
                ax.text(column, row, str(int(matrix[row, column])), ha="center", va="center", color=color, fontweight="bold")
        subtitle = (
            "Synthetic five-seed sensitivity"
            if len(seeds) == 5
            else f"Synthetic {len(seeds)}-seed sensitivity"
        )
        ax.set_title(("TGNN · solid/circle" if model == "tgnn" else "XGBoost · dashed/square") + f"\n{subtitle}", fontsize=9)
        ax.set_xticks([0, 1], labels=["Predicted 0", "Predicted 1"])
        ax.set_yticks([0, 1], labels=["Actual 0", "Actual 1"])
        ax.set_xlabel("Counts summed across seeds")
        ax.set_facecolor("white")
    fig.text(0.5, 0.025, f"n={len(seeds)} synthetic reruns; counts are not rates or significance tests", ha="center", fontsize=7.5, color="#444444")
    return _save(fig, path)


def render_figures(pack: Mapping[str, Any], output: str | Path) -> tuple[Path, ...]:
    seeds = verified_seeds(pack)
    destination = Path(output)
    destination.mkdir(parents=True, exist_ok=True)
    renderers: tuple[tuple[str, Callable[[], Path]], ...] = (
        ("roc.png", lambda: _curve_figure(seeds, destination / "roc.png", kind="roc")),
        ("precision-recall.png", lambda: _curve_figure(seeds, destination / "precision-recall.png", kind="pr")),
        ("calibration.png", lambda: _calibration_figure(seeds, destination / "calibration.png")),
        ("threshold-sensitivity.png", lambda: _threshold_figure(pack, seeds, destination / "threshold-sensitivity.png")),
        ("confusion-matrices.png", lambda: _confusion_figure(seeds, destination / "confusion-matrices.png")),
    )
    return tuple(render() for _, render in renderers)
