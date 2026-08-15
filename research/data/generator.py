from __future__ import annotations

import numpy as np

from research.data.schema import GeneratorConfig, SyntheticDataset


def _relationships(
    rng: np.random.Generator,
    enterprise_count: int,
    edge_count: int,
) -> np.ndarray:
    edges: set[tuple[int, int]] = set()
    while len(edges) < edge_count:
        suppliers = rng.integers(0, enterprise_count, size=edge_count)
        customers = rng.integers(0, enterprise_count, size=edge_count)
        for supplier, customer in zip(suppliers, customers, strict=True):
            source = int(supplier)
            target = int(customer)
            if source != target:
                edges.add((source, target))
            if len(edges) == edge_count:
                break
    return np.asarray(sorted(edges), dtype=np.int32)


def generate_dataset(
    config: GeneratorConfig | None = None,
) -> SyntheticDataset:
    active = config or GeneratorConfig.reference()
    rng = np.random.default_rng(active.seed)
    n = active.enterprise_count
    months = active.months

    industry_codes = rng.choice(
        4,
        size=n,
        p=(0.35, 0.25, 0.25, 0.15),
    ).astype(np.int8)
    size_codes = rng.choice(
        4,
        size=n,
        p=(0.08, 0.22, 0.42, 0.28),
    ).astype(np.int8)

    size_risk = np.take(np.asarray((0.02, 0.08, 0.16, 0.23)), size_codes)
    industry_risk = np.take(
        np.asarray((0.08, 0.10, 0.06, 0.14)), industry_codes
    )
    base_risk = np.clip(
        0.08 + size_risk + industry_risk + 0.38 * rng.beta(2.0, 5.0, n),
        0.03,
        0.90,
    )
    latent = np.empty((months, n), dtype=np.float64)
    latent[0] = np.clip(base_risk + rng.normal(0.0, 0.04, n), 0.01, 0.98)
    for month in range(1, months):
        shock_mask = rng.random(n) < 0.035
        shocks = shock_mask * rng.uniform(0.22, 0.52, n)
        recovery = (latent[month - 1] > 0.65) * rng.uniform(0.0, 0.05, n)
        latent[month] = np.clip(
            0.82 * latent[month - 1]
            + 0.18 * base_risk
            + shocks
            - recovery
            + rng.normal(0.0, 0.025, n),
            0.01,
            0.99,
        )

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

    event_probability = np.clip(
        0.006 + 0.16 * latent**3 + 0.18 * (latent >= 0.76),
        0.0,
        0.55,
    )
    severe_events = (rng.random((months, n)) < event_probability).astype(np.uint8)

    relationships = _relationships(rng, n, active.edge_count)
    suppliers = relationships[:, 0]
    customers = relationships[:, 1]
    exposure = 0.62 * latent[:, suppliers] + 0.38 * latent[:, customers]
    counts = rng.poisson(3.0 + 7.0 * (1.0 - exposure)).astype(np.float64) + 1.0
    amount_scale = rng.lognormal(mean=10.2, sigma=0.55, size=active.edge_count)
    amounts = counts * amount_scale[None, :] * (1.0 - 0.25 * exposure)
    overdue = np.clip(
        0.015 + 0.76 * exposure + rng.normal(0.0, 0.025, exposure.shape),
        0.0,
        1.0,
    )
    delay = np.clip(
        1.0 + 56.0 * overdue + rng.normal(0.0, 2.0, overdue.shape),
        0.0,
        90.0,
    )
    edge_observations = np.stack(
        (amounts, counts, overdue, delay), axis=-1
    ).astype(np.float32)

    return SyntheticDataset(
        config=active,
        enterprise_ids=tuple(f"E{index:04d}" for index in range(1, n + 1)),
        industry_codes=industry_codes,
        size_codes=size_codes,
        states=states,
        relationships=relationships,
        edge_observations=edge_observations,
        severe_events=severe_events,
    )
