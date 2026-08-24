from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

try:
    from scripts.browser_acceptance import BASE_URL, login, run_acceptance
except ModuleNotFoundError:  # Direct `python scripts\...py` execution.
    from browser_acceptance import BASE_URL, login, run_acceptance


ROOT = Path(__file__).parents[1]
FABRIC_COMPOSE = ROOT / "advanced" / "fabric" / "network" / "docker-compose.fabric.yml"
COMPOSE = [
    "docker",
    "--context",
    "desktop-linux",
    "compose",
    "-f",
    str(FABRIC_COMPOSE),
    "--project-name",
    "daibm-fabric-demo",
]


def _compose_exec(service: str, *command: str) -> str:
    result = subprocess.run(
        [*COMPOSE, "exec", "-T", service, *command],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _read_gateway(anchor_id: str) -> dict[str, object]:
    script = (
        f"fetch('http://127.0.0.1:8090/anchors/{anchor_id}')"
        ".then(async response => { if (!response.ok) throw new Error(await response.text()); "
        "return response.json(); }).then(value => process.stdout.write(JSON.stringify(value)))"
        ".catch(error => { console.error(error.message); process.exit(1); })"
    )
    return json.loads(_compose_exec("gateway", "node", "-e", script))


def _read_cli(anchor_id: str) -> dict[str, object]:
    invocation = json.dumps({"Args": ["ReadAnchor", anchor_id]}, separators=(",", ":"))
    output = _compose_exec(
        "cli",
        "peer",
        "chaincode",
        "query",
        "-C",
        "scfchannel",
        "-n",
        "audit-anchor",
        "-c",
        invocation,
    )
    return json.loads(output.splitlines()[-1])


def run_fabric_acceptance() -> str:
    run_acceptance(record_video=False)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 390, "height": 844})
            page.goto(BASE_URL)
            page.wait_for_load_state("networkidle")
            login(page, "auditor.demo")
            page.locator('[data-view-button="ledger"]:visible').click()
            panel = page.locator("#fabricAnchorPanel")
            panel.wait_for(state="visible")
            page.locator("#refreshAnchors").click()
            page.wait_for_function(
                "document.querySelector('#fabricAnchorPanel').getAttribute('aria-busy') === 'false'"
            )
            anchors = page.evaluate(
                "fetch('/api/v1/anchors?limit=50').then(response => response.json())"
            )
            target = next(anchor for anchor in anchors if anchor["status"] == "pending")
            anchor_id = target["anchor_id"]
            dispatch_deadline = time.monotonic() + 180
            while time.monotonic() < dispatch_deadline:
                page.locator("#dispatchAnchors").click()
                page.wait_for_function(
                    "document.querySelector('#fabricAnchorPanel').getAttribute('aria-busy') === 'false'"
                )
                current = page.evaluate(
                    "anchorId => fetch(`/api/v1/anchors/${anchorId}`).then(response => response.json())",
                    anchor_id,
                )
                if current["status"] == "anchored":
                    target = current
                    break
            assert target["status"] == "anchored", {
                "target": target,
                "reason": "new anchor was not reached while draining the persistent outbox",
            }
            row = page.locator(f'[data-anchor-id="{anchor_id}"]')
            row.wait_for(state="visible")
            assert row.get_attribute("data-anchor-status") == "anchored"
            box = panel.bounding_box()
            assert box is not None and box["x"] >= 0 and box["width"] <= 390
        finally:
            browser.close()

    gateway_anchor = _read_gateway(anchor_id)
    cli_anchor = _read_cli(anchor_id)
    assert gateway_anchor == cli_anchor
    assert gateway_anchor["anchorId"] == anchor_id
    assert gateway_anchor["eventHash"] == target["event_hash"]
    return anchor_id


if __name__ == "__main__":
    accepted_anchor_id = run_fabric_acceptance()
    print(f"fabric_browser_acceptance=passed anchor_id={accepted_anchor_id}")
