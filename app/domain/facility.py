from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from app.domain.workflow import Role


class FacilityStatus(StrEnum):
    READY = "ready_for_disbursement"
    DISBURSED = "disbursed"
    ACTIVE = "active"
    OVERDUE = "overdue"
    IN_DISPOSAL = "in_disposal"
    RESTRUCTURED = "restructured"
    DEFAULTED = "defaulted"
    IN_RECOVERY = "in_recovery"
    REPAID = "repaid"
    RECOVERED = "recovered"
    WRITTEN_OFF = "written_off"
    CLOSED = "closed"


class InstallmentStatus(StrEnum):
    SCHEDULED = "scheduled"
    PARTIALLY_PAID = "partially_paid"
    PAID = "paid"
    OVERDUE = "overdue"
    SUPERSEDED = "superseded"


class PaymentStatus(StrEnum):
    SUBMITTED = "submitted"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class FacilityAction(StrEnum):
    CREATE = "create"
    INITIATE_DISBURSEMENT = "initiate_disbursement"
    CONFIRM_DISBURSEMENT = "confirm_disbursement"
    SUBMIT_PAYMENT = "submit_payment"
    CONFIRM_PAYMENT = "confirm_payment"
    REJECT_PAYMENT = "reject_payment"
    CONFIRM_FINAL_PAYMENT = "confirm_final_payment"
    MARK_OVERDUE = "mark_overdue"
    # A system consequence of a confirmed payment that clears every arrear; it
    # is audited as its own transition but never issued as a separate command.
    CURE_OVERDUE = "cure_overdue"
    OPEN_DISPOSAL = "open_disposal"
    CLOSE_DISPOSAL = "close_disposal"
    RESTRUCTURE = "restructure"
    DECLARE_DEFAULT = "declare_default"
    START_RECOVERY = "start_recovery"
    RECORD_RECOVERY = "record_recovery"
    RECORD_FINAL_RECOVERY = "record_final_recovery"
    WRITE_OFF = "write_off"
    CLOSE = "close"


class RecoverySource(StrEnum):
    GUARANTOR = "GUARANTOR"
    COLLATERAL = "COLLATERAL"
    CORE_ENTERPRISE_BUYBACK = "CORE_ENTERPRISE_BUYBACK"
    LEGAL_ENFORCEMENT = "LEGAL_ENFORCEMENT"
    COLLECTION_AGENCY = "COLLECTION_AGENCY"
    INSURANCE = "INSURANCE"
    OTHER = "OTHER"


class InvalidFacilityTransition(Exception):
    """Raised when a role cannot perform an action from the current state."""


CENT = Decimal("0.01")


def exact_money(value: Decimal) -> Decimal:
    """Validate and normalize a positive monetary value to exact cents."""

    try:
        normalized = value.quantize(CENT)
    except (InvalidOperation, ValueError) as error:
        raise ValueError("money must be finite and expressed as exact cents") from error
    if not value.is_finite() or value <= 0 or value != normalized:
        raise ValueError("money must be positive with at most two decimal places")
    return normalized


@dataclass(frozen=True)
class LifecycleFacts:
    """Immutable history facts that decide where a resolved transition lands.

    A default is never erased by a zero balance: once any default episode
    exists, settlement lands in ``recovered`` rather than ordinary ``repaid``.
    Returning to performing lands in ``restructured`` whenever the facility
    already runs on a replacement contract version.
    """

    has_default_history: bool = False
    schedule_version: int = 1


class _Resolved(StrEnum):
    PERFORMING = "performing"
    SETTLED = "settled"


_Target = FacilityStatus | _Resolved

_PAYMENT_STATES = (
    FacilityStatus.ACTIVE,
    FacilityStatus.OVERDUE,
    FacilityStatus.IN_DISPOSAL,
    FacilityStatus.RESTRUCTURED,
    FacilityStatus.DEFAULTED,
    FacilityStatus.IN_RECOVERY,
)
_DEFAULT_STAGE_STATES = (FacilityStatus.DEFAULTED, FacilityStatus.IN_RECOVERY)


