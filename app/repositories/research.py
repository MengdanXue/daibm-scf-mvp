from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

import numpy as np
from sqlalchemy.orm import Session

from app.models_research import (
    DatasetVersionModel,
    GraphSnapshotModel,
    ModelRunModel,
    ModelVersionModel,
    SyntheticScenarioModel,
)
from research.artifacts.verification import VerifiedArtifact


REFERENCE_NAMESPACE = uuid.UUID("8e15ee99-2de0-47c6-9af6-969a06d2ec04")
REFERENCE_CREATED_AT = datetime(2026, 8, 15, tzinfo=timezone.utc)


def reference_uuid(kind: str, identity: str) -> uuid.UUID:
    return uuid.uuid5(REFERENCE_NAMESPACE, f"{kind}:{identity}")


class ResearchRepository:
    def ensure_reference_registry(
        self,
        session: Session,
        artifact: VerifiedArtifact,
    ) -> dict[str, uuid.UUID]:
        manifest = artifact.manifest
        dataset_manifest = manifest["dataset"]
        dataset_id = reference_uuid(
            "dataset", dataset_manifest["content_sha256"]
        )
        scenario_id = reference_uuid("scenario", f"{dataset_id}:reference:0")
        snapshot_id = reference_uuid("snapshot", manifest["snapshot_sha256"])
        run_id = reference_uuid("run", manifest["checkpoint_sha256"])
        model_id = reference_uuid("model", manifest["artifact_sha256"])

        if session.get(DatasetVersionModel, dataset_id) is None:
            session.add(
                DatasetVersionModel(
                    dataset_version_id=dataset_id,
                    name=dataset_manifest["dataset_name"],
                    version=dataset_manifest["dataset_version"],
                    generation_seed=int(dataset_manifest["seed"]),
                    schema_version=dataset_manifest["schema_version"],
                    manifest=dataset_manifest,
                    content_sha256=dataset_manifest["content_sha256"],
                    created_at=REFERENCE_CREATED_AT,
                )
            )
        empty_overlay_hash = hashlib.sha256(b"{}").hexdigest()
        if session.get(SyntheticScenarioModel, scenario_id) is None:
            session.add(
                SyntheticScenarioModel(
                    synthetic_scenario_id=scenario_id,
                    dataset_version_id=dataset_id,
                    name="reference",
                    revision=0,
                    overlay={},
                    overlay_sha256=empty_overlay_hash,
                    status="active",
                    creator="reference-artifact",
                    created_at=REFERENCE_CREATED_AT,
                )
            )
        if session.get(GraphSnapshotModel, snapshot_id) is None:
            feature_hash = hashlib.sha256(
                np.ascontiguousarray(artifact.input_x.astype("<f4")).tobytes()
            ).hexdigest()
            adjacency_hash = hashlib.sha256(
                np.ascontiguousarray(
                    artifact.input_adjacency.astype("<f4")
                ).tobytes()
            ).hexdigest()
            node_hash = hashlib.sha256(
                "\n".join(f"E{index:04d}" for index in range(1, 501)).encode(
                    "ascii"
                )
            ).hexdigest()
            session.add(
                GraphSnapshotModel(
                    graph_snapshot_id=snapshot_id,
                    dataset_version_id=dataset_id,
                    synthetic_scenario_id=scenario_id,
                    scenario_revision=0,
                    overlay_sha256=empty_overlay_hash,
                    anchor_month=21,
                    window_start_month=10,
                    window_end_month=21,
                    feature_schema_version=manifest["feature_schema"]["version"],
                    normalization_id=manifest["normalization_id"],
                    node_ordering_sha256=node_hash,
                    adjacency_sha256=adjacency_hash,
                    feature_sha256=feature_hash,
                    content_sha256=manifest["snapshot_sha256"],
                    storage_locator="artifacts/reference/reference-inputs.npz",
                    created_at=REFERENCE_CREATED_AT,
                )
            )
        if session.get(ModelRunModel, run_id) is None:
            session.add(
                ModelRunModel(
                    model_run_id=run_id,
                    model_family="tgnn",
                    run_seed=int(manifest["run_seed"]),
                    dataset_version_id=dataset_id,
                    configuration=manifest["training_configuration"],
                    status="completed",
                    started_at=REFERENCE_CREATED_AT,
                    ended_at=REFERENCE_CREATED_AT,
                    metrics=manifest["metrics"],
                    logs_locator="output/research/reference-run/tgnn/tgnn-run.json",
                    failure_summary=None,
                )
            )
        if session.get(ModelVersionModel, model_id) is None:
            session.add(
                ModelVersionModel(
                    model_version_id=model_id,
                    model_name=manifest["model_name"],
                    semantic_version=manifest["semantic_version"],
                    model_family=manifest["model_family"],
                    source_run_id=run_id,
                    dataset_version_id=dataset_id,
                    feature_schema_version=manifest["feature_schema"]["version"],
                    inference_format=manifest["inference_format"],
                    artifact_locator=f"artifacts/reference/{manifest['artifact']}",
                    checkpoint_sha256=manifest["checkpoint_sha256"],
                    metrics=manifest["metrics"],
                    lifecycle_status="promoted",
                    deployment_slot="default",
                    created_at=REFERENCE_CREATED_AT,
                    promoted_at=REFERENCE_CREATED_AT,
                )
            )
        session.flush()
        return {
            "dataset_version_id": dataset_id,
            "synthetic_scenario_id": scenario_id,
            "graph_snapshot_id": snapshot_id,
            "model_run_id": run_id,
            "model_version_id": model_id,
        }

    @staticmethod
    def get_model(
        session: Session, model_version_id: uuid.UUID
    ) -> ModelVersionModel | None:
        return session.get(ModelVersionModel, model_version_id)

    @staticmethod
    def get_snapshot(
        session: Session, graph_snapshot_id: uuid.UUID
    ) -> GraphSnapshotModel | None:
        return session.get(GraphSnapshotModel, graph_snapshot_id)
