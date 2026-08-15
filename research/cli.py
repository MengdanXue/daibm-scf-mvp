from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import numpy as np
import torch

from research.artifacts.registry import promote_tgnn
from research.artifacts.verification import verify_reference_artifact
from research.data.generator import generate_dataset
from research.data.manifest import build_dataset_manifest
from research.graph.builder import TemporalSamples, build_samples
from research.graph.features import GRAPH_FEATURE_NAMES
from research.graph.split import TemporalSplit, temporal_split
from research.models.tgnn import TemporalGCNBiLSTM
from research.training.export_onnx import export_onnx
from research.training.train_tgnn import (
    TGNNTrainingConfig,
    TrainedTGNN,
    train_tgnn,
)
from research.training.train_xgboost import train_xgboost


def _reference_pipeline() -> tuple[dict[str, Any], TemporalSplit]:
    dataset = generate_dataset()
    manifest = build_dataset_manifest(dataset).to_dict()
    return manifest, temporal_split(build_samples(dataset))


def _feature_schema() -> dict[str, Any]:
    return {
        "version": "graph-features-v1",
        "names": list(GRAPH_FEATURE_NAMES),
        "sequence_length": 12,
        "node_count": 500,
        "direction": "supplier_to_customer",
        "gcn_adjacency": "symmetric_normalized_with_self_loops",
    }


def _last_test_sample(samples: TemporalSamples) -> TemporalSamples:
    return samples.subset(np.asarray([len(samples.anchors) - 1], dtype=np.int64))


def _load_trained(run_dir: Path) -> TrainedTGNN:
    run_manifest_path = run_dir / "tgnn-run.json"
    manifest = json.loads(run_manifest_path.read_text(encoding="utf-8"))
    checkpoint_path = run_dir / manifest["checkpoint"]
    actual_hash = hashlib.sha256(checkpoint_path.read_bytes()).hexdigest()
    if actual_hash != manifest["checkpoint_sha256"]:
        raise RuntimeError("TGNN checkpoint hash mismatch")
    config = TGNNTrainingConfig(**manifest["configuration"])
    checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=True)
    model = TemporalGCNBiLSTM(
        int(manifest["feature_count"]),
        spatial_dimension=config.spatial_dimension,
        temporal_hidden=config.temporal_hidden,
        dropout=config.dropout,
    )
    model.load_state_dict(checkpoint["model_state_dict"])
    model.eval()
    return TrainedTGNN(
        model=model,
        configuration=config,
        history=tuple(manifest["history"]),
        test_probabilities=np.empty(0, dtype=np.float32),
        metrics=manifest["metrics"],
        checkpoint_path=checkpoint_path,
        checkpoint_sha256=actual_hash,
        run_manifest_path=run_manifest_path,
    )


