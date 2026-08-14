from app.risk import assess
from app.schemas import FinancingRequestCreate


def request(**overrides):
    values = {
        "applicant_id": "supplier-test",
        "amount": 500_000,
        "term_days": 60,
        "payment_delay_days": 2,
        "counterparty_risk": 0.1,
        "invoice_mismatch": False,
        "relationship_months": 48,
        "transactions_last_30d": 8,
    }
    values.update(overrides)
    return FinancingRequestCreate(**values)


def test_risk_orders_stable_and_risky_scenarios():
    stable = assess(request())
    risky = assess(
        request(
            amount=4_500_000,
            payment_delay_days=55,
            counterparty_risk=0.9,
            invoice_mismatch=True,
            relationship_months=1,
            transactions_last_30d=50,
        )
    )
    assert stable.score < risky.score
    assert stable.band == "low"
    assert risky.band == "high"
    assert risky.contributions[0]["weighted_contribution"] > 0

