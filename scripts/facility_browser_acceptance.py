from __future__ import annotations

import argparse
import uuid
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any


DEFAULT_BASE_URL = "http://127.0.0.1:8010"
DEFAULT_SCREENSHOT = Path("output/facility-lifecycle-acceptance.png")
PASSWORD = "Demo123!"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Drive an approved audited application through the controlled "
            "financing lifecycle in a real browser."
        ),
        formatter_class=lambda prog: argparse.HelpFormatter(
            prog, width=120
        ),
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument(
        "--screenshot",
        type=Path,
        default=DEFAULT_SCREENSHOT,
        help="Final Chinese screenshot (default: facility-lifecycle-acceptance.png)",
    )
    parser.add_argument("--timeout-ms", type=int, default=15_000)
    return parser


def _login(page: Any, username: str) -> None:
    page.goto("/")
    page.wait_for_load_state("networkidle")
    page.locator('input[name="username"]').fill(username)
    page.locator('input[name="password"]').fill(PASSWORD)
    page.locator("#loginButton").click()
    page.locator("body.authenticated").wait_for()
    page.wait_for_load_state("networkidle")


def _fetch_json(page: Any, path: str) -> Any:
    return page.evaluate(
        """async (path) => {
            const response = await fetch(path);
            const payload = await response.json();
            if (!response.ok) {
                throw new Error(`${response.status} ${JSON.stringify(payload)}`);
            }
            return payload;
        }""",
        path,
    )


def _open_facility(page: Any, facility_id: str) -> None:
    page.locator('[data-view-button="facilities"]').first.click()
    page.locator("#view-facilities.active").wait_for()
    page.wait_for_function(
        "!document.querySelector('#view-facilities').classList.contains('facility-pending')"
    )
    page.locator("#refreshFacilities").click()
    item = page.locator(f'[data-facility-id="{facility_id}"]')
    item.wait_for()
    item.click()


def _money_from_cents(cents: int) -> str:
    return f"{cents // 100}.{cents % 100:02d}"


def _facility_plan(application: dict[str, Any]) -> dict[str, Any]:
    principal = Decimal(str(application["amount"])).quantize(Decimal("0.01"))
    principal_cents = int(principal * 100)
    first_cents = principal_cents // 2
    second_cents = principal_cents - first_cents
    assert first_cents > 0 and first_cents + second_cents == principal_cents
    return {
        "currency": "CNY",
        "principal": _money_from_cents(principal_cents),
        "installments": (
            _money_from_cents(first_cents),
            _money_from_cents(second_cents),
        ),
    }


def _select_application(page: Any) -> tuple[dict[str, Any], set[str]]:
    applications = _fetch_json(page, "/api/v1/applications?limit=200")
    facilities = _fetch_json(page, "/api/v1/facilities?limit=200")
    used_requests = {item["request_id"] for item in facilities}
    candidates = [
        item
        for item in applications
        if item["status"] == "audited"
        and item["decision"] == "approved"
        and item["request_id"] not in used_requests
    ]
    if not candidates:
        raise AssertionError(
            "No unused approved audited application is available. Run "
            "scripts/browser_acceptance.py first to create one."
        )
    candidates.sort(key=lambda item: item["updated_at"], reverse=True)
    return candidates[0], used_requests


def _wait_for_status(page: Any, status: str) -> None:
    page.locator(f'#facilityDetail [data-status="{status}"]').first.wait_for()
    page.wait_for_function(
        "!document.querySelector('#view-facilities').classList.contains('facility-pending')"
    )


def _expected_no_active_deployment(response: Any) -> bool:
    return (
        response.status == 404
        and "/api/v1/calibration-deployments/active" in response.url
    )


