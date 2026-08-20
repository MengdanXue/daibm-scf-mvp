from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.domain.evidence import evidence_sha256, trade_evidence
from app.domain.workflow import (
    Action,
    InvalidTransition,
    Role,
    Status,
    allowed_actions,
    next_status,
)
from app.identity import AuthenticatedUser
from app.ledger import canonical_timestamp
from app.models import FinancingRequestModel
from app.models_workflow import WorkflowActionModel
from app.repositories.identity import IdentityRepository
from app.repositories.ledger import LedgerRepository
from app.repositories.workflow import WorkflowRepository
from app.risk import assess
from app.schemas import FinancingRequestCreate
from app.schemas_workflow import ApplicationDraftCreate


class ApplicationNotFound(Exception):
    pass


class ForbiddenWorkflow(Exception):
    pass


class StaleApplication(Exception):
    pass


class DuplicateInvoiceClaim(Exception):
    pass


DECISION_CONTROLS = {
    "approved": "standard_monitoring",
    "manual_review": "request_documents_and_enhanced_validation",
    "rejected": "suspend_auto_approval_and_enhanced_validation",
}


class WorkflowService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        workflow_repository: WorkflowRepository | None = None,
        identity_repository: IdentityRepository | None = None,
        ledger_repository: LedgerRepository | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.workflow_repository = workflow_repository or WorkflowRepository()
        self.identity_repository = identity_repository or IdentityRepository()
        self.ledger_repository = ledger_repository or LedgerRepository()

    def create_draft(
        self,
        payload: ApplicationDraftCreate,
        user: AuthenticatedUser,
    ) -> dict[str, Any]:
        self._require_role(user, Role.SUPPLIER)
        now = datetime.now(timezone.utc)
        risk_request = payload.to_risk_request(user.organization_code)
        try:
            with self.session_factory.begin() as session:
                core_organization = self.identity_repository.get_organization_by_code(
                    session,
                    payload.core_enterprise_organization_code,
                )
                if (
                    core_organization is None
                    or core_organization.organization_type != "core_enterprise"
                ):
                    raise ApplicationNotFound("Core enterprise not found")
                amount = Decimal(str(payload.amount)).quantize(Decimal("0.01"))
                fingerprint, invoice_claim = trade_evidence(
                    supplier_code=user.organization_code,
                    core_enterprise_code=core_organization.organization_code,
                    contract_number=payload.contract_number,
                    invoice_number=payload.invoice_number,
                    amount=amount,
                    term_days=payload.term_days,
                )
                if self.workflow_repository.get_by_invoice_claim(
                    session, invoice_claim
                ) is not None:
                    raise DuplicateInvoiceClaim(invoice_claim)
                application = FinancingRequestModel(
                    request_id=uuid.uuid4(),
                    created_at=now,
                    updated_at=now,
                    applicant_id=user.organization_code,
                    amount=amount,
                    term_days=payload.term_days,
                    features=risk_request.model_dump(),
                    risk_score=None,
                    decision=None,
                    explanations=None,
                    control_action=None,
                    status=Status.DRAFT.value,
                    version=1,
                    created_by_user_id=user.user_id,
                    supplier_organization_id=user.organization_id,
                    core_enterprise_organization_id=(
                        core_organization.organization_id
                    ),
                    contract_number=payload.contract_number,
                    invoice_number=payload.invoice_number,
                    trade_evidence_sha256=fingerprint,
                    invoice_claim_sha256=invoice_claim,
                )
                self.workflow_repository.add_application(session, application)
                self._record(
                    session,
                    application,
                    user,
                    action_type="create_draft",
                    event_type="APPLICATION_DRAFT_CREATED",
                    from_status=None,
                    to_status=Status.DRAFT,
                    comment=None,
                    payload={
                        "contract_number": payload.contract_number,
                        "invoice_number": payload.invoice_number,
                        "amount": payload.amount,
                        "trade_evidence_sha256": fingerprint,
                        "invoice_claim_sha256": invoice_claim,
                    },
                )
        except IntegrityError as error:
            if self._is_duplicate_invoice(error):
                raise DuplicateInvoiceClaim("Duplicate invoice claim") from error
            raise
        return self.get(application.request_id, user)

    def update_draft(
        self,
        request_id: str | uuid.UUID,
        version: int,
        payload: ApplicationDraftCreate,
        user: AuthenticatedUser,
    ) -> dict[str, Any]:
        self._require_role(user, Role.SUPPLIER)
        normalized_id = self._normalize_id(request_id)
        try:
            with self.session_factory.begin() as session:
                application = self._load_for_action(session, normalized_id, user)
                self._check_version(application, version)
                current = Status(application.status)
                target = next_status(current, Action.UPDATE, Role(user.role))
                core_organization = self.identity_repository.get_organization_by_code(
                    session,
                    payload.core_enterprise_organization_code,
                )
                if (
                    core_organization is None
                    or core_organization.organization_type != "core_enterprise"
                ):
                    raise ApplicationNotFound("Core enterprise not found")
                risk_request = payload.to_risk_request(user.organization_code)
                amount = Decimal(str(payload.amount)).quantize(Decimal("0.01"))
                fingerprint, invoice_claim = trade_evidence(
                    supplier_code=user.organization_code,
                    core_enterprise_code=core_organization.organization_code,
                    contract_number=payload.contract_number,
                    invoice_number=payload.invoice_number,
                    amount=amount,
                    term_days=payload.term_days,
                )
                if self.workflow_repository.get_by_invoice_claim(
                    session,
                    invoice_claim,
                    excluding_request_id=application.request_id,
                ) is not None:
                    raise DuplicateInvoiceClaim(invoice_claim)
                application.amount = amount
                application.term_days = payload.term_days
                application.features = risk_request.model_dump()
                application.core_enterprise_organization_id = (
                    core_organization.organization_id
                )
                application.contract_number = payload.contract_number
                application.invoice_number = payload.invoice_number
                application.trade_evidence_sha256 = fingerprint
                application.invoice_claim_sha256 = invoice_claim
                self._advance(
                    session,
                    application,
                    user,
                    action_type="update",
                    event_type="APPLICATION_UPDATED",
                    from_status=current,
                    to_status=target,
                    comment=None,
                    payload={
                        "contract_number": payload.contract_number,
                        "invoice_number": payload.invoice_number,
                        "trade_evidence_sha256": fingerprint,
                        "invoice_claim_sha256": invoice_claim,
                    },
                )
        except IntegrityError as error:
            if self._is_duplicate_invoice(error):
                raise DuplicateInvoiceClaim("Duplicate invoice claim") from error
            raise
        return self.get(normalized_id, user)

    def submit(
        self,
        request_id: str | uuid.UUID,
        version: int,
        user: AuthenticatedUser,
    ) -> dict[str, Any]:
        return self._simple_transition(
            request_id,
            version,
            user,
            required_role=Role.SUPPLIER,
            action=Action.SUBMIT,
            event_type="APPLICATION_SUBMITTED",
        )

    def confirm_trade(
        self,
        request_id: str | uuid.UUID,
        version: int,
        *,
        confirmed: bool,
        comment: str,
        user: AuthenticatedUser,
    ) -> dict[str, Any]:
        return self._simple_transition(
            request_id,
            version,
            user,
            required_role=Role.CORE_ENTERPRISE,
            action=Action.CONFIRM_TRADE if confirmed else Action.RETURN_TRADE,
            event_type="TRADE_CONFIRMED" if confirmed else "TRADE_RETURNED",
            comment=comment,
        )

    def assess_risk(
        self,
        request_id: str | uuid.UUID,
        version: int,
        user: AuthenticatedUser,
    ) -> dict[str, Any]:
        self._require_role(user, Role.FINANCIER)
        normalized_id = self._normalize_id(request_id)
        with self.session_factory.begin() as session:
            application = self._load_for_action(session, normalized_id, user)
            self._check_version(application, version)
            current = Status(application.status)
            target = next_status(current, Action.ASSESS_RISK, Role(user.role))
            risk_result = assess(
                FinancingRequestCreate.model_validate(application.features)
            )
            application.risk_score = risk_result.score
            application.explanations = risk_result.contributions
            application.risk_assessment_id = uuid.uuid4()
            application.risk_engine_version = "transparent_logistic_baseline_v0.1"
            application.risk_input_sha256 = evidence_sha256(
                application.features
            )
            application.risk_assessed_at = datetime.now(timezone.utc)
            self._advance(
                session,
                application,
                user,
                action_type=Action.ASSESS_RISK.value,
                event_type="RISK_ASSESSMENT",
                from_status=current,
                to_status=target,
                comment=None,
                payload={
                    "score": risk_result.score,
                    "band": risk_result.band,
                    "model": "transparent_logistic_baseline_v0.1",
                    "assessment_id": str(application.risk_assessment_id),
                    "input_sha256": application.risk_input_sha256,
                    "provenance": "DEMO_WORKFLOW",
                },
            )
        return self.get(normalized_id, user)

    def decide(
        self,
        request_id: str | uuid.UUID,
        version: int,
        *,
        decision: str,
        comment: str,
        user: AuthenticatedUser,
    ) -> dict[str, Any]:
        self._require_role(user, Role.FINANCIER)
        normalized_id = self._normalize_id(request_id)
        with self.session_factory.begin() as session:
            application = self._load_for_action(session, normalized_id, user)
            self._check_version(application, version)
            current = Status(application.status)
            target = next_status(
                current,
                Action.DECIDE,
                Role(user.role),
                decision=decision,
            )
            application.decision = target.value
            application.control_action = DECISION_CONTROLS[target.value]
            self._advance(
                session,
                application,
                user,
                action_type=Action.DECIDE.value,
                event_type="FINANCING_DECISION",
                from_status=current,
                to_status=target,
                comment=comment,
                payload={
                    "decision": target.value,
                    "risk_score": application.risk_score,
                },
            )
        return self.get(normalized_id, user)

    def apply_control(
        self,
        request_id: str | uuid.UUID,
        version: int,
        *,
        comment: str,
        user: AuthenticatedUser,
    ) -> dict[str, Any]:
        return self._simple_transition(
            request_id,
            version,
            user,
            required_role=Role.RISK_MANAGER,
            action=Action.APPLY_CONTROL,
            event_type="CONTROL_ACTION",
            comment=comment,
        )

    def audit(
        self,
        request_id: str | uuid.UUID,
        version: int,
        *,
        comment: str,
        user: AuthenticatedUser,
    ) -> dict[str, Any]:
        self._require_role(user, Role.AUDITOR)
        normalized_id = self._normalize_id(request_id)
        with self.session_factory.begin() as session:
            application = self._load_for_action(session, normalized_id, user)
            self._check_version(application, version)
            verification = self.ledger_repository.verify(session)
            if not verification["valid"]:
                raise InvalidTransition("Audit ledger integrity failed")
            current = Status(application.status)
            target = next_status(current, Action.AUDIT, Role(user.role))
            self._advance(
                session,
                application,
                user,
                action_type=Action.AUDIT.value,
                event_type="AUDIT_REVIEW_COMPLETED",
                from_status=current,
                to_status=target,
                comment=comment,
                payload={"ledger_head_hash": verification["head_hash"]},
            )
        return self.get(normalized_id, user)

    def get(
        self,
        request_id: str | uuid.UUID,
        user: AuthenticatedUser,
    ) -> dict[str, Any]:
        normalized_id = self._normalize_id(request_id)
        with self.session_factory() as session:
            application = self.workflow_repository.get_application(
                session, normalized_id
            )
            if application is None or not self._can_view(application, user):
                raise ApplicationNotFound(str(normalized_id))
            return self._serialize(session, application, user)

    def list_for_user(
        self,
        user: AuthenticatedUser,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            applications = self.workflow_repository.list_for_user(
                session, user, limit=limit, offset=offset
            )
            return [self._serialize(session, item, user) for item in applications]

    def tasks_for_user(self, user: AuthenticatedUser) -> list[dict[str, Any]]:
        return [
            application
            for application in self.list_for_user(user, limit=200)
            if application["allowed_actions"]
        ]

    def _simple_transition(
        self,
        request_id: str | uuid.UUID,
        version: int,
        user: AuthenticatedUser,
        *,
        required_role: Role,
        action: Action,
        event_type: str,
        comment: str | None = None,
    ) -> dict[str, Any]:
        self._require_role(user, required_role)
        normalized_id = self._normalize_id(request_id)
        with self.session_factory.begin() as session:
            application = self._load_for_action(session, normalized_id, user)
            self._check_version(application, version)
            current = Status(application.status)
            target = next_status(current, action, Role(user.role))
            self._advance(
                session,
                application,
                user,
                action_type=action.value,
                event_type=event_type,
                from_status=current,
                to_status=target,
                comment=comment,
                payload={
                    "control_action": application.control_action
                    if action == Action.APPLY_CONTROL
                    else None
                },
            )
        return self.get(normalized_id, user)

    def _load_for_action(
        self,
        session: Session,
        request_id: uuid.UUID,
        user: AuthenticatedUser,
    ) -> FinancingRequestModel:
        application = self.workflow_repository.get_application(
            session, request_id, for_update=True
        )
        if application is None or not self._can_view(application, user):
            raise ApplicationNotFound(str(request_id))
        return application

    @staticmethod
    def _require_role(user: AuthenticatedUser, role: Role) -> None:
        if user.role != role.value:
            raise ForbiddenWorkflow(f"{user.role} cannot perform this action")

    @staticmethod
    def _check_version(application: FinancingRequestModel, version: int) -> None:
        if application.version != version:
            raise StaleApplication(
                f"Expected version {version}, current version {application.version}"
            )

    @staticmethod
    def _normalize_id(request_id: str | uuid.UUID) -> uuid.UUID:
        try:
            return uuid.UUID(str(request_id))
        except ValueError as error:
            raise ApplicationNotFound(str(request_id)) from error

    @staticmethod
    def _can_view(
        application: FinancingRequestModel,
        user: AuthenticatedUser,
    ) -> bool:
        role = Role(user.role)
        if role == Role.SUPPLIER:
            return application.supplier_organization_id == user.organization_id
        if role == Role.CORE_ENTERPRISE:
            return (
                application.core_enterprise_organization_id
                == user.organization_id
            )
        if role == Role.FINANCIER:
            return application.status not in {
                Status.DRAFT.value,
                Status.SUBMITTED.value,
                Status.TRADE_RETURNED.value,
            }
        if role == Role.RISK_MANAGER:
            return application.status in {
                Status.APPROVED.value,
                Status.MANUAL_REVIEW.value,
                Status.REJECTED.value,
                Status.CONTROLLED.value,
                Status.AUDITED.value,
            }
        return role == Role.AUDITOR

    def _advance(
        self,
        session: Session,
        application: FinancingRequestModel,
        user: AuthenticatedUser,
        *,
        action_type: str,
        event_type: str,
        from_status: Status,
        to_status: Status,
        comment: str | None,
        payload: dict[str, Any],
    ) -> None:
        application.status = to_status.value
        application.version += 1
        application.updated_at = datetime.now(timezone.utc)
        self._record(
            session,
            application,
            user,
            action_type=action_type,
            event_type=event_type,
            from_status=from_status,
            to_status=to_status,
            comment=comment,
            payload=payload,
        )

    def _record(
        self,
        session: Session,
        application: FinancingRequestModel,
        user: AuthenticatedUser,
        *,
        action_type: str,
        event_type: str,
        from_status: Status | None,
        to_status: Status,
        comment: str | None,
        payload: dict[str, Any],
    ) -> None:
        timestamp = datetime.now(timezone.utc)
        safe_payload = {key: value for key, value in payload.items() if value is not None}
        session.add(
            WorkflowActionModel(
                request_id=application.request_id,
                actor_user_id=user.user_id,
                actor_role=user.role,
                action_type=action_type,
                from_status=from_status.value if from_status else None,
                to_status=to_status.value,
                comment=comment.strip() if comment else None,
                payload=safe_payload,
                created_at=timestamp,
            )
        )
        ledger_payload = {
            "actor_user_id": str(user.user_id),
            "actor_role": user.role,
            "from_status": from_status.value if from_status else None,
            "to_status": to_status.value,
            "application_version": application.version,
            **safe_payload,
        }
        self.ledger_repository.append_many(
            session,
            application.request_id,
            [(event_type, ledger_payload)],
        )

    def _serialize(
        self,
        session: Session,
        application: FinancingRequestModel,
        user: AuthenticatedUser,
    ) -> dict[str, Any]:
        timeline = [
            {
                "action_id": action.action_id,
                "action_type": action.action_type,
                "actor_user_id": str(action.actor_user_id),
                "actor_display_name": actor.display_name,
                "actor_role": action.actor_role,
                "organization_code": organization.organization_code,
                "from_status": action.from_status,
                "to_status": action.to_status,
                "comment": action.comment,
                "payload": action.payload,
                "created_at": canonical_timestamp(action.created_at),
            }
            for action, actor, organization in self.workflow_repository.list_timeline(
                session, application.request_id
            )
        ]
        actions = allowed_actions(Status(application.status), Role(user.role))
        return {
            "request_id": str(application.request_id),
            "created_at": canonical_timestamp(application.created_at),
            "updated_at": canonical_timestamp(application.updated_at),
            "applicant_id": application.applicant_id,
            "amount": float(application.amount),
            "term_days": application.term_days,
            "features": application.features,
            "risk_score": application.risk_score,
            "decision": application.decision,
            "explanations": application.explanations,
            "control_action": application.control_action,
            "status": application.status,
            "version": application.version,
            "supplier_organization_id": str(application.supplier_organization_id),
            "core_enterprise_organization_id": str(
                application.core_enterprise_organization_id
            ),
            "contract_number": application.contract_number,
            "invoice_number": application.invoice_number,
            "trade_evidence": (
                {
                    "fingerprint_sha256": application.trade_evidence_sha256,
                    "invoice_claim_sha256": application.invoice_claim_sha256,
                    "duplicate_check": "passed",
                    "evidence_type": "declared_fields_fingerprint",
                }
                if application.trade_evidence_sha256
                and application.invoice_claim_sha256
                else None
            ),
            "risk_evidence": (
                {
                    "assessment_id": str(application.risk_assessment_id),
                    "engine_type": "business_baseline",
                    "engine_version": application.risk_engine_version,
                    "input_sha256": application.risk_input_sha256,
                    "assessed_at": canonical_timestamp(application.risk_assessed_at),
                    "provenance": "DEMO_WORKFLOW",
                }
                if application.risk_assessment_id
                and application.risk_engine_version
                and application.risk_input_sha256
                and application.risk_assessed_at
                else None
            ),
            "allowed_actions": [action.value for action in actions],
            "timeline": timeline,
        }

    @staticmethod
    def _is_duplicate_invoice(error: IntegrityError) -> bool:
        original = getattr(error, "orig", None)
        diagnostics = getattr(original, "diag", None)
        return (
            getattr(diagnostics, "constraint_name", None)
            == "uq_financing_requests_invoice_claim_sha256"
        )
