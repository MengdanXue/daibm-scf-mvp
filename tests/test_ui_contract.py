from pathlib import Path


HTML_PATH = Path(__file__).parents[1] / "app" / "static" / "index.html"


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
