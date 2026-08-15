from pathlib import Path

import numpy as np
import torch

from research.graph.builder import NormalizationStats, TemporalSamples
from research.graph.split import TemporalSplit
from research.models.tgnn import TemporalGCNBiLSTM
from research.training.train_tgnn import TGNNTrainingConfig, train_tgnn


def sample_partition(seed: int, anchors: list[int]) -> TemporalSamples:
    rng = np.random.default_rng(seed)
    batch = len(anchors)
    x = rng.normal(size=(batch, 4, 8, 5)).astype(np.float32)
    adjacency = np.broadcast_to(
        np.eye(8, dtype=np.float32), (batch, 4, 8, 8)
    ).copy()
    labels = np.asarray(
        [[(anchor + node) % 2 for node in range(8)] for anchor in anchors],
        dtype=np.uint8,
    )
    normalization = NormalizationStats(
        mean=np.zeros(5, dtype=np.float32),
        standard_deviation=np.ones(5, dtype=np.float32),
        normalization_id="n" * 64,
        fit_start_month=1,
        fit_end_month=4,
    )
    return TemporalSamples(
        x=x,
        adjacency=adjacency,
        y=labels,
        anchors=np.asarray(anchors, dtype=np.int16),
        snapshot_sha256=tuple("s" * 64 for _ in anchors),
        normalization=normalization,
    )


def tiny_split() -> TemporalSplit:
    return TemporalSplit(
        train=sample_partition(1, [12, 13]),
        validation=sample_partition(2, [17]),
        test=sample_partition(3, [19]),
    )


def test_tgnn_forward_has_node_logits_and_trainable_graph_path():
    torch.manual_seed(7)
    model = TemporalGCNBiLSTM(feature_count=5)
    x = torch.randn(2, 4, 3, 5)
    adjacency = torch.eye(3).expand(2, 4, 3, 3).clone()

    logits = model(x, adjacency)
    logits.sum().backward()

    assert logits.shape == (2, 3)
    assert model.gcn.weight.grad is not None
    assert torch.count_nonzero(model.gcn.weight.grad).item() > 0
    assert model.temporal.weight_ih_l0.grad is not None


def test_tgnn_trainer_fits_and_emits_checkpoint_and_metrics(tmp_path):
    result = train_tgnn(
        tiny_split(),
        config=TGNNTrainingConfig(
            max_epochs=4,
            early_stopping_patience=2,
            seed=20260815,
        ),
        output_dir=tmp_path,
    )

    assert result.checkpoint_path == Path(tmp_path) / "tgnn-v0.4.pt"
    assert result.checkpoint_path.exists()
    assert len(result.checkpoint_sha256) == 64
    assert 1 <= len(result.history) <= 4
    assert result.test_probabilities.shape == (8,)
    assert set(result.metrics) == {
        "roc_auc",
        "pr_auc",
        "f1",
        "precision",
        "recall",
        "confusion_matrix",
        "brier_score",
    }
    assert sum(sum(row) for row in result.metrics["confusion_matrix"]) == 8
