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
