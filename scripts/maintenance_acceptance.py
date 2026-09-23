"""Explicit synthetic-only local acceptance, even with historical pending work.

Never calls the global anchor-dispatch API. The journey and gateway outage
phases are for isolated rehearsals; the internal-error phase pins the original
8010 source and controlled maintenance app separately. Private evidence stays
on the operator's local machine.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import queue
import re
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request

from playwright.sync_api import sync_playwright
from app.ledger import canonical_json

from scripts import browser_acceptance as browser
from scripts import fabric_outage_acceptance as outage
from scripts import maintenance_snapshot as snapshots


_INTERNAL_ERROR_ONCE = r'''
import hashlib
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from app.ledger import canonical_json

expected_id, expected_digest = sys.argv[1:]
finished = threading.Event()

class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = self.headers.get("content-length", "")
        valid = self.path == "/anchors" and self.headers.get_content_type() == "application/json"
        valid = valid and length.isdecimal() and 0 < int(length) <= 16 * 1024
        if valid:
            try:
                envelope = json.loads(self.rfile.read(int(length)))
                valid = (isinstance(envelope, dict)
                         and envelope.get("anchorId") == expected_id
                         and hashlib.sha256(canonical_json(envelope).encode()).hexdigest() == expected_digest)
            except (UnicodeDecodeError, json.JSONDecodeError):
                valid = False
        status = 500 if valid else 422
        body = ({"code": "INTERNAL_ERROR", "message": "Controlled synthetic HTTP 500", "retryable": True}
                if valid else
                {"code": "INVALID_ANCHOR", "message": "Fault injector rejected unexpected request", "retryable": False})
        encoded = json.dumps(body, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)
        self.server.result = {"status": status, "anchor_id": expected_id,
                              "envelope_sha256": expected_digest}
        finished.set()

    def log_message(self, format, *args):
        pass

server = HTTPServer(("127.0.0.1", 0), Handler)
server.timeout = 0.2
def watch_stdin():
    sys.stdin.read()
    finished.set()
threading.Thread(target=watch_stdin, daemon=True).start()
print(json.dumps({"port": server.server_port}), flush=True)
deadline = time.monotonic() + 30
while not finished.is_set() and time.monotonic() < deadline:
    server.handle_request()
server.server_close()
print(json.dumps(getattr(server, "result", {"status": "not_served"})), flush=True)
if getattr(server, "result", {}).get("status") != 500:
    sys.exit(2)
'''


def docker(*args, stdin=None):
    return subprocess.run(
        ["docker", "--context", "desktop-linux", *args], input=stdin,
        capture_output=True, text=True, check=True, timeout=120,
    ).stdout.strip()


def save(path, value):
    # Failure evidence is retained; never overwrite a previous maintenance run.
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)


def _inspect_explicit_container(name):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name):
        raise ValueError("Select one explicit container name or ID")
    rows = json.loads(docker("container", "inspect", name))
    if (len(rows) != 1 or not re.fullmatch(r"[0-9a-f]{64}", rows[0].get("Id", ""))
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", rows[0].get("Image", ""))):
        raise RuntimeError("Container identity could not be pinned")
    return rows[0]


def _controlled_app(args):
    """Select a separate maintenance app without impersonating Compose labels."""
    app = _inspect_explicit_container(args.app_container)
    source = _inspect_explicit_container(args.source_app_container)
    postgres = _inspect_explicit_container(args.postgres_container)
    source_labels = source["Config"].get("Labels") or {}
    postgres_labels = postgres["Config"].get("Labels") or {}
    app_labels = app["Config"].get("Labels") or {}
    project = source_labels.get("com.docker.compose.project")
    if (not project or source_labels.get("com.docker.compose.service") != "mvp"
            or source["State"]["Running"] is not False):
        raise RuntimeError("Source must be the stopped original Compose app")
    if (postgres_labels.get("com.docker.compose.project") != project
            or postgres_labels.get("com.docker.compose.service") != "postgres"
            or postgres["State"]["Running"] is not True):
        raise RuntimeError("PostgreSQL must be the running source Compose database")
    if (app["State"]["Running"] is not True or app["Id"] in {source["Id"], postgres["Id"]}
            or app["Image"] != args.expected_app_image or app["Image"] == source["Image"]
            or app_labels.get("org.daibm.acceptance.role") != "original-8010-controlled-app"
            or app_labels.get("org.daibm.acceptance.source-app-id") != source["Id"]
            or app_labels.get("org.daibm.acceptance.source-project") != project
            or "com.docker.compose.project" in app_labels
            or "com.docker.compose.service" in app_labels):
        raise RuntimeError("Controlled app identity, image or independent labels differ")
    if (app["Config"].get("Entrypoint") not in (None, [])
            or app["Config"].get("Cmd") != [
                "uvicorn", "app.maintenance:app", "--host", "0.0.0.0", "--port", "8000",
            ]):
        raise RuntimeError("Controlled app must use the no-reconciliation maintenance entrypoint")
    app_env = dict(item.split("=", 1) for item in app["Config"]["Env"])
    source_env = dict(item.split("=", 1) for item in source["Config"]["Env"])
    for key in ("POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB", "POSTGRES_USER",
                "POSTGRES_PASSWORD"):
        if not app_env.get(key) or app_env[key] != source_env.get(key):
            raise RuntimeError("Controlled app database target differs from the original")
    networks = set(app["NetworkSettings"]["Networks"]) & set(postgres["NetworkSettings"]["Networks"])
    ips = {postgres["NetworkSettings"]["Networks"][name]["IPAddress"] for name in networks}
    if not ips:
        raise RuntimeError("Controlled app is not connected to the original PostgreSQL network")
    code = "import socket;print(socket.gethostbyname(" + repr(app_env["POSTGRES_HOST"]) + "))"
    if docker("exec", app["Id"], "python", "-c", code) not in ips:
        raise RuntimeError("Controlled app database DNS resolves to a different container")
    destination = "/app/artifacts/candidates/calibration"
    source_mounts = [row for row in source["Mounts"] if row["Destination"] == destination]
    app_mounts = [row for row in app["Mounts"] if row["Destination"] == destination]
    if (len(source_mounts) != 1 or len(app_mounts) != 1
            or any(source_mounts[0][key] != app_mounts[0][key] for key in ("Type", "Source"))
            or app_mounts[0]["RW"] is not False):
        raise RuntimeError("Controlled app calibration mount differs from the original")
    return app, source, postgres


def resolve_targets(args):
    base = outage._local_base_url(args.base_url)
    endpoint, gateway, cli = outage._resolve_targets(args.gateway_container, args.cli_container)
    extra = {}
    if args.phase == "internal-error":
        app, source, postgres = _controlled_app(args)
        extra = {"source_app": source, "postgres": postgres}
    else:
        app = outage._inspect_container(args.app_container, "mvp")
    prover = json.loads(docker("container", "inspect", args.prover_container))[0]
    if (not prover["State"]["Running"] or
            prover["Config"]["Labels"].get("com.docker.compose.service")
            not in {"zkp-prover", "restore-prover"}):
        raise RuntimeError("Selected prover is not a running project prover")
    port = str(urlsplit(base).port or 80)
    bindings = app["HostConfig"]["PortBindings"].get("8000/tcp", [])
    if bindings != [{"HostIp": "127.0.0.1", "HostPort": port}]:
        raise RuntimeError("Application container does not match the loopback origin")
    env = dict(item.split("=", 1) for item in app["Config"]["Env"])
    if env.get("ZKP_PROOF_REQUIRED", "").lower() != "true":
        raise RuntimeError("Acceptance requires real proofs without fallback")
    for key, selected, expected_port in (
        ("FABRIC_GATEWAY_URL", gateway, 8090), ("ZKP_PROVER_URL", prover, 8091),
    ):
        url = urlsplit(env[key])
        networks = set(app["NetworkSettings"]["Networks"]) & set(selected["NetworkSettings"]["Networks"])
        ips = {selected["NetworkSettings"]["Networks"][n]["IPAddress"] for n in networks}
        if (not ips or url.scheme != "http" or url.port != expected_port
                or url.username or url.password or url.path not in {"", "/"}
                or url.query or url.fragment):
            raise RuntimeError("Application service configuration does not match selected targets")
        code = "import socket;print(socket.gethostbyname(" + repr(url.hostname) + "))"
        if docker("exec", app["Id"], "python", "-c", code) not in ips:
            raise RuntimeError("Application service DNS resolves to a different container")
    return base, {"app": app, "gateway": gateway, "cli": cli, "prover": prover, **extra}, endpoint


def check_identity(targets):
    for name, row in targets.items():
        current = json.loads(docker("container", "inspect", row["Id"]))[0]
        if (current["Id"], current["Image"], current["Name"]) != (row["Id"], row["Image"], row["Name"]):
            raise RuntimeError("A pinned container identity changed")
        if current["State"]["Running"] is not (name != "source_app"):
            raise RuntimeError("A pinned container running state changed")


def snapshot(app_id):
    code = Path(snapshots.__file__).read_text(encoding="utf-8")
    return json.loads(docker("exec", app_id, "python", "-c", code))


def scoped_call(app_id, payload, *, gateway_url=None):
    # Executes reviewed maintenance code in the existing image/env, without
    # app.main startup, image replacement, file copies, or credential output.
    source = Path(__file__).with_name("maintenance_dispatch.py").read_text(encoding="utf-8")
    options = ["exec", "-i"]
    if gateway_url is not None:
        if payload.get("action") != "dispatch":
            raise ValueError("Only scoped dispatch may use fault injection")
        parsed = urlsplit(gateway_url)
        if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1"
                or parsed.port is None or parsed.username or parsed.password
                or parsed.path or parsed.query or parsed.fragment):
            raise ValueError("Fault-injected gateway URL must be app-container loopback")
        options += ["-e", "FABRIC_GATEWAY_URL=" + gateway_url]
    return json.loads(docker(*options, app_id, "python", "-c", source,
                             "--allow-synthetic-dispatch", stdin=json.dumps(payload)))


def make_manifest(app_id, run_id, subject_id, baseline_ids, output):
    manifest = scoped_call(app_id, {
        "action": "create", "run_id": run_id, "subject_ids": [subject_id],
        "baseline_anchor_ids": baseline_ids,
    })
    save(output / "manifest.json", manifest)
    return manifest


def dispatch(app_id, manifest, baseline_ids, *, gateway_url=None):
    payload = {
        "action": "dispatch", "manifest": manifest,
        "baseline_anchor_ids": baseline_ids, "limit": len(manifest["anchors"]),
    }
    if gateway_url is None:
        return scoped_call(app_id, payload)
    return scoped_call(app_id, payload, gateway_url=gateway_url)


class InternalErrorOnce:
    """One matching request gets a synthetic 500 on the app container's loopback."""

    def __init__(self, app_id, anchor):
        self.app_id = app_id
        self.anchor = anchor
        self.process = None
        self.url = None
        self.result = None

    def __enter__(self):
        if set(self.anchor) != {"anchor_id", "subject_id", "event_hash", "envelope_sha256"}:
            raise ValueError("Fault injection requires one exact synthetic manifest anchor")
        source_id = str(uuid.UUID(self.anchor["anchor_id"]))
        if source_id != self.anchor["anchor_id"] or not isinstance(self.anchor["envelope_sha256"], str):
            raise ValueError("Fault injection anchor identity is invalid")
        if len(self.anchor["envelope_sha256"]) != 64 or any(
            char not in "0123456789abcdef" for char in self.anchor["envelope_sha256"]
        ):
            raise ValueError("Fault injection envelope digest is invalid")
        self.process = subprocess.Popen(
            ["docker", "--context", "desktop-linux", "exec", "-i", self.app_id,
             "python", "-u", "-c", _INTERNAL_ERROR_ONCE,
             source_id, self.anchor["envelope_sha256"]],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        lines = queue.Queue()
        threading.Thread(
            target=lambda: lines.put(self.process.stdout.readline()), daemon=True
        ).start()
        try:
            ready = lines.get(timeout=30)
            port = json.loads(ready)["port"]
            if type(port) is not int or not 1 <= port <= 65535:
                raise ValueError("Fault injector returned an invalid port")
            self.url = f"http://127.0.0.1:{port}"
            return self
        except (queue.Empty, ValueError, KeyError, TypeError) as error:
            self.__exit__(None, None, None)
            raise RuntimeError("Fault injector did not become ready") from error

    def __exit__(self, exc_type, exc_value, traceback):
        process = self.process
        if process is None:
            return
        if process.stdin is not None:
            process.stdin.close()
            process.stdin = None
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=5)
        output = process.stdout.read().splitlines()
        if output:
            try:
                self.result = json.loads(output[-1])
            except json.JSONDecodeError:
                pass
        process.stdout.close()
        process.stderr.close()
        if exc_type is None and (process.returncode != 0 or self.result != {
            "status": 500,
            "anchor_id": self.anchor["anchor_id"],
            "envelope_sha256": self.anchor["envelope_sha256"],
        }):
            raise RuntimeError("Fault injector did not serve the exact synthetic HTTP 500")


