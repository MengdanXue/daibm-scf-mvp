from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from research.data.schema import SyntheticDataset
from research.graph.features import aggregate_node_features


@dataclass(frozen=True)
class NormalizationStats:
    mean: NDArray[np.float32]
    standard_deviation: NDArray[np.float32]
    normalization_id: str
    fit_start_month: int
    fit_end_month: int


@dataclass(frozen=True)
class GraphSeries:
    dataset: SyntheticDataset
    node_features: NDArray[np.float32]
    directed_adjacency: NDArray[np.uint8]
    normalized_adjacency: NDArray[np.float32]


@dataclass(frozen=True)
class TemporalSamples:
    x: NDArray[np.float32]
    adjacency: NDArray[np.float32]
    y: NDArray[np.uint8]
    anchors: NDArray[np.int16]
    snapshot_sha256: tuple[str, ...]
    normalization: NormalizationStats

    def subset(self, indices: NDArray[np.int64]) -> "TemporalSamples":
        return TemporalSamples(
            x=np.array(self.x[indices], copy=True),
            adjacency=np.array(self.adjacency[indices], copy=True),
            y=np.array(self.y[indices], copy=True),
            anchors=np.array(self.anchors[indices], copy=True),
            snapshot_sha256=tuple(
                self.snapshot_sha256[int(index)] for index in indices
            ),
            normalization=self.normalization,
        )


def _build_adjacency(dataset: SyntheticDataset) -> tuple[np.ndarray, np.ndarray]:
    months = dataset.config.months
    n = dataset.config.enterprise_count
    directed = np.zeros((months, n, n), dtype=np.uint8)
    normalized = np.zeros((months, n, n), dtype=np.float32)
    for month_index in range(months):
        month = month_index + 1
        active = (
            (dataset.relationship_active_months[:, 0] <= month)
            & (dataset.relationship_active_months[:, 1] >= month)
        )
        edges = dataset.relationships[active]
        if edges.size:
            directed[month_index, edges[:, 0], edges[:, 1]] = 1
        symmetric = np.maximum(
            directed[month_index], directed[month_index].T
        ).astype(np.float32)
        symmetric += np.eye(n, dtype=np.float32)
        degree = symmetric.sum(axis=1)
        inverse_sqrt = np.power(degree, -0.5)
        normalized[month_index] = (
            inverse_sqrt[:, None] * symmetric * inverse_sqrt[None, :]
        )
    return directed, normalized


def build_graph_series(
    dataset: SyntheticDataset,
    scenario: dict[str, object] | None = None,
) -> GraphSeries:
    if scenario:
        raise NotImplementedError("scenario overlays are introduced in Task 9")
    directed, normalized = _build_adjacency(dataset)
    return GraphSeries(
        dataset=dataset,
        node_features=aggregate_node_features(dataset),
        directed_adjacency=directed,
        normalized_adjacency=normalized,
    )


def _fit_normalization(
    features: np.ndarray,
    fit_end_month: int,
) -> NormalizationStats:
    fit_values = features[:fit_end_month].astype(np.float64)
    mean = fit_values.mean(axis=(0, 1)).astype(np.float32)
    standard_deviation = fit_values.std(axis=(0, 1)).astype(np.float32)
    standard_deviation[standard_deviation < 1e-6] = 1.0
    identity_material = mean.astype("<f4").tobytes() + standard_deviation.astype(
        "<f4"
    ).tobytes()
    return NormalizationStats(
        mean=mean,
        standard_deviation=standard_deviation,
        normalization_id=hashlib.sha256(identity_material).hexdigest(),
        fit_start_month=1,
        fit_end_month=fit_end_month,
    )


def _snapshot_hash(
    anchor: int,
    x: np.ndarray,
    adjacency: np.ndarray,
    normalization_id: str,
) -> str:
    digest = hashlib.sha256()
    digest.update(
        json.dumps(
            {"anchor": anchor, "normalization_id": normalization_id},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    )
    digest.update(np.ascontiguousarray(x.astype("<f4")).tobytes())
    digest.update(np.ascontiguousarray(adjacency.astype("<f4")).tobytes())
    return digest.hexdigest()


def build_samples(series_or_dataset: GraphSeries | SyntheticDataset) -> TemporalSamples:
    series = (
        series_or_dataset
        if isinstance(series_or_dataset, GraphSeries)
        else build_graph_series(series_or_dataset)
    )
    months = series.dataset.config.months
    anchors = np.arange(12, months - 2, dtype=np.int16)
    if anchors.size == 0:
        raise ValueError("dataset does not contain a valid prediction anchor")
    fit_end_month = min(16, int(anchors.max()))
    normalization = _fit_normalization(
        series.node_features,
        fit_end_month=fit_end_month,
    )
    normalized_features = (
        series.node_features - normalization.mean[None, None, :]
    ) / normalization.standard_deviation[None, None, :]

    x_values: list[np.ndarray] = []
    adjacency_values: list[np.ndarray] = []
    labels: list[np.ndarray] = []
    hashes: list[str] = []
    for anchor_value in anchors:
        anchor = int(anchor_value)
        x_window = normalized_features[anchor - 12 : anchor].astype(np.float32)
        adjacency_window = series.normalized_adjacency[
            anchor - 12 : anchor
        ].astype(np.float32)
        label = series.dataset.severe_events[anchor : anchor + 3].max(axis=0)
        x_values.append(x_window)
        adjacency_values.append(adjacency_window)
        labels.append(label.astype(np.uint8))
        hashes.append(
            _snapshot_hash(
                anchor,
                x_window,
                adjacency_window,
                normalization.normalization_id,
            )
        )
    return TemporalSamples(
        x=np.stack(x_values),
        adjacency=np.stack(adjacency_values),
        y=np.stack(labels),
        anchors=anchors,
        snapshot_sha256=tuple(hashes),
        normalization=normalization,
    )
