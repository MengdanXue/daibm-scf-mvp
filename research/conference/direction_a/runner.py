from __future__ import annotations

import hashlib
import json
import pickle
import random
from pathlib import Path
from typing import Any

import numpy as np
import torch
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from torch import Tensor, nn

from research.conference.direction_a.data import (
    FEATURE_SCHEMA_VERSION,
    PropagationGeneratorConfig,
    generate_propagation_dataset,
)
from research.conference.direction_a.models import (
    ConferenceGCN,
    ConferenceLSTM,
    ConferenceTemporalGCNBiLSTM,
)
from research.conference.direction_a.protocol import (
    ConferenceProtocol,
    ConferenceSamples,
    ConferenceSplit,
    build_conference_split,
    choose_validation_threshold,
)


MODEL_VERSION = "direction-a-models-v1"


def _seed_everything(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.use_deterministic_algorithms(True)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _metrics(labels: np.ndarray, probabilities: np.ndarray, threshold: float) -> dict[str, Any]:
    truth = np.asarray(labels, dtype=np.uint8).reshape(-1)
    scores = np.asarray(probabilities, dtype=np.float64).reshape(-1)
    predicted = (scores >= threshold).astype(np.uint8)
    result: dict[str, Any] = {
        "f1": float(f1_score(truth, predicted, zero_division=0)),
        "precision": float(precision_score(truth, predicted, zero_division=0)),
        "recall": float(recall_score(truth, predicted, zero_division=0)),
        "confusion_matrix": confusion_matrix(truth, predicted, labels=(0, 1)).astype(int).tolist(),
        "positive_rate": float(truth.mean()),
    }
    if np.unique(truth).size >= 2:
        result["roc_auc"] = float(roc_auc_score(truth, scores))
        result["pr_auc"] = float(average_precision_score(truth, scores))
    else:
        result["roc_auc"] = None
        result["pr_auc"] = None
    return result


def _early_warning_summary(
    samples: ConferenceSamples,
    probabilities: np.ndarray,
    threshold: float,
) -> dict[str, Any]:
    scores = np.asarray(probabilities).reshape(len(samples.anchors), -1)
    alerts = scores >= threshold
    leads: list[int] = []
    for node in range(alerts.shape[1]):
        positive_positions = np.flatnonzero(samples.y[:, node] == 1)
        for event_position in positive_positions:
            prior_alerts = np.flatnonzero(alerts[: event_position + 1, node])
            if prior_alerts.size:
                leads.append(int(samples.anchors[event_position] - samples.anchors[prior_alerts[0]]))
    return {
        "definition": "anchor-month lead before a positive future-horizon label; descriptive only",
        "evaluable_positive_windows": int(samples.y.sum()),
        "alerted_positive_windows": len(leads),
        "mean_lead_months": float(np.mean(leads)) if leads else None,
    }


def _flatten(samples: ConferenceSamples) -> np.ndarray:
    return samples.x.transpose(0, 2, 1, 3).reshape(
        samples.x.shape[0] * samples.x.shape[2], -1
    )


def _labels(samples: ConferenceSamples) -> np.ndarray:
    return samples.y.reshape(-1).astype(np.uint8)


def _save_pickle(value: object, path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pickle.dumps(value, protocol=5))
    return _sha256(path)


def _classical_model(
    name: str,
    split: ConferenceSplit,
    seed: int,
    output: Path,
) -> dict[str, Any]:
    if name == "logistic_regression":
        model: Any = LogisticRegression(
            class_weight="balanced",
            max_iter=500,
            random_state=seed,
        )
    elif name == "random_forest":
        model = RandomForestClassifier(
            n_estimators=80,
            class_weight="balanced_subsample",
            random_state=seed,
            n_jobs=1,
        )
    else:
        raise ValueError(name)
    train_labels = _labels(split.train)
    if np.unique(train_labels).size < 2:
        raise RuntimeError(f"{name} requires both classes in the training partition")
    model.fit(_flatten(split.train), train_labels)
    validation_probabilities = model.predict_proba(_flatten(split.validation))[:, 1]
    test_probabilities = model.predict_proba(_flatten(split.test))[:, 1]
    selected = choose_validation_threshold(_labels(split.validation), validation_probabilities)
    artifact = output / f"{name}.pkl"
    checkpoint_sha256 = _save_pickle(model, artifact)
    return _result_payload(split.test, test_probabilities, selected.threshold, checkpoint_sha256)


def _sample_tensors(samples: ConferenceSamples) -> tuple[Tensor, Tensor, Tensor]:
    return (
        torch.from_numpy(np.array(samples.x, copy=True)),
        torch.from_numpy(np.array(samples.adjacency, copy=True)),
        torch.from_numpy(np.array(samples.y, copy=True)).float(),
    )


def _deep_probabilities(model: nn.Module, samples: ConferenceSamples, uses_graph: bool) -> np.ndarray:
    x, adjacency, _ = _sample_tensors(samples)
    model.eval()
    with torch.no_grad():
        logits = model(x, adjacency) if uses_graph else model(x)
        return torch.sigmoid(logits).cpu().numpy().reshape(-1)


def _deep_model(
    name: str,
    split: ConferenceSplit,
    seed: int,
    max_epochs: int,
    output: Path,
) -> dict[str, Any]:
    _seed_everything(seed)
    feature_count = int(split.train.x.shape[-1])
    if name == "lstm":
        model: nn.Module = ConferenceLSTM(feature_count)
        uses_graph = False
    elif name == "gcn":
        model = ConferenceGCN(feature_count)
        uses_graph = True
    elif name == "tgnn":
        model = ConferenceTemporalGCNBiLSTM(feature_count)
        uses_graph = True
    else:
        raise ValueError(name)

    train_x, train_adjacency, train_y = _sample_tensors(split.train)
    validation_x, validation_adjacency, validation_y = _sample_tensors(split.validation)
    positives = float(train_y.sum().item())
    negatives = float(train_y.numel() - positives)
    if positives == 0 or negatives == 0:
        raise RuntimeError(f"{name} requires both classes in the training partition")
    criterion = nn.BCEWithLogitsLoss(
        pos_weight=torch.tensor(negatives / positives, dtype=torch.float32)
    )
    optimizer = torch.optim.Adam(model.parameters(), lr=0.002)
    best_state: dict[str, Tensor] | None = None
    best_validation = float("inf")
    for _ in range(max_epochs):
        model.train()
        optimizer.zero_grad(set_to_none=True)
        train_logits = model(train_x, train_adjacency) if uses_graph else model(train_x)
        loss = criterion(train_logits, train_y)
        loss.backward()
        optimizer.step()
        model.eval()
        with torch.no_grad():
            validation_logits = (
                model(validation_x, validation_adjacency) if uses_graph else model(validation_x)
            )
            validation_loss = float(criterion(validation_logits, validation_y).item())
        if validation_loss < best_validation:
            best_validation = validation_loss
            best_state = {
                key: value.detach().cpu().clone()
                for key, value in model.state_dict().items()
            }
    if best_state is None:
        raise RuntimeError(f"{name} did not produce a checkpoint")
    model.load_state_dict(best_state)
    validation_probabilities = _deep_probabilities(model, split.validation, uses_graph)
    test_probabilities = _deep_probabilities(model, split.test, uses_graph)
    selected = choose_validation_threshold(_labels(split.validation), validation_probabilities)
    artifact = output / f"{name}.pt"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "model_version": MODEL_VERSION,
            "model": name,
            "seed": seed,
            "feature_count": feature_count,
            "state_dict": best_state,
        },
        artifact,
    )
    return _result_payload(split.test, test_probabilities, selected.threshold, _sha256(artifact))


