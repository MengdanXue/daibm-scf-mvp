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
        "researchMetrics",
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
        "researchRerunMetrics",
        "researchMetricBoundary",
    ):
        assert html.count(f"{key}:") == 2


def test_research_page_renders_reimplementation_metrics_with_provenance():
    html = _html()

    for element_id in (
        "researchMetrics",
        "researchMetricsValue",
        "researchMetricsMeta",
    ):
        assert f'id="{element_id}"' in html
    assert "model.metrics?.tgnn?.roc_auc" in html
    assert "model.metrics?.xgboost?.roc_auc" in html
    assert "2026_REIMPLEMENTATION" in html


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


def test_auditor_ledger_exposes_accessible_bilingual_fabric_anchor_controls():
    html = _html()
    javascript = WORKFLOW_JS_PATH.read_text(encoding="utf-8")

    for element_id in (
        "fabricAnchorPanel",
        "fabricAnchorMode",
        "anchorPendingCount",
        "anchorRetryCount",
        "anchorAnchoredCount",
        "anchorFailedCount",
        "refreshAnchors",
        "dispatchAnchors",
        "anchorDispatchSummary",
        "anchorList",
    ):
        assert f'id="{element_id}"' in html
    assert 'aria-labelledby="fabricAnchorTitle"' in html
    assert 'data-wf-i18n-aria-label="anchorCountsLabel"' in html
    assert html.count('aria-live="polite"') >= 3
    for key in (
        "fabricAnchorTitle",
        "fabricAnchorOptional",
        "anchorPending",
        "anchorRetry",
        "anchorAnchored",
        "anchorPermanentFailed",
        "anchorRefresh",
        "anchorDispatch",
        "anchorRetryFailed",
        "anchorCountsLabel",
    ):
        assert javascript.count(f"{key}:") == 2
    assert "data-wf-i18n-aria-label" in javascript


def test_fabric_anchor_controls_use_real_auditor_apis_without_fake_success():
    javascript = WORKFLOW_JS_PATH.read_text(encoding="utf-8")

    assert 'wfApi("/api/v1/anchors?limit=50")' in javascript
    assert 'wfApi("/api/v1/anchor-dispatches"' in javascript
    assert 'wfApi(`/api/v1/anchors/${anchorId}/retry`' in javascript
    assert 'anchor.status === "permanent_failed"' in javascript
    assert 'summary.anchored' in javascript
    assert "fabricAnchorOptional" in javascript


def test_ui_scientific_boundary_distinguishes_optional_fabric_from_postgresql():
    html = _html()

    assert "локальную опциональную привязку хешей к Hyperledger Fabric" in html
    assert "可选的本地 Hyperledger Fabric 哈希锚定" in html
    assert "получение консенсуса промышленного уровня" in html
    assert "生产级共识" in html


def test_workflow_translates_created_draft_and_rerenders_user_on_language_change():
    javascript = WORKFLOW_JS_PATH.read_text(encoding="utf-8")

    assert javascript.count("create_draft:") == 2
    render_workbench = javascript.split("function renderWorkbench()", 1)[1]
    assert "renderCurrentUser()" in render_workbench.split(
        "function renderCustodyRail()", 1
    )[0]


def test_workflow_detail_exposes_trade_and_business_risk_evidence():
    javascript = WORKFLOW_JS_PATH.read_text(encoding="utf-8")

    assert "application.trade_evidence" in javascript
    assert "application.risk_evidence" in javascript
    assert javascript.count("tradeEvidence:") == 2
    assert javascript.count("businessRiskEvidence:") == 2
    assert javascript.count("duplicateCheckPassed:") == 2
    assert javascript.count("researchComparisonBoundary:") == 2


def test_legacy_risk_overview_ignores_unscored_workflow_records():
    html = _html()

    assert "const isScoredRequest" in html
    assert "items.filter(isScoredRequest)" in html
    assert "requestsState.filter(isScoredRequest)" in html
    assert "const decisionTotal=approved+review+rejected" in html
    assert "approved/decisionTotal" in html


