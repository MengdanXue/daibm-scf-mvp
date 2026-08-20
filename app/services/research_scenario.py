from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

import numpy as np

from app.repositories.ledger import LedgerRepository
from app.repositories.research import ResearchRepository, reference_uuid
from app.services.research_decision import ResearchDecisionService
from app.services.research_inference import ResearchInferenceService
from research.data.generator import generate_dataset
from research.data.schema import STATE_FEATURE_NAMES
from research.graph.builder import build_graph_series, build_samples


class ResearchScenarioService:
    def __init__(
        self,
        inference_service: ResearchInferenceService,
        decision_service: ResearchDecisionService,
        *,
        research_repository: ResearchRepository | None = None,
        ledger_repository: LedgerRepository | None = None,
    ) -> None:
        self.inference_service = inference_service
        self.decision_service = decision_service
        self.session_factory = inference_service.session_factory
        self.research_repository = research_repository or ResearchRepository()
        self.ledger_repository = ledger_repository or LedgerRepository()
        self.reference_dataset = generate_dataset()

    def inject_risk(self, enterprise_id: str) -> dict[str, Any]:
        self.inference_service._require_available()
        enterprise_index = self.inference_service._enterprise_index(enterprise_id)
        identities = self.inference_service.identities
        before = self.decision_service.assess(
            enterprise_id=enterprise_id,
            graph_snapshot_id=identities["graph_snapshot_id"],
            model_version_id=identities["model_version_id"],
        )
        overlay = self._overlay(enterprise_id, enterprise_index)
        overlay_bytes = json.dumps(
            overlay,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        overlay_sha256 = hashlib.sha256(overlay_bytes).hexdigest()
        graph = build_graph_series(self.reference_dataset, overlay)
        samples = build_samples(graph)
        sample_x = np.asarray(samples.x[-1:], dtype=np.float32)
        sample_adjacency = np.asarray(samples.adjacency[-1:], dtype=np.float32)
        feature_sha256 = hashlib.sha256(
            np.ascontiguousarray(sample_x.astype("<f4")).tobytes()
        ).hexdigest()
        adjacency_sha256 = hashlib.sha256(
            np.ascontiguousarray(sample_adjacency.astype("<f4")).tobytes()
        ).hexdigest()
        node_ordering_sha256 = hashlib.sha256(
            "\n".join(self.reference_dataset.enterprise_ids).encode("ascii")
        ).hexdigest()
        created_at = datetime.now(timezone.utc)
        scenario_name = f"risk-demo:{enterprise_id}"

        with self.session_factory.begin() as session:
            revision = self.research_repository.next_scenario_revision(
                session,
                identities["dataset_version_id"],
                scenario_name,
            )
            scenario_id = reference_uuid(
                "scenario",
                f"{scenario_name}:{revision}:{overlay_sha256}",
            )
            snapshot_content_sha256 = hashlib.sha256(
                (
                    samples.snapshot_sha256[-1]
                    + overlay_sha256
                    + str(revision)
                ).encode("ascii")
            ).hexdigest()
            graph_snapshot_id = reference_uuid(
                "snapshot", snapshot_content_sha256
            )
            self.research_repository.add_scenario(
                session,
                scenario_id=scenario_id,
                dataset_version_id=identities["dataset_version_id"],
                name=scenario_name,
                revision=revision,
                overlay=overlay,
                overlay_sha256=overlay_sha256,
                created_at=created_at,
            )
            self.research_repository.add_graph_snapshot(
                session,
                graph_snapshot_id=graph_snapshot_id,
                dataset_version_id=identities["dataset_version_id"],
                synthetic_scenario_id=scenario_id,
                scenario_revision=revision,
                overlay_sha256=overlay_sha256,
                feature_schema_version=self.inference_service.artifact.manifest[
                    "feature_schema"
                ]["version"],
                normalization_id=samples.normalization.normalization_id,
                node_ordering_sha256=node_ordering_sha256,
                adjacency_sha256=adjacency_sha256,
                feature_sha256=feature_sha256,
                content_sha256=snapshot_content_sha256,
                created_at=created_at,
            )
            self.ledger_repository.append_many(
                session,
                scenario_id,
                [
                    (
                        "SIMULATED_RISK_INJECTED",
                        {
                            "enterprise_id": enterprise_id,
                            "scenario_id": str(scenario_id),
                            "scenario_revision": revision,
                            "overlay_sha256": overlay_sha256,
                            "graph_snapshot_id": str(graph_snapshot_id),
                            "graph_snapshot_sha256": snapshot_content_sha256,
                            "changed_months": overlay["months"],
                            "simulated": True,
                        },
                    )
                ],
                stream_id="global",
            )

        after = self.decision_service.assess(
            enterprise_id=enterprise_id,
            graph_snapshot_id=graph_snapshot_id,
            model_version_id=identities["model_version_id"],
            node_features=sample_x,
            adjacency=sample_adjacency,
        )
        feature_index = {
            name: index for index, name in enumerate(STATE_FEATURE_NAMES)
        }
        changed_inputs = [
            {
                "feature": name,
                "before": float(
                    self.reference_dataset.states[20, enterprise_index, index]
                ),
                "after": float(graph.dataset.states[20, enterprise_index, index]),
            }
            for name, index in feature_index.items()
            if not np.isclose(
                self.reference_dataset.states[20, enterprise_index, index],
                graph.dataset.states[20, enterprise_index, index],
            )
        ]
        return {
            "scenario": {
                "synthetic_scenario_id": str(scenario_id),
                "name": scenario_name,
                "revision": revision,
                "overlay_sha256": overlay_sha256,
                "provenance": "SIMULATED",
                "relationship_direction": "supplier_to_customer",
                "affected_enterprise_ids": overlay[
                    "affected_enterprise_ids"
                ],
            },
            "before": before,
            "after": after,
            "changed_inputs": changed_inputs,
        }

    def reset(self) -> dict[str, Any]:
        status = self.inference_service.status()
        return {
            "scenario": {
                "synthetic_scenario_id": str(
                    self.inference_service.identities["synthetic_scenario_id"]
                ),
                "name": "reference",
                "revision": 0,
                "provenance": "SYNTHETIC_DEMO",
            },
            "graph": status["graph"],
        }

    def _overlay(self, enterprise_id: str, enterprise_index: int) -> dict[str, Any]:
        connected = (
            (self.reference_dataset.relationships[:, 0] == enterprise_index)
            | (self.reference_dataset.relationships[:, 1] == enterprise_index)
        )
        affected_indices = sorted(
            {
                enterprise_index,
                *self.reference_dataset.relationships[connected]
                .reshape(-1)
                .astype(int)
                .tolist(),
            }
        )
        return {
            "event": {
                "event_type": "synthetic_liquidity_and_payment_shock",
                "enterprise_id": enterprise_id,
                "severity": "high",
                "simulated": True,
            },
            "enterprise_index": enterprise_index,
            "affected_enterprise_indices": affected_indices,
            "affected_enterprise_ids": [
                f"E{index + 1:04d}" for index in affected_indices
            ],
            "months": [19, 20, 21],
            "state_values": {
                "credit_history_score": 0.03,
                "liquidity_ratio": 0.01,
                "leverage_ratio": 0.99,
                "cash_flow_index": 0.01,
                "operational_stability": 0.02,
                "overdue_ratio": 0.99,
                "average_delay_normalized": 0.99,
            },
            "edge_values": {
                "amount_multiplier": 0.25,
                "count_multiplier": 0.50,
                "overdue_ratio": 0.99,
                "average_delay_days": 85.0,
            },
        }
