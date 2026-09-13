"""Mock-only outage scope and restoration checks; never connect to Docker or HTTP."""
from __future__ import annotations

import json
import subprocess
from copy import deepcopy
from types import SimpleNamespace

import pytest

from scripts import fabric_outage_acceptance as outage


GATEWAY_ID = "a" * 64
CLI_ID = "b" * 64
IMAGE_ID = "sha256:" + "c" * 64
BASE_URL = "http://127.0.0.1:8030"
GATEWAY_NAME = "safeupgrade-rehearsal-gateway"
CLI_NAME = "safeupgrade-rehearsal-cli"
ENDPOINT = "npipe:////./pipe/dockerDesktopLinuxEngine"


def container_row(name, container_id, service):
    return {
        "Id": container_id, "Image": IMAGE_ID, "Name": "/" + name,
        "State": {"Running": True},
        "Config": {"Labels": {
            "com.docker.compose.service": service,
            "com.docker.compose.project": "safeupgrade-rehearsal-fabric",
        }},
        "NetworkSettings": {"Networks": {"safeupgrade-rehearsal-fabric-network": {}}},
    }


class MockStack:
    def __init__(self, monkeypatch):
        self.endpoint = ENDPOINT
        self.gateway_row = container_row(GATEWAY_NAME, GATEWAY_ID, "gateway")
        self.cli_row = container_row(CLI_NAME, CLI_ID, "cli")
        self.created = False
        self.running = True
        self.attempts = 0
        self.anchored = False
        self.stop_error = False
        self.dispatch_error = False
        self.start_error = False
        self.changed_identity = False
        self.calls = []
        self.readiness = []
        self.api_origins = []
        self.login_origins = []
        self.record = {"anchorId": "synthetic-anchor", "eventHash": "event-hash"}
        monkeypatch.setattr(outage, "docker", self.docker)
        monkeypatch.setattr(outage, "api", self.api)
        monkeypatch.setattr(outage, "client", self.client)
        monkeypatch.setattr(outage, "gateway", self.gateway)
        monkeypatch.setattr(outage, "chain_height", self.height)
        monkeypatch.setattr(outage, "wait_gateway", self.wait_gateway)
        monkeypatch.setattr(outage.time, "sleep", lambda delay: None)

    def docker(self, *args):
        self.calls.append(args)
        if args == ("context", "inspect", "desktop-linux", "--format", "{{json .Endpoints.docker}}"):
            return json.dumps({"Host": self.endpoint})
        if args[:2] == ("container", "inspect"):
            if args[2] in {GATEWAY_NAME, GATEWAY_ID}:
                row = deepcopy(self.gateway_row)
                if args[2] == GATEWAY_ID and self.changed_identity:
                    row["Image"] = "sha256:" + "d" * 64
            elif args[2] == CLI_NAME:
                row = self.cli_row
            else:
                raise AssertionError(f"Unexpected container: {args}")
            return json.dumps([row])
        if args == ("stop", "--time", "10", GATEWAY_ID):
            self.running = False
            if self.stop_error:
                raise subprocess.TimeoutExpired(args, 45)
            return GATEWAY_ID
        if args == ("inspect", GATEWAY_ID, "--format", "{{.State.Running}}"):
            return str(self.running).lower()
        if args == ("start", GATEWAY_ID):
            self.running = True
            if self.start_error:
                raise subprocess.TimeoutExpired(args, 45)
            return GATEWAY_ID
        if args[:4] == ("exec", CLI_ID, "peer", "chaincode"):
            return json.dumps(self.record)
        raise AssertionError(f"Unexpected Docker operation: {args}")

    def client(self, username, *, base_url):
        self.login_origins.append(base_url)
        return username

    def anchor(self):
        return {
            "anchor_id": "synthetic-anchor", "subject_id": "synthetic-request",
            "event_hash": "event-hash", "status": "anchored" if self.anchored else "pending",
            "attempt_count": self.attempts,
            "last_error_code": "FABRIC_UNAVAILABLE" if self.attempts == 1 else None,
            "next_attempt_at": "2020-01-01T00:00:00+00:00",
        }

    def api(self, client, path, payload=None, *, base_url):
        self.api_origins.append(base_url)
        if path == "/api/health":
            return {"ledger": {"valid": True}}
        if path == "/api/v1/anchors?limit=200":
            return [self.anchor()] if self.created else []
        if path == "/api/v1/applications":
            assert payload["contract_number"].startswith("SIM-OUTAGE-")
            self.created = True
            return {"request_id": "synthetic-request"}
        if path == "/api/v1/anchors/synthetic-anchor":
            return self.anchor()
        if path == "/api/v1/anchor-dispatches":
            if not self.running and self.dispatch_error:
                raise RuntimeError("dispatch connection failed")
            if self.anchored:
                return {"claimed": 0}
            self.attempts += 1
            self.anchored = self.running
            return {
                "claimed": 1, "anchored": int(self.running),
                "retryable": int(not self.running), "permanent_failed": 0,
            }
        raise AssertionError(f"Unexpected API operation: {path}")

    def gateway(self, path, payload=None, *, container):
        assert container == GATEWAY_ID
        assert self.running
        if path == "/anchors":
            assert payload == self.record
            return {"status": 200, "body": self.record}
        assert path == "/anchors/synthetic-anchor"
        return {"status": 200 if self.anchored else 404, "body": self.record}

    def height(self, container):
        assert container == CLI_ID
        return 50 + int(self.anchored)

    def wait_gateway(self, container):
        self.readiness.append(container)
        assert container == GATEWAY_ID and self.running

    def run(self):
        outage.run(base_url=BASE_URL, gateway_container=GATEWAY_NAME, cli_container=CLI_NAME)


