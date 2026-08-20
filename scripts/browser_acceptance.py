from __future__ import annotations

import uuid
from pathlib import Path

from playwright.sync_api import sync_playwright


BASE_URL = "http://127.0.0.1:8010"
SCREENSHOT = Path("output/five-role-acceptance.png").resolve()


def login(page, username: str) -> None:
    page.locator('input[name="username"]').fill(username)
    page.locator('input[name="password"]').fill("Demo123!")
    page.locator("#loginButton").click()
    page.locator("body.authenticated").wait_for()
    page.wait_for_load_state("networkidle")


def logout(page) -> None:
    page.locator("#logoutButton").click()
    page.locator("#authGate").wait_for(state="visible")


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 1100})
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
    page.locator('[data-workflow-action="submit"]').wait_for()
    application_id = page.locator(
        "#workflowDetail .detail-top p"
    ).inner_text()
    trade_text = page.locator(".workflow-evidence").inner_text()
    assert "Проверка дублирования пройдена" in trade_text
    page.locator('[data-workflow-action="submit"]').click()
    page.locator('#workflowDetail [data-status="submitted"]').wait_for()
    logout(page)

    login(page, "core.demo")
    assert application_id in page.locator("#workflowDetail").inner_text()
    page.locator("#workflowComment").fill(
        "Подтверждено в демонстрационном процессе"
    )
    page.locator('[data-workflow-action="confirm"]').click()
    page.locator(
        '#workflowDetail [data-status="trade_confirmed"]'
    ).wait_for()
    logout(page)

    login(page, "financier.demo")
    assert application_id in page.locator("#workflowDetail").inner_text()
    page.locator('[data-workflow-action="assess"]').click()
    page.locator(
        '#workflowDetail [data-status="risk_assessed"]'
    ).wait_for()
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
    assert application_id in page.locator("#workflowDetail").inner_text()
    page.locator("#workflowComment").fill(
        "Назначен стандартный мониторинг"
    )
    page.locator('[data-workflow-action="control"]').click()
    page.locator('#workflowDetail [data-status="controlled"]').wait_for()
    logout(page)

    login(page, "auditor.demo")
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

    page.evaluate("window.setLanguage('zh')")
    assert "审计已完成" in page.locator("#workflowDetail").inner_text()
    SCREENSHOT.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SCREENSHOT), full_page=True)

    assert browser_messages == [], browser_messages

    alternate_context = browser.new_context(
        viewport={"width": 1280, "height": 900}
    )
    alternate = alternate_context.new_page()
    alternate.route(
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
    alternate.goto(BASE_URL)
    alternate.wait_for_load_state("networkidle")
    login(alternate, "supplier.demo")
    core_select = alternate.locator(
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
    alternate_context.close()

    failing_context = browser.new_context(
        viewport={"width": 1280, "height": 900}
    )
    failing = failing_context.new_page()
    failing.route(
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
    failing.goto(BASE_URL)
    failing.wait_for_load_state("networkidle")
    failing.locator('input[name="username"]').fill("supplier.demo")
    failing.locator('input[name="password"]').fill("Demo123!")
    failing.locator("#loginButton").click()
    failing.locator("#loginError").filter(has_text="Не удалось войти").wait_for()
    assert failing.locator("body.authenticated").count() == 0
    session = failing.evaluate(
        "fetch('/api/v1/auth/session').then(response => response.json())"
    )
    assert session["authenticated"] is False
    failing_context.close()

    browser.close()

print(f"browser_acceptance=passed screenshot={SCREENSHOT}")
