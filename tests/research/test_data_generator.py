import hashlib

import numpy as np

from research.data.generator import generate_dataset
from research.data.manifest import build_dataset_manifest
from research.data.schema import GeneratorConfig, STATE_FEATURE_NAMES


def test_reference_dataset_has_frozen_dimensions_and_safe_relationships():
    dataset = generate_dataset()

    assert dataset.config == GeneratorConfig.reference()
    assert dataset.enterprise_ids[0] == "E0001"
    assert dataset.enterprise_ids[-1] == "E0500"
    assert len(dataset.enterprise_ids) == 500
    assert dataset.states.shape == (24, 500, 7)
    assert dataset.relationships.shape == (1500, 2)
    assert dataset.edge_observations.shape == (24, 1500, 4)
    assert dataset.severe_events.shape == (24, 500)
    assert np.all(dataset.relationships[:, 0] != dataset.relationships[:, 1])
    assert len({tuple(edge) for edge in dataset.relationships.tolist()}) == 1500
    assert np.isfinite(dataset.states).all()
    assert np.isfinite(dataset.edge_observations).all()
    assert 0 < int(dataset.severe_events.sum()) < dataset.severe_events.size


def test_reference_generation_is_byte_deterministic():
    first = generate_dataset()
    second = generate_dataset()

    assert first.canonical_bytes() == second.canonical_bytes()
    assert hashlib.sha256(first.canonical_bytes()).hexdigest() == hashlib.sha256(
        second.canonical_bytes()
    ).hexdigest()


def test_different_seed_changes_dataset_identity():
    reference = generate_dataset()
    alternate = generate_dataset(
        GeneratorConfig(enterprise_count=500, months=24, seed=20260816)
    )

    assert hashlib.sha256(reference.canonical_bytes()).hexdigest() != hashlib.sha256(
        alternate.canonical_bytes()
    ).hexdigest()


def test_manifest_binds_schema_counts_and_content_hash():
    dataset = generate_dataset()
    manifest = build_dataset_manifest(dataset)

    assert manifest.dataset_name == "synthetic-scf-v1"
    assert manifest.schema_version == "scf-data-v1"
    assert manifest.seed == 20260815
    assert manifest.counts == {
        "enterprises": 500,
        "months": 24,
        "relationships": 1500,
        "edge_observations": 36000,
        "severe_events": int(dataset.severe_events.sum()),
    }
    assert manifest.content_sha256 == hashlib.sha256(
        dataset.canonical_bytes()
    ).hexdigest()
    assert len(manifest.content_sha256) == 64


def test_model_state_schema_contains_only_observable_features():
    assert STATE_FEATURE_NAMES == (
        "credit_history_score",
        "liquidity_ratio",
        "leverage_ratio",
        "cash_flow_index",
        "operational_stability",
        "overdue_ratio",
        "average_delay_normalized",
    )
    assert all("label" not in name and "future" not in name for name in STATE_FEATURE_NAMES)
