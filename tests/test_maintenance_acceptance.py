"""Maintenance runner contracts. Mock-only: no original database or Docker use."""
import json
import hashlib
import subprocess
import sys
import uuid
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

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


@pytest.mark.parametrize("url", [
    "http://gateway:8090", "http://localhost:8090", "https://127.0.0.1:8090",
    "http://127.0.0.1:8090/anchors", "http://user@127.0.0.1:8090",
])
def test_fault_override_requires_app_container_loopback(monkeypatch, url):
    monkeypatch.setattr(acceptance, "docker", lambda *a, **kw: pytest.fail("Docker must not run"))
    with pytest.raises(ValueError, match="loopback"):
        acceptance.scoped_call("pinned-app", {"action": "dispatch"}, gateway_url=url)


def test_fault_override_applies_only_to_scoped_dispatch_process(monkeypatch):
    calls = []
    monkeypatch.setattr(acceptance, "docker", lambda *args, **kwargs:
                        calls.append((args, kwargs)) or '{}')
    with pytest.raises(ValueError, match="Only scoped dispatch"):
        acceptance.scoped_call("pinned-app", {"action": "create"},
                               gateway_url="http://127.0.0.1:8123")
    acceptance.scoped_call("pinned-app", {"action": "dispatch"},
                           gateway_url="http://127.0.0.1:8123")
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[:6] == ("exec", "-i", "-e", "FABRIC_GATEWAY_URL=http://127.0.0.1:8123",
                        "pinned-app", "python")
    assert json.loads(kwargs["stdin"]) == {"action": "dispatch"}


@pytest.mark.parametrize("match", [True, False])
def test_one_shot_injector_serves_only_exact_envelope_on_local_loopback(match):
    anchor_id = str(uuid.uuid4())
    envelope = {"anchorId": anchor_id, "eventHash": "a" * 64}
    digest = hashlib.sha256(acceptance.canonical_json(envelope).encode()).hexdigest()
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", acceptance._INTERNAL_ERROR_ONCE, anchor_id, digest],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        port = json.loads(process.stdout.readline())["port"]
        payload = envelope if match else {**envelope, "eventHash": "b" * 64}
        with pytest.raises(HTTPError) as caught:
            urlopen(Request(
                f"http://127.0.0.1:{port}/anchors",
                data=json.dumps(payload).encode(),
                headers={"content-type": "application/json"}, method="POST",
            ), timeout=3)
        assert caught.value.code == (500 if match else 422)
        body = json.loads(caught.value.read())
        assert body["retryable"] is match
        process.stdin.close()
        process.stdin = None
        output, _ = process.communicate(timeout=5)
        result = json.loads(output.splitlines()[-1])
        assert result["status"] == (500 if match else 422)
        assert process.returncode == (0 if match else 2)
    finally:
        if process.poll() is None:
            process.stdin.close()
            process.stdin = None
            process.terminate()
            process.communicate(timeout=5)


def test_outage_requires_separate_opt_in():
    argv = ["--allow-synthetic-writes", "--phase", "outage"]
    for name in ("base-url", "app-container", "gateway-container", "cli-container", "prover-container", "run-id", "baseline-snapshot", "output"):
        argv += ["--" + name, "value"]
    with pytest.raises(SystemExit):
        acceptance.parse_args(argv)
    assert acceptance.parse_args(argv + ["--allow-local-gateway-outage"]).phase == "outage"


def test_internal_error_requires_separate_opt_in():
    argv = ["--allow-synthetic-writes", "--phase", "internal-error"]
    for name in ("base-url", "app-container", "gateway-container", "cli-container",
                 "prover-container", "run-id", "baseline-snapshot", "output"):
        argv += ["--" + name, "value"]
    argv += ["--source-app-container", "original-app",
             "--postgres-container", "original-postgres",
             "--expected-app-image", "sha256:" + "a" * 64]
    with pytest.raises(SystemExit):
        acceptance.parse_args(argv)
    assert acceptance.parse_args(argv + ["--allow-internal-error-injection"]).phase == "internal-error"


