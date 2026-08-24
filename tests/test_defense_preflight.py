from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from scripts.defense_preflight import HttpResponse, run_preflight


ROLE_ACCOUNTS = {
    "supplier.demo": "supplier",
    "core.demo": "core_enterprise",
    "financier.demo": "financier",
    "risk.demo": "risk_manager",
    "auditor.demo": "auditor",
}
ROOT = Path(__file__).parents[1]


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
            return self.server.json_response(
                {
                    "status": "ok",
                    "database": {"backend": "postgresql", "reachable": True},
                    "ledger": {"valid": True},
                    "research_core": {"status": "ready"},
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
    def __init__(self, *, html: str | None = None):
        self.html = html or _ui_html()
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


def test_reset_launcher_rejects_any_other_confirmation_without_cmd_errors():
    completed = subprocess.run(
        ["cmd.exe", "/d", "/c", str(ROOT / "reset-defense-demo.cmd")],
        cwd=ROOT,
        input=b"NO\r\n",
        capture_output=True,
        timeout=10,
    )

    output = (completed.stdout + completed.stderr).decode(errors="replace")
    assert completed.returncode == 1
    assert "RESET DEMO" in output
    assert "ВНИМАНИЕ" in output
    assert "警告" in output
    assert "not recognized as an internal or external command" not in output
    assert "Get-Content" not in output
