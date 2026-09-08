from __future__ import annotations

import uuid
from pathlib import Path
import argparse

from playwright.sync_api import Route, sync_playwright

try:
    from scripts.browser_acceptance import (
        _exercise_primary,
        login,
        logout,
    )
except ModuleNotFoundError:  # Direct `python scripts\...py` execution.
    from browser_acceptance import _exercise_primary, login, logout


SCREENSHOT = Path("output/outcome-feedback-acceptance.png").resolve()


def _wait_facility_idle(page) -> None:
    page.locator("#view-facilities:not(.facility-pending)").wait_for()


def _open_facilities(page) -> None:
    page.locator('[data-view-button="facilities"]:visible').click()
    page.locator("#facilityList").wait_for(state="visible")


def _select_facility(page, facility_id: str) -> None:
    item = page.locator(f'button[data-facility-id="{facility_id}"]')
    item.wait_for(state="visible")
    item.click()
    page.locator("#facilityDetail .facility-detail-head p").filter(
        has_text=facility_id
    ).wait_for()


def _switch_role(page, username: str, facility_id: str) -> None:
    logout(page)
    login(page, username)
    _open_facilities(page)
    _select_facility(page, facility_id)


def _submit_payment(page, amount: str, reference: str) -> None:
    form = page.locator('[data-facility-action-form="submit_payment"]')
    form.wait_for(state="visible")
    form.locator('input[name="amount"]').fill(amount)
    form.locator('input[name="payment_reference"]').fill(reference)
    form.locator('[data-facility-action="submit_payment"]').click()
    page.locator("#facilityPayments").filter(has_text=reference).wait_for()
    _wait_facility_idle(page)


def _confirm_payment(page, reference: str) -> None:
    form = page.locator('[data-facility-action-form="confirm_payment"]')
    form.wait_for(state="visible")
    form.locator('input[name="comment"]').fill(
        f"Verified controlled payment {reference}"
    )
    form.locator('[data-facility-action="confirm_payment"]').click()
    page.locator("#facilityPayments .facility-payment-row", has_text=reference).locator(
        '[data-status="confirmed"]'
    ).wait_for()
    _wait_facility_idle(page)


