from __future__ import annotations

import hashlib
import uuid
from collections import defaultdict
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
    LifecycleFacts,
    PaymentStatus,
    derive_closure_reason,
    next_facility_status,
    settlement_classification,
)
from app.domain.workflow import Role
from app.identity import AuthenticatedUser
from app.ledger import canonical_json, canonical_timestamp
from app.models import FinancingRequestModel
from app.models_facility import (
    FacilityActionModel,
    FinancingFacilityModel,
    InstallmentModel,
    PaymentModel,
)
from app.models_identity import UserModel
from app.models_lifecycle import (
    FacilityContractVersionModel,
    FacilityDefaultModel,
    FacilityDelinquencyModel,
    FacilityLifecycleDecisionModel,
    FacilityRecoveryModel,
    FacilityRestructureModel,
    FacilityStatusTransitionModel,
    FacilityWriteOffModel,
)
from app.repositories.facility import FacilityRepository
from app.repositories.ledger import LedgerRepository
from app.repositories.workflow import WorkflowRepository
from app.schemas_facility import (
    CreateFacilityRequest,
    DeclareDefaultRequest,
    DecisionPaymentRequest,
    LifecycleDecisionRequest,
    MarkOverdueRequest,
    RecordRecoveryRequest,
    RestructureFacilityRequest,
    SubmitPaymentRequest,
    VersionedFacilityCommand,
    WriteOffRequest,
)
from pydantic import BaseModel


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
        semantic = self._command_semantic(
            FacilityAction.CREATE.value,
            {"request_id": str(request.request_id)},
            request,
        )
        with self.session_factory.begin() as session:
            replay = self._replay(
                session,
                request.idempotency_key,
                user,
                semantic,
            )
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
            replay = self._replay(
                session,
                request.idempotency_key,
                user,
                semantic,
            )
            if replay is not None:
                return replay
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
            session.add(
                self._contract_version(
                    facility,
                    contract_version=1,
                    origin="origination",
                    outstanding_at_start=Decimal(facility.principal),
                    schedule=[
                        {
                            "sequence": item.sequence,
                            "due_date": item.due_date.isoformat(),
                            "amount": self._money(item.amount),
                        }
                        for item in request.installments
                    ],
                    superseded_schedule=None,
                    restructure_id=None,
                    user=user,
                    now=now,
                )
            )
            session.add(
                FacilityStatusTransitionModel(
                    facility_id=facility.facility_id,
                    from_status=None,
                    to_status=facility.status,
                    trigger_action=FacilityAction.CREATE.value,
                    actor_user_id=user.user_id,
                    actor_role=user.role,
                    resulting_version=facility.version,
                    reason_code=None,
                    recorded_at=now,
                )
            )
            self._record(
                session,
                facility,
                user,
                action=FacilityAction.CREATE,
                idempotency_key=request.idempotency_key,
                expected_version=1,
                semantic=semantic,
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
        normalized_id = self._normalize_uuid(facility_id)
        semantic = self._command_semantic(
            FacilityAction.INITIATE_DISBURSEMENT.value,
            {"facility_id": str(normalized_id)},
            command,
        )
        with self.session_factory.begin() as session:
            replay = self._replay(
                session, command.idempotency_key, user, semantic
            )
            if replay is not None:
                return replay
            self._require_role(user, Role.FINANCIER)
            facility = self._load_for_command(session, normalized_id, user)
            replay = self._replay(
                session, command.idempotency_key, user, semantic
            )
            if replay is not None:
                return replay
            self._check_version(facility, command.version)
            target = self._next(
                session,
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
            self._advance(session, facility, target, now, user, FacilityAction.INITIATE_DISBURSEMENT)
            self._record(
                session,
                facility,
                user,
                action=FacilityAction.INITIATE_DISBURSEMENT,
                idempotency_key=command.idempotency_key,
                expected_version=command.version,
                semantic=semantic,
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
        normalized_id = self._normalize_uuid(facility_id)
        semantic = self._command_semantic(
            FacilityAction.CONFIRM_DISBURSEMENT.value,
            {"facility_id": str(normalized_id)},
            command,
        )
        with self.session_factory.begin() as session:
            replay = self._replay(
                session, command.idempotency_key, user, semantic
            )
            if replay is not None:
                return replay
            self._require_role(user, Role.FINANCIER)
            facility = self._load_for_command(session, normalized_id, user)
            replay = self._replay(
                session, command.idempotency_key, user, semantic
            )
            if replay is not None:
                return replay
            self._check_version(facility, command.version)
            target = self._next(
                session,
                facility,
                FacilityAction.CONFIRM_DISBURSEMENT,
                user,
            )
            now = self._now()
            facility.disbursed_at = now
            self._advance(session, facility, target, now, user, FacilityAction.CONFIRM_DISBURSEMENT)
            self._record(
                session,
                facility,
                user,
                action=FacilityAction.CONFIRM_DISBURSEMENT,
                idempotency_key=command.idempotency_key,
                expected_version=command.version,
                semantic=semantic,
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
        normalized_id = self._normalize_uuid(facility_id)
        semantic = self._command_semantic(
            FacilityAction.SUBMIT_PAYMENT.value,
            {
                "facility_id": str(normalized_id),
                "installment_id": str(request.installment_id),
            },
            request,
        )
        with self.session_factory.begin() as session:
            replay = self._replay(
                session, request.idempotency_key, user, semantic
            )
            if replay is not None:
                return replay
            self._require_role(user, Role.SUPPLIER)
            facility = self._load_for_command(session, normalized_id, user)
            replay = self._replay(
                session, request.idempotency_key, user, semantic
            )
            if replay is not None:
                return replay
            self._check_version(facility, request.version)
            self._next(session, facility, FacilityAction.SUBMIT_PAYMENT, user)
            installment = self._load_installment(
                session,
                facility.facility_id,
                request.installment_id,
            )
            if (
                installment.schedule_version != facility.current_schedule_version
                or installment.status == InstallmentStatus.SUPERSEDED.value
            ):
                raise FacilityConflict(
                    "Payment must reference the current repayment schedule"
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
                semantic=semantic,
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
        normalized_id = self._normalize_uuid(facility_id)
        normalized_payment_id = self._normalize_uuid(payment_id)
        semantic = self._command_semantic(
            "decide_payment",
            {
                "facility_id": str(normalized_id),
                "payment_id": str(normalized_payment_id),
            },
            request,
        )
        with self.session_factory.begin() as session:
            replay = self._replay(
                session, request.idempotency_key, user, semantic
            )
            if replay is not None:
                return replay
            self._require_role(user, Role.FINANCIER)
            facility = self._load_for_command(session, normalized_id, user)
            replay = self._replay(
                session, request.idempotency_key, user, semantic
            )
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
                    session,
                    facility,
                    FacilityAction.REJECT_PAYMENT,
                    user,
                )
                payment.status = PaymentStatus.REJECTED.value
                self._advance(session, facility, target, now, user, FacilityAction.REJECT_PAYMENT)
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
                target = self._next(session, facility, action, user)
                payment.status = PaymentStatus.CONFIRMED.value
                installment.paid_amount = new_installment_paid
                if new_installment_paid == Decimal(installment.amount):
                    installment.status = InstallmentStatus.PAID.value
                elif installment.status != InstallmentStatus.OVERDUE.value:
                    # A partial payment never clears an overdue marker.
                    installment.status = InstallmentStatus.PARTIALLY_PAID.value
                installment.updated_at = now
                facility.outstanding_amount = new_outstanding
                trigger = action
                cured = False
                if (
                    not is_final
                    and target == FacilityStatus.OVERDUE
                    and not self._arrears(session, facility, now)
                ):
                    target = self._next(
                        session, facility, FacilityAction.CURE_OVERDUE, user
                    )
                    trigger = FacilityAction.CURE_OVERDUE
                    cured = True
                if target == FacilityStatus.REPAID:
                    facility.repaid_at = now
                self._advance(session, facility, target, now, user, trigger)
                confirmation_payload = {
                    "payment_id": str(payment.payment_id),
                    "installment_id": str(installment.installment_id),
                    "amount": self._money(amount),
                    "outstanding_amount": self._money(new_outstanding),
                }
                events = [("REPAYMENT_CONFIRMED", confirmation_payload)]
                if cured:
                    events.append(
                        (
                            "FACILITY_OVERDUE_CURED",
                            {
                                "cured_by_payment_id": str(payment.payment_id),
                                "resulting_status": target.value,
                            },
                        )
                    )
                if is_final:
                    events.append(
                        (
                            "FACILITY_REPAID"
                            if target == FacilityStatus.REPAID
                            else "FACILITY_RECOVERED",
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
                semantic=semantic,
                event_types=events,
            )
            return self._serialize(session, facility, user)

    def mark_overdue(
        self,
        facility_id: str | uuid.UUID,
        installment_id: str | uuid.UUID,
        command: MarkOverdueRequest,
        user: AuthenticatedUser,
    ) -> dict[str, Any]:
        normalized_id = self._normalize_uuid(facility_id)
        normalized_installment_id = self._normalize_uuid(installment_id)
        semantic = self._command_semantic(
            FacilityAction.MARK_OVERDUE.value,
            {
                "facility_id": str(normalized_id),
                "installment_id": str(normalized_installment_id),
            },
            command,
        )
        with self.session_factory.begin() as session:
            replay = self._replay(
                session, command.idempotency_key, user, semantic
            )
            if replay is not None:
                return replay
            self._require_role(user, Role.FINANCIER)
            facility = self._load_for_command(session, normalized_id, user)
            replay = self._replay(
                session, command.idempotency_key, user, semantic
            )
            if replay is not None:
                return replay
            self._check_version(facility, command.version)
            if normalized_installment_id != command.installment_id:
                raise FacilityConflict("Overdue installment scope does not match")
            installment = self._load_installment(
                session,
                facility.facility_id,
                normalized_installment_id,
            )
            now = self._now()
            if installment.status == InstallmentStatus.PAID.value:
                raise FacilityConflict("A paid installment cannot be overdue")
            if (
                installment.schedule_version != facility.current_schedule_version
                or installment.status == InstallmentStatus.SUPERSEDED.value
            ):
                raise FacilityConflict(
                    "Only a current-schedule installment can be overdue"
                )
            if installment.due_date >= now.date():
                raise FacilityConflict("The installment is not past due")
            target = self._next(session, facility, FacilityAction.MARK_OVERDUE, user)
            installment.status = InstallmentStatus.OVERDUE.value
            installment.updated_at = now
            session.add(
                FacilityDelinquencyModel(
                    delinquency_id=uuid.uuid4(),
                    facility_id=facility.facility_id,
                    marked_by_user_id=user.user_id,
                    days_past_due=command.days_past_due,
                    reason_code="PAST_DUE",
                    comment="Current installment marked overdue",
                    evidence_sha256=command.evidence_sha256,
                    recorded_at=now,
                )
            )
            self._advance(session, facility, target, now, user, FacilityAction.MARK_OVERDUE)
            self._record(
                session,
                facility,
                user,
                action=FacilityAction.MARK_OVERDUE,
                idempotency_key=command.idempotency_key,
                expected_version=command.version,
                semantic=semantic,
                event_types=[
                    (
                        "FACILITY_MARKED_OVERDUE",
                        {
                            "installment_id": str(installment.installment_id),
                            "due_date": installment.due_date.isoformat(),
                            "days_past_due": command.days_past_due,
                            "evidence_sha256": command.evidence_sha256,
                        },
                    )
                ],
            )
            return self._serialize(session, facility, user)

    def restructure(
        self,
        facility_id: str | uuid.UUID,
        command: RestructureFacilityRequest,
        user: AuthenticatedUser,
    ) -> dict[str, Any]:
        normalized_id = self._normalize_uuid(facility_id)
        semantic = self._command_semantic(
            FacilityAction.RESTRUCTURE.value,
            {"facility_id": str(normalized_id)},
            command,
        )
        with self.session_factory.begin() as session:
            replay = self._replay(session, command.idempotency_key, user, semantic)
            if replay is not None:
                return replay
            self._require_role(user, Role.RISK_MANAGER)
            facility = self._load_for_command(session, normalized_id, user)
            replay = self._replay(session, command.idempotency_key, user, semantic)
            if replay is not None:
                return replay
            self._check_version(facility, command.version)
            target = self._next(session, facility, FacilityAction.RESTRUCTURE, user)
            outstanding = Decimal(facility.outstanding_amount)
            if command.schedule_total != outstanding:
                raise FacilityConflict(
                    "Replacement schedule must equal the exact outstanding balance"
                )
            now = self._now()
            if any(item.due_date <= now.date() for item in command.installments):
                raise FacilityConflict(
                    "Replacement installments must have future due dates"
                )
            payments = self.repository.list_payments(session, facility.facility_id)
            if any(item.status == PaymentStatus.SUBMITTED.value for item in payments):
                raise FacilityConflict(
                    "Pending payment decisions must be resolved before restructuring"
                )
            current_rows = [
                item
                for item in self.repository.list_installments(
                    session, facility.facility_id
                )
                if item.schedule_version == facility.current_schedule_version
            ]
            unpaid_rows = [
                item
                for item in current_rows
                if item.status != InstallmentStatus.PAID.value
            ]
            unpaid_total = sum(
                (
                    Decimal(item.amount) - Decimal(item.paid_amount)
                    for item in unpaid_rows
                ),
                Decimal("0.00"),
            )
            if not unpaid_rows or unpaid_total != outstanding:
                raise FacilityConflict(
                    "Current schedule does not reconcile to the exact outstanding balance"
                )
            old_version = facility.current_schedule_version
            new_version = old_version + 1
            # Freeze the prior contract state before supersession marks it.
            superseded_schedule = [
                {
                    "installment_id": str(item.installment_id),
                    "sequence": item.sequence,
                    "due_date": item.due_date.isoformat(),
                    "amount": self._money(item.amount),
                    "paid_amount": self._money(item.paid_amount),
                    "status_before": item.status,
                }
                for item in sorted(unpaid_rows, key=lambda row: row.sequence)
            ]
            for installment in unpaid_rows:
                installment.status = InstallmentStatus.SUPERSEDED.value
                installment.updated_at = now
            session.add_all(
                [
                    InstallmentModel(
                        installment_id=uuid.uuid4(),
                        facility_id=facility.facility_id,
                        sequence=item.sequence,
                        schedule_version=new_version,
                        due_date=item.due_date,
                        amount=item.amount,
                        paid_amount=Decimal("0.00"),
                        status=InstallmentStatus.SCHEDULED.value,
                        created_at=now,
                        updated_at=now,
                    )
                    for item in command.installments
                ]
            )
            restructure = FacilityRestructureModel(
                restructure_id=uuid.uuid4(),
                facility_id=facility.facility_id,
                restructured_by_user_id=user.user_id,
                old_schedule_version=old_version,
                new_schedule_version=new_version,
                reason_code=command.reason_code,
                comment=command.comment,
                evidence_sha256=command.evidence_sha256,
                recorded_at=now,
            )
            session.add(restructure)
            session.flush()
            previous_contract = self.repository.get_contract_version(
                session, facility.facility_id, old_version
            )
            contract = self._contract_version(
                facility,
                contract_version=new_version,
                origin="restructure",
                outstanding_at_start=outstanding,
                schedule=[
                    {
                        "sequence": item.sequence,
                        "due_date": item.due_date.isoformat(),
                        "amount": self._money(item.amount),
                    }
                    for item in command.installments
                ],
                superseded_schedule=superseded_schedule,
                restructure_id=restructure.restructure_id,
                user=user,
                now=now,
            )
            session.add(contract)
            facility.current_schedule_version = new_version
            self._advance(
                session,
                facility,
                target,
                now,
                user,
                FacilityAction.RESTRUCTURE,
                reason_code=command.reason_code,
            )
            self._record(
                session,
                facility,
                user,
                action=FacilityAction.RESTRUCTURE,
                idempotency_key=command.idempotency_key,
                expected_version=command.version,
                semantic=semantic,
                event_types=[
                    (
                        "FACILITY_RESTRUCTURED",
                        {
                            "old_schedule_version": old_version,
                            "new_schedule_version": new_version,
                            "outstanding_amount": self._money(outstanding),
                            "reason_code": command.reason_code,
                            "evidence_sha256": command.evidence_sha256,
                            "previous_contract_sha256": (
                                previous_contract.terms_sha256
                                if previous_contract is not None
                                else None
                            ),
                            "contract_sha256": contract.terms_sha256,
                        },
                    )
                ],
            )
            return self._serialize(session, facility, user)

    def declare_default(
        self,
        facility_id: str | uuid.UUID,
        command: DeclareDefaultRequest,
        user: AuthenticatedUser,
    ) -> dict[str, Any]:
        normalized_id = self._normalize_uuid(facility_id)
        semantic = self._command_semantic(
            FacilityAction.DECLARE_DEFAULT.value,
            {"facility_id": str(normalized_id)},
            command,
        )
        with self.session_factory.begin() as session:
            replay = self._replay(session, command.idempotency_key, user, semantic)
            if replay is not None:
                return replay
            self._require_role(user, Role.RISK_MANAGER)
            facility = self._load_for_command(session, normalized_id, user)
            replay = self._replay(session, command.idempotency_key, user, semantic)
            if replay is not None:
                return replay
            self._check_version(facility, command.version)
            target = self._next(session, facility, FacilityAction.DECLARE_DEFAULT, user)
            if any(
                item.schedule_version == facility.current_schedule_version
                for item in self.repository.list_defaults_batch(session, [facility.facility_id])
            ):
                raise FacilityConflict("Current schedule default has already been declared")
            delinquencies = self.repository.list_delinquencies(
                session, facility.facility_id
            )
            if not delinquencies:
                raise FacilityConflict(
                    "Default requires governed overdue evidence"
                )
            now = self._now()
            session.add(
                FacilityDefaultModel(
                    default_id=uuid.uuid4(),
                    facility_id=facility.facility_id,
                    schedule_version=facility.current_schedule_version,
                    declared_by_user_id=user.user_id,
                    defaulted_at=command.defaulted_at,
                    days_past_due=command.days_past_due,
                    reason_code=command.reason_code,
                    comment=command.comment,
                    evidence_sha256=command.evidence_sha256,
                    recorded_at=now,
                )
            )
            self._advance(
                session, facility, target, now, user,
                FacilityAction.DECLARE_DEFAULT, reason_code=command.reason_code,
            )
            self._record(
                session,
                facility,
                user,
                action=FacilityAction.DECLARE_DEFAULT,
                idempotency_key=command.idempotency_key,
                expected_version=command.version,
                semantic=semantic,
                event_types=[
                    (
                        "FACILITY_DEFAULTED",
                        {
                            "defaulted_at": canonical_timestamp(
                                command.defaulted_at
                            ),
                            "days_past_due": command.days_past_due,
                            "reason_code": command.reason_code,
                            "evidence_sha256": command.evidence_sha256,
                            "outstanding_amount": self._money(
                                facility.outstanding_amount
                            ),
                        },
                    )
                ],
            )
            return self._serialize(session, facility, user)

    def write_off(
        self,
        facility_id: str | uuid.UUID,
        command: WriteOffRequest,
        user: AuthenticatedUser,
    ) -> dict[str, Any]:
        normalized_id = self._normalize_uuid(facility_id)
        semantic = self._command_semantic(
            FacilityAction.WRITE_OFF.value,
            {"facility_id": str(normalized_id)},
            command,
        )
        with self.session_factory.begin() as session:
            replay = self._replay(session, command.idempotency_key, user, semantic)
            if replay is not None:
                return replay
            self._require_role(user, Role.AUDITOR)
            facility = self._load_for_command(session, normalized_id, user)
            replay = self._replay(session, command.idempotency_key, user, semantic)
            if replay is not None:
                return replay
            self._check_version(facility, command.version)
            target = self._next(session, facility, FacilityAction.WRITE_OFF, user)
            payments = self.repository.list_payments(
                session, facility.facility_id
            )
            if any(
                item.status == PaymentStatus.SUBMITTED.value
                for item in payments
            ):
                raise FacilityConflict(
                    "Pending payment decisions must be resolved before write-off"
                )
            if self.repository.get_default(session, facility.facility_id) is None:
                raise FacilityConflict("Write-off requires immutable default history")
            if self.repository.get_writeoff(session, facility.facility_id) is not None:
                raise FacilityConflict("Facility balance has already been written off")
            amount = Decimal(facility.outstanding_amount)
            if amount <= Decimal("0.00"):
                raise FacilityConflict("Write-off requires a positive remaining balance")
            now = self._now()
            session.add(
                FacilityWriteOffModel(
                    writeoff_id=uuid.uuid4(),
                    facility_id=facility.facility_id,
                    amount=amount,
                    auditor_user_id=user.user_id,
                    reason_code=command.reason_code,
                    comment=command.comment,
                    evidence_sha256=command.evidence_sha256,
                    recorded_at=now,
                )
            )
            facility.outstanding_amount = Decimal("0.00")
            self._advance(
                session, facility, target, now, user,
                FacilityAction.WRITE_OFF, reason_code=command.reason_code,
            )
            self._record(
                session,
                facility,
                user,
                action=FacilityAction.WRITE_OFF,
                idempotency_key=command.idempotency_key,
                expected_version=command.version,
                semantic=semantic,
                event_types=[
                    (
                        "FACILITY_WRITTEN_OFF",
                        {
                            "amount": self._money(amount),
                            "reason_code": command.reason_code,
                            "evidence_sha256": command.evidence_sha256,
                            "outstanding_amount": "0.00",
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
        normalized_id = self._normalize_uuid(facility_id)
        semantic = self._command_semantic(
            FacilityAction.CLOSE.value,
            {"facility_id": str(normalized_id)},
            command,
        )
        with self.session_factory.begin() as session:
            replay = self._replay(
                session, command.idempotency_key, user, semantic
            )
            if replay is not None:
                return replay
            self._require_role(user, Role.AUDITOR)
            facility = self._load_for_command(session, normalized_id, user)
            replay = self._replay(
                session, command.idempotency_key, user, semantic
            )
            if replay is not None:
                return replay
            self._check_version(facility, command.version)
            if Decimal(facility.outstanding_amount) != Decimal("0.00"):
                raise FacilityConflict("Only a fully repaid facility can be closed")
            verification = self.ledger_repository.verify(session)
            if not verification["valid"]:
                raise FacilityConflict("Facility audit ledger verification failed")
            target = self._next(session, facility, FacilityAction.CLOSE, user)
            default_event = self.repository.get_default(
                session, facility.facility_id
            )
            writeoff_event = self.repository.get_writeoff(
                session, facility.facility_id
            )
            try:
                closure_reason = derive_closure_reason(
                    has_default=default_event is not None,
                    has_writeoff=writeoff_event is not None,
                )
            except ValueError as error:
                raise FacilityConflict(str(error)) from error
            if facility.closure_reason is not None:
                raise FacilityConflict("Facility closure reason is immutable")
            now = self._now()
            facility.closed_at = now
            facility.closure_reason = closure_reason
            self._advance(session, facility, target, now, user, FacilityAction.CLOSE)
            self._record(
                session,
                facility,
                user,
                action=FacilityAction.CLOSE,
                idempotency_key=command.idempotency_key,
                expected_version=command.version,
                semantic=semantic,
                event_types=[
                    (
                        "FACILITY_CLOSED",
                        {
                            "verified_ledger_head": verification["head_hash"],
                            "closure_reason": closure_reason,
                        },
                    )
                ],
            )
            return self._serialize(session, facility, user)

    def open_disposal(
        self,
        facility_id: str | uuid.UUID,
        command: LifecycleDecisionRequest,
        user: AuthenticatedUser,
    ) -> dict[str, Any]:
        """Move an overdue facility into governed risk disposal (workout)."""

        def precondition(session: Session, facility: FinancingFacilityModel) -> None:
            if not self._arrears(session, facility, self._now()):
                raise FacilityConflict(
                    "Risk disposal requires a past-due unpaid current-schedule installment"
                )

        return self._lifecycle_decision(
            facility_id,
            command,
            user,
            action=FacilityAction.OPEN_DISPOSAL,
            decision_type="disposal_opened",
            event_type="FACILITY_DISPOSAL_OPENED",
            precondition=precondition,
        )

    def close_disposal(
        self,
        facility_id: str | uuid.UUID,
        command: LifecycleDecisionRequest,
        user: AuthenticatedUser,
    ) -> dict[str, Any]:
        """Return a facility from disposal to performing once arrears are cleared."""

        def precondition(session: Session, facility: FinancingFacilityModel) -> None:
            if self._arrears(session, facility, self._now()):
                raise FacilityConflict(
                    "Risk disposal can close only after every arrear is cleared"
                )

        return self._lifecycle_decision(
            facility_id,
            command,
            user,
            action=FacilityAction.CLOSE_DISPOSAL,
            decision_type="disposal_closed",
            event_type="FACILITY_DISPOSAL_CLOSED",
            precondition=precondition,
        )

    def start_recovery(
        self,
        facility_id: str | uuid.UUID,
        command: LifecycleDecisionRequest,
        user: AuthenticatedUser,
    ) -> dict[str, Any]:
        """Start recovery (追偿) on a facility defaulted on its current contract."""

        def precondition(session: Session, facility: FinancingFacilityModel) -> None:
            defaults = self.repository.list_defaults_batch(session, [facility.facility_id])
            if not any(
                item.schedule_version == facility.current_schedule_version
                for item in defaults
            ):
                raise FacilityConflict(
                    "Recovery requires a default on the current contract version"
                )

        return self._lifecycle_decision(
            facility_id,
            command,
            user,
            action=FacilityAction.START_RECOVERY,
            decision_type="recovery_started",
            event_type="FACILITY_RECOVERY_STARTED",
            precondition=precondition,
        )

    def record_recovery(
        self,
        facility_id: str | uuid.UUID,
        request: RecordRecoveryRequest,
        user: AuthenticatedUser,
    ) -> dict[str, Any]:
        """Record third-party recovery cash, before or after write-off."""

        normalized_id = self._normalize_uuid(facility_id)
        semantic = self._command_semantic(
            FacilityAction.RECORD_RECOVERY.value,
            {"facility_id": str(normalized_id)},
            request,
        )
        with self.session_factory.begin() as session:
            replay = self._replay(session, request.idempotency_key, user, semantic)
            if replay is not None:
                return replay
            self._require_role(user, Role.FINANCIER)
            facility = self._load_for_command(session, normalized_id, user)
            replay = self._replay(session, request.idempotency_key, user, semantic)
            if replay is not None:
                return replay
            self._check_version(facility, request.version)
            status = FacilityStatus(facility.status)
            if status == FacilityStatus.IN_RECOVERY:
                if any(
                    item.status == PaymentStatus.SUBMITTED.value
                    for item in self.repository.list_payments(session, facility.facility_id)
                ):
                    raise FacilityConflict(
                        "Pending payment decisions must be resolved before recording recovery"
                    )
                outstanding = Decimal(facility.outstanding_amount)
                if request.amount > outstanding:
                    raise FacilityConflict("Recovery exceeds the exact outstanding balance")
                new_outstanding = outstanding - request.amount
                action = (
                    FacilityAction.RECORD_FINAL_RECOVERY
                    if new_outstanding == Decimal("0.00")
                    else FacilityAction.RECORD_RECOVERY
                )
                applied_to = "outstanding"
            elif status == FacilityStatus.WRITTEN_OFF:
                writeoff = self.repository.get_writeoff(session, facility.facility_id)
                if writeoff is None:
                    raise FacilityConflict("Written-off claim has no write-off record")
                collected = sum(
                    (
                        Decimal(item.amount)
                        for item in self.repository.list_recoveries_batch(
                            session, [facility.facility_id]
                        )
                        if item.applied_to == "written_off"
                    ),
                    Decimal("0.00"),
                )
                if request.amount > Decimal(writeoff.amount) - collected:
                    raise FacilityConflict(
                        "Recovery exceeds the remaining written-off claim"
                    )
                new_outstanding = Decimal(facility.outstanding_amount)
                action = FacilityAction.RECORD_RECOVERY
                applied_to = "written_off"
            else:
                raise FacilityConflict(
                    f"{user.role} cannot {FacilityAction.RECORD_RECOVERY.value} "
                    f"from {facility.status}"
                )
            target = self._next(session, facility, action, user)
            duplicate = session.scalar(
                select(FacilityRecoveryModel.recovery_id).where(
                    FacilityRecoveryModel.facility_id == facility.facility_id,
                    FacilityRecoveryModel.recovery_reference == request.recovery_reference,
                )
            )
            if duplicate is not None:
                raise FacilityConflict("Recovery reference already exists")
            now = self._now()
            recovery = FacilityRecoveryModel(
                recovery_id=uuid.uuid4(),
                facility_id=facility.facility_id,
                amount=request.amount,
                applied_to=applied_to,
                source=request.source.value,
                recovery_reference=request.recovery_reference,
                evidence_sha256=request.evidence_sha256,
                recorded_by_user_id=user.user_id,
                recorded_at=now,
            )
            session.add(recovery)
            facility.outstanding_amount = new_outstanding
            self._advance(session, facility, target, now, user, action)
            events: list[tuple[str, dict[str, Any]]] = [
                (
                    "FACILITY_RECOVERY_RECORDED",
                    {
                        "recovery_id": str(recovery.recovery_id),
                        "amount": self._money(request.amount),
                        "source": request.source.value,
                        "applied_to": applied_to,
                        "recovery_reference": request.recovery_reference,
                        "evidence_sha256": request.evidence_sha256,
                        "outstanding_amount": self._money(new_outstanding),
                    },
                )
            ]
            if target == FacilityStatus.RECOVERED:
                events.append(
                    (
                        "FACILITY_RECOVERED",
                        {
                            "outstanding_amount": "0.00",
                            "final_recovery_id": str(recovery.recovery_id),
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
                semantic=semantic,
                event_types=events,
            )
            return self._serialize(session, facility, user)

    def _lifecycle_decision(
        self,
        facility_id: str | uuid.UUID,
        command: LifecycleDecisionRequest,
        user: AuthenticatedUser,
        *,
        action: FacilityAction,
        decision_type: str,
        event_type: str,
        precondition: Callable[[Session, FinancingFacilityModel], None],
    ) -> dict[str, Any]:
        normalized_id = self._normalize_uuid(facility_id)
        semantic = self._command_semantic(
            action.value,
            {"facility_id": str(normalized_id)},
            command,
        )
        with self.session_factory.begin() as session:
            replay = self._replay(session, command.idempotency_key, user, semantic)
            if replay is not None:
                return replay
            self._require_role(user, Role.RISK_MANAGER)
            facility = self._load_for_command(session, normalized_id, user)
            replay = self._replay(session, command.idempotency_key, user, semantic)
            if replay is not None:
                return replay
            self._check_version(facility, command.version)
            target = self._next(session, facility, action, user)
            precondition(session, facility)
            now = self._now()
            decision = FacilityLifecycleDecisionModel(
                decision_id=uuid.uuid4(),
                facility_id=facility.facility_id,
                decision_type=decision_type,
                schedule_version=facility.current_schedule_version,
                decided_by_user_id=user.user_id,
                reason_code=command.reason_code,
                comment=command.comment,
                evidence_sha256=command.evidence_sha256,
                recorded_at=now,
            )
            session.add(decision)
            self._advance(
                session, facility, target, now, user, action,
                reason_code=command.reason_code,
            )
            self._record(
                session,
                facility,
                user,
                action=action,
                idempotency_key=command.idempotency_key,
                expected_version=command.version,
                semantic=semantic,
                event_types=[
                    (
                        event_type,
                        {
                            "decision_id": str(decision.decision_id),
                            "schedule_version": decision.schedule_version,
                            "reason_code": command.reason_code,
                            "evidence_sha256": command.evidence_sha256,
                            "outstanding_amount": self._money(
                                facility.outstanding_amount
                            ),
                        },
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
            facility = self.repository.get_visible_for_share(
                session,
                normalized_id,
                role=user.role,
                organization_id=user.organization_id,
            )
            if facility is None:
                raise FacilityNotFound(str(facility_id))
            related = self._load_related_batch(session, [facility])
            return self._serialize(session, facility, user, related=related)

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
            facilities = self.repository.list_visible_for_share(
                session,
                role=user.role,
                organization_id=user.organization_id,
                limit=limit,
                offset=offset,
            )
            related = self._load_related_batch(session, facilities)
            return [
                self._serialize(session, facility, user, related=related)
                for facility in facilities
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
        semantic: dict[str, Any],
    ) -> dict[str, Any] | None:
        action = self.repository.find_action(session, key)
        if action is None:
            return None
        if action.actor_user_id != user.user_id:
            raise ForbiddenFacility("Idempotency key belongs to another actor")
        stored = action.payload
        if (
            stored.get("command_name") != semantic["command_name"]
            or stored.get("scope") != semantic["scope"]
            or stored.get("command_fingerprint_sha256")
            != semantic["command_fingerprint_sha256"]
        ):
            raise FacilityConflict(
                "Idempotency key was already used for different command semantics"
            )
        facility = self.repository.get_for_update(session, action.facility_id)
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

    def _next(
        self,
        session: Session,
        facility: FinancingFacilityModel,
        action: FacilityAction,
        user: AuthenticatedUser,
    ) -> FacilityStatus:
        try:
            return next_facility_status(
                FacilityStatus(facility.status),
                action,
                Role(user.role),
                facts=self._facts(session, facility),
            )
        except (InvalidFacilityTransition, ValueError) as error:
            raise FacilityConflict(str(error)) from error

    @staticmethod
    def _facts(
        session: Session,
        facility: FinancingFacilityModel,
    ) -> LifecycleFacts:
        has_default = (
            session.scalar(
                select(FacilityDefaultModel.default_id)
                .where(FacilityDefaultModel.facility_id == facility.facility_id)
                .limit(1)
            )
            is not None
        )
        return LifecycleFacts(
            has_default_history=has_default,
            schedule_version=facility.current_schedule_version,
        )

    def _arrears(
        self,
        session: Session,
        facility: FinancingFacilityModel,
        now: datetime,
    ) -> list[InstallmentModel]:
        return self._arrears_from(
            self.repository.list_installments(session, facility.facility_id),
            facility.current_schedule_version,
            now,
        )

    @staticmethod
    def _arrears_from(
        installments: list[InstallmentModel],
        schedule_version: int,
        now: datetime,
    ) -> list[InstallmentModel]:
        """Current-schedule installments past due and not fully paid."""

        return [
            item
            for item in installments
            if item.schedule_version == schedule_version
            and item.status != InstallmentStatus.SUPERSEDED.value
            and item.due_date < now.date()
            and Decimal(item.paid_amount) < Decimal(item.amount)
        ]

    def _contract_version(
        self,
        facility: FinancingFacilityModel,
        *,
        contract_version: int,
        origin: str,
        outstanding_at_start: Decimal,
        schedule: list[dict[str, Any]],
        superseded_schedule: list[dict[str, Any]] | None,
        restructure_id: uuid.UUID | None,
        user: AuthenticatedUser,
        now: datetime,
    ) -> FacilityContractVersionModel:
        material = {
            "facility_id": str(facility.facility_id),
            "contract_version": contract_version,
            "principal": self._money(facility.principal),
            "outstanding_at_start": self._money(outstanding_at_start),
            "currency": facility.currency,
            "schedule": schedule,
        }
        return FacilityContractVersionModel(
            contract_version_id=uuid.uuid4(),
            facility_id=facility.facility_id,
            contract_version=contract_version,
            origin=origin,
            principal=facility.principal,
            outstanding_at_start=outstanding_at_start,
            currency=facility.currency,
            schedule=schedule,
            superseded_schedule=superseded_schedule,
            restructure_id=restructure_id,
            terms_sha256=hashlib.sha256(
                canonical_json(material).encode("utf-8")
            ).hexdigest(),
            created_by_user_id=user.user_id,
            recorded_at=now,
        )

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
        session: Session,
        facility: FinancingFacilityModel,
        target: FacilityStatus,
        now: datetime,
        user: AuthenticatedUser,
        trigger: FacilityAction,
        *,
        reason_code: str | None = None,
    ) -> None:
        """Apply a command's state change; a status change is always audited.

        PostgreSQL rejects both an illegal status pair and any status change
        without a matching transition row at the same resulting version.
        """

        previous = facility.status
        facility.status = target.value
        facility.version += 1
        facility.updated_at = now
        if previous != target.value:
            session.add(
                FacilityStatusTransitionModel(
                    facility_id=facility.facility_id,
                    from_status=previous,
                    to_status=target.value,
                    trigger_action=trigger.value,
                    actor_user_id=user.user_id,
                    actor_role=user.role,
                    resulting_version=facility.version,
                    reason_code=reason_code,
                    recorded_at=now,
                )
            )

    def _record(
        self,
        session: Session,
        facility: FinancingFacilityModel,
        user: AuthenticatedUser,
        *,
        action: FacilityAction,
        idempotency_key: uuid.UUID,
        expected_version: int,
        semantic: dict[str, Any],
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
                payload={
                    **semantic,
                    "ledger_event_types": [item[0] for item in event_types],
                },
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

    @staticmethod
    def _command_semantic(
        command_name: str,
        scope: dict[str, str],
        payload: BaseModel,
    ) -> dict[str, Any]:
        material = {
            "command_name": command_name,
            "scope": scope,
            "payload": payload.model_dump(mode="json"),
        }
        fingerprint = hashlib.sha256(
            canonical_json(material).encode("utf-8")
        ).hexdigest()
        return {
            "command_name": command_name,
            "scope": scope,
            "command_fingerprint_sha256": fingerprint,
        }

    def _serialize(
        self,
        session: Session,
        facility: FinancingFacilityModel,
        user: AuthenticatedUser,
        *,
        related: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if related is None:
            related = self._load_related_batch(session, [facility])
        facility_id = facility.facility_id
        installments = related["installments"].get(facility_id, [])
        payments = related["payments"].get(facility_id, [])
        delinquencies = related["delinquencies"].get(facility_id, [])
        restructures = related["restructures"].get(facility_id, [])
        default_history = related["defaults"].get(facility_id, [])
        writeoff_event = related["writeoffs"].get(facility_id)
        recoveries = related["recoveries"].get(facility_id, [])
        contract_versions = related["contract_versions"].get(facility_id, [])
        transitions = related["transitions"].get(facility_id, [])
        decisions = related["decisions"].get(facility_id, [])
        default_event = default_history[0] if default_history else None
        repaid_cash = sum(
            (Decimal(item.amount) for item in payments if item.status == PaymentStatus.CONFIRMED.value),
            Decimal("0.00"),
        )
        recovery_cash = sum(
            (Decimal(item.amount) for item in recoveries if item.applied_to == "outstanding"),
            Decimal("0.00"),
        )
        post_writeoff_recovery = sum(
            (Decimal(item.amount) for item in recoveries if item.applied_to == "written_off"),
            Decimal("0.00"),
        )
        written_off = Decimal(writeoff_event.amount) if writeoff_event else Decimal("0.00")
        if (
            Decimal(facility.principal)
            != Decimal(facility.outstanding_amount) + repaid_cash + recovery_cash + written_off
        ):
            raise FacilityConflict("Facility principal conservation failed")
        now = self._now()
        arrears = self._arrears_from(installments, facility.current_schedule_version, now)
        return {
            "facility_id": str(facility.facility_id),
            "request_id": str(facility.request_id),
            "principal": self._money(facility.principal),
            "outstanding_amount": self._money(facility.outstanding_amount),
            "outstanding_balance": self._money(facility.outstanding_amount),
            "recovered_amount": self._money(repaid_cash),
            "recovery_collected_amount": self._money(recovery_cash),
            "post_writeoff_recovery_amount": self._money(post_writeoff_recovery),
            "written_off_amount": self._money(written_off),
            "realized_loss": self._money(written_off),
            "net_loss": self._money(written_off - post_writeoff_recovery),
            "arrears_amount": self._money(
                sum(
                    (Decimal(item.amount) - Decimal(item.paid_amount) for item in arrears),
                    Decimal("0.00"),
                )
            ),
            "settlement_classification": (
                settlement_classification(facility.closure_reason)
                if facility.status == FacilityStatus.CLOSED.value
                else None
            ),
            "default_history": [self._serialize_default(item) for item in default_history],
            "currency": facility.currency,
            "status": facility.status,
            "version": facility.version,
            "current_schedule_version": facility.current_schedule_version,
            "closure_reason": facility.closure_reason,
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
            "allowed_actions": self._allowed_actions(
                facility,
                payments,
                user,
                arrears=bool(arrears),
                default_history=default_history,
                writeoff_remaining=(
                    written_off - post_writeoff_recovery
                    if writeoff_event is not None
                    else Decimal("0.00")
                ),
            ),
            "installments": [
                {
                    "installment_id": str(item.installment_id),
                    "sequence": item.sequence,
                    "schedule_version": item.schedule_version,
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
            "delinquencies": [
                {
                    "delinquency_id": str(item.delinquency_id),
                    "marked_by_user_id": str(item.marked_by_user_id),
                    "days_past_due": item.days_past_due,
                    "reason_code": item.reason_code,
                    "comment": item.comment,
                    "evidence_sha256": item.evidence_sha256,
                    "recorded_at": canonical_timestamp(item.recorded_at),
                }
                for item in delinquencies
            ],
            "restructures": [
                {
                    "restructure_id": str(item.restructure_id),
                    "restructured_by_user_id": str(
                        item.restructured_by_user_id
                    ),
                    "old_schedule_version": item.old_schedule_version,
                    "new_schedule_version": item.new_schedule_version,
                    "reason_code": item.reason_code,
                    "comment": item.comment,
                    "evidence_sha256": item.evidence_sha256,
                    "recorded_at": canonical_timestamp(item.recorded_at),
                }
                for item in restructures
            ],
            "default_event": (
                self._serialize_default(default_event)
                if default_event is not None
                else None
            ),
            "writeoff_event": (
                {
                    "writeoff_id": str(writeoff_event.writeoff_id),
                    "auditor_user_id": str(writeoff_event.auditor_user_id),
                    "amount": self._money(writeoff_event.amount),
                    "reason_code": writeoff_event.reason_code,
                    "comment": writeoff_event.comment,
                    "evidence_sha256": writeoff_event.evidence_sha256,
                    "recorded_at": canonical_timestamp(writeoff_event.recorded_at),
                }
                if writeoff_event is not None
                else None
            ),
            "recoveries": [
                {
                    "recovery_id": str(item.recovery_id),
                    "amount": self._money(item.amount),
                    "applied_to": item.applied_to,
                    "source": item.source,
                    "recovery_reference": item.recovery_reference,
                    "evidence_sha256": item.evidence_sha256,
                    "recorded_by_user_id": str(item.recorded_by_user_id),
                    "recorded_at": canonical_timestamp(item.recorded_at),
                }
                for item in recoveries
            ],
            "lifecycle_decisions": [
                {
                    "decision_id": str(item.decision_id),
                    "decision_type": item.decision_type,
                    "schedule_version": item.schedule_version,
                    "decided_by_user_id": str(item.decided_by_user_id),
                    "reason_code": item.reason_code,
                    "comment": item.comment,
                    "evidence_sha256": item.evidence_sha256,
                    "recorded_at": canonical_timestamp(item.recorded_at),
                }
                for item in decisions
            ],
            "contract_versions": [
                {
                    "contract_version": item.contract_version,
                    "origin": item.origin,
                    "principal": self._money(item.principal),
                    "outstanding_at_start": (
                        self._money(item.outstanding_at_start)
                        if item.outstanding_at_start is not None
                        else None
                    ),
                    "currency": item.currency,
                    "schedule": item.schedule,
                    "superseded_schedule": item.superseded_schedule,
                    "restructure_id": (
                        str(item.restructure_id) if item.restructure_id else None
                    ),
                    "terms_sha256": item.terms_sha256,
                    "recorded_at": canonical_timestamp(item.recorded_at),
                }
                for item in contract_versions
            ],
            "status_history": [
                {
                    "from_status": item.from_status,
                    "to_status": item.to_status,
                    "trigger_action": item.trigger_action,
                    "actor_role": item.actor_role,
                    "resulting_version": item.resulting_version,
                    "reason_code": item.reason_code,
                    "recorded_at": canonical_timestamp(item.recorded_at),
                }
                for item in transitions
            ],
        }

    def _load_related_batch(
        self,
        session: Session,
        facilities: list[FinancingFacilityModel],
    ) -> dict[str, Any]:
        facility_ids = [item.facility_id for item in facilities]

        def grouped(rows) -> dict[uuid.UUID, list[Any]]:
            result: defaultdict[uuid.UUID, list[Any]] = defaultdict(list)
            for row in rows:
                result[row.facility_id].append(row)
            return dict(result)

        defaults = self.repository.list_defaults_batch(session, facility_ids)
        writeoffs = self.repository.list_writeoffs_batch(session, facility_ids)
        return {
            "installments": grouped(
                self.repository.list_installments_batch(session, facility_ids)
            ),
            "payments": grouped(
                self.repository.list_payments_batch(session, facility_ids)
            ),
            "delinquencies": grouped(
                self.repository.list_delinquencies_batch(session, facility_ids)
            ),
            "restructures": grouped(
                self.repository.list_restructures_batch(session, facility_ids)
            ),
            "defaults": grouped(defaults),
            "writeoffs": {row.facility_id: row for row in writeoffs},
            "recoveries": grouped(
                self.repository.list_recoveries_batch(session, facility_ids)
            ),
            "contract_versions": grouped(
                self.repository.list_contract_versions_batch(session, facility_ids)
            ),
            "transitions": grouped(
                self.repository.list_transitions_batch(session, facility_ids)
            ),
            "decisions": grouped(
                self.repository.list_lifecycle_decisions_batch(session, facility_ids)
            ),
        }

    @staticmethod
    def _allowed_actions(
        facility: FinancingFacilityModel,
        payments: list[PaymentModel],
        user: AuthenticatedUser,
        *,
        arrears: bool,
        default_history: list[FacilityDefaultModel],
        writeoff_remaining: Decimal,
    ) -> list[str]:
        """Commands the current role can issue now, given state and preconditions."""

        status = FacilityStatus(facility.status)
        pending = any(item.status == PaymentStatus.SUBMITTED.value for item in payments)
        payment_states = {
            FacilityStatus.ACTIVE,
            FacilityStatus.OVERDUE,
            FacilityStatus.IN_DISPOSAL,
            FacilityStatus.RESTRUCTURED,
            FacilityStatus.DEFAULTED,
            FacilityStatus.IN_RECOVERY,
        }
        defaulted_on_current = any(
            item.schedule_version == facility.current_schedule_version
            for item in default_history
        )
        actions: list[FacilityAction] = []
        if user.role == Role.FINANCIER.value:
            if status == FacilityStatus.READY:
                actions.append(FacilityAction.INITIATE_DISBURSEMENT)
            elif status == FacilityStatus.DISBURSED:
                actions.append(FacilityAction.CONFIRM_DISBURSEMENT)
            if status in {FacilityStatus.ACTIVE, FacilityStatus.RESTRUCTURED}:
                actions.append(FacilityAction.MARK_OVERDUE)
            if status in payment_states and pending:
                actions.extend(
                    [FacilityAction.CONFIRM_PAYMENT, FacilityAction.REJECT_PAYMENT]
                )
            if (
                status == FacilityStatus.IN_RECOVERY
                and not pending
                and Decimal(facility.outstanding_amount) > 0
            ) or (status == FacilityStatus.WRITTEN_OFF and writeoff_remaining > 0):
                actions.append(FacilityAction.RECORD_RECOVERY)
        elif user.role == Role.SUPPLIER.value:
            if status in payment_states:
                actions.append(FacilityAction.SUBMIT_PAYMENT)
        elif user.role == Role.RISK_MANAGER.value:
            if status == FacilityStatus.OVERDUE and arrears:
                actions.append(FacilityAction.OPEN_DISPOSAL)
            elif status == FacilityStatus.IN_DISPOSAL:
                if not arrears:
                    actions.append(FacilityAction.CLOSE_DISPOSAL)
                if not pending:
                    actions.append(FacilityAction.RESTRUCTURE)
                if not defaulted_on_current:
                    actions.append(FacilityAction.DECLARE_DEFAULT)
            elif status == FacilityStatus.DEFAULTED:
                if not pending:
                    actions.append(FacilityAction.RESTRUCTURE)
                if defaulted_on_current:
                    actions.append(FacilityAction.START_RECOVERY)
        elif user.role == Role.AUDITOR.value:
            if (
                status == FacilityStatus.IN_RECOVERY
                and not pending
                and Decimal(facility.outstanding_amount) > 0
            ):
                actions.append(FacilityAction.WRITE_OFF)
            if status in {
                FacilityStatus.REPAID,
                FacilityStatus.RECOVERED,
                FacilityStatus.WRITTEN_OFF,
            }:
                actions.append(FacilityAction.CLOSE)
        return [action.value for action in actions]

    @staticmethod
    def _serialize_default(item: FacilityDefaultModel) -> dict[str, Any]:
        return {
            "default_id": str(item.default_id),
            "schedule_version": item.schedule_version,
            "declared_by_user_id": str(item.declared_by_user_id),
            "defaulted_at": canonical_timestamp(item.defaulted_at),
            "days_past_due": item.days_past_due,
            "reason_code": item.reason_code,
            "comment": item.comment,
            "evidence_sha256": item.evidence_sha256,
            "recorded_at": canonical_timestamp(item.recorded_at),
        }

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
