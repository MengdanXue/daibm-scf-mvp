from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.domain.model_registry import ModelVersionStatus, model_id_for
from app.models_model_governance import (
    RiskModelVersionModel,
    RiskModelVersionTransitionModel,
)
from app.models_outcome import CalibrationRunModel


class ModelRegistryRepository:
    """Persistence for governed model versions; every status change is audited."""

    def get(self, session: Session, version_id: uuid.UUID, *, for_update: bool = False):
        statement = select(RiskModelVersionModel).where(RiskModelVersionModel.id == version_id)
        if for_update:
            statement = statement.with_for_update()
        return session.scalar(statement)

    def get_by_run(self, session: Session, run_id: uuid.UUID) -> RiskModelVersionModel | None:
        return session.scalar(
            select(RiskModelVersionModel).where(RiskModelVersionModel.calibration_run_id == run_id)
        )

    def get_active_version(
        self, session: Session, *, scope: str, for_update: bool = False
    ) -> RiskModelVersionModel | None:
        statement = select(RiskModelVersionModel).where(
            RiskModelVersionModel.scope == scope,
            RiskModelVersionModel.status == ModelVersionStatus.ACTIVE.value,
        )
        if for_update:
            statement = statement.with_for_update()
        return session.scalar(statement)

    def list_versions(
        self, session: Session, *, scope: str | None = None
    ) -> list[RiskModelVersionModel]:
        statement = select(RiskModelVersionModel).order_by(
            RiskModelVersionModel.scope, RiskModelVersionModel.version.desc()
        )
        if scope is not None:
            statement = statement.where(RiskModelVersionModel.scope == scope)
        return list(session.scalars(statement))

    def list_transitions(
        self,
        session: Session,
        *,
        version_ids: list[uuid.UUID] | None = None,
        scope: str | None = None,
        activation_only: bool = False,
    ) -> list[RiskModelVersionTransitionModel]:
        statement = (
            select(RiskModelVersionTransitionModel)
            .join(
                RiskModelVersionModel,
                RiskModelVersionModel.id == RiskModelVersionTransitionModel.model_version_id,
            )
            .order_by(RiskModelVersionTransitionModel.id)
        )
        if version_ids is not None:
            statement = statement.where(
                RiskModelVersionTransitionModel.model_version_id.in_(version_ids)
            )
        if scope is not None:
            statement = statement.where(RiskModelVersionModel.scope == scope)
        if activation_only:
            statement = statement.where(
                (RiskModelVersionTransitionModel.to_status == ModelVersionStatus.ACTIVE.value)
                | (RiskModelVersionTransitionModel.from_status == ModelVersionStatus.ACTIVE.value)
            )
        return list(session.scalars(statement))

    def register(
        self,
        session: Session,
        run: CalibrationRunModel,
        *,
        created_by: str,
        created_by_user_id: uuid.UUID | None,
        reason: str = "artifact_registered",
    ) -> RiskModelVersionModel:
        if run.artifact_locator is None or run.artifact_sha256 is None:
            raise ValueError("only a published artifact can be registered")
        model_id = model_id_for(run.deployment_scope)
        session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": f"registry:{model_id}"}
        )
        next_version = (
            session.scalar(
                select(func.max(RiskModelVersionModel.version)).where(
                    RiskModelVersionModel.model_id == model_id
                )
            )
            or 0
        ) + 1
        now = datetime.now(timezone.utc)
        version = RiskModelVersionModel(
            id=uuid.uuid4(),
            model_id=model_id,
            version=next_version,
            model_type="platt_calibration",
            calibration_run_id=run.calibration_run_id,
            artifact_path=run.artifact_locator,
            artifact_hash=run.artifact_sha256,
            scope=run.deployment_scope,
            training_dataset_version=run.dataset_sha256,
            metrics={
                "holdout_before": run.metrics_before,
                "holdout_after": run.metrics_after,
                "sample_count": run.sample_count,
                "positive_count": run.positive_count,
                "negative_count": run.negative_count,
            },
            status=ModelVersionStatus.DRAFT.value,
            status_sequence=1,
            created_by=created_by,
            created_by_user_id=created_by_user_id,
            created_at=now,
        )
        session.add(version)
        session.flush()
        session.add(
            RiskModelVersionTransitionModel(
                model_version_id=version.id,
                from_status=None,
                to_status=ModelVersionStatus.DRAFT.value,
                status_sequence=1,
                reason=reason,
                actor_user_id=created_by_user_id,
                actor_label=created_by,
                evaluation_metrics=None,
                artifact_hash=version.artifact_hash,
            )
        )
        session.flush()
        return version

    def transition(
        self,
        session: Session,
        version: RiskModelVersionModel,
        target: ModelVersionStatus,
        *,
        reason: str,
        metrics: dict[str, Any] | None = None,
    ) -> None:
        """Change status through the audited SQL function, then refresh the row."""

        session.flush()
        session.execute(
            text(
                "SELECT transition_model_version(:id, :target, :reason, CAST(:metrics AS jsonb))"
            ),
            {
                "id": version.id,
                "target": target.value,
                "reason": reason,
                "metrics": None if metrics is None else _json(metrics),
            },
        )
        session.refresh(version)

    def record_evaluation(
        self,
        session: Session,
        version: RiskModelVersionModel,
        *,
        evidence: dict[str, Any],
        passed: bool,
        reason: str,
    ) -> None:
        """DRAFT -> EVALUATING -> CANDIDATE or REJECTED, evidence kept immutably."""

        if version.status == ModelVersionStatus.DRAFT.value:
            self.transition(
                session, version, ModelVersionStatus.EVALUATING, reason="evaluation_started"
            )
        if version.status != ModelVersionStatus.EVALUATING.value:
            raise ValueError(f"model version is {version.status}, not EVALUATING")
        version.evaluation = evidence
        version.evaluation_passed = passed
        version.evaluated_at = datetime.now(timezone.utc)
        session.flush()
        self.transition(
            session,
            version,
            ModelVersionStatus.CANDIDATE if passed else ModelVersionStatus.REJECTED,
            reason=reason,
            metrics=evidence,
        )


def _json(value: dict[str, Any]) -> str:
    import json

    return json.dumps(value, sort_keys=True, default=str)


__all__ = ["ModelRegistryRepository"]
