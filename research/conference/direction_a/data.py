from __future__ import annotations

import hashlib
import json
import struct
from dataclasses import asdict, dataclass

import numpy as np
from numpy.typing import NDArray


DATASET_VERSION = "synthetic-scf-propagation-v1"
SCHEMA_VERSION = "scf-propagation-data-v1"
FEATURE_SCHEMA_VERSION = "scf-propagation-feature-v1"


@dataclass(frozen=True)
class PropagationGeneratorConfig:
    enterprise_count: int = 500
    months: int = 30
    relationships_per_enterprise: int = 3
    seed: int = 20260909
    propagation_strength: float = 0.30
    shock_probability: float = 0.025

    def __post_init__(self) -> None:
        if self.enterprise_count < 8:
            raise ValueError("enterprise_count must be at least 8")
        if self.months < 12:
            raise ValueError("months must be at least 12")
        if self.relationships_per_enterprise < 1:
            raise ValueError("relationships_per_enterprise must be positive")
        if not 0.0 <= self.propagation_strength <= 1.0:
            raise ValueError("propagation_strength must be in [0, 1]")
        if not 0.0 <= self.shock_probability <= 1.0:
            raise ValueError("shock_probability must be in [0, 1]")
        if self.edge_count > self.enterprise_count * (self.enterprise_count - 1):
            raise ValueError("requested relationships exceed directed graph capacity")

    @property
    def edge_count(self) -> int:
        return self.enterprise_count * self.relationships_per_enterprise


