from __future__ import annotations

import hashlib
import platform
from dataclasses import asdict, dataclass

import numpy as np

from research import __version__
from research.data.schema import EDGE_FEATURE_NAMES, STATE_FEATURE_NAMES, SyntheticDataset


@dataclass(frozen=True)
class DatasetManifest:
    dataset_name: str
    dataset_version: str
    schema_version: str
    generator_code_version: str
    seed: int
    parameters: dict[str, int]
    counts: dict[str, int]
    temporal_range: dict[str, int]
    state_features: tuple[str, ...]
    edge_features: tuple[str, ...]
    content_sha256: str
    generation_timestamp: str
    software_versions: dict[str, str]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def build_dataset_manifest(dataset: SyntheticDataset) -> DatasetManifest:
    return DatasetManifest(
        dataset_name="synthetic-scf-v1",
        dataset_version="1.0.0",
        schema_version="scf-data-v1",
        generator_code_version=__version__,
        seed=dataset.config.seed,
        parameters=dataset.config.identity_dict(),
        counts={
            "enterprises": dataset.config.enterprise_count,
            "months": dataset.config.months,
            "relationships": int(dataset.relationships.shape[0]),
            "edge_observations": int(
                dataset.edge_observations.shape[0]
                * dataset.edge_observations.shape[1]
            ),
            "severe_events": int(dataset.severe_events.sum()),
        },
        temporal_range={"first_month": 1, "last_month": dataset.config.months},
        state_features=STATE_FEATURE_NAMES,
        edge_features=EDGE_FEATURE_NAMES,
        content_sha256=hashlib.sha256(dataset.canonical_bytes()).hexdigest(),
        generation_timestamp="2026-08-15T00:00:00+00:00",
        software_versions={
            "python": platform.python_version(),
            "numpy": np.__version__,
        },
    )
