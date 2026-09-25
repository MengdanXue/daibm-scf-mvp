from __future__ import annotations

import os

import argparse
import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any, Callable, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, Request, build_opener

# The demo password is deployment configuration, never a literal.
DEMO_PASSWORD = os.environ.get("DAIBM_DEMO_PASSWORD", "")


ROOT = Path(__file__).resolve().parents[1]
ROLE_ACCOUNTS = (
    ("supplier.demo", "supplier"),
    ("core.demo", "core_enterprise"),
    ("financier.demo", "financier"),
    ("risk.demo", "risk_manager"),
    ("auditor.demo", "auditor"),
)
DOCUMENT_PATHS = (
    Path("docs/defense-one-page.md"),
    Path("docs/defense-one-page.pdf"),
    Path("docs/research-brief-en.md"),
    Path("docs/research-brief-en.pdf"),
)


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes


class HttpClient(Protocol):
    def request(
        self,
        method: str,
        url: str,
        payload: dict[str, str] | None = None,
    ) -> HttpResponse: ...


@dataclass(frozen=True)
class PreflightResult:
    ok: bool
    roles: tuple[str, ...]
    checks: tuple[str, ...]
    errors: tuple[str, ...]


class PreflightError(RuntimeError):
    """A failed, named defense-readiness check."""


class UrllibClient:
    """Small stateful client; every instance owns a distinct cookie jar."""

    def __init__(self, *, timeout: float = 5.0):
        self._opener = build_opener(HTTPCookieProcessor(CookieJar()))
        self._timeout = timeout

    def request(
        self,
        method: str,
        url: str,
        payload: dict[str, str] | None = None,
    ) -> HttpResponse:
        data = None
        headers = {"Accept": "application/json, text/html;q=0.9"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(url, data=data, headers=headers, method=method)
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                return HttpResponse(response.status, response.read())
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace").strip()
            raise PreflightError(
                f"{method} {url} returned HTTP {error.code}: {detail or 'no body'}"
            ) from error
        except (URLError, TimeoutError, OSError) as error:
            raise PreflightError(f"{method} {url} failed: {error}") from error


class _LoginPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.html_lang: str | None = None
        self.chinese_control_text: list[str] = []
        self.guide_usernames: list[str] = []
        self.login_inputs: set[str] = set()
        self.login_submit = False
        self._in_login_form = False
        self._in_chinese_control = False

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        values = dict(attrs)
        if tag == "html":
            self.html_lang = values.get("lang")
        if tag == "form" and values.get("id") == "loginForm":
            self._in_login_form = True
        if tag == "button" and values.get("data-lang") == "zh":
            self._in_chinese_control = True
        if tag == "button":
            classes = set((values.get("class") or "").split())
            username = values.get("data-demo-username")
            if "demo-role-guide" in classes and username:
                self.guide_usernames.append(username)
        if self._in_login_form and tag == "input":
            name = values.get("name")
            if name:
                self.login_inputs.add(name)
            if values.get("type", "text").lower() == "submit":
                self.login_submit = True
        if (
            self._in_login_form
            and tag == "button"
            and values.get("type", "submit").lower() == "submit"
        ):
            self.login_submit = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "form" and self._in_login_form:
            self._in_login_form = False
        if tag == "button" and self._in_chinese_control:
            self._in_chinese_control = False

    def handle_data(self, data: str) -> None:
        if self._in_chinese_control:
            self.chinese_control_text.append(data)


def _new_urllib_client() -> UrllibClient:
    return UrllibClient()


def _url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}{path}"


def _response(
    client: HttpClient,
    method: str,
    url: str,
    payload: dict[str, str] | None = None,
) -> HttpResponse:
    try:
        response = client.request(method, url, payload)
    except PreflightError:
        raise
    except Exception as error:
        raise PreflightError(f"{method} {url} failed: {error}") from error
    if not 200 <= response.status < 300:
        raise PreflightError(f"{method} {url} returned HTTP {response.status}")
    return response


def _json_response(
    client: HttpClient,
    method: str,
    url: str,
    payload: dict[str, str] | None = None,
) -> dict[str, Any]:
    response = _response(client, method, url, payload)
    try:
        decoded = json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PreflightError(f"{method} {url} returned invalid JSON") from error
    if not isinstance(decoded, dict):
        raise PreflightError(f"{method} {url} returned a non-object JSON value")
    return decoded


def _expected_artifact_sha256(project_root: Path) -> str:
    manifest_path = project_root / "artifacts/reference/model-manifest.json"
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except OSError as error:
        raise PreflightError(f"cannot read {manifest_path}: {error}") from error
    except json.JSONDecodeError as error:
        raise PreflightError(f"{manifest_path} is not valid JSON") from error
    if not isinstance(payload, dict):
        raise PreflightError(f"{manifest_path} must contain a JSON object")
    artifact_sha256 = payload.get("artifact_sha256")
    if not isinstance(artifact_sha256, str) or re.fullmatch(
        r"[0-9a-f]{64}", artifact_sha256
    ) is None:
        raise PreflightError(
            f"{manifest_path} artifact_sha256 must be 64 lowercase hex characters"
        )
    return artifact_sha256


