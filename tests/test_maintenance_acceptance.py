"""Maintenance runner contracts. Mock-only: no original database or Docker use."""
import json
import hashlib
from copy import deepcopy
from types import SimpleNamespace

import pytest

from scripts import maintenance_acceptance as acceptance
from scripts.browser_acceptance import synthetic_prefix


@pytest.mark.parametrize("run_id", ["", "../original", "a b", "x" * 41, "run;stop"])
def test_synthetic_prefix_rejects_ambiguous_identifiers(run_id):
    with pytest.raises(ValueError):
        synthetic_prefix(run_id)


def test_synthetic_prefix_is_explicit():
    assert synthetic_prefix("20260920-clone") == "SYNTHETIC-20260920-clone-"


def test_persisted_proof_digest_is_independently_checked():
    proof = {"pi_a": ["1", "2"]}
    payload = {"invoice_limit_proof": proof, "proof_sha256": "0" * 64}
    with pytest.raises(RuntimeError, match="SHA-256"):
        acceptance.validate_proof_hash(payload)
    payload["proof_sha256"] = hashlib.sha256(acceptance.canonical_json(proof).encode()).hexdigest()
    acceptance.validate_proof_hash(payload)


def test_logout_accepts_empty_204_without_parsing_json():
    class Response:
        status = 204

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    actor = SimpleNamespace(open=lambda request, timeout: Response())
    acceptance.logout_api(actor, "http://127.0.0.1:8040")


def test_dispatch_only_uses_manifest_helper(monkeypatch):
    calls = []
    manifest = {"anchors": [{"anchor_id": "new-only"}]}
    monkeypatch.setattr(acceptance, "scoped_call", lambda app, payload: calls.append((app, payload)) or {})
    acceptance.dispatch("pinned-app", manifest, ["historical"])
    assert calls == [("pinned-app", {"action": "dispatch", "manifest": manifest,
                                   "baseline_anchor_ids": ["historical"], "limit": 1})]


def test_outage_requires_separate_opt_in():
    argv = ["--allow-synthetic-writes", "--phase", "outage"]
    for name in ("base-url", "app-container", "gateway-container", "cli-container", "prover-container", "run-id", "baseline-snapshot", "output"):
        argv += ["--" + name, "value"]
    with pytest.raises(SystemExit):
        acceptance.parse_args(argv)
    assert acceptance.parse_args(argv + ["--allow-local-gateway-outage"]).phase == "outage"


def test_source_never_calls_global_dispatch_api():
    from pathlib import Path
    assert "/api/v1/anchor-dispatches" not in Path(acceptance.__file__).read_text()


def test_run_rejects_historical_change_and_retains_failure_evidence(monkeypatch, tmp_path):
    before = {
        "tables": {"anchor_outbox": {"rows": {'["historical"]': "unchanged"}}},
        "columns": {"anchor_outbox": ["anchor_id", "attempt_count"]},
        "sequences": {}, "ledger": {"valid": True, "head_hash": "old", "event_count": 90},
        "revisions": ["20260908_0014"],
    }
    after = deepcopy(before)
    after["tables"]["anchor_outbox"]["rows"]['["historical"]'] = "touched"
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps(before))
    args = SimpleNamespace(run_id="test", base_url="http://127.0.0.1:8040",
                           baseline_snapshot=baseline, output=tmp_path / "new", phase="journey")
    targets = {"app": {"Id": "app", "Image": "image", "Name": "name"}}
    monkeypatch.setattr(acceptance, "resolve_targets", lambda args: (args.base_url, targets, "local"))
    states = iter((before, after))
    monkeypatch.setattr(acceptance, "snapshot", lambda app: next(states))
    monkeypatch.setattr(acceptance, "journey", lambda *args: {})
    with pytest.raises(RuntimeError, match="Historical rows changed"):
        acceptance.run(args)
    assert not (args.output / "accepted.json").exists()
    assert json.loads((args.output / "history.json").read_text())["history_equal"] is False


@pytest.mark.parametrize("failure", ["stop", "dispatch"])
def test_outage_always_restores_pinned_gateway_on_failure(monkeypatch, tmp_path, failure):
    args = SimpleNamespace(base_url="http://127.0.0.1:8040", run_id="test", output=tmp_path)
    targets = {name: {"Id": name + "-pinned"} for name in ("app", "gateway", "cli")}
    monkeypatch.setattr(acceptance.outage, "client", lambda *a, **kw: "actor")
    monkeypatch.setattr(acceptance.outage, "api", lambda actor, path, *a, **kw:
                        {"request_id": "new-subject"} if path.endswith("applications") else
                        {"status": "pending", "attempt_count": 0})
    monkeypatch.setattr(acceptance, "make_manifest", lambda *args: {"anchors": [{"anchor_id": "new"}]})
    monkeypatch.setattr(acceptance.outage, "gateway", lambda *a, **kw: {"status": 404})
    monkeypatch.setattr(acceptance.outage, "chain_height", lambda *a: 46)
    monkeypatch.setattr(acceptance, "check_identity", lambda *a: None)
    calls = []

    def docker(*args):
        calls.append(args)
        if args[0] == "stop" and failure == "stop":
            raise RuntimeError("failed stop")
        return "false"

    def dispatch(*args):
        raise RuntimeError("failed dispatch")

    monkeypatch.setattr(acceptance.outage, "docker", docker)
    monkeypatch.setattr(acceptance, "dispatch", dispatch)
    monkeypatch.setattr(acceptance.outage, "_restore_gateway", lambda target: calls.append(("restore", target)))
    with pytest.raises(RuntimeError, match="failed"):
        acceptance.gateway_outage(args, targets, ["historical"])
    assert calls[-1] == ("restore", "gateway-pinned")