def expected_summary(result, claimed, anchored, retryable=0):
    actual = {key: result[key] for key in ("claimed", "anchored", "retryable", "permanent_failed")}
    expected = dict(claimed=claimed, anchored=anchored, retryable=retryable, permanent_failed=0)
    if actual != expected:
        raise RuntimeError(f"Unexpected targeted dispatch result: {actual}")


def maintenance_client(args, username):
    actor = outage.client(username, base_url=args.base_url)
    if not hasattr(args, "_actors"):
        args._actors = []
    args._actors.append(actor)
    return actor


def logout_api(actor, base_url):
    with actor.open(Request(base_url + "/api/v1/auth/logout", data=b"{}",
                            headers={"Content-Type": "application/json"}), timeout=20) as response:
        if response.status != 204:
            raise RuntimeError("Maintenance session logout did not return 204")


def validate_proof_hash(payload):
    digest = hashlib.sha256(canonical_json(payload["invoice_limit_proof"]).encode("utf-8")).hexdigest()
    if digest != payload["proof_sha256"]:
        raise RuntimeError("Persisted proof does not match its SHA-256 digest")


def readback(targets, anchor_id):
    response = outage.gateway("/anchors/" + anchor_id, container=targets["gateway"]["Id"])
    assert response["status"] == 200
    cli_record = json.loads(docker(
        "exec", targets["cli"]["Id"], "peer", "chaincode", "query", "-C", "scfchannel",
        "-n", "audit-anchor", "-c", json.dumps({"Args": ["ReadAnchor", anchor_id]}),
    ))
    assert cli_record == response["body"]
    return cli_record


