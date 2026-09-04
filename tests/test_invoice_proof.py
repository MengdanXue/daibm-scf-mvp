from __future__ import annotations

import hashlib

import pytest

from app.config import ZkpProverSettings
from app.ledger import canonical_json
from app.services.invoice_proof import (
    HttpInvoiceLimitProver,
    InvoiceLimitProof,
    ProofRejected,
    ProverUnavailable,
)


def _proof(**overrides):
    payload = {
        "circuitVersion": "invoice_limit@1",
        "proof": {"pi_a": ["1", "2"], "protocol": "groth16"},
        "publicSignals": ["18446744073709551", "150000000"],
    }
    payload.update(overrides)
    return payload


def _prover() -> HttpInvoiceLimitProver:
    return HttpInvoiceLimitProver(
        ZkpProverSettings.from_env({"ZKP_PROVER_URL": "http://prover:8091"})
    )


def test_proof_digest_uses_the_ledger_canonical_encoding():
    proof = InvoiceLimitProof(
        circuit_version="invoice_limit@1",
        proof={"b": 2, "a": 1},
        public_signals=["7", "9"],
    )
    expected = hashlib.sha256(
        canonical_json({"a": 1, "b": 2}).encode("utf-8")
    ).hexdigest()
    assert proof.proof_sha256 == expected
    assert proof.commitment == "7"


def test_ledger_payload_carries_the_two_keys_the_outbox_lifts():
    proof = InvoiceLimitProof("invoice_limit@1", {"pi_a": ["1"]}, ["7", "9"])
    payload = proof.ledger_payload()
    # AnchorOutboxRepository.enqueue reads exactly these two keys out of a
    # ledger event payload, so anchoring needs no further plumbing.
    assert payload["circuit_version"] == "invoice_limit@1"
    assert payload["proof_sha256"] == proof.proof_sha256
    assert payload["invoice_limit_public_signals"] == ["7", "9"]


def test_a_statement_that_is_false_never_reaches_the_network():
    with pytest.raises(ProofRejected):
        _prover().prove(
            invoice_amount_minor=150_000_001,
            payable_limit_minor=150_000_000,
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"publicSignals": ["1"]},
        {"publicSignals": ["1", "2", "3"]},
        {"publicSignals": [1, 2]},
        {"proof": "not-an-object"},
        {"circuitVersion": "some_other_circuit"},
        {"circuitVersion": "invoice_limit" + "x" * 80},
    ],
)
def test_malformed_prover_responses_are_refused(overrides):
    with pytest.raises(ProverUnavailable):
        HttpInvoiceLimitProver._to_proof(_proof(**overrides), 150_000_000)


def test_a_proof_of_a_different_ceiling_is_refused():
    # A prover that quietly proves a laxer bound must not be recorded as
    # evidence for this application.
    with pytest.raises(ProverUnavailable):
        HttpInvoiceLimitProver._to_proof(
            _proof(publicSignals=["7", "999999999"]), 150_000_000
        )


def test_a_well_formed_response_is_accepted():
    proof = HttpInvoiceLimitProver._to_proof(_proof(), 150_000_000)
    assert proof.circuit_version == "invoice_limit@1"
    assert proof.public_signals[1] == "150000000"
    assert len(proof.proof_sha256) == 64