@dataclass(frozen=True)
class PropagationDataset:
    config: PropagationGeneratorConfig
    enterprise_ids: tuple[str, ...]
    industry_codes: NDArray[np.int8]
    size_codes: NDArray[np.int8]
    states: NDArray[np.float32]
    relationships: NDArray[np.int32]
    relationship_active_months: NDArray[np.int16]
    edge_observations: NDArray[np.float32]
    severe_events: NDArray[np.uint8]
    propagation_contribution: NDArray[np.float32]
    propagation_events: NDArray[np.uint8]

    @property
    def dataset_version(self) -> str:
        return DATASET_VERSION

    @property
    def schema_version(self) -> str:
        return SCHEMA_VERSION

    @property
    def content_sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    def canonical_bytes(self) -> bytes:
        header = json.dumps(
            {
                "dataset_version": DATASET_VERSION,
                "schema_version": SCHEMA_VERSION,
                "feature_schema_version": FEATURE_SCHEMA_VERSION,
                "config": asdict(self.config),
                "enterprise_ids": self.enterprise_ids,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        chunks = [struct.pack("<Q", len(header)), header]
        arrays = (
            self.industry_codes,
            self.size_codes,
            self.states,
            self.relationships,
            self.relationship_active_months,
            self.edge_observations,
            self.severe_events,
            self.propagation_contribution,
            self.propagation_events,
        )
        for array in arrays:
            value = np.ascontiguousarray(array.astype(array.dtype.newbyteorder("<"), copy=False))
            chunks.extend((struct.pack("<Q", value.nbytes), value.tobytes(order="C")))
        return b"".join(chunks)


def _relationships(rng: np.random.Generator, config: PropagationGeneratorConfig) -> np.ndarray:
    edges: set[tuple[int, int]] = set()
    while len(edges) < config.edge_count:
        source = int(rng.integers(0, config.enterprise_count))
        target = int(rng.integers(0, config.enterprise_count))
        if source != target:
            edges.add((source, target))
    return np.asarray(sorted(edges), dtype=np.int32)


def _active_intervals(config: PropagationGeneratorConfig) -> np.ndarray:
    intervals = np.empty((config.edge_count, 2), dtype=np.int16)
    for index in range(config.edge_count):
        start = 1 + (index % min(4, max(config.months - 2, 1)))
        end_offset = (index // 4) % min(3, max(config.months - 1, 1))
        end = config.months - end_offset
        intervals[index] = (start, max(start, end))
    return intervals


def _incoming_signal(
    latent_previous: np.ndarray,
    relationships: np.ndarray,
    intervals: np.ndarray,
    month: int,
    enterprise_count: int,
) -> np.ndarray:
    active = (intervals[:, 0] <= month) & (intervals[:, 1] >= month)
    signal = np.zeros(enterprise_count, dtype=np.float64)
    counts = np.zeros(enterprise_count, dtype=np.float64)
    for source, target in relationships[active]:
        signal[int(target)] += max(float(latent_previous[int(source)]) - 0.45, 0.0)
        counts[int(target)] += 1.0
    return np.divide(signal, counts, out=np.zeros_like(signal), where=counts > 0)


def generate_propagation_dataset(
    config: PropagationGeneratorConfig | None = None,
) -> PropagationDataset:
    active = config or PropagationGeneratorConfig()
    rng = np.random.default_rng(active.seed)
    n, months = active.enterprise_count, active.months
    relationships = _relationships(rng, active)
    intervals = _active_intervals(active)

    industry_codes = rng.choice(4, size=n, p=(0.35, 0.25, 0.25, 0.15)).astype(np.int8)
    size_codes = rng.choice(4, size=n, p=(0.08, 0.22, 0.42, 0.28)).astype(np.int8)
    industry_risk = np.take(np.asarray((0.07, 0.10, 0.06, 0.13)), industry_codes)
    size_risk = np.take(np.asarray((0.02, 0.07, 0.14, 0.21)), size_codes)
    base = np.clip(0.08 + industry_risk + size_risk + 0.30 * rng.beta(2.0, 5.0, n), 0.03, 0.82)

    latent = np.empty((months, n), dtype=np.float64)
    propagation = np.zeros((months, n), dtype=np.float64)
    propagation_events = np.zeros((months, n), dtype=np.uint8)
    severe_events = np.zeros((months, n), dtype=np.uint8)
    latent[0] = np.clip(base + rng.normal(0.0, 0.035, n), 0.01, 0.95)

    for month_index in range(1, months):
        month = month_index + 1
        neighbor_signal = _incoming_signal(latent[month_index - 1], relationships, intervals, month, n)
        propagation[month_index] = active.propagation_strength * neighbor_signal
        independent_shocks = (rng.random(n) < active.shock_probability) * rng.uniform(0.18, 0.42, n)
        latent[month_index] = np.clip(
            0.78 * latent[month_index - 1]
            + 0.22 * base
            + propagation[month_index]
            + independent_shocks
            - 0.035 * (latent[month_index - 1] > 0.72)
            + rng.normal(0.0, 0.02, n),
            0.01,
            0.99,
        )
        independent_probability = np.clip(0.004 + 0.09 * latent[month_index] ** 3, 0.0, 0.20)
        propagation_probability = np.clip(1.8 * propagation[month_index], 0.0, 0.45)
        independent_event = rng.random(n) < independent_probability
        propagated_event = rng.random(n) < propagation_probability
        propagation_events[month_index] = propagated_event.astype(np.uint8)
        severe_events[month_index] = (independent_event | propagated_event).astype(np.uint8)

    noise = rng.normal(0.0, 0.025, size=(months, n, 7))
    states = np.stack(
        (
            0.94 - 0.72 * latent,
            0.90 - 0.68 * latent,
            0.10 + 0.78 * latent,
            0.88 - 0.72 * latent,
            0.92 - 0.74 * latent,
            0.02 + 0.82 * latent,
            0.01 + 0.86 * latent,
        ),
        axis=-1,
    )
    states = np.clip(states + noise, 0.0, 1.0).astype(np.float32)

    suppliers, customers = relationships[:, 0], relationships[:, 1]
    exposure = 0.58 * latent[:, suppliers] + 0.42 * latent[:, customers]
    counts = rng.poisson(3.0 + 7.0 * (1.0 - exposure)).astype(np.float64) + 1.0
    amount_scale = rng.lognormal(mean=10.2, sigma=0.55, size=active.edge_count)
    amounts = counts * amount_scale[None, :] * (1.0 - 0.22 * exposure)
    overdue = np.clip(0.015 + 0.76 * exposure + rng.normal(0.0, 0.025, exposure.shape), 0.0, 1.0)
    delay = np.clip(1.0 + 56.0 * overdue + rng.normal(0.0, 2.0, overdue.shape), 0.0, 90.0)
    edge_observations = np.stack((amounts, counts, overdue, delay), axis=-1).astype(np.float32)

    return PropagationDataset(
        config=active,
        enterprise_ids=tuple(f"P{index:04d}" for index in range(1, n + 1)),
        industry_codes=industry_codes,
        size_codes=size_codes,
        states=states,
        relationships=relationships,
        relationship_active_months=intervals,
        edge_observations=edge_observations,
        severe_events=severe_events,
        propagation_contribution=propagation.astype(np.float32),
        propagation_events=propagation_events,
    )