def journey(args, targets, baseline_ids):
    browser.SCREENSHOT = args.output / "five-role.png"
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch(headless=True, channel=args.browser_channel)
        page = None
        try:
            context = instance.new_context(viewport={"width": 1440, "height": 1100})
            page = context.new_page()
            messages, optional = [], []
            page.on("response", lambda response: optional.append(response)
                    if browser._is_active_calibration_url(response.url) else None)
            page.on("console", lambda message: messages.append(
                (message.type, message.text, message.location.get("url", "")))
                if message.type in {"error", "warning"} else None)
            page.on("pageerror", lambda error: messages.append(("pageerror", str(error), "")))
            subject = browser._exercise_primary(page, args.base_url, synthetic_run_id=args.run_id)
            save(args.output / "application.json", {"subject_id": subject, "synthetic_run_id": args.run_id})
            assert not [m for m in messages if not browser._is_expected_empty_calibration_error(m, optional)]
            browser.logout(page)
        finally:
            try:
                if page is not None and not page.is_closed():
                    status = page.evaluate("fetch('/api/v1/auth/logout',{method:'POST'}).then(r=>r.status)")
                    if status != 204:
                        raise RuntimeError("Browser maintenance session logout failed")
            finally:
                instance.close()
    manifest = make_manifest(targets["app"]["Id"], args.run_id, subject, baseline_ids, args.output)
    auditor = maintenance_client(args, "auditor.demo")
    request = lambda path: outage.api(auditor, path, base_url=args.base_url)
    rows = [request("/api/v1/anchors/" + item["anchor_id"]) for item in manifest["anchors"]]
    assert all(row["status"] == "pending" and row["attempt_count"] == 0 for row in rows)
    proof_rows = [row for row in rows if row["circuit_version"] == "invoice_limit@1"]
    assert len(proof_rows) == 1
    proof_row = proof_rows[0]
    events = request("/api/ledger?limit=500")
    event = next(row for row in events if row["event_hash"] == proof_row["event_hash"])
    payload = event["payload"]
    assert event["event_type"] == "TRADE_CONFIRMED" and "proof_fallback_code" not in payload
    assert payload["circuit_version"] == proof_row["circuit_version"]
    assert payload["proof_sha256"] == proof_row["proof_sha256"]
    validate_proof_hash(payload)
    verification = (
        "import {verifyInvoiceLimit} from './index.mjs';"
        "let s='';for await(const c of process.stdin)s+=c;const p=JSON.parse(s);"
        "if(!await verifyInvoiceLimit(p.invoice_limit_proof,p.invoice_limit_public_signals))process.exit(1);"
        "p.invoice_limit_public_signals[1]=(BigInt(p.invoice_limit_public_signals[1])+1n).toString();"
        "if(await verifyInvoiceLimit(p.invoice_limit_proof,p.invoice_limit_public_signals))process.exit(2);"
        "console.log('persisted-proof-valid; tampered-signal-rejected');"
    )
    verified = docker("exec", "-i", targets["prover"]["Id"], "node", "--input-type=module",
                      "-e", verification, stdin=json.dumps(payload))
    assert "tampered-signal-rejected" in verified
    before_height = outage.chain_height(targets["cli"]["Id"])
    for row in rows:
        assert outage.gateway("/anchors/" + row["anchor_id"], container=targets["gateway"]["Id"])["status"] == 404
    result = dispatch(targets["app"]["Id"], manifest, baseline_ids)
    expected_summary(result, len(rows), len(rows))
    for row in rows:
        after = request("/api/v1/anchors/" + row["anchor_id"])
        assert after["status"] == "anchored" and after["attempt_count"] == 1 and after["last_error_code"] is None
        record = readback(targets, row["anchor_id"])
        assert record["eventHash"] == row["event_hash"] and record["subjectId"] == subject
        if row == proof_row:
            assert record["circuitVersion"] == "invoice_limit@1"
            assert record["proofSha256"] == payload["proof_sha256"]
    after_height = outage.chain_height(targets["cli"]["Id"])
    assert after_height == before_height + len(rows)
    proof_record = readback(targets, proof_row["anchor_id"])
    assert outage.gateway("/anchors", proof_record, container=targets["gateway"]["Id"]) == {"status": 200, "body": proof_record}
    assert outage.chain_height(targets["cli"]["Id"]) == after_height
    expected_summary(dispatch(targets["app"]["Id"], manifest, baseline_ids), 0, 0)
    return dict(five_roles=True, synthetic_subject_id=subject, targeted_dispatch=result,
                persisted_proof_verified=True, tampered_signal_rejected=True,
                circuit_version="invoice_limit@1", proof_sha256=payload["proof_sha256"],
                gateway_cli_readback_equal=True, height_before=before_height,
                height_after=after_height, duplicate_added_blocks=0)


