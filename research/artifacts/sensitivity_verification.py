from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path, PurePosixPath
from typing import Any

import numpy as np


class SensitivityVerificationError(RuntimeError):
    """Raised when an exploratory sensitivity pack is incomplete or altered."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _pack_path(root: Path, relative: object) -> Path:
    if not isinstance(relative, str):
        raise SensitivityVerificationError("declared file path is invalid")
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts or not pure.parts:
        raise SensitivityVerificationError(f"unsafe declared file path: {relative}")
    return root.joinpath(*pure.parts)


def _load_json(path: Path, description: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SensitivityVerificationError(f"{description} is unreadable") from error
    if not isinstance(payload, dict):
        raise SensitivityVerificationError(f"{description} must be an object")
    return payload


def _load_array(path: Path, description: str) -> np.ndarray:
    try:
        value = np.load(path, allow_pickle=False)
    except (OSError, ValueError) as error:
        raise SensitivityVerificationError(f"{description} is unreadable") from error
    array = np.asarray(value)
    if array.ndim != 1 or array.size == 0:
        raise SensitivityVerificationError(f"{description} must be a non-empty vector")
    return array


def _finite_metric_payload(metrics: object, model: str) -> None:
    if not isinstance(metrics, dict):
        raise SensitivityVerificationError(f"{model} metrics are invalid")
    required = {
        "roc_auc",
        "pr_auc",
        "f1",
        "precision",
        "recall",
        "confusion_matrix",
        "brier_score",
    }
    if not required.issubset(metrics):
        raise SensitivityVerificationError(f"{model} metrics are incomplete")
    for name in required - {"confusion_matrix"}:
        value = metrics[name]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
        ):
            raise SensitivityVerificationError(f"{model}.{name} is invalid")
    matrix = metrics["confusion_matrix"]
    if (
        not isinstance(matrix, list)
        or len(matrix) != 2
        or any(not isinstance(row, list) or len(row) != 2 for row in matrix)
        or any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for row in matrix
            for value in row
        )
    ):
        raise SensitivityVerificationError(f"{model} confusion matrix is invalid")


def _verify_seed(root: Path, entry: object) -> dict[str, Any]:
    if not isinstance(entry, dict) or not isinstance(entry.get("seed"), int):
        raise SensitivityVerificationError("seed entry is invalid")
    seed = int(entry["seed"])
    files = entry.get("files")
    if not isinstance(files, dict):
        raise SensitivityVerificationError(f"seed {seed} file manifest is invalid")
    prefix = f"seeds/{seed}/"
    expected_names = {
        f"{prefix}evidence.json",
        f"{prefix}labels.npy",
        f"{prefix}tgnn-probabilities.npy",
        f"{prefix}xgboost-probabilities.npy",
        f"{prefix}tgnn-artifact.pt",
        f"{prefix}xgboost-artifact.json",
    }
    if set(files) != expected_names:
        raise SensitivityVerificationError(f"seed {seed} declared files are incomplete")
    for relative, expected_hash in files.items():
        if not _is_sha256(expected_hash):
            raise SensitivityVerificationError(f"invalid declared hash for {relative}")
        path = _pack_path(root, relative)
        if not path.is_file():
            raise SensitivityVerificationError(f"declared file is missing: {relative}")
        if _sha256(path) != expected_hash:
            raise SensitivityVerificationError(f"hash mismatch for {relative}")

    seed_root = root / "seeds" / str(seed)
    evidence = _load_json(seed_root / "evidence.json", f"seed {seed} evidence")
    if evidence.get("seed") != seed:
        raise SensitivityVerificationError(f"seed {seed} evidence identity mismatch")
    if not _is_sha256(evidence.get("dataset_sha256")):
        raise SensitivityVerificationError(f"seed {seed} dataset hash is invalid")

    artifact_hashes = evidence.get("artifact_sha256")
    if not isinstance(artifact_hashes, dict) or set(artifact_hashes) != {
        "tgnn",
        "xgboost",
    }:
        raise SensitivityVerificationError(f"seed {seed} artifact hashes are invalid")
    artifact_paths = {
        "tgnn": seed_root / "tgnn-artifact.pt",
        "xgboost": seed_root / "xgboost-artifact.json",
    }
    for model, path in artifact_paths.items():
        if not _is_sha256(artifact_hashes[model]) or _sha256(path) != artifact_hashes[model]:
            raise SensitivityVerificationError(f"seed {seed} {model} artifact hash mismatch")

    labels = _load_array(seed_root / "labels.npy", f"seed {seed} labels")
    if labels.dtype.kind not in "biu" or not np.isin(labels, (0, 1)).all():
        raise SensitivityVerificationError(f"seed {seed} labels are invalid")
    if evidence.get("labels") != labels.astype(int).tolist():
        raise SensitivityVerificationError(f"seed {seed} labels do not match evidence")

    probabilities = evidence.get("probabilities")
    metrics = evidence.get("metrics")
    if not isinstance(probabilities, dict) or set(probabilities) != {"tgnn", "xgboost"}:
        raise SensitivityVerificationError(f"seed {seed} probabilities are invalid")
    if not isinstance(metrics, dict) or set(metrics) != {"tgnn", "xgboost"}:
        raise SensitivityVerificationError(f"seed {seed} metrics are invalid")
    for model in ("tgnn", "xgboost"):
        array = _load_array(
            seed_root / f"{model}-probabilities.npy",
            f"seed {seed} {model} probabilities",
        ).astype(np.float64)
        if array.size != labels.size or not np.isfinite(array).all() or not ((0 <= array) & (array <= 1)).all():
            raise SensitivityVerificationError(f"seed {seed} {model} probabilities are invalid")
        try:
            declared = np.asarray(probabilities[model], dtype=np.float64)
        except (TypeError, ValueError) as error:
            raise SensitivityVerificationError(
                f"seed {seed} {model} evidence probabilities are invalid"
            ) from error
        if not np.array_equal(declared, array):
            raise SensitivityVerificationError(
                f"seed {seed} {model} probabilities do not match evidence"
            )
        _finite_metric_payload(metrics[model], model)

    anchors = evidence.get("split_anchors")
    if not isinstance(anchors, dict) or set(anchors) != {"train", "validation", "test"}:
        raise SensitivityVerificationError(f"seed {seed} split anchors are invalid")
    for name, values in anchors.items():
        if not isinstance(values, list) or not values or any(
            isinstance(value, bool) or not isinstance(value, int) for value in values
        ):
            raise SensitivityVerificationError(f"seed {seed} {name} anchors are invalid")
    return evidence


def verify_sensitivity_pack(path: str | Path) -> dict[str, Any]:
    """Verify a self-contained exploratory sensitivity evidence pack.

    This module intentionally depends only on JSON, hashes, and NumPy so the
    verification command remains usable without model-training frameworks.
    """

    root = Path(path)
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise SensitivityVerificationError("sensitivity manifest is missing")
    manifest = _load_json(manifest_path, "sensitivity manifest")
    if manifest.get("format_version") != 1:
        raise SensitivityVerificationError("sensitivity format version is unsupported")
    if manifest.get("provenance") != "2026_EXPLORATORY_SENSITIVITY":
        raise SensitivityVerificationError("sensitivity provenance is invalid")
    threshold = manifest.get("reporting_threshold")
    if (
        isinstance(threshold, bool)
        or not isinstance(threshold, (int, float))
        or not 0 < float(threshold) < 1
    ):
        raise SensitivityVerificationError("reporting threshold is invalid")
    seeds = manifest.get("seeds")
    entries = manifest.get("seed_entries")
    if (
        not isinstance(seeds, list)
        or not seeds
        or any(isinstance(seed, bool) or not isinstance(seed, int) for seed in seeds)
        or len(set(seeds)) != len(seeds)
        or not isinstance(entries, list)
        or [entry.get("seed") if isinstance(entry, dict) else None for entry in entries]
        != seeds
    ):
        raise SensitivityVerificationError("seed manifest is invalid")
    evidence = [_verify_seed(root, entry) for entry in entries]
    return {
        "manifest": manifest,
        "manifest_sha256": _sha256(manifest_path),
        "provenance": manifest["provenance"],
        "seeds": evidence,
        "status": "verified",
    }
