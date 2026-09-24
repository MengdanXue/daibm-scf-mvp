"""Risk dashboard and the facility risk detail view, computed from recorded data only."""

from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.domain.outcome_governance import training_failure_reason
from app.domain.risk_operations import (
    DASHBOARD_ROLES,
    DEFAULTED,
    ENTERPRISE_ROLES,
    HIGH_RISK,
    NORMAL,
    WATCH,
    risk_class,
)
from app.identity import AuthenticatedUser
from app.models import FinancingRequestModel, LedgerEventModel
from app.models_facility import FinancingFacilityModel, PaymentModel
from app.models_identity import OrganizationModel
from app.models_lifecycle import (
    FacilityDefaultModel,
    FacilityDelinquencyModel,
    FacilityRecoveryModel,
    FacilityRestructureModel,
    FacilityStatusTransitionModel,
    FacilityWriteOffModel,
)
from app.models_model_governance import (
    RiskDecisionRecordModel,
    RiskModelVersionModel,
    RiskModelVersionTransitionModel,
    TrainingDatasetSnapshotModel,
)
from app.models_outcome import CalibrationRunModel
from app.models_risk_ops import RiskAlertEventModel, RiskAlertModel, RiskTaskEventModel, RiskTaskModel
from app.models_workflow import WorkflowActionModel
from app.repositories.facility import FacilityRepository
from app.services.facility import FacilityNotFound, FacilityService
from app.services.risk_operations import (
    RiskOpsNotFound,
    current_rules,
    org_scope,
    parse_uuid,
    require_role,
    ts,
    usernames,
)

ZERO = Decimal("0.00")
STAGE_LABELS = {
    "application": "申请",
    "review": "审核",
    "disbursement": "放款",
    "repayment": "还款",
    "overdue": "逾期",
    "restructure": "重组",
    "disposal": "处置",
    "default": "违约",
    "recovery": "追偿",
    "write_off": "核销",
    "closed": "结清",
}
_WORKFLOW_STAGE = {
    "create": "application",
    "update": "application",
    "submit": "application",
    "confirm_trade": "review",
    "return_trade": "review",
    "assess_risk": "review",
    "decide": "review",
    "apply_control": "review",
    "audit": "review",
}
_TRANSITION_STAGE = {
    "disbursed": "disbursement",
    "active": "disbursement",
    "overdue": "overdue",
    "in_disposal": "disposal",
    "restructured": "restructure",
    "defaulted": "default",
    "in_recovery": "recovery",
    "recovered": "recovery",
    "written_off": "write_off",
    "repaid": "repayment",
    "closed": "closed",
}


def _funded(facility: FinancingFacilityModel) -> bool:
    """Money reached the borrower: disbursement confirmed or any later stage."""

    return facility.disbursed_at is not None or facility.status not in {
        "ready_for_disbursement",
        "disbursed",
    }


def _money(value: Decimal) -> str:
    return f"{Decimal(value).quantize(Decimal('0.01')):.2f}"


