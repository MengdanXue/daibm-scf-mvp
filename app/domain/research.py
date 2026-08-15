from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID


def _require_probability(value: float, name: str) -> None:
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be between 0 and 1")


@dataclass(frozen=True)
class DatasetVersion:
    dataset_version_id: UUID
    name: str
    version: str
    generation_seed: int
    schema_version: str
    content_sha256: str
    manifest: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True)
class SyntheticScenario:
    synthetic_scenario_id: UUID
    dataset_version_id: UUID
    name: str
    revision: int
    overlay_sha256: str
    overlay: dict[str, Any]
    status: str
    created_at: datetime


@dataclass(frozen=True)
class GraphSnapshot:
    graph_snapshot_id: UUID
    dataset_version_id: UUID
    anchor_month: int
    window_start_month: int
    window_end_month: int
    feature_schema_version: str
    normalization_id: str
    node_ordering_sha256: str
    adjacency_sha256: str
    feature_sha256: str
    content_sha256: str
    created_at: datetime
    synthetic_scenario_id: UUID | None = None
    scenario_revision: int | None = None
    overlay_sha256: str | None = None


@dataclass(frozen=True)
class ModelVersion:
    model_version_id: UUID
    model_name: str
    semantic_version: str
    model_family: str
    dataset_version_id: UUID
    feature_schema_version: str
    inference_format: str
    artifact_locator: str
    checkpoint_sha256: str
    metrics: dict[str, Any]
    lifecycle_status: str
    created_at: datetime


@dataclass(frozen=True)
class RiskAssessment:
    risk_assessment_id: UUID
    enterprise_id: str
    graph_snapshot_id: UUID
    model_version_id: UUID
    input_sha256: str
    risk_score: float
    band: str
    explanations: tuple[dict[str, Any], ...]
    inferred_at: datetime

    def __post_init__(self) -> None:
        _require_probability(self.risk_score, "risk_score")


@dataclass(frozen=True)
class PolicyDecision:
    policy_decision_id: UUID
    risk_assessment_id: UUID
    policy_version: str
    decision: str
    low_threshold: float
    high_threshold: float
    reason_codes: tuple[str, ...]
    permitted_action: str
    created_at: datetime
    source_assessment: RiskAssessment = field(repr=False, compare=False)


@dataclass(frozen=True)
class IntegrityIncident:
    integrity_incident_id: UUID
    affected_ledger_event_id: int
    detected_at: datetime
    expected_hash: str
    actual_hash: str
    corrupted_payload: dict[str, Any]
    recovery_status: str
    trusted_recovery_source: str
    recovery_method: str | None = None
    operator: str | None = None
    recovered_at: datetime | None = None


@dataclass(frozen=True)
class LedgerEvent:
    event_id: int | None
    stream_id: str
    event_type: str
    entity_id: UUID
    payload: dict[str, Any]
    previous_hash: str
    event_hash: str
    created_at: datetime
