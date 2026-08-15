from __future__ import annotations

import hashlib
import json
import random
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import torch
from numpy.typing import NDArray
from torch import Tensor, nn

from research.graph.builder import TemporalSamples
from research.graph.split import TemporalSplit
from research.models.tgnn import TemporalGCNBiLSTM
from research.training.metrics import evaluate_binary_predictions


@dataclass(frozen=True)
class TGNNTrainingConfig:
    spatial_dimension: int = 32
    temporal_hidden: int = 32
    dropout: float = 0.2
    learning_rate: float = 0.001
    max_epochs: int = 100
    early_stopping_patience: int = 10
    seed: int = 20260815

    def to_dict(self) -> dict[str, int | float]:
        return asdict(self)


@dataclass(frozen=True)
class TrainedTGNN:
    model: TemporalGCNBiLSTM
    configuration: TGNNTrainingConfig
    history: tuple[dict[str, float | int], ...]
    test_probabilities: NDArray[np.float32]
    metrics: dict[str, Any]
    checkpoint_path: Path
    checkpoint_sha256: str
    run_manifest_path: Path


def _seed_everything(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.use_deterministic_algorithms(True)


def _tensors(samples: TemporalSamples) -> tuple[Tensor, Tensor, Tensor]:
    return (
        torch.from_numpy(np.array(samples.x, copy=True)),
        torch.from_numpy(np.array(samples.adjacency, copy=True)),
        torch.from_numpy(np.array(samples.y, copy=True)).float(),
    )


def train_tgnn(
    split: TemporalSplit,
    *,
    config: TGNNTrainingConfig | None = None,
    output_dir: str | Path,
) -> TrainedTGNN:
    active = config or TGNNTrainingConfig()
    _seed_everything(active.seed)
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)

    feature_count = int(split.train.x.shape[-1])
    model = TemporalGCNBiLSTM(
        feature_count,
        spatial_dimension=active.spatial_dimension,
        temporal_hidden=active.temporal_hidden,
        dropout=active.dropout,
    )
    optimizer = torch.optim.Adam(model.parameters(), lr=active.learning_rate)
    train_x, train_adjacency, train_y = _tensors(split.train)
    validation_x, validation_adjacency, validation_y = _tensors(
        split.validation
    )
    test_x, test_adjacency, test_y = _tensors(split.test)
    train_aggregated = torch.matmul(train_adjacency, train_x)
    validation_aggregated = torch.matmul(validation_adjacency, validation_x)
    test_aggregated = torch.matmul(test_adjacency, test_x)

    positives = float(train_y.sum().item())
    negatives = float(train_y.numel() - positives)
    positive_weight = negatives / max(positives, 1.0)
    criterion = nn.BCEWithLogitsLoss(
        pos_weight=torch.tensor(positive_weight, dtype=torch.float32)
    )

    best_validation = float("inf")
    best_state: dict[str, Tensor] | None = None
    epochs_without_improvement = 0
    history: list[dict[str, float | int]] = []
    for epoch in range(1, active.max_epochs + 1):
        model.train()
        optimizer.zero_grad(set_to_none=True)
        train_logits = model.forward_from_aggregated(train_aggregated)
        train_loss = criterion(train_logits, train_y)
        train_loss.backward()
        optimizer.step()

        model.eval()
        with torch.no_grad():
            validation_logits = model.forward_from_aggregated(
                validation_aggregated
            )
            validation_loss = criterion(validation_logits, validation_y)
        history.append(
            {
                "epoch": epoch,
                "train_loss": float(train_loss.item()),
                "validation_loss": float(validation_loss.item()),
            }
        )
        if validation_loss.item() < best_validation - 1e-8:
            best_validation = float(validation_loss.item())
            best_state = {
                name: value.detach().cpu().clone()
                for name, value in model.state_dict().items()
            }
            epochs_without_improvement = 0
        else:
            epochs_without_improvement += 1
            if epochs_without_improvement >= active.early_stopping_patience:
                break

    if best_state is None:
        raise RuntimeError("TGNN training did not produce a valid checkpoint")
    model.load_state_dict(best_state)
    model.eval()
    with torch.no_grad():
        test_probabilities = torch.sigmoid(
            model.forward_from_aggregated(test_aggregated)
        ).reshape(-1)
    probabilities = test_probabilities.numpy().astype(np.float32)
    metrics = evaluate_binary_predictions(
        test_y.numpy().reshape(-1), probabilities
    )

    checkpoint_path = destination / "tgnn-v0.4.pt"
    torch.save(
        {
            "configuration": active.to_dict(),
            "feature_count": feature_count,
            "model_state_dict": best_state,
            "normalization_id": split.train.normalization.normalization_id,
        },
        checkpoint_path,
    )
    checkpoint_sha256 = hashlib.sha256(
        checkpoint_path.read_bytes()
    ).hexdigest()
    run_manifest_path = destination / "tgnn-run.json"
    run_manifest_path.write_text(
        json.dumps(
            {
                "checkpoint": checkpoint_path.name,
                "checkpoint_sha256": checkpoint_sha256,
                "configuration": active.to_dict(),
                "feature_count": feature_count,
                "history": history,
                "metrics": metrics,
                "normalization_id": split.train.normalization.normalization_id,
                "status": "completed",
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return TrainedTGNN(
        model=model,
        configuration=active,
        history=tuple(history),
        test_probabilities=probabilities,
        metrics=metrics,
        checkpoint_path=checkpoint_path,
        checkpoint_sha256=checkpoint_sha256,
        run_manifest_path=run_manifest_path,
    )
