"""Policy tests use explicit test doubles, not cryptographic evidence."""
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import sessionmaker

from app.api.workflow import _execute
from app.services.invoice_proof import InvoiceLimitProof, ProverUnavailable
from app.services import workflow


class UnavailableProver:
    def prove(self, **kwargs):
        raise ProverUnavailable("private transport detail")


class ShapeOnlyProver:
    def prove(self, **kwargs):
        return InvoiceLimitProof(
            "invoice_limit@1", {"fixture": "not a real proof"},
            ["7", str(kwargs["payable_limit_minor"])],
        )


@pytest.mark.parametrize("prover", [None, UnavailableProver()])
def test_required_policy_refuses_missing_evidence(prover):
    service = workflow.WorkflowService(
        sessionmaker(), invoice_prover=prover, proof_required=True
    )
    with pytest.raises(workflow.InvoiceProofRequired):
        service._invoice_limit_evidence(Decimal("1"), Decimal("2"))


@pytest.mark.parametrize(
    "prover, code",
    [(None, "prover_not_configured"), (UnavailableProver(), "prover_unavailable")],
)
def test_optional_policy_keeps_explicit_fallback(prover, code):
    service = workflow.WorkflowService(
        sessionmaker(), invoice_prover=prover, proof_required=False
    )
    assert service._invoice_limit_evidence(Decimal("1"), Decimal("2")) == {
        "proof_fallback_code": code,
    }


def test_required_policy_carries_trusted_prover_evidence():
    service = workflow.WorkflowService(
        sessionmaker(), invoice_prover=ShapeOnlyProver(), proof_required=True
    )
    evidence = service._invoice_limit_evidence(Decimal("1"), Decimal("2"))
    assert evidence["invoice_limit_public_signals"] == ["7", "200"]
    assert "proof_fallback_code" not in evidence


def test_required_failure_maps_to_retryable_non_leaking_api_error():
    def fail():
        raise workflow.InvoiceProofRequired("private transport detail")

    with pytest.raises(HTTPException) as raised:
        _execute(fail)
    assert raised.value.status_code == 503
    assert raised.value.detail == {
        "code": "invoice_proof_required",
        "message": "Required invoice proof is unavailable; retry after prover recovery",
    }


def test_application_wires_required_setting(monkeypatch):
    import app.main as main

    captured = {}
    original = main.WorkflowService

    def capture(*args, **kwargs):
        captured.update(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setenv("ZKP_PROOF_REQUIRED", "true")
    monkeypatch.setattr(main, "WorkflowService", capture)
    main.create_app(database=SimpleNamespace(session_factory=sessionmaker()))
    assert captured["proof_required"] is True
    assert captured["invoice_prover"].settings.required is True


def test_compose_passes_prover_settings_into_application():
    compose = (Path(__file__).parents[1] / "docker-compose.yml").read_text("utf-8")
    assert "ZKP_PROVER_URL: ${ZKP_PROVER_URL:-http://zkp-prover:8091}" in compose
    assert 'ZKP_PROOF_REQUIRED: "${ZKP_PROOF_REQUIRED:-false}"' in compose
