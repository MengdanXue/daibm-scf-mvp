from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import DateTime, Numeric, inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError


FACILITY_TABLES = {
    "financing_facilities",
    "facility_installments",
    "facility_payments",
    "facility_actions",
}


def _seed_prerequisites(session_factory):
    organization_id = uuid.uuid4()
    user_id = uuid.uuid4()
    request_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    with session_factory.begin() as session:
        session.execute(
            text(
                "INSERT INTO organizations "
                "(organization_id, organization_code, name, organization_type, created_at) "
                "VALUES (:id, :code, 'Test financier', 'financier', :created_at)"
            ),
            {
                "id": organization_id,
                "code": f"FIN-{organization_id.hex[:12]}",
                "created_at": now,
            },
        )
        session.execute(
            text(
                "INSERT INTO users "
                "(user_id, username, display_name, password_hash, password_salt, "
                "role, organization_id, is_active, created_at) "
                "VALUES (:id, :username, 'Test financier', 'hash', 'salt', "
                "'financier', :organization_id, true, :created_at)"
            ),
            {
                "id": user_id,
                "username": f"financier-{user_id.hex[:12]}",
                "organization_id": organization_id,
                "created_at": now,
            },
        )
        session.execute(
            text(
                "INSERT INTO financing_requests "
                "(request_id, created_at, updated_at, applicant_id, assessment_scope, amount, "
                "term_days, features, risk_score, decision, explanations, "
                "control_action, status, version) "
                "VALUES (:id, :created_at, :created_at, 'supplier-test', "
                "'controlled_demo', 1000.00, "
                "30, '{}'::jsonb, 0.1, 'approved', '[]'::jsonb, NULL, 'audited', 1)"
            ),
            {"id": request_id, "created_at": now},
        )
    return request_id, user_id


def _facility(*, request_id, user_id, facility_id=None):
    from app.models_facility import FinancingFacilityModel

    now = datetime.now(timezone.utc)
    return FinancingFacilityModel(
        facility_id=facility_id or uuid.uuid4(),
        request_id=request_id,
        principal=Decimal("1000.00"),
        outstanding_amount=Decimal("1000.00"),
        currency="RUB",
        status="ready_for_disbursement",
        version=1,
        created_by_user_id=user_id,
        created_at=now,
        updated_at=now,
    )


def test_clean_migration_creates_exact_postgresql_facility_schema(migrated_engine):
    inspector = inspect(migrated_engine)
    assert FACILITY_TABLES <= set(inspector.get_table_names())

    facility_columns = {
        column["name"]: column
        for column in inspector.get_columns("financing_facilities")
    }
    installment_columns = {
        column["name"]: column
        for column in inspector.get_columns("facility_installments")
    }
    payment_columns = {
        column["name"]: column
        for column in inspector.get_columns("facility_payments")
    }

    for column in (
        facility_columns["principal"],
        facility_columns["outstanding_amount"],
        installment_columns["amount"],
        installment_columns["paid_amount"],
        payment_columns["amount"],
    ):
        assert isinstance(column["type"], Numeric)
        assert (column["type"].precision, column["type"].scale) == (14, 2)
        assert column["nullable"] is False

    for table in FACILITY_TABLES:
        timestamp_columns = [
            column
            for column in inspector.get_columns(table)
            if column["name"].endswith("_at")
        ]
        assert timestamp_columns
        assert all(
            isinstance(column["type"], DateTime) and column["type"].timezone
            for column in timestamp_columns
        )


def test_financial_foreign_keys_are_restrictive_and_explicitly_indexed(
    migrated_engine,
):
    inspector = inspect(migrated_engine)
    for table in FACILITY_TABLES:
        indexes = {
            tuple(index["column_names"])
            for index in inspector.get_indexes(table)
            if index["column_names"]
        }
        for foreign_key in inspector.get_foreign_keys(table):
            assert foreign_key["options"].get("ondelete") == "RESTRICT"
            constrained = tuple(foreign_key["constrained_columns"])
            assert any(index[: len(constrained)] == constrained for index in indexes), (
                table,
                constrained,
            )


