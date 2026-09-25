"""Stable, reproducible demo dataset created only through the business services.

Nothing is inserted behind the services' back: every application, facility,
payment, default, outcome, alert, task, snapshot and model version is created
by the same governed code path a user would trigger, so ledgers, transition
histories and audit trails are complete. The dataset contains:

* a model history portfolio of closed facilities of the demo lender whose
  outcomes train the ACTIVE calibration model;
* three enterprises that tell the lifecycle stories:
  normal (apply -> credit -> disburse -> repay -> closed),
  at risk (apply -> disburse -> overdue -> alert -> task in progress) and
  defaulted (default -> recovery -> write-off);
* a newer CANDIDATE model that waits for an auditor's promotion, its dataset
  snapshot, and risk decisions made with the ACTIVE model (decision lineage).

It runs once (idempotent), needs the demo accounts (``DAIBM_DEMO_PASSWORD``)
and is enabled at startup by ``DAIBM_DEMO_DATASET=true`` or explicitly::

    python -m app.demo_dataset
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.identity import AuthenticatedUser, hash_password
from app.models import FinancingRequestModel
from app.models_identity import OrganizationModel, UserModel
from app.schemas import FinancingRequestCreate
from app.schemas_facility import (
    CreateFacilityRequest,
    DecisionPaymentRequest,
    DeclareDefaultRequest,
    LifecycleDecisionRequest,
    MarkOverdueRequest,
    RecordRecoveryRequest,
    SubmitPaymentRequest,
    VersionedFacilityCommand,
    WriteOffRequest,
)
from app.schemas_outcome import ActualOutcomeCreate
from app.schemas_workflow import ApplicationDraftCreate
from app.risk import assess
from app.services.calibration_jobs import CalibrationJobService
from app.services.facility import FacilityService
from app.services.identity import IdentityService
from app.services.outcomes import OutcomeService
from app.services.risk_operations import RiskAlertService, RiskDetectionService, seed_default_rules
from app.services.risk_tasks import RiskTaskService
from app.services.security import demo_password_from_env
from app.services.workflow import WorkflowService

MARKER_CONTRACT = "DEMO-NORMAL-001"
LENDER_CODE = "BANK-001"
CORE_CODE = "CORE-001"
HISTORY_SIZE = 32
CANDIDATE_EXTRA = 5

# Story enterprises: (organization code, name, supplier username, display name).
ENTERPRISES = {
    "normal": ("SUPPLIER-101", "ООО «Надёжный партнёр» / 稳健制造", "supplier.normal.demo",
               "Поставщик · Надёжный партнёр"),
    "watch": ("SUPPLIER-102", "ООО «Восточная логистика» / 东方物流", "supplier.watch.demo",
              "Поставщик · Восточная логистика"),
    "default": ("SUPPLIER-103", "ООО «Западная химия» / 西部化工", "supplier.default.demo",
                "Поставщик · Западная химия"),
}


def _sha(label: str) -> str:
    return hashlib.sha256(f"daibm-demo:{label}".encode()).hexdigest()


@dataclass(frozen=True)
class Profile:
    """Application features and the realized outcome of one history facility."""

    payment_delay_days: int
    counterparty_risk: float
    invoice_mismatch: bool
    relationship_months: int
    defaults: bool


def history_profiles(count: int) -> list[Profile]:
    """A portfolio where realized defaults follow the baseline risk, but more
    often than the baseline probability says: the calibration has something
    real to correct. Classes alternate so every time partition has both."""

    profiles = []
    for index in range(count):
        risky = index % 3 == 0 or index % 7 == 0
        profiles.append(
            Profile(
                payment_delay_days=(8 + index % 5 * 6) if risky else index % 4,
                counterparty_risk=round((0.42 + index % 5 * 0.05) if risky else (0.05 + index % 6 * 0.03), 2),
                invoice_mismatch=risky and index % 2 == 0,
                relationship_months=(6 + index % 4) if risky else (24 + index % 12),
                defaults=risky,
            )
        )
    return profiles


class DemoDatasetSeeder:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        artifact_root: Path,
        demo_password: str | None = None,
        log: Callable[[str], None] = lambda message: None,
    ) -> None:
        self.session_factory = session_factory
        self.artifact_root = Path(artifact_root)
        self.demo_password = demo_password if demo_password is not None else demo_password_from_env()
        self.log = log
        self.workflow = WorkflowService(session_factory)
        self.facilities = FacilityService(session_factory)
        self.alerts = RiskAlertService(session_factory)
        self.tasks = RiskTaskService(session_factory)
        self.sequence = 0

    # --- Entry point ---------------------------------------------------------------

    def seeded(self) -> bool:
        with self.session_factory() as session:
            return (
                session.scalar(
                    select(FinancingRequestModel.request_id).where(
                        FinancingRequestModel.contract_number == MARKER_CONTRACT
                    )
                )
                is not None
            )

    def seed(self) -> dict[str, Any]:
        if self.seeded():
            return {"seeded": False, "reason": "already_present"}
        if not self.demo_password:
            return {"seeded": False, "reason": "demo_accounts_disabled"}
        IdentityService(self.session_factory, demo_password=self.demo_password).seed_demo_accounts()
        with self.session_factory.begin() as session:
            seed_default_rules(session)
        users = self._users()
        summary: dict[str, Any] = {"seeded": True}

        # 1. Model history: closed facilities with outcomes train the ACTIVE model.
        self.log("model history portfolio")
        history = [
            self._history_facility(users, index, profile)
            for index, profile in enumerate(history_profiles(HISTORY_SIZE))
        ]
        self._train(users, auto_promotion=True)
        summary["history_facilities"] = len(history)

        # 2. Lifecycle stories, assessed by the ACTIVE model (decision lineage).
        self.log("enterprise stories")
        normal = self._normal_story(users)
        watch = self._watch_story(users)
        defaulted = self._default_story(users)
        summary.update(normal=normal, watch=watch, defaulted=defaulted)

        # 3. More outcomes train a newer model that waits for an auditor (CANDIDATE).
        self.log("candidate model")
        for index, profile in enumerate(history_profiles(HISTORY_SIZE + CANDIDATE_EXTRA)[HISTORY_SIZE:]):
            self._history_facility(users, HISTORY_SIZE + index, profile)
        self._submit_outcome(users, normal["facility_id"])
        self._submit_outcome(users, defaulted["facility_id"])
        self._train(users, auto_promotion=False)

        # 4. Rules scan everything once: the history portfolio's alerts were
        # handled in the past; the story enterprises' alerts are live.
        self.log("risk alerts and tasks")
        summary["watch"].update(self._alerts(users, watch, defaulted))
        return summary

    # --- Actors ----------------------------------------------------------------------

    def _users(self) -> dict[str, AuthenticatedUser]:
        assert self.demo_password is not None
        with self.session_factory.begin() as session:
            for code, name, username, display in ENTERPRISES.values():
                organization = session.scalar(
                    select(OrganizationModel).where(OrganizationModel.organization_code == code)
                )
                if organization is None:
                    organization = OrganizationModel(
                        organization_id=uuid.uuid5(uuid.NAMESPACE_URL, f"daibm-scf:organization:{code}"),
                        organization_code=code,
                        name=name,
                        organization_type="supplier",
                        created_at=datetime.now(timezone.utc),
                    )
                    session.add(organization)
                    session.flush()
                if session.scalar(select(UserModel).where(UserModel.username == username)) is None:
                    password_hash, password_salt = hash_password(self.demo_password)
                    session.add(
                        UserModel(
                            user_id=uuid.uuid5(uuid.NAMESPACE_URL, f"daibm-scf:user:{username}"),
                            username=username,
                            display_name=display,
                            password_hash=password_hash,
                            password_salt=password_salt,
                            role="supplier",
                            organization_id=organization.organization_id,
                            is_active=True,
                            created_at=datetime.now(timezone.utc),
                        )
                    )
        identity = IdentityService(self.session_factory, demo_password=self.demo_password)
        wanted = {
            "supplier": "supplier.demo",
            "core": "core.demo",
            "financier": "financier.demo",
            "risk": "risk.demo",
            "auditor": "auditor.demo",
            **{key: value[2] for key, value in ENTERPRISES.items()},
        }
        with self.session_factory() as session:
            rows = {
                user.username: (user, organization)
                for user, organization in session.execute(
                    select(UserModel, OrganizationModel)
                    .join(OrganizationModel, OrganizationModel.organization_id == UserModel.organization_id)
                    .where(UserModel.username.in_(wanted.values()))
                )
            }
        return {key: identity._to_identity(*rows[username]) for key, username in wanted.items()}

    # --- Application stage ---------------------------------------------------------------

    def _apply(
        self, users, supplier: str, contract: str, profile: Profile, amount: str
    ) -> dict[str, Any]:
        """Draft -> submit -> trade confirmed -> risk assessed -> approved -> controlled -> audited."""

        self.sequence += 1
        payload = ApplicationDraftCreate(
            core_enterprise_organization_code=CORE_CODE,
            lender_organization_code=LENDER_CODE,
            contract_number=contract,
            invoice_number=f"INV-{contract}",
            amount=float(amount),
            term_days=90,
            payment_delay_days=profile.payment_delay_days,
            counterparty_risk=profile.counterparty_risk,
            invoice_mismatch=profile.invoice_mismatch,
            relationship_months=profile.relationship_months,
            transactions_last_30d=12,
        )
        flow = self.workflow
        application = flow.create_draft(payload, users[supplier])
        request_id = application["request_id"]
        application = flow.submit(request_id, application["version"], users[supplier])
        application = flow.confirm_trade(
            request_id, application["version"], confirmed=True,
            comment="Contract and invoice match the delivery records",
            user=users["core"], confirmed_payable_amount=Decimal(amount),
        )
        application = flow.assess_risk(request_id, application["version"], users["financier"])
        application = flow.decide(
            request_id, application["version"], decision="approved",
            comment="Within limit after review", user=users["financier"],
        )
        application = flow.apply_control(
            request_id, application["version"], comment="Standard monitoring", user=users["risk"]
        )
        return flow.audit(request_id, application["version"], comment="Evidence verified", user=users["auditor"])

    # --- Facility stage ------------------------------------------------------------------

    def _open_facility(
        self, users, application: dict[str, Any], amount: str, *, overdue_days: int = 0
    ) -> dict[str, Any]:
        """Two installments; a facility that will fall overdue has its first one
        due ``overdue_days`` ago, matching the delinquency evidence."""

        half = (Decimal(amount) / 2).quantize(Decimal("0.01"))
        first_due = date.today() - timedelta(days=overdue_days) if overdue_days else date.today() + timedelta(days=30)
        facility = self.facilities.create(
            CreateFacilityRequest.model_validate(
                {
                    "request_id": application["request_id"],
                    "principal": amount,
                    "currency": "CNY",
                    "version": 1,
                    "idempotency_key": str(uuid.uuid4()),
                    "installments": [
                        {"sequence": 1, "due_date": first_due.isoformat(), "amount": str(half)},
                        {
                            "sequence": 2,
                            "due_date": (date.today() + timedelta(days=60)).isoformat(),
                            "amount": str(Decimal(amount) - half),
                        },
                    ],
                }
            ),
            users["financier"],
        )
        facility = self.facilities.initiate_disbursement(
            facility["facility_id"], self._command(facility), users["financier"]
        )
        return self.facilities.confirm_disbursement(
            facility["facility_id"], self._command(facility), users["financier"]
        )

    def _repay(self, users, facility: dict[str, Any], supplier: str) -> dict[str, Any]:
        for index, installment in enumerate(facility["installments"]):
            facility = self.facilities.submit_payment(
                facility["facility_id"],
                SubmitPaymentRequest(
                    installment_id=installment["installment_id"],
                    amount=installment["amount"],
                    payment_reference=f"PAY-{facility['facility_id'][:8]}-{index + 1}",
                    version=facility["version"],
                    idempotency_key=uuid.uuid4(),
                ),
                users[supplier],
            )
            facility = self.facilities.decide_payment(
                facility["facility_id"],
                facility["payments"][-1]["payment_id"],
                DecisionPaymentRequest(
                    decision="confirmed", comment="Funds received",
                    version=facility["version"], idempotency_key=uuid.uuid4(),
                ),
                users["financier"],
            )
        return facility

    def _overdue(self, users, facility: dict[str, Any], days: int) -> dict[str, Any]:
        installment_id = facility["installments"][0]["installment_id"]
        return self.facilities.mark_overdue(
            facility["facility_id"],
            installment_id,
            MarkOverdueRequest(
                installment_id=installment_id, days_past_due=days,
                evidence_sha256=_sha(f"overdue:{facility['facility_id']}"),
                version=facility["version"], idempotency_key=uuid.uuid4(),
            ),
            users["financier"],
        )

    def _default_and_write_off(
        self, users, facility: dict[str, Any], *, recovered: str | None = None
    ) -> dict[str, Any]:
        facility = self._overdue(users, facility, 95)
        facility = self.facilities.open_disposal(
            facility["facility_id"], self._lifecycle(facility, "ARREARS_WORKOUT"), users["risk"]
        )
        facility = self.facilities.declare_default(
            facility["facility_id"],
            DeclareDefaultRequest(
                version=facility["version"], idempotency_key=uuid.uuid4(),
                reason_code="PAYMENT_DEFAULT", comment="Arrears unresolved after workout",
                evidence_sha256=_sha(f"default:{facility['facility_id']}"),
                defaulted_at=datetime.now(timezone.utc), days_past_due=95,
            ),
            users["risk"],
        )
        facility = self.facilities.start_recovery(
            facility["facility_id"], self._lifecycle(facility, "LEGAL_RECOVERY"), users["risk"]
        )
        if recovered is not None:
            facility = self.facilities.record_recovery(
                facility["facility_id"],
                RecordRecoveryRequest.model_validate(
                    {
                        "version": facility["version"],
                        "idempotency_key": str(uuid.uuid4()),
                        "amount": recovered,
                        "source": "GUARANTOR",
                        "recovery_reference": f"REC-{facility['facility_id'][:8]}",
                        "evidence_sha256": _sha(f"recovery:{facility['facility_id']}"),
                    }
                ),
                users["financier"],
            )
        return self.facilities.write_off(
            facility["facility_id"],
            WriteOffRequest(
                version=facility["version"], idempotency_key=uuid.uuid4(),
                reason_code="UNCOLLECTIBLE_BALANCE", comment="Recovery review completed",
                evidence_sha256=_sha(f"writeoff:{facility['facility_id']}"),
            ),
            users["auditor"],
        )

    # --- Outcomes and training -----------------------------------------------------------

    def _history_facility(self, users, index: int, profile: Profile) -> str:
        amount = "1000.00"
        application = self._apply(users, "supplier", f"DEMO-HIST-{index + 1:03d}", profile, amount)
        facility = self._open_facility(
            users, application, amount, overdue_days=95 if profile.defaults else 0
        )
        facility = (
            self._default_and_write_off(users, facility)
            if profile.defaults
            else self._repay(users, facility, "supplier")
        )
        self._submit_outcome(users, facility["facility_id"])
        return facility["facility_id"]

    def _close(self, users, facility: dict[str, Any]) -> dict[str, Any]:
        """The auditor closes a repaid or written-off facility."""

        return self.facilities.close(facility["facility_id"], self._command(facility), users["auditor"])

    def _submit_outcome(self, users, facility_id: str) -> None:
        facility = self.facilities.get(facility_id, users["auditor"])
        if facility["status"] != "closed":
            facility = self._close(users, facility)
        closed_at = datetime.fromisoformat(facility["closed_at"].replace("Z", "+00:00"))
        self._outcomes(auto_promotion=True).submit(
            facility_id,
            ActualOutcomeCreate(
                idempotency_key=uuid.uuid5(uuid.NAMESPACE_URL, f"daibm-demo:outcome:{facility_id}"),
                observed_at=closed_at,
                evidence_sha256=_sha(f"outcome:{facility_id}"),
                provenance="CONTROLLED_DEMO",
            ),
            users["auditor"],
        )

    def _outcomes(self, *, auto_promotion: bool) -> OutcomeService:
        return OutcomeService(
            self.session_factory,
            artifact_root=self.artifact_root,
            auto_promotion=auto_promotion,
            manual_review=False,
        )

    def _train(self, users, *, auto_promotion: bool) -> None:
        worker = CalibrationJobService(
            self.session_factory, outcome_service=self._outcomes(auto_promotion=auto_promotion)
        )
        while worker.process_next("demo-dataset"):
            pass

    # --- Stories ---------------------------------------------------------------------------

    def _normal_story(self, users) -> dict[str, Any]:
        profile = Profile(1, 0.08, False, 30, False)
        application = self._apply(users, "normal", MARKER_CONTRACT, profile, "80000.00")
        facility = self._repay(users, self._open_facility(users, application, "80000.00"), "normal")
        return {"request_id": application["request_id"], "facility_id": facility["facility_id"],
                "status": facility["status"]}

    def _watch_story(self, users) -> dict[str, Any]:
        profile = Profile(12, 0.38, False, 10, True)
        application = self._apply(users, "watch", "DEMO-WATCH-001", profile, "120000.00")
        facility = self._overdue(
            users, self._open_facility(users, application, "120000.00", overdue_days=35), 35
        )
        return {"request_id": application["request_id"], "facility_id": facility["facility_id"]}

    def _alerts(self, users, watch: dict[str, Any], defaulted: dict[str, Any]) -> dict[str, Any]:
        RiskDetectionService(self.session_factory).scan()
        live = {watch["facility_id"], defaulted["facility_id"]}
        for item in self.alerts.list_alerts(users["risk"], status="OPEN", limit=1000):
            if item["facility_id"] in live:
                continue
            alert = self.alerts.assign(
                item["alert_id"], users["risk"], owner_user_id=str(users["risk"].user_id),
                version=item["version"], comment="Historical case review",
            )
            alert = self.alerts.start(alert["alert_id"], users["risk"], version=alert["version"],
                                      comment=None)
            alert = self.alerts.resolve(
                alert["alert_id"], users["risk"], version=alert["version"],
                resolution="Historical case: the facility is closed and its outcome recorded.",
            )
            self.alerts.close(alert["alert_id"], users["auditor"], version=alert["version"],
                              comment="Resolution verified against the closed facility")
        alert = next(
            item for item in self.alerts.list_alerts(users["risk"], facility_id=watch["facility_id"])
            if item["status"] == "OPEN"
        )
        alert = self.alerts.assign(
            alert["alert_id"], users["risk"], owner_user_id=str(users["risk"].user_id),
            version=alert["version"], comment="Taking ownership",
        )
        alert = self.alerts.start(alert["alert_id"], users["risk"], version=alert["version"],
                                  comment="Contacting the borrower")
        task = self.tasks.create(
            users["risk"], title="Collect the overdue installment", task_type="COLLECTION",
            description="Call the borrower, agree a payment date and document the promise to pay.",
            assignee_user_id=str(users["risk"].user_id),
            due_at=datetime.now(timezone.utc) + timedelta(days=5),
            alert_id=alert["alert_id"],
        )
        task = self.tasks.start(task["task_id"], users["risk"], version=task["version"])
        task = self.tasks.add_note(task["task_id"], users["risk"], version=task["version"],
                                   note="Borrower confirmed a delayed customer payment; follow up in 3 days.")
        return {"alert_id": alert["alert_id"], "task_id": task["task_id"]}

    def _default_story(self, users) -> dict[str, Any]:
        profile = Profile(20, 0.62, True, 4, True)
        application = self._apply(users, "default", "DEMO-DEFAULT-001", profile, "60000.00")
        facility = self._default_and_write_off(
            users, self._open_facility(users, application, "60000.00", overdue_days=95),
            recovered="15000.00",
        )
        return {"request_id": application["request_id"], "facility_id": facility["facility_id"],
                "status": facility["status"]}

    # --- Commands --------------------------------------------------------------------------

    @staticmethod
    def _command(facility: dict[str, Any]) -> VersionedFacilityCommand:
        return VersionedFacilityCommand(version=facility["version"], idempotency_key=uuid.uuid4())

    @staticmethod
    def _lifecycle(facility: dict[str, Any], reason: str) -> LifecycleDecisionRequest:
        return LifecycleDecisionRequest(
            version=facility["version"], idempotency_key=uuid.uuid4(), reason_code=reason,
            comment="Governed lifecycle decision",
            evidence_sha256=_sha(f"{reason}:{facility['facility_id']}"),
        )


def baseline_scores() -> list[float]:
    """The baseline score of every history profile (documentation and tests)."""

    return [
        assess(
            FinancingRequestCreate(
                applicant_id="demo", amount=1000.0, term_days=90,
                payment_delay_days=item.payment_delay_days,
                counterparty_risk=item.counterparty_risk,
                invoice_mismatch=item.invoice_mismatch,
                relationship_months=item.relationship_months,
                transactions_last_30d=12,
            )
        ).score
        for item in history_profiles(HISTORY_SIZE + CANDIDATE_EXTRA)
    ]


def default_artifact_root() -> Path:
    return Path(
        os.environ.get(
            "CALIBRATION_ARTIFACT_DIR",
            str(Path(__file__).resolve().parents[1] / "artifacts" / "candidates" / "calibration"),
        )
    )


def main() -> int:
    from app.config import PostgresSettings
    from app.database import Database

    database = Database.create(PostgresSettings.from_env().sqlalchemy_url)
    try:
        result = DemoDatasetSeeder(
            database.session_factory,
            artifact_root=default_artifact_root(),
            log=lambda message: print(f"[demo-dataset] {message}", file=sys.stderr),
        ).seed()
    finally:
        database.dispose()
    print(json.dumps(result, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
