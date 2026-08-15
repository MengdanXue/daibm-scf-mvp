from __future__ import annotations

import json
import struct
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray


STATE_FEATURE_NAMES = (
    "credit_history_score",
    "liquidity_ratio",
    "leverage_ratio",
    "cash_flow_index",
    "operational_stability",
    "overdue_ratio",
    "average_delay_normalized",
)
EDGE_FEATURE_NAMES = (
    "monthly_transaction_amount",
    "transaction_count",
    "overdue_ratio",
    "average_payment_delay_days",
)
INDUSTRIES = ("manufacturing", "wholesale_retail", "services", "agriculture")
SIZE_CLASSES = ("large", "medium", "small", "micro")


@dataclass(frozen=True)
class GeneratorConfig:
    enterprise_count: int = 500
    months: int = 24
    seed: int = 20260815
    relationships_per_enterprise: int = 3

    def __post_init__(self) -> None:
        if self.enterprise_count < 2:
            raise ValueError("enterprise_count must be at least 2")
        if self.months < 15:
            raise ValueError("months must cover a 12-month window and 3-month horizon")
        if self.relationships_per_enterprise < 1:
            raise ValueError("relationships_per_enterprise must be positive")
        max_edges = self.enterprise_count * (self.enterprise_count - 1)
        if self.edge_count > max_edges:
            raise ValueError("requested relationships exceed directed graph capacity")

    @property
    def edge_count(self) -> int:
        return self.enterprise_count * self.relationships_per_enterprise

    @classmethod
    def reference(cls) -> "GeneratorConfig":
        return cls()

    def identity_dict(self) -> dict[str, int]:
        return {
            "enterprise_count": self.enterprise_count,
            "months": self.months,
            "seed": self.seed,
            "relationships_per_enterprise": self.relationships_per_enterprise,
        }


@dataclass(frozen=True)
class SyntheticDataset:
    config: GeneratorConfig
    enterprise_ids: tuple[str, ...]
    industry_codes: NDArray[np.int8]
    size_codes: NDArray[np.int8]
    states: NDArray[np.float32]
    relationships: NDArray[np.int32]
    relationship_active_months: NDArray[np.int16]
    edge_observations: NDArray[np.float32]
    severe_events: NDArray[np.uint8]

    def __post_init__(self) -> None:
        for array in (
            self.industry_codes,
            self.size_codes,
            self.states,
            self.relationships,
            self.relationship_active_months,
            self.edge_observations,
            self.severe_events,
        ):
            array.setflags(write=False)

    def canonical_bytes(self) -> bytes:
        header = {
            "dataset_name": "synthetic-scf-v1",
            "schema_version": "scf-data-v1",
            "config": self.config.identity_dict(),
            "enterprise_ids": self.enterprise_ids,
            "state_features": STATE_FEATURE_NAMES,
            "edge_features": EDGE_FEATURE_NAMES,
            "industries": INDUSTRIES,
            "size_classes": SIZE_CLASSES,
        }
        chunks = [
            json.dumps(
                header,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ]
        arrays = (
            ("industry_codes", self.industry_codes),
            ("size_codes", self.size_codes),
            ("states", self.states),
            ("relationships", self.relationships),
            ("relationship_active_months", self.relationship_active_months),
            ("edge_observations", self.edge_observations),
            ("severe_events", self.severe_events),
        )
        for name, array in arrays:
            little_endian = np.ascontiguousarray(
                array.astype(array.dtype.newbyteorder("<"), copy=False)
            )
            descriptor = json.dumps(
                {
                    "name": name,
                    "dtype": little_endian.dtype.str,
                    "shape": little_endian.shape,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("ascii")
            data = little_endian.tobytes(order="C")
            chunks.extend(
                (
                    struct.pack("<Q", len(descriptor)),
                    descriptor,
                    struct.pack("<Q", len(data)),
                    data,
                )
            )
        return b"".join(chunks)
