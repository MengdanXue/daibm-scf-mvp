from __future__ import annotations

import hashlib
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from research.artifacts.json_io import write_canonical_json
from research.artifacts.sensitivity_verification import verify_sensitivity_pack
from research.data.generator import generate_dataset
from research.data.manifest import build_dataset_manifest
from research.data.schema import GeneratorConfig
from research.experiments.sensitivity import SeedEvidence, summarize_runs
from research.graph.builder import build_samples
from research.graph.split import temporal_split
from research.models.xgboost_model import XGBoostConfig
from research.training.metrics import evaluate_binary_predictions
from research.training.train_tgnn import TGNNTrainingConfig, train_tgnn
from research.training.train_xgboost import train_xgboost


_PROVENANCE = "2026_EXPLORATORY_SENSITIVITY"


@dataclass(frozen=True)
class SensitivityConfig:
    seeds: tuple[int, ...] = (
        20260815,
        20260816,
        20260817,
        20260818,
        20260819,
    )
    max_epochs: int = 100
    patience: int = 10
    threshold: float = 0.50


def _validate_config(config: SensitivityConfig) -> None:
    if not config.seeds:
        raise ValueError("at least one seed is required")
    if any(isinstance(seed, bool) or not isinstance(seed, int) for seed in config.seeds):
        raise TypeError("seeds must contain integers")
    if len(set(config.seeds)) != len(config.seeds):
        raise ValueError("seeds must be unique")
    if config.max_epochs < 1:
        raise ValueError("max_epochs must be positive")
    if config.patience < 1:
        raise ValueError("patience must be positive")
    if not np.isfinite(config.threshold) or config.threshold != 0.50:
        raise ValueError("threshold must remain the fixed reporting threshold 0.50")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _validated_probabilities(
    values: object,
    *,
    model: str,
    expected_size: int,
) -> np.ndarray:
    probabilities = np.asarray(values, dtype=np.float32).reshape(-1)
    if probabilities.size != expected_size:
        raise RuntimeError(
            f"{model} returned {probabilities.size} probabilities for {expected_size} labels"
        )
    if not np.isfinite(probabilities).all() or not (
        (0 <= probabilities) & (probabilities <= 1)
    ).all():
        raise RuntimeError(f"{model} returned invalid probabilities")
    return probabilities


def _copy_verified_artifact(
    source: object,
    declared_hash: object,
    destination: Path,
    *,
    model: str,
) -> str:
    path = Path(source)
    if not path.is_file():
        raise RuntimeError(f"{model} artifact is missing: {path}")
    actual = _sha256(path)
    if actual != declared_hash:
        raise RuntimeError(f"{model} artifact hash mismatch")
    shutil.copy2(path, destination)
    if _sha256(destination) != actual:
        raise RuntimeError(f"{model} artifact copy hash mismatch")
    return actual


def _file_manifest(root: Path, files: tuple[Path, ...]) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): _sha256(path)
        for path in sorted(files, key=lambda value: value.as_posix())
    }


def _publish_sensitivity_bundle(staging: Path, destination: Path) -> None:
    backup = staging.with_name(f"{staging.name}.previous")
    failed = staging.with_name(f"{staging.name}.failed")
    if destination.exists():
        destination.replace(backup)
    try:
        staging.replace(destination)
        verify_sensitivity_pack(destination)
    except BaseException:
        if destination.exists():
            destination.replace(failed)
        if backup.exists():
            backup.replace(destination)
        if failed.exists():
            shutil.rmtree(failed)
        raise
    if backup.exists():
        shutil.rmtree(backup)