def test_controlled_app_requires_separate_identity_and_readonly_artifact_volume(monkeypatch):
    source_id, postgres_id, app_id = "a" * 64, "b" * 64, "c" * 64
    project = "daibm-scf-defense"
    network = "daibm-scf-mvp-network"
    image = "sha256:" + "1" * 64
    source_image = "sha256:" + "2" * 64
    postgres_image = "sha256:" + "3" * 64
    source_env = {
        "POSTGRES_HOST": "postgres", "POSTGRES_PORT": "5432",
        "POSTGRES_DB": "daibm_scf", "POSTGRES_USER": "daibm",
        "POSTGRES_PASSWORD": "private",
    }
    app_env = {
        **source_env, "ZKP_PROOF_REQUIRED": "true",
        "FABRIC_GATEWAY_URL": "http://fabric-gateway:8090",
        "ZKP_PROVER_URL": "http://zkp-prover:8091",
    }
    destination = "/app/artifacts/candidates/calibration"
    source = {
        "Id": source_id, "Image": source_image, "Name": "/original-app",
        "State": {"Running": False},
        "Config": {"Labels": {"com.docker.compose.project": project,
                               "com.docker.compose.service": "mvp"},
                    "Env": [f"{key}={value}" for key, value in source_env.items()]},
        "Mounts": [{"Destination": destination, "Type": "volume",
                    "Source": "calibration-artifacts", "RW": True}],
    }
    postgres = {
        "Id": postgres_id, "Image": postgres_image, "Name": "/original-postgres",
        "State": {"Running": True},
        "Config": {"Labels": {"com.docker.compose.project": project,
                               "com.docker.compose.service": "postgres"}},
        "NetworkSettings": {"Networks": {network: {"IPAddress": "172.30.0.2"}}},
    }
    app_labels = {
        "org.daibm.acceptance.role": "original-8010-controlled-app",
        "org.daibm.acceptance.source-app-id": source_id,
        "org.daibm.acceptance.source-project": project,
    }
    app = {
        "Id": app_id, "Image": image, "Name": "/controlled-app",
        "State": {"Running": True}, "Config": {
            "Labels": app_labels, "Entrypoint": [],
            "Cmd": ["uvicorn", "app.maintenance:app", "--host", "0.0.0.0",
                    "--port", "8000"],
            "Env": [f"{key}={value}" for key, value in app_env.items()],
        },
        "NetworkSettings": {"Networks": {network: {"IPAddress": "172.30.0.3"}}},
        "Mounts": [{"Destination": destination, "Type": "volume",
                    "Source": "calibration-artifacts", "RW": False}],
    }
    rows = {"controlled-app": app, "original-app": source,
            "original-postgres": postgres}
    def docker(*args):
        if args[:2] == ("container", "inspect"):
            return json.dumps([rows[args[2]]])
        if args[0] == "exec":
            return "172.30.0.2"
        pytest.fail(f"Unexpected Docker invocation: {args}")

    monkeypatch.setattr(acceptance, "docker", docker)
    args = SimpleNamespace(
        app_container="controlled-app", source_app_container="original-app",
        postgres_container="original-postgres", expected_app_image=image,
    )
    assert acceptance._controlled_app(args) == (app, source, postgres)

    app["Mounts"][0]["RW"] = True
    with pytest.raises(RuntimeError, match="calibration mount"):
        acceptance._controlled_app(args)


