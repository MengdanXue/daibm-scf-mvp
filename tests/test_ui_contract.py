from pathlib import Path


HTML_PATH = Path(__file__).parents[1] / "app" / "static" / "index.html"
WORKFLOW_JS_PATH = HTML_PATH.with_name("workflow.js")


def _html() -> str:
    return HTML_PATH.read_text(encoding="utf-8")


def test_research_page_exposes_complete_evidence_chain_controls():
    html = _html()

    assert '<html lang="ru">' in html
    assert 'data-view-button="research"' in html
    for element_id in (
        "view-research",
        "researchStatus",
        "researchEnterprise",
        "researchInfer",
        "researchInject",
        "researchReset",
        "researchTraceRail",
        "researchDataset",
        "researchGraph",
        "researchConnections",
        "researchModel",
        "researchPolicy",
        "researchLedger",
    ):
        assert f'id="{element_id}"' in html
    assert 'aria-live="polite"' in html
    assert "REAL MODEL INFERENCE" in html
    assert "SYNTHETIC DATA" in html


def test_research_page_has_russian_and_chinese_copy_for_every_new_action():
    html = _html()

    for key in (
        "navResearch",
        "researchTitle",
        "researchSubtitle",
        "researchInfer",
        "researchInject",
        "researchReset",
        "researchDataset",
        "researchGraph",
        "researchModel",
        "researchPolicy",
        "researchLedger",
        "researchUnavailable",
        "researchEmpty",
        "researchBefore",
        "researchAfter",
        "researchChangedInputs",
    ):
        assert html.count(f"{key}:") == 2


def test_research_javascript_uses_real_apis_and_non_silent_recovery():
    html = _html()

    assert "'/api/research/status'" in html
    assert "'/api/research/inference'" in html
    assert "'/api/research/scenarios/'" in html
    assert "'/inject-risk'" in html
    assert "'/api/research/scenarios/reset'" in html
    assert "'/api/demo/recover'" in html
    assert "integrity_incident_id" in html
    assert "risk_score" not in html.split("async function injectResearchRisk", 1)[1].split("}", 1)[0]


def test_postgresql_ledger_is_not_presented_as_blockchain():
    html = _html().lower()

    forbidden = (
        "postgresql blockchain",
        "блокчейн postgresql",
        "postgresql 区块链",
    )
    assert all(phrase not in html for phrase in forbidden)
    assert "tamper-evident" in html


def test_financing_creation_is_the_primary_defense_journey():
    html = _html()

    assert 'id="beginFinancing"' in html
    assert 'onclick="beginFinancingJourney()"' in html
    assert 'data-i18n="createFinancing"' in html
    assert 'id="loadPresetCases"' in html
    assert 'onclick="startDemo()"' in html


def test_financing_journey_exposes_five_bilingual_business_stages():
    html = _html()

    assert 'id="financingLifecycle"' in html
    for index, stage in enumerate(
        ("application", "assessment", "decision", "control", "audit"), start=1
    ):
        assert f'data-journey-stage="{stage}"' in html
        assert f'data-step="0{index}"' in html
        assert html.count(f"journey{stage.title()}:") == 2


def test_created_financing_links_to_audit_and_research_evidence():
    html = _html()

    assert "setJourneyState('complete')" in html
    assert 'data-result-action="ledger"' in html
    assert 'data-result-action="research"' in html
    assert html.count("viewAuditEvidence:") == 2
    assert html.count("viewResearchEvidence:") == 2


def test_ui_exposes_real_login_logout_and_five_role_workbench():
    html = _html()

    assert 'id="authGate"' in html
    assert 'id="loginForm"' in html
    assert 'id="demoAccounts"' in html
    assert 'id="logoutButton"' in html
    assert 'id="view-workflow"' in html
    assert 'data-view-button="workflow"' in html
    assert 'id="workflowApplications"' in html
    assert 'id="workflowDetail"' in html
    assert 'id="workflowTimeline"' in html


def test_supplier_workbench_has_complete_application_fields():
    html = _html()

    assert 'id="applicationForm"' in html
    for field in (
        "core_enterprise_organization_code",
        "contract_number",
        "invoice_number",
        "amount",
        "term_days",
        "payment_delay_days",
        "counterparty_risk",
        "invoice_mismatch",
        "relationship_months",
        "transactions_last_30d",
    ):
        assert f'name="{field}"' in html


def test_workflow_javascript_uses_authenticated_versioned_apis():
    javascript = WORKFLOW_JS_PATH.read_text(encoding="utf-8")

    for path in (
        "/api/v1/auth/session",
        "/api/v1/auth/login",
        "/api/v1/auth/logout",
        "/api/v1/dashboard",
        "/api/v1/tasks",
        "/api/v1/applications",
        "/submit",
        "/trade-confirmation",
        "/risk-assessment",
        "/decision",
        "/control-action",
        "/audit-review",
    ):
        assert path in javascript
    assert "allowed_actions" in javascript
    assert "Demo123!" in javascript


def test_workflow_translates_created_draft_and_rerenders_user_on_language_change():
    javascript = WORKFLOW_JS_PATH.read_text(encoding="utf-8")

    assert javascript.count("create_draft:") == 2
    render_workbench = javascript.split("function renderWorkbench()", 1)[1]
    assert "renderCurrentUser()" in render_workbench.split(
        "function renderCustodyRail()", 1
    )[0]
