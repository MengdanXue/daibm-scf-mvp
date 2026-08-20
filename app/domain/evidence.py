from __future__ import annotations

import hashlib
from decimal import Decimal
from typing import Any

from app.ledger import canonical_json


def evidence_sha256(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def trade_evidence(
    *,
    supplier_code: str,
    core_enterprise_code: str,
    contract_number: str,
    invoice_number: str,
    amount: Decimal,
    term_days: int,
) -> tuple[str, str]:
    normalized_invoice = invoice_number.strip().casefold()
    invoice_claim_sha256 = evidence_sha256(
        {
            "supplier_code": supplier_code,
            "core_enterprise_code": core_enterprise_code,
            "invoice_number": normalized_invoice,
        }
    )
    fingerprint_sha256 = evidence_sha256(
        {
            "supplier_code": supplier_code,
            "core_enterprise_code": core_enterprise_code,
            "contract_number": contract_number.strip(),
            "invoice_number": normalized_invoice,
            "amount": format(amount, ".2f"),
            "term_days": term_days,
        }
    )
    return fingerprint_sha256, invoice_claim_sha256
