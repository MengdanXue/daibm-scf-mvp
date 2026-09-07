"""Client for the optional invoice-limit zero-knowledge prover.

The circuit in ``advanced/zkp`` proves ``invoiceAmount <= financingLimit``
while revealing only a Poseidon commitment to the amount. The prover runs as
an internal-only sidecar for the same reason the Fabric gateway does: the
application image carries no Node runtime. In optional mode, a missing
sidecar records a fallback code; required mode blocks trade confirmation.

This client validates response structure and the public ceiling, NOT the
Groth16 pairing equation. It trusts the internal prover. Neither this client
nor Fabric hash anchoring constitutes independent proof verification.

Only the proof and its public signals cross the boundary. This module hashes
the proof itself so the digest written to the audit ledger is derived from
the same canonical encoding the ledger uses everywhere else, rather than
trusting a digest computed by another process with its own serializer.
"""

from __future__ import annotations

import hashlib
import json
import re
import socket
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.config import ZkpProverSettings
from app.ledger import canonical_json

__all__ = [
    "HttpInvoiceLimitProver",
    "InvoiceLimitProof",
    "InvoiceLimitProver",
    "ProofRejected",
    "ProverUnavailable",
]

_MAX_PROVER_BODY = 64 * 1024
_CIRCUIT_VERSION = "invoice_limit@1"
_SCALAR_FIELD = 21888242871839275222246405745257275088548364400416034343698204186575808495617
_BASE_FIELD = 21888242871839275222246405745257275088696311157297823662689037894645226208583


def _field_element(value: Any, modulus: int) -> bool:
    return (
        isinstance(value, str)
        and re.fullmatch(r"(0|[1-9][0-9]{0,76})", value) is not None
        and int(value) < modulus
    )


def _coordinate_vector(value: Any, length: int) -> bool:
    return (
        isinstance(value, list)
        and len(value) == length
        and all(_field_element(item, _BASE_FIELD) for item in value)
    )


def _groth16_shape(proof: Any) -> bool:
    # Syntax only: well-shaped coordinates can still be a forged proof.
    return (
        isinstance(proof, dict)
        and proof.get("protocol") == "groth16"
        and proof.get("curve") == "bn128"
        and _coordinate_vector(proof.get("pi_a"), 3)
        and _coordinate_vector(proof.get("pi_c"), 3)
        and isinstance(proof.get("pi_b"), list)
        and len(proof["pi_b"]) == 3
        and all(_coordinate_vector(row, 2) for row in proof["pi_b"])
    )


class ProverUnavailable(RuntimeError):
    """The prover could not be reached or answered unusably."""


class ProofRejected(RuntimeError):
    """The prover refused to prove the statement as supplied."""


@dataclass(frozen=True)
class InvoiceLimitProof:
    circuit_version: str
    proof: dict[str, Any]
    public_signals: list[str]

    @property
    def proof_sha256(self) -> str:
        return hashlib.sha256(
            canonical_json(self.proof).encode("utf-8")
        ).hexdigest()

    @property
    def commitment(self) -> str:
        return self.public_signals[0]

    def ledger_payload(self) -> dict[str, Any]:
        """Fields merged into the trade-confirmation ledger event.

        ``circuit_version`` and ``proof_sha256`` are the two keys the anchor
        outbox already lifts out of an event payload, so anchoring picks the
        proof up with no further plumbing.
        """

        return {
            "circuit_version": self.circuit_version,
            "proof_sha256": self.proof_sha256,
            "payable_commitment": self.commitment,
            "invoice_limit_proof": self.proof,
            "invoice_limit_public_signals": list(self.public_signals),
        }


class InvoiceLimitProver(Protocol):
    def prove(
        self,
        *,
        invoice_amount_minor: int,
        payable_limit_minor: int,
    ) -> InvoiceLimitProof: ...


class HttpInvoiceLimitProver:
    def __init__(self, settings: ZkpProverSettings) -> None:
        self.settings = settings

    def prove(
        self,
        *,
        invoice_amount_minor: int,
        payable_limit_minor: int,
    ) -> InvoiceLimitProof:
        if invoice_amount_minor <= 0 or payable_limit_minor <= 0:
            raise ProofRejected("Both amounts must be positive minor units")
        if invoice_amount_minor > payable_limit_minor:
            raise ProofRejected("Invoice amount exceeds the payable ceiling")
        body = canonical_json(
            {
                "invoiceAmount": str(invoice_amount_minor),
                "financingLimit": str(payable_limit_minor),
            }
        ).encode("utf-8")
        request = Request(
            f"{self.settings.base_url}/proofs/invoice-limit",
            data=body,
            headers={"content-type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(
                request, timeout=self.settings.timeout_seconds
            ) as response:
                status_code = response.status
                content_type = response.headers.get_content_type()
                payload = response.read(_MAX_PROVER_BODY + 1)
        except HTTPError as error:
            if error.code == 422:
                raise ProofRejected(
                    "Prover rejected the invoice-limit statement"
                ) from error
            raise ProverUnavailable(
                f"Prover returned HTTP {error.code}"
            ) from error
        except URLError as error:
            if isinstance(error.reason, (TimeoutError, socket.timeout)):
                raise ProverUnavailable("Prover request timed out") from error
            raise ProverUnavailable("Prover is unavailable") from error
        except TimeoutError as error:
            # urlopen AND response.read may raise a bare socket timeout.
            raise ProverUnavailable("Prover request timed out") from error

        if (
            status_code != 200
            or content_type != "application/json"
            or len(payload) > _MAX_PROVER_BODY
        ):
            raise ProverUnavailable("Prover returned an invalid response")
        try:
            decoded = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ProverUnavailable("Prover returned invalid JSON") from error
        return self._to_proof(decoded, payable_limit_minor)

    @staticmethod
    def _to_proof(
        decoded: Any,
        payable_limit_minor: int,
    ) -> InvoiceLimitProof:
        if not isinstance(decoded, dict):
            raise ProverUnavailable("Prover returned an invalid proof")
        proof = decoded.get("proof")
        signals = decoded.get("publicSignals")
        circuit_version = decoded.get("circuitVersion")
        if (
            not isinstance(proof, dict)
            or not _groth16_shape(proof)
            or not isinstance(signals, list)
            or len(signals) != 2
            or not all(_field_element(item, _SCALAR_FIELD) for item in signals)
            or circuit_version != _CIRCUIT_VERSION
        ):
            raise ProverUnavailable("Prover returned an invalid proof")
        # The circuit publishes [commitment, financingLimit]. Re-checking the
        # second signal keeps a prover that silently proves a different
        # ceiling from being recorded as evidence for this application.
        if signals[1] != str(payable_limit_minor):
            raise ProverUnavailable(
                "Prover proved a different payable ceiling"
            )
        return InvoiceLimitProof(
            circuit_version=circuit_version,
            proof=proof,
            public_signals=signals,
        )