class RiskInsightService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        facility_service: FacilityService,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.facility_service = facility_service
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    # --- Dashboard --------------------------------------------------------------

    def dashboard(self, user: AuthenticatedUser) -> dict[str, Any]:
        require_role(user, DASHBOARD_ROLES, "Current role cannot view the risk dashboard")
        now = self.clock()
        with self.session_factory() as session:
            facilities = self._facilities(session, user)
            ids = [item.facility_id for item, _ in facilities]
            profile = self._profiles(session, facilities, now)
            by_class = Counter(item["risk_class"] for item in profile.values())
            transitions = self._transition_sets(session, ids)
            recoveries = self._rows(session, FacilityRecoveryModel, ids)
            writeoffs = self._rows(session, FacilityWriteOffModel, ids)
            defaulted_ids = {row.facility_id for row in self._rows(session, FacilityDefaultModel, ids)}
            total_recovered = sum((Decimal(r.amount) for r in recoveries), ZERO)
            recovered_outstanding = sum(
                (Decimal(r.amount) for r in recoveries if r.applied_to == "outstanding"), ZERO
            )
            recovered_after_writeoff = total_recovered - recovered_outstanding
            written_off = sum((Decimal(w.amount) for w in writeoffs), ZERO)
            remaining_defaulted = sum(
                (Decimal(f.outstanding_amount) for f, _ in facilities if f.facility_id in defaulted_ids),
                ZERO,
            )
            exposure = recovered_outstanding + written_off + remaining_defaulted
            return {
                "scope": "all" if org_scope(user) is None else "organization",
                "generated_at": ts(now),
                "assets": {
                    "total_financed": _money(
                        sum((Decimal(f.principal) for f, _ in facilities if _funded(f)), ZERO)
                    ),
                    "current_balance": _money(
                        sum((Decimal(f.outstanding_amount) for f, _ in facilities), ZERO)
                    ),
                    "enterprise_count": len({supplier for _, supplier in facilities if supplier}),
                    "financing_count": len(facilities),
                },
                "risk_distribution": {key: by_class.get(key, 0) for key in (NORMAL, WATCH, HIGH_RISK, DEFAULTED)},
                "lifecycle": {
                    "normal_repaid": sum(
                        1
                        for f, _ in facilities
                        if f.status == "repaid" or (f.status == "closed" and f.closure_reason == "repaid")
                    ),
                    "overdue": len({row.facility_id for row in self._rows(session, FacilityDelinquencyModel, ids)}),
                    "disposal": len(transitions["in_disposal"]),
                    "restructured": len(
                        {row.facility_id for row in self._rows(session, FacilityRestructureModel, ids)}
                    ),
                    "recovery": len(transitions["in_recovery"]),
                    "written_off": len({w.facility_id for w in writeoffs}),
                },
                "losses": {
                    "total_recovered": _money(total_recovered),
                    "written_off": _money(written_off),
                    "net_loss": _money(written_off - recovered_after_writeoff),
                    "recovery_rate": (
                        float((total_recovered / exposure).quantize(Decimal("0.0001")))
                        if exposure > 0
                        else None
                    ),
                    "defaulted_exposure": _money(exposure),
                },
                "model": self._model_status(session),
                "alerts": self._alert_summary(session, user),
                "tasks": self._task_summary(session, user, now),
            }

    def facilities(self, user: AuthenticatedUser) -> list[dict[str, Any]]:
        """Facilities the user may see, with their current risk class."""

        with self.session_factory() as session:
            facilities = self._facilities(session, user)
            profile = self._profiles(session, facilities, self.clock())
            requests = {
                row.request_id: row
                for row in session.scalars(
                    select(FinancingRequestModel).where(
                        FinancingRequestModel.request_id.in_([f.request_id for f, _ in facilities])
                    )
                )
            }
            orgs = self._org_names(session, {r.supplier_organization_id for r in requests.values()})
            open_alerts: Counter[uuid.UUID] = Counter()
            if user.role not in ENTERPRISE_ROLES:
                for facility_id in session.scalars(
                    select(RiskAlertModel.facility_id).where(
                        RiskAlertModel.facility_id.in_([f.facility_id for f, _ in facilities]),
                        RiskAlertModel.status != "CLOSED",
                    )
                ):
                    if facility_id is not None:
                        open_alerts[facility_id] += 1
            return [
                {
                    "facility_id": str(f.facility_id),
                    "request_id": str(f.request_id),
                    "enterprise": orgs.get(requests[f.request_id].supplier_organization_id),
                    "principal": _money(f.principal),
                    "outstanding_amount": _money(f.outstanding_amount),
                    "status": f.status,
                    "risk_class": profile[f.facility_id]["risk_class"],
                    "latest_score": profile[f.facility_id]["latest_score"],
                    "open_alerts": open_alerts.get(f.facility_id, 0),
                    "updated_at": ts(f.updated_at),
                }
                for f, _ in facilities
            ]

    # --- Risk detail ------------------------------------------------------------

    def facility_detail(self, facility_id: str, user: AuthenticatedUser) -> dict[str, Any]:
        normalized = parse_uuid(facility_id)
        try:
            # Reuses the facility's own visibility rules (enterprises see only their own).
            facility_view = self.facility_service.get(normalized, user)
        except FacilityNotFound as error:
            raise RiskOpsNotFound(str(facility_id)) from error
        internal = user.role not in ENTERPRISE_ROLES
        now = self.clock()
        with self.session_factory() as session:
            facility = session.get(FinancingFacilityModel, normalized)
            assert facility is not None
            request = session.get(FinancingRequestModel, facility.request_id)
            assert request is not None
            orgs = self._org_names(
                session, {request.supplier_organization_id, request.core_enterprise_organization_id}
            )
            profile = self._profiles(session, [(facility, request.supplier_organization_id)], now)[
                facility.facility_id
            ]
            decisions = list(
                session.scalars(
                    select(RiskDecisionRecordModel)
                    .where(RiskDecisionRecordModel.request_id == request.request_id)
                    .order_by(RiskDecisionRecordModel.recorded_at)
                )
            )
            versions = {
                row.id: row
                for row in session.scalars(
                    select(RiskModelVersionModel).where(
                        RiskModelVersionModel.id.in_(
                            [d.model_version_id for d in decisions if d.model_version_id]
                        )
                    )
                )
            }
            history = [
                {
                    "decision_record_id": str(item.decision_record_id),
                    "recorded_at": ts(item.recorded_at),
                    "raw_score": item.raw_score,
                    "final_score": item.final_score,
                    "band": item.band,
                    "model_version": (
                        f"{versions[item.model_version_id].model_id}@v{versions[item.model_version_id].version}"
                        if item.model_version_id in versions
                        else None
                    ),
                    "calibration_applied": item.calibration_run_id is not None,
                    "fallback_code": item.fallback_code,
                }
                for item in decisions
            ]
            latest = decisions[-1] if decisions else None
            version = versions.get(latest.model_version_id) if latest and latest.model_version_id else None
            trend = None
            if len(decisions) >= 2:
                delta = decisions[-1].final_score - decisions[-2].final_score
                trend = {"delta": round(delta, 6), "direction": "up" if delta > 0 else "down" if delta < 0 else "flat"}
            timeline = self._timeline(session, facility, request)
            return {
                "facility": facility_view,
                "enterprise": {
                    "supplier": orgs.get(request.supplier_organization_id),
                    "core_enterprise": orgs.get(request.core_enterprise_organization_id),
                    "applicant_id": request.applicant_id,
                    "contract_number": request.contract_number,
                    "invoice_number": request.invoice_number,
                },
                "financing": {
                    "request_id": str(request.request_id),
                    "requested_amount": _money(request.amount),
                    "term_days": request.term_days,
                    "principal": _money(facility.principal),
                    "outstanding_amount": _money(facility.outstanding_amount),
                    "currency": facility.currency,
                    "status": facility.status,
                    "application_status": request.status,
                    "disbursed_at": ts(facility.disbursed_at),
                },
                "risk": {
                    "risk_class": profile["risk_class"],
                    "current_band": latest.band if latest else None,
                    "current_score": latest.final_score if latest else None,
                    "max_days_past_due": profile["days_past_due"],
                    "history": history,
                    "trend": trend,
                },
                "model": (
                    {
                        "model_version_id": str(version.id) if version else None,
                        "model_version": f"{version.model_id}@v{version.version}" if version else None,
                        "artifact_hash": latest.calibration_artifact_sha256 if latest else None,
                        "dataset_snapshot_id": (
                            str(version.dataset_snapshot_id)
                            if version and version.dataset_snapshot_id
                            else None
                        ),
                        "engine_version": latest.engine_version if latest else None,
                        "calibration_applied": bool(latest and latest.calibration_run_id),
                    }
                    if internal
                    else None
                ),
                "timeline": timeline,
                "audit": self._audit(session, facility, request, internal),
                "alerts": self._facility_alerts(session, facility.facility_id, user) if internal else None,
                "tasks": self._facility_tasks(session, facility.facility_id) if internal else None,
            }

    # --- helpers ------------------------------------------------------------------

    @staticmethod
    def _facilities(
        session: Session, user: AuthenticatedUser
    ) -> list[tuple[FinancingFacilityModel, uuid.UUID | None]]:
        visible = (
            FacilityRepository._visible_facilities_statement(
                role=user.role, organization_id=user.organization_id
            )
            .with_only_columns(FinancingFacilityModel.facility_id)
            .subquery()
        )
        return [
            (facility, supplier)
            for facility, supplier in session.execute(
                select(FinancingFacilityModel, FinancingRequestModel.supplier_organization_id)
                .join(
                    FinancingRequestModel,
                    FinancingRequestModel.request_id == FinancingFacilityModel.request_id,
                )
                .where(FinancingFacilityModel.facility_id.in_(select(visible.c.facility_id)))
                .order_by(FinancingFacilityModel.updated_at.desc(), FinancingFacilityModel.facility_id)
            )
        ]

    def _profiles(
        self,
        session: Session,
        facilities: list[tuple[FinancingFacilityModel, uuid.UUID | None]],
        now: datetime,
    ) -> dict[uuid.UUID, dict[str, Any]]:
        ids = [f.facility_id for f, _ in facilities]
        rules = current_rules(session, now)
        overdue_rule = rules.get("OVERDUE_DAYS")
        threshold = int(overdue_rule.threshold) if overdue_rule and overdue_rule.threshold is not None else 30
        latest_dpd: dict[uuid.UUID, int] = {}
        for row in self._rows(session, FacilityDelinquencyModel, ids, order=FacilityDelinquencyModel.recorded_at):
            latest_dpd[row.facility_id] = row.days_past_due
        decisions: dict[uuid.UUID, RiskDecisionRecordModel] = {}
        for record in session.scalars(
            select(RiskDecisionRecordModel)
            .where(RiskDecisionRecordModel.request_id.in_([f.request_id for f, _ in facilities]))
            .order_by(RiskDecisionRecordModel.recorded_at)
        ):
            decisions[record.request_id] = record
        result: dict[uuid.UUID, dict[str, Any]] = {}
        for facility, _ in facilities:
            # A cured delinquency no longer counts; only open arrears do.
            dpd = latest_dpd.get(facility.facility_id, 0) if facility.status in {"overdue", "in_disposal"} else 0
            decision = decisions.get(facility.request_id)
            result[facility.facility_id] = {
                "days_past_due": dpd,
                "latest_score": decision.final_score if decision else None,
                "risk_class": risk_class(
                    status=facility.status,
                    closure_reason=facility.closure_reason,
                    max_days_past_due=dpd,
                    latest_band=decision.band if decision else None,
                    overdue_threshold=threshold,
                ),
            }
        return result

    @staticmethod
    def _rows(session: Session, model, ids: list[uuid.UUID], *, order=None) -> list[Any]:
        if not ids:
            return []
        statement = select(model).where(model.facility_id.in_(ids))
        if order is not None:
            statement = statement.order_by(order)
        return list(session.scalars(statement))

    def _transition_sets(self, session: Session, ids: list[uuid.UUID]) -> dict[str, set[uuid.UUID]]:
        sets: dict[str, set[uuid.UUID]] = defaultdict(set)
        for row in self._rows(session, FacilityStatusTransitionModel, ids):
            sets[row.to_status].add(row.facility_id)
        return sets

    @staticmethod
    def _org_names(
        session: Session, ids: set[uuid.UUID | None]
    ) -> dict[uuid.UUID | None, dict[str, str]]:
        wanted = [item for item in ids if item is not None]
        if not wanted:
            return {}
        return {
            row.organization_id: {"code": row.organization_code, "name": row.name}
            for row in session.scalars(
                select(OrganizationModel).where(OrganizationModel.organization_id.in_(wanted))
            )
        }

    @staticmethod
    def _model_status(session: Session) -> dict[str, Any]:
        active = list(
            session.scalars(
                select(RiskModelVersionModel)
                .where(RiskModelVersionModel.status == "ACTIVE")
                .order_by(RiskModelVersionModel.scope)
            )
        )
        last_change = session.scalar(select(func.max(RiskModelVersionTransitionModel.recorded_at)))
        snapshot = session.scalar(
            select(TrainingDatasetSnapshotModel)
            .order_by(TrainingDatasetSnapshotModel.created_at.desc())
            .limit(1)
        )
        failed = session.scalar(
            select(CalibrationRunModel)
            .where(
                or_(
                    CalibrationRunModel.status == "failed",
                    CalibrationRunModel.deployment_status.in_(("rejected", "activation_failed")),
                )
            )
            .order_by(CalibrationRunModel.completed_at.desc())
            .limit(1)
        )
        return {
            "active": [
                {
                    "id": str(item.id),
                    "label": f"{item.model_id}@v{item.version}",
                    "scope": item.scope,
                    "artifact_hash": item.artifact_hash,
                    "activated_at": ts(item.activated_at),
                    "dataset_snapshot_id": str(item.dataset_snapshot_id) if item.dataset_snapshot_id else None,
                }
                for item in active
            ],
            "last_model_update": ts(last_change),
            "latest_snapshot": (
                {
                    "snapshot_id": str(snapshot.snapshot_id),
                    "scope": snapshot.deployment_scope,
                    "created_at": ts(snapshot.created_at),
                    "included_count": snapshot.included_count,
                    "excluded_count": snapshot.excluded_count,
                }
                if snapshot is not None
                else None
            ),
            "latest_failure": (
                {
                    "calibration_run_id": str(failed.calibration_run_id),
                    "failure_reason": training_failure_reason(
                        failure_code=failed.failure_code, activation_reason=failed.activation_reason
                    ),
                    "completed_at": ts(failed.completed_at),
                }
                if failed is not None
                else None
            ),
        }

    @staticmethod
    def _alert_summary(session: Session, user: AuthenticatedUser) -> dict[str, Any]:
        statement = select(RiskAlertModel.status, RiskAlertModel.severity, func.count()).group_by(
            RiskAlertModel.status, RiskAlertModel.severity
        )
        scope = org_scope(user)
        if scope is not None:
            statement = statement.where(RiskAlertModel.organization_id == scope)
        by_status: Counter[str] = Counter()
        open_by_severity: Counter[str] = Counter()
        for status, severity, count in session.execute(statement):
            by_status[status] += count
            if status != "CLOSED":
                open_by_severity[severity] += count
        return {"by_status": dict(by_status), "open_by_severity": dict(open_by_severity)}

    @staticmethod
    def _task_summary(session: Session, user: AuthenticatedUser, now: datetime) -> dict[str, int]:
        rows = session.execute(
            select(RiskTaskModel.status, RiskTaskModel.due_at).where(
                RiskTaskModel.assignee_user_id == user.user_id
            )
        ).all()
        open_rows = [due for status, due in rows if status in ("OPEN", "IN_PROGRESS")]
        return {"my_open": len(open_rows), "my_overdue": sum(1 for due in open_rows if due < now)}

    def _timeline(
        self, session: Session, facility: FinancingFacilityModel, request: FinancingRequestModel
    ) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        actions = list(
            session.scalars(
                select(WorkflowActionModel)
                .where(WorkflowActionModel.request_id == request.request_id)
                .order_by(WorkflowActionModel.created_at)
            )
        )
        transitions = self._rows(session, FacilityStatusTransitionModel, [facility.facility_id],
                                 order=FacilityStatusTransitionModel.recorded_at)
        payments = [
            p for p in self._rows(session, PaymentModel, [facility.facility_id], order=PaymentModel.submitted_at)
            if p.status == "confirmed"
        ]
        recoveries = self._rows(session, FacilityRecoveryModel, [facility.facility_id],
                                order=FacilityRecoveryModel.recorded_at)
        delinquencies = self._rows(session, FacilityDelinquencyModel, [facility.facility_id],
                                   order=FacilityDelinquencyModel.recorded_at)
        names = usernames(
            session,
            {a.actor_user_id for a in actions}
            | {t.actor_user_id for t in transitions}
            | {p.decided_by_user_id for p in payments}
            | {r.recorded_by_user_id for r in recoveries}
            | {d.marked_by_user_id for d in delinquencies},
        )
        for action in actions:
            entries.append(
                self._entry(
                    _WORKFLOW_STAGE.get(action.action_type, "review"),
                    f"{action.action_type}: {action.from_status or '—'} → {action.to_status}",
                    action.created_at,
                    names.get(action.actor_user_id),
                    action.actor_role,
                    action.comment,
                )
            )
        for item in transitions:
            entries.append(
                self._entry(
                    _TRANSITION_STAGE.get(item.to_status, "disbursement"),
                    f"{item.trigger_action}: {item.from_status or '—'} → {item.to_status}",
                    item.recorded_at,
                    names.get(item.actor_user_id) if item.actor_user_id else "system",
                    item.actor_role,
                    item.reason_code,
                )
            )
        for payment in payments:
            entries.append(
                self._entry(
                    "repayment",
                    f"还款确认 {_money(payment.amount)}",
                    payment.decided_at or payment.submitted_at,
                    names.get(payment.decided_by_user_id) if payment.decided_by_user_id else None,
                    None,
                    payment.payment_reference,
                )
            )
        for item in delinquencies:
            entries.append(
                self._entry(
                    "overdue", f"逾期 {item.days_past_due} 天", item.recorded_at,
                    names.get(item.marked_by_user_id), None, item.reason_code,
                )
            )
        for item in recoveries:
            entries.append(
                self._entry(
                    "recovery", f"追偿回收 {_money(item.amount)}（{item.source}）", item.recorded_at,
                    names.get(item.recorded_by_user_id), None, item.recovery_reference,
                )
            )
        entries.sort(key=lambda entry: entry["time"] or "")
        return entries

    @staticmethod
    def _entry(stage, event, at, actor, role, reason) -> dict[str, Any]:
        return {
            "stage": stage,
            "stage_label": STAGE_LABELS.get(stage, stage),
            "event": event,
            "time": ts(at),
            "actor": actor,
            "role": role,
            "reason": reason,
        }

    def _audit(
        self,
        session: Session,
        facility: FinancingFacilityModel,
        request: FinancingRequestModel,
        internal: bool,
    ) -> list[dict[str, Any]]:
        """Who did what, when and why: lifecycle, alerts, tasks and ledger records."""

        entries = [
            {
                "source": "lifecycle",
                "action": item["event"],
                "actor": item["actor"],
                "role": item["role"],
                "time": item["time"],
                "reason": item["reason"],
            }
            for item in self._timeline(session, facility, request)
        ]
        if internal:
            alert_ids = list(
                session.scalars(select(RiskAlertModel.alert_id).where(RiskAlertModel.facility_id == facility.facility_id))
            )
            task_ids = list(
                session.scalars(select(RiskTaskModel.task_id).where(RiskTaskModel.facility_id == facility.facility_id))
            )
            alert_events = list(
                session.scalars(select(RiskAlertEventModel).where(RiskAlertEventModel.alert_id.in_(alert_ids)))
            ) if alert_ids else []
            task_events = list(
                session.scalars(select(RiskTaskEventModel).where(RiskTaskEventModel.task_id.in_(task_ids)))
            ) if task_ids else []
            names = usernames(
                session, {e.actor_user_id for e in alert_events} | {e.actor_user_id for e in task_events}
            )
            for alert_event in alert_events:
                entries.append({
                    "source": "alert",
                    "action": f"{alert_event.action}: {alert_event.from_status or '—'} → {alert_event.to_status}",
                    "actor": names.get(alert_event.actor_user_id) if alert_event.actor_user_id else "system",
                    "role": alert_event.actor_role,
                    "time": ts(alert_event.recorded_at),
                    "reason": alert_event.comment,
                })
            for task_event in task_events:
                entries.append({
                    "source": "task",
                    "action": f"{task_event.action}: {task_event.from_status or '—'} → {task_event.to_status}",
                    "actor": names.get(task_event.actor_user_id) if task_event.actor_user_id else "system",
                    "role": task_event.actor_role,
                    "time": ts(task_event.recorded_at),
                    "reason": task_event.comment,
                })
            ledger_count = session.scalar(
                select(func.count())
                .select_from(LedgerEventModel)
                .where(LedgerEventModel.entity_id.in_([facility.facility_id, request.request_id, *alert_ids, *task_ids]))
            )
            entries.append({
                "source": "ledger",
                "action": f"{ledger_count} 条哈希链账本事件",
                "actor": None,
                "role": None,
                "time": None,
                "reason": None,
            })
        entries.sort(key=lambda entry: entry["time"] or "", reverse=True)
        return entries

    @staticmethod
    def _facility_alerts(session: Session, facility_id: uuid.UUID, user: AuthenticatedUser) -> list[dict[str, Any]]:
        statement = select(RiskAlertModel).where(RiskAlertModel.facility_id == facility_id)
        scope = org_scope(user)
        if scope is not None:
            statement = statement.where(RiskAlertModel.organization_id == scope)
        return [
            {
                "alert_id": str(item.alert_id),
                "risk_type": item.risk_type,
                "severity": item.severity,
                "status": item.status,
                "trigger_reason": item.trigger_reason,
                "created_at": ts(item.created_at),
            }
            for item in session.scalars(statement.order_by(RiskAlertModel.created_at.desc()))
        ]

    @staticmethod
    def _facility_tasks(session: Session, facility_id: uuid.UUID) -> list[dict[str, Any]]:
        tasks = list(
            session.scalars(
                select(RiskTaskModel).where(RiskTaskModel.facility_id == facility_id).order_by(RiskTaskModel.due_at)
            )
        )
        names = usernames(session, {task.assignee_user_id for task in tasks})
        return [
            {
                "task_id": str(task.task_id),
                "title": task.title,
                "status": task.status,
                "assignee": names.get(task.assignee_user_id),
                "due_at": ts(task.due_at),
            }
            for task in tasks
        ]


__all__ = ["RiskInsightService"]
