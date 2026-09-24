"""Risk task center: assigned work items with notes, results and an audit trail."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.domain.risk_operations import ADMIN, TASK_ROLES, TASK_TRANSITIONS, TaskStatus
from app.identity import AuthenticatedUser
from app.models_facility import FinancingFacilityModel
from app.models_identity import UserModel
from app.models_risk_ops import (
    RiskAlertModel,
    RiskTaskAttachmentModel,
    RiskTaskEventModel,
    RiskTaskModel,
)
from app.repositories.ledger import LedgerRepository
from app.services.risk_operations import (
    RiskOpsConflict,
    RiskOpsForbidden,
    RiskOpsNotFound,
    facility_organization,
    org_scope,
    parse_uuid,
    require_role,
    ts,
    usernames,
)

MAX_ATTACHMENT_BYTES = 2 * 1024 * 1024
OPEN_STATES = (TaskStatus.OPEN.value, TaskStatus.IN_PROGRESS.value)
DONE_STATES = (TaskStatus.COMPLETED.value, TaskStatus.CANCELLED.value)


class RiskTaskService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        ledger_repository: LedgerRepository | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.ledger_repository = ledger_repository or LedgerRepository()
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    # --- Queries -----------------------------------------------------------

    def assignees(self, user: AuthenticatedUser) -> list[dict[str, Any]]:
        require_role(user, TASK_ROLES, "Current role cannot use the task center")
        with self.session_factory() as session:
            return [
                {
                    "user_id": str(row.user_id),
                    "username": row.username,
                    "display_name": row.display_name,
                    "role": row.role,
                }
                for row in session.scalars(
                    select(UserModel)
                    .where(UserModel.role.in_(sorted(TASK_ROLES)), UserModel.is_active.is_(True))
                    .order_by(UserModel.username)
                )
            ]

    def list_tasks(
        self, user: AuthenticatedUser, *, view: str = "mine", limit: int = 200
    ) -> list[dict[str, Any]]:
        require_role(user, TASK_ROLES, "Current role cannot use the task center")
        with self.session_factory() as session:
            statement = self._visible(user)
            if view == "mine":
                statement = statement.where(
                    RiskTaskModel.assignee_user_id == user.user_id,
                    RiskTaskModel.status.in_(OPEN_STATES),
                )
            elif view == "pending":
                statement = statement.where(RiskTaskModel.status.in_(OPEN_STATES))
            elif view == "completed":
                statement = statement.where(RiskTaskModel.status.in_(DONE_STATES))
            elif view != "all":
                raise RiskOpsConflict("unknown task view")
            tasks = list(
                session.scalars(
                    statement.order_by(RiskTaskModel.due_at, RiskTaskModel.task_id).limit(limit)
                )
            )
            names = usernames(
                session,
                {item.assignee_user_id for item in tasks} | {item.created_by_user_id for item in tasks},
            )
            now = self.clock()
            return [self._serialize(item, names, now) for item in tasks]

    def get_task(self, task_id: str, user: AuthenticatedUser) -> dict[str, Any]:
        require_role(user, TASK_ROLES, "Current role cannot use the task center")
        with self.session_factory() as session:
            return self._detail(session, self._load(session, task_id, user))

    def attachment(
        self, task_id: str, attachment_id: str, user: AuthenticatedUser
    ) -> tuple[str, str, bytes]:
        require_role(user, TASK_ROLES, "Current role cannot use the task center")
        with self.session_factory() as session:
            task = self._load(session, task_id, user)
            row = session.get(RiskTaskAttachmentModel, parse_uuid(attachment_id))
            if row is None or row.task_id != task.task_id:
                raise RiskOpsNotFound(attachment_id)
            return row.filename, row.content_type, row.content

    # --- Commands ----------------------------------------------------------

    def create(
        self,
        user: AuthenticatedUser,
        *,
        title: str,
        task_type: str,
        description: str,
        assignee_user_id: str,
        due_at: datetime,
        alert_id: str | None = None,
        facility_id: str | None = None,
    ) -> dict[str, Any]:
        require_role(user, TASK_ROLES, "Current role cannot create tasks")
        now = self.clock()
        with self.session_factory.begin() as session:
            assignee = self._assignee(session, assignee_user_id)
            alert = None
            facility_uuid = parse_uuid(facility_id) if facility_id else None
            organization_id = None
            if alert_id:
                alert = session.get(RiskAlertModel, parse_uuid(alert_id))
                scope = org_scope(user)
                if alert is None or (scope is not None and alert.organization_id != scope):
                    raise RiskOpsNotFound(str(alert_id))
                facility_uuid = facility_uuid or alert.facility_id
                organization_id = alert.organization_id
            if facility_uuid is not None:
                if session.get(FinancingFacilityModel, facility_uuid) is None:
                    raise RiskOpsNotFound(str(facility_uuid))
                organization_id = organization_id or facility_organization(session, facility_uuid)
                scope = org_scope(user)
                if scope is not None and organization_id != scope:
                    raise RiskOpsNotFound(str(facility_uuid))
            task = RiskTaskModel(
                task_id=uuid.uuid4(),
                alert_id=alert.alert_id if alert else None,
                facility_id=facility_uuid,
                organization_id=organization_id or user.organization_id,
                title=title,
                task_type=task_type,
                description=description,
                status=TaskStatus.OPEN.value,
                assignee_user_id=assignee.user_id,
                created_by_user_id=user.user_id,
                due_at=due_at,
                version=1,
                created_at=now,
                updated_at=now,
            )
            session.add(task)
            session.flush()
            self._event(
                session,
                task,
                user,
                "CREATED",
                None,
                description,
                {"assignee": assignee.username, "due_at": ts(due_at), "task_type": task_type},
            )
            return self._detail(session, task)

    def reassign(
        self, task_id: str, user: AuthenticatedUser, *, version: int, assignee_user_id: str, comment: str | None
    ) -> dict[str, Any]:
        def apply(session: Session, task: RiskTaskModel) -> tuple[str, dict[str, Any]]:
            self._require_manager(task, user)
            self._require_open(task)
            assignee = self._assignee(session, assignee_user_id)
            previous = task.assignee_user_id
            task.assignee_user_id = assignee.user_id
            return "REASSIGNED", {"assignee": assignee.username, "previous_assignee_user_id": str(previous)}

        return self._change(task_id, user, version, comment, apply)

    def set_due(
        self, task_id: str, user: AuthenticatedUser, *, version: int, due_at: datetime, comment: str | None
    ) -> dict[str, Any]:
        def apply(session: Session, task: RiskTaskModel) -> tuple[str, dict[str, Any]]:
            self._require_manager(task, user)
            self._require_open(task)
            previous = task.due_at
            task.due_at = due_at
            return "DUE_CHANGED", {"due_at": ts(due_at), "previous_due_at": ts(previous)}

        return self._change(task_id, user, version, comment, apply)

    def add_note(self, task_id: str, user: AuthenticatedUser, *, version: int, note: str) -> dict[str, Any]:
        def apply(session: Session, task: RiskTaskModel) -> tuple[str, dict[str, Any]]:
            self._require_participant(task, user)
            return "NOTE_ADDED", {}

        return self._change(task_id, user, version, note, apply)

    def start(self, task_id: str, user: AuthenticatedUser, *, version: int) -> dict[str, Any]:
        def apply(session: Session, task: RiskTaskModel) -> tuple[str, dict[str, Any]]:
            self._require_assignee(task, user)
            self._move(task, TaskStatus.IN_PROGRESS)
            return "STARTED", {}

        return self._change(task_id, user, version, None, apply)

    def attach_result(
        self,
        task_id: str,
        user: AuthenticatedUser,
        *,
        version: int,
        filename: str,
        content_type: str,
        content: bytes,
    ) -> dict[str, Any]:
        if not content or len(content) > MAX_ATTACHMENT_BYTES:
            raise RiskOpsConflict("Result file must be between 1 byte and 2 MiB")

        def apply(session: Session, task: RiskTaskModel) -> tuple[str, dict[str, Any]]:
            self._require_assignee(task, user)
            if task.status != TaskStatus.IN_PROGRESS.value:
                raise RiskOpsConflict("Results are attached while the task is in progress")
            digest = hashlib.sha256(content).hexdigest()
            attachment = RiskTaskAttachmentModel(
                attachment_id=uuid.uuid4(),
                task_id=task.task_id,
                filename=filename[:200] or "result",
                content_type=content_type[:120] or "application/octet-stream",
                size_bytes=len(content),
                sha256=digest,
                content=content,
                uploaded_by_user_id=user.user_id,
                uploaded_at=self.clock(),
            )
            session.add(attachment)
            return "RESULT_ATTACHED", {
                "attachment_id": str(attachment.attachment_id),
                "filename": attachment.filename,
                "sha256": digest,
                "size_bytes": len(content),
            }

        return self._change(task_id, user, version, filename, apply)

    def complete(
        self, task_id: str, user: AuthenticatedUser, *, version: int, result_summary: str
    ) -> dict[str, Any]:
        def apply(session: Session, task: RiskTaskModel) -> tuple[str, dict[str, Any]]:
            self._require_assignee(task, user)
            self._move(task, TaskStatus.COMPLETED)
            task.result_summary = result_summary
            task.completed_at = self.clock()
            return "COMPLETED", {"result_summary": result_summary}

        return self._change(task_id, user, version, result_summary, apply)

    def cancel(self, task_id: str, user: AuthenticatedUser, *, version: int, comment: str) -> dict[str, Any]:
        def apply(session: Session, task: RiskTaskModel) -> tuple[str, dict[str, Any]]:
            self._require_manager(task, user)
            self._move(task, TaskStatus.CANCELLED)
            return "CANCELLED", {}

        return self._change(task_id, user, version, comment, apply)

    # --- helpers -----------------------------------------------------------

    def _change(
        self,
        task_id: str,
        user: AuthenticatedUser,
        version: int,
        comment: str | None,
        apply: Callable[[Session, RiskTaskModel], tuple[str, dict[str, Any]]],
    ) -> dict[str, Any]:
        require_role(user, TASK_ROLES, "Current role cannot use the task center")
        with self.session_factory.begin() as session:
            task = self._load(session, task_id, user, for_update=True)
            if task.version != version:
                raise RiskOpsConflict("Task changed since it was read; refresh and retry")
            before = task.status
            action, payload = apply(session, task)
            task.version += 1
            task.updated_at = self.clock()
            session.flush()
            self._event(session, task, user, action, before, comment, payload)
            return self._detail(session, task)

    def _event(
        self,
        session: Session,
        task: RiskTaskModel,
        user: AuthenticatedUser,
        action: str,
        before: str | None,
        comment: str | None,
        payload: dict[str, Any],
    ) -> None:
        session.add(
            RiskTaskEventModel(
                task_id=task.task_id,
                action=action,
                from_status=before,
                to_status=task.status,
                resulting_version=task.version,
                actor_user_id=user.user_id,
                actor_role=user.role,
                comment=comment,
                payload=payload,
            )
        )
        session.flush()
        self.ledger_repository.append_many(
            session,
            task.task_id,
            [
                (
                    "RISK_TASK_RECORDED",
                    {
                        "task_id": str(task.task_id),
                        "action": action,
                        "from_status": before,
                        "to_status": task.status,
                        "version": task.version,
                        "actor_user_id": str(user.user_id),
                        "actor_role": user.role,
                        "comment": comment,
                        **payload,
                    },
                )
            ],
        )

    @staticmethod
    def _assignee(session: Session, assignee_user_id: str) -> UserModel:
        assignee = session.get(UserModel, parse_uuid(assignee_user_id))
        if assignee is None or assignee.role not in TASK_ROLES or not assignee.is_active:
            raise RiskOpsConflict("Tasks go to risk managers, auditors or administrators")
        return assignee

    @staticmethod
    def _move(task: RiskTaskModel, target: TaskStatus) -> None:
        if (TaskStatus(task.status), target) not in TASK_TRANSITIONS:
            raise RiskOpsConflict(f"Task is {task.status}; cannot move to {target.value}")
        task.status = target.value

    @staticmethod
    def _require_open(task: RiskTaskModel) -> None:
        if task.status not in OPEN_STATES:
            raise RiskOpsConflict("The task is already finished")

    @staticmethod
    def _require_assignee(task: RiskTaskModel, user: AuthenticatedUser) -> None:
        if task.assignee_user_id != user.user_id:
            raise RiskOpsForbidden("Only the assignee can work this task")

    @staticmethod
    def _require_manager(task: RiskTaskModel, user: AuthenticatedUser) -> None:
        if user.role != ADMIN and task.created_by_user_id != user.user_id:
            raise RiskOpsForbidden("Only the task creator or an administrator can change this")

    @staticmethod
    def _require_participant(task: RiskTaskModel, user: AuthenticatedUser) -> None:
        if user.role != ADMIN and user.user_id not in (task.assignee_user_id, task.created_by_user_id):
            raise RiskOpsForbidden("Only task participants can add notes")

    @staticmethod
    def _visible(user: AuthenticatedUser):
        statement = select(RiskTaskModel)
        if user.role == ADMIN:
            return statement
        conditions = [
            RiskTaskModel.assignee_user_id == user.user_id,
            RiskTaskModel.created_by_user_id == user.user_id,
        ]
        scope = org_scope(user)
        if scope is not None:
            conditions.append(RiskTaskModel.organization_id == scope)
        else:
            # Auditors review work across organizations.
            return statement
        return statement.where(or_(*conditions))

    def _load(
        self, session: Session, task_id: str, user: AuthenticatedUser, *, for_update: bool = False
    ) -> RiskTaskModel:
        statement = self._visible(user).where(RiskTaskModel.task_id == parse_uuid(task_id))
        if for_update:
            statement = statement.with_for_update()
        task = session.scalar(statement)
        if task is None:
            raise RiskOpsNotFound(str(task_id))
        return task

    def _detail(self, session: Session, task: RiskTaskModel) -> dict[str, Any]:
        events = list(
            session.scalars(
                select(RiskTaskEventModel)
                .where(RiskTaskEventModel.task_id == task.task_id)
                .order_by(RiskTaskEventModel.event_id)
            )
        )
        attachments = list(
            session.scalars(
                select(RiskTaskAttachmentModel)
                .where(RiskTaskAttachmentModel.task_id == task.task_id)
                .order_by(RiskTaskAttachmentModel.uploaded_at)
            )
        )
        people: set[uuid.UUID | None] = {task.assignee_user_id, task.created_by_user_id}
        names = usernames(session, people | {e.actor_user_id for e in events})
        return {
            **self._serialize(task, names, self.clock()),
            "description": task.description,
            "events": [
                {
                    "action": event.action,
                    "from_status": event.from_status,
                    "to_status": event.to_status,
                    "version": event.resulting_version,
                    "actor": names.get(event.actor_user_id) if event.actor_user_id else "system",
                    "actor_role": event.actor_role,
                    "comment": event.comment,
                    "payload": event.payload,
                    "recorded_at": ts(event.recorded_at),
                }
                for event in events
            ],
            "attachments": [
                {
                    "attachment_id": str(item.attachment_id),
                    "filename": item.filename,
                    "content_type": item.content_type,
                    "size_bytes": item.size_bytes,
                    "sha256": item.sha256,
                    "uploaded_at": ts(item.uploaded_at),
                }
                for item in attachments
            ],
        }

    @staticmethod
    def _serialize(task: RiskTaskModel, names: dict[uuid.UUID, str], now: datetime) -> dict[str, Any]:
        return {
            "task_id": str(task.task_id),
            "alert_id": str(task.alert_id) if task.alert_id else None,
            "facility_id": str(task.facility_id) if task.facility_id else None,
            "title": task.title,
            "task_type": task.task_type,
            "status": task.status,
            "assignee_user_id": str(task.assignee_user_id),
            "assignee": names.get(task.assignee_user_id),
            "created_by": names.get(task.created_by_user_id),
            "due_at": ts(task.due_at),
            "overdue": task.status in OPEN_STATES and task.due_at < now,
            "result_summary": task.result_summary,
            "version": task.version,
            "created_at": ts(task.created_at),
            "completed_at": ts(task.completed_at),
        }


__all__ = ["RiskTaskService"]
