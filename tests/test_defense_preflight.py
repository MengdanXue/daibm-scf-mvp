from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from scripts.defense_preflight import HttpResponse, run_preflight


ROLE_ACCOUNTS = {
    "supplier.demo": "supplier",
    "core.demo": "core_enterprise",
    "financier.demo": "financier",
    "risk.demo": "risk_manager",
    "auditor.demo": "auditor",
}
ROOT = Path(__file__).parents[1]
ARTIFACT_SHA256 = (
    "158d273db310c3f1abf4be7cb06aee78568e475ebb7564ebbeaa16d3efeeb0e5"
)
ACTUAL_DEFENSE_DEPENDENCIES_READY = (
    "demo-role-guide"
    in (ROOT / "app/static/index.html").read_text(encoding="utf-8")
    and all(
        (ROOT / path).is_file()
        for path in (
            "docs/defense-one-page.md",
            "docs/defense-one-page.pdf",
            "docs/research-brief-en.md",
            "docs/research-brief-en.pdf",
        )
    )
)


def _write_defense_documents(root: Path) -> None:
    for relative_path in (
        "docs/defense-one-page.md",
        "docs/defense-one-page.pdf",
        "docs/research-brief-en.md",
        "docs/research-brief-en.pdf",
    ):
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"verified defense document")
    manifest = root / "artifacts/reference/model-manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps({"artifact_sha256": ARTIFACT_SHA256}), encoding="utf-8"
    )


def _ui_html(*, guide_count: int = 5) -> str:
    guide = "".join(
        f'<button class="demo-role-guide" data-demo-username="{username}">'
        f"{position:02d}</button>"
        for position, username in enumerate(ROLE_ACCOUNTS, 1)
    )
    if guide_count != 5:
        guide = guide[:0]
    return f"""
    <!doctype html>
    <html lang="ru"><body>
      <button type="button" data-lang="zh">中文</button>
      <form id="loginForm">
        <input name="username">
        <input name="password" type="password">
        <button type="submit">Войти</button>
      </form>
      {guide}
    </body></html>
    """


@dataclass
class _Call:
    client_id: int
    method: str
    path: str
    payload: dict[str, str] | None


class _FakeClient:
    def __init__(self, server: "FakeHttp", client_id: int):
        self.server = server
        self.client_id = client_id
        self.username: str | None = None

    def request(
        self,
        method: str,
        url: str,
        payload: dict[str, str] | None = None,
    ) -> HttpResponse:
        path = urlsplit(url).path
        self.server.calls.append(_Call(self.client_id, method, path, payload))
        if method == "GET" and path == "/api/health":
            research_core = {"status": "ready"}
            if self.server.artifact_sha256 is not None:
                research_core["artifact_sha256"] = self.server.artifact_sha256
            return self.server.json_response(
                {
                    "status": "ok",
                    "database": {"backend": "postgresql", "reachable": True},
                    "ledger": {"valid": True},
                    "research_core": research_core,
                }
            )
        if method == "GET" and path == "/":
            return HttpResponse(200, self.server.html.encode("utf-8"))
        if method == "POST" and path == "/api/v1/auth/login":
            assert payload is not None
            self.username = payload["username"]
            role = ROLE_ACCOUNTS[self.username]
            return self.server.json_response({"user": {"role": role}})
        if method == "GET" and path == "/api/v1/auth/session":
            assert self.username is not None
            role = ROLE_ACCOUNTS[self.username]
            if self.username == self.server.bad_session_username:
                role = "auditor"
            return self.server.json_response(
                {"authenticated": True, "user": {"role": role}}
            )
        if method == "POST" and path == "/api/v1/auth/logout":
            self.server.logout_count += 1
            self.username = None
            return HttpResponse(204, b"")
        raise AssertionError(f"Unexpected request: {method} {path}")


class FakeHttp:
    def __init__(
        self,
        *,
        html: str | None = None,
        artifact_sha256: str | None = ARTIFACT_SHA256,
    ):
        self.html = html or _ui_html()
        self.artifact_sha256 = artifact_sha256
        self.calls: list[_Call] = []
        self.logout_count = 0
        self.bad_session_username: str | None = None
        self.clients: list[_FakeClient] = []

    def client(self) -> _FakeClient:
        client = _FakeClient(self, len(self.clients))
        self.clients.append(client)
        return client

    @staticmethod
    def json_response(payload: dict) -> HttpResponse:
        return HttpResponse(200, json.dumps(payload).encode("utf-8"))


