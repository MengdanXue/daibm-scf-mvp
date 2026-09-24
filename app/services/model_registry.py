"""Model registry over calibration models and promoted research models.

Calibration models are the governed ``calibration_runs``; their registry
status and full event history come from PostgreSQL (``model_registry_events``
is written by triggers, so every state change is recorded). Research TGNN
models are read-only entries from ``model_versions``.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.domain.governance import (
    REGISTRY_STATUS_DESCRIPTIONS,
    RegistryStatus,
    calibration_registry_status,
    research_registry_status,
    scope_matrix,
)
from app.identity import AuthenticatedUser
from app.ledger import canonical_timestamp
from app.models_governance import CalibrationJobModel, OutcomeCorrectionModel
from app.models_identity import UserModel
from app.models_model_governance import ModelRegistryEventModel
from app.models_outcome import ActualOutcomeModel, CalibrationRunModel
from app.models_research import ModelVersionModel
from app.repositories.ledger import LedgerRepository
from app.repositories.outcomes import OutcomeRepository
from app.services.outcomes import OutcomeService

READ_ROLES = {"auditor", "risk_manager", "financier"}


class RegistryError(Exception):
    pass


class RegistryNotFound(RegistryError):
    pass


class RegistryForbidden(RegistryError):
    pass


class RegistryConflict(RegistryError):
    pass


class ModelRegistryService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        repository: OutcomeRepository | None = None,
        ledger_repository: LedgerRepository | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.repository = repository or OutcomeRepository()
        self.ledger_repository = ledger_repository or LedgerRepository()
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    # --- Queries ------------------------------------------------------------

    def list_models(
        self, user: AuthenticatedUser, *, scope: str | None = None
    ) -> dict[str, Any]:
        self._require_read(user)
        with self.session_factory() as session:
            runs = list(
                session.scalars(
                    select(CalibrationRunModel).order_by(
                        CalibrationRunModel.deployment_scope,
                        CalibrationRunModel.completed_at,
                        CalibrationRunModel.calibration_run_id,
                    )
                )
            )
            versions = self._version_labels(runs)
            creators = self._creators(session, runs)
            entries = [
                self._calibration_entry(run, versions[run.calibration_run_id], creators)
                for run in runs
                if scope is None or run.deployment_scope == scope
            ]
            if scope is None:
                entries.extend(
                    self._research_entry(item)
                    for item in session.scalars(
                        select(ModelVersionModel).order_by(ModelVersionModel.created_at)
                    )
                )
            entries.sort(key=lambda item: item["created_time"], reverse=True)
            active = {
                item["training_scope"]: item["model_id"]
                for item in entries
                if item["status"] == RegistryStatus.ACTIVE and item["model_kind"] == "calibration"
            }
            return {
                "models": entries,
                "active_by_scope": active,
                "status_descriptions": {
                    status.value: text for status, text in REGISTRY_STATUS_DESCRIPTIONS.items()
                },
                "scope_compatibility": scope_matrix(),
            }

    def get_model(
        self, kind: str, model_id: str | uuid.UUID, user: AuthenticatedUser
    ) -> dict[str, Any]:
        self._require_read(user)
        normalized = self._uuid(model_id)
        with self.session_factory() as session:
            if kind == "research":
                version = session.get(ModelVersionModel, normalized)
                if version is None:
                    raise RegistryNotFound(str(model_id))
                return {**self._research_entry(version), "events": [], "artifact_check": None}
            if kind != "calibration":
                raise RegistryNotFound(kind)
            run = session.get(CalibrationRunModel, normalized)
            if run is None:
                raise RegistryNotFound(str(model_id))
            runs = list(
                session.scalars(
                    select(CalibrationRunModel)
                    .where(CalibrationRunModel.deployment_scope == run.deployment_scope)
                    .order_by(
                        CalibrationRunModel.completed_at,
                        CalibrationRunModel.calibration_run_id,
                    )
                )
            )
            entry = self._calibration_entry(
                run, self._version_labels(runs)[run.calibration_run_id], self._creators(session, [run])
            )
            events = list(
                session.scalars(
                    select(ModelRegistryEventModel)
                    .where(
                        ModelRegistryEventModel.model_kind == "calibration",
                        ModelRegistryEventModel.model_id == run.calibration_run_id,
                    )
                    .order_by(ModelRegistryEventModel.event_id)
                )
            )
            actors = self._usernames(session, {event.actor_user_id for event in events})
            return {
                **entry,
                "membership_count": len(
                    self.repository.list_run_membership(session, run.calibration_run_id)
                ),
                "artifact_check": self._artifact_check(run),
                "events": [
                    {
                        "event_type": event.event_type,
                        "from_status": event.from_status,
                        "to_status": event.to_status,
                        "reason": event.reason,
                        "dataset_sha256": event.dataset_sha256,
                        "sample_count": event.sample_count,
                        "artifact_sha256": event.artifact_sha256,
                        "metrics": event.metrics,
                        "actor": actors.get(event.actor_user_id, "system"),
                        "recorded_at": canonical_timestamp(event.recorded_at),
                    }
                    for event in events
                ],
            }

    # --- Commands -----------------------------------------------------------

    def retire(
        self,
        model_id: str | uuid.UUID,
        user: AuthenticatedUser,
        *,
        reason_code: str,
    ) -> dict[str, Any]:
        """Retire a non-active calibration model; an ACTIVE model must roll back first."""

        if user.role != "auditor":
            raise RegistryForbidden("Only auditors can retire models")
        normalized = self._uuid(model_id)
        with self.session_factory.begin() as session:
            unlocked = session.get(CalibrationRunModel, normalized)
            if unlocked is None:
                raise RegistryNotFound(str(model_id))
            self.repository.acquire_scope_lock(session, scope=unlocked.deployment_scope)
            run = self.repository.get_run_for_update(session, normalized)
            if run is None:
                raise RegistryNotFound(str(model_id))
            if run.deployment_status == "active":
                raise RegistryConflict("An ACTIVE model cannot be retired; roll back first")
            if run.retired_at is not None:
                raise RegistryConflict("Model is already retired")
            previous = calibration_registry_status(
                status=run.status,
                deployment_status=run.deployment_status,
                rolled_back_at=run.rolled_back_at,
                retired_at=run.retired_at,
            )
            OutcomeService.set_governance_actor(session, user)
            now = self.clock()
            run.retired_at = now
            run.retired_by_user_id = user.user_id
            run.retirement_reason = reason_code
            session.flush()
            self.ledger_repository.append_many(
                session,
                run.calibration_run_id,
                [
                    (
                        "CALIBRATION_MODEL_RETIRED",
                        {
                            "calibration_run_id": str(run.calibration_run_id),
                            "deployment_scope": run.deployment_scope,
                            "previous_registry_status": previous.value,
                            "reason_code": reason_code,
                            "retired_by_user_id": str(user.user_id),
                        },
                    )
                ],
            )
        return self.get_model("calibration", normalized, user)

    # --- Helpers ------------------------------------------------------------

    @staticmethod
    def _version_labels(runs: list[CalibrationRunModel]) -> dict[uuid.UUID, str]:
        labels: dict[uuid.UUID, str] = {}
        counters: dict[str, int] = {}
        for run in sorted(runs, key=lambda item: (item.completed_at, str(item.calibration_run_id))):
            counters[run.deployment_scope] = counters.get(run.deployment_scope, 0) + 1
            labels[run.calibration_run_id] = f"platt-{run.deployment_scope}-v{counters[run.deployment_scope]}"
        return labels

    def _creators(
        self, session: Session, runs: list[CalibrationRunModel]
    ) -> dict[uuid.UUID, str]:
        """Who triggered each training run: the outcome or correction author."""

        job_ids = [run.trigger_job_id for run in runs if run.trigger_job_id is not None]
        creators: dict[uuid.UUID, str] = {}
        if not job_ids:
            return creators
        rows = session.execute(
            select(
                CalibrationJobModel.job_id,
                UserModel.username,
            )
            .select_from(CalibrationJobModel)
            .outerjoin(
                ActualOutcomeModel,
                ActualOutcomeModel.outcome_id == CalibrationJobModel.trigger_outcome_id,
            )
            .outerjoin(
                OutcomeCorrectionModel,
                OutcomeCorrectionModel.correction_id == CalibrationJobModel.trigger_correction_id,
            )
            .join(
                UserModel,
                UserModel.user_id.in_(
                    [ActualOutcomeModel.submitted_by_user_id, OutcomeCorrectionModel.auditor_user_id]
                ),
            )
            .where(CalibrationJobModel.job_id.in_(job_ids))
        ).all()
        by_job = {job_id: username for job_id, username in rows}
        for run in runs:
            if run.trigger_job_id in by_job:
                creators[run.calibration_run_id] = by_job[run.trigger_job_id]
        return creators

    @staticmethod
    def _usernames(session: Session, user_ids: set[uuid.UUID | None]) -> dict[uuid.UUID | None, str]:
        ids = [item for item in user_ids if item is not None]
        if not ids:
            return {}
        return {
            user_id: username
            for user_id, username in session.execute(
                select(UserModel.user_id, UserModel.username).where(UserModel.user_id.in_(ids))
            )
        }

    @staticmethod
    def _calibration_entry(
        run: CalibrationRunModel, version: str, creators: dict[uuid.UUID, str]
    ) -> dict[str, Any]:
        status = calibration_registry_status(
            status=run.status,
            deployment_status=run.deployment_status,
            rolled_back_at=run.rolled_back_at,
            retired_at=run.retired_at,
        )
        return {
            "model_kind": "calibration",
            "model_id": str(run.calibration_run_id),
            "version": version,
            "model_type": "platt_calibration",
            "artifact_path": run.artifact_locator,
            "artifact_hash": run.artifact_sha256,
            "artifact_schema": run.artifact_schema,
            "created_time": canonical_timestamp(run.completed_at),
            "creator": creators.get(run.calibration_run_id, "calibration-worker"),
            "training_scope": run.deployment_scope,
            "training_dataset_version": run.dataset_sha256,
            "sample_count": run.sample_count,
            "evaluation_metrics": {
                "holdout_before": run.metrics_before,
                "holdout_after": run.metrics_after,
            },
            "status": status.value,
            "deployment_status": run.deployment_status,
            "activation_reason": run.activation_reason,
            "failure_code": run.failure_code,
            "activated_at": canonical_timestamp(run.activated_at) if run.activated_at else None,
            "deactivated_at": (
                canonical_timestamp(run.deactivated_at) if run.deactivated_at else None
            ),
            "previous_active_model_id": (
                str(run.previous_active_run_id) if run.previous_active_run_id else None
            ),
            "retirement_reason": run.retirement_reason,
            "can_rollback": status == RegistryStatus.ACTIVE and run.previous_active_run_id is not None,
            "can_retire": status not in {RegistryStatus.ACTIVE, RegistryStatus.RETIRED},
        }

    @staticmethod
    def _research_entry(version: ModelVersionModel) -> dict[str, Any]:
        status = research_registry_status(version.lifecycle_status, version.deployment_slot)
        return {
            "model_kind": "research",
            "model_id": str(version.model_version_id),
            "version": f"{version.model_name}@{version.semantic_version}",
            "model_type": f"{version.model_family}_{version.inference_format}",
            "artifact_path": version.artifact_locator,
            "artifact_hash": version.checkpoint_sha256,
            "artifact_schema": version.feature_schema_version,
            "created_time": canonical_timestamp(version.created_at),
            "creator": "offline-research-pipeline",
            "training_scope": "synthetic_reference",
            "training_dataset_version": str(version.dataset_version_id),
            "sample_count": None,
            "evaluation_metrics": version.metrics,
            "status": status.value,
            "deployment_status": version.deployment_slot or version.lifecycle_status,
            "activation_reason": version.lifecycle_status,
            "failure_code": None,
            "activated_at": canonical_timestamp(version.promoted_at) if version.promoted_at else None,
            "deactivated_at": None,
            "previous_active_model_id": None,
            "retirement_reason": None,
            "can_rollback": False,
            "can_retire": False,
        }

    @staticmethod
    def _artifact_check(run: CalibrationRunModel) -> dict[str, Any] | None:
        if run.artifact_locator is None or run.artifact_sha256 is None:
            return None
        try:
            actual = hashlib.sha256(Path(run.artifact_locator).read_bytes()).hexdigest()
        except OSError:
            return {"expected_sha256": run.artifact_sha256, "actual_sha256": None, "consistent": False}
        return {
            "expected_sha256": run.artifact_sha256,
            "actual_sha256": actual,
            "consistent": actual == run.artifact_sha256,
        }

    @staticmethod
    def _require_read(user: AuthenticatedUser) -> None:
        if user.role not in READ_ROLES:
            raise RegistryForbidden("Current role cannot view the model registry")

    @staticmethod
    def _uuid(value: str | uuid.UUID) -> uuid.UUID:
        try:
            return uuid.UUID(str(value))
        except ValueError as error:
            raise RegistryNotFound(str(value)) from error
