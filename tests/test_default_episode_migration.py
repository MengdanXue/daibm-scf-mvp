import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.models_facility import FinancingFacilityModel, InstallmentModel
from app.models_lifecycle import (
    FacilityDefaultModel,
    FacilityDelinquencyModel,
    FacilityRestructureModel,
)
from app.identity import AuthenticatedUser
from test_facility_service import _approved_application


def _users(factory) -> dict[str, AuthenticatedUser]:
    """Accounts written with the 0014-era columns; today's identity service
    (sessions, lock-out, security events) needs the later schema."""

    organizations = {
        "supplier": ("SUPPLIER-001", "supplier"),
        "core": ("CORE-001", "core_enterprise"),
        "financier": ("BANK-001", "financier"),
        "risk": ("BANK-001", "financier"),
        "auditor": ("AUDIT-001", "auditor"),
    }
    roles = {"supplier": "supplier", "core": "core_enterprise", "financier": "financier",
             "risk": "risk_manager", "auditor": "auditor"}
    org_ids = {code: uuid.uuid4() for code, _ in organizations.values()}
    users = {}
    with factory.begin() as session:
        for code, organization_type in set(organizations.values()):
            session.execute(
                text("INSERT INTO organizations (organization_id, organization_code, name, "
                     "organization_type, created_at) VALUES (:id, :code, :code, :type, now())"),
                {"id": org_ids[code], "code": code, "type": organization_type},
            )
        for name, (code, _) in organizations.items():
            user = AuthenticatedUser(
                user_id=uuid.uuid4(), username=f"{name}.demo", display_name=name, role=roles[name],
                organization_id=org_ids[code], organization_code=code, organization_name=code,
            )
            session.execute(
                text("INSERT INTO users (user_id, username, display_name, password_hash, "
                     "password_salt, role, organization_id, is_active, created_at) VALUES "
                     "(:id, :username, :name, 'x', 'y', :role, :org, true, now())"),
                {"id": user.user_id, "username": user.username, "name": name,
                 "role": user.role, "org": user.organization_id},
            )
            users[user.username] = user
    return users

# These tests exercise revision 20260907_0013. Revision 20260924_0015 refuses to
# downgrade over governed lifecycle history, so the default-episode fixtures are
# written directly at the 0013-era schema (merge head 20260908_0014).
EPISODE_HEAD = "20260908_0014"


def _migrate(engine, target, *, down=False):
    with engine.begin() as connection:
        config = Config("alembic.ini")
        config.attributes["connection"] = connection
        (command.downgrade if down else command.upgrade)(config, target)


def _seed(engine, *, restructured=False, second_default=False):
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    users = _users(factory)
    request_id = _approved_application(factory, users, lender=None)
    now = datetime(2026, 9, 7, tzinfo=timezone.utc)
    financier = users["financier.demo"].user_id
    risk = users["risk.demo"].user_id
    facility_id = uuid.uuid4()
    default_id = uuid.uuid4()
    version = 2 if restructured else 1
    with factory.begin() as session:
        session.add(FinancingFacilityModel(
            facility_id=facility_id, request_id=request_id,
            principal=Decimal("1000.00"), outstanding_amount=Decimal("1000.00"),
            currency="RUB",
            status="defaulted" if second_default or not restructured else "restructured",
            version=6, current_schedule_version=version, created_by_user_id=financier,
            created_at=now, updated_at=now,
        ))
        session.flush()
        for sequence, due in ((1, date(2025, 1, 1)), (2, date(2025, 2, 1))):
            session.add(InstallmentModel(
                installment_id=uuid.uuid4(), facility_id=facility_id, sequence=sequence,
                schedule_version=1, due_date=due, amount=Decimal("500.00"),
                paid_amount=Decimal("0.00"),
                status="superseded" if restructured else "overdue",
                created_at=now, updated_at=now,
            ))
        if restructured:
            session.add(InstallmentModel(
                installment_id=uuid.uuid4(), facility_id=facility_id, sequence=1,
                schedule_version=2, due_date=date(2027, 1, 1), amount=Decimal("1000.00"),
                paid_amount=Decimal("0.00"), status="scheduled", created_at=now, updated_at=now,
            ))
            session.add(FacilityRestructureModel(
                restructure_id=uuid.uuid4(), facility_id=facility_id,
                restructured_by_user_id=risk, old_schedule_version=1, new_schedule_version=2,
                reason_code="BORROWER_CASH_FLOW", comment="Verified revised capacity",
                evidence_sha256="e" * 64, recorded_at=now,
            ))
        session.add(FacilityDelinquencyModel(
            delinquency_id=uuid.uuid4(), facility_id=facility_id, marked_by_user_id=financier,
            days_past_due=45, reason_code="PAST_DUE", comment="Marked overdue",
            evidence_sha256="d" * 64, recorded_at=now,
        ))
        episodes = [(default_id, 1)] + ([(uuid.uuid4(), 2)] if second_default else [])
        for episode_id, schedule_version in episodes:
            session.add(FacilityDefaultModel(
                default_id=episode_id, facility_id=facility_id,
                schedule_version=schedule_version, declared_by_user_id=risk,
                defaulted_at=now, days_past_due=90, reason_code="PAYMENT_DEFAULT",
                comment="Governed delinquency remains unresolved",
                evidence_sha256="f" * 64, recorded_at=now,
            ))
    return facility_id, default_id