def test_schema_declares_business_checks_and_idempotency_uniqueness(
    migrated_engine,
):
    inspector = inspect(migrated_engine)
    check_sql = {
        table: " ".join(
            constraint["sqltext"]
            for constraint in inspector.get_check_constraints(table)
        ).lower()
        for table in FACILITY_TABLES
    }

    assert "outstanding_amount" in check_sql["financing_facilities"]
    assert "principal" in check_sql["financing_facilities"]
    assert "ready_for_disbursement" in check_sql["financing_facilities"]
    assert "char_length(disbursement_evidence_sha256) = 64" in check_sql[
        "financing_facilities"
    ]
    assert "paid_amount" in check_sql["facility_installments"]
    assert "scheduled" in check_sql["facility_installments"]
    assert "submitted" in check_sql["facility_payments"]
    assert "char_length(evidence_sha256) = 64" in check_sql["facility_payments"]

    action_uniques = {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("facility_actions")
    }
    payment_uniques = {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("facility_payments")
    }
    installment_uniques = {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("facility_installments")
    }
    assert ("idempotency_key",) in action_uniques
    assert ("facility_id", "payment_reference") in payment_uniques
    assert ("facility_id", "schedule_version", "sequence") in installment_uniques


def test_database_rejects_invalid_outstanding_and_duplicate_idempotency(
    session_factory,
):
    from app.models_facility import FacilityActionModel

    request_id, user_id = _seed_prerequisites(session_factory)
    facility = _facility(request_id=request_id, user_id=user_id)
    with session_factory.begin() as session:
        session.add(facility)

    with pytest.raises(IntegrityError):
        with session_factory.begin() as session:
            session.execute(
                text(
                    "UPDATE financing_facilities "
                    "SET outstanding_amount = principal + 0.01 "
                    "WHERE facility_id = :facility_id"
                ),
                {"facility_id": facility.facility_id},
            )

    key = uuid.uuid4()
    now = datetime.now(timezone.utc)
    with session_factory.begin() as session:
        session.add(
            FacilityActionModel(
                facility_id=facility.facility_id,
                actor_user_id=user_id,
                actor_role="financier",
                action_type="create",
                idempotency_key=key,
                expected_version=1,
                resulting_version=1,
                payload={},
                created_at=now,
            )
        )
    with pytest.raises(IntegrityError):
        with session_factory.begin() as session:
            session.add(
                FacilityActionModel(
                    facility_id=facility.facility_id,
                    actor_user_id=user_id,
                    actor_role="financier",
                    action_type="initiate_disbursement",
                    idempotency_key=key,
                    expected_version=1,
                    resulting_version=2,
                    payload={},
                    created_at=now,
                )
            )


def test_payment_must_reference_an_installment_owned_by_the_same_facility(
    session_factory,
):
    from app.models_facility import InstallmentModel, PaymentModel

    request_a, user_a = _seed_prerequisites(session_factory)
    request_b, user_b = _seed_prerequisites(session_factory)
    facility_a = _facility(request_id=request_a, user_id=user_a)
    facility_b = _facility(request_id=request_b, user_id=user_b)
    installment_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    with session_factory.begin() as session:
        session.add_all([facility_a, facility_b])
        session.add(
            InstallmentModel(
                installment_id=installment_id,
                facility_id=facility_a.facility_id,
                sequence=1,
                due_date=date(2026, 10, 1),
                amount=Decimal("1000.00"),
                paid_amount=Decimal("0.00"),
                status="scheduled",
                created_at=now,
                updated_at=now,
            )
        )

    with pytest.raises(IntegrityError):
        with session_factory.begin() as session:
            session.add(
                PaymentModel(
                    payment_id=uuid.uuid4(),
                    facility_id=facility_b.facility_id,
                    installment_id=installment_id,
                    submitted_by_user_id=user_b,
                    amount=Decimal("100.00"),
                    payment_reference="CROSS-FACILITY",
                    status="submitted",
                    submitted_at=now,
                )
            )


