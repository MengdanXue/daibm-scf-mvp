"""Real principal repayment records for synthetic closed-facility fixtures."""
import uuid
from decimal import Decimal

from app.models_facility import InstallmentModel, PaymentModel


def confirmed_cash_rows(facility_id, user_id, recorded_at, amount="1000.00"):
    installment_id = uuid.uuid4()
    cash = Decimal(amount)
    return [
        InstallmentModel(
            installment_id=installment_id, facility_id=facility_id,
            sequence=1, schedule_version=1, due_date=recorded_at.date(),
            amount=cash, paid_amount=cash, status="paid",
            created_at=recorded_at, updated_at=recorded_at,
        ),
        PaymentModel(
            payment_id=uuid.uuid4(), facility_id=facility_id,
            installment_id=installment_id, submitted_by_user_id=user_id,
            amount=cash, payment_reference=f"fixture-{installment_id}",
            status="confirmed", submitted_at=recorded_at, decided_at=recorded_at,
            decided_by_user_id=user_id, decision_comment="Verified fixture principal cash",
        ),
    ]
