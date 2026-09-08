"""Real PostgreSQL rollback plus opt-in real HTTP/Groth16/audit evidence."""
import json
import os
import shutil
import subprocess
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.config import ZkpProverSettings
from app.models import FinancingRequestModel, LedgerEventModel
from app.models_advanced import AnchorOutboxModel
from app.models_workflow import WorkflowActionModel
from app.services.invoice_proof import HttpInvoiceLimitProver, ProverUnavailable
from app.services.workflow import InvoiceProofRequired, WorkflowService
from test_workflow_service import demo_users, draft_payload

ROOT = Path(__file__).parents[1]


class UnavailableProver:
    def prove(self, **kwargs):
        raise ProverUnavailable("synthetic outage")


def _submitted(session_factory, **kwargs):
    users = demo_users(session_factory)
    service = WorkflowService(session_factory, **kwargs)
    draft = service.create_draft(draft_payload(), users["supplier"])
    submitted = service.submit(draft["request_id"], draft["version"], users["supplier"])
    return service, users, submitted


def _confirm(service, users, submitted):
    return service.confirm_trade(
        submitted["request_id"], submitted["version"], confirmed=True,
        comment="Synthetic integration test", user=users["core_enterprise"],
        confirmed_payable_amount=Decimal("1500000.00"),
    )


def _counts(session):
    return tuple(
        session.scalar(select(func.count()).select_from(model))
        for model in (LedgerEventModel, WorkflowActionModel)
    )


@pytest.mark.parametrize("prover", [None, UnavailableProver()])
def test_required_failure_rolls_back_all_confirmation_state(session_factory, prover):
    service, users, submitted = _submitted(
        session_factory, invoice_prover=prover, proof_required=True
    )
    with session_factory() as session:
        before = _counts(session)
    with pytest.raises(InvoiceProofRequired):
        _confirm(service, users, submitted)
    with session_factory() as session:
        stored = session.get(FinancingRequestModel, uuid.UUID(submitted["request_id"]))
        assert stored.status == "submitted"
        assert stored.version == submitted["version"]
        assert stored.confirmed_payable_amount is None
        assert _counts(session) == before


def test_optional_outage_commits_explicit_fallback(session_factory):
    service, users, submitted = _submitted(
        session_factory, invoice_prover=UnavailableProver()
    )
    assert _confirm(service, users, submitted)["status"] == "trade_confirmed"
    with session_factory() as session:
        event = session.scalar(
            select(LedgerEventModel).where(
                LedgerEventModel.event_type == "TRADE_CONFIRMED"
            )
        )
        assert event.payload["proof_fallback_code"] == "prover_unavailable"
        assert "proof_sha256" not in event.payload


@pytest.fixture
def real_prover_url():
    if os.environ.get("RUN_ZKP_WORKFLOW_INTEGRATION") != "1":
        pytest.skip("Requires Node 24 and advanced/zkp npm ci; enabled in application CI")
    node = shutil.which("node")
    assert node, "Node is required when real ZKP integration is explicitly enabled"
    # Bind port 0 in the child, avoiding a free-port lookup race.
    script = (
        "import { createProverServer } from './src/server.mjs';"
        "const server=createProverServer();"
        "server.listen(0,'127.0.0.1',()=>console.log(server.address().port));"
    )
    process = subprocess.Popen(
        [node, "--input-type=module", "-e", script],
        cwd=ROOT / "advanced/zkp", stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    try:
        # stdout is written only once listen succeeds. communicate cannot be
        # used on a live server; a bounded reader prevents startup deadlocks.
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(process.stdout.readline)
            try:
                port = pending.result(timeout=30).strip()
            except Exception:
                process.terminate()
                raise
        assert port.isdecimal(), "Real prover failed to start"
        yield f"http://127.0.0.1:{port}"
    finally:
        process.terminate()
        try:
            process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=10)


def test_real_proof_survives_http_workflow_and_audit(
    session_factory, real_prover_url
):
    node = shutil.which("node")
    preflight = subprocess.run(
        [node, "src/preflight.mjs", real_prover_url], cwd=ROOT / "advanced/zkp",
        capture_output=True, text=True, timeout=60, check=True,
    )
    assert json.loads(preflight.stdout)["proof_verified"] is True
    service, users, submitted = _submitted(
        session_factory,
        invoice_prover=HttpInvoiceLimitProver(ZkpProverSettings(real_prover_url)),
        proof_required=True,
    )
    assert _confirm(service, users, submitted)["status"] == "trade_confirmed"
    with session_factory() as session:
        event = session.scalar(
            select(LedgerEventModel).where(
                LedgerEventModel.event_type == "TRADE_CONFIRMED"
            )
        )
        payload = dict(event.payload)
        assert service.ledger_repository.verify(session)["valid"] is True
        anchor = session.scalar(select(AnchorOutboxModel).where(
            AnchorOutboxModel.ledger_event_id == event.id
        ))
        assert anchor is not None
        assert anchor.circuit_version == payload["circuit_version"] == "invoice_limit@1"
        assert anchor.proof_sha256 == payload["proof_sha256"]
        assert anchor.event_hash == event.event_hash
        assert anchor.status == "pending"
    from app.services.invoice_proof import InvoiceLimitProof
    evidence = InvoiceLimitProof(
        payload["circuit_version"], payload["invoice_limit_proof"],
        payload["invoice_limit_public_signals"],
    )
    assert evidence.proof_sha256 == payload["proof_sha256"]
    assert evidence.public_signals[1] == "150000000"
    assert "proof_fallback_code" not in payload
    # Independent offline verification of what PostgreSQL actually persisted,
    # NOT a new production verification boundary in the application.
    verification = subprocess.run(
        [node, "--input-type=module", "-e",
         "import { verifyInvoiceLimit } from './index.mjs';"
         "let s='';for await(const c of process.stdin)s+=c;"
         "const p=JSON.parse(s);"
         "if(!await verifyInvoiceLimit(p.invoice_limit_proof,"
         "p.invoice_limit_public_signals))process.exit(1);"
         "p.invoice_limit_public_signals[1]='150000001';"
         "if(await verifyInvoiceLimit(p.invoice_limit_proof,"
         "p.invoice_limit_public_signals))process.exit(2);"],
        cwd=ROOT / "advanced/zkp", input=json.dumps(payload),
        capture_output=True, text=True, timeout=60,
    )
    assert verification.returncode == 0, verification.stderr