def _build_transitions() -> dict[tuple[FacilityStatus, FacilityAction, Role], _Target]:
    table: dict[tuple[FacilityStatus, FacilityAction, Role], _Target] = {
        (FacilityStatus.READY, FacilityAction.INITIATE_DISBURSEMENT, Role.FINANCIER): (
            FacilityStatus.DISBURSED
        ),
        (FacilityStatus.DISBURSED, FacilityAction.CONFIRM_DISBURSEMENT, Role.FINANCIER): (
            FacilityStatus.ACTIVE
        ),
        (FacilityStatus.ACTIVE, FacilityAction.MARK_OVERDUE, Role.FINANCIER): (
            FacilityStatus.OVERDUE
        ),
        (FacilityStatus.RESTRUCTURED, FacilityAction.MARK_OVERDUE, Role.FINANCIER): (
            FacilityStatus.OVERDUE
        ),
        (FacilityStatus.OVERDUE, FacilityAction.CURE_OVERDUE, Role.FINANCIER): (
            _Resolved.PERFORMING
        ),
        (FacilityStatus.OVERDUE, FacilityAction.OPEN_DISPOSAL, Role.RISK_MANAGER): (
            FacilityStatus.IN_DISPOSAL
        ),
        (FacilityStatus.IN_DISPOSAL, FacilityAction.CLOSE_DISPOSAL, Role.RISK_MANAGER): (
            _Resolved.PERFORMING
        ),
        (FacilityStatus.IN_DISPOSAL, FacilityAction.RESTRUCTURE, Role.RISK_MANAGER): (
            FacilityStatus.RESTRUCTURED
        ),
        (FacilityStatus.DEFAULTED, FacilityAction.RESTRUCTURE, Role.RISK_MANAGER): (
            FacilityStatus.RESTRUCTURED
        ),
        (FacilityStatus.IN_DISPOSAL, FacilityAction.DECLARE_DEFAULT, Role.RISK_MANAGER): (
            FacilityStatus.DEFAULTED
        ),
        (FacilityStatus.DEFAULTED, FacilityAction.START_RECOVERY, Role.RISK_MANAGER): (
            FacilityStatus.IN_RECOVERY
        ),
        (FacilityStatus.IN_RECOVERY, FacilityAction.RECORD_RECOVERY, Role.FINANCIER): (
            FacilityStatus.IN_RECOVERY
        ),
        (FacilityStatus.WRITTEN_OFF, FacilityAction.RECORD_RECOVERY, Role.FINANCIER): (
            FacilityStatus.WRITTEN_OFF
        ),
        (FacilityStatus.IN_RECOVERY, FacilityAction.RECORD_FINAL_RECOVERY, Role.FINANCIER): (
            FacilityStatus.RECOVERED
        ),
        (FacilityStatus.IN_RECOVERY, FacilityAction.WRITE_OFF, Role.AUDITOR): (
            FacilityStatus.WRITTEN_OFF
        ),
        (FacilityStatus.REPAID, FacilityAction.CLOSE, Role.AUDITOR): FacilityStatus.CLOSED,
        (FacilityStatus.RECOVERED, FacilityAction.CLOSE, Role.AUDITOR): FacilityStatus.CLOSED,
        (FacilityStatus.WRITTEN_OFF, FacilityAction.CLOSE, Role.AUDITOR): FacilityStatus.CLOSED,
    }
    for status in _PAYMENT_STATES:
        table[(status, FacilityAction.SUBMIT_PAYMENT, Role.SUPPLIER)] = status
        table[(status, FacilityAction.CONFIRM_PAYMENT, Role.FINANCIER)] = status
        table[(status, FacilityAction.REJECT_PAYMENT, Role.FINANCIER)] = status
        table[(status, FacilityAction.CONFIRM_FINAL_PAYMENT, Role.FINANCIER)] = (
            FacilityStatus.RECOVERED if status in _DEFAULT_STAGE_STATES else _Resolved.SETTLED
        )
    return table


_TRANSITIONS = _build_transitions()


def _resolve(target: _Target, facts: LifecycleFacts) -> FacilityStatus:
    if target == _Resolved.PERFORMING:
        return (
            FacilityStatus.RESTRUCTURED
            if facts.schedule_version > 1
            else FacilityStatus.ACTIVE
        )
    if target == _Resolved.SETTLED:
        return (
            FacilityStatus.RECOVERED
            if facts.has_default_history
            else FacilityStatus.REPAID
        )
    assert isinstance(target, FacilityStatus)
    return target


def derive_closure_reason(*, has_default: bool, has_writeoff: bool) -> str:
    if has_writeoff:
        if not has_default:
            raise ValueError("write-off requires default history")
        return "written_off"
    return "settled_after_default" if has_default else "repaid"


def settlement_classification(closure_reason: str | None) -> str | None:
    """Keep ordinary repayment, recovery after default and write-off distinct."""

    return {
        "repaid": "NORMAL_SETTLED",
        "settled_after_default": "SETTLED_AFTER_DEFAULT",
        "written_off": "WRITTEN_OFF",
    }.get(closure_reason or "")


def next_facility_status(
    current: FacilityStatus,
    action: FacilityAction,
    role: Role,
    *,
    facts: LifecycleFacts | None = None,
) -> FacilityStatus:
    try:
        target = _TRANSITIONS[(current, action, role)]
    except KeyError as error:
        raise InvalidFacilityTransition(
            f"{role.value} cannot {action.value} from {current.value}"
        ) from error
    return _resolve(target, facts or LifecycleFacts())


def allowed_status_changes() -> frozenset[tuple[FacilityStatus, FacilityStatus]]:
    """Every concrete (from, to) pair the lifecycle can produce, for DB guards."""

    pairs: set[tuple[FacilityStatus, FacilityStatus]] = set()
    fact_space = [
        LifecycleFacts(has_default_history=default, schedule_version=version)
        for default in (False, True)
        for version in (1, 2)
    ]
    for (current, _action, _role), target in _TRANSITIONS.items():
        for facts in fact_space:
            # Every default either ends in recovery/write-off or restructures to
            # a new contract version, so ``active`` never carries default history.
            if current == FacilityStatus.ACTIVE and facts.has_default_history:
                continue
            resolved = _resolve(target, facts)
            if resolved != current:
                pairs.add((current, resolved))
    return frozenset(pairs)