def test_research_metrics_card_degrades_when_comparison_is_unavailable():
    html = _html()

    assert "model.metrics?.tgnn?.roc_auc" in html
    assert "model.metrics?.xgboost?.roc_auc" in html
    assert "formatResearchMetric" in html


def test_facility_tab_exposes_bilingual_lifecycle_workbench():
    html = _html()
    javascript = WORKFLOW_JS_PATH.read_text(encoding="utf-8")

    assert 'data-view-button="facilities"' in html
    assert 'id="view-facilities"' in html
    for element_id in (
        "facilityCreatePanel",
        "facilityCreateForm",
        "facilityList",
        "facilityDetail",
        "facilityMoneyRail",
        "facilityInstallments",
        "facilityPayments",
        "facilityActions",
    ):
        assert f'id="{element_id}"' in html
    for key in (
        "navFacilities",
        "facilityTitle",
        "facilityCreate",
        "facilityPrincipal",
        "facilityPaid",
        "facilityOutstanding",
        "facilityInstallments",
        "facilityPayments",
        "facilityBoundary",
    ):
        assert javascript.count(f"{key}:") == 2


def test_closed_auditor_facility_exposes_bilingual_actual_outcome_candidate_ui():
    javascript = WORKFLOW_JS_PATH.read_text(encoding="utf-8")

    for element_id in (
        "actualOutcomePanel",
        "actualOutcomeForm",
        "outcomeLineage",
        "calibrationCandidate",
        "outcomeSubmitError",
    ):
        assert f'id="{element_id}"' in javascript
    for key in (
        "outcomeTitle",
        "outcomeDefaulted",
        "outcomeDaysPastDue",
        "outcomeLossAmount",
        "outcomeObservedAt",
        "outcomeEvidenceReference",
        "outcomeProvenance",
        "outcomeSubmit",
        "outcomeExploratory",
        "outcomeEligible",
        "outcomeNeverPromoted",
        "outcomeLineageTitle",
        "outcomeMetrics",
        "outcomeArtifactIntegrity",
    ):
        assert javascript.count(f"{key}:") == 2
    assert 'state.user.role === "auditor" && facility.status === "closed"' in javascript


def test_actual_outcome_ui_hashes_reference_and_preserves_request_identity():
    javascript = WORKFLOW_JS_PATH.read_text(encoding="utf-8")

    for path in (
        "/api/v1/outcomes?limit=50",
        "/api/v1/calibration-runs?limit=50",
        "/actual-outcome",
    ):
        assert path in javascript
    assert 'crypto.subtle.digest("SHA-256"' in javascript
    assert "new TextEncoder().encode" in javascript
    assert "evidence_sha256" in javascript
    assert "evidence_reference:" not in javascript
    assert "outcomeIdempotencyKeys[facility.facility_id] ||= crypto.randomUUID()" in javascript
    assert ".toISOString()" in javascript


def test_financing_rerender_preserves_pending_disabled_and_busy_state():
    javascript = WORKFLOW_JS_PATH.read_text(encoding="utf-8")

    assert "function renderFacilityWorkbench()" in javascript
    assert "setFacilityPending(state.facilityPending);" in javascript
    assert 'view?.setAttribute("aria-busy", String(value));' in javascript


def test_delegated_actual_outcome_submit_uses_the_dynamic_form_not_document():
    javascript = WORKFLOW_JS_PATH.read_text(encoding="utf-8")

    assert 'const form = event.target.closest?.("#actualOutcomeForm");' in javascript
    assert "if (!form) return;" in javascript
    assert "const data = new FormData(form);" in javascript


def test_outcome_lineage_displays_required_engine_and_nullable_research_model():
    javascript = WORKFLOW_JS_PATH.read_text(encoding="utf-8")

    assert "outcome.risk_engine_version" in javascript
    assert 'outcome.model_version_id || "—"' in javascript