def command_generate(output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    dataset = generate_dataset()
    manifest = build_dataset_manifest(dataset).to_dict()
    (output / "dataset-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    np.savez_compressed(
        output / "synthetic-scf-v1.npz",
        industry_codes=dataset.industry_codes,
        size_codes=dataset.size_codes,
        states=dataset.states,
        relationships=dataset.relationships,
        relationship_active_months=dataset.relationship_active_months,
        edge_observations=dataset.edge_observations,
        severe_events=dataset.severe_events,
    )
    return manifest


def command_train_xgboost(output: Path) -> dict[str, Any]:
    _, split = _reference_pipeline()
    result = train_xgboost(split, output_dir=output)
    return {"artifact_sha256": result.artifact_sha256, "metrics": result.metrics}


def command_train_tgnn(
    output: Path,
    max_epochs: int,
    patience: int,
) -> dict[str, Any]:
    _, split = _reference_pipeline()
    result = train_tgnn(
        split,
        config=TGNNTrainingConfig(
            max_epochs=max_epochs,
            early_stopping_patience=patience,
        ),
        output_dir=output,
    )
    return {
        "checkpoint_sha256": result.checkpoint_sha256,
        "epochs": len(result.history),
        "metrics": result.metrics,
    }


def command_promote(run_dir: Path, destination: Path) -> dict[str, Any]:
    dataset_manifest, split = _reference_pipeline()
    trained = _load_trained(run_dir)
    reference_sample = _last_test_sample(split.test)
    onnx_path = export_onnx(
        trained.model,
        reference_sample.x,
        reference_sample.adjacency,
        run_dir / "tgnn-v0.4.onnx",
    )
    return promote_tgnn(
        trained=trained,
        onnx_path=onnx_path,
        reference_samples=reference_sample,
        dataset_manifest=dataset_manifest,
        feature_schema=_feature_schema(),
        destination=destination,
    )


def command_build_reference(
    output: Path,
    destination: Path,
    max_epochs: int,
    patience: int,
) -> dict[str, Any]:
    dataset_manifest, split = _reference_pipeline()
    xgboost_result = train_xgboost(split, output_dir=output / "xgboost")
    trained = train_tgnn(
        split,
        config=TGNNTrainingConfig(
            max_epochs=max_epochs,
            early_stopping_patience=patience,
        ),
        output_dir=output / "tgnn",
    )
    reference_sample = _last_test_sample(split.test)
    onnx_path = export_onnx(
        trained.model,
        reference_sample.x,
        reference_sample.adjacency,
        output / "tgnn" / "tgnn-v0.4.onnx",
    )
    model_manifest = promote_tgnn(
        trained=trained,
        onnx_path=onnx_path,
        reference_samples=reference_sample,
        dataset_manifest=dataset_manifest,
        feature_schema=_feature_schema(),
        destination=destination,
    )
    shutil.copy2(
        xgboost_result.artifact_path,
        destination / "xgboost-v0.4.json",
    )
    shutil.copy2(
        xgboost_result.manifest_path,
        destination / "xgboost-manifest.json",
    )
    (destination / "metrics.json").write_text(
        json.dumps(
            {"tgnn": trained.metrics, "xgboost": xgboost_result.metrics},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    verify_reference_artifact(destination)
    return model_manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m research.cli",
        description="Reproducible DAIBM-SCF Research Core pipeline",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    generate = commands.add_parser("generate", help="generate synthetic data")
    generate.add_argument("--output", type=Path, default=Path("output/research/data"))

    xgboost = commands.add_parser("train-xgboost", help="fit XGBoost")
    xgboost.add_argument("--output", type=Path, default=Path("output/research/xgboost"))

    tgnn = commands.add_parser("train-tgnn", help="fit the minimal TGNN")
    tgnn.add_argument("--output", type=Path, default=Path("output/research/tgnn"))
    tgnn.add_argument("--max-epochs", type=int, default=100)
    tgnn.add_argument("--patience", type=int, default=10)

    promote = commands.add_parser("promote", help="promote a TGNN run")
    promote.add_argument("--run-dir", type=Path, required=True)
    promote.add_argument("--destination", type=Path, default=Path("artifacts/reference"))

    reference = commands.add_parser(
        "build-reference", help="train and promote all reference artifacts"
    )
    reference.add_argument("--output", type=Path, default=Path("output/research/reference-run"))
    reference.add_argument("--destination", type=Path, default=Path("artifacts/reference"))
    reference.add_argument("--max-epochs", type=int, default=100)
    reference.add_argument("--patience", type=int, default=10)

    verify = commands.add_parser("verify", help="verify promoted artifacts")
    verify.add_argument("--reference", type=Path, default=Path("artifacts/reference"))
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if arguments.command == "generate":
        result = command_generate(arguments.output)
    elif arguments.command == "train-xgboost":
        result = command_train_xgboost(arguments.output)
    elif arguments.command == "train-tgnn":
        result = command_train_tgnn(
            arguments.output, arguments.max_epochs, arguments.patience
        )
    elif arguments.command == "promote":
        result = command_promote(arguments.run_dir, arguments.destination)
    elif arguments.command == "build-reference":
        result = command_build_reference(
            arguments.output,
            arguments.destination,
            arguments.max_epochs,
            arguments.patience,
        )
    else:
        verified = verify_reference_artifact(arguments.reference)
        result = {
            "artifact_sha256": verified.manifest["artifact_sha256"],
            "status": "verified",
        }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