def test_internal_error_phase_targets_only_new_manifest_and_restores_real_gateway(monkeypatch, tmp_path):
    subject_id, anchor_id = str(uuid.uuid4()), str(uuid.uuid4())
    event_hash = "a" * 64
    manifest = {"anchors": [{"anchor_id": anchor_id, "subject_id": subject_id,
                              "event_hash": event_hash, "envelope_sha256": "b" * 64}]}
    args = SimpleNamespace(base_url="http://127.0.0.1:8010", run_id="test-500", output=tmp_path)
    targets = {name: {"Id": name + "-pinned"} for name in ("app", "gateway", "cli")}
    state = {"status": "pending", "attempt_count": 0,
             "subject_id": subject_id, "event_hash": event_hash,
             "next_attempt_at": datetime.now(timezone.utc).isoformat(),
             "last_error_code": None}
    height = [54]
    calls = []
    record = {"anchorId": anchor_id, "eventHash": event_hash}
    monkeypatch.setattr(acceptance, "maintenance_client", lambda args, username: username)

    def api(actor, path, payload=None, *, base_url):
        if path == "/api/v1/applications":
            assert actor == "supplier.demo"
            assert payload["contract_number"].startswith("SYNTHETIC-test-500-")
            assert payload["invoice_number"].startswith("SYNTHETIC-test-500-")
            return {"request_id": subject_id}
        assert actor == "auditor.demo" and path == "/api/v1/anchors/" + anchor_id
        return dict(state)

    monkeypatch.setattr(acceptance.outage, "api", api)
    monkeypatch.setattr(acceptance, "make_manifest", lambda *args: manifest)
    monkeypatch.setattr(acceptance, "check_identity", lambda *args: None)
    monkeypatch.setattr(acceptance.outage, "chain_height", lambda *args: height[0])
    monkeypatch.setattr(acceptance.outage, "gateway", lambda path, payload=None, **kw:
                        {"status": 200, "body": payload} if payload else {"status": 404})
    monkeypatch.setattr(acceptance, "readback", lambda *args: record)
    monkeypatch.setattr(acceptance.time, "sleep", lambda seconds: None)

    class Fault:
        url = "http://127.0.0.1:8123"
        result = {"status": 500, "anchor_id": anchor_id, "envelope_sha256": "b" * 64}

        def __init__(self, app_id, anchor):
            assert app_id == "app-pinned" and anchor == manifest["anchors"][0]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(acceptance, "InternalErrorOnce", Fault)

    def dispatch(app_id, selected, baseline, *, gateway_url=None):
        calls.append((app_id, selected, baseline, gateway_url))
        assert app_id == "app-pinned" and selected is manifest and baseline == ["historical"]
        if len(calls) == 1:
            assert gateway_url == "http://127.0.0.1:8123"
            state.update(status="pending", attempt_count=1, last_error_code="INTERNAL_ERROR",
                         next_attempt_at=(datetime.now(timezone.utc) + timedelta(seconds=2)).isoformat())
            return dict(claimed=1, anchored=0, retryable=1, permanent_failed=0)
        assert gateway_url is None
        if len(calls) == 2:
            state.update(status="anchored", attempt_count=2, last_error_code=None)
            height[0] += 1
            return dict(claimed=1, anchored=1, retryable=0, permanent_failed=0)
        return dict(claimed=0, anchored=0, retryable=0, permanent_failed=0)

    monkeypatch.setattr(acceptance, "dispatch", dispatch)
    result = acceptance.internal_error(args, targets, ["historical"])
    assert len(calls) == 3
    assert result["fault_provenance"] == "SYNTHETIC_CONTROLLED_HTTP_500_INJECTION"
    assert result["height_before"] == 54 and result["height_after"] == 55
    assert result["attempt_count"] == 2 and result["duplicate_added_blocks"] == 0
    assert json.loads((tmp_path / "injected-500.json").read_text())["fault"]["status"] == 500


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


def test_internal_error_requires_exact_preacceptance_database_baseline(monkeypatch, tmp_path):
    baseline = {
        "tables": {"anchor_outbox": {"rows": {'["historical"]': "unchanged"}}},
        "columns": {"anchor_outbox": ["anchor_id", "attempt_count"]},
        "sequences": {},
        "ledger": {"valid": True, "head_hash": "old", "event_count": 90},
        "revisions": ["20260908_0014"],
    }
    before = deepcopy(baseline)
    before["tables"]["anchor_outbox"]["rows"]['["unexplained"]'] = "new"
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps(baseline))
    args = SimpleNamespace(
        run_id="test-500", base_url="http://127.0.0.1:8010",
        baseline_snapshot=baseline_path, output=tmp_path / "run",
        phase="internal-error",
    )
    monkeypatch.setattr(
        acceptance, "resolve_targets",
        lambda args: (args.base_url, {"app": {"Id": "app"}}, "local"),
    )
    monkeypatch.setattr(acceptance, "snapshot", lambda app_id: before)
    with pytest.raises(RuntimeError, match="anchor_outbox: row set changed"):
        acceptance.run(args)
    assert not (args.output / "before.json").exists()


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