def test_repository_add_lookup_and_ordered_child_lists(session_factory):
    from app.models_facility import InstallmentModel, PaymentModel
    from app.repositories.facility import FacilityRepository

    request_id, user_id = _seed_prerequisites(session_factory)
    facility = _facility(request_id=request_id, user_id=user_id)
    now = datetime.now(timezone.utc)
    repository = FacilityRepository()

    with session_factory.begin() as session:
        repository.add(session, facility)
        session.add_all(
            [
                InstallmentModel(
                    installment_id=uuid.uuid4(),
                    facility_id=facility.facility_id,
                    sequence=2,
                    due_date=date(2026, 11, 1),
                    amount=Decimal("500.00"),
                    paid_amount=Decimal("0.00"),
                    status="scheduled",
                    created_at=now,
                    updated_at=now,
                ),
                InstallmentModel(
                    installment_id=uuid.uuid4(),
                    facility_id=facility.facility_id,
                    sequence=1,
                    due_date=date(2026, 10, 1),
                    amount=Decimal("500.00"),
                    paid_amount=Decimal("0.00"),
                    status="scheduled",
                    created_at=now,
                    updated_at=now,
                ),
            ]
        )
    with session_factory.begin() as session:
        installments = repository.list_installments(session, facility.facility_id)
        session.add_all(
            [
                PaymentModel(
                    payment_id=uuid.UUID(int=3),
                    facility_id=facility.facility_id,
                    installment_id=installments[0].installment_id,
                    submitted_by_user_id=user_id,
                    amount=Decimal("100.00"),
                    payment_reference="PAY-Z",
                    status="submitted",
                    submitted_at=now,
                ),
                PaymentModel(
                    payment_id=uuid.UUID(int=2),
                    facility_id=facility.facility_id,
                    installment_id=installments[0].installment_id,
                    submitted_by_user_id=user_id,
                    amount=Decimal("50.00"),
                    payment_reference="PAY-A",
                    status="submitted",
                    submitted_at=now + timedelta(seconds=1),
                ),
                PaymentModel(
                    payment_id=uuid.UUID(int=1),
                    facility_id=facility.facility_id,
                    installment_id=installments[0].installment_id,
                    submitted_by_user_id=user_id,
                    amount=Decimal("25.00"),
                    payment_reference="PAY-M",
                    status="submitted",
                    submitted_at=now + timedelta(seconds=1),
                ),
            ]
        )

    with session_factory() as session:
        assert repository.get_by_request(session, request_id).facility_id == facility.facility_id
        assert [item.sequence for item in repository.list_installments(session, facility.facility_id)] == [1, 2]
        assert [
            item.payment_reference
            for item in repository.list_payments(session, facility.facility_id)
        ] == ["PAY-Z", "PAY-M", "PAY-A"]
        assert repository.find_action(session, uuid.uuid4()) is None


def test_get_for_update_holds_a_real_postgresql_row_lock(session_factory):
    from app.repositories.facility import FacilityRepository

    request_id, user_id = _seed_prerequisites(session_factory)
    facility = _facility(request_id=request_id, user_id=user_id)
    repository = FacilityRepository()
    with session_factory.begin() as session:
        repository.add(session, facility)

    first = session_factory()
    second = session_factory()
    try:
        assert repository.get_for_update(first, facility.facility_id) is not None
        second.execute(text("SET LOCAL lock_timeout = '100ms'"))
        with pytest.raises(DBAPIError):
            second.execute(
                text(
                    "UPDATE financing_facilities SET version = version + 1 "
                    "WHERE facility_id = :facility_id"
                ),
                {"facility_id": facility.facility_id},
            )
    finally:
        first.rollback()
        second.rollback()
        first.close()
        second.close()