def run_outcome_acceptance(
    base_url: str = "http://127.0.0.1:8017", *, browser_channel: str | None = None
) -> tuple[str, Path]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, channel=browser_channel)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 1100})
            errors: list[str] = []
            job_poll_count = 0
            page.on("pageerror", lambda error: errors.append(str(error)))
            def count_job_poll(request) -> None:
                nonlocal job_poll_count
                if "/api/v1/calibration-jobs/" in request.url:
                    job_poll_count += 1
            page.on("request", count_job_poll)
            application_id = _exercise_primary(page) if base_url == "http://127.0.0.1:8010" else _exercise_primary(page, base_url)
            prior_outcomes = page.evaluate(
                "fetch('/api/v1/outcomes?limit=50').then(response => response.json())"
            )
            expected_sample_count = len(prior_outcomes) + 1
            expected_positive_count = sum(
                1 for outcome in prior_outcomes if outcome["defaulted"]
            )
            expected_negative_count = expected_sample_count - expected_positive_count
            expected_status = (
                "eligible_candidate"
                if expected_sample_count >= 20
                and expected_positive_count >= 5
                and expected_negative_count >= 5
                else "exploratory_candidate"
            )

            page.evaluate("window.setLanguage('ru')")
            logout(page)
            login(page, "financier.demo")
            _open_facilities(page)
            create = page.locator("#facilityCreateForm")
            values = {
                "request_id": application_id,
                "principal": "1200000.00",
                "due_date_1": "2027-10-01",
                "amount_1": "600000.00",
                "due_date_2": "2027-11-01",
                "amount_2": "600000.00",
            }
            for name, value in values.items():
                create.locator(f'[name="{name}"]').fill(value)
            create.locator('select[name="currency"]').select_option("CNY")
            create.locator('button[type="submit"]').click()
            page.locator("#facilityDetail .facility-detail-head p").wait_for()
            _wait_facility_idle(page)
            facility_id = page.locator(
                "#facilityDetail .facility-detail-head p"
            ).inner_text().strip()

            page.locator('[data-facility-action="initiate_disbursement"]').click()
            page.locator('#facilityDetail [data-status="disbursed"]').wait_for()
            _wait_facility_idle(page)
            page.locator('[data-facility-action="confirm_disbursement"]').click()
            page.locator('#facilityDetail [data-status="active"]').wait_for()
            _wait_facility_idle(page)

            first_reference = f"OUTCOME-PAY-1-{uuid.uuid4().hex[:8]}"
            _switch_role(page, "supplier.demo", facility_id)
            _submit_payment(page, "600000.00", first_reference)
            _switch_role(page, "financier.demo", facility_id)
            _confirm_payment(page, first_reference)

            second_reference = f"OUTCOME-PAY-2-{uuid.uuid4().hex[:8]}"
            _switch_role(page, "supplier.demo", facility_id)
            _submit_payment(page, "600000.00", second_reference)
            _switch_role(page, "financier.demo", facility_id)
            _confirm_payment(page, second_reference)
            page.locator('#facilityDetail [data-status="repaid"]').wait_for()
            _wait_facility_idle(page)

            _switch_role(page, "auditor.demo", facility_id)
            page.locator('[data-facility-action="close"]').click()
            page.locator('#facilityDetail [data-status="closed"]').wait_for()
            _wait_facility_idle(page)
            form = page.locator("#actualOutcomeForm")
            form.wait_for(state="visible")

            captured_payloads: list[dict[str, object]] = []

            def retry_once(route: Route) -> None:
                captured_payloads.append(route.request.post_data_json)
                if len(captured_payloads) == 1:
                    route.fulfill(status=503, content_type="application/json", body='{"detail":{"code":"temporary_unavailable","message":"Retry the same outcome"}}')
                else:
                    route.continue_()

            page.route(
                f"**/api/v1/facilities/{facility_id}/actual-outcome",
                retry_once,
            )
            evidence_reference = f"controlled-demo://closure/{facility_id}"
            form.locator('input[name="evidence_reference"]').fill(
                evidence_reference
            )
            form.locator('select[name="provenance"]').select_option(
                "CONTROLLED_DEMO"
            )
            form.locator('button[type="submit"]').click()
            page.wait_for_function(
                "document.querySelector('#outcomeSubmitError')?.textContent.trim().length > 0"
            )
            assert page.locator("#outcomeSubmitError").inner_text().strip()
            page.locator('#actualOutcomePanel[aria-busy="false"]').wait_for()
            form.locator('button[type="submit"]').click()
            page.locator("#actualOutcomePanel.outcome-recorded").wait_for()
            page.unroute(
                f"**/api/v1/facilities/{facility_id}/actual-outcome",
                retry_once,
            )

            first_payload, retry_payload = captured_payloads
            assert first_payload == retry_payload
            assert len(first_payload["evidence_sha256"]) == 64
            assert evidence_reference not in str(first_payload)
            assert first_payload["observed_at"].endswith("Z")
            assert job_poll_count >= 1

            lineage = page.locator("#outcomeLineage").inner_text().lower()
            candidate = page.locator("#calibrationCandidate").inner_text().lower()
            assert "outcome" in lineage and "assessment" in lineage and "model" in lineage
            assert "risk engine" in lineage
            assert "transparent_logistic_baseline_v0.1" in lineage
            assert "статус внедрения" in candidate
            assert any(status in candidate for status in ("eligible_candidate", "exploratory_candidate", "failed", "no run", "training_not_eligible"))
            if expected_status in candidate:
                assert f"n={expected_sample_count}" in candidate
                assert f"+{expected_positive_count} / −{expected_negative_count}" in candidate
            else:
                # A durable job may fail its temporal partition gate on a
                # small controlled fixture; the UI must still expose the
                # diagnostic fields rather than pretending activation.
                assert "failed" in candidate or "no run" in candidate
            assert "brier" in candidate and "log loss" in candidate
            assert "verified" in candidate or "not_applicable" in candidate

            page.evaluate("window.setLanguage('zh')")
            assert "部署状态" in page.locator("#calibrationCandidate").inner_text()
            assert "不重训 TGNN" in page.locator("#actualOutcomePanel").inner_text()
            page.set_viewport_size({"width": 390, "height": 844})
            panel_box = page.locator("#actualOutcomePanel").bounding_box()
            assert panel_box is not None
            assert panel_box["x"] >= 0 and panel_box["width"] <= 390

            SCREENSHOT.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(SCREENSHOT), full_page=True)

            def expose_failed_run(route: Route) -> None:
                response = route.fetch()
                runs = response.json()
                for run in runs:
                    run["status"] = "failed"
                    run["artifact_integrity"] = "failed"
                route.fulfill(status=200, json=runs)

            runs_pattern = "**/api/v1/calibration-runs?limit=50"
            page.route(runs_pattern, expose_failed_run)
            page.locator("#refreshFacilities").click()
            failed_badge = page.locator("#actualOutcomePanel .candidate-status.failed")
            failed_badge.wait_for()
            assert "failed" in failed_badge.inner_text().lower()
            assert "exploratory" not in failed_badge.inner_text().lower()
            assert "failed" in page.locator(
                "#calibrationCandidate"
            ).inner_text().lower()
            # Let the intercepted refresh complete before removing the route;
            # otherwise Playwright may report a late handler after browser close.
            page.wait_for_timeout(250)
            page.unroute(runs_pattern, expose_failed_run)
            assert errors == [], errors
            return facility_id, SCREENSHOT
        finally:
            browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run isolated outcome browser acceptance")
    parser.add_argument("--base-url", default="http://127.0.0.1:8017")
    parser.add_argument("--browser-channel", default=None)
    args = parser.parse_args()
    accepted_facility, screenshot = run_outcome_acceptance(
        args.base_url, browser_channel=args.browser_channel
    )
    print(
        "outcome_browser_acceptance=passed "
        f"facility={accepted_facility} screenshot={screenshot}"
    )
