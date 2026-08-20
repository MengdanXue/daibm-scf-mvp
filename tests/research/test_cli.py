import hashlib
import shutil
import subprocess
import sys

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
