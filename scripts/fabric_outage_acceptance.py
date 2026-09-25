"""Opt-in local gateway outage acceptance; leaves one synthetic draft as evidence."""
from __future__ import annotations

import os

import argparse
import json
import re
import subprocess
import time
import uuid
from datetime import datetime, timezone
from functools import partial
from http.cookiejar import CookieJar
from urllib.parse import urlsplit
from urllib.request import HTTPCookieProcessor, Request, build_opener

# The demo password is deployment configuration, never a literal.
DEMO_PASSWORD = os.environ.get("DAIBM_DEMO_PASSWORD", "")


BASE = "http://127.0.0.1:8010"
GATEWAY = "daibm-fabric-anchor-gateway"
CLI = "daibm-fabric-cli"
DOCKER_CONTEXT = "desktop-linux"


def docker(*args):
    return subprocess.run(
        ["docker", "--context", DOCKER_CONTEXT, *args],
        check=True, capture_output=True, text=True, timeout=45,
    ).stdout.strip()


def api(client, path, payload=None, *, base_url=BASE):
    request = Request(
        base_url + path,
        data=None if payload is None else json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with client.open(request, timeout=20) as response:
        return json.load(response)


def client(username, *, base_url=BASE):
    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    api(opener, "/api/v1/auth/login", {"username": username, "password": DEMO_PASSWORD},
        base_url=base_url)
    return opener


def gateway(path, payload=None, *, container=GATEWAY):
    options = {} if payload is None else {
        "method": "POST", "headers": {"content-type": "application/json"},
        "body": json.dumps(payload),
    }
    script = (
        f"fetch({json.dumps('http://127.0.0.1:8090' + path)}, {json.dumps(options)})"
        ".then(async r=>console.log(JSON.stringify({status:r.status,body:await r.json()})))"
        ".catch(e=>{console.error(e.message);process.exit(1)})"
    )
    return json.loads(docker("exec", container, "node", "-e", script))


def wait_gateway(container=GATEWAY):
    deadline = time.monotonic() + 90
    while True:
        try:
            result = gateway("/health", container=container)
            if result["status"] == 200 and result["body"]["fabric"] == "ready":
                return
        except (subprocess.SubprocessError, ValueError, KeyError):
            pass
        if time.monotonic() >= deadline:
            raise RuntimeError("Gateway did not become ready after restart")
        time.sleep(2)


def chain_height(container=CLI):
    output = docker("exec", container, "peer", "channel", "getinfo", "-c", "scfchannel")
    return json.loads(output[output.index("{"):])["height"]


def _local_base_url(value):
    value = value.rstrip("/")
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"}
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username is not None or parsed.password is not None
            or parsed.path or parsed.query or parsed.fragment):
        raise ValueError("Outage acceptance requires a loopback application origin")
    return value


def _inspect_container(name, service):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name):
        raise ValueError("Select one explicit container name or ID")
    rows = json.loads(docker("container", "inspect", name))
    if len(rows) != 1:
        raise RuntimeError("Container selection was not unique")
    row = rows[0]
    labels = row.get("Config", {}).get("Labels") or {}
    if (not re.fullmatch(r"[0-9a-f]{64}", row.get("Id", ""))
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", row.get("Image", ""))
            or labels.get("com.docker.compose.service") != service
            or not labels.get("com.docker.compose.project")
            or row.get("State", {}).get("Running") is not True):
        raise RuntimeError(f"Selected container is not a running Compose {service}")
    return row


def _resolve_targets(gateway_container, cli_container):
    endpoint = json.loads(docker(
        "context", "inspect", DOCKER_CONTEXT, "--format", "{{json .Endpoints.docker}}",
    ))["Host"]
    if not endpoint.startswith(("npipe://", "unix://")):
        raise RuntimeError("Outage acceptance requires a local desktop-linux endpoint")
    gateway_row = _inspect_container(gateway_container, "gateway")
    cli_row = _inspect_container(cli_container, "cli")
    gateway_project = gateway_row["Config"]["Labels"]["com.docker.compose.project"]
    cli_project = cli_row["Config"]["Labels"]["com.docker.compose.project"]
    if (gateway_row["Id"] == cli_row["Id"] or gateway_project != cli_project
            or not (set(gateway_row["NetworkSettings"]["Networks"])
                    & set(cli_row["NetworkSettings"]["Networks"]))):
        raise RuntimeError("Gateway and CLI must belong to the same connected Compose project")
    return endpoint, gateway_row, cli_row


def _restore_gateway(container_id):
    try:
        docker("start", container_id)
    finally:
        # A timeout may occur after Docker has started the container.
        wait_gateway(container_id)


