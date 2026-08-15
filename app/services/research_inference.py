from __future__ import annotations

import hashlib
import math
import uuid
from datetime import datetime, timezone
from typing import Any

import numpy as np
from sqlalchemy.orm import Session, sessionmaker

from app.config import ResearchSettings
from app.domain.research import RiskAssessment
from app.repositories.research import ResearchRepository
from research.artifacts.verification import (
    ArtifactVerificationError,
    VerifiedArtifact,
    verify_reference_artifact,
)


class ResearchModelUnavailable(RuntimeError):
    pass


class ResearchResourceNotFound(KeyError):
    pass


class ResearchInferenceService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: ResearchSettings,
        repository: ResearchRepository | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.repository = repository or ResearchRepository()
        self.artifact: VerifiedArtifact | None = None
        self.unavailable_reason = "artifact_not_initialized"
        self.identities: dict[str, uuid.UUID] = {}
        try:
            self.artifact = verify_reference_artifact(settings.reference_dir)
            self.unavailable_reason = ""
        except (ArtifactVerificationError, OSError, ValueError, KeyError):
            self.unavailable_reason = "artifact_verification_failed"

    def initialize(self) -> None:
        if self.artifact is None:
            return
        with self.session_factory.begin() as session:
            self.identities = self.repository.ensure_reference_registry(
                session, self.artifact
            )

    @property
    def available(self) -> bool:
        return self.artifact is not None and bool(self.identities)

    def health(self) -> dict[str, Any]:
        if not self.available:
            return {
                "status": "unavailable",
                "required": self.settings.required,
            }
        assert self.artifact is not None
        return {
            "status": "ready",
            "required": self.settings.required,
            "artifact_sha256": self.artifact.manifest["artifact_sha256"],
            "model_version_id": str(self.identities["model_version_id"]),
        }

    def status(self) -> dict[str, Any]:
        self._require_available()
        assert self.artifact is not None
        manifest = self.artifact.manifest
        dataset = manifest["dataset"]
        return {
            "status": "ready",
            "data_provenance": "SYNTHETIC_DEMO",
            "dataset": {
                "dataset_version_id": str(
                    self.identities["dataset_version_id"]
                ),
                "name": dataset["dataset_name"],
                "version": dataset["dataset_version"],
                "content_sha256": dataset["content_sha256"],
                "enterprise_count": dataset["counts"]["enterprises"],
                "months": dataset["counts"]["months"],
            },
            "graph": {
                "graph_snapshot_id": str(
                    self.identities["graph_snapshot_id"]
                ),
                "anchor_month": 21,
                "window": [10, 21],
                "sequence_length": manifest["sequence_length"],
                "node_count": manifest["node_count"],
                "snapshot_sha256": manifest["snapshot_sha256"],
                "direction": manifest["feature_schema"]["direction"],
            },
            "model": {
                "model_version_id": str(self.identities["model_version_id"]),
                "name": manifest["model_name"],
                "version": manifest["semantic_version"],
                "family": manifest["model_family"],
                "checkpoint_sha256": manifest["checkpoint_sha256"],
                "artifact_sha256": manifest["artifact_sha256"],
                "lifecycle_status": manifest["lifecycle_status"],
                "deployment_slot": manifest["deployment_slot"],
                "metrics": manifest["metrics"],
                "real_inference": True,
            },
            "policy": {
                "version": "scf-risk-policy-v0.4",
                "thresholds": [0.40, 0.75],
            },
        }

    def assess(
        self,
        *,
        enterprise_id: str,
        graph_snapshot_id: uuid.UUID,
        model_version_id: uuid.UUID,
    ) -> RiskAssessment:
        self._require_available()
        assert self.artifact is not None
        enterprise_index = self._enterprise_index(enterprise_id)
        with self.session_factory() as session:
            model = self.repository.get_model(session, model_version_id)
            snapshot = self.repository.get_snapshot(session, graph_snapshot_id)
            if model is None or snapshot is None:
                raise ResearchResourceNotFound
            manifest = self.artifact.manifest
            if (
                model.lifecycle_status != "promoted"
                or model.deployment_slot != "default"
                or model.checkpoint_sha256 != manifest["checkpoint_sha256"]
                or snapshot.content_sha256 != manifest["snapshot_sha256"]
                or snapshot.normalization_id != manifest["normalization_id"]
            ):
                raise ResearchModelUnavailable

        logits = self.artifact.session.run(
            ("logits",),
            {
                "node_features": self.artifact.input_x,
                "adjacency": self.artifact.input_adjacency,
            },
        )[0]
        score = 1.0 / (1.0 + math.exp(-float(logits[0, enterprise_index])))
        feature_names = self.artifact.manifest["feature_schema"]["names"]
        current = self.artifact.input_x[0, -1, enterprise_index]
        top = np.argsort(np.abs(current))[-3:][::-1]
        explanations = tuple(
            {
                "feature": feature_names[int(index)],
                "normalized_value": round(float(current[int(index)]), 6),
                "interpretation": "input_signal_not_causal_attribution",
            }
            for index in top
        )
        input_digest = hashlib.sha256()
        input_digest.update(self.artifact.manifest["artifact_sha256"].encode())
        input_digest.update(str(graph_snapshot_id).encode())
        input_digest.update(enterprise_id.encode())
        input_digest.update(
            np.ascontiguousarray(
                self.artifact.input_x[:, :, enterprise_index].astype("<f4")
            ).tobytes()
        )
        band = "low" if score < 0.40 else "medium" if score < 0.75 else "high"
        return RiskAssessment(
            risk_assessment_id=uuid.uuid4(),
            enterprise_id=enterprise_id,
            graph_snapshot_id=graph_snapshot_id,
            model_version_id=model_version_id,
            input_sha256=input_digest.hexdigest(),
            risk_score=score,
            band=band,
            explanations=explanations,
            inferred_at=datetime.now(timezone.utc),
        )

    @staticmethod
    def response(assessment: RiskAssessment) -> dict[str, Any]:
        return {
            "risk_assessment_id": str(assessment.risk_assessment_id),
            "enterprise_id": assessment.enterprise_id,
            "graph_snapshot_id": str(assessment.graph_snapshot_id),
            "model_version_id": str(assessment.model_version_id),
            "input_sha256": assessment.input_sha256,
            "risk_score": assessment.risk_score,
            "band": assessment.band,
            "explanations": list(assessment.explanations),
            "inferred_at": assessment.inferred_at.isoformat(),
            "inference_engine": "onnxruntime-cpu",
            "real_model_inference": True,
            "synthetic_data": True,
            "persisted": False,
        }

    def _require_available(self) -> None:
        if not self.available:
            raise ResearchModelUnavailable

    def _enterprise_index(self, enterprise_id: str) -> int:
        try:
            numeric = int(enterprise_id.removeprefix("E"))
        except ValueError as error:
            raise ResearchResourceNotFound from error
        node_count = int(self.artifact.manifest["node_count"]) if self.artifact else 0
        if enterprise_id != f"E{numeric:04d}" or not 1 <= numeric <= node_count:
            raise ResearchResourceNotFound
        return numeric - 1
