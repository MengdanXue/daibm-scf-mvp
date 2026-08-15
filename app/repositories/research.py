from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ledger import canonical_timestamp
from app.domain.research import PolicyDecision, RiskAssessment
from app.models import LedgerEventModel

from app.models_research import (
    DatasetVersionModel,
    GraphSnapshotModel,
    ModelRunModel,
    ModelVersionModel,
    PolicyDecisionModel,
    RiskAssessmentModel,
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

    @staticmethod
    def add_assessment(
        session: Session, assessment: RiskAssessment
    ) -> RiskAssessmentModel:
        model = RiskAssessmentModel(
            risk_assessment_id=assessment.risk_assessment_id,
            enterprise_id=assessment.enterprise_id,
            graph_snapshot_id=assessment.graph_snapshot_id,
            model_version_id=assessment.model_version_id,
            input_sha256=assessment.input_sha256,
            risk_score=assessment.risk_score,
            band=assessment.band,
            explanations=list(assessment.explanations),
            inferred_at=assessment.inferred_at,
        )
        session.add(model)
        session.flush()
        return model

    @staticmethod
    def add_policy_decision(
        session: Session, decision: PolicyDecision
    ) -> PolicyDecisionModel:
        model = PolicyDecisionModel(
            policy_decision_id=decision.policy_decision_id,
            risk_assessment_id=decision.risk_assessment_id,
            policy_version=decision.policy_version,
            decision=decision.decision,
            low_threshold=decision.low_threshold,
            high_threshold=decision.high_threshold,
            reason_codes=list(decision.reason_codes),
            permitted_action=decision.permitted_action,
            created_at=decision.created_at,
        )
        session.add(model)
        session.flush()
        return model

    @staticmethod
    def get_trace(
        session: Session, risk_assessment_id: uuid.UUID
    ) -> dict[str, object] | None:
        assessment = session.get(RiskAssessmentModel, risk_assessment_id)
        if assessment is None:
            return None
        decision = session.scalar(
            select(PolicyDecisionModel).where(
                PolicyDecisionModel.risk_assessment_id == risk_assessment_id
            )
        )
        events = list(
            session.scalars(
                select(LedgerEventModel)
                .where(LedgerEventModel.entity_id == risk_assessment_id)
                .order_by(LedgerEventModel.id.asc())
            )
        )
        if decision is None or len(events) != 3:
            return None
        first_payload = events[0].payload
        return {
            "risk_assessment_id": str(assessment.risk_assessment_id),
            "enterprise_id": assessment.enterprise_id,
            "graph_snapshot_id": str(assessment.graph_snapshot_id),
            "model_version_id": str(assessment.model_version_id),
            "input_sha256": assessment.input_sha256,
            "risk_score": assessment.risk_score,
            "band": assessment.band,
            "explanations": assessment.explanations,
            "inferred_at": canonical_timestamp(assessment.inferred_at),
            "policy_decision": {
                "policy_decision_id": str(decision.policy_decision_id),
                "policy_version": decision.policy_version,
                "decision": decision.decision,
                "thresholds": [
                    decision.low_threshold,
                    decision.high_threshold,
                ],
                "reason_codes": decision.reason_codes,
                "permitted_action": decision.permitted_action,
                "created_at": canonical_timestamp(decision.created_at),
            },
            "ledger_events": [
                {
                    "id": event.id,
                    "created_at": canonical_timestamp(event.created_at),
                    "stream_id": event.stream_id,
                    "event_type": event.event_type,
                    "entity_id": str(event.entity_id),
                    "payload": event.payload,
                    "previous_hash": event.previous_hash,
                    "event_hash": event.event_hash,
                }
                for event in events
            ],
            "lineage": {
                key: first_payload[key]
                for key in (
                    "artifact_sha256",
                    "checkpoint_sha256",
                    "dataset_content_sha256",
                    "feature_schema_version",
                    "graph_snapshot_sha256",
                    "input_sha256",
                    "model_version_id",
                )
            },
            "inference_engine": "onnxruntime-cpu",
            "real_model_inference": True,
            "synthetic_data": True,
            "persisted": True,
        }