def run(resume_anchor=None, *, base_url=BASE, gateway_container=GATEWAY, cli_container=CLI):
    base_url = _local_base_url(base_url)
    endpoint, selected_gateway, selected_cli = _resolve_targets(gateway_container, cli_container)
    gateway_id, cli_id = selected_gateway["Id"], selected_cli["Id"]
    request = partial(api, base_url=base_url)
    gateway_request = partial(gateway, container=gateway_id)
    height = partial(chain_height, container=cli_id)
    auditor, supplier = client("auditor.demo", base_url=base_url), client("supplier.demo", base_url=base_url)
    wait_gateway(gateway_id)
    health = request(auditor, "/api/health")
    assert health["ledger"]["valid"] is True
    rows = request(auditor, "/api/v1/anchors?limit=200")
    assert len(rows) < 200 and all(
        row["status"] == "anchored" or row["anchor_id"] == resume_anchor for row in rows
    ), "Drain existing outbox first"
    suffix = uuid.uuid4().hex[:12]
    if resume_anchor:
        existing = request(auditor, "/api/v1/anchors/" + resume_anchor)
        application = request(supplier, "/api/v1/applications/" + existing["subject_id"])
        assert application["status"] == "draft" and application["contract_number"].startswith("SIM-OUTAGE-")
        assert existing["status"] == "pending" and existing["attempt_count"] == 0
    else:
        application = request(supplier, "/api/v1/applications", {
            "core_enterprise_organization_code": "CORE-001",
            "contract_number": f"SIM-OUTAGE-{suffix}", "invoice_number": f"SIM-OUTAGE-{suffix}",
            "amount": 100, "term_days": 30, "payment_delay_days": 0,
            "counterparty_risk": 0.1, "relationship_months": 12, "transactions_last_30d": 2,
        })
    pending = [row for row in request(auditor, "/api/v1/anchors?limit=200") if row["status"] == "pending"]
    assert len(pending) == 1, pending
    before = pending[0]
    anchor_id = before["anchor_id"]
    assert before["subject_id"] == application["request_id"]
    height_before = height()
    assert gateway_request("/anchors/" + anchor_id)["status"] == 404
    current_gateway = _inspect_container(gateway_id, "gateway")
    if (current_gateway["Id"] != gateway_id
            or current_gateway["Image"] != selected_gateway["Image"]
            or current_gateway["Name"] != selected_gateway["Name"]):
        raise RuntimeError("Gateway identity changed before the outage")
    try:
        # Restoration runs even when stop times out after stopping the container.
        docker("stop", "--time", "10", gateway_id)
        assert docker("inspect", gateway_id, "--format", "{{.State.Running}}") == "false"
        failed = request(auditor, "/api/v1/anchor-dispatches", {"limit": 1})
        assert failed == {"claimed": 1, "anchored": 0, "retryable": 1, "permanent_failed": 0}, failed
        retry = request(auditor, "/api/v1/anchors/" + anchor_id)
        assert retry["status"] == "pending" and retry["last_error_code"] in {"FABRIC_UNAVAILABLE", "FABRIC_TIMEOUT"}
        assert retry["attempt_count"] == before["attempt_count"] + 1
        assert retry["event_hash"] == before["event_hash"]
        assert request(auditor, "/api/health")["ledger"]["valid"] is True
        assert height() == height_before
    finally:
        _restore_gateway(gateway_id)
    delay = (datetime.fromisoformat(retry["next_attempt_at"]) - datetime.now(timezone.utc)).total_seconds()
    time.sleep(max(0, min(delay + 0.2, 60)))
    recovered = request(auditor, "/api/v1/anchor-dispatches", {"limit": 1})
    assert recovered == {"claimed": 1, "anchored": 1, "retryable": 0, "permanent_failed": 0}, recovered
    after = request(auditor, "/api/v1/anchors/" + anchor_id)
    assert after["status"] == "anchored" and after["last_error_code"] is None
    assert after["attempt_count"] == before["attempt_count"] + 2
    record = gateway_request("/anchors/" + anchor_id)["body"]
    chain_record = json.loads(docker(
        "exec", cli_id, "peer", "chaincode", "query", "-C", "scfchannel",
        "-n", "audit-anchor", "-c", json.dumps({"Args": ["ReadAnchor", anchor_id]}),
    ))
    assert record == chain_record and record["eventHash"] == before["event_hash"]
    height_after = height()
    assert height_after == height_before + 1
    identical = gateway_request("/anchors", record)
    assert identical == {"status": 200, "body": record}
    assert height() == height_after
    assert request(auditor, "/api/v1/anchor-dispatches", {"limit": 1})["claimed"] == 0
    assert request(auditor, "/api/health")["ledger"]["valid"] is True
    print(json.dumps({
        "accepted_at": datetime.now(timezone.utc).isoformat(),
        "base_url": base_url, "docker_context": DOCKER_CONTEXT, "docker_endpoint": endpoint,
        "gateway_container": selected_gateway["Name"].lstrip("/"),
        "gateway_container_id": gateway_id, "gateway_image_id": selected_gateway["Image"],
        "cli_container": selected_cli["Name"].lstrip("/"),
        "cli_container_id": cli_id,
        "anchor_id": anchor_id, "subject_id": before["subject_id"], "event_hash": before["event_hash"],
        "failed_dispatch": failed, "retry_error": retry["last_error_code"],
        "recovered_dispatch": recovered, "attempt_count": after["attempt_count"],
        "height_before": height_before, "height_after": height_after,
        "readback_equal": True, "identical_retry_status": identical["status"], "gateway_restored": True,
    }, indent=2))


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-local-gateway-outage", action="store_true", required=True)
    parser.add_argument("--resume-anchor", help="Reuse an untouched SIM-OUTAGE draft anchor")
    parser.add_argument("--base-url", default=BASE)
    parser.add_argument("--gateway-container", default=GATEWAY)
    parser.add_argument("--cli-container", default=CLI)
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = parse_args()
    run(args.resume_anchor, base_url=args.base_url, gateway_container=args.gateway_container,
        cli_container=args.cli_container)
