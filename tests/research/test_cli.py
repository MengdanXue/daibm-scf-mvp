import hashlib
import json
import shutil
import subprocess
import sys

import numpy as np
import pytest

from research.artifacts.verification import (
    ArtifactVerificationError,
    verify_reference_artifact,
)
from research.cli import _publish_reference_bundle, command_build_reference


def test_research_cli_exposes_reproducibility_commands():
    result = subprocess.run(
        [sys.executable, "-m", "research.cli", "--help"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "generate" in result.stdout
    assert "train-xgboost" in result.stdout
    assert "train-tgnn" in result.stdout
    assert "promote" in result.stdout
    assert "build-reference" in result.stdout
    assert "verify" in result.stdout
    assert "evaluate-multiseed" in result.stdout
    assert "verify-multiseed" in result.stdout


def test_verify_multiseed_cli_does_not_import_training_frameworks(tmp_path):
    pack = tmp_path / "pack"
    seed_dir = pack / "seeds" / "7"
    seed_dir.mkdir(parents=True)
    labels = np.asarray([0, 1], dtype=np.uint8)
    tgnn = np.asarray([0.2, 0.8], dtype=np.float32)
    xgboost = np.asarray([0.3, 0.7], dtype=np.float32)
    np.save(seed_dir / "labels.npy", labels, allow_pickle=False)
    np.save(seed_dir / "tgnn-probabilities.npy", tgnn, allow_pickle=False)
    np.save(seed_dir / "xgboost-probabilities.npy", xgboost, allow_pickle=False)
    tgnn_artifact = seed_dir / "tgnn-artifact.pt"
    xgboost_artifact = seed_dir / "xgboost-artifact.json"
    tgnn_artifact.write_bytes(b"standalone-tgnn-artifact")
    xgboost_artifact.write_text("{}\n", encoding="utf-8")

    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    perfect_metrics = {
        "roc_auc": 1.0,
        "pr_auc": 1.0,
        "f1": 1.0,
        "precision": 1.0,
        "recall": 1.0,
        "confusion_matrix": [[1, 0], [0, 1]],
        "brier_score": 0.04,
    }
    evidence = {
        "artifact_sha256": {
            "tgnn": digest(tgnn_artifact),
            "xgboost": digest(xgboost_artifact),
        },
        "dataset_sha256": "c" * 64,
        "labels": [0, 1],
        "metrics": {
            "tgnn": perfect_metrics,
            "xgboost": {**perfect_metrics, "brier_score": 0.09},
        },
        "probabilities": {
            "tgnn": [float(value) for value in tgnn],
            "xgboost": [float(value) for value in xgboost],
        },
        "seed": 7,
        "split_anchors": {"train": [12], "validation": [17], "test": [19]},
    }
    evidence_path = seed_dir / "evidence.json"
    evidence_path.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    files = {
        str(path.relative_to(pack)).replace("\\", "/"): digest(path)
        for path in (
            evidence_path,
            seed_dir / "labels.npy",
            seed_dir / "tgnn-probabilities.npy",
            seed_dir / "xgboost-probabilities.npy",
            tgnn_artifact,
            xgboost_artifact,
        )
    }
    (pack / "manifest.json").write_text(
        json.dumps(
            {
                "format_version": 1,
                "provenance": "2026_EXPLORATORY_SENSITIVITY",
                "reporting_threshold": 0.5,
                "seed_entries": [{"seed": 7, "files": files}],
                "seeds": [7],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    script = """
import builtins
import sys

real_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name == 'torch' or name.startswith('torch.') or name == 'xgboost' or name.startswith('xgboost.'):
        raise AssertionError(f'heavy training import attempted: {name}')
    return real_import(name, *args, **kwargs)
builtins.__import__ = guarded_import

from research.cli import main
raise SystemExit(main(['verify-multiseed', '--path', sys.argv[1]]))
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(pack)],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "verified"


def test_build_reference_creates_a_self_verifying_comparison_bundle(tmp_path):
    destination = tmp_path / "reference"
    destination.mkdir()
    (destination / "xgboost-manifest.json").write_text(
        "{\"stale\": true}\n",
        encoding="utf-8",
    )
    (destination / "xgboost-v0.4.json").write_text(
        "{\"stale\": true}\n",
        encoding="utf-8",
    )

    manifest = command_build_reference(
        output=tmp_path / "run",
        destination=destination,
        max_epochs=1,
        patience=1,
    )

    verified = verify_reference_artifact(destination)
    xgboost_manifest = destination / "xgboost-manifest.json"
    assert manifest["xgboost_manifest_sha256"] == hashlib.sha256(
        xgboost_manifest.read_bytes()
    ).hexdigest()
    assert set(verified.comparison_metrics) == {"tgnn", "xgboost"}
    assert b"\r\n" not in (
        destination / "model-manifest.json"
    ).read_bytes()


def test_reference_publish_failure_restores_previous_verified_bundle(tmp_path):
    destination = tmp_path / "reference"
    shutil.copytree("artifacts/reference", destination)
    previous_manifest = (destination / "model-manifest.json").read_bytes()
    verify_reference_artifact(destination)

    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "model-manifest.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(ArtifactVerificationError):
        _publish_reference_bundle(staging, destination)

    verify_reference_artifact(destination)
    assert (destination / "model-manifest.json").read_bytes() == previous_manifest
    assert not staging.with_name(f"{staging.name}.previous").exists()
    assert not staging.with_name(f"{staging.name}.failed").exists()
