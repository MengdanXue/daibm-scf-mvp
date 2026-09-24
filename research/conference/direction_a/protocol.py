from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum

import numpy as np
from numpy.typing import NDArray
from sklearn.metrics import f1_score

from research.conference.direction_a.data import PropagationDataset


SPLIT_VERSION = "direction-a-chronological-v1"


@dataclass(frozen=True)
class ConferenceProtocol:
    window_months: int = 12
    horizon_months: int = 3
    train_fraction: float = 0.60
    validation_fraction: float = 0.20

    def __post_init__(self) -> None:
        if self.window_months < 2 or self.horizon_months < 1:
            raise ValueError("window and horizon must be positive")
        if not 0.0 < self.train_fraction < 1.0:
            raise ValueError("train_fraction must be in (0, 1)")
        if not 0.0 < self.validation_fraction < 1.0:
            raise ValueError("validation_fraction must be in (0, 1)")
        if self.train_fraction + self.validation_fraction >= 1.0:
            raise ValueError("train and validation fractions must leave a test partition")


@dataclass(frozen=True)
class NormalizationStats:
    mean: NDArray[np.float32]
    standard_deviation: NDArray[np.float32]
    normalization_id: str
    fit_end_month: int


@dataclass(frozen=True)
class ConferenceSamples:
    x: NDArray[np.float32]
    adjacency: NDArray[np.float32]
    y: NDArray[np.uint8]
    propagation_y: NDArray[np.uint8]
    anchors: NDArray[np.int16]
    node_indices: NDArray[np.int64]

    def select_nodes(self, indices: NDArray[np.int64]) -> "ConferenceSamples":
        local = np.asarray(indices, dtype=np.int64)
        return ConferenceSamples(
            x=np.asarray(self.x[:, :, local, :], dtype=np.float32),
            adjacency=np.asarray(self.adjacency[:, :, local][:, :, :, local], dtype=np.float32),
            y=np.asarray(self.y[:, local], dtype=np.uint8),
            propagation_y=np.asarray(self.propagation_y[:, local], dtype=np.uint8),
            anchors=self.anchors.copy(),
            node_indices=self.node_indices[local].copy(),
        )


@dataclass(frozen=True)
class ConferenceSplit:
    train: ConferenceSamples
    validation: ConferenceSamples
    test: ConferenceSamples
    normalization: NormalizationStats
    split_version: str = SPLIT_VERSION


@dataclass(frozen=True)
class IndustryHoldout:
    train: ConferenceSamples
    validation: ConferenceSamples
    test: ConferenceSamples
    held_out_industry: int
    protocol: str = "strict_inductive_leave_one_industry_out_v1"


@dataclass(frozen=True)
class ThresholdSelection:
    threshold: float
    score: float
    objective: str
    selection_partition: str
    candidate_count: int


class GraphCondition(str, Enum):
    OBSERVED = "observed"
    IDENTITY_ONLY = "identity_only"
    NODE_PERMUTED = "node_permuted"
    STATIC_FIRST = "static_first"


def _adjacency(dataset: PropagationDataset) -> np.ndarray:
    months = dataset.config.months
    n = dataset.config.enterprise_count
    result = np.zeros((months, n, n), dtype=np.float32)
    for month_index in range(months):
        month = month_index + 1
        active = (
            (dataset.relationship_active_months[:, 0] <= month)
            & (dataset.relationship_active_months[:, 1] >= month)
        )
        directed = np.zeros((n, n), dtype=np.float32)
        edges = dataset.relationships[active]
        if edges.size:
            directed[edges[:, 0], edges[:, 1]] = 1.0
        symmetric = np.maximum(directed, directed.T)
        symmetric += np.eye(n, dtype=np.float32)
        degree = symmetric.sum(axis=1)
        inv = np.power(degree, -0.5)
        result[month_index] = inv[:, None] * symmetric * inv[None, :]
    return result


def _features(dataset: PropagationDataset) -> np.ndarray:
    states = dataset.states.astype(np.float32)
    months, nodes, _ = states.shape
    edge_summary = np.zeros((months, nodes, 4), dtype=np.float32)
    counts = np.zeros((months, nodes, 1), dtype=np.float32)
    for edge_index, (source, target) in enumerate(dataset.relationships):
        start, end = dataset.relationship_active_months[edge_index]
        for month in range(int(start) - 1, int(end)):
            observation = dataset.edge_observations[month, edge_index]
            edge_summary[month, int(source)] += observation
            edge_summary[month, int(target)] += observation
            counts[month, int(source), 0] += 1.0
            counts[month, int(target), 0] += 1.0
    edge_summary = np.divide(
        edge_summary,
        counts,
        out=np.zeros_like(edge_summary),
        where=counts > 0,
    )
    edge_summary[..., 0] = np.log1p(edge_summary[..., 0]) / 15.0
    edge_summary[..., 1] = np.log1p(edge_summary[..., 1]) / 5.0
    edge_summary[..., 3] /= 90.0
    return np.concatenate((states, edge_summary), axis=-1).astype(np.float32)


def _partition_anchors(anchors: np.ndarray, protocol: ConferenceProtocol) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    count = anchors.size
    if count < 5:
        raise ValueError("not enough chronological anchors for train/validation/test")
    train_count = max(2, int(np.floor(count * protocol.train_fraction)))
    validation_count = max(1, int(np.floor(count * protocol.validation_fraction)))
    if train_count + validation_count >= count:
        validation_count = 1
        train_count = count - 2
    return (
        anchors[:train_count],
        anchors[train_count : train_count + validation_count],
        anchors[train_count + validation_count :],
    )


