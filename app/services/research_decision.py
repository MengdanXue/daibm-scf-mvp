from __future__ import annotations

import uuid
from typing import Any

import numpy as np

from sqlalchemy.orm import Session, sessionmaker

from app.domain.research import PolicyDecision, RiskAssessment
from app.repositories.ledger import LedgerRepository
from app.repositories.research import ResearchRepository
from app.services.policy import PolicyEngine
from app.services.research_inference import ResearchInferenceService


class ResearchDecisionService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        inference_service: ResearchInferenceService,
        *,
        policy_engine: PolicyEngine | None = None,
        research_repository: ResearchRepository | None = None,
        ledger_repository: LedgerRepository | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.inference_service = inference_service
        self.policy_engine = policy_engine or PolicyEngine()
        self.research_repository = research_repository or ResearchRepository()
        self.ledger_repository = ledger_repository or LedgerRepository()

    def assess(
        self,
        *,
        enterprise_id: str,
        graph_snapshot_id: uuid.UUID,
        model_version_id: uuid.UUID,
        node_features: np.ndarray | None = None,
        adjacency: np.ndarray | None = None,
    ) -> dict[str, Any]:
        assessment = self.inference_service.assess(
            enterprise_id=enterprise_id,
            graph_snapshot_id=graph_snapshot_id,
            model_version_id=model_version_id,
            node_features=node_features,
            adjacency=adjacency,
        )
        decision = self.policy_engine.evaluate(assessment)
        with self.session_factory.begin() as session:
            snapshot = self.research_repository.get_snapshot(
                session, assessment.graph_snapshot_id
            )
            if snapshot is None:
                raise RuntimeError("Verified graph snapshot disappeared")
            snapshot_lineage = {
                "graph_snapshot_sha256": snapshot.content_sha256,
                "synthetic_scenario_id": (
                    str(snapshot.synthetic_scenario_id)
                    if snapshot.synthetic_scenario_id is not None
                    else None
                ),
                "scenario_revision": snapshot.scenario_revision,
                "overlay_sha256": snapshot.overlay_sha256,
                "normalization_id": snapshot.normalization_id,
                "node_ordering_sha256": snapshot.node_ordering_sha256,
                "adjacency_sha256": snapshot.adjacency_sha256,
                "feature_sha256": snapshot.feature_sha256,
            }
            self.research_repository.add_assessment(session, assessment)
            self.research_repository.add_policy_decision(session, decision)
            events = self.ledger_repository.append_many(
                session,
                assessment.risk_assessment_id,
                self._event_specs(assessment, decision, snapshot_lineage),
                stream_id="global",
            )
            event_dicts = [
                self.ledger_repository._event_to_dict(event) for event in events
            ]
        return self._trace(
            assessment, decision, event_dicts, snapshot_lineage
        )

    def get_trace(self, risk_assessment_id: uuid.UUID) -> dict[str, Any]:
        with self.session_factory() as session:
            trace = self.research_repository.get_trace(
                session, risk_assessment_id
            )
        if trace is None:
            raise KeyError(str(risk_assessment_id))
        return trace

    def _event_specs(
        self,
        assessment: RiskAssessment,
        decision: PolicyDecision,
        snapshot_lineage: dict[str, Any],
    ) -> list[tuple[str, dict[str, Any]]]:
        artifact = self.inference_service.artifact
        if artifact is None:
            raise RuntimeError("Promoted artifact disappeared after inference")
        manifest = artifact.manifest
        common = {
            "assessment_id": str(assessment.risk_assessment_id),
            "enterprise_id": assessment.enterprise_id,
            "graph_snapshot_id": str(assessment.graph_snapshot_id),
            "model_version_id": str(assessment.model_version_id),
            "artifact_sha256": manifest["artifact_sha256"],
            "checkpoint_sha256": manifest["checkpoint_sha256"],
            "dataset_content_sha256": manifest["dataset"]["content_sha256"],
            "feature_schema_version": manifest["feature_schema"]["version"],
            **snapshot_lineage,
            "input_sha256": assessment.input_sha256,
            "risk_score": assessment.risk_score,
            "inferred_at": assessment.inferred_at.isoformat(),
            "synthetic_data": True,
        }
        return [
            (
                "MODEL_INFERENCE_COMPLETED",
                {
                    **common,
                    "inference_engine": "onnxruntime-cpu",
                    "model_family": manifest["model_family"],
                },
            ),
            (
                "RISK_POLICY_TRIGGERED",
                {
                    **common,
                    "policy_decision_id": str(decision.policy_decision_id),
                    "policy_version": decision.policy_version,
                    "thresholds": [
                        decision.low_threshold,
                        decision.high_threshold,
                    ],
                    "decision": decision.decision,
                    "reason_codes": list(decision.reason_codes),
                },
            ),
            (
                "CONTROL_ACTION_REQUESTED",
                {
                    **common,
                    "policy_decision_id": str(decision.policy_decision_id),
                    "decision": decision.decision,
                    "permitted_action": decision.permitted_action,
                },
            ),
        ]

    def _trace(
        self,
        assessment: RiskAssessment,
        decision: PolicyDecision,
        ledger_events: list[dict[str, Any]],
        snapshot_lineage: dict[str, Any],
    ) -> dict[str, Any]:
        artifact = self.inference_service.artifact
        assert artifact is not None
        return {
            **self.inference_service.response(assessment),
            "policy_decision": {
                "policy_decision_id": str(decision.policy_decision_id),
                "policy_version": decision.policy_version,
                "decision": decision.decision,
                "thresholds": [
                    decision.low_threshold,
                    decision.high_threshold,
                ],
                "reason_codes": list(decision.reason_codes),
                "permitted_action": decision.permitted_action,
                "created_at": decision.created_at.isoformat(),
            },
            "ledger_events": ledger_events,
            "lineage": {
                "artifact_sha256": artifact.manifest["artifact_sha256"],
                "checkpoint_sha256": artifact.manifest["checkpoint_sha256"],
                "dataset_content_sha256": artifact.manifest["dataset"][
                    "content_sha256"
                ],
                "feature_schema_version": artifact.manifest["feature_schema"][
                    "version"
                ],
                **snapshot_lineage,
                "input_sha256": assessment.input_sha256,
                "model_version_id": str(assessment.model_version_id),
            },
            "persisted": True,
        }
