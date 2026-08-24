"""Allow financing-lifecycle events in the audit ledger.

Revision ID: 20260824_0005a
Revises: 20260824_0005
Create Date: 2026-08-24
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260824_0005a"
down_revision: str | None = "20260824_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_PREVIOUS_LEDGER_EVENT_TYPES = (
    "FINANCING_REQUEST",
    "RISK_ASSESSMENT",
    "FINANCING_DECISION",
    "CONTROL_ACTION",
    "MODEL_INFERENCE_COMPLETED",
    "RISK_POLICY_TRIGGERED",
    "CONTROL_ACTION_REQUESTED",
    "SIMULATED_RISK_INJECTED",
    "INTEGRITY_VIOLATION_DETECTED",
    "LEDGER_RECOVERY_COMPLETED",
    "APPLICATION_DRAFT_CREATED",
    "APPLICATION_UPDATED",
    "APPLICATION_SUBMITTED",
    "TRADE_CONFIRMED",
    "TRADE_RETURNED",
    "AUDIT_REVIEW_COMPLETED",
)
_FACILITY_LEDGER_EVENT_TYPES = (
    "FACILITY_CREATED",
    "DISBURSEMENT_INITIATED",
    "DISBURSEMENT_CONFIRMED",
    "REPAYMENT_SUBMITTED",
    "REPAYMENT_CONFIRMED",
    "REPAYMENT_REJECTED",
    "FACILITY_MARKED_OVERDUE",
    "FACILITY_REPAID",
    "FACILITY_CLOSED",
)


def _event_type_check(values: tuple[str, ...]) -> str:
    return "event_type IN (" + ", ".join(f"'{value}'" for value in values) + ")"


def upgrade() -> None:
    op.drop_constraint(
        "ck_ledger_events_event_type",
        "ledger_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_ledger_events_event_type",
        "ledger_events",
        _event_type_check(
            _PREVIOUS_LEDGER_EVENT_TYPES + _FACILITY_LEDGER_EVENT_TYPES
        ),
    )


def downgrade() -> None:
    facility_event_sql = ", ".join(
        f"'{event_type}'" for event_type in _FACILITY_LEDGER_EVENT_TYPES
    )
    count = op.get_bind().scalar(
        sa.text(
            "SELECT count(*) FROM ledger_events "
            f"WHERE event_type IN ({facility_event_sql})"
        )
    )
    if count:
        raise RuntimeError(
            "Cannot downgrade to 20260824_0005: facility ledger events exist; "
            "the append-only audit chain was left unchanged"
        )
    op.drop_constraint(
        "ck_ledger_events_event_type",
        "ledger_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_ledger_events_event_type",
        "ledger_events",
        _event_type_check(_PREVIOUS_LEDGER_EVENT_TYPES),
    )
