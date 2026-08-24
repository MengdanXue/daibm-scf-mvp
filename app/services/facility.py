from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.domain.facility import (
    FacilityAction,
    FacilityStatus,
    InstallmentStatus,
    InvalidFacilityTransition,
    PaymentStatus,
    next_facility_status,
)
from app.domain.workflow import Role
from app.identity import AuthenticatedUser
from app.ledger import canonical_timestamp
from app.models import FinancingRequestModel
from app.models_facility import (
    FacilityActionModel,
    FinancingFacilityModel,
    InstallmentModel,
    PaymentModel,
)
from app.models_identity import UserModel
from app.repositories.facility import FacilityRepository
from app.repositories.ledger import LedgerRepository
from app.repositories.workflow import WorkflowRepository
from app.schemas_facility import (
    CreateFacilityRequest,
    DecisionPaymentRequest,
    SubmitPaymentRequest,
    VersionedFacilityCommand,
)


class FacilityError(Exception):
    """Base error for a financing-facility use case."""


class FacilityNotFound(FacilityError):
    """The facility does not exist or is outside the actor's organization."""


class ForbiddenFacility(FacilityError):
    """The actor's role cannot perform the requested command."""


class FacilityConflict(FacilityError):
    """The command conflicts with aggregate state or version."""


class FacilityService:
    """Orchestrate versioned facility commands in PostgreSQL transactions."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        repository: FacilityRepository | None = None,
        workflow_repository: WorkflowRepository | None = None,
        ledger_repository: LedgerRepository | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.repository = repository or FacilityRepository()
        self.workflow_repository = workflow_repository or WorkflowRepository()
        self.ledger_repository = ledger_repository or LedgerRepository()
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def create(
        self,
        request: CreateFacilityRequest,
        user: AuthenticatedUser,
    ) -> dict[str, Any]:
        with self.session_factory.begin() as session:
            replay = self._replay(session, request.idempotency_key, user)
            if replay is not None:
                return replay
            self._require_role(user, Role.FINANCIER)
            if request.version != 1:
                raise FacilityConflict("A new facility must start at version 1")

            application = self.workflow_repository.get_application(
                session,
                request.request_id,
                for_update=True,
            )
            if application is None:
                raise FacilityNotFound(str(request.request_id))
            if application.status != "audited" or application.decision != "approved":
                raise FacilityConflict(
                    "Facility creation requires an approved audited application"
                )
            if Decimal(application.amount) != request.principal:
                raise FacilityConflict(
                    "Facility principal must equal the approved application amount"
                )
            if self.repository.get_by_request(session, request.request_id) is not None:
                raise FacilityConflict("The application already has a facility")

            now = self._now()
            facility = FinancingFacilityModel(
                facility_id=uuid.uuid4(),
                request_id=request.request_id,
                principal=request.principal,
                outstanding_amount=request.principal,
                currency=request.currency,
                status=FacilityStatus.READY.value,
                version=1,
                created_by_user_id=user.user_id,
                created_at=now,
                updated_at=now,
            )
            self.repository.add(session, facility)
            session.add_all(
                [
                    InstallmentModel(
                        installment_id=uuid.uuid4(),
                        facility_id=facility.facility_id,
                        sequence=item.sequence,
                        due_date=item.due_date,
                        amount=item.amount,
                        paid_amount=Decimal("0.00"),
                        status=InstallmentStatus.SCHEDULED.value,
                        created_at=now,
                        updated_at=now,
                    )
                    for item in request.installments
                ]
            )
            self._record(
                session,
                facility,
                user,
                action=FacilityAction.CREATE,
                idempotency_key=request.idempotency_key,
                expected_version=1,
                event_types=[
                    (
                        "FACILITY_CREATED",
                        {
                            "request_id": str(facility.request_id),
                            "principal": self._money(facility.principal),
                            "currency": facility.currency,
                            "installment_count": len(request.installments),
                        },
                    )
                ],
            )
            return self._serialize(session, facility, user)

    def initiate_disbursement(
        self,
        facility_id: str | uuid.UUID,
        command: VersionedFacilityCommand,
        user: AuthenticatedUser,
    ) -> dict[str, Any]:
        with self.session_factory.begin() as session:
            replay = self._replay(session, command.idempotency_key, user)
            if replay is not None:
                return replay
            self._require_role(user, Role.FINANCIER)
            facility = self._load_for_command(session, facility_id, user)
            replay = self._replay(session, command.idempotency_key, user)
            if replay is not None:
                return replay
            self._check_version(facility, command.version)
            target = self._next(
                facility,
                FacilityAction.INITIATE_DISBURSEMENT,
                user,
            )
            now = self._now()
            reference = f"SIM-{facility.facility_id}"
            facility.disbursement_reference = reference
            facility.disbursement_evidence_sha256 = hashlib.sha256(
                reference.encode("utf-8")
            ).hexdigest()
            facility.disbursement_initiated_at = now
            self._advance(facility, target, now)
            self._record(
                session,
                facility,
                user,
                action=FacilityAction.INITIATE_DISBURSEMENT,
                idempotency_key=command.idempotency_key,
                expected_version=command.version,
                event_types=[
                    (
                        "DISBURSEMENT_INITIATED",
                        {
                            "disbursement_reference": reference,
                            "evidence_sha256": facility.disbursement_evidence_sha256,
                        },
                    )
                ],
            )
            return self._serialize(session, facility, user)

    def confirm_disbursement(
        self,
        facility_id: str | uuid.UUID,
        command: VersionedFacilityCommand,
        user: AuthenticatedUser,
    ) -> dict[str, Any]:
        with self.session_factory.begin() as session:
            replay = self._replay(session, command.idempotency_key, user)
            if replay is not None:
                return replay
            self._require_role(user, Role.FINANCIER)
            facility = self._load_for_command(session, facility_id, user)
            replay = self._replay(session, command.idempotency_key, user)
            if replay is not None:
                return replay
            self._check_version(facility, command.version)
            target = self._next(
                facility,
                FacilityAction.CONFIRM_DISBURSEMENT,
                user,
            )
            now = self._now()
            facility.disbursed_at = now
            self._advance(facility, target, now)
            self._record(
                session,
                facility,
                user,
                action=FacilityAction.CONFIRM_DISBURSEMENT,
                idempotency_key=command.idempotency_key,
                expected_version=command.version,
                event_types=[
                    (
                        "DISBURSEMENT_CONFIRMED",
                        {
                            "principal": self._money(facility.principal),
                            "currency": facility.currency,
                        },
                    )
                ],
            )
            return self._serialize(session, facility, user)

    def submit_payment(
        self,
        facility_id: str | uuid.UUID,
        request: SubmitPaymentRequest,
        user: AuthenticatedUser,
    ) -> dict[str, Any]:
        with self.session_factory.begin() as session:
            replay = self._replay(session, request.idempotency_key, user)
            if replay is not None:
                return replay
            self._require_role(user, Role.SUPPLIER)
            facility = self._load_for_command(session, facility_id, user)
            replay = self._replay(session, request.idempotency_key, user)
            if replay is not None:
                return replay
            self._check_version(facility, request.version)
            self._next(facility, FacilityAction.SUBMIT_PAYMENT, user)
            installment = self._load_installment(
                session,
                facility.facility_id,
                request.installment_id,
            )
            remaining = Decimal(installment.amount) - Decimal(installment.paid_amount)
            if installment.status == InstallmentStatus.PAID.value:
                raise FacilityConflict("The installment is already paid")
            if (
                request.amount > remaining
                or request.amount > facility.outstanding_amount
            ):
                raise FacilityConflict("Payment exceeds the exact outstanding balance")
            duplicate_reference = session.scalar(
                select(PaymentModel.payment_id).where(
                    PaymentModel.facility_id == facility.facility_id,
                    PaymentModel.payment_reference == request.payment_reference,
                )
            )
            if duplicate_reference is not None:
                raise FacilityConflict("Payment reference already exists")

            now = self._now()
            payment = PaymentModel(
                payment_id=uuid.uuid4(),
                facility_id=facility.facility_id,
                installment_id=installment.installment_id,
                submitted_by_user_id=user.user_id,
                amount=request.amount,
                payment_reference=request.payment_reference,
                status=PaymentStatus.SUBMITTED.value,
                submitted_at=now,
            )
            session.add(payment)
            facility.version += 1
            facility.updated_at = now
            self._record(
                session,
                facility,
                user,
                action=FacilityAction.SUBMIT_PAYMENT,
                idempotency_key=request.idempotency_key,
                expected_version=request.version,
                event_types=[
                    (
                        "REPAYMENT_SUBMITTED",
                        {
                            "payment_id": str(payment.payment_id),
                            "installment_id": str(installment.installment_id),
                            "amount": self._money(payment.amount),
                            "payment_reference": payment.payment_reference,
                        },
                    )
                ],
            )
            return self._serialize(session, facility, user)

    def decide_payment(
        self,
        facility_id: str | uuid.UUID,
        payment_id: str | uuid.UUID,
        request: DecisionPaymentRequest,
        user: AuthenticatedUser,
    ) -> dict[str, Any]:
        with self.session_factory.begin() as session:
            replay = self._replay(session, request.idempotency_key, user)
            if replay is not None:
                return replay
            self._require_role(user, Role.FINANCIER)
            normalized_payment_id = self._normalize_uuid(payment_id)
            facility = self._load_for_command(session, facility_id, user)
            replay = self._replay(session, request.idempotency_key, user)
            if replay is not None:
                return replay
            self._check_version(facility, request.version)
            payment = session.scalar(
                select(PaymentModel).where(
                    PaymentModel.payment_id == normalized_payment_id,
                    PaymentModel.facility_id == facility.facility_id,
                )
            )
            if payment is None:
                raise FacilityNotFound(str(payment_id))
            if payment.status != PaymentStatus.SUBMITTED.value:
                raise FacilityConflict("The payment has already been decided")
            installment = self._load_installment(
                session,
                facility.facility_id,
                payment.installment_id,
            )

            now = self._now()
            payment.decided_at = now
            payment.decided_by_user_id = user.user_id
            payment.decision_comment = request.comment
            if request.decision == PaymentStatus.REJECTED.value:
                target = self._next(
                    facility,
                    FacilityAction.REJECT_PAYMENT,
                    user,
                )
                payment.status = PaymentStatus.REJECTED.value
                self._advance(facility, target, now)
                action = FacilityAction.REJECT_PAYMENT
                events = [
                    (
                        "REPAYMENT_REJECTED",
                        {
                            "payment_id": str(payment.payment_id),
                            "comment": request.comment,
                        },
                    )
                ]
            else:
                installment_remaining = Decimal(installment.amount) - Decimal(
                    installment.paid_amount
                )
                amount = Decimal(payment.amount)
                if (
                    amount > installment_remaining
                    or amount > facility.outstanding_amount
                ):
                    raise FacilityConflict(
                        "Confirmed payment would exceed the exact outstanding balance"
                    )
                new_installment_paid = Decimal(installment.paid_amount) + amount
                new_outstanding = Decimal(facility.outstanding_amount) - amount
                is_final = new_outstanding == Decimal("0.00")
                action = (
                    FacilityAction.CONFIRM_FINAL_PAYMENT
                    if is_final
                    else FacilityAction.CONFIRM_PAYMENT
                )
                target = self._next(facility, action, user)
                payment.status = PaymentStatus.CONFIRMED.value
                installment.paid_amount = new_installment_paid
                installment.status = (
                    InstallmentStatus.PAID.value
                    if new_installment_paid == Decimal(installment.amount)
                    else InstallmentStatus.PARTIALLY_PAID.value
                )
                installment.updated_at = now
                facility.outstanding_amount = new_outstanding
                if is_final:
                    facility.repaid_at = now
                self._advance(facility, target, now)
                confirmation_payload = {
                    "payment_id": str(payment.payment_id),
                    "installment_id": str(installment.installment_id),
                    "amount": self._money(amount),
                    "outstanding_amount": self._money(new_outstanding),
                }
                events = [("REPAYMENT_CONFIRMED", confirmation_payload)]
                if is_final:
                    events.append(
                        (
                            "FACILITY_REPAID",
                            {
                                "outstanding_amount": "0.00",
                                "final_payment_id": str(payment.payment_id),
                            },
                        )
                    )

            self._record(
                session,
                facility,
                user,
                action=action,
                idempotency_key=request.idempotency_key,
                expected_version=request.version,
                event_types=events,
            )
            return self._serialize(session, facility, user)

    def mark_overdue(
        self,
        facility_id: str | uuid.UUID,
        installment_id: str | uuid.UUID,
        command: VersionedFacilityCommand,
        user: AuthenticatedUser,
    ) -> dict[str, Any]:
        with self.session_factory.begin() as session:
            replay = self._replay(session, command.idempotency_key, user)
            if replay is not None:
                return replay
            self._require_role(user, Role.RISK_MANAGER)
            normalized_installment_id = self._normalize_uuid(installment_id)
            facility = self._load_for_command(session, facility_id, user)
            replay = self._replay(session, command.idempotency_key, user)
            if replay is not None:
                return replay
            self._check_version(facility, command.version)
            installment = self._load_installment(
                session,
                facility.facility_id,
                normalized_installment_id,
            )
            now = self._now()
            if installment.status == InstallmentStatus.PAID.value:
                raise FacilityConflict("A paid installment cannot be overdue")
            if installment.due_date >= now.date():
                raise FacilityConflict("The installment is not past due")
            target = self._next(facility, FacilityAction.MARK_OVERDUE, user)
            installment.status = InstallmentStatus.OVERDUE.value
            installment.updated_at = now
            self._advance(facility, target, now)
            self._record(
                session,
                facility,
                user,
                action=FacilityAction.MARK_OVERDUE,
                idempotency_key=command.idempotency_key,
                expected_version=command.version,
                event_types=[
                    (
                        "FACILITY_MARKED_OVERDUE",
                        {
                            "installment_id": str(installment.installment_id),
                            "due_date": installment.due_date.isoformat(),
                        },
                    )
                ],
            )
            return self._serialize(session, facility, user)

    def close(
        self,
        facility_id: str | uuid.UUID,
        command: VersionedFacilityCommand,
        user: AuthenticatedUser,
    ) -> dict[str, Any]:
        with self.session_factory.begin() as session:
            replay = self._replay(session, command.idempotency_key, user)
            if replay is not None:
                return replay
            self._require_role(user, Role.AUDITOR)
            facility = self._load_for_command(session, facility_id, user)
            replay = self._replay(session, command.idempotency_key, user)
            if replay is not None:
                return replay
            self._check_version(facility, command.version)
            if Decimal(facility.outstanding_amount) != Decimal("0.00"):
                raise FacilityConflict("Only a fully repaid facility can be closed")
            verification = self.ledger_repository.verify(session)
            if not verification["valid"]:
                raise FacilityConflict("Facility audit ledger verification failed")
            target = self._next(facility, FacilityAction.CLOSE, user)
            now = self._now()
            facility.closed_at = now
            self._advance(facility, target, now)
            self._record(
                session,
                facility,
                user,
                action=FacilityAction.CLOSE,
                idempotency_key=command.idempotency_key,
                expected_version=command.version,
                event_types=[
                    (
                        "FACILITY_CLOSED",
                        {"verified_ledger_head": verification["head_hash"]},
                    )
                ],
            )
            return self._serialize(session, facility, user)

    def get(
        self,
        facility_id: str | uuid.UUID,
        user: AuthenticatedUser,
    ) -> dict[str, Any]:
        normalized_id = self._normalize_uuid(facility_id)
        with self.session_factory() as session:
            facility = session.get(FinancingFacilityModel, normalized_id)
            if facility is None:
                raise FacilityNotFound(str(facility_id))
            application, creator = self._scope_models(session, facility)
            if not self._can_view(application, creator, user):
                raise FacilityNotFound(str(facility_id))
            return self._serialize(session, facility, user)

    def list_for_user(
        self,
        user: AuthenticatedUser,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        if limit < 1 or limit > 200 or offset < 0:
            raise ValueError("limit must be 1-200 and offset must be non-negative")
        with self.session_factory() as session:
            facilities = list(
                session.scalars(
                    select(FinancingFacilityModel).order_by(
                        FinancingFacilityModel.updated_at.desc()
                    )
                )
            )
            visible = []
            for facility in facilities:
                application, creator = self._scope_models(session, facility)
                if self._can_view(application, creator, user):
                    visible.append(facility)
            return [
                self._serialize(session, facility, user)
                for facility in visible[offset : offset + limit]
            ]

    def _load_for_command(
        self,
        session: Session,
        facility_id: str | uuid.UUID,
        user: AuthenticatedUser,
    ) -> FinancingFacilityModel:
        normalized_id = self._normalize_uuid(facility_id)
        facility = self.repository.get_for_update(session, normalized_id)
        if facility is None:
            raise FacilityNotFound(str(facility_id))
        application, creator = self._scope_models(session, facility)
        if not self._can_view(application, creator, user):
            raise FacilityNotFound(str(facility_id))
        return facility

    def _replay(
        self,
        session: Session,
        key: uuid.UUID,
        user: AuthenticatedUser,
    ) -> dict[str, Any] | None:
        action = self.repository.find_action(session, key)
        if action is None:
            return None
        if action.actor_user_id != user.user_id:
            raise ForbiddenFacility("Idempotency key belongs to another actor")
        facility = session.get(FinancingFacilityModel, action.facility_id)
        if facility is None:
            raise FacilityNotFound(str(action.facility_id))
        application, creator = self._scope_models(session, facility)
        if not self._can_view(application, creator, user):
            raise FacilityNotFound(str(action.facility_id))
        return self._serialize(session, facility, user)

    @staticmethod
    def _scope_models(
        session: Session,
        facility: FinancingFacilityModel,
    ) -> tuple[FinancingRequestModel, UserModel]:
        application = session.get(FinancingRequestModel, facility.request_id)
        creator = session.get(UserModel, facility.created_by_user_id)
        if application is None or creator is None:
            raise FacilityNotFound(str(facility.facility_id))
        return application, creator

    @staticmethod
    def _can_view(
        application: FinancingRequestModel,
        creator: UserModel,
        user: AuthenticatedUser,
    ) -> bool:
        try:
            role = Role(user.role)
        except ValueError:
            return False
        if role == Role.SUPPLIER:
            return application.supplier_organization_id == user.organization_id
        if role == Role.CORE_ENTERPRISE:
            return (
                application.core_enterprise_organization_id == user.organization_id
            )
        if role in {Role.FINANCIER, Role.RISK_MANAGER}:
            return creator.organization_id == user.organization_id
        return role == Role.AUDITOR

    @staticmethod
    def _require_role(user: AuthenticatedUser, expected: Role) -> None:
        if user.role != expected.value:
            raise ForbiddenFacility(
                f"{user.role} cannot perform an action reserved for {expected.value}"
            )

    @staticmethod
    def _check_version(
        facility: FinancingFacilityModel,
        expected_version: int,
    ) -> None:
        if facility.version != expected_version:
            raise FacilityConflict(
                f"Expected version {expected_version}, "
                f"current version {facility.version}"
            )

    @staticmethod
    def _next(
        facility: FinancingFacilityModel,
        action: FacilityAction,
        user: AuthenticatedUser,
    ) -> FacilityStatus:
        try:
            return next_facility_status(
                FacilityStatus(facility.status),
                action,
                Role(user.role),
            )
        except (InvalidFacilityTransition, ValueError) as error:
            raise FacilityConflict(str(error)) from error

    @staticmethod
    def _load_installment(
        session: Session,
        facility_id: uuid.UUID,
        installment_id: uuid.UUID,
    ) -> InstallmentModel:
        installment = session.scalar(
            select(InstallmentModel).where(
                InstallmentModel.facility_id == facility_id,
                InstallmentModel.installment_id == installment_id,
            )
        )
        if installment is None:
            raise FacilityNotFound(str(installment_id))
        return installment

    @staticmethod
    def _advance(
        facility: FinancingFacilityModel,
        target: FacilityStatus,
        now: datetime,
    ) -> None:
        facility.status = target.value
        facility.version += 1
        facility.updated_at = now

    def _record(
        self,
        session: Session,
        facility: FinancingFacilityModel,
        user: AuthenticatedUser,
        *,
        action: FacilityAction,
        idempotency_key: uuid.UUID,
        expected_version: int,
        event_types: list[tuple[str, dict[str, Any]]],
    ) -> None:
        now = self._now()
        session.add(
            FacilityActionModel(
                facility_id=facility.facility_id,
                actor_user_id=user.user_id,
                actor_role=user.role,
                action_type=action.value,
                idempotency_key=idempotency_key,
                expected_version=expected_version,
                resulting_version=facility.version,
                payload={"ledger_event_types": [item[0] for item in event_types]},
                created_at=now,
            )
        )
        common = {
            "facility_id": str(facility.facility_id),
            "actor_user_id": str(user.user_id),
            "actor_role": user.role,
            "facility_status": facility.status,
            "facility_version": facility.version,
        }
        self.ledger_repository.append_many(
            session,
            facility.facility_id,
            [
                (event_type, {**common, **payload})
                for event_type, payload in event_types
            ],
        )

    def _serialize(
        self,
        session: Session,
        facility: FinancingFacilityModel,
        user: AuthenticatedUser,
    ) -> dict[str, Any]:
        installments = self.repository.list_installments(
            session,
            facility.facility_id,
        )
        payments = self.repository.list_payments(session, facility.facility_id)
        return {
            "facility_id": str(facility.facility_id),
            "request_id": str(facility.request_id),
            "principal": self._money(facility.principal),
            "outstanding_amount": self._money(facility.outstanding_amount),
            "currency": facility.currency,
            "status": facility.status,
            "version": facility.version,
            "disbursement_reference": facility.disbursement_reference,
            "disbursement_evidence_sha256": facility.disbursement_evidence_sha256,
            "created_at": canonical_timestamp(facility.created_at),
            "updated_at": canonical_timestamp(facility.updated_at),
            "disbursement_initiated_at": self._timestamp(
                facility.disbursement_initiated_at
            ),
            "disbursed_at": self._timestamp(facility.disbursed_at),
            "repaid_at": self._timestamp(facility.repaid_at),
            "closed_at": self._timestamp(facility.closed_at),
            "allowed_actions": self._allowed_actions(facility, payments, user),
            "installments": [
                {
                    "installment_id": str(item.installment_id),
                    "sequence": item.sequence,
                    "due_date": item.due_date.isoformat(),
                    "amount": self._money(item.amount),
                    "paid_amount": self._money(item.paid_amount),
                    "status": item.status,
                }
                for item in installments
            ],
            "payments": [
                {
                    "payment_id": str(item.payment_id),
                    "installment_id": str(item.installment_id),
                    "amount": self._money(item.amount),
                    "payment_reference": item.payment_reference,
                    "status": item.status,
                    "submitted_at": canonical_timestamp(item.submitted_at),
                    "decided_at": self._timestamp(item.decided_at),
                    "decision_comment": item.decision_comment,
                }
                for item in payments
            ],
        }

    @staticmethod
    def _allowed_actions(
        facility: FinancingFacilityModel,
        payments: list[PaymentModel],
        user: AuthenticatedUser,
    ) -> list[str]:
        status = FacilityStatus(facility.status)
        if user.role == Role.FINANCIER.value:
            if status == FacilityStatus.READY:
                return [FacilityAction.INITIATE_DISBURSEMENT.value]
            if status == FacilityStatus.DISBURSED:
                return [FacilityAction.CONFIRM_DISBURSEMENT.value]
            if status in {FacilityStatus.ACTIVE, FacilityStatus.OVERDUE} and any(
                item.status == PaymentStatus.SUBMITTED.value for item in payments
            ):
                return [
                    FacilityAction.CONFIRM_PAYMENT.value,
                    FacilityAction.REJECT_PAYMENT.value,
                ]
        if user.role == Role.SUPPLIER.value and status in {
            FacilityStatus.ACTIVE,
            FacilityStatus.OVERDUE,
        }:
            return [FacilityAction.SUBMIT_PAYMENT.value]
        if user.role == Role.RISK_MANAGER.value and status == FacilityStatus.ACTIVE:
            return [FacilityAction.MARK_OVERDUE.value]
        if user.role == Role.AUDITOR.value and status == FacilityStatus.REPAID:
            return [FacilityAction.CLOSE.value]
        return []

    def _now(self) -> datetime:
        now = self.clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Facility clock must return a timezone-aware datetime")
        return now

    @staticmethod
    def _normalize_uuid(value: str | uuid.UUID) -> uuid.UUID:
        try:
            return uuid.UUID(str(value))
        except ValueError as error:
            raise FacilityNotFound(str(value)) from error

    @staticmethod
    def _money(value: Decimal) -> str:
        return format(Decimal(value), ".2f")

    @staticmethod
    def _timestamp(value: datetime | None) -> str | None:
        return canonical_timestamp(value) if value is not None else None


__all__ = [
    "FacilityConflict",
    "FacilityError",
    "FacilityNotFound",
    "FacilityService",
    "ForbiddenFacility",
]