def test_parameterized_outage_pins_target_and_emits_evidence(monkeypatch, capsys):
    stack = MockStack(monkeypatch)
    stack.run()
    result = json.loads(capsys.readouterr().out)
    assert set(stack.api_origins + stack.login_origins) == {BASE_URL}
    assert [call for call in stack.calls if call[0] in {"stop", "start"}] == [
        ("stop", "--time", "10", GATEWAY_ID), ("start", GATEWAY_ID),
    ]
    assert stack.readiness == [GATEWAY_ID, GATEWAY_ID]
    assert result["base_url"] == BASE_URL
    assert result["gateway_container_id"] == GATEWAY_ID
    assert result["gateway_image_id"] == IMAGE_ID
    assert result["cli_container_id"] == CLI_ID
    assert result["docker_endpoint"] == ENDPOINT
    assert result["height_after"] == result["height_before"] + 1
    assert result["identical_retry_status"] == 200 and result["gateway_restored"]


@pytest.mark.parametrize("failure", ["stop_error", "dispatch_error", "start_error"])
def test_failure_still_starts_same_gateway_and_checks_readiness(monkeypatch, failure):
    stack = MockStack(monkeypatch)
    setattr(stack, failure, True)
    with pytest.raises((subprocess.TimeoutExpired, RuntimeError)):
        stack.run()
    assert stack.running
    assert stack.readiness == [GATEWAY_ID, GATEWAY_ID]
    assert [call for call in stack.calls if call[0] == "start"] == [("start", GATEWAY_ID)]


@pytest.mark.parametrize("bad_scope", ["remote_context", "service", "project", "network", "stopped"])
def test_bad_scope_is_rejected_before_application_writes_or_outage(monkeypatch, bad_scope):
    stack = MockStack(monkeypatch)
    if bad_scope == "remote_context":
        stack.endpoint = "ssh://other-host"
    elif bad_scope == "service":
        stack.gateway_row["Config"]["Labels"]["com.docker.compose.service"] = "postgres"
    elif bad_scope == "project":
        stack.cli_row["Config"]["Labels"]["com.docker.compose.project"] = "original-fabric"
    elif bad_scope == "network":
        stack.cli_row["NetworkSettings"]["Networks"] = {"original-network": {}}
    else:
        stack.gateway_row["State"]["Running"] = False
    with pytest.raises(RuntimeError):
        stack.run()
    assert not stack.created and not stack.login_origins
    assert not any(call[0] in {"stop", "start"} for call in stack.calls)


def test_changed_gateway_identity_is_not_stopped(monkeypatch):
    stack = MockStack(monkeypatch)
    stack.changed_identity = True
    with pytest.raises(RuntimeError, match="identity changed"):
        stack.run()
    assert not any(call[0] in {"stop", "start"} for call in stack.calls)


@pytest.mark.parametrize("url", [
    "https://example.com", "http://127.0.0.1:8030/path", "http://user@localhost:8030",
    "http://localhost:8030?target=old", "file:///tmp/app",
])
def test_nonlocal_or_nonorigin_base_url_is_rejected(url):
    with pytest.raises(ValueError, match="loopback"):
        outage._local_base_url(url)


def test_cli_keeps_defaults_and_accepts_explicit_targets():
    defaults = outage.parse_args(["--allow-local-gateway-outage"])
    assert (defaults.base_url, defaults.gateway_container, defaults.cli_container) == (
        outage.BASE, outage.GATEWAY, outage.CLI,
    )
    selected = outage.parse_args([
        "--allow-local-gateway-outage", "--base-url", BASE_URL,
        "--gateway-container", GATEWAY_NAME, "--cli-container", CLI_NAME,
    ])
    assert (selected.base_url, selected.gateway_container, selected.cli_container) == (
        BASE_URL, GATEWAY_NAME, CLI_NAME,
    )
    with pytest.raises(SystemExit):
        outage.parse_args([])


def test_docker_always_uses_desktop_linux_context(monkeypatch):
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(stdout="answer\n")

    monkeypatch.setattr(outage.subprocess, "run", fake_run)
    assert outage.docker("inspect", GATEWAY_ID) == "answer"
    assert calls[0][0] == ["docker", "--context", "desktop-linux", "inspect", GATEWAY_ID]
    assert calls[0][1]["timeout"] == 45
