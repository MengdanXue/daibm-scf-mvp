from __future__ import annotations

import argparse
import uuid
from pathlib import Path

from playwright.sync_api import sync_playwright


BASE_URL = "http://127.0.0.1:8010"
SCREENSHOT = Path("output/five-role-acceptance.png").resolve()
VIDEO_DIR = Path("output/defense-video").resolve()


def login(page, username: str) -> None:
    page.locator('input[name="username"]').fill(username)
    page.locator('input[name="password"]').fill("Demo123!")
    page.locator("#loginButton").click()
    page.locator("body.authenticated").wait_for()
    page.wait_for_load_state("networkidle")


def logout(page) -> None:
    page.locator("#logoutButton").click()
    page.locator("#authGate").wait_for(state="visible")


def _wait_for_created_application(page, contract_number: str) -> str:
    page.locator(
        "#workflowDetail .detail-top h2",
        has_text=contract_number,
    ).wait_for()
    return page.locator("#workflowDetail .detail-top p").inner_text().strip()


def _select_application(page, application_id: str) -> None:
    application = page.locator(
        f'[data-application-id="{application_id}"]'
    )
    application.wait_for()
    application.click()
    page.locator("#workflowDetail .detail-top p").filter(
        has_text=application_id
    ).wait_for()


def _exercise_primary(page) -> str:
    page.goto(BASE_URL)
    page.wait_for_load_state("networkidle")
    assert page.locator("html").get_attribute("lang") == "ru"

    login(page, "supplier.demo")
    suffix = uuid.uuid4().hex[:10].upper()
    values = {
        "core_enterprise_organization_code": "CORE-001",
        "contract_number": f"SCF-ACCEPTANCE-{suffix}",
        "invoice_number": f"INV-ACCEPTANCE-{suffix}",
        "amount": "1200000",
        "term_days": "90",
        "payment_delay_days": "18",
        "counterparty_risk": "0.58",
        "relationship_months": "18",
        "transactions_last_30d": "12",
    }
    for name, value in values.items():
        field = page.locator(f'#applicationForm [name="{name}"]')
        if name == "core_enterprise_organization_code":
            field.select_option(value)
        else:
            field.fill(value)
    page.locator('#applicationForm [name="invoice_mismatch"]').check()
    page.locator('#applicationForm button[type="submit"]').click()
    application_id = _wait_for_created_application(
        page,
        values["contract_number"],
    )
    trade_text = page.locator(".workflow-evidence").inner_text()
    assert "Проверка дублирования пройдена" in trade_text
    page.locator('[data-workflow-action="submit"]').click()
    page.locator('#workflowDetail [data-status="submitted"]').wait_for()
    logout(page)

    login(page, "core.demo")
    _select_application(page, application_id)
    assert application_id in page.locator("#workflowDetail").inner_text()
    page.locator("#workflowComment").fill(
        "Подтверждено в демонстрационном процессе"
    )
    page.locator('[data-workflow-action="confirm"]').click()
    page.locator('#workflowDetail [data-status="trade_confirmed"]').wait_for()
    logout(page)

    login(page, "financier.demo")
    _select_application(page, application_id)
    assert application_id in page.locator("#workflowDetail").inner_text()
    page.locator('[data-workflow-action="assess"]').click()
    page.locator('#workflowDetail [data-status="risk_assessed"]').wait_for()
    risk_text = page.locator(".workflow-evidence").inner_text()
    assert "transparent_logistic_baseline_v0.1" in risk_text
    assert "TGNN" in risk_text

    page.locator('[data-view-button="research"]').first.click()
    metrics = page.locator("#researchMetricsValue").inner_text()
    assert "TGNN 0.6597" in metrics
    assert "XGBoost 0.5969" in metrics
    assert "2026_REIMPLEMENTATION" in page.locator(
        "#researchMetricsMeta"
    ).inner_text()

    page.evaluate("window.setLanguage('zh')")
    assert "重实现实验指标" in page.locator("#researchMetrics").inner_text()
    assert "业务评分使用透明基线模型" in page.locator(
        "#workflowDetail"
    ).inner_text()
    page.evaluate("window.setLanguage('ru')")
    page.locator('[data-view-button="workflow"]').first.click()
    page.locator("#workflowComment").fill(
        "Риск принят в пределах демонстрационной политики"
    )
    page.locator(
        '[data-workflow-action="decision"][data-decision="approved"]'
    ).click()
    page.locator('#workflowDetail [data-status="approved"]').wait_for()
    logout(page)

    login(page, "risk.demo")
    _select_application(page, application_id)
    assert application_id in page.locator("#workflowDetail").inner_text()
    page.locator("#workflowComment").fill("Назначен стандартный мониторинг")
    page.locator('[data-workflow-action="control"]').click()
    page.locator('#workflowDetail [data-status="controlled"]').wait_for()
    logout(page)

    login(page, "auditor.demo")
    _select_application(page, application_id)
    assert application_id in page.locator("#workflowDetail").inner_text()
    timeline_before_audit = page.locator("#workflowTimeline").inner_text()
    for expected_action in (
        "Создание черновика",
        "Подтверждение сделки",
        "Оценка риска",
        "Финансовое решение",
        "Контрольное действие",
    ):
        assert expected_action in timeline_before_audit
    page.locator("#workflowComment").fill(
        "Цепочка действий и журнал проверены"
    )
    page.locator('[data-workflow-action="audit"]').click()
    page.locator('#workflowDetail [data-status="audited"]').wait_for()
    assert "Аудиторская проверка" in page.locator(
        "#workflowTimeline"
    ).inner_text()

    page.locator('[data-view-button="ledger"]').first.click()
    page.locator("#fabricAnchorPanel").wait_for(state="visible")
    page.locator("#refreshAnchors").click()
    page.locator("#fabricAnchorPanel[aria-busy=\"false\"]").wait_for()
    anchor_total = page.evaluate(
        "[...document.querySelectorAll('#fabricAnchorPanel .anchor-kpis strong')]"
        ".reduce((total, item) => total + Number(item.textContent), 0)"
    )
    assert anchor_total > 0
    assert "Опциональный расширенный режим" in page.locator(
        "#fabricAnchorMode"
    ).inner_text()

    page.evaluate("window.setLanguage('zh')")
    assert "可选高级模式" in page.locator("#fabricAnchorMode").inner_text()
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_function("window.scrollY === 0")
    page.wait_for_timeout(150)
    SCREENSHOT.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SCREENSHOT), full_page=True)
    return application_id


