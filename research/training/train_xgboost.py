from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray
from xgboost import XGBClassifier

from research.graph.builder import TemporalSamples
from research.graph.split import TemporalSplit
from research.models.xgboost_model import XGBoostConfig
from research.training.metrics import evaluate_binary_predictions


@dataclass(frozen=True)
class TrainedXGBoost:
    model: XGBClassifier
    configuration: XGBoostConfig
    test_probabilities: NDArray[np.float32]
    metrics: dict[str, Any]
    artifact_path: Path
    manifest_path: Path
    artifact_sha256: str


def tabular_features(samples: TemporalSamples) -> np.ndarray:
    values = samples.x
    current = values[:, -1]
    mean_3 = values[:, -3:].mean(axis=1)
    mean_6 = values[:, -6:].mean(axis=1)
    mean_12 = values.mean(axis=1)
    change_3 = current - values[:, -3]
    change_6 = current - values[:, -6]
    change_12 = current - values[:, 0]
    features = np.concatenate(
        (current, mean_3, mean_6, mean_12, change_3, change_6, change_12),
        axis=-1,
    )
    return features.reshape(-1, features.shape[-1]).astype(np.float32)


def flattened_labels(samples: TemporalSamples) -> np.ndarray:
    return samples.y.reshape(-1).astype(np.uint8)


def train_xgboost(
    split: TemporalSplit,
    *,
    config: XGBoostConfig | None = None,
    output_dir: str | Path,
) -> TrainedXGBoost:
    active = config or XGBoostConfig()
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)

    train_x = tabular_features(split.train)
    validation_x = tabular_features(split.validation)
    test_x = tabular_features(split.test)
    train_y = flattened_labels(split.train)
    validation_y = flattened_labels(split.validation)
    test_y = flattened_labels(split.test)

    model = active.build()
    model.fit(
        train_x,
        train_y,
        eval_set=[(validation_x, validation_y)],
        verbose=False,
    )
    probabilities = model.predict_proba(test_x)[:, 1].astype(np.float32)
    metrics = evaluate_binary_predictions(test_y, probabilities)

    artifact_path = destination / "xgboost-v0.4.json"
    model.save_model(artifact_path)
    artifact_sha256 = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    manifest = {
        "artifact": artifact_path.name,
        "artifact_sha256": artifact_sha256,
        "configuration": active.to_dict(),
        "feature_count": int(train_x.shape[1]),
        "lifecycle_status": "evaluated",
        "metrics": metrics,
        "model_family": "xgboost",
        "normalization_id": split.train.normalization.normalization_id,
        "run_seed": active.random_state,
        "semantic_version": "0.4.0",
        "test_anchors": split.test.anchors.astype(int).tolist(),
        "training_anchors": split.train.anchors.astype(int).tolist(),
        "validation_anchors": split.validation.anchors.astype(int).tolist(),
    }
    manifest_path = destination / "xgboost-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return TrainedXGBoost(
        model=model,
        configuration=active,
        test_probabilities=probabilities,
        metrics=metrics,
        artifact_path=artifact_path,
        manifest_path=manifest_path,
        artifact_sha256=artifact_sha256,
    )