def gateway_outage(args, targets, baseline_ids):
    gateway_id, app_id = targets["gateway"]["Id"], targets["app"]["Id"]
    auditor = maintenance_client(args, "auditor.demo")
    supplier = maintenance_client(args, "supplier.demo")
    request = lambda path: outage.api(auditor, path, base_url=args.base_url)
    marker = browser.synthetic_prefix(args.run_id) + uuid.uuid4().hex[:10]
    application = outage.api(supplier, "/api/v1/applications", {
        "core_enterprise_organization_code": "CORE-001", "contract_number": marker,
        "invoice_number": marker, "amount": 100, "term_days": 30,
        "payment_delay_days": 0, "counterparty_risk": 0.1,
        "relationship_months": 12, "transactions_last_30d": 2,
    }, base_url=args.base_url)
    save(args.output / "application.json", {"subject_id": application["request_id"], "synthetic_run_id": args.run_id})
    manifest = make_manifest(app_id, args.run_id, application["request_id"], baseline_ids, args.output)
    assert len(manifest["anchors"]) == 1
    anchor_id = manifest["anchors"][0]["anchor_id"]
    before = request("/api/v1/anchors/" + anchor_id)
    assert before["status"] == "pending" and before["attempt_count"] == 0
    assert outage.gateway("/anchors/" + anchor_id, container=gateway_id)["status"] == 404
    before_height = outage.chain_height(targets["cli"]["Id"])
    check_identity(targets)
    try:
        outage.docker("stop", "--time", "10", gateway_id)
        assert outage.docker("inspect", gateway_id, "--format", "{{.State.Running}}") == "false"
        failed = dispatch(app_id, manifest, baseline_ids)
        expected_summary(failed, 1, 0, 1)
        retry = request("/api/v1/anchors/" + anchor_id)
        assert retry["status"] == "pending" and retry["attempt_count"] == 1
        assert retry["last_error_code"] in {"FABRIC_UNAVAILABLE", "FABRIC_TIMEOUT"}
        assert retry["event_hash"] == before["event_hash"]
        assert outage.chain_height(targets["cli"]["Id"]) == before_height
        save(args.output / "during-outage.json", {"dispatch": failed, "anchor": retry})
    finally:
        outage._restore_gateway(gateway_id)
    delay = (datetime.fromisoformat(retry["next_attempt_at"]) - datetime.now(timezone.utc)).total_seconds()
    time.sleep(max(0, min(delay + 0.2, 60)))
    recovered = dispatch(app_id, manifest, baseline_ids)
    expected_summary(recovered, 1, 1)
    after = request("/api/v1/anchors/" + anchor_id)
    assert after["status"] == "anchored" and after["attempt_count"] == 2 and after["last_error_code"] is None
    record = readback(targets, anchor_id)
    assert record["eventHash"] == before["event_hash"]
    after_height = outage.chain_height(targets["cli"]["Id"])
    assert after_height == before_height + 1
    assert outage.gateway("/anchors", record, container=gateway_id) == {"status": 200, "body": record}
    assert outage.chain_height(targets["cli"]["Id"]) == after_height
    expected_summary(dispatch(app_id, manifest, baseline_ids), 0, 0)
    return dict(synthetic_subject_id=application["request_id"], failed_dispatch=failed,
                recovered_dispatch=recovered, retry_error=retry["last_error_code"],
                attempt_count=after["attempt_count"], height_before=before_height,
                height_after=after_height, gateway_restored=True,
                gateway_cli_readback_equal=True, duplicate_added_blocks=0)