def _run_seed(
    seed: int,
    config: SensitivityConfig,
    output: Path,
    staging: Path,
) -> tuple[SeedEvidence, dict[str, Any]]:
    dataset = generate_dataset(GeneratorConfig(seed=seed))
    dataset_manifest = build_dataset_manifest(dataset).to_dict()
    split = temporal_split(build_samples(dataset))
    labels = np.asarray(split.test.y, dtype=np.uint8).reshape(-1)
    if labels.size == 0 or not np.isin(labels, (0, 1)).all():
        raise RuntimeError(f"seed {seed} produced invalid test labels")

    seed_output = output / f"seed-{seed}"
    tgnn_result = train_tgnn(
        split,
        config=TGNNTrainingConfig(
            max_epochs=config.max_epochs,
            early_stopping_patience=config.patience,
            seed=seed,
        ),
        output_dir=seed_output / "tgnn",
    )
    xgboost_result = train_xgboost(
        split,
        config=XGBoostConfig(random_state=seed),
        output_dir=seed_output / "xgboost",
    )
    probabilities = {
        "tgnn": _validated_probabilities(
            tgnn_result.test_probabilities,
            model="tgnn",
            expected_size=labels.size,
        ),
        "xgboost": _validated_probabilities(
            xgboost_result.test_probabilities,
            model="xgboost",
            expected_size=labels.size,
        ),
    }
    metrics = {
        model: evaluate_binary_predictions(labels, values, threshold=config.threshold)
        for model, values in probabilities.items()
    }

    seed_staging = staging / "seeds" / str(seed)
    seed_staging.mkdir(parents=True)
    artifact_hashes = {
        "tgnn": _copy_verified_artifact(
            tgnn_result.checkpoint_path,
            tgnn_result.checkpoint_sha256,
            seed_staging / "tgnn-artifact.pt",
            model="tgnn",
        ),
        "xgboost": _copy_verified_artifact(
            xgboost_result.artifact_path,
            xgboost_result.artifact_sha256,
            seed_staging / "xgboost-artifact.json",
            model="xgboost",
        ),
    }
    np.save(seed_staging / "labels.npy", labels, allow_pickle=False)
    for model, values in probabilities.items():
        np.save(
            seed_staging / f"{model}-probabilities.npy",
            values,
            allow_pickle=False,
        )

    evidence = SeedEvidence(
        seed=seed,
        dataset_sha256=str(dataset_manifest["content_sha256"]),
        labels=tuple(int(value) for value in labels),
        probabilities={
            model: tuple(float(value) for value in values)
            for model, values in probabilities.items()
        },
        metrics=metrics,
        artifact_sha256=artifact_hashes,
    )
    payload = evidence.to_payload()
    payload["split_anchors"] = {
        "train": split.train.anchors.astype(int).tolist(),
        "validation": split.validation.anchors.astype(int).tolist(),
        "test": split.test.anchors.astype(int).tolist(),
    }
    evidence_path = write_canonical_json(seed_staging / "evidence.json", payload)
    declared_files = (
        evidence_path,
        seed_staging / "labels.npy",
        seed_staging / "tgnn-probabilities.npy",
        seed_staging / "xgboost-probabilities.npy",
        seed_staging / "tgnn-artifact.pt",
        seed_staging / "xgboost-artifact.json",
    )
    return evidence, {"seed": seed, "files": _file_manifest(staging, declared_files)}


def run_sensitivity(
    config: SensitivityConfig,
    output: str | Path,
    destination: str | Path,
) -> dict[str, Any]:
    """Train each configured seed and atomically publish a verified evidence pack.

    A seed failure leaves the currently published destination untouched. The
    run directory remains available for diagnosis.
    """

    _validate_config(config)
    run_output = Path(output)
    pack_destination = Path(destination)
    run_output.mkdir(parents=True, exist_ok=True)
    pack_destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{pack_destination.name}-",
        dir=pack_destination.parent,
        ignore_cleanup_errors=True,
    ) as temporary_directory:
        staging = Path(temporary_directory)
        evidence: list[SeedEvidence] = []
        entries: list[dict[str, Any]] = []
        for seed in config.seeds:
            try:
                seed_evidence, entry = _run_seed(seed, config, run_output, staging)
            except Exception as error:
                raise RuntimeError(
                    f"sensitivity run failed for seed {seed}: {error}"
                ) from error
            evidence.append(seed_evidence)
            entries.append(entry)

        manifest = {
            "configuration": {
                "max_epochs": config.max_epochs,
                "patience": config.patience,
            },
            "format_version": 1,
            "provenance": _PROVENANCE,
            "reporting_threshold": config.threshold,
            "seed_entries": entries,
            "seeds": list(config.seeds),
            "summary": summarize_runs(evidence) if len(evidence) >= 2 else {},
        }
        write_canonical_json(staging / "manifest.json", manifest)
        verify_sensitivity_pack(staging)
        _publish_sensitivity_bundle(staging, pack_destination)
    return verify_sensitivity_pack(pack_destination)["manifest"]
