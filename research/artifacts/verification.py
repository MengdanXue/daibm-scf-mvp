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
    comparison_metrics: dict[str, dict[str, Any]]


def _verify_hash(path: Path, expected: str) -> None:
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        raise ArtifactVerificationError(
            f"artifact hash mismatch for {path.name}"
        )


def _validated_metrics(
    metrics: Any,
    *,
    model_name: str,
) -> dict[str, Any]:
    if not isinstance(metrics, dict):
        raise ArtifactVerificationError(f"{model_name} metrics are invalid")
    roc_auc = metrics.get("roc_auc")
    if (
        isinstance(roc_auc, bool)
        or not isinstance(roc_auc, (int, float))
        or not 0 <= float(roc_auc) <= 1
    ):
        raise ArtifactVerificationError(
            f"{model_name} roc_auc is invalid"
        )
    return metrics


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
    dataset_manifest_path = reference / "dataset-manifest.json"
    feature_schema_path = reference / "feature-schema.json"
    if not all(
        path.is_file()
        for path in (
            artifact,
            input_bundle,
            dataset_manifest_path,
            feature_schema_path,
        )
    ):
        raise ArtifactVerificationError("promoted artifact files are missing")
    _verify_hash(artifact, manifest["artifact_sha256"])
    _verify_hash(input_bundle, manifest["input_bundle_sha256"])
    try:
        _verify_hash(
            dataset_manifest_path,
            manifest["dataset_manifest_sha256"],
        )
    except (KeyError, ArtifactVerificationError) as error:
        raise ArtifactVerificationError(
            "dataset manifest hash mismatch"
        ) from error
    try:
        _verify_hash(
            feature_schema_path,
            manifest["feature_schema_sha256"],
        )
    except (KeyError, ArtifactVerificationError) as error:
        raise ArtifactVerificationError(
            "feature schema hash mismatch"
        ) from error
    dataset_manifest = json.loads(
        dataset_manifest_path.read_text(encoding="utf-8")
    )
    feature_schema = json.loads(
        feature_schema_path.read_text(encoding="utf-8")
    )
    if dataset_manifest != manifest.get("dataset"):
        raise ArtifactVerificationError(
            "dataset manifest does not match model manifest"
        )
    if feature_schema != manifest.get("feature_schema"):
        raise ArtifactVerificationError(
            "feature schema does not match model manifest"
        )
    feature_names = feature_schema.get("names")
    if (
        not isinstance(feature_names, list)
        or len(feature_names) != int(manifest["feature_count"])
        or len(set(feature_names)) != len(feature_names)
        or not all(isinstance(name, str) and name for name in feature_names)
    ):
        raise ArtifactVerificationError("feature schema names are incompatible")
    for key, expected in (
        ("node_count", int(manifest["node_count"])),
        ("sequence_length", int(manifest["sequence_length"])),
    ):
        if key in feature_schema and int(feature_schema[key]) != expected:
            raise ArtifactVerificationError(
                f"feature schema {key} is incompatible"
            )
    expected_node_ordering = hashlib.sha256(
        "\n".join(
            f"E{index:04d}"
            for index in range(1, int(manifest["node_count"]) + 1)
        ).encode("ascii")
    ).hexdigest()
    if manifest.get("node_ordering_sha256") != expected_node_ordering:
        raise ArtifactVerificationError("node ordering contract mismatch")
    if len(str(manifest.get("normalization_id", ""))) != 64:
        raise ArtifactVerificationError("normalization identity is invalid")

    comparison_metrics: dict[str, dict[str, Any]] = {
        "tgnn": _validated_metrics(
            manifest.get("metrics"), model_name="tgnn"
        )
    }
    xgboost_manifest_path = reference / "xgboost-manifest.json"
    xgboost_artifact_path = reference / "xgboost-v0.4.json"
    if xgboost_manifest_path.is_file() or xgboost_artifact_path.is_file():
        if not xgboost_manifest_path.is_file() or not xgboost_artifact_path.is_file():
            raise ArtifactVerificationError("xgboost comparison files are incomplete")
        try:
            _verify_hash(
                xgboost_manifest_path,
                manifest["xgboost_manifest_sha256"],
            )
        except (KeyError, ArtifactVerificationError) as error:
            raise ArtifactVerificationError(
                "xgboost manifest hash mismatch"
            ) from error
        xgboost_manifest = json.loads(
            xgboost_manifest_path.read_text(encoding="utf-8")
        )
        if (
            xgboost_manifest.get("model_family") != "xgboost"
            or xgboost_manifest.get("normalization_id")
            != manifest.get("normalization_id")
        ):
            raise ArtifactVerificationError(
                "xgboost comparison manifest is incompatible"
            )
        try:
            _verify_hash(
                xgboost_artifact_path,
                xgboost_manifest["artifact_sha256"],
            )
        except (KeyError, ArtifactVerificationError) as error:
            raise ArtifactVerificationError(
                "xgboost comparison hash mismatch"
            ) from error
        comparison_metrics["xgboost"] = _validated_metrics(
            xgboost_manifest.get("metrics"), model_name="xgboost"
        )

    with np.load(input_bundle, allow_pickle=False) as bundle:
        raw_x = np.asarray(bundle["node_features"])
        raw_adjacency = np.asarray(bundle["adjacency"])
    if raw_x.dtype != np.float32 or raw_adjacency.dtype != np.float32:
        raise ArtifactVerificationError("reference input dtype mismatch")
    x = np.asarray(raw_x, dtype=np.float32)
    adjacency = np.asarray(raw_adjacency, dtype=np.float32)
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
    onnx_inputs = session.get_inputs()
    names = [value.name for value in onnx_inputs]
    if names != manifest["input_names"]:
        raise ArtifactVerificationError("ONNX input names do not match manifest")
    if any(value.type != "tensor(float)" for value in onnx_inputs):
        raise ArtifactVerificationError("ONNX input dtype mismatch")
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
        comparison_metrics=comparison_metrics,
    )
