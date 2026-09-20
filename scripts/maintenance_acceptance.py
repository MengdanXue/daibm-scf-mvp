"""Explicit synthetic-only local acceptance, even with historical pending work.

Never calls the global anchor-dispatch API. All private snapshots and manifests
must stay on the operator's local machine. Run against the restored clone first.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request

from playwright.sync_api import sync_playwright
from app.ledger import canonical_json

from scripts import browser_acceptance as browser
from scripts import fabric_outage_acceptance as outage
from scripts import maintenance_snapshot as snapshots


def docker(*args, stdin=None):
    return subprocess.run(
        ["docker", "--context", "desktop-linux", *args], input=stdin,
        capture_output=True, text=True, check=True, timeout=120,
    ).stdout.strip()


def save(path, value):
    # Failure evidence is retained; never overwrite a previous maintenance run.
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)


def resolve_targets(args):
    base = outage._local_base_url(args.base_url)
    endpoint, gateway, cli = outage._resolve_targets(args.gateway_container, args.cli_container)
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
    return base, {"app": app, "gateway": gateway, "cli": cli, "prover": prover}, endpoint


def check_identity(targets):
    for row in targets.values():
        current = json.loads(docker("container", "inspect", row["Id"]))[0]
        if (current["Id"], current["Image"], current["Name"]) != (row["Id"], row["Image"], row["Name"]):
            raise RuntimeError("A pinned container identity changed")
        if current["State"]["Running"] is not True:
            raise RuntimeError("A pinned container is not running")


def snapshot(app_id):
    code = Path(snapshots.__file__).read_text(encoding="utf-8")
    return json.loads(docker("exec", app_id, "python", "-c", code))


def scoped_call(app_id, payload):
    # Executes reviewed maintenance code in the existing image/env, without
    # app.main startup, image replacement, file copies, or credential output.
    source = Path(__file__).with_name("maintenance_dispatch.py").read_text(encoding="utf-8")
    return json.loads(docker("exec", "-i", app_id, "python", "-c", source,
                             "--allow-synthetic-dispatch", stdin=json.dumps(payload)))


def make_manifest(app_id, run_id, subject_id, baseline_ids, output):
    manifest = scoped_call(app_id, {
        "action": "create", "run_id": run_id, "subject_ids": [subject_id],
        "baseline_anchor_ids": baseline_ids,
    })
    save(output / "manifest.json", manifest)
    return manifest


def dispatch(app_id, manifest, baseline_ids):
    return scoped_call(app_id, {
        "action": "dispatch", "manifest": manifest,
        "baseline_anchor_ids": baseline_ids, "limit": len(manifest["anchors"]),
    })


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


def run(args):
    if sys.flags.optimize:
        raise RuntimeError("Acceptance must run without Python assertion optimization")
    browser.synthetic_prefix(args.run_id)
    args.base_url, targets, endpoint = resolve_targets(args)
    args.output.mkdir(parents=True, exist_ok=False)
    baseline = json.loads(args.baseline_snapshot.read_text(encoding="utf-8"))
    before = snapshot(targets["app"]["Id"])
    errors = snapshots.compare_history(baseline, before, allow_new_rows=True, allow_sequence_advance=True)
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
        result = (journey if args.phase == "journey" else gateway_outage)(args, targets, baseline_ids)
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
    parser.add_argument("--phase", choices=("journey", "outage"), required=True)
    for name in ("base-url", "app-container", "gateway-container", "cli-container", "prover-container", "run-id"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--baseline-snapshot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--browser-channel", default=None)
    args = parser.parse_args(argv)
    if args.phase == "outage" and not args.allow_local_gateway_outage:
        parser.error("Outage phase requires --allow-local-gateway-outage")
    return args


if __name__ == "__main__":
    run(parse_args())
