from __future__ import annotations

import hashlib
from email.message import Message
from unittest.mock import MagicMock, patch

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
        # Shape-only fixture, NOT a cryptographically valid proof.
        "proof": {
            "pi_a": ["1", "2", "1"],
            "pi_b": [["1", "2"], ["3", "4"], ["1", "0"]],
            "pi_c": ["1", "2", "1"],
            "protocol": "groth16",
            "curve": "bn128",
        },
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
        {"proof": {}},
        {"proof": {**_proof()["proof"], "protocol": "plonk"}},
        {"proof": {**_proof()["proof"], "pi_b": [["1", "2"]]}},
        {"publicSignals": ["not-a-field-element", "150000000"]},
        {"publicSignals": ["-1", "150000000"]},
        {"publicSignals": ["01", "150000000"]},
        {"publicSignals": ["9" * 79, "150000000"]},
        {"circuitVersion": "some_other_circuit"},
        {"circuitVersion": "invoice_limit@2"},
        {"circuitVersion": "invoice_limit_evil"},
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


@pytest.mark.parametrize("during_read", [False, True])
def test_raw_transport_timeouts_are_normalized(during_read):
    headers = Message()
    headers["content-type"] = "application/json"
    response = MagicMock(status=200, headers=headers)
    response.__enter__.return_value = response
    response.read.side_effect = TimeoutError("read timed out")
    with patch("app.services.invoice_proof.urlopen") as open_url:
        if during_read:
            open_url.return_value = response
        else:
            open_url.side_effect = TimeoutError("connect timed out")
        with pytest.raises(ProverUnavailable, match="timed out"):
            _prover().prove(
                invoice_amount_minor=100, payable_limit_minor=150_000_000
            )
