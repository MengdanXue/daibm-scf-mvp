import numpy as np

from research.data.generator import generate_dataset
from research.data.schema import GeneratorConfig, SyntheticDataset
from research.graph.builder import build_graph_series, build_samples
from research.graph.features import GRAPH_FEATURE_NAMES
from research.graph.split import temporal_split


def small_dataset() -> SyntheticDataset:
    months = 15
    states = np.zeros((months, 3, 7), dtype=np.float32)
    relationships = np.asarray([[0, 1], [2, 1]], dtype=np.int32)
    active = np.asarray([[1, 2], [2, 3]], dtype=np.int16)
    observations = np.zeros((months, 2, 4), dtype=np.float32)
    observations[0, 0] = (100.0, 2.0, 0.25, 5.0)
    observations[1, 0] = (120.0, 3.0, 0.50, 8.0)
    observations[1, 1] = (80.0, 1.0, 0.10, 2.0)
    severe = np.zeros((months, 3), dtype=np.uint8)
    severe[12, 1] = 1
    return SyntheticDataset(
        config=GeneratorConfig(
            enterprise_count=3,
            months=months,
            seed=7,
            relationships_per_enterprise=1,
        ),
        enterprise_ids=("E0001", "E0002", "E0003"),
        industry_codes=np.zeros(3, dtype=np.int8),
        size_codes=np.zeros(3, dtype=np.int8),
        states=states,
        relationships=relationships,
        relationship_active_months=active,
        edge_observations=observations,
        severe_events=severe,
    )


def test_graph_retains_direction_but_gcn_adjacency_is_symmetric():
    series = build_graph_series(small_dataset())

    assert series.directed_adjacency[0, 0, 1] == 1
    assert series.directed_adjacency[0, 1, 0] == 0
    assert series.directed_adjacency[0, 2, 1] == 0
    assert series.directed_adjacency[1, 2, 1] == 1
    assert series.normalized_adjacency[0, 0, 1] > 0
    assert series.normalized_adjacency[0, 1, 0] > 0
    assert np.allclose(
        series.normalized_adjacency,
        series.normalized_adjacency.transpose(0, 2, 1),
    )


def test_graph_aggregates_monthly_inbound_and_outbound_edge_features():
    series = build_graph_series(small_dataset())
    feature_index = {name: index for index, name in enumerate(GRAPH_FEATURE_NAMES)}

    assert series.node_features[0, 1, feature_index["inbound_amount"]] == 100.0
    assert series.node_features[0, 1, feature_index["inbound_count"]] == 2.0
    assert series.node_features[0, 0, feature_index["outbound_overdue_ratio"]] == 0.25
    assert series.node_features[0, 1, feature_index["in_degree"]] == 1.0
    assert series.node_features[2, 0, feature_index["out_degree"]] == 0.0


def test_samples_use_previous_twelve_months_and_next_three_month_label():
    samples = build_samples(small_dataset())

    assert samples.anchors.tolist() == [12]
    assert samples.x.shape == (1, 12, 3, len(GRAPH_FEATURE_NAMES))
    assert samples.adjacency.shape == (1, 12, 3, 3)
    assert samples.y.shape == (1, 3)
    assert samples.y[0].tolist() == [0, 1, 0]
    assert samples.normalization.fit_end_month == 12


def test_reference_split_is_temporal_and_normalization_stops_at_month_16():
    samples = build_samples(generate_dataset())
    split = temporal_split(samples)

    assert samples.anchors.tolist() == list(range(12, 22))
    assert samples.normalization.fit_start_month == 1
    assert samples.normalization.fit_end_month == 16
    assert split.train.anchors.tolist() == [12, 13, 14, 15, 16]
    assert split.validation.anchors.tolist() == [17, 18]
    assert split.test.anchors.tolist() == [19, 20, 21]
    assert not np.shares_memory(split.train.y, split.test.y)
