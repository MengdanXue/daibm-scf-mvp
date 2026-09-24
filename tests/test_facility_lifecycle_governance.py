"""Governed facility lifecycle: legal stages, audited transitions, immutable history."""

from __future__ import annotations

import importlib.util
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.domain.facility import (
    STAGE_CATALOG,
    FacilityAction,
    FacilityStatus,
    InvalidFacilityTransition,
    LifecycleFacts,
    allowed_status_changes,
    next_facility_status,
    state_machine_definition,
)
from app.domain.workflow import Role
from app.models import LedgerEventModel
from app.models_facility import FacilityActionModel, FinancingFacilityModel
from app.models_lifecycle import FacilityStatusTransitionModel
from app.schemas_facility import RecordRecoveryRequest
from app.services.facility import FacilityConflict, ForbiddenFacility
from app.services.outcomes import derive_outcome_facts
from app.services.facility import FacilityService
from test_facility_service import (
    _activate,
    _approved_application,
    _users,
    _command,
    _decision,
    _defaulted,
    _mark_overdue,
    _open_disposal,
    _restructure,
    _start_recovery,
    _submit,
    _write_off,
    _lifecycle,
)


@pytest.fixture
def facility_context(session_factory):
    users = _users(session_factory)
    request_id = _approved_application(session_factory, users)
    return FacilityService(session_factory), users, request_id


