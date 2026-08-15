from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import onnxruntime as ort


class ArtifactVerificationError(RuntimeError):
    pass


@dataclass(frozen=True)
class VerifiedArtifact:
    root: Path
    manifest: dict[str, Any]
    input_x: np.ndarray
    input_adjacency: np.ndarray
    session: ort.InferenceSession


def _verify_hash(path: Path, expected: str) -> None:
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        raise ArtifactVerificationError(
            f"artifact hash mismatch for {path.name}"
        )


def verify_reference_artifact(
    root: str | Path,
) -> VerifiedArtifact:
    reference = Path(root)
    manifest_path = reference / "model-manifest.json"
    if not manifest_path.is_file():
        raise ArtifactVerificationError("model manifest is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("lifecycle_status") != "promoted":
        raise ArtifactVerificationError("model is not promoted")
    if manifest.get("deployment_slot") != "default":
        raise ArtifactVerificationError("model is not assigned to default slot")

    artifact = reference / manifest["artifact"]
    input_bundle = reference / manifest["input_bundle"]
    if not artifact.is_file() or not input_bundle.is_file():
        raise ArtifactVerificationError("promoted artifact files are missing")
    _verify_hash(artifact, manifest["artifact_sha256"])
    _verify_hash(input_bundle, manifest["input_bundle_sha256"])

    with np.load(input_bundle, allow_pickle=False) as bundle:
        x = np.asarray(bundle["node_features"], dtype=np.float32)
        adjacency = np.asarray(bundle["adjacency"], dtype=np.float32)
    expected_x_shape = (
        1,
        int(manifest["sequence_length"]),
        int(manifest["node_count"]),
        int(manifest["feature_count"]),
    )
    expected_adjacency_shape = (
        1,
        int(manifest["sequence_length"]),
        int(manifest["node_count"]),
        int(manifest["node_count"]),
    )
    if x.shape != expected_x_shape or adjacency.shape != expected_adjacency_shape:
        raise ArtifactVerificationError("reference input shape mismatch")
    if not np.isfinite(x).all() or not np.isfinite(adjacency).all():
        raise ArtifactVerificationError("reference inputs contain non-finite values")

    session = ort.InferenceSession(
        str(artifact), providers=("CPUExecutionProvider",)
    )
    names = [value.name for value in session.get_inputs()]
    if names != manifest["input_names"]:
        raise ArtifactVerificationError("ONNX input names do not match manifest")
    logits = session.run(
        ("logits",),
        {"node_features": x, "adjacency": adjacency},
    )[0]
    if logits.shape != (1, int(manifest["node_count"])):
        raise ArtifactVerificationError("ONNX output shape mismatch")
    if not np.isfinite(logits).all():
        raise ArtifactVerificationError("ONNX output contains non-finite values")
    return VerifiedArtifact(
        root=reference,
        manifest=manifest,
        input_x=x,
        input_adjacency=adjacency,
        session=session,
    )