def run(base_url: str, screenshot: Path, timeout_ms: int) -> Path:
    from playwright.sync_api import sync_playwright

    screenshot = screenshot.resolve()
    browser_messages: list[str] = []
    http_errors: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        contexts = {
            role: browser.new_context(
                base_url=base_url,
                viewport={"width": 1440, "height": 1100},
            )
            for role in ("financier", "supplier", "risk", "auditor")
        }
        pages = {role: context.new_page() for role, context in contexts.items()}
        for page in pages.values():
            page.set_default_timeout(timeout_ms)
            page.on(
                "console",
                lambda message: browser_messages.append(
                    f"console:{message.type}:{message.text}"
                )
                if message.type in {"error", "warning"}
                and not (
                    message.type == "error"
                    and message.text
                    == "Failed to load resource: the server responded with a status of 404 (Not Found)"
                )
                else None,
            )
            page.on(
                "pageerror",
                lambda error: browser_messages.append(f"pageerror:{error}"),
            )
            page.on(
                "response",
                lambda response: http_errors.append(
                    f"{response.status} {response.url}"
                )
                if response.status >= 400
                and not _expected_no_active_deployment(response)
                else None,
            )

        try:
            _login(pages["financier"], "financier.demo")
            _login(pages["supplier"], "supplier.demo")
            _login(pages["risk"], "risk.demo")
            _login(pages["auditor"], "auditor.demo")

            financier = pages["financier"]
            application, _ = _select_application(financier)
            facility_plan = _facility_plan(application)
            principal_text = facility_plan["principal"]
            first_amount, second_amount = facility_plan["installments"]

            financier.locator('[data-view-button="facilities"]').first.click()
            form = financier.locator("#facilityCreateForm")
            form.locator('[name="request_id"]').fill(application["request_id"])
            form.locator('[name="principal"]').fill(principal_text)
            form.locator('[name="currency"]').select_option(
                facility_plan["currency"]
            )
            due_one = date.today() + timedelta(days=30)
            due_two = date.today() + timedelta(days=60)
            form.locator('[name="due_date_1"]').fill(due_one.isoformat())
            form.locator('[name="amount_1"]').fill(first_amount)
            form.locator('[name="due_date_2"]').fill(due_two.isoformat())
            form.locator('[name="amount_2"]').fill(second_amount)
            form.locator('button[type="submit"]').click()
            _wait_for_status(financier, "ready_for_disbursement")

            created = next(
                item
                for item in _fetch_json(
                    financier, "/api/v1/facilities?limit=200"
                )
                if item["request_id"] == application["request_id"]
            )
            assert created["request_id"] == application["request_id"]
            assert created["principal"] == principal_text
            assert created["currency"] == "CNY"
            assert [item["amount"] for item in created["installments"]] == [
                first_amount,
                second_amount,
            ]
            facility_id = created["facility_id"]

            financier.locator(
                '[data-facility-action="initiate_disbursement"]'
            ).click()
            _wait_for_status(financier, "disbursed")
            financier.locator(
                '[data-facility-action="confirm_disbursement"]'
            ).click()
            _wait_for_status(financier, "active")

            # Financier owns the overdue action; risk manager can review the
            # active facility but must not receive the mutation control.
            _open_facility(financier, facility_id)
            _wait_for_status(financier, "active")
            assert financier.locator(
                '[data-facility-action="mark_overdue"]'
            ).count() == 1
            risk = pages["risk"]
            _open_facility(risk, facility_id)
            _wait_for_status(risk, "active")
            assert risk.locator(
                '[data-facility-action="mark_overdue"]'
            ).count() == 0

            supplier = pages["supplier"]
            payment_ids: list[str] = []
            for index, amount in enumerate((first_amount, second_amount), start=1):
                _open_facility(supplier, facility_id)
                current = _fetch_json(
                    supplier, f"/api/v1/facilities/{facility_id}"
                )
                installment = current["installments"][index - 1]
                reference = f"ACCEPT-{uuid.uuid4().hex[:12].upper()}-{index}"
                payment_form = supplier.locator(
                    '[data-facility-action-form="submit_payment"]'
                )
                payment_form.locator('[name="installment_id"]').select_option(
                    installment["installment_id"]
                )
                payment_form.locator('[name="amount"]').fill(amount)
                payment_form.locator('[name="payment_reference"]').fill(reference)
                payment_form.locator(
                    '[data-facility-action="submit_payment"]'
                ).click()
                supplier.locator("#facilityPayments", has_text=reference).wait_for()
                supplier.wait_for_function(
                    "!document.querySelector('#view-facilities').classList.contains('facility-pending')"
                )

                submitted = _fetch_json(
                    supplier, f"/api/v1/facilities/{facility_id}"
                )
                payment = next(
                    item
                    for item in submitted["payments"]
                    if item["payment_reference"] == reference
                )
                assert payment["status"] == "submitted"
                payment_ids.append(payment["payment_id"])

                _open_facility(financier, facility_id)
                decision_form = financier.locator(
                    '[data-facility-action-form="confirm_payment"]'
                )
                decision_form.locator('[name="payment_id"]').select_option(
                    payment["payment_id"]
                )
                decision_form.locator('[name="comment"]').fill(
                    f"Acceptance repayment {index} confirmed"
                )
                decision_form.locator(
                    '[data-facility-action="confirm_payment"]'
                ).click()
                expected_status = "repaid" if index == 2 else "active"
                _wait_for_status(financier, expected_status)

            auditor = pages["auditor"]
            _open_facility(auditor, facility_id)
            _wait_for_status(auditor, "repaid")
            auditor.locator('[data-facility-action="close"]').click()
            _wait_for_status(auditor, "closed")

            final = _fetch_json(auditor, f"/api/v1/facilities/{facility_id}")
            assert final["status"] == "closed"
            assert final["outstanding_amount"] == "0.00"
            assert len(final["installments"]) == 2
            assert all(item["status"] == "paid" for item in final["installments"])
            assert [item["payment_id"] for item in final["payments"]] == payment_ids
            assert all(item["status"] == "confirmed" for item in final["payments"])

            ledger = _fetch_json(auditor, "/api/ledger?limit=500")
            facility_events = [
                item["event_type"]
                for item in reversed(ledger)
                if item["entity_id"] == facility_id
            ]
            assert facility_events == [
                "FACILITY_CREATED",
                "DISBURSEMENT_INITIATED",
                "DISBURSEMENT_CONFIRMED",
                "REPAYMENT_SUBMITTED",
                "REPAYMENT_CONFIRMED",
                "REPAYMENT_SUBMITTED",
                "REPAYMENT_CONFIRMED",
                "FACILITY_REPAID",
                "FACILITY_CLOSED",
            ]
            verification = _fetch_json(auditor, "/api/ledger/verify")
            assert verification["valid"] is True

            auditor.evaluate("window.setLanguage('zh')")
            assert auditor.locator("html").get_attribute("lang") == "zh-CN"
            assert "已关闭" in auditor.locator("#facilityDetail").inner_text()
            assert "这是受控融资生命周期模拟" in auditor.locator(
                ".facility-evidence-boundary"
            ).inner_text()
            screenshot.parent.mkdir(parents=True, exist_ok=True)
            auditor.screenshot(path=str(screenshot), full_page=True)
            assert browser_messages == [], browser_messages
            assert http_errors == [], http_errors
        finally:
            for context in contexts.values():
                context.close()
            browser.close()

    return screenshot


def main() -> int:
    arguments = _parser().parse_args()
    screenshot = run(
        arguments.base_url.rstrip("/"),
        arguments.screenshot,
        arguments.timeout_ms,
    )
    print(f"facility_browser_acceptance=passed screenshot={screenshot}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
