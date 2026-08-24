from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from research.artifacts.sensitivity_verification import (
    SensitivityVerificationError,
    verify_sensitivity_pack,
)
from research.experiments.runner import (
    SensitivityConfig,
    _publish_sensitivity_bundle,
    run_sensitivity,
)


def _metrics(labels: np.ndarray, probabilities: np.ndarray) -> dict[str, object]:
    predicted = probabilities >= 0.5
    true_positive = int(np.sum(predicted & (labels == 1)))
    false_positive = int(np.sum(predicted & (labels == 0)))
    false_negative = int(np.sum(~predicted & (labels == 1)))
    true_negative = int(np.sum(~predicted & (labels == 0)))
    return {
        "roc_auc": 0.75,
        "pr_auc": 0.70,
        "f1": 0.67,
        "precision": 0.75,
        "recall": 0.60,
        "confusion_matrix": [
            [true_negative, false_positive],
            [false_negative, true_positive],
        ],
        "brier_score": float(np.mean((probabilities - labels) ** 2)),
    }


def _install_fake_pipeline(monkeypatch, observed: dict[str, list[int]], *, fail_seed=None):
    import research.experiments.runner as runner

    labels = np.asarray([0, 1, 1, 0], dtype=np.uint8)

    def fake_generate(config):
        observed["data"].append(config.seed)
        return SimpleNamespace(config=config)

    def fake_manifest(dataset):
        return SimpleNamespace(
            to_dict=lambda: {
                "seed": dataset.config.seed,
                "content_sha256": hashlib.sha256(
                    f"dataset-{dataset.config.seed}".encode("ascii")
                ).hexdigest(),
            }
        )

    def fake_split(dataset):
        partition = lambda anchors, y=None: SimpleNamespace(
            anchors=np.asarray(anchors, dtype=np.int16),
            y=labels.reshape(1, -1) if y is None else y,
        )
        return SimpleNamespace(
            train=partition([12, 13]),
            validation=partition([17]),
            test=partition([19], labels.reshape(1, -1)),
        )

    def fake_tgnn(split, *, config, output_dir):
        observed["tgnn"].append(config.seed)
        if config.seed == fail_seed:
            raise RuntimeError(f"training failed for seed {config.seed}")
        target = Path(output_dir) / "tgnn-v0.4.pt"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(f"tgnn-{config.seed}".encode("ascii"))
        probabilities = np.asarray([0.1, 0.8, 0.6, 0.3], dtype=np.float32)
        return SimpleNamespace(
            test_probabilities=probabilities,
            metrics=_metrics(labels, probabilities),
            checkpoint_path=target,
            checkpoint_sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
        )

    def fake_xgboost(split, *, config, output_dir):
        observed["xgboost"].append(config.random_state)
        target = Path(output_dir) / "xgboost-v0.4.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps({"seed": config.random_state}), encoding="utf-8"
        )
        probabilities = np.asarray([0.2, 0.7, 0.9, 0.4], dtype=np.float32)
        return SimpleNamespace(
            test_probabilities=probabilities,
            metrics=_metrics(labels, probabilities),
            artifact_path=target,
            artifact_sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
        )

    monkeypatch.setattr(runner, "generate_dataset", fake_generate)
    monkeypatch.setattr(runner, "build_dataset_manifest", fake_manifest)
    monkeypatch.setattr(runner, "build_samples", lambda dataset: dataset)
    monkeypatch.setattr(runner, "temporal_split", fake_split)
    monkeypatch.setattr(runner, "train_tgnn", fake_tgnn)
    monkeypatch.setattr(runner, "train_xgboost", fake_xgboost)


def _run_fake_pack(tmp_path, monkeypatch, *, seeds=(11, 12), fail_seed=None):
    observed = {"data": [], "tgnn": [], "xgboost": []}
    _install_fake_pipeline(monkeypatch, observed, fail_seed=fail_seed)
    manifest = run_sensitivity(
        SensitivityConfig(seeds=seeds, max_epochs=1, patience=1),
        tmp_path / "runs",
        tmp_path / "pack",
    )
    return manifest, observed


def test_runner_propagates_seed_to_data_and_both_models(tmp_path, monkeypatch):
    manifest, observed = _run_fake_pack(tmp_path, monkeypatch)

    assert observed == {
        "data": [11, 12],
        "tgnn": [11, 12],
        "xgboost": [11, 12],
    }
    assert manifest["provenance"] == "2026_EXPLORATORY_SENSITIVITY"
    assert manifest["reporting_threshold"] == 0.5


def test_runner_persists_labels_probabilities_metrics_anchors_and_hashes(
    tmp_path, monkeypatch
):
    _run_fake_pack(tmp_path, monkeypatch)

    verified = verify_sensitivity_pack(tmp_path / "pack")
    evidence = verified["seeds"][0]
    assert evidence["seed"] == 11
    assert evidence["labels"] == [0, 1, 1, 0]
    assert evidence["probabilities"]["tgnn"] == pytest.approx([0.1, 0.8, 0.6, 0.3])
    assert set(evidence["metrics"]) == {"tgnn", "xgboost"}
    assert evidence["split_anchors"] == {
        "test": [19],
        "train": [12, 13],
        "validation": [17],
    }
    assert all(len(value) == 64 for value in evidence["artifact_sha256"].values())
    assert (tmp_path / "pack" / "seeds" / "11" / "labels.npy").is_file()
    assert (tmp_path / "pack" / "seeds" / "11" / "tgnn-probabilities.npy").is_file()
    assert (tmp_path / "pack" / "seeds" / "11" / "xgboost-probabilities.npy").is_file()


