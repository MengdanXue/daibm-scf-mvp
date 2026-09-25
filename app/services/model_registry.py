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
from app.models_identity import OrganizationModel, UserModel
from app.domain.model_registry import ModelVersionStatus
from app.models_model_governance import (
    ModelRegistryEventModel,
    RiskModelVersionModel,
    RiskModelVersionTransitionModel,
    TrainingDatasetSnapshotModel,
)
from app.models_outcome import ActualOutcomeModel, CalibrationRunModel
from app.models_research import ModelVersionModel
from app.repositories.ledger import LedgerRepository
from app.repositories.model_registry import ModelRegistryRepository
from app.repositories.outcomes import OutcomeRepository
from app.services.permissions import PermissionService
from app.services.outcomes import OutcomeConflict, OutcomeNotFound, OutcomeService



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
        outcome_service: OutcomeService | None = None,
        versions: ModelRegistryRepository | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.outcome_service = outcome_service
        self.versions = versions or ModelRegistryRepository()
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
                    select(CalibrationRunModel)
                    .where(self._visible_runs(user))
                    .order_by(
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
            if run is None or not PermissionService.can_access_organization(
                session, user, run.organization_id
            ):
                raise RegistryNotFound(str(model_id))
            runs = list(
                session.scalars(
                    select(CalibrationRunModel)
                    .where(
                        CalibrationRunModel.deployment_scope == run.deployment_scope,
                        CalibrationRunModel.organization_id == run.organization_id,
                    )
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

    # --- Model versions (registry of record) -------------------------------

    def list_versions(
        self, user: AuthenticatedUser, *, scope: str | None = None
    ) -> dict[str, Any]:
        self._require_read(user)
        with self.session_factory() as session:
            versions = self.versions.list_versions(
                session, scope=scope, visibility=self._visible_versions(user)
            )
            snapshots = self._snapshots(session, versions)
            codes = self._organization_codes(session)
            entries = [
                self._version_entry(item, snapshots.get(item.dataset_snapshot_id), codes)
                for item in versions
            ]
            active_by_organization: dict[str, dict[str, Any]] = {}
            for item in entries:
                if item["status"] == "ACTIVE":
                    active_by_organization.setdefault(item["organization_code"], {})[
                        item["scope"]
                    ] = item
            own = codes.get(user.organization_id)
            return {
                "versions": entries,
                # The caller's own organization when it lends, else the first
                # visible one; every visible organization is listed below.
                "active_by_scope": active_by_organization.get(own or "")
                or next(iter(active_by_organization.values()), {}),
                "active_by_organization": active_by_organization,
                "candidates": [item for item in entries if item["status"] == "CANDIDATE"],
                "unregistered_artifacts": [
                    str(run_id) for run_id in self._unregistered_runs(session, scope, user)
                ],
            }

    def get_version(self, version_id: str | uuid.UUID, user: AuthenticatedUser) -> dict[str, Any]:
        self._require_read(user)
        normalized = self._uuid(version_id)
        with self.session_factory() as session:
            version = self.versions.get(session, normalized)
            if version is None or not PermissionService.can_access_organization(
                session, user, version.organization_id
            ):
                raise RegistryNotFound(str(version_id))
            transitions = self.versions.list_transitions(session, version_ids=[version.id])
            labels = {version.id: self._label(version)}
            snapshot = self._snapshots(session, [version]).get(version.dataset_snapshot_id)
            return {
                **self._version_entry(version, snapshot, self._organization_codes(session)),
                "artifact_check": self._hash_check(version.artifact_path, version.artifact_hash),
                "transitions": [self._transition_entry(item, labels) for item in transitions],
            }

    def activation_history(
        self, user: AuthenticatedUser, *, scope: str | None = None
    ) -> list[dict[str, Any]]:
        self._require_read(user)
        with self.session_factory() as session:
            visibility = self._visible_versions(user)
            transitions = self.versions.list_transitions(
                session, scope=scope, activation_only=True, visibility=visibility
            )
            labels = {
                item.id: self._label(item)
                for item in self.versions.list_versions(session, visibility=visibility)
            }
            return [self._transition_entry(item, labels) for item in reversed(transitions)]

    def register_version(self, run_id: str | uuid.UUID, user: AuthenticatedUser) -> dict[str, Any]:
        self._require_auditor(user)
        version_id = self._delegate(lambda service: service.register_version(run_id, user))
        return self.get_version(version_id, user)

    def activate_version(
        self, version_id: str | uuid.UUID, user: AuthenticatedUser, *, reason: str
    ) -> dict[str, Any]:
        self._require_auditor(user)
        activated = self._delegate(
            lambda service: service.promote_version(version_id, user, reason=reason)
        )
        return self.get_version(activated, user)

    def rollback_version(
        self, version_id: str | uuid.UUID, user: AuthenticatedUser, *, reason_code: str
    ) -> dict[str, Any]:
        """Deactivate an ACTIVE version and restore the version it replaced."""

        self._require_auditor(user)
        normalized = self._uuid(version_id)
        with self.session_factory() as session:
            version = self.versions.get(session, normalized)
            if version is None or not PermissionService.can_access_organization(
                session, user, version.organization_id
            ):
                raise RegistryNotFound(str(version_id))
            if version.status != ModelVersionStatus.ACTIVE.value:
                raise RegistryConflict(f"model version is {version.status}, not ACTIVE")
            run_id, scope = version.calibration_run_id, version.scope
            organization_id = version.organization_id
        self._delegate(
            lambda service: service.rollback(
                run_id, user, scope=scope, reason=f"manual_rollback:{reason_code}"
            )
        )
        with self.session_factory() as session:
            restored = self.versions.get_active_version(
                session, scope=scope, organization_id=organization_id
            )
            assert restored is not None
            restored_id = restored.id
        return self.get_version(restored_id, user)

    def _delegate(self, operation: Callable[[OutcomeService], Any]) -> Any:
        if self.outcome_service is None:
            raise RegistryConflict("model promotion is not configured")
        try:
            return operation(self.outcome_service)
        except OutcomeNotFound as error:
            raise RegistryNotFound(str(error)) from error
        except OutcomeConflict as error:
            raise RegistryConflict(str(error)) from error

    @staticmethod
    def _visible_runs(user: AuthenticatedUser):
        return PermissionService.organization_filter(user, CalibrationRunModel.organization_id)

    @staticmethod
    def _visible_versions(user: AuthenticatedUser):
        return PermissionService.organization_filter(user, RiskModelVersionModel.organization_id)

    @staticmethod
    def _organization_codes(session: Session) -> dict[uuid.UUID, str]:
        return {
            organization_id: code
            for organization_id, code in session.execute(
                select(OrganizationModel.organization_id, OrganizationModel.organization_code)
            )
        }

    @classmethod
    def _unregistered_runs(
        cls, session: Session, scope: str | None, user: AuthenticatedUser
    ) -> list[uuid.UUID]:
        statement = (
            select(CalibrationRunModel.calibration_run_id)
            .outerjoin(
                RiskModelVersionModel,
                RiskModelVersionModel.calibration_run_id == CalibrationRunModel.calibration_run_id,
            )
            .where(
                RiskModelVersionModel.id.is_(None),
                CalibrationRunModel.artifact_locator.is_not(None),
                cls._visible_runs(user),
            )
            .order_by(CalibrationRunModel.completed_at)
        )
        if scope is not None:
            statement = statement.where(CalibrationRunModel.deployment_scope == scope)
        return list(session.scalars(statement))

    @staticmethod
    def _label(version: RiskModelVersionModel) -> str:
        return f"{version.model_id}@v{version.version}"

    @staticmethod
    def _snapshots(
        session: Session, versions: list[RiskModelVersionModel]
    ) -> dict[uuid.UUID | None, TrainingDatasetSnapshotModel]:
        ids = {item.dataset_snapshot_id for item in versions if item.dataset_snapshot_id}
        if not ids:
            return {}
        return {
            item.snapshot_id: item
            for item in session.scalars(
                select(TrainingDatasetSnapshotModel).where(
                    TrainingDatasetSnapshotModel.snapshot_id.in_(ids)
                )
            )
        }

    def _version_entry(
        self,
        version: RiskModelVersionModel,
        snapshot: TrainingDatasetSnapshotModel | None = None,
        organization_codes: dict[uuid.UUID, str] | None = None,
    ) -> dict[str, Any]:
        status = version.status
        return {
            "organization_id": str(version.organization_id),
            "organization_code": (organization_codes or {}).get(version.organization_id),
            "dataset_snapshot_id": str(snapshot.snapshot_id) if snapshot else None,
            "dataset_snapshot_hash": snapshot.dataset_hash if snapshot else None,
            "training_outcome_count": snapshot.included_count if snapshot else None,
            "excluded_outcome_count": snapshot.excluded_count if snapshot else None,
            "exclusion_reasons": snapshot.exclusion_summary if snapshot else None,
            "id": str(version.id),
            "model_id": version.model_id,
            "version": version.version,
            "label": self._label(version),
            "model_type": version.model_type,
            "calibration_run_id": str(version.calibration_run_id),
            "artifact_path": version.artifact_path,
            "artifact_hash": version.artifact_hash,
            "scope": version.scope,
            "training_dataset_version": version.training_dataset_version,
            "metrics": version.metrics,
            "evaluation": version.evaluation,
            "evaluation_passed": version.evaluation_passed,
            "evaluated_at": _timestamp(version.evaluated_at),
            "status": status,
            "created_by": version.created_by,
            "created_at": _timestamp(version.created_at),
            "activated_at": _timestamp(version.activated_at),
            "deactivated_at": _timestamp(version.deactivated_at),
            "promotion_reason": version.promotion_reason,
            "previous_active_version_id": (
                str(version.previous_active_version_id)
                if version.previous_active_version_id
                else None
            ),
            "can_activate": status == "CANDIDATE" and bool(version.evaluation_passed),
            "can_rollback": status == "ACTIVE" and version.previous_active_version_id is not None,
        }

    @staticmethod
    def _transition_entry(
        item: RiskModelVersionTransitionModel, labels: dict[uuid.UUID, str]
    ) -> dict[str, Any]:
        return {
            "model_version_id": str(item.model_version_id),
            "version_label": labels.get(item.model_version_id),
            "from_status": item.from_status,
            "to_status": item.to_status,
            "status_sequence": item.status_sequence,
            "reason": item.reason,
            "actor": item.actor_label,
            "actor_user_id": str(item.actor_user_id) if item.actor_user_id else None,
            "evaluation_metrics": item.evaluation_metrics,
            "artifact_hash": item.artifact_hash,
            "recorded_at": canonical_timestamp(item.recorded_at),
        }

    @staticmethod
    def _hash_check(path: str, expected: str) -> dict[str, Any]:
        try:
            actual: str | None = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        except OSError:
            actual = None
        return {"expected_sha256": expected, "actual_sha256": actual, "consistent": actual == expected}

    @staticmethod
    def _require_auditor(user: AuthenticatedUser) -> None:
        if not PermissionService.allowed(user, "model:change"):
            raise RegistryForbidden("Only auditors can change model versions")

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
            if unlocked is None or not PermissionService.can_access_organization(
                session, user, unlocked.organization_id
            ):
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
        counters: dict[tuple[uuid.UUID, str], int] = {}
        for run in sorted(runs, key=lambda item: (item.completed_at, str(item.calibration_run_id))):
            key = (run.organization_id, run.deployment_scope)
            counters[key] = counters.get(key, 0) + 1
            labels[run.calibration_run_id] = f"platt-{run.deployment_scope}-v{counters[key]}"
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
            "organization_id": str(run.organization_id),
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
            # Synthetic reference research model: platform-level, holds no tenant data.
            "organization_id": None,
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
        if not PermissionService.allowed(user, "model:read"):
            raise RegistryForbidden("Current role cannot view the model registry")

    @staticmethod
    def _uuid(value: str | uuid.UUID) -> uuid.UUID:
        try:
            return uuid.UUID(str(value))
        except ValueError as error:
            raise RegistryNotFound(str(value)) from error


def _timestamp(value: datetime | None) -> str | None:
    return canonical_timestamp(value) if value is not None else None