def internal_error(args, targets, baseline_ids):
    """Exercise the app's HTTP 500 path on one new manifest anchor only."""
    app_id, gateway_id, cli_id = (targets[name]["Id"] for name in ("app", "gateway", "cli"))
    auditor = maintenance_client(args, "auditor.demo")
    supplier = maintenance_client(args, "supplier.demo")
    request = lambda path: outage.api(auditor, path, base_url=args.base_url)
    marker = browser.synthetic_prefix(args.run_id) + uuid.uuid4().hex[:10]
    application = outage.api(supplier, "/api/v1/applications", {
        "core_enterprise_organization_code": "CORE-001", "contract_number": marker,
        "invoice_number": marker, "amount": 100, "term_days": 30,
        "payment_delay_days": 0, "counterparty_risk": 0.1,
        "relationship_months": 12, "transactions_last_30d": 2,
    }, base_url=args.base_url)
    save(args.output / "application.json", {
        "subject_id": application["request_id"], "synthetic_run_id": args.run_id,
    })
    manifest = make_manifest(app_id, args.run_id, application["request_id"], baseline_ids, args.output)
    if len(manifest["anchors"]) != 1:
        raise RuntimeError("HTTP 500 acceptance requires exactly one new synthetic anchor")
    anchor = manifest["anchors"][0]
    anchor_id = anchor["anchor_id"]
    before = request("/api/v1/anchors/" + anchor_id)
    assert before["status"] == "pending" and before["attempt_count"] == 0
    assert before["subject_id"] == application["request_id"]
    assert outage.gateway("/anchors/" + anchor_id, container=gateway_id)["status"] == 404
    height_before = outage.chain_height(cli_id)
    check_identity(targets)

    started = datetime.now(timezone.utc)
    with InternalErrorOnce(app_id, anchor) as fault:
        failed = dispatch(app_id, manifest, baseline_ids, gateway_url=fault.url)
    finished = datetime.now(timezone.utc)
    expected_summary(failed, 1, 0, 1)
    retry = request("/api/v1/anchors/" + anchor_id)
    next_attempt_at = datetime.fromisoformat(retry["next_attempt_at"])
    assert retry["status"] == "pending" and retry["attempt_count"] == 1
    assert retry["last_error_code"] == "INTERNAL_ERROR"
    assert retry["event_hash"] == before["event_hash"]
    assert started + timedelta(seconds=2) <= next_attempt_at
    assert next_attempt_at <= finished + timedelta(seconds=2)
    assert outage.chain_height(cli_id) == height_before
    assert outage.gateway("/anchors/" + anchor_id, container=gateway_id)["status"] == 404
    save(args.output / "injected-500.json", {
        "provenance": "SYNTHETIC_CONTROLLED_HTTP_500_INJECTION",
        "fault": fault.result, "failed_dispatch": failed, "pending_anchor": retry,
        "height_before": height_before,
    })

    delay = (next_attempt_at - datetime.now(timezone.utc)).total_seconds()
    time.sleep(max(0, min(delay + 0.2, 60)))
    recovered = dispatch(app_id, manifest, baseline_ids)
    expected_summary(recovered, 1, 1)
    after = request("/api/v1/anchors/" + anchor_id)
    assert after["status"] == "anchored" and after["attempt_count"] == 2
    assert after["last_error_code"] is None
    record = readback(targets, anchor_id)
    assert record["eventHash"] == before["event_hash"]
    height_after = outage.chain_height(cli_id)
    assert height_after == height_before + 1
    assert outage.gateway("/anchors", record, container=gateway_id) == {
        "status": 200, "body": record,
    }
    assert outage.chain_height(cli_id) == height_after
    expected_summary(dispatch(app_id, manifest, baseline_ids), 0, 0)
    return dict(synthetic_subject_id=application["request_id"],
                failed_dispatch=failed, recovered_dispatch=recovered,
                retry_error=retry["last_error_code"], attempt_count=after["attempt_count"],
                height_before=height_before, height_after=height_after,
                gateway_cli_readback_equal=True, duplicate_added_blocks=0,
                fault_provenance="SYNTHETIC_CONTROLLED_HTTP_500_INJECTION")


