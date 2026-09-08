from __future__ import annotations

import numpy as np
import torch

from research.conference.direction_a.data import (
    PropagationGeneratorConfig,
    generate_propagation_dataset,
)
from research.conference.direction_a.models import combine_bidirectional_hidden
from research.conference.direction_a.protocol import (
    ConferenceProtocol,
    GraphCondition,
    apply_graph_condition,
    build_conference_split,
    build_industry_holdout,
    choose_validation_threshold,
)
from research.conference.direction_a.runner import run_smoke_experiment


def _dataset(seed: int = 20260909):
    return generate_propagation_dataset(
        PropagationGeneratorConfig(
            enterprise_count=72,
            months=30,
            relationships_per_enterprise=3,
            seed=seed,
        )
    )


def test_propagation_dataset_is_versioned_deterministic_and_has_ground_truth() -> None:
    first = _dataset()
    second = _dataset()

    assert first.dataset_version == "synthetic-scf-propagation-v1"
    assert first.schema_version == "scf-propagation-data-v1"
    assert first.content_sha256 == second.content_sha256
    assert first.propagation_contribution.shape == first.severe_events.shape
    assert first.propagation_events.shape == first.severe_events.shape
    assert int(first.propagation_events.sum()) > 0
    assert np.all(first.propagation_events <= first.severe_events)
    assert np.any(first.relationship_active_months[:, 0] > 1)
    assert np.any(first.relationship_active_months[:, 1] < first.config.months)


def test_zero_propagation_control_has_no_propagation_ground_truth() -> None:
    dataset = generate_propagation_dataset(
        PropagationGeneratorConfig(
            enterprise_count=64,
            months=30,
            relationships_per_enterprise=3,
            propagation_strength=0.0,
            seed=20260910,
        )
    )

    assert np.count_nonzero(dataset.propagation_contribution) == 0
    assert int(dataset.propagation_events.sum()) == 0


def test_conference_split_is_strictly_chronological_and_normalization_is_train_only() -> None:
    split = build_conference_split(_dataset(), ConferenceProtocol())

    assert int(split.train.anchors.max()) < int(split.validation.anchors.min())
    assert int(split.validation.anchors.max()) < int(split.test.anchors.min())
    assert split.normalization.fit_end_month < int(split.validation.anchors.min())
    assert split.split_version == "direction-a-chronological-v1"
    assert split.train.y.size > 0
    assert split.validation.y.size > 0
    assert split.test.y.size > 0


def test_validation_threshold_selection_is_explicit_and_deterministic() -> None:
    labels = np.asarray([0, 0, 0, 1, 1, 1], dtype=np.uint8)
    probabilities = np.asarray([0.05, 0.20, 0.45, 0.55, 0.60, 0.90])

    selected = choose_validation_threshold(
        labels,
        probabilities,
        candidates=(0.30, 0.50, 0.70),
    )

    assert selected.threshold == 0.50
    assert selected.selection_partition == "validation"
    assert selected.objective == "f1"
    assert selected.candidate_count == 3


def test_graph_conditions_are_explicit_and_preserve_tensor_shape() -> None:
    split = build_conference_split(_dataset(), ConferenceProtocol())
    observed = split.train.adjacency

    identity = apply_graph_condition(
        observed,
        GraphCondition.IDENTITY_ONLY,
        seed=17,
    )
    permuted = apply_graph_condition(
        observed,
        GraphCondition.NODE_PERMUTED,
        seed=17,
    )
    static = apply_graph_condition(
        observed,
        GraphCondition.STATIC_FIRST,
        seed=17,
    )

    assert identity.shape == observed.shape
    assert permuted.shape == observed.shape
    assert static.shape == observed.shape
    assert np.allclose(identity, np.eye(observed.shape[-1], dtype=np.float32))
    assert np.allclose(
        np.sort(permuted.reshape(-1)),
        np.sort(observed.reshape(-1)),
    )
    assert np.allclose(static[:, 1:], static[:, :1])


def test_cross_industry_holdout_keeps_target_industry_out_of_train_and_validation() -> None:
    dataset = _dataset()
    split = build_conference_split(dataset, ConferenceProtocol())
    holdout = build_industry_holdout(split, dataset.industry_codes, held_out_industry=2)

    assert np.all(dataset.industry_codes[holdout.train.node_indices] != 2)
    assert np.all(dataset.industry_codes[holdout.validation.node_indices] != 2)
    assert np.all(dataset.industry_codes[holdout.test.node_indices] == 2)
    assert set(holdout.train.node_indices).isdisjoint(set(holdout.test.node_indices))
    assert holdout.protocol == "strict_inductive_leave_one_industry_out_v1"


def test_bidirectional_state_combines_true_forward_and_backward_final_hidden_states() -> None:
    hidden = torch.tensor(
        [
            [[1.0, 2.0], [3.0, 4.0]],
            [[10.0, 20.0], [30.0, 40.0]],
        ]
    )

    combined = combine_bidirectional_hidden(hidden)

    assert torch.equal(
        combined,
        torch.tensor([[1.0, 2.0, 10.0, 20.0], [3.0, 4.0, 30.0, 40.0]]),
    )


def test_smoke_experiment_covers_five_models_and_marks_results_non_publishable(tmp_path) -> None:
    pack = run_smoke_experiment(
        dataset_config=PropagationGeneratorConfig(
            enterprise_count=48,
            months=24,
            relationships_per_enterprise=2,
            seed=20260911,
        ),
        protocol=ConferenceProtocol(window_months=8, horizon_months=2),
        seeds=(11,),
        max_epochs=2,
        output_dir=tmp_path,
    )

    assert pack["purpose"] == "SMOKE_ONLY_NOT_FOR_PAPER"
    assert pack["dataset_version"] == "synthetic-scf-propagation-v1"
    assert pack["split_version"] == "direction-a-chronological-v1"
    assert set(pack["runs"][0]["models"]) == {
        "logistic_regression",
        "random_forest",
        "lstm",
        "gcn",
        "tgnn",
    }
    for result in pack["runs"][0]["models"].values():
        assert result["threshold_source"] == "validation"
        assert result["checkpoint_sha256"]
        assert "test_metrics" in result
    assert pack["provenance"]["feature_schema"] == "scf-propagation-feature-v1"
    assert pack["provenance"]["run_config"]["max_epochs"] == 2