def test_preflight_checks_and_logs_out_all_five_roles(tmp_path):
    _write_defense_documents(tmp_path)
    fake_http = FakeHttp()

    result = run_preflight(
        "http://127.0.0.1:8010/", fake_http.client, project_root=tmp_path
    )

    assert result.ok is True
    assert result.roles == (
        "supplier",
        "core_enterprise",
        "financier",
        "risk_manager",
        "auditor",
    )
    assert result.errors == ()
    assert fake_http.logout_count == 5

    role_calls = [call for call in fake_http.calls if call.client_id > 0]
    assert {call.client_id for call in role_calls} == {1, 2, 3, 4, 5}
    for client_id in range(1, 6):
        calls = [call for call in role_calls if call.client_id == client_id]
        assert [(call.method, call.path) for call in calls] == [
            ("POST", "/api/v1/auth/login"),
            ("GET", "/api/v1/auth/session"),
            ("POST", "/api/v1/auth/logout"),
        ]


def test_preflight_is_read_only_and_reports_a_role_mismatch_after_cleanup(tmp_path):
    _write_defense_documents(tmp_path)
    fake_http = FakeHttp()
    fake_http.bad_session_username = "risk.demo"

    result = run_preflight(
        "http://127.0.0.1:8010", fake_http.client, project_root=tmp_path
    )

    assert result.ok is False
    assert fake_http.logout_count == 5
    assert any("risk_manager" in error and "session" in error for error in result.errors)
    assert {(call.method, call.path) for call in fake_http.calls} <= {
        ("GET", "/"),
        ("GET", "/api/health"),
        ("POST", "/api/v1/auth/login"),
        ("GET", "/api/v1/auth/session"),
        ("POST", "/api/v1/auth/logout"),
    }


def test_preflight_requires_semantic_ui_and_all_four_document_artifacts(tmp_path):
    fake_http = FakeHttp(html=_ui_html(guide_count=0))

    result = run_preflight(
        "http://127.0.0.1:8010", fake_http.client, project_root=tmp_path
    )

    assert result.ok is False
    assert fake_http.logout_count == 5
    errors = "\n".join(result.errors)
    assert "five-role guide" in errors
    assert "docs/defense-one-page.md" in errors
    assert "docs/defense-one-page.pdf" in errors
    assert "docs/research-brief-en.md" in errors
    assert "docs/research-brief-en.pdf" in errors


@pytest.mark.parametrize(
    "served_hash",
    [None, "A" * 64, "0" * 64],
    ids=["missing", "not-lowercase-hex", "wrong-artifact"],
)
def test_preflight_rejects_missing_invalid_or_wrong_runtime_artifact_hash(
    tmp_path, served_hash
):
    _write_defense_documents(tmp_path)
    fake_http = FakeHttp(artifact_sha256=served_hash)

    result = run_preflight(
        "http://127.0.0.1:8010", fake_http.client, project_root=tmp_path
    )

    assert result.ok is False
    assert fake_http.logout_count == 5
    assert any("artifact_sha256" in error for error in result.errors)


def _run_reset(
    tmp_path: Path,
    confirmation: str,
    *,
    context_available: bool = True,
    compose_exit: int = 23,
    launcher_root: Path = ROOT,
    extra_environment: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[bytes], str]:
    docker_log = tmp_path / "docker.log"
    fake_docker = tmp_path / "docker.cmd"
    context_exit = 0 if context_available else 27
    fake_docker.write_text(
        "\r\n".join(
            (
                "@echo off",
                (
                    f'echo CF=[%COMPOSE_FILE%] PROJECT=[%COMPOSE_PROJECT_NAME%] '
                    f'HOST=[%DOCKER_HOST%] ARGS=%*>>"%DOCKER_LOG%"'
                ),
                f'if "%3"=="info" exit /b {context_exit}',
                f"exit /b {compose_exit}",
            )
        )
        + "\r\n",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{tmp_path}{os.pathsep}{environment['PATH']}",
            "DOCKER_LOG": str(docker_log),
            "COMPOSE_FILE": "C:\\outside\\hostile-compose.yml",
            "COMPOSE_PROJECT_NAME": "hostile-project",
            "DOCKER_HOST": "tcp://hostile.example:2375",
            "DOCKER_CONTEXT": "hostile-remote",
        }
    )
    if extra_environment:
        environment.update(extra_environment)
    completed = subprocess.run(
        ["cmd.exe", "/d", "/c", str(launcher_root / "reset-defense-demo.cmd")],
        cwd=launcher_root,
        env=environment,
        input=f"{confirmation}\r\n".encode(),
        capture_output=True,
        timeout=30,
    )
    log = (
        docker_log.read_text(encoding="utf-8", errors="replace")
        if docker_log.exists()
        else ""
    )
    return completed, log