def _exercise_alternate_core_directory(browser) -> None:
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    try:
        page = context.new_page()
        page.route(
            "**/api/v1/organizations/core-enterprises",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body=(
                    '[{"organization_code":"CORE-001","name":"Primary Core"},'
                    '{"organization_code":"CORE-ALT-002","name":"Alternate Core"}]'
                ),
            ),
        )
        page.goto(BASE_URL)
        page.wait_for_load_state("networkidle")
        login(page, "supplier.demo")
        core_select = page.locator(
            'select[name="core_enterprise_organization_code"]'
        )
        core_select.locator("option").nth(1).wait_for(state="attached")
        core_options = core_select.locator("option").all_text_contents()
        assert core_options == [
            "Primary Core · CORE-001",
            "Alternate Core · CORE-ALT-002",
        ], core_options
        core_select.select_option("CORE-ALT-002")
        assert core_select.input_value() == "CORE-ALT-002"
    finally:
        context.close()


def _exercise_failed_core_directory(browser) -> None:
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    try:
        page = context.new_page()
        page.route(
            "**/api/v1/organizations/core-enterprises",
            lambda route: route.fulfill(
                status=503,
                content_type="application/json",
                body=(
                    '{"detail":{"code":"core_directory_unavailable",'
                    '"message":"Core enterprise directory unavailable"}}'
                ),
            ),
        )
        page.goto(BASE_URL)
        page.wait_for_load_state("networkidle")
        page.locator('input[name="username"]').fill("supplier.demo")
        page.locator('input[name="password"]').fill("Demo123!")
        page.locator("#loginButton").click()
        page.locator("#loginError").filter(
            has_text="Не удалось войти"
        ).wait_for()
        assert page.locator("body.authenticated").count() == 0
        session = page.evaluate(
            "fetch('/api/v1/auth/session').then(response => response.json())"
        )
        assert session["authenticated"] is False
    finally:
        context.close()


def run_acceptance(record_video=False) -> tuple[Path, Path | None]:
    video_path: Path | None = None
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            context_options: dict[str, object] = {
                "viewport": {"width": 1440, "height": 1100}
            }
            if record_video:
                VIDEO_DIR.mkdir(parents=True, exist_ok=True)
                context_options["record_video_dir"] = str(VIDEO_DIR)
            primary_context = browser.new_context(**context_options)
            try:
                page = primary_context.new_page()
                browser_messages: list[str] = []
                page.on(
                    "console",
                    lambda message: browser_messages.append(
                        f"console:{message.type}:{message.text}"
                    )
                    if message.type in {"error", "warning"}
                    else None,
                )
                page.on(
                    "pageerror",
                    lambda error: browser_messages.append(f"pageerror:{error}"),
                )
                _exercise_primary(page)
                assert browser_messages == [], browser_messages
                video = page.video if record_video else None
            finally:
                primary_context.close()
            if video is not None:
                video_path = Path(video.path()).resolve()

            _exercise_alternate_core_directory(browser)
            _exercise_failed_core_directory(browser)
        finally:
            browser.close()
    return SCREENSHOT, video_path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the five-role browser acceptance journey."
    )
    parser.add_argument(
        "--record-video",
        action="store_true",
        help="Record the primary defense journey to output/defense-video.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    arguments = _parse_args()
    screenshot_path, optional_video_path = run_acceptance(
        record_video=arguments.record_video
    )
    print(
        "browser_acceptance=passed "
        f"screenshot={screenshot_path} "
        f"video={optional_video_path or 'not-recorded'}"
    )