def build_conference_split(
    dataset: PropagationDataset,
    protocol: ConferenceProtocol | None = None,
) -> ConferenceSplit:
    active = protocol or ConferenceProtocol()
    last_anchor = dataset.config.months - active.horizon_months
    anchors = np.arange(active.window_months, last_anchor + 1, dtype=np.int16)
    train_anchors, validation_anchors, test_anchors = _partition_anchors(anchors, active)

    raw_features = _features(dataset)
    fit_end_month = int(train_anchors.max())
    fit = raw_features[:fit_end_month].astype(np.float64)
    mean = fit.mean(axis=(0, 1)).astype(np.float32)
    std = fit.std(axis=(0, 1)).astype(np.float32)
    std[std < 1e-6] = 1.0
    material = mean.astype("<f4").tobytes() + std.astype("<f4").tobytes()
    normalization = NormalizationStats(
        mean=mean,
        standard_deviation=std,
        normalization_id=hashlib.sha256(material).hexdigest(),
        fit_end_month=fit_end_month,
    )
    normalized = (raw_features - mean[None, None, :]) / std[None, None, :]
    adjacency = _adjacency(dataset)
    node_indices = np.arange(dataset.config.enterprise_count, dtype=np.int64)

    def make(selected: np.ndarray) -> ConferenceSamples:
        xs, graphs, labels, propagation_labels = [], [], [], []
        for anchor_value in selected:
            anchor = int(anchor_value)
            xs.append(normalized[anchor - active.window_months : anchor])
            graphs.append(adjacency[anchor - active.window_months : anchor])
            labels.append(dataset.severe_events[anchor : anchor + active.horizon_months].max(axis=0))
            propagation_labels.append(
                dataset.propagation_events[anchor : anchor + active.horizon_months].max(axis=0)
            )
        return ConferenceSamples(
            x=np.stack(xs).astype(np.float32),
            adjacency=np.stack(graphs).astype(np.float32),
            y=np.stack(labels).astype(np.uint8),
            propagation_y=np.stack(propagation_labels).astype(np.uint8),
            anchors=selected.copy(),
            node_indices=node_indices.copy(),
        )

    return ConferenceSplit(
        train=make(train_anchors),
        validation=make(validation_anchors),
        test=make(test_anchors),
        normalization=normalization,
    )


def choose_validation_threshold(
    labels: np.ndarray,
    probabilities: np.ndarray,
    *,
    candidates: tuple[float, ...] | None = None,
) -> ThresholdSelection:
    truth = np.asarray(labels, dtype=np.uint8).reshape(-1)
    scores = np.asarray(probabilities, dtype=np.float64).reshape(-1)
    if truth.size != scores.size or truth.size == 0:
        raise ValueError("validation labels and probabilities must have equal nonzero size")
    values = candidates or tuple(float(value) for value in np.linspace(0.10, 0.90, 17))
    if not values:
        raise ValueError("at least one threshold candidate is required")
    ranked: list[tuple[float, float]] = []
    for threshold in values:
        if not 0.0 < threshold < 1.0:
            raise ValueError("threshold candidates must be in (0, 1)")
        predicted = (scores >= threshold).astype(np.uint8)
        ranked.append((float(f1_score(truth, predicted, zero_division=0)), float(threshold)))
    best_score = max(score for score, _ in ranked)
    tied = [threshold for score, threshold in ranked if score == best_score]
    best_threshold = min(tied, key=lambda value: (abs(value - 0.5), value))
    return ThresholdSelection(
        threshold=best_threshold,
        score=best_score,
        objective="f1",
        selection_partition="validation",
        candidate_count=len(values),
    )


def apply_graph_condition(
    adjacency: np.ndarray,
    condition: GraphCondition,
    *,
    seed: int,
) -> np.ndarray:
    observed = np.asarray(adjacency, dtype=np.float32)
    if condition is GraphCondition.OBSERVED:
        return observed.copy()
    if condition is GraphCondition.IDENTITY_ONLY:
        identity = np.eye(observed.shape[-1], dtype=np.float32)
        return np.broadcast_to(identity, observed.shape).copy()
    if condition is GraphCondition.STATIC_FIRST:
        return np.repeat(observed[:, :1], observed.shape[1], axis=1).copy()
    if condition is GraphCondition.NODE_PERMUTED:
        rng = np.random.default_rng(seed)
        permutation = rng.permutation(observed.shape[-1])
        return observed[..., permutation, :][..., :, permutation].copy()
    raise ValueError(f"unsupported graph condition: {condition}")


def build_industry_holdout(
    split: ConferenceSplit,
    industry_codes: np.ndarray,
    *,
    held_out_industry: int,
) -> IndustryHoldout:
    codes = np.asarray(industry_codes, dtype=np.int8)
    train_nodes = np.flatnonzero(codes != held_out_industry).astype(np.int64)
    test_nodes = np.flatnonzero(codes == held_out_industry).astype(np.int64)
    if train_nodes.size == 0 or test_nodes.size == 0:
        raise ValueError("industry holdout requires both source and target industry nodes")
    return IndustryHoldout(
        train=split.train.select_nodes(train_nodes),
        validation=split.validation.select_nodes(train_nodes),
        test=split.test.select_nodes(test_nodes),
        held_out_industry=held_out_industry,
    )
