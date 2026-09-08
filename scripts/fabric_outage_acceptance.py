"""Opt-in local gateway outage acceptance; leaves one synthetic draft as evidence."""
from __future__ import annotations

import argparse
import json
import subprocess
import time
import uuid
from datetime import datetime, timezone
from http.cookiejar import CookieJar
from urllib.request import HTTPCookieProcessor, Request, build_opener


BASE = "http://127.0.0.1:8010"
GATEWAY = "daibm-fabric-anchor-gateway"


def docker(*args):
    return subprocess.run(
        ["docker", "--context", "desktop-linux", *args],
        check=True, capture_output=True, text=True, timeout=45,
    ).stdout.strip()


def api(client, path, payload=None):
    request = Request(
        BASE + path,
        data=None if payload is None else json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with client.open(request, timeout=20) as response:
        return json.load(response)


def client(username):
    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    api(opener, "/api/v1/auth/login", {"username": username, "password": "Demo123!"})
    return opener


def gateway(path, payload=None):
    options = {} if payload is None else {
        "method": "POST", "headers": {"content-type": "application/json"},
        "body": json.dumps(payload),
    }
    script = (
        f"fetch({json.dumps('http://127.0.0.1:8090' + path)}, {json.dumps(options)})"
        ".then(async r=>console.log(JSON.stringify({status:r.status,body:await r.json()})))"
        ".catch(e=>{console.error(e.message);process.exit(1)})"
    )
    return json.loads(docker("exec", GATEWAY, "node", "-e", script))


def wait_gateway():
    deadline = time.monotonic() + 90
    while True:
        try:
            result = gateway("/health")
            if result["status"] == 200 and result["body"]["fabric"] == "ready":
                return
        except (subprocess.SubprocessError, ValueError, KeyError):
            pass
        if time.monotonic() >= deadline:
            raise RuntimeError("Gateway did not become ready after restart")
        time.sleep(2)


def chain_height():
    output = docker("exec", "daibm-fabric-cli", "peer", "channel", "getinfo", "-c", "scfchannel")
    return json.loads(output[output.index("{"):])["height"]


def run(resume_anchor=None):
    auditor, supplier = client("auditor.demo"), client("supplier.demo")
    wait_gateway()
    health = api(auditor, "/api/health")
    assert health["ledger"]["valid"] is True
    rows = api(auditor, "/api/v1/anchors?limit=200")
    assert len(rows) < 200 and all(
        row["status"] == "anchored" or row["anchor_id"] == resume_anchor for row in rows
    ), "Drain existing outbox first"
    suffix = uuid.uuid4().hex[:12]
    if resume_anchor:
        existing = api(auditor, "/api/v1/anchors/" + resume_anchor)
        application = api(supplier, "/api/v1/applications/" + existing["subject_id"])
        assert application["status"] == "draft" and application["contract_number"].startswith("SIM-OUTAGE-")
        assert existing["status"] == "pending" and existing["attempt_count"] == 0
    else:
        application = api(supplier, "/api/v1/applications", {
            "core_enterprise_organization_code": "CORE-001",
            "contract_number": f"SIM-OUTAGE-{suffix}", "invoice_number": f"SIM-OUTAGE-{suffix}",
            "amount": 100, "term_days": 30, "payment_delay_days": 0,
            "counterparty_risk": 0.1, "relationship_months": 12, "transactions_last_30d": 2,
        })
    pending = [row for row in api(auditor, "/api/v1/anchors?limit=200") if row["status"] == "pending"]
    assert len(pending) == 1, pending
    before = pending[0]
    anchor_id = before["anchor_id"]
    assert before["subject_id"] == application["request_id"]
    height_before = chain_height()
    assert gateway("/anchors/" + anchor_id)["status"] == 404
    try:
        # Restoration runs even when stop times out after stopping the container.
        docker("stop", "--time", "10", GATEWAY)
        assert docker("inspect", GATEWAY, "--format", "{{.State.Running}}") == "false"
        failed = api(auditor, "/api/v1/anchor-dispatches", {"limit": 1})
        assert failed == {"claimed": 1, "anchored": 0, "retryable": 1, "permanent_failed": 0}, failed
        retry = api(auditor, "/api/v1/anchors/" + anchor_id)
        assert retry["status"] == "pending" and retry["last_error_code"] in {"FABRIC_UNAVAILABLE", "FABRIC_TIMEOUT"}
        assert retry["attempt_count"] == before["attempt_count"] + 1
        assert retry["event_hash"] == before["event_hash"]
        assert api(auditor, "/api/health")["ledger"]["valid"] is True
        assert chain_height() == height_before
    finally:
        docker("start", GATEWAY)
        wait_gateway()
    delay = (datetime.fromisoformat(retry["next_attempt_at"]) - datetime.now(timezone.utc)).total_seconds()
    time.sleep(max(0, min(delay + 0.2, 60)))
    recovered = api(auditor, "/api/v1/anchor-dispatches", {"limit": 1})
    assert recovered == {"claimed": 1, "anchored": 1, "retryable": 0, "permanent_failed": 0}, recovered
    after = api(auditor, "/api/v1/anchors/" + anchor_id)
    assert after["status"] == "anchored" and after["last_error_code"] is None
    assert after["attempt_count"] == before["attempt_count"] + 2
    record = gateway("/anchors/" + anchor_id)["body"]
    chain_record = json.loads(docker(
        "exec", "daibm-fabric-cli", "peer", "chaincode", "query", "-C", "scfchannel",
        "-n", "audit-anchor", "-c", json.dumps({"Args": ["ReadAnchor", anchor_id]}),
    ))
    assert record == chain_record and record["eventHash"] == before["event_hash"]
    height_after = chain_height()
    assert height_after == height_before + 1
    identical = gateway("/anchors", record)
    assert identical == {"status": 200, "body": record}
    assert chain_height() == height_after
    assert api(auditor, "/api/v1/anchor-dispatches", {"limit": 1})["claimed"] == 0
    assert api(auditor, "/api/health")["ledger"]["valid"] is True
    print(json.dumps({
        "accepted_at": datetime.now(timezone.utc).isoformat(),
        "anchor_id": anchor_id, "subject_id": before["subject_id"], "event_hash": before["event_hash"],
        "failed_dispatch": failed, "retry_error": retry["last_error_code"],
        "recovered_dispatch": recovered, "attempt_count": after["attempt_count"],
        "height_before": height_before, "height_after": height_after,
        "readback_equal": True, "identical_retry_status": identical["status"], "gateway_restored": True,
    }, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-local-gateway-outage", action="store_true", required=True)
    parser.add_argument("--resume-anchor", help="Reuse an untouched SIM-OUTAGE draft anchor")
    args = parser.parse_args()
    run(args.resume_anchor)