def _result_payload(
    test: ConferenceSamples,
    probabilities: np.ndarray,
    threshold: float,
    checkpoint_sha256: str,
) -> dict[str, Any]:
    propagation_truth = test.propagation_y.reshape(-1)
    return {
        "checkpoint_sha256": checkpoint_sha256,
        "model_version": MODEL_VERSION,
        "threshold": threshold,
        "threshold_source": "validation",
        "test_metrics": _metrics(_labels(test), probabilities, threshold),
        "propagation_metrics": _metrics(propagation_truth, probabilities, threshold),
        "early_warning": _early_warning_summary(test, probabilities, threshold),
    }


def run_smoke_experiment(
    *,
    dataset_config: PropagationGeneratorConfig,
    protocol: ConferenceProtocol,
    seeds: tuple[int, ...],
    max_epochs: int,
    output_dir: str | Path,
) -> dict[str, Any]:
    if not seeds:
        raise ValueError("at least one seed is required")
    if max_epochs < 1:
        raise ValueError("max_epochs must be positive")
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    runs: list[dict[str, Any]] = []
    dataset_hashes: list[str] = []
    for seed in seeds:
        config = PropagationGeneratorConfig(
            enterprise_count=dataset_config.enterprise_count,
            months=dataset_config.months,
            relationships_per_enterprise=dataset_config.relationships_per_enterprise,
            seed=dataset_config.seed + seed,
            propagation_strength=dataset_config.propagation_strength,
            shock_probability=dataset_config.shock_probability,
        )
        dataset = generate_propagation_dataset(config)
        dataset_hashes.append(dataset.content_sha256)
        split = build_conference_split(dataset, protocol)
        run_output = destination / f"seed-{seed}"
        models = {
            "logistic_regression": _classical_model("logistic_regression", split, seed, run_output),
            "random_forest": _classical_model("random_forest", split, seed, run_output),
            "lstm": _deep_model("lstm", split, seed, max_epochs, run_output),
            "gcn": _deep_model("gcn", split, seed, max_epochs, run_output),
            "tgnn": _deep_model("tgnn", split, seed, max_epochs, run_output),
        }
        runs.append({"seed": seed, "dataset_sha256": dataset.content_sha256, "models": models})

    pack: dict[str, Any] = {
        "purpose": "SMOKE_ONLY_NOT_FOR_PAPER",
        "dataset_version": "synthetic-scf-propagation-v1",
        "split_version": "direction-a-chronological-v1",
        "runs": runs,
        "provenance": {
            "dataset_sha256": dataset_hashes,
            "feature_schema": FEATURE_SCHEMA_VERSION,
            "model_version": MODEL_VERSION,
            "run_config": {
                "max_epochs": max_epochs,
                "seeds": list(seeds),
                "window_months": protocol.window_months,
                "horizon_months": protocol.horizon_months,
            },
        },
    }
    (destination / "smoke-manifest.json").write_text(
        json.dumps(pack, sort_keys=True, indent=2), encoding="utf-8"
    )
    return pack
