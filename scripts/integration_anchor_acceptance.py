"""Dispatch synthetic browser evidence in the isolated integration stack.

Run browser/outcome acceptance first. This writes Fabric anchors, but does not
stop services, edit historical evidence, or touch the original demo defaults.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

from scripts.browser_acceptance import login


def docker(*args: str, stdin: str | None = None) -> str:
    return subprocess.run(
        ["docker", "--context", "desktop-linux", *args],
        input=stdin, capture_output=True, text=True, check=True, timeout=60,
    ).stdout.strip()


def gateway(container, path, payload=None):
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


def height(cli):
    output = docker("exec", cli, "peer", "channel", "getinfo", "-c", "scfchannel")
    return json.loads(output[output.index("{"):])["height"]


def run(args):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, channel=args.browser_channel)
        try:
            page = browser.new_page(viewport={"width": 390, "height": 844})
            page.goto(args.base_url)
            login(page, "auditor.demo")

            def api(path, payload=None):
                return page.evaluate("""async ([path, payload]) => {
                    const response = await fetch(path, payload === null ? {} : {
                        method: 'POST', headers: {'content-type': 'application/json'},
                        body: JSON.stringify(payload)
                    });
                    if (!response.ok) throw new Error(await response.text());
                    return await response.json();
                }""", [path, payload])

            assert api("/api/health")["ledger"]["valid"] is True
            anchors = api("/api/v1/anchors?limit=200")
            assert len(anchors) < 200, "Use a fresh isolated acceptance database"
            target = next(row for row in anchors if row["circuit_version"] == "invoice_limit@1")
            assert target["status"] == "pending", "Run a new browser journey first"
            anchor_id = target["anchor_id"]
            events = api("/api/ledger?limit=500")
            event = next(row for row in events if row["event_hash"] == target["event_hash"])
            payload = event["payload"]
            assert event["event_type"] == "TRADE_CONFIRMED"
            assert payload["circuit_version"] == target["circuit_version"]
            assert payload["proof_sha256"] == target["proof_sha256"]
            assert "proof_fallback_code" not in payload
            # Verify the persisted proof independently, not a newly generated one.
            verification = (
                "import { verifyInvoiceLimit } from './index.mjs';"
                "let s='';for await(const c of process.stdin)s+=c;"
                "const p=JSON.parse(s);"
                "if(!await verifyInvoiceLimit(p.invoice_limit_proof,"
                "p.invoice_limit_public_signals))process.exit(1);"
                "p.invoice_limit_public_signals[1]=(BigInt(p.invoice_limit_public_signals[1])+1n).toString();"
                "if(await verifyInvoiceLimit(p.invoice_limit_proof,"
                "p.invoice_limit_public_signals))process.exit(2);"
                "console.log('persisted-proof-valid; tampered-signal-rejected');"
            )
            verified = docker("exec", "-i", args.prover_container, "node", "--input-type=module",
                              "-e", verification, stdin=json.dumps(payload))
            assert "tampered-signal-rejected" in verified
            before_height = height(args.cli_container)
            assert gateway(args.gateway_container, "/anchors/" + anchor_id)["status"] == 404
            dispatches = []
            for _ in range(3):
                dispatched = api("/api/v1/anchor-dispatches", {"limit": 100})
                dispatches.append(dispatched)
                assert dispatched["retryable"] == dispatched["permanent_failed"] == 0
                if dispatched["claimed"] == 0:
                    break
            assert dispatches[-1]["claimed"] == 0, "Outbox was not fully drained"
            after = api("/api/v1/anchors/" + anchor_id)
            assert after["status"] == "anchored" and after["attempt_count"] == 1
            record_response = gateway(args.gateway_container, "/anchors/" + anchor_id)
            assert record_response["status"] == 200
            record = record_response["body"]
            cli_record = json.loads(docker(
                "exec", args.cli_container, "peer", "chaincode", "query", "-C", "scfchannel",
                "-n", "audit-anchor", "-c", json.dumps({"Args": ["ReadAnchor", anchor_id]}),
            ))
            assert record == cli_record
            assert record["circuitVersion"] == "invoice_limit@1"
            assert record["proofSha256"] == payload["proof_sha256"]
            assert record["eventHash"] == target["event_hash"]
            assert record["subjectId"] == target["subject_id"]
            after_height = height(args.cli_container)
            assert after_height == before_height + sum(row["anchored"] for row in dispatches)
            assert gateway(args.gateway_container, "/anchors", record) == {
                "status": 200, "body": record,
            }
            assert height(args.cli_container) == after_height
            assert api("/api/health")["ledger"]["valid"] is True
            page.locator('[data-view-button="ledger"]:visible').click()
            page.locator("#refreshAnchors").click()
            # The UI intentionally renders only the latest twelve records. A
            # lifecycle journey can put its proof event outside that window;
            # check the current first row, while verifying the proof via API/CLI.
            ui_anchor_id = api("/api/v1/anchors?limit=50")[0]["anchor_id"]
            page.locator(f'[data-anchor-id="{ui_anchor_id}"][data-anchor-status="anchored"]').wait_for()
            panel = page.locator("#fabricAnchorPanel")
            box = panel.bounding_box()
            assert box is not None and box["x"] >= 0 and box["width"] <= 390
            args.screenshot.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(args.screenshot), full_page=True)
            return {
                "accepted_at": datetime.now(timezone.utc).isoformat(),
                "provenance": "CONTROLLED_INTEGRATION_ACCEPTANCE",
                "anchor_id": anchor_id, "subject_id": target["subject_id"],
                "event_hash": target["event_hash"], "proof_sha256": payload["proof_sha256"],
                "circuit_version": record["circuitVersion"], "dispatches": dispatches,
                "height_before": before_height, "height_after": after_height,
                "persisted_proof_verified": True, "tampered_signal_rejected": True,
                "gateway_cli_readback_equal": True, "duplicate_post_status": 200,
                "duplicate_post_added_blocks": 0, "mobile_ui_verified": True,
                "mobile_ui_anchor_id": ui_anchor_id,
            }
        finally:
            browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dispatch-synthetic-outbox", action="store_true", required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8019")
    parser.add_argument("--browser-channel", default=None)
    parser.add_argument("--gateway-container", default="daibm-integration-gateway")
    parser.add_argument("--cli-container", default="daibm-integration-cli")
    parser.add_argument("--prover-container", default="daibm-integration-prover")
    parser.add_argument("--screenshot", type=Path, default=Path("output/integration-anchor-acceptance.png"))
    print(json.dumps(run(parser.parse_args()), indent=2))