def test_reset_launcher_passes_fixed_local_context_to_start_after_successful_down(
    tmp_path,
):
    launcher_root = tmp_path / "controlled-demo"
    launcher_root.mkdir()
    for filename in ("reset-defense-demo.cmd", "launcher-messages.json"):
        shutil.copy2(ROOT / filename, launcher_root / filename)
    (launcher_root / "docker-compose.yml").write_text(
        "services: {}\n", encoding="utf-8"
    )
    start_log = tmp_path / "start.log"
    (launcher_root / "start-demo.cmd").write_bytes(
        (
            "@echo off\r\n"
            "echo CONTEXT=[%DOCKER_CONTEXT%] HOST=[%DOCKER_HOST%] "
            "CF=[%COMPOSE_FILE%] PROJECT=[%COMPOSE_PROJECT_NAME%] "
            "> \"%START_LOG%\"\r\n"
            "exit /b 19\r\n"
        ).encode("utf-8")
    )
    completed, docker_log = _run_reset(
        tmp_path,
        "RESET DEMO",
        compose_exit=0,
        launcher_root=launcher_root,
        extra_environment={"START_LOG": str(start_log)},
    )

    assert completed.returncode == 1
    assert docker_log.count("--context desktop-linux") == 2
    assert start_log.read_text(encoding="utf-8").strip() == (
        "CONTEXT=[desktop-linux] HOST=[] CF=[] PROJECT=[]"
    )


@pytest.mark.parametrize(
    "confirmation",
    ['"', "'", "&", "RESET DEMO ", "reset demo", 'RESET DEMO" & echo INJECTED'],
)
def test_reset_launcher_rejects_malicious_or_inexact_confirmation_without_docker(
    tmp_path, confirmation
):
    completed, docker_log = _run_reset(tmp_path, confirmation)

    output = (completed.stdout + completed.stderr).decode(errors="replace")
    assert completed.returncode == 1
    assert docker_log == ""
    assert "not recognized as an internal or external command" not in output


def test_reset_launcher_pins_context_compose_file_and_project_despite_environment(
    tmp_path,
):
    completed, docker_log = _run_reset(tmp_path, "RESET DEMO")

    assert completed.returncode == 1
    lines = docker_log.splitlines()
    assert lines[0] == "CF=[] PROJECT=[] HOST=[] ARGS=--context desktop-linux info"
    assert lines[1].startswith(
        "CF=[] PROJECT=[] HOST=[] ARGS=--context desktop-linux compose -f "
    )
    assert "docker-compose.yml" in lines[1]
    assert lines[1].endswith("--project-name daibm-scf-mvp down -v")


def test_reset_launcher_fails_before_compose_down_when_local_context_is_unavailable(
    tmp_path,
):
    completed, docker_log = _run_reset(
        tmp_path, "RESET DEMO", context_available=False
    )

    assert completed.returncode == 1
    assert docker_log.splitlines() == [
        "CF=[] PROJECT=[] HOST=[] ARGS=--context desktop-linux info"
    ]


def test_reset_launcher_rejects_any_other_confirmation_without_cmd_errors(tmp_path):
    completed, docker_log = _run_reset(tmp_path, "NO")

    output = (completed.stdout + completed.stderr).decode(errors="replace")
    assert completed.returncode == 1
    assert docker_log == ""
    assert "RESET DEMO" in output
    assert "ВНИМАНИЕ" in output
    assert "警告" in output
    assert "not recognized as an internal or external command" not in output
    assert "Get-Content" not in output


@pytest.mark.xfail(
    not ACTUAL_DEFENSE_DEPENDENCIES_READY,
    strict=True,
    reason="Task 5 role guide and Task 6 defense documents are not committed yet",
)
def test_actual_repository_assets_satisfy_the_strict_preflight_contract():
    fake_http = FakeHttp(
        html=(ROOT / "app/static/index.html").read_text(encoding="utf-8")
    )

    result = run_preflight(
        "http://127.0.0.1:8010", fake_http.client, project_root=ROOT
    )

    assert result.ok is True, result.errors