def run(args):
    if sys.flags.optimize:
        raise RuntimeError("Acceptance must run without Python assertion optimization")
    browser.synthetic_prefix(args.run_id)
    args.base_url, targets, endpoint = resolve_targets(args)
    args.output.mkdir(parents=True, exist_ok=False)
    baseline = json.loads(args.baseline_snapshot.read_text(encoding="utf-8"))
    before = snapshot(targets["app"]["Id"])
    allow_prior_acceptance = args.phase != "internal-error"
    errors = snapshots.compare_history(
        baseline, before,
        allow_new_rows=allow_prior_acceptance,
        allow_sequence_advance=allow_prior_acceptance,
    )
    if errors:
        raise RuntimeError("Pre-acceptance history check failed: " + str(errors))
    if before["revisions"] != baseline["revisions"]:
        raise RuntimeError("Migration revision differs from verified baseline")
    save(args.output / "before.json", before)
    baseline_ids = [json.loads(key)[0] for key in before["tables"]["anchor_outbox"]["rows"]]
    target_evidence = {key: {field: row[field] for field in ("Id", "Image", "Name")} for key, row in targets.items()}
    save(args.output / "scope.json", dict(targets=target_evidence, docker_endpoint=endpoint,
         baseline_sha256=hashlib.sha256(args.baseline_snapshot.read_bytes()).hexdigest()))
    failure = None
    try:
        phases = {"journey": journey, "outage": gateway_outage, "internal-error": internal_error}
        result = phases[args.phase](args, targets, baseline_ids)
    except BaseException as error:
        failure = error
        raise
    finally:
        cleanup_errors = []
        for actor in getattr(args, "_actors", []):
            try:
                logout_api(actor, args.base_url)
            except Exception as error:
                cleanup_errors.append(type(error).__name__)
        after = snapshot(targets["app"]["Id"])
        save(args.output / "after.json", after)
        errors = snapshots.compare_history(before, after, allow_new_rows=True, allow_sequence_advance=True)
        if before["revisions"] != after["revisions"]:
            errors.append("Migration revision changed during acceptance")
        errors.extend("Session cleanup failed: " + error for error in cleanup_errors)
        save(args.output / "history.json", dict(history_equal=not errors, errors=errors,
             baseline_anchor_count=len(baseline_ids), new_ledger_events=after["ledger"]["event_count"] - before["ledger"]["event_count"]))
        if errors and failure is None:
            raise RuntimeError("Historical rows changed: " + str(errors))
    check_identity(targets)
    result.update(accepted_at=datetime.now(timezone.utc).isoformat(), provenance="SYNTHETIC_CONTROLLED_ACCEPTANCE",
                  run_id=args.run_id, phase=args.phase, base_url=args.base_url, history_equal=True)
    save(args.output / "accepted.json", result)
    print(json.dumps(result, indent=2))


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-synthetic-writes", action="store_true", required=True)
    parser.add_argument("--allow-local-gateway-outage", action="store_true")
    parser.add_argument("--allow-internal-error-injection", action="store_true")
    parser.add_argument("--phase", choices=("journey", "outage", "internal-error"), required=True)
    for name in ("base-url", "app-container", "gateway-container", "cli-container", "prover-container", "run-id"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--source-app-container")
    parser.add_argument("--postgres-container")
    parser.add_argument("--expected-app-image")
    parser.add_argument("--baseline-snapshot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--browser-channel", default=None)
    args = parser.parse_args(argv)
    if args.phase == "outage" and not args.allow_local_gateway_outage:
        parser.error("Outage phase requires --allow-local-gateway-outage")
    if args.phase == "internal-error" and not args.allow_internal_error_injection:
        parser.error("HTTP 500 phase requires --allow-internal-error-injection")
    if args.phase == "internal-error":
        if not args.source_app_container or not args.postgres_container:
            parser.error("HTTP 500 phase requires the pinned source app and PostgreSQL containers")
        if not args.expected_app_image or not re.fullmatch(
            r"sha256:[0-9a-f]{64}", args.expected_app_image
        ):
            parser.error("HTTP 500 phase requires --expected-app-image sha256:<64 hex>")
    return args


if __name__ == "__main__":
    run(parse_args())