def _default_count(engine):
    with engine.connect() as connection:
        return connection.scalar(text("SELECT count(*) FROM facility_defaults"))


def _bytes(engine):
    with engine.connect() as connection:
        return list(connection.scalars(text(
            "SELECT row_to_json(e)::text FROM ledger_events e ORDER BY id"
        )))


def _revisions(engine):
    with engine.connect() as connection:
        return set(connection.scalars(text("SELECT version_num FROM alembic_version")))


def test_episode_migration_roundtrip_preserves_original_payload_and_ledger(isolated_postgres_engine):
    engine = isolated_postgres_engine
    _migrate(engine, EPISODE_HEAD)
    _, default_id = _seed(engine)
    before = _bytes(engine)
    with engine.connect() as connection:
        original = connection.scalar(text("SELECT to_jsonb(d) - 'schedule_version' FROM facility_defaults d"))
    _migrate(engine, "20260907_0012", down=True)
    _migrate(engine, "head")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT to_jsonb(d) - 'schedule_version' FROM facility_defaults d")) == original
        assert connection.scalar(text("SELECT schedule_version FROM facility_defaults")) == 1
        assert str(connection.scalar(text("SELECT default_id FROM facility_defaults"))) == str(default_id)
    assert _bytes(engine) == before


def test_episode_downgrade_refuses_multiple_history_without_changes(isolated_postgres_engine):
    engine = isolated_postgres_engine
    _migrate(engine, EPISODE_HEAD)
    _seed(engine, restructured=True, second_default=True)
    before = _bytes(engine)
    revisions_before = _revisions(engine)
    with pytest.raises(RuntimeError, match="multiple default episodes"):
        _migrate(engine, "20260907_0012", down=True)
    assert _default_count(engine) == 2
    assert _bytes(engine) == before
    assert _revisions(engine) == revisions_before


def test_conservation_preflight_refuses_inconsistent_legacy_without_repair(isolated_postgres_engine):
    engine = isolated_postgres_engine
    _migrate(engine, EPISODE_HEAD)
    _seed(engine)
    _migrate(engine, "20260907_0012", down=True)
    with engine.begin() as connection:
        connection.execute(text("UPDATE financing_facilities SET outstanding_amount = 900.00"))
    before = _bytes(engine)
    revisions_before = _revisions(engine)
    with pytest.raises(RuntimeError, match="principal conservation failed for facility"):
        _migrate(engine, "head")
    with engine.connect() as connection:
        assert str(connection.scalar(text("SELECT outstanding_amount FROM financing_facilities"))) == "900.00"
    assert _revisions(engine) == revisions_before
    assert _bytes(engine) == before


def test_downgrade_refuses_single_default_followed_by_restructure(isolated_postgres_engine):
    engine = isolated_postgres_engine
    _migrate(engine, EPISODE_HEAD)
    _seed(engine, restructured=True)
    with pytest.raises(RuntimeError, match="post-default restructure"):
        _migrate(engine, "20260907_0012", down=True)
