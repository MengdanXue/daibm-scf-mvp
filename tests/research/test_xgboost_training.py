import hashlib
import json

import numpy as np

from research.data.generator import generate_dataset
from research.graph.builder import build_samples
from research.graph.split import temporal_split
from research.models.xgboost_model import XGBoostConfig
from research.training.train_xgboost import train_xgboost


def test_xgboost_performs_real_fit_and_emits_evidence(tmp_path):
    split = temporal_split(build_samples(generate_dataset()))
    config = XGBoostConfig(n_estimators=12, random_state=20260815)

    result = train_xgboost(split, config=config, output_dir=tmp_path)

    assert result.test_probabilities.shape == (1500,)
    assert np.unique(np.round(result.test_probabilities, 6)).size > 10
    assert set(result.metrics) == {
        "roc_auc",
        "pr_auc",
        "f1",
        "precision",
        "recall",
        "confusion_matrix",
        "brier_score",
    }
    assert all(
        0.0 <= result.metrics[name] <= 1.0
        for name in (
            "roc_auc",
            "pr_auc",
            "f1",
            "precision",
            "recall",
            "brier_score",
        )
    )
    assert sum(sum(row) for row in result.metrics["confusion_matrix"]) == 1500

    model_path = tmp_path / "xgboost-v0.4.json"
    manifest_path = tmp_path / "xgboost-manifest.json"
    assert model_path.exists()
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest_path.read_bytes().endswith(b"\n")
    assert b"\r\n" not in manifest_path.read_bytes()
    assert manifest["lifecycle_status"] == "evaluated"
    assert manifest["model_family"] == "xgboost"
    assert manifest["configuration"]["max_depth"] == 4
    assert manifest["artifact_sha256"] == hashlib.sha256(
        model_path.read_bytes()
    ).hexdigest()


def test_xgboost_reference_seed_is_deterministic(tmp_path):
    split = temporal_split(build_samples(generate_dataset()))
    config = XGBoostConfig(n_estimators=8, random_state=20260815)

    first = train_xgboost(split, config=config, output_dir=tmp_path / "first")
    second = train_xgboost(split, config=config, output_dir=tmp_path / "second")

    assert np.array_equal(first.test_probabilities, second.test_probabilities)
    assert first.metrics == second.metrics