def test_outcome_observation_default_is_second_precision_and_after_closure():
    javascript = WORKFLOW_JS_PATH.read_text(encoding="utf-8")

    assert "localObservedAt(facility.closed_at)" in javascript
    assert "Math.ceil(instant / 1000) * 1000" in javascript
    assert 'name="observed_at" type="datetime-local" step="1"' in javascript


def test_failed_or_missing_calibration_run_is_never_badged_as_exploratory():
    javascript = WORKFLOW_JS_PATH.read_text(encoding="utf-8")

    for status in (
        '"eligible_candidate"',
        '"exploratory_candidate"',
        '"failed"',
        '"missing"',
    ):
        assert status in javascript
    for key in ("outcomeFailed", "outcomeRunMissing", "outcomeNotCreated"):
        assert javascript.count(f"{key}:") == 2
    assert 'hasCandidate ? "outcomeNeverPromoted" : "outcomeNotCreated"' in javascript
    assert 'run?.status === "eligible_candidate" ? "outcomeEligible" : "outcomeExploratory"' not in javascript


def test_facility_javascript_preserves_exact_money_and_covers_every_api_action():
    javascript = WORKFLOW_JS_PATH.read_text(encoding="utf-8")

    for path in (
        "/api/v1/facilities",
        "/initiate-disbursement",
        "/confirm-disbursement",
        "/payments",
        "/mark-overdue",
        "/close",
    ):
        assert path in javascript
    assert "facility.principal" in javascript
    assert "facility.outstanding_amount" in javascript
    assert "Number(facility.outstanding_amount)" not in javascript
    assert "parseFloat(facility.outstanding_amount)" not in javascript
    assert "facility.allowed_actions" in javascript


def test_facility_commands_are_versioned_idempotent_and_pending_safe():
    javascript = WORKFLOW_JS_PATH.read_text(encoding="utf-8")

    assert "crypto.randomUUID()" in javascript
    assert "version: facility.version" in javascript
    assert "facilityPending" in javascript
    assert "data-facility-action" in javascript
    assert "facility.allowed_actions.map" in javascript


def test_facility_errors_and_accessibility_have_bilingual_contracts():
    javascript = WORKFLOW_JS_PATH.read_text(encoding="utf-8")
    stylesheet = (HTML_PATH.with_name("workflow.css")).read_text(encoding="utf-8")

    for code in (
        "facility_not_found",
        "forbidden_role",
        "facility_precondition_failed",
        "facility_conflict",
    ):
        assert javascript.count(f"{code}:") == 2
    assert ":focus-visible" in stylesheet
    assert "prefers-reduced-motion: reduce" in stylesheet
    assert "@media (max-width: 600px)" in stylesheet


def test_login_has_bilingual_numbered_role_guide():
    html = _html()
    javascript = WORKFLOW_JS_PATH.read_text(encoding="utf-8")

    assert html.count('data-demo-username="') == 5
    assert html.count('class="demo-role-guide') == 5
    assert 'aria-label="Demo role order"' in html
    assert javascript.count("demoRoleGuide:") == 2
    assert 'document.querySelector("#loginButton").focus()' in javascript
    assert 'login(button.dataset.demoUsername, "Demo123!")' not in javascript


def test_login_role_guide_keeps_native_button_semantics_inside_a_real_list():
    html = _html()

    assert '<ol id="demoAccounts" class="account-grid"' in html
    assert html.count('class="demo-role-item"') == 5
    assert 'type="button" role="listitem"' not in html


def test_browser_script_requires_explicit_video_flag():
    source = (HTML_PATH.parents[2] / "scripts" / "browser_acceptance.py").read_text(
        encoding="utf-8"
    )

    assert "--record-video" in source
    assert "record_video_dir" in source
    assert "record_video=False" in source
    assert "def run_acceptance(" in source
