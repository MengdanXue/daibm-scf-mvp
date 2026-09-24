"""Risk operations: versioned rules, rule-based detection and the alert lifecycle.

Every alert is raised by a rule over data the platform already recorded (risk
decisions, delinquencies, payments, lifecycle transitions, outcome reviews),
at most once per (rule, source record). Every change to an alert advances its
version, writes an immutable ``risk_alert_events`` row and a ledger event.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, func, select, text
from sqlalchemy.orm import Session, aliased, sessionmaker

from app.domain.risk_operations import (
    ADMIN,
    ALERT_TRANSITIONS,
    DEFAULT_RULES,
    AlertStatus,
)
from app.identity import AuthenticatedUser
from app.ledger import canonical_timestamp
from app.models import FinancingRequestModel
from app.models_facility import FinancingFacilityModel, PaymentModel
from app.models_identity import UserModel
from app.models_lifecycle import FacilityDelinquencyModel, FacilityStatusTransitionModel
from app.models_model_governance import OutcomeReviewEventModel, RiskDecisionRecordModel
from app.models_outcome import ActualOutcomeModel
from app.models_risk_ops import RiskAlertEventModel, RiskAlertModel, RiskRuleModel
from app.repositories.ledger import LedgerRepository
from app.services.permissions import PermissionService

DATA_QUALITY_REASONS = (
    "DATA_QUALITY_INSUFFICIENT",
    "BUSINESS_INCONSISTENT",
    "BUSINESS_EXCEPTION",
    "SCOPE_MISMATCH",
    "QUALITY_ANOMALY",
)
OWNER_ROLES = frozenset({ADMIN, "risk_manager"})


class RiskOpsError(Exception):
    pass


class RiskOpsNotFound(RiskOpsError):
    pass


class RiskOpsForbidden(RiskOpsError):
    pass


class RiskOpsConflict(RiskOpsError):
    pass


def require_role(user: AuthenticatedUser, action: str, message: str) -> None:
    """Role check through the central permission table."""

    if not PermissionService.allowed(user, action):
        raise RiskOpsForbidden(message)


def org_condition(session: Session, user: AuthenticatedUser, column: Any):
    """Tenant filter for an ``organization_id`` column, from the permission layer."""

    return PermissionService.organization_condition(column, PermissionService.scope(session, user))


def sees_everything(session: Session, user: AuthenticatedUser) -> bool:
    return PermissionService.scope(session, user).everything


def parse_uuid(value: str | uuid.UUID) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except ValueError as error:
        raise RiskOpsNotFound(str(value)) from error


def ts(value: datetime | None) -> str | None:
    return canonical_timestamp(value) if value is not None else None


def usernames(session: Session, ids: set[uuid.UUID | None]) -> dict[uuid.UUID, str]:
    wanted = [item for item in ids if item is not None]
    if not wanted:
        return {}
    return {
        row.user_id: row.username
        for row in session.scalars(select(UserModel).where(UserModel.user_id.in_(wanted)))
    }


def facility_organization(session: Session, facility_id: uuid.UUID) -> uuid.UUID | None:
    """The bank organization that owns a facility (its creator's organization)."""

    return session.scalar(
        select(UserModel.organization_id)
        .join(FinancingFacilityModel, FinancingFacilityModel.created_by_user_id == UserModel.user_id)
        .where(FinancingFacilityModel.facility_id == facility_id)
    )


# --- Rules ----------------------------------------------------------------------------


@dataclass(frozen=True)
class RuleVersion:
    rule_key: str
    version: int
    risk_type: str
    threshold: Decimal | None
    severity: str
    enabled: bool


def current_rules(session: Session, now: datetime) -> dict[str, RiskRuleModel]:
    """The latest version of every rule that is already in effect."""

    latest = (
        select(RiskRuleModel.rule_key, func.max(RiskRuleModel.version).label("version"))
        .where(RiskRuleModel.effective_from <= now)
        .group_by(RiskRuleModel.rule_key)
        .subquery()
    )
    return {
        rule.rule_key: rule
        for rule in session.scalars(
            select(RiskRuleModel).join(
                latest,
                and_(
                    latest.c.rule_key == RiskRuleModel.rule_key,
                    latest.c.version == RiskRuleModel.version,
                ),
            )
        )
    }


def seed_default_rules(session: Session) -> None:
    """Ensure version 1 of every default rule exists (idempotent, like demo accounts)."""

    from sqlalchemy.dialects.postgresql import insert

    session.execute(
        insert(RiskRuleModel)
        .values(
            [
                {
                    "rule_key": key,
                    "version": 1,
                    "risk_type": risk_type,
                    "threshold": Decimal(threshold) if threshold is not None else None,
                    "severity": severity,
                    "enabled": True,
                    "description": description,
                    "effective_from": datetime(2000, 1, 1, tzinfo=timezone.utc),
                    "created_by": "migration_seed",
                    "change_reason": "initial_rule_set",
                }
                for key, risk_type, threshold, severity, description in DEFAULT_RULES
            ]
        )
        .on_conflict_do_nothing(index_elements=["rule_key", "version"])
    )


def serialize_rule(rule: RiskRuleModel) -> dict[str, Any]:
    return {
        "rule_key": rule.rule_key,
        "version": rule.version,
        "risk_type": rule.risk_type,
        "threshold": str(rule.threshold) if rule.threshold is not None else None,
        "severity": rule.severity,
        "enabled": rule.enabled,
        "description": rule.description,
        "effective_from": ts(rule.effective_from),
        "created_by": rule.created_by,
        "change_reason": rule.change_reason,
        "created_at": ts(rule.created_at),
    }


class RiskRuleService:
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

    def list_rules(self, user: AuthenticatedUser) -> dict[str, Any]:
        require_role(user, "rule:read", "Current role cannot view risk rules")
        now = self.clock()
        with self.session_factory.begin() as session:
            seed_default_rules(session)
            current = current_rules(session, now)
            history = list(
                session.scalars(
                    select(RiskRuleModel).order_by(
                        RiskRuleModel.rule_key, RiskRuleModel.version.desc()
                    )
                )
            )
            return {
                "rules": [serialize_rule(rule) for rule in sorted(current.values(), key=lambda r: r.rule_key)],
                "pending": [
                    serialize_rule(rule)
                    for rule in history
                    if rule.effective_from > now
                ],
                "history": [serialize_rule(rule) for rule in history],
            }

    def change_rule(
        self,
        rule_key: str,
        user: AuthenticatedUser,
        *,
        threshold: Decimal | None,
        severity: str,
        enabled: bool,
        effective_from: datetime | None,
        change_reason: str,
        expected_version: int,
    ) -> dict[str, Any]:
        """Every change is a new immutable version; nothing is edited in place."""

        require_role(user, "rule:write", "Only administrators can change risk rules")
        now = self.clock()
        with self.session_factory.begin() as session:
            session.execute(text("SELECT pg_advisory_xact_lock(hashtext('risk-rules'))"))
            seed_default_rules(session)
            latest = session.scalar(
                select(RiskRuleModel)
                .where(RiskRuleModel.rule_key == rule_key)
                .order_by(RiskRuleModel.version.desc())
                .limit(1)
            )
            if latest is None:
                raise RiskOpsNotFound(rule_key)
            if latest.version != expected_version:
                raise RiskOpsConflict("Rule changed since it was read; refresh and retry")
            if (latest.threshold is None) != (threshold is None):
                raise RiskOpsConflict("This rule's threshold cannot be added or removed")
            rule = RiskRuleModel(
                rule_key=rule_key,
                version=latest.version + 1,
                risk_type=latest.risk_type,
                threshold=threshold,
                severity=severity,
                enabled=enabled,
                description=latest.description,
                effective_from=effective_from or now,
                created_by_user_id=user.user_id,
                created_by=user.username,
                change_reason=change_reason,
            )
            session.add(rule)
            session.flush()
            self.ledger_repository.append_many(
                session,
                uuid.uuid5(uuid.NAMESPACE_URL, f"daibm-scf:risk-rule:{rule_key}"),
                [
                    (
                        "RISK_RULE_VERSIONED",
                        {
                            "rule_key": rule_key,
                            "version": rule.version,
                            "previous": serialize_rule(latest),
                            "threshold": str(threshold) if threshold is not None else None,
                            "severity": severity,
                            "enabled": enabled,
                            "effective_from": ts(rule.effective_from),
                            "change_reason": change_reason,
                            "changed_by_user_id": str(user.user_id),
                        },
                    )
                ],
            )
            return serialize_rule(rule)


    def rollback_rule(
        self,
        rule_key: str,
        user: AuthenticatedUser,
        *,
        to_version: int,
        change_reason: str,
        expected_version: int,
    ) -> dict[str, Any]:
        """Re-issue an earlier version's settings as a new version (history is kept)."""

        require_role(user, "rule:write", "Only administrators can change risk rules")
        with self.session_factory() as session:
            target = session.get(RiskRuleModel, (rule_key, to_version))
            if target is None:
                raise RiskOpsNotFound(f"{rule_key} v{to_version}")
            threshold, severity, enabled = target.threshold, target.severity, target.enabled
        return self.change_rule(
            rule_key,
            user,
            threshold=threshold,
            severity=severity,
            enabled=enabled,
            effective_from=None,
            change_reason=f"rollback_to_v{to_version}: {change_reason}",
            expected_version=expected_version,
        )


# --- Detection -------------------------------------------------------------------------


@dataclass(frozen=True)
class Detection:
    rule: RiskRuleModel
    severity: str
    source_type: str
    source_ref: str
    facility_id: uuid.UUID | None
    request_id: uuid.UUID | None
    organization_id: uuid.UUID | None
    trigger_reason: str
    evidence: dict[str, Any]


class RiskDetectionService:
    """Apply the rules in effect to recorded business data; idempotent."""

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

    def scan_as(self, user: AuthenticatedUser) -> dict[str, Any]:
        require_role(user, "alert:scan", "Current role cannot run risk detection")
        return self.scan(actor=user)

    def scan(self, actor: AuthenticatedUser | None = None) -> dict[str, Any]:
        now = self.clock()
        with self.session_factory.begin() as session:
            session.execute(text("SELECT pg_advisory_xact_lock(hashtext('risk-scan'))"))
            seed_default_rules(session)
            rules = {
                key: rule for key, rule in current_rules(session, now).items() if rule.enabled
            }
            existing = {
                (row.rule_key, row.source_ref)
                for row in session.execute(select(RiskAlertModel.rule_key, RiskAlertModel.source_ref))
            }
            detections: list[Detection] = []
            for key, detect in (
                ("MODEL_SCORE_THRESHOLD", self._model_scores),
                ("OVERDUE_DAYS", self._overdue),
                ("REPAYMENT_ANOMALY", self._repayment_anomalies),
                ("LIFECYCLE_ANOMALY", self._lifecycle),
                ("DATA_QUALITY", self._data_quality),
            ):
                if key in rules:
                    detections.extend(
                        item
                        for item in detect(session, rules[key])
                        if (key, item.source_ref) not in existing
                    )
            created = [self._create(session, item, now, actor) for item in detections]
            return {
                "created": len(created),
                "alert_ids": [str(item) for item in created],
                "rules_evaluated": sorted(rules),
                "scanned_at": ts(now),
            }

    def _create(
        self,
        session: Session,
        detection: Detection,
        now: datetime,
        actor: AuthenticatedUser | None,
    ) -> uuid.UUID:
        alert = RiskAlertModel(
            alert_id=uuid.uuid4(),
            rule_key=detection.rule.rule_key,
            rule_version=detection.rule.version,
            risk_type=detection.rule.risk_type,
            severity=detection.severity,
            source_type=detection.source_type,
            source_ref=detection.source_ref,
            facility_id=detection.facility_id,
            request_id=detection.request_id,
            organization_id=detection.organization_id,
            trigger_reason=detection.trigger_reason,
            evidence=detection.evidence,
            status=AlertStatus.OPEN.value,
            owner_user_id=None,
            version=1,
            created_at=now,
            updated_at=now,
        )
        session.add(alert)
        session.flush()
        session.add(
            RiskAlertEventModel(
                alert_id=alert.alert_id,
                action="CREATED",
                from_status=None,
                to_status=AlertStatus.OPEN.value,
                resulting_version=1,
                actor_user_id=actor.user_id if actor else None,
                actor_role=actor.role if actor else "system",
                comment=detection.trigger_reason,
                payload={"rule_version": detection.rule.version, **detection.evidence},
            )
        )
        session.flush()
        self.ledger_repository.append_many(
            session,
            alert.alert_id,
            [
                (
                    "RISK_ALERT_CREATED",
                    {
                        "alert_id": str(alert.alert_id),
                        "rule_key": alert.rule_key,
                        "rule_version": alert.rule_version,
                        "risk_type": alert.risk_type,
                        "severity": alert.severity,
                        "source_type": alert.source_type,
                        "source_ref": alert.source_ref,
                        "facility_id": str(alert.facility_id) if alert.facility_id else None,
                        "request_id": str(alert.request_id) if alert.request_id else None,
                        "trigger_reason": alert.trigger_reason,
                    },
                )
            ],
        )
        return alert.alert_id

    # Each detector reads recorded business facts only.

    @staticmethod
    def _model_scores(session: Session, rule: RiskRuleModel) -> list[Detection]:
        assert rule.threshold is not None
        assessor = aliased(UserModel)
        rows = session.execute(
            select(
                RiskDecisionRecordModel,
                FinancingFacilityModel.facility_id,
                assessor.organization_id,
            )
            .join(assessor, assessor.user_id == RiskDecisionRecordModel.assessed_by_user_id)
            .outerjoin(
                FinancingFacilityModel,
                FinancingFacilityModel.request_id == RiskDecisionRecordModel.request_id,
            )
            .where(RiskDecisionRecordModel.final_score >= float(rule.threshold))
        ).all()
        return [
            Detection(
                rule=rule,
                severity=rule.severity,
                source_type="risk_decision",
                source_ref=str(record.decision_record_id),
                facility_id=facility_id,
                request_id=record.request_id,
                organization_id=organization_id,
                trigger_reason=(
                    f"风险评分 {record.final_score:.4f} ≥ 阈值 {rule.threshold:.4f}（{record.band}）"
                ),
                evidence={
                    "final_score": record.final_score,
                    "band": record.band,
                    "threshold": str(rule.threshold),
                    "decision_record_id": str(record.decision_record_id),
                    "model_version_id": (
                        str(record.model_version_id) if record.model_version_id else None
                    ),
                },
            )
            for record, facility_id, organization_id in rows
        ]

    @staticmethod
    def _overdue(session: Session, rule: RiskRuleModel) -> list[Detection]:
        assert rule.threshold is not None
        rows = session.execute(
            select(FacilityDelinquencyModel, FinancingFacilityModel.request_id, UserModel.organization_id)
            .join(
                FinancingFacilityModel,
                FinancingFacilityModel.facility_id == FacilityDelinquencyModel.facility_id,
            )
            .join(UserModel, UserModel.user_id == FinancingFacilityModel.created_by_user_id)
        ).all()
        threshold = int(rule.threshold)
        return [
            Detection(
                rule=rule,
                severity=rule.severity if item.days_past_due >= threshold else "MEDIUM",
                source_type="delinquency",
                source_ref=str(item.delinquency_id),
                facility_id=item.facility_id,
                request_id=request_id,
                organization_id=organization_id,
                trigger_reason=(
                    f"逾期 {item.days_past_due} 天"
                    + (f"，达到阈值 {threshold} 天" if item.days_past_due >= threshold else "")
                    + f"（{item.reason_code}）"
                ),
                evidence={
                    "days_past_due": item.days_past_due,
                    "threshold_days": threshold,
                    "reason_code": item.reason_code,
                    "recorded_at": ts(item.recorded_at),
                },
            )
            for item, request_id, organization_id in rows
        ]

    @staticmethod
    def _repayment_anomalies(session: Session, rule: RiskRuleModel) -> list[Detection]:
        assert rule.threshold is not None
        threshold = int(rule.threshold)
        rows = session.execute(
            select(
                FinancingFacilityModel.facility_id,
                FinancingFacilityModel.request_id,
                UserModel.organization_id,
                func.count(PaymentModel.payment_id),
            )
            .join(PaymentModel, PaymentModel.facility_id == FinancingFacilityModel.facility_id)
            .join(UserModel, UserModel.user_id == FinancingFacilityModel.created_by_user_id)
            .where(PaymentModel.status == "rejected")
            .group_by(
                FinancingFacilityModel.facility_id,
                FinancingFacilityModel.request_id,
                UserModel.organization_id,
            )
            .having(func.count(PaymentModel.payment_id) >= threshold)
        ).all()
        return [
            Detection(
                rule=rule,
                severity=rule.severity,
                source_type="facility_payments",
                source_ref=f"{facility_id}:{threshold}",
                facility_id=facility_id,
                request_id=request_id,
                organization_id=organization_id,
                trigger_reason=f"{count} 笔还款被拒绝，达到阈值 {threshold}",
                evidence={"rejected_payments": count, "threshold": threshold},
            )
            for facility_id, request_id, organization_id, count in rows
        ]

    @staticmethod
    def _lifecycle(session: Session, rule: RiskRuleModel) -> list[Detection]:
        rows = session.execute(
            select(FacilityStatusTransitionModel, FinancingFacilityModel.request_id, UserModel.organization_id)
            .join(
                FinancingFacilityModel,
                FinancingFacilityModel.facility_id == FacilityStatusTransitionModel.facility_id,
            )
            .join(UserModel, UserModel.user_id == FinancingFacilityModel.created_by_user_id)
            .where(FacilityStatusTransitionModel.to_status.in_(("defaulted", "in_disposal")))
        ).all()
        return [
            Detection(
                rule=rule,
                severity=rule.severity if item.to_status == "defaulted" else "HIGH",
                source_type="facility_transition",
                source_ref=str(item.transition_id),
                facility_id=item.facility_id,
                request_id=request_id,
                organization_id=organization_id,
                trigger_reason=(
                    ("融资进入违约" if item.to_status == "defaulted" else "融资进入风险处置")
                    + f"（{item.from_status or '—'} → {item.to_status}"
                    + (f"，{item.reason_code}" if item.reason_code else "")
                    + "）"
                ),
                evidence={
                    "from_status": item.from_status,
                    "to_status": item.to_status,
                    "trigger_action": item.trigger_action,
                    "reason_code": item.reason_code,
                    "recorded_at": ts(item.recorded_at),
                },
            )
            for item, request_id, organization_id in rows
        ]

    @staticmethod
    def _data_quality(session: Session, rule: RiskRuleModel) -> list[Detection]:
        successor = aliased(ActualOutcomeModel)
        rows = session.execute(
            select(
                OutcomeReviewEventModel,
                ActualOutcomeModel.facility_id,
                ActualOutcomeModel.request_id,
                UserModel.organization_id,
            )
            .join(ActualOutcomeModel, ActualOutcomeModel.outcome_id == OutcomeReviewEventModel.outcome_id)
            .join(
                FinancingFacilityModel,
                FinancingFacilityModel.facility_id == ActualOutcomeModel.facility_id,
            )
            .join(UserModel, UserModel.user_id == FinancingFacilityModel.created_by_user_id)
            .outerjoin(successor, successor.supersedes_outcome_id == ActualOutcomeModel.outcome_id)
            .where(
                OutcomeReviewEventModel.status == "REJECTED",
                OutcomeReviewEventModel.reason_code.in_(DATA_QUALITY_REASONS),
                successor.outcome_id.is_(None),
            )
        ).all()
        return [
            Detection(
                rule=rule,
                severity=rule.severity,
                source_type="outcome_review",
                source_ref=str(event.event_id),
                facility_id=facility_id,
                request_id=request_id,
                organization_id=organization_id,
                trigger_reason=f"业务结果审核拒绝：{event.reason_code}",
                evidence={
                    "outcome_id": str(event.outcome_id),
                    "reason_code": event.reason_code,
                    "comment": event.comment,
                    "recorded_at": ts(event.recorded_at),
                },
            )
            for event, facility_id, request_id, organization_id in rows
        ]


# --- Alert center ------------------------------------------------------------------------


class RiskAlertService:
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

    def list_alerts(
        self,
        user: AuthenticatedUser,
        *,
        status: str | None = None,
        severity: str | None = None,
        facility_id: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        require_role(user, "alert:read", "Current role cannot view risk alerts")
        with self.session_factory() as session:
            statement = self._visible(session, user).order_by(
                RiskAlertModel.created_at.desc(), RiskAlertModel.alert_id
            )
            if status:
                statement = statement.where(RiskAlertModel.status == status)
            if severity:
                statement = statement.where(RiskAlertModel.severity == severity)
            if facility_id:
                statement = statement.where(RiskAlertModel.facility_id == parse_uuid(facility_id))
            alerts = list(session.scalars(statement.limit(limit)))
            names = usernames(session, {item.owner_user_id for item in alerts})
            return [self._serialize(item, names) for item in alerts]

    def get_alert(self, alert_id: str | uuid.UUID, user: AuthenticatedUser) -> dict[str, Any]:
        require_role(user, "alert:read", "Current role cannot view risk alerts")
        with self.session_factory() as session:
            alert = self._load(session, alert_id, user)
            return self._detail(session, alert)

    def assign(
        self, alert_id: str, user: AuthenticatedUser, *, owner_user_id: str, version: int, comment: str | None
    ) -> dict[str, Any]:
        require_role(user, "alert:assign", "Only risk managers and administrators assign alerts")

        def apply(session: Session, alert: RiskAlertModel) -> tuple[str, dict[str, Any]]:
            owner = session.get(UserModel, parse_uuid(owner_user_id))
            if owner is None or owner.role not in OWNER_ROLES:
                raise RiskOpsConflict("An alert owner must be a risk manager or administrator")
            if (
                owner.role != ADMIN
                and alert.organization_id is not None
                and owner.organization_id != alert.organization_id
            ):
                raise RiskOpsConflict("The owner must belong to the alert's organization")
            if alert.status in (AlertStatus.RESOLVED.value, AlertStatus.CLOSED.value):
                raise RiskOpsConflict("A resolved alert cannot be reassigned")
            action = "ASSIGNED" if alert.status == AlertStatus.OPEN.value else "REASSIGNED"
            previous = alert.owner_user_id
            alert.owner_user_id = owner.user_id
            if alert.status == AlertStatus.OPEN.value:
                alert.status = AlertStatus.ASSIGNED.value
            return action, {
                "owner_user_id": str(owner.user_id),
                "owner": owner.username,
                "previous_owner_user_id": str(previous) if previous else None,
            }

        return self._change(alert_id, user, version, comment, apply)

    def start(self, alert_id: str, user: AuthenticatedUser, *, version: int, comment: str | None) -> dict[str, Any]:
        def apply(session: Session, alert: RiskAlertModel) -> tuple[str, dict[str, Any]]:
            self._require_owner(alert, user)
            self._move(alert, AlertStatus.PROCESSING)
            return "STARTED", {}

        return self._change(alert_id, user, version, comment, apply)

    def resolve(
        self, alert_id: str, user: AuthenticatedUser, *, version: int, resolution: str
    ) -> dict[str, Any]:
        def apply(session: Session, alert: RiskAlertModel) -> tuple[str, dict[str, Any]]:
            self._require_owner(alert, user)
            self._move(alert, AlertStatus.RESOLVED)
            alert.resolution = resolution
            alert.resolved_at = self.clock()
            return "RESOLVED", {"resolution": resolution}

        return self._change(alert_id, user, version, resolution, apply)

    def close(self, alert_id: str, user: AuthenticatedUser, *, version: int, comment: str) -> dict[str, Any]:
        require_role(user, "alert:review", "Only auditors and administrators close alerts")

        def apply(session: Session, alert: RiskAlertModel) -> tuple[str, dict[str, Any]]:
            if alert.owner_user_id == user.user_id:
                raise RiskOpsConflict("The alert owner cannot review their own resolution")
            self._move(alert, AlertStatus.CLOSED)
            alert.closed_at = self.clock()
            return "CLOSED", {"resolution": alert.resolution}

        return self._change(alert_id, user, version, comment, apply)

    def reopen(self, alert_id: str, user: AuthenticatedUser, *, version: int, comment: str) -> dict[str, Any]:
        require_role(user, "alert:review", "Only auditors and administrators return resolutions")

        def apply(session: Session, alert: RiskAlertModel) -> tuple[str, dict[str, Any]]:
            rejected = alert.resolution
            self._move(alert, AlertStatus.PROCESSING)
            alert.resolution = None
            alert.resolved_at = None
            return "REOPENED", {"rejected_resolution": rejected}

        return self._change(alert_id, user, version, comment, apply)

    def comment(self, alert_id: str, user: AuthenticatedUser, *, version: int, comment: str) -> dict[str, Any]:
        def apply(session: Session, alert: RiskAlertModel) -> tuple[str, dict[str, Any]]:
            return "COMMENTED", {}

        return self._change(alert_id, user, version, comment, apply)

    # --- helpers -----------------------------------------------------------

    def _change(
        self,
        alert_id: str | uuid.UUID,
        user: AuthenticatedUser,
        version: int,
        comment: str | None,
        apply: Callable[[Session, RiskAlertModel], tuple[str, dict[str, Any]]],
    ) -> dict[str, Any]:
        require_role(user, "alert:read", "Current role cannot change risk alerts")
        with self.session_factory.begin() as session:
            alert = self._load(session, alert_id, user, for_update=True)
            if alert.version != version:
                raise RiskOpsConflict("Alert changed since it was read; refresh and retry")
            before = alert.status
            action, payload = apply(session, alert)
            alert.version += 1
            alert.updated_at = self.clock()
            session.flush()
            session.add(
                RiskAlertEventModel(
                    alert_id=alert.alert_id,
                    action=action,
                    from_status=before,
                    to_status=alert.status,
                    resulting_version=alert.version,
                    actor_user_id=user.user_id,
                    actor_role=user.role,
                    comment=comment,
                    payload=payload,
                )
            )
            session.flush()
            self.ledger_repository.append_many(
                session,
                alert.alert_id,
                [
                    (
                        "RISK_ALERT_TRANSITIONED",
                        {
                            "alert_id": str(alert.alert_id),
                            "action": action,
                            "from_status": before,
                            "to_status": alert.status,
                            "version": alert.version,
                            "actor_user_id": str(user.user_id),
                            "actor_role": user.role,
                            "comment": comment,
                            **payload,
                        },
                    )
                ],
            )
            return self._detail(session, alert)

    @staticmethod
    def _move(alert: RiskAlertModel, target: AlertStatus) -> None:
        if (AlertStatus(alert.status), target) not in ALERT_TRANSITIONS:
            raise RiskOpsConflict(f"Alert is {alert.status}; cannot move to {target.value}")
        alert.status = target.value

    @staticmethod
    def _require_owner(alert: RiskAlertModel, user: AuthenticatedUser) -> None:
        if user.role != ADMIN and alert.owner_user_id != user.user_id:
            raise RiskOpsForbidden("Only the alert owner or an administrator can process it")

    @staticmethod
    def _visible(session: Session, user: AuthenticatedUser):
        return select(RiskAlertModel).where(
            org_condition(session, user, RiskAlertModel.organization_id)
        )

    def _load(
        self,
        session: Session,
        alert_id: str | uuid.UUID,
        user: AuthenticatedUser,
        *,
        for_update: bool = False,
    ) -> RiskAlertModel:
        statement = self._visible(session, user).where(RiskAlertModel.alert_id == parse_uuid(alert_id))
        if for_update:
            statement = statement.with_for_update()
        alert = session.scalar(statement)
        if alert is None:
            raise RiskOpsNotFound(str(alert_id))
        return alert

    def _detail(self, session: Session, alert: RiskAlertModel) -> dict[str, Any]:
        from app.models_risk_ops import RiskTaskModel

        events = list(
            session.scalars(
                select(RiskAlertEventModel)
                .where(RiskAlertEventModel.alert_id == alert.alert_id)
                .order_by(RiskAlertEventModel.event_id)
            )
        )
        tasks = list(
            session.scalars(
                select(RiskTaskModel)
                .where(RiskTaskModel.alert_id == alert.alert_id)
                .order_by(RiskTaskModel.created_at)
            )
        )
        names = usernames(
            session,
            {alert.owner_user_id}
            | {event.actor_user_id for event in events}
            | {task.assignee_user_id for task in tasks},
        )
        return {
            **self._serialize(alert, names),
            "evidence": alert.evidence,
            "events": [
                {
                    "action": event.action,
                    "from_status": event.from_status,
                    "to_status": event.to_status,
                    "version": event.resulting_version,
                    "actor": names.get(event.actor_user_id, "system") if event.actor_user_id else "system",
                    "actor_role": event.actor_role,
                    "comment": event.comment,
                    "payload": event.payload,
                    "recorded_at": ts(event.recorded_at),
                }
                for event in events
            ],
            "tasks": [
                {
                    "task_id": str(task.task_id),
                    "title": task.title,
                    "status": task.status,
                    "assignee": names.get(task.assignee_user_id),
                    "due_at": ts(task.due_at),
                }
                for task in tasks
            ],
        }

    @staticmethod
    def _serialize(alert: RiskAlertModel, names: dict[uuid.UUID, str]) -> dict[str, Any]:
        return {
            "alert_id": str(alert.alert_id),
            "facility_id": str(alert.facility_id) if alert.facility_id else None,
            "request_id": str(alert.request_id) if alert.request_id else None,
            "risk_type": alert.risk_type,
            "severity": alert.severity,
            "rule_key": alert.rule_key,
            "rule_version": alert.rule_version,
            "source_type": alert.source_type,
            "trigger_reason": alert.trigger_reason,
            "status": alert.status,
            "owner_user_id": str(alert.owner_user_id) if alert.owner_user_id else None,
            "owner": names.get(alert.owner_user_id) if alert.owner_user_id else None,
            "resolution": alert.resolution,
            "version": alert.version,
            "created_at": ts(alert.created_at),
            "updated_at": ts(alert.updated_at),
            "resolved_at": ts(alert.resolved_at),
            "closed_at": ts(alert.closed_at),
        }


def enterprise_can_see(request: FinancingRequestModel, user: AuthenticatedUser) -> bool:
    if user.role == "supplier":
        return request.supplier_organization_id == user.organization_id
    if user.role == "core_enterprise":
        return request.core_enterprise_organization_id == user.organization_id
    return False


__all__ = [
    "RiskAlertService",
    "RiskDetectionService",
    "RiskOpsConflict",
    "RiskOpsError",
    "RiskOpsForbidden",
    "RiskOpsNotFound",
    "RiskRuleService",
    "current_rules",
    "org_condition",
    "sees_everything",
    "seed_default_rules",
]