def _migration_module():
    path = Path("alembic/versions/20260924_0015_facility_lifecycle_governance.py")
    spec = importlib.util.spec_from_file_location("lifecycle_0015", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _recovery(version: int, amount: str, reference: str, source: str = "GUARANTOR"):
    return RecordRecoveryRequest(
        version=version,
        idempotency_key=uuid.uuid4(),
        amount=amount,
        source=source,
        recovery_reference=reference,
        evidence_sha256="9" * 64,
    )


def _pay(service, users, facility: dict, index: int, amount: str, reference: str) -> dict:
    facility = service.submit_payment(
        facility["facility_id"],
        _submit(facility, index, amount, reference),
        users["supplier.demo"],
    )
    return service.decide_payment(
        facility["facility_id"],
        facility["payments"][-1]["payment_id"],
        _decision(facility["version"]),
        users["financier.demo"],
    )


def _ticking_clock(start: datetime):
    ticks = iter(range(1, 10_000_000))
    return lambda: start + timedelta(microseconds=next(ticks))


def _transitions(service, facility_id: str) -> list[FacilityStatusTransitionModel]:
    with service.session_factory() as session:
        return list(
            session.scalars(
                select(FacilityStatusTransitionModel)
                .where(FacilityStatusTransitionModel.facility_id == uuid.UUID(facility_id))
                .order_by(FacilityStatusTransitionModel.transition_id)
            )
        )


# --- State machine definition -------------------------------------------------


def test_database_guard_pairs_are_the_domain_state_machine():
    assert frozenset(
        (FacilityStatus(source), FacilityStatus(target))
        for source, target in _migration_module()._ALLOWED_STATUS_CHANGES
    ) == allowed_status_changes()


def test_every_stage_declares_entry_conditions_and_role_actions():
    definition = state_machine_definition()
    stages = {row["status"]: row for row in definition["stages"]}
    assert set(stages) == {status.value for status in FacilityStatus}
    assert [stage.status for stage in STAGE_CATALOG] == list(FacilityStatus)
    for status, stage in stages.items():
        assert stage["entry_conditions"], status
        assert stage["label_zh"] and stage["label_en"]
        if status == "closed":
            assert stage["terminal"] and stage["allowed_actions"] == {}
        else:
            assert stage["allowed_actions"], status
    assert stages["overdue"]["allowed_actions"]["risk_manager"] == ["open_disposal"]
    assert stages["in_recovery"]["allowed_actions"]["auditor"] == ["write_off"]
    assert {"from": "overdue", "to": "active"} in definition["transitions"]
    assert {"from": "overdue", "to": "defaulted"} not in definition["transitions"]


@pytest.mark.parametrize(
    ("current", "action", "role"),
    (
        # Skipping risk disposal is illegal.
        (FacilityStatus.OVERDUE, FacilityAction.DECLARE_DEFAULT, Role.RISK_MANAGER),
        (FacilityStatus.OVERDUE, FacilityAction.RESTRUCTURE, Role.RISK_MANAGER),
        (FacilityStatus.RESTRUCTURED, FacilityAction.DECLARE_DEFAULT, Role.RISK_MANAGER),
        # Write-off only after recovery was attempted.
        (FacilityStatus.DEFAULTED, FacilityAction.WRITE_OFF, Role.AUDITOR),
        (FacilityStatus.ACTIVE, FacilityAction.WRITE_OFF, Role.AUDITOR),
        # Recovery only after default.
        (FacilityStatus.IN_DISPOSAL, FacilityAction.START_RECOVERY, Role.RISK_MANAGER),
        (FacilityStatus.ACTIVE, FacilityAction.RECORD_RECOVERY, Role.FINANCIER),
        (FacilityStatus.ACTIVE, FacilityAction.OPEN_DISPOSAL, Role.RISK_MANAGER),
        # Close only from a settled stage.
        (FacilityStatus.DEFAULTED, FacilityAction.CLOSE, Role.AUDITOR),
        (FacilityStatus.IN_RECOVERY, FacilityAction.CLOSE, Role.AUDITOR),
        (FacilityStatus.ACTIVE, FacilityAction.CLOSE, Role.AUDITOR),
        # Roles are part of the transition key.
        (FacilityStatus.OVERDUE, FacilityAction.OPEN_DISPOSAL, Role.FINANCIER),
        (FacilityStatus.IN_RECOVERY, FacilityAction.WRITE_OFF, Role.RISK_MANAGER),
        # Closed is terminal.
        (FacilityStatus.CLOSED, FacilityAction.SUBMIT_PAYMENT, Role.SUPPLIER),
        (FacilityStatus.CLOSED, FacilityAction.RECORD_RECOVERY, Role.FINANCIER),
    ),
)
def test_illegal_jumps_are_rejected_by_the_domain(current, action, role):
    with pytest.raises(InvalidFacilityTransition):
        next_facility_status(current, action, role)


def test_resolved_transitions_depend_on_immutable_history_not_balance():
    settle = FacilityAction.CONFIRM_FINAL_PAYMENT
    assert next_facility_status(
        FacilityStatus.ACTIVE, settle, Role.FINANCIER
    ) is FacilityStatus.REPAID
    assert next_facility_status(
        FacilityStatus.RESTRUCTURED,
        settle,
        Role.FINANCIER,
        facts=LifecycleFacts(has_default_history=True, schedule_version=2),
    ) is FacilityStatus.RECOVERED
    assert next_facility_status(
        FacilityStatus.IN_RECOVERY, settle, Role.FINANCIER
    ) is FacilityStatus.RECOVERED
    assert next_facility_status(
        FacilityStatus.OVERDUE, FacilityAction.CURE_OVERDUE, Role.FINANCIER
    ) is FacilityStatus.ACTIVE
    assert next_facility_status(
        FacilityStatus.OVERDUE,
        FacilityAction.CURE_OVERDUE,
        Role.FINANCIER,
        facts=LifecycleFacts(schedule_version=2),
    ) is FacilityStatus.RESTRUCTURED


# --- Audited transitions --------------------------------------------------------


def test_every_status_change_is_an_audited_transition_matching_the_command_log(
    facility_context,
):
    service, users, request_id = facility_context
    facility = _defaulted(service, users, request_id)
    facility = _start_recovery(service, users, facility)
    facility = service.write_off(
        facility["facility_id"], _write_off(facility["version"]), users["auditor.demo"]
    )
    facility = service.close(
        facility["facility_id"], _command(facility["version"]), users["auditor.demo"]
    )

    rows = _transitions(service, facility["facility_id"])
    assert [(row.from_status, row.to_status, row.trigger_action) for row in rows] == [
        (None, "ready_for_disbursement", "create"),
        ("ready_for_disbursement", "disbursed", "initiate_disbursement"),
        ("disbursed", "active", "confirm_disbursement"),
        ("active", "overdue", "mark_overdue"),
        ("overdue", "in_disposal", "open_disposal"),
        ("in_disposal", "defaulted", "declare_default"),
        ("defaulted", "in_recovery", "start_recovery"),
        ("in_recovery", "written_off", "write_off"),
        ("written_off", "closed", "close"),
    ]
    assert [row["to_status"] for row in facility["status_history"]] == [
        row.to_status for row in rows
    ]
    assert all(row.actor_user_id is not None for row in rows)
    with service.session_factory() as session:
        actions = {
            row.action_type: row.resulting_version
            for row in session.scalars(
                select(FacilityActionModel).where(
                    FacilityActionModel.facility_id == uuid.UUID(facility["facility_id"])
                )
            )
        }
    for row in rows:
        assert actions[row.trigger_action] == row.resulting_version
    assert rows[5].reason_code == "PAYMENT_DEFAULT"
    assert rows[4].reason_code == "ARREARS_WORKOUT"


def test_database_rejects_illegal_jump_and_unaudited_status_change(facility_context):
    service, users, request_id = facility_context
    facility = _activate(service, users, request_id)
    facility_id = uuid.UUID(facility["facility_id"])
    with pytest.raises(DBAPIError, match="illegal facility status transition active -> closed"):
        with service.session_factory.begin() as session:
            session.execute(
                text("UPDATE financing_facilities SET status = 'closed' WHERE facility_id = :id"),
                {"id": facility_id},
            )
    with pytest.raises(DBAPIError, match="has no audited transition"):
        with service.session_factory.begin() as session:
            session.execute(
                text(
                    "UPDATE financing_facilities SET status = 'overdue', version = version + 1 "
                    "WHERE facility_id = :id"
                ),
                {"id": facility_id},
            )
    unchanged = service.get(facility["facility_id"], users["financier.demo"])
    assert (unchanged["status"], unchanged["version"]) == ("active", facility["version"])


@pytest.mark.parametrize(
    "table",
    (
        "facility_status_transitions",
        "facility_lifecycle_decisions",
        "facility_contract_versions",
        "facility_actions",
    ),
)
def test_lifecycle_history_rejects_update_and_delete(facility_context, table):
    service, users, request_id = facility_context
    _open_disposal(service, users, _mark_overdue(service, users, _activate(service, users, request_id)))
    for statement in (f"UPDATE {table} SET facility_id = facility_id", f"DELETE FROM {table}"):
        with pytest.raises(DBAPIError, match="governed history is immutable"):
            with service.session_factory.begin() as session:
                session.execute(text(statement))


# --- Overdue, cure and risk disposal -------------------------------------------


def test_partial_payment_keeps_overdue_and_full_arrears_payment_cures(facility_context):
    service, users, request_id = facility_context
    facility = _mark_overdue(service, users, _activate(service, users, request_id))
    facility = _pay(service, users, facility, 0, "200.00", "ARREARS-PART")
    assert facility["status"] == "overdue"
    assert facility["installments"][0]["status"] == "overdue"
    # 300.00 left on the first installment plus the second, also past due.
    assert facility["arrears_amount"] == "800.00"

    # The second installment (due 2025-02-01) is also past due: paying only the
    # first one still leaves arrears, so the facility stays overdue.
    facility = _pay(service, users, facility, 0, "300.00", "ARREARS-REST")
    assert facility["status"] == "overdue"
    assert facility["installments"][0]["status"] == "paid"

    service.clock = lambda: datetime(2025, 1, 15, tzinfo=timezone.utc)
    cured_view = service.get(facility["facility_id"], users["financier.demo"])
    assert cured_view["arrears_amount"] == "0.00"
    service.clock = lambda: datetime.now(timezone.utc)


def test_confirmed_payment_clearing_every_arrear_cures_to_performing(facility_context):
    service, users, request_id = facility_context
    service.clock = lambda: datetime(2025, 1, 15, tzinfo=timezone.utc)
    facility = _mark_overdue(service, users, _activate(service, users, request_id))
    facility = _pay(service, users, facility, 0, "500.00", "CURE-CASH")
    assert facility["status"] == "active"
    assert facility["arrears_amount"] == "0.00"
    assert facility["status_history"][-1] == {
        **facility["status_history"][-1],
        "from_status": "overdue",
        "to_status": "active",
        "trigger_action": "cure_overdue",
    }
    with service.session_factory() as session:
        events = list(
            session.scalars(
                select(LedgerEventModel.event_type)
                .where(LedgerEventModel.entity_id == uuid.UUID(facility["facility_id"]))
                .order_by(LedgerEventModel.id)
            )
        )
    assert events[-2:] == ["REPAYMENT_CONFIRMED", "FACILITY_OVERDUE_CURED"]


def test_risk_disposal_is_governed_and_closes_only_without_arrears(facility_context):
    service, users, request_id = facility_context
    service.clock = _ticking_clock(datetime(2025, 1, 15, tzinfo=timezone.utc))
    facility = _mark_overdue(service, users, _activate(service, users, request_id))
    with pytest.raises(ForbiddenFacility):
        service.open_disposal(
            facility["facility_id"], _lifecycle(facility["version"]), users["financier.demo"]
        )
    facility = _open_disposal(service, users, facility)
    assert facility["status"] == "in_disposal"
    assert facility["lifecycle_decisions"][0]["decision_type"] == "disposal_opened"
    with pytest.raises(FacilityConflict, match="every arrear"):
        service.close_disposal(
            facility["facility_id"], _lifecycle(facility["version"]), users["risk.demo"]
        )
    facility = _pay(service, users, facility, 0, "500.00", "WORKOUT-CASH")
    # Payments in disposal never auto-cure: the risk manager closes the case.
    assert facility["status"] == "in_disposal"
    assert "close_disposal" in service.get(
        facility["facility_id"], users["risk.demo"]
    )["allowed_actions"]
    facility = service.close_disposal(
        facility["facility_id"], _lifecycle(facility["version"], "ARREARS_CLEARED"),
        users["risk.demo"],
    )
    assert facility["status"] == "active"
    assert [row["decision_type"] for row in facility["lifecycle_decisions"]] == [
        "disposal_opened",
        "disposal_closed",
    ]


# --- Default, recovery, write-off ----------------------------------------------


def test_default_is_not_erased_when_the_balance_reaches_zero(facility_context):
    service, users, request_id = facility_context
    facility = _defaulted(service, users, request_id)
    for index, reference in ((0, "POST-DEFAULT-A"), (1, "POST-DEFAULT-B")):
        facility = _pay(service, users, facility, index, "500.00", reference)
    assert facility["outstanding_amount"] == "0.00"
    assert facility["status"] == "recovered"
    assert facility["repaid_at"] is None
    closed = service.close(
        facility["facility_id"], _command(facility["version"]), users["auditor.demo"]
    )
    assert closed["closure_reason"] == "settled_after_default"
    assert closed["settlement_classification"] == "SETTLED_AFTER_DEFAULT"
    with service.session_factory() as session:
        row = session.get(FinancingFacilityModel, uuid.UUID(closed["facility_id"]))
        assert derive_outcome_facts(session, row).defaulted is True


def test_recovery_cash_reduces_balance_conserves_principal_and_settles(facility_context):
    service, users, request_id = facility_context
    facility = _defaulted(service, users, request_id)
    with pytest.raises(FacilityConflict, match="cannot record_recovery from defaulted"):
        service.record_recovery(
            facility["facility_id"], _recovery(facility["version"], "100.00", "EARLY"),
            users["financier.demo"],
        )
    facility = _start_recovery(service, users, facility)
    assert facility["lifecycle_decisions"][-1]["decision_type"] == "recovery_started"
    with pytest.raises(ForbiddenFacility):
        service.record_recovery(
            facility["facility_id"], _recovery(facility["version"], "100.00", "WRONG-ROLE"),
            users["risk.demo"],
        )
    facility = service.record_recovery(
        facility["facility_id"],
        _recovery(facility["version"], "600.00", "GUARANTEE-1"),
        users["financier.demo"],
    )
    assert facility["status"] == "in_recovery"
    assert facility["outstanding_amount"] == "400.00"
    assert facility["recovery_collected_amount"] == "600.00"
    with pytest.raises(FacilityConflict, match="already exists"):
        service.record_recovery(
            facility["facility_id"],
            _recovery(facility["version"], "10.00", "GUARANTEE-1"),
            users["financier.demo"],
        )
    with pytest.raises(FacilityConflict, match="exceeds"):
        service.record_recovery(
            facility["facility_id"],
            _recovery(facility["version"], "400.01", "TOO-MUCH"),
            users["financier.demo"],
        )
    facility = service.record_recovery(
        facility["facility_id"],
        _recovery(facility["version"], "400.00", "COLLATERAL-1", "COLLATERAL"),
        users["financier.demo"],
    )
    assert facility["status"] == "recovered"
    assert facility["outstanding_amount"] == "0.00"
    closed = service.close(
        facility["facility_id"], _command(facility["version"]), users["auditor.demo"]
    )
    assert closed["settlement_classification"] == "SETTLED_AFTER_DEFAULT"
    assert closed["written_off_amount"] == closed["net_loss"] == "0.00"


def test_database_rejects_recovery_that_breaks_principal_conservation(facility_context):
    service, users, request_id = facility_context
    facility = _start_recovery(service, users, _defaulted(service, users, request_id))
    with pytest.raises(DBAPIError, match="principal conservation"):
        with service.session_factory.begin() as session:
            session.execute(
                text(
                    "INSERT INTO facility_recoveries (recovery_id, facility_id, amount, "
                    "applied_to, source, recovery_reference, evidence_sha256, "
                    "recorded_by_user_id, recorded_at) VALUES (:id, :facility_id, 100.00, "
                    "'outstanding', 'OTHER', 'UNFUNDED', :hash, :user_id, now())"
                ),
                {
                    "id": uuid.uuid4(),
                    "facility_id": uuid.UUID(facility["facility_id"]),
                    "hash": "1" * 64,
                    "user_id": users["financier.demo"].user_id,
                },
            )


def test_writeoff_is_not_settlement_and_the_claim_survives(facility_context):
    service, users, request_id = facility_context
    facility = _start_recovery(service, users, _defaulted(service, users, request_id))
    facility = service.record_recovery(
        facility["facility_id"],
        _recovery(facility["version"], "250.00", "PRE-WRITEOFF"),
        users["financier.demo"],
    )
    facility = service.write_off(
        facility["facility_id"], _write_off(facility["version"]), users["auditor.demo"]
    )
    assert facility["status"] == "written_off"
    assert facility["repaid_at"] is None
    assert facility["writeoff_event"]["amount"] == "750.00"
    assert facility["realized_loss"] == facility["net_loss"] == "750.00"
    assert facility["settlement_classification"] is None

    facility = service.record_recovery(
        facility["facility_id"],
        _recovery(facility["version"], "150.00", "POST-WRITEOFF", "LEGAL_ENFORCEMENT"),
        users["financier.demo"],
    )
    assert facility["status"] == "written_off"
    assert facility["outstanding_amount"] == "0.00"
    assert facility["post_writeoff_recovery_amount"] == "150.00"
    assert facility["net_loss"] == "600.00"
    assert [row["applied_to"] for row in facility["recoveries"]] == [
        "outstanding",
        "written_off",
    ]
    with pytest.raises(FacilityConflict, match="remaining written-off claim"):
        service.record_recovery(
            facility["facility_id"],
            _recovery(facility["version"], "600.01", "OVER-CLAIM"),
            users["financier.demo"],
        )
    closed = service.close(
        facility["facility_id"], _command(facility["version"]), users["auditor.demo"]
    )
    assert closed["closure_reason"] == "written_off"
    assert closed["settlement_classification"] == "WRITTEN_OFF"
    with pytest.raises(FacilityConflict):
        service.record_recovery(
            closed["facility_id"],
            _recovery(closed["version"], "1.00", "AFTER-CLOSE"),
            users["financier.demo"],
        )
    with service.session_factory() as session:
        row = session.get(FinancingFacilityModel, uuid.UUID(closed["facility_id"]))
        facts = derive_outcome_facts(session, row)
        assert facts.defaulted is True
        assert facts.loss_amount == Decimal("750.00")


# --- Contract versions ---------------------------------------------------------


def test_restructure_appends_a_contract_version_and_freezes_history(facility_context):
    service, users, request_id = facility_context
    facility = _open_disposal(
        service, users, _mark_overdue(service, users, _activate(service, users, request_id))
    )
    facility = service.restructure(
        facility["facility_id"], _restructure(facility["version"], total="1000.00"),
        users["risk.demo"],
    )
    original, replacement = facility["contract_versions"]
    assert original["schedule"] == [
        {"sequence": 1, "due_date": "2025-01-01", "amount": "500.00"},
        {"sequence": 2, "due_date": "2025-02-01", "amount": "500.00"},
    ]
    assert [row["status_before"] for row in replacement["superseded_schedule"]] == [
        "overdue",
        "scheduled",
    ]
    with service.session_factory() as session:
        restructured_event = session.scalar(
            select(LedgerEventModel.payload).where(
                LedgerEventModel.entity_id == uuid.UUID(facility["facility_id"]),
                LedgerEventModel.event_type == "FACILITY_RESTRUCTURED",
            )
        )
    assert restructured_event["previous_contract_sha256"] == original["terms_sha256"]
    assert restructured_event["contract_sha256"] == replacement["terms_sha256"]

    superseded_id = facility["installments"][0]["installment_id"]
    current_id = facility["installments"][-1]["installment_id"]
    for statement, parameters, message in (
        (
            "UPDATE facility_installments SET status = 'scheduled' WHERE installment_id = :id",
            {"id": superseded_id},
            "superseded installments are frozen",
        ),
        (
            "UPDATE facility_installments SET amount = 1.00 WHERE installment_id = :id",
            {"id": current_id},
            "contract terms are immutable",
        ),
        (
            "UPDATE facility_installments SET due_date = '2030-01-01' WHERE installment_id = :id",
            {"id": current_id},
            "contract terms are immutable",
        ),
        (
            "DELETE FROM facility_installments WHERE installment_id = :id",
            {"id": superseded_id},
            "cannot be deleted",
        ),
    ):
        with pytest.raises(DBAPIError, match=message):
            with service.session_factory.begin() as session:
                session.execute(text(statement), parameters)


# --- API -----------------------------------------------------------------------


def test_state_machine_endpoint_requires_login_and_lists_every_stage(
    migrated_engine, session_factory
):
    from app.database import Database
    from app.main import create_app

    with TestClient(create_app(Database(migrated_engine, session_factory))) as client:
        assert client.get("/api/v1/facilities/state-machine").status_code == 401
        assert client.post(
            "/api/v1/auth/login", json={"username": "auditor.demo", "password": "Demo123!"}
        ).status_code == 200
        response = client.get("/api/v1/facilities/state-machine")
    assert response.status_code == 200
    assert [row["status"] for row in response.json()["stages"]] == [
        status.value for status in FacilityStatus
    ]


# --- Migration -----------------------------------------------------------------


def test_upgrade_backfills_history_append_only_and_downgrade_is_guarded(
    isolated_postgres_engine,
):
    engine = isolated_postgres_engine
    config = Config("alembic.ini")
    with engine.connect() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "20260908_0014")
        connection.commit()
    with engine.begin() as connection:
        user_id = uuid.uuid4()
        organization_id = uuid.uuid4()
        request_id = uuid.uuid4()
        facility_id = uuid.uuid4()
        connection.execute(
            text(
                "INSERT INTO organizations (organization_id, organization_code, name, "
                "organization_type, created_at) VALUES (:id, 'FIN-LEGACY', 'Legacy financier', "
                "'financier', now())"
            ),
            {"id": organization_id},
        )
        connection.execute(
            text(
                "INSERT INTO users (user_id, username, display_name, password_hash, "
                "password_salt, role, organization_id, is_active, created_at) VALUES (:id, "
                "'legacy.financier', 'Legacy', 'x', 'y', 'financier', :org, true, now())"
            ),
            {"id": user_id, "org": organization_id},
        )
        connection.execute(
            text(
                "INSERT INTO financing_requests (request_id, created_at, applicant_id, amount, "
                "term_days, features, risk_score, decision, explanations, control_action, "
                "assessment_scope) VALUES (:id, now(), 'legacy', 1000, 30, '{}', 0.2, "
                "'approved', '[]', 'standard_monitoring', 'controlled_demo')"
            ),
            {"id": request_id},
        )
        connection.execute(
            text(
                "INSERT INTO financing_facilities (facility_id, request_id, principal, "
                "outstanding_amount, currency, status, version, current_schedule_version, "
                "created_by_user_id, created_at, updated_at) VALUES (:id, :request, 1000.00, "
                "1000.00, 'RUB', 'overdue', 5, 1, :user, now(), now())"
            ),
            {"id": facility_id, "request": request_id, "user": user_id},
        )
        for sequence, due in ((1, "2025-01-01"), (2, "2025-02-01")):
            connection.execute(
                text(
                    "INSERT INTO facility_installments (installment_id, facility_id, sequence, "
                    "schedule_version, due_date, amount, paid_amount, status, created_at, "
                    "updated_at) VALUES (:id, :facility, :sequence, 1, :due, 500.00, 0.00, "
                    "'scheduled', now(), now())"
                ),
                {"id": uuid.uuid4(), "facility": facility_id, "sequence": sequence, "due": due},
            )
        before = connection.execute(
            text("SELECT * FROM financing_facilities WHERE facility_id = :id"), {"id": facility_id}
        ).mappings().one()

    with engine.connect() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "20260924_0015")
        connection.commit()
    with engine.connect() as connection:
        after = connection.execute(
            text("SELECT * FROM financing_facilities WHERE facility_id = :id"), {"id": facility_id}
        ).mappings().one()
        assert dict(after) == dict(before)
        baseline = connection.execute(
            text("SELECT from_status, to_status, trigger_action, resulting_version "
                 "FROM facility_status_transitions")
        ).all()
        assert baseline == [(None, "overdue", "migration_baseline", 5)]
        contract = connection.execute(
            text("SELECT contract_version, origin, schedule FROM facility_contract_versions")
        ).one()
        assert contract[:2] == (1, "migration_backfill")
        assert [row["amount"] for row in contract[2]] == ["500.00", "500.00"]

    # Only backfill exists: the downgrade is allowed and returns the old schema.
    with engine.connect() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "20260908_0014")
        connection.commit()
        assert connection.scalar(
            text("SELECT to_regclass('facility_status_transitions')")
        ) is None
        command.upgrade(config, "head")
        connection.commit()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO facility_lifecycle_decisions (decision_id, facility_id, "
                "decision_type, schedule_version, decided_by_user_id, reason_code, comment, "
                "evidence_sha256, recorded_at) VALUES (:id, :facility, 'disposal_opened', 1, "
                ":user, 'ARREARS_WORKOUT', 'Opened', :hash, now())"
            ),
            {"id": uuid.uuid4(), "facility": facility_id, "user": user_id, "hash": "2" * 64},
        )
    with engine.connect() as connection:
        config.attributes["connection"] = connection
        with pytest.raises(RuntimeError, match="governed lifecycle history"):
            command.downgrade(config, "20260908_0014")
        connection.rollback()
        assert connection.scalar(
            select(func.count()).select_from(text("facility_lifecycle_decisions"))
        ) == 1