def _require_health(
    payload: dict[str, Any], expected_artifact_sha256: str
) -> None:
    try:
        runtime_artifact_sha256 = payload["research_core"]["artifact_sha256"]
        healthy = (
            payload["status"] == "ok"
            and payload["database"]["backend"] == "postgresql"
            and payload["database"]["reachable"] is True
            and payload["ledger"]["valid"] is True
            and payload["research_core"]["status"] == "ready"
        )
    except (KeyError, TypeError) as error:
        raise PreflightError(f"health payload is incomplete at {error}") from error
    if not healthy:
        raise PreflightError("health payload is not semantically ready")
    if not isinstance(runtime_artifact_sha256, str) or re.fullmatch(
        r"[0-9a-f]{64}", runtime_artifact_sha256
    ) is None:
        raise PreflightError(
            "research_core.artifact_sha256 must be 64 lowercase hex characters"
        )
    if runtime_artifact_sha256 != expected_artifact_sha256:
        raise PreflightError(
            "research_core.artifact_sha256 does not match "
            "artifacts/reference/model-manifest.json"
        )


def _require_role(payload: dict[str, Any], expected_role: str, check: str) -> None:
    try:
        role = payload["user"]["role"]
    except (KeyError, TypeError) as error:
        raise PreflightError(f"{check} response has no user role") from error
    if role != expected_role:
        raise PreflightError(
            f"{check} returned role {role!r}; expected {expected_role!r}"
        )


def _require_login_page(html: str) -> None:
    parser = _LoginPageParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception as error:
        raise PreflightError(f"login page HTML could not be parsed: {error}") from error
    errors: list[str] = []
    if parser.html_lang != "ru":
        errors.append('<html lang="ru">')
    if "中文" not in "".join(parser.chinese_control_text):
        errors.append("Chinese language control")
    if not {"username", "password"} <= parser.login_inputs or not parser.login_submit:
        errors.append("login form")
    expected_usernames = [username for username, _ in ROLE_ACCOUNTS]
    # The five thesis roles lead the guide in order; operations roles may follow.
    if parser.guide_usernames[: len(expected_usernames)] != expected_usernames:
        errors.append("five-role guide")
    if errors:
        raise PreflightError("login page is missing: " + ", ".join(errors))


def run_preflight(
    base_url: str,
    client_factory: Callable[[], HttpClient] = _new_urllib_client,
    *,
    project_root: Path = ROOT,
) -> PreflightResult:
    """Verify defense readiness without calling any business mutation endpoint."""
    checks: list[str] = []
    errors: list[str] = []
    verified_roles: list[str] = []
    root = Path(project_root)
    public_client = client_factory()

    try:
        expected_artifact_sha256 = _expected_artifact_sha256(root)
        health = _json_response(public_client, "GET", _url(base_url, "/api/health"))
        _require_health(health, expected_artifact_sha256)
        checks.append("health")
    except PreflightError as error:
        errors.append(f"health: {error}")

    for username, role in ROLE_ACCOUNTS:
        client = client_factory()
        primary_error: PreflightError | None = None
        try:
            login = _json_response(
                client,
                "POST",
                _url(base_url, "/api/v1/auth/login"),
                {"username": username, "password": DEMO_PASSWORD},
            )
            _require_role(login, role, f"{role} login")
            session = _json_response(
                client,
                "GET",
                _url(base_url, "/api/v1/auth/session"),
            )
            if session.get("authenticated") is not True:
                raise PreflightError(f"{role} session is not authenticated")
            _require_role(session, role, f"{role} session")
            verified_roles.append(role)
            checks.append(f"role:{role}")
        except PreflightError as error:
            primary_error = error
        finally:
            try:
                _response(
                    client,
                    "POST",
                    _url(base_url, "/api/v1/auth/logout"),
                )
            except PreflightError as logout_error:
                if primary_error is None:
                    primary_error = PreflightError(
                        f"{role} logout cleanup failed: {logout_error}"
                    )
                else:
                    primary_error = PreflightError(
                        f"{primary_error}; logout cleanup failed: {logout_error}"
                    )
        if primary_error is not None:
            errors.append(f"role {role}: {primary_error}")

    try:
        page = _response(public_client, "GET", _url(base_url, "/"))
        try:
            html = page.body.decode("utf-8")
        except UnicodeDecodeError as error:
            raise PreflightError("login page is not valid UTF-8") from error
        _require_login_page(html)
        checks.append("ui")
    except PreflightError as error:
        errors.append(f"ui: {error}")

    for relative_path in DOCUMENT_PATHS:
        path = root / relative_path
        try:
            present = path.is_file() and path.stat().st_size > 0
        except OSError as error:
            errors.append(f"document {relative_path.as_posix()}: {error}")
            continue
        if present:
            checks.append(f"document:{relative_path.as_posix()}")
        else:
            errors.append(
                f"document {relative_path.as_posix()}: file is missing or empty"
            )

    return PreflightResult(
        ok=not errors,
        roles=tuple(verified_roles),
        checks=tuple(checks),
        errors=tuple(errors),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the read-only defense preflight")
    parser.add_argument("--base-url", default="http://127.0.0.1:8010")
    args = parser.parse_args(argv)
    result = run_preflight(args.base_url)
    if result.ok:
        print(
            "[PASS] Defense preflight: health, UI, documents, and roles "
            + ", ".join(result.roles)
        )
        print("[PASS] Read-only endpoint set; zero ledger mutation endpoints called.")
        return 0
    print("[FAIL] Defense preflight did not pass:")
    for error in result.errors:
        print(f"  - {error}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