def test_verifier_rejects_a_tampered_declared_file(tmp_path, monkeypatch):
    _run_fake_pack(tmp_path, monkeypatch)
    labels_path = tmp_path / "pack" / "seeds" / "11" / "labels.npy"
    labels_path.write_bytes(labels_path.read_bytes() + b"tampered")

    with pytest.raises(SensitivityVerificationError, match="hash mismatch"):
        verify_sensitivity_pack(tmp_path / "pack")


def _refresh_declared_hash(pack: Path, path: Path) -> None:
    manifest_path = pack / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    relative = path.relative_to(pack).as_posix()
    for entry in manifest["seed_entries"]:
        if relative in entry["files"]:
            entry["files"][relative] = hashlib.sha256(path.read_bytes()).hexdigest()
            break
    else:
        raise AssertionError(f"undeclared test fixture path: {relative}")
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def test_verifier_recomputes_metrics_instead_of_trusting_evidence(
    tmp_path, monkeypatch
):
    _run_fake_pack(tmp_path, monkeypatch)
    pack = tmp_path / "pack"
    evidence_path = pack / "seeds" / "11" / "evidence.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["metrics"]["tgnn"]["brier_score"] = 0.999
    evidence_path.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    _refresh_declared_hash(pack, evidence_path)

    with pytest.raises(SensitivityVerificationError, match="metrics do not match"):
        verify_sensitivity_pack(pack)


def test_verifier_recomputes_summary_instead_of_trusting_manifest(
    tmp_path, monkeypatch
):
    _run_fake_pack(tmp_path, monkeypatch)
    manifest_path = tmp_path / "pack" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["summary"]["tgnn"]["roc_auc"]["mean"] = 0.999
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    with pytest.raises(SensitivityVerificationError, match="summary does not match"):
        verify_sensitivity_pack(tmp_path / "pack")


def test_verifier_rejects_non_reporting_threshold(tmp_path, monkeypatch):
    _run_fake_pack(tmp_path, monkeypatch)
    manifest_path = tmp_path / "pack" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["reporting_threshold"] = 0.40
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    with pytest.raises(SensitivityVerificationError, match="0.50"):
        verify_sensitivity_pack(tmp_path / "pack")


def test_failed_run_does_not_replace_previous_verified_pack(tmp_path, monkeypatch):
    _run_fake_pack(tmp_path, monkeypatch, seeds=(11, 12))
    previous = verify_sensitivity_pack(tmp_path / "pack")["manifest_sha256"]

    observed = {"data": [], "tgnn": [], "xgboost": []}
    _install_fake_pipeline(monkeypatch, observed, fail_seed=13)
    with pytest.raises(RuntimeError, match="training failed for seed 13"):
        run_sensitivity(
            SensitivityConfig(seeds=(13,), max_epochs=1, patience=1),
            tmp_path / "failed-runs",
            tmp_path / "pack",
        )

    assert verify_sensitivity_pack(tmp_path / "pack")["manifest_sha256"] == previous


@pytest.mark.parametrize("relationship", ["equal", "output_contains", "destination_contains"])
def test_runner_rejects_overlapping_output_and_destination_before_writing(
    tmp_path, monkeypatch, relationship
):
    _run_fake_pack(tmp_path, monkeypatch)
    pack = tmp_path / "pack"
    previous = verify_sensitivity_pack(pack)["manifest_sha256"]
    if relationship == "equal":
        output, destination = pack, pack
    elif relationship == "output_contains":
        output, destination = tmp_path, pack
    else:
        output, destination = pack / "new-runs", pack

    with pytest.raises(ValueError, match="must not overlap"):
        run_sensitivity(
            SensitivityConfig(seeds=(13,), max_epochs=1, patience=1),
            output,
            destination,
        )

    assert verify_sensitivity_pack(pack)["manifest_sha256"] == previous
    assert not (pack / "new-runs").exists()


def test_publish_failure_after_replacement_restores_previous_pack(
    tmp_path, monkeypatch
):
    _run_fake_pack(tmp_path, monkeypatch)
    destination = tmp_path / "pack"
    previous = verify_sensitivity_pack(destination)["manifest_sha256"]
    staging = tmp_path / "publish-staging"
    shutil.copytree(destination, staging)

    def fail_final_verification(path):
        assert Path(path) == destination
        raise RuntimeError("injected final verification failure")

    monkeypatch.setattr(
        "research.experiments.runner.verify_sensitivity_pack",
        fail_final_verification,
    )
    with pytest.raises(RuntimeError, match="injected final verification failure"):
        _publish_sensitivity_bundle(staging, destination)

    assert verify_sensitivity_pack(destination)["manifest_sha256"] == previous
    assert not staging.with_name(f"{staging.name}.previous").exists()
    assert not staging.with_name(f"{staging.name}.failed").exists()


@pytest.mark.parametrize(
    "config, message",
    [
        (SensitivityConfig(seeds=()), "at least one seed"),
        (SensitivityConfig(seeds=(1, 1)), "unique"),
        (SensitivityConfig(max_epochs=0), "max_epochs"),
        (SensitivityConfig(patience=0), "patience"),
        (SensitivityConfig(threshold=1.1), "threshold"),
    ],
)
def test_runner_rejects_invalid_configuration_before_training(
    tmp_path, config, message
):
    with pytest.raises(ValueError, match=message):
        run_sensitivity(config, tmp_path / "runs", tmp_path / "pack")
