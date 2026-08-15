from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import numpy as np

from research.graph.builder import TemporalSamples
from research.training.export_onnx import verify_onnx_parity
from research.training.train_tgnn import TrainedTGNN


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def promote_tgnn(
    *,
    trained: TrainedTGNN,
    onnx_path: str | Path,
    reference_samples: TemporalSamples,
    dataset_manifest: dict[str, Any],
    feature_schema: dict[str, Any],
    destination: str | Path,
    parity_tolerance: float = 1e-5,
) -> dict[str, Any]:
    reference = Path(destination)
    reference.mkdir(parents=True, exist_ok=True)
    source_onnx = Path(onnx_path)
    sample_x = np.asarray(reference_samples.x[:1], dtype=np.float32)
    sample_adjacency = np.asarray(
        reference_samples.adjacency[:1], dtype=np.float32
    )
    parity = verify_onnx_parity(
        trained.model,
        source_onnx,
        sample_x,
        sample_adjacency,
    )
    if parity > parity_tolerance:
        raise ValueError(
            f"ONNX parity difference {parity} exceeds {parity_tolerance}"
        )

    promoted_onnx = reference / "tgnn-v0.4.onnx"
    shutil.copy2(source_onnx, promoted_onnx)
    input_bundle = reference / "reference-inputs.npz"
    np.savez_compressed(
        input_bundle,
        node_features=sample_x,
        adjacency=sample_adjacency,
        anchor=np.asarray(reference_samples.anchors[:1], dtype=np.int16),
    )
    (reference / "dataset-manifest.json").write_text(
        json.dumps(dataset_manifest, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    (reference / "feature-schema.json").write_text(
        json.dumps(feature_schema, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    manifest = {
        "artifact": promoted_onnx.name,
        "artifact_sha256": _sha256(promoted_onnx),
        "checkpoint_sha256": trained.checkpoint_sha256,
        "dataset": dataset_manifest,
        "deployment_slot": "default",
        "feature_count": int(sample_x.shape[-1]),
        "feature_schema": feature_schema,
        "inference_format": "onnx",
        "input_bundle": input_bundle.name,
        "input_bundle_sha256": _sha256(input_bundle),
        "input_names": ["node_features", "adjacency"],
        "lifecycle_status": "promoted",
        "metrics": trained.metrics,
        "model_family": "tgnn",
        "model_name": "minimal-gcn-bilstm",
        "node_count": int(sample_x.shape[2]),
        "normalization_id": reference_samples.normalization.normalization_id,
        "onnx_opset": 18,
        "parity_max_absolute_difference": parity,
        "parity_tolerance": parity_tolerance,
        "run_seed": trained.configuration.seed,
        "semantic_version": "0.4.0",
        "sequence_length": int(sample_x.shape[1]),
        "snapshot_sha256": reference_samples.snapshot_sha256[0],
        "training_configuration": trained.configuration.to_dict(),
    }
    (reference / "model-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    from research.artifacts.verification import verify_reference_artifact

    verify_reference_artifact(reference)
    return manifest
