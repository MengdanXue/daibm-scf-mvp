import json

import numpy as np
import pytest

from research.artifacts.registry import promote_tgnn
from research.artifacts.verification import (
    ArtifactVerificationError,
    verify_reference_artifact,
)
from research.training.export_onnx import export_onnx, verify_onnx_parity
from research.training.train_tgnn import TGNNTrainingConfig, train_tgnn
from tests.research.test_tgnn_training import tiny_split


def test_onnx_export_matches_pytorch_and_promoted_artifact_verifies(tmp_path):
    split = tiny_split()
    trained = train_tgnn(
        split,
        config=TGNNTrainingConfig(max_epochs=2, early_stopping_patience=1),
        output_dir=tmp_path / "run",
    )
    onnx_path = export_onnx(
        trained.model,
        split.test.x[:1],
        split.test.adjacency[:1],
        tmp_path / "run" / "tgnn-v0.4.onnx",
    )

    difference = verify_onnx_parity(
        trained.model,
        onnx_path,
        split.test.x[:1],
        split.test.adjacency[:1],
    )
    assert difference <= 1e-5

    reference = tmp_path / "reference"
    manifest = promote_tgnn(
        trained=trained,
        onnx_path=onnx_path,
        reference_samples=split.test,
        dataset_manifest={
            "dataset_name": "unit-synthetic",
            "dataset_version": "1.0.0",
            "schema_version": "scf-data-v1",
            "content_sha256": "d" * 64,
        },
        feature_schema={
            "version": "graph-features-v1",
            "names": [f"f{index}" for index in range(5)],
        },
        destination=reference,
        parity_tolerance=1e-5,
    )
    verified = verify_reference_artifact(reference)

    assert manifest["lifecycle_status"] == "promoted"
    assert manifest["deployment_slot"] == "default"
    assert verified.input_x.shape == (1, 4, 8, 5)
    assert verified.input_adjacency.shape == (1, 4, 8, 8)
    assert verified.session.get_inputs()[0].name == "node_features"
    assert json.loads(
        (reference / "model-manifest.json").read_text(encoding="utf-8")
    )["checkpoint_sha256"] == trained.checkpoint_sha256


def test_artifact_verifier_rejects_corrupted_onnx(tmp_path):
    split = tiny_split()
    trained = train_tgnn(
        split,
        config=TGNNTrainingConfig(max_epochs=1, early_stopping_patience=1),
        output_dir=tmp_path / "run",
    )
    onnx_path = export_onnx(
        trained.model,
        split.test.x[:1],
        split.test.adjacency[:1],
        tmp_path / "run" / "tgnn-v0.4.onnx",
    )
    reference = tmp_path / "reference"
    promote_tgnn(
        trained=trained,
        onnx_path=onnx_path,
        reference_samples=split.test,
        dataset_manifest={
            "dataset_name": "unit-synthetic",
            "dataset_version": "1.0.0",
            "schema_version": "scf-data-v1",
            "content_sha256": "d" * 64,
        },
        feature_schema={
            "version": "graph-features-v1",
            "names": [f"f{index}" for index in range(5)],
        },
        destination=reference,
    )
    artifact = reference / "tgnn-v0.4.onnx"
    artifact.write_bytes(artifact.read_bytes() + b"corruption")

    with pytest.raises(ArtifactVerificationError, match="hash"):
        verify_reference_artifact(reference)
