from __future__ import annotations

from pathlib import Path
import logging
import warnings

import numpy as np
import onnxruntime as ort
import torch

from research.models.tgnn import TemporalGCNBiLSTM


def export_onnx(
    model: TemporalGCNBiLSTM,
    node_features: np.ndarray,
    adjacency: np.ndarray,
    destination: str | Path,
) -> Path:
    output = Path(destination)
    output.parent.mkdir(parents=True, exist_ok=True)
    model.eval()
    registration_logger = logging.getLogger(
        "torch.onnx._internal.exporter._registration"
    )
    previous_level = registration_logger.level
    registration_logger.setLevel(logging.ERROR)
    try:
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="The tensor attributes .* were assigned during export.*",
                category=UserWarning,
            )
            warnings.filterwarnings(
                "ignore",
                message=r"`isinstance\(treespec, LeafSpec\)` is deprecated.*",
                category=FutureWarning,
            )
            torch.onnx.export(
                model,
                (
                    torch.from_numpy(
                        np.asarray(node_features, dtype=np.float32)
                    ),
                    torch.from_numpy(np.asarray(adjacency, dtype=np.float32)),
                ),
                output,
                input_names=("node_features", "adjacency"),
                output_names=("logits",),
                opset_version=18,
                dynamo=True,
                external_data=False,
                verbose=False,
            )
    finally:
        registration_logger.setLevel(previous_level)
    return output


def verify_onnx_parity(
    model: TemporalGCNBiLSTM,
    onnx_path: str | Path,
    node_features: np.ndarray,
    adjacency: np.ndarray,
) -> float:
    x = np.asarray(node_features, dtype=np.float32)
    graph = np.asarray(adjacency, dtype=np.float32)
    model.eval()
    with torch.no_grad():
        expected = model(torch.from_numpy(x), torch.from_numpy(graph)).numpy()
    session = ort.InferenceSession(
        str(onnx_path), providers=("CPUExecutionProvider",)
    )
    actual = session.run(
        ("logits",),
        {"node_features": x, "adjacency": graph},
    )[0]
    return float(np.max(np.abs(expected - actual)))