@dataclass(frozen=True)
class StageDefinition:
    status: FacilityStatus
    label_zh: str
    label_en: str
    entry_conditions: tuple[str, ...]
    terminal: bool = False


STAGE_CATALOG: tuple[StageDefinition, ...] = (
    StageDefinition(
        FacilityStatus.READY,
        "已授信待放款",
        "Credit granted, awaiting disbursement",
        (
            "application is audited with an approved decision",
            "principal equals the approved application amount",
            "repayment schedule sums exactly to principal",
            "one facility per application",
        ),
    ),
    StageDefinition(
        FacilityStatus.DISBURSED,
        "放款处理中",
        "Disbursement initiated",
        ("financier initiated disbursement from ready_for_disbursement",),
    ),
    StageDefinition(
        FacilityStatus.ACTIVE,
        "正常还款",
        "Performing",
        (
            "disbursement confirmed by the financier",
            "or every past-due current-schedule installment has been paid (cure)",
            "or risk disposal closed with no arrears on the original contract",
        ),
    ),
    StageDefinition(
        FacilityStatus.OVERDUE,
        "逾期",
        "Overdue",
        (
            "a current-schedule installment is past its due date and not fully paid",
            "financier recorded days-past-due evidence",
        ),
    ),
    StageDefinition(
        FacilityStatus.IN_DISPOSAL,
        "风险处置",
        "Risk disposal (workout)",
        (
            "facility is overdue",
            "governed delinquency evidence exists for the current contract version",
            "risk manager opened disposal with a reason code and evidence hash",
        ),
    ),
    StageDefinition(
        FacilityStatus.RESTRUCTURED,
        "重组履约",
        "Restructured, performing",
        (
            "entered from risk disposal or default by the risk manager",
            "replacement schedule equals the exact outstanding balance",
            "replacement installments are due in the future",
            "no payment decision is pending",
            "prior contract version stays immutable; a new version is appended",
        ),
    ),
    StageDefinition(
        FacilityStatus.DEFAULTED,
        "违约",
        "Defaulted",
        (
            "entered only from risk disposal",
            "governed delinquency evidence exists",
            "at most one default episode per contract version",
            "default is an immutable event, independent of the balance",
        ),
    ),
    StageDefinition(
        FacilityStatus.IN_RECOVERY,
        "追偿",
        "Recovery",
        (
            "facility is defaulted on its current contract version",
            "risk manager started recovery with a reason code and evidence hash",
        ),
    ),
    StageDefinition(
        FacilityStatus.REPAID,
        "已还清",
        "Repaid",
        ("outstanding balance reached zero through repayment with no default history",),
    ),
    StageDefinition(
        FacilityStatus.RECOVERED,
        "追偿完毕",
        "Recovered after default",
        (
            "outstanding balance reached zero after at least one default episode",
            "a zero balance never erases the default history",
        ),
    ),
    StageDefinition(
        FacilityStatus.WRITTEN_OFF,
        "已核销",
        "Written off",
        (
            "facility is in recovery with a default on record",
            "remaining balance is positive and no payment decision is pending",
            "auditor approved the write-off with evidence",
            "the claim survives: post-write-off recoveries remain recordable",
        ),
    ),
    StageDefinition(
        FacilityStatus.CLOSED,
        "已关闭",
        "Closed",
        (
            "status is repaid, recovered, or written_off",
            "outstanding balance is exactly zero",
            "audit ledger verifies",
            "closure reason is derived from immutable history, never supplied",
        ),
        terminal=True,
    ),
)


def allowed_actions_by_role(status: FacilityStatus) -> dict[str, list[str]]:
    """Commands each role may issue from ``status`` (system actions excluded)."""

    result: dict[str, list[str]] = {}
    for (current, action, role) in _TRANSITIONS:
        if current != status or action == FacilityAction.CURE_OVERDUE:
            continue
        result.setdefault(role.value, [])
        if action.value not in result[role.value]:
            result[role.value].append(action.value)
    return {role: sorted(actions) for role, actions in sorted(result.items())}


def state_machine_definition() -> dict[str, object]:
    stages = []
    for stage in STAGE_CATALOG:
        stages.append(
            {
                "status": stage.status.value,
                "label_zh": stage.label_zh,
                "label_en": stage.label_en,
                "entry_conditions": list(stage.entry_conditions),
                "allowed_actions": allowed_actions_by_role(stage.status),
                "terminal": stage.terminal,
            }
        )
    return {
        "stages": stages,
        "transitions": sorted(
            [
                {"from": current.value, "to": target.value}
                for current, target in allowed_status_changes()
            ],
            key=lambda item: (item["from"], item["to"]),
        ),
        "system_transitions": [
            {
                "action": FacilityAction.CURE_OVERDUE.value,
                "trigger": "confirmed payment clears every past-due current-schedule installment",
            }
        ],
    }
