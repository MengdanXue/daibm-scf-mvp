from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path

from pypdf import PdfReader

from scripts.render_defense_documents import render_documents


ROOT = Path(__file__).parents[1]
DOCUMENTS = (
    ("defense-one-page.md", "defense-one-page.pdf"),
    ("research-brief-en.md", "research-brief-en.pdf"),
)


def _render_in_temp_root(tmp_path: Path) -> tuple[Path, Path]:
    docs = tmp_path / "docs"
    docs.mkdir()
    for source_name, _ in DOCUMENTS:
        shutil.copy2(ROOT / "docs" / source_name, docs / source_name)
    return render_documents(tmp_path)


def _text(path: Path) -> str:
    extracted = "\n".join(
        page.extract_text() or "" for page in PdfReader(path).pages
    )
    return re.sub(r"\s+", " ", extracted).strip()


def test_renderer_creates_exactly_two_single_page_pdfs_with_source_metadata(
    tmp_path,
):
    pdfs = _render_in_temp_root(tmp_path)

    assert tuple(path.name for path in pdfs) == (
        "defense-one-page.pdf",
        "research-brief-en.pdf",
    )
    for (source_name, _), path in zip(DOCUMENTS, pdfs, strict=True):
        reader = PdfReader(path)
        source_sha256 = hashlib.sha256(
            (tmp_path / "docs" / source_name).read_bytes()
        ).hexdigest()
        subject = reader.metadata.subject or ""
        assert len(reader.pages) == 1
        assert f"source_sha256={source_sha256}" in subject
        assert re.search(r"generated_date=\d{4}-\d{2}-\d{2}", subject)
        assert "generated_date=" not in _text(path)


def test_bilingual_sheet_extracts_architecture_flow_evidence_and_nonclaims(tmp_path):
    defense, _ = _render_in_temp_root(tmp_path)
    text = _text(defense)

    for expected in (
        "Поставщик",
        "供应商",
        "FastAPI",
        "PostgreSQL 17",
        "supplier.demo",
        "auditor.demo",
        "disbursement",
        "2026_EXPLORATORY_SENSITIVITY",
        "0.5469",
        "0.5889",
        "n=5 descriptive t intervals",
        "does not reproduce the original thesis",
        "does not execute a real bank transfer",
        "reset-defense-demo.cmd",
        "scripts/defense_preflight.py",
        "output/five-role-acceptance.png",
    ):
        assert expected in text


def test_bilingual_sheet_uses_script_appropriate_fonts_for_mixed_lines(tmp_path):
    defense, _ = _render_in_temp_root(tmp_path)
    runs: list[tuple[str, str]] = []

    def capture(text, _cm, _tm, font, _size):
        runs.append((text, str((font or {}).get("/BaseFont", ""))))

    PdfReader(defense).pages[0].extract_text(visitor_text=capture)
    cyrillic_runs = [font for text, font in runs if re.search(r"[\u0400-\u04ff]", text)]
    cjk_runs = [font for text, font in runs if re.search(r"[\u3400-\u9fff]", text)]

    assert cyrillic_runs and all("DejaVu" in font for font in cyrillic_runs)
    assert cjk_runs and all("STSong" in font for font in cjk_runs)


def test_bilingual_sheet_balances_major_sections_across_columns(tmp_path):
    defense, _ = _render_in_temp_root(tmp_path)
    page = PdfReader(defense).pages[0]
    heading_x: dict[str, float] = {}

    def capture(text, cm, _tm, _font, _size):
        for heading in ("Architecture /", "Research evidence /"):
            if text.startswith(heading):
                heading_x[heading] = float(cm[4])

    page.extract_text(visitor_text=capture)

    assert heading_x["Architecture /"] < float(page.mediabox.width) / 2
    assert heading_x["Research evidence /"] > float(page.mediabox.width) / 2


def test_english_brief_extracts_question_method_results_limits_and_next_step(tmp_path):
    _, brief = _render_in_temp_root(tmp_path)
    text = _text(brief)

    for expected in (
        "Research question",
        "Method",
        "Verified implementation",
        "Synthetic evidence",
        "TGNN ROC-AUC 0.5469",
        "XGBoost ROC-AUC 0.5889",
        "positive-class recall 0.022",
        "Limitations",
        "does not reproduce the original thesis",
        "Next research step",
        "real-enterprise outcomes",
    ):
        assert expected in text


def test_pdfs_use_cyrillic_and_cjk_capable_fonts(tmp_path):
    defense, brief = _render_in_temp_root(tmp_path)

    for path in (defense, brief):
        fonts = PdfReader(path).pages[0]["/Resources"]["/Font"]
        base_fonts = {
            str(reference.get_object().get("/BaseFont", ""))
            for reference in fonts.get_object().values()
        }
        assert any("DejaVuSans" in name for name in base_fonts)
    defense_fonts = PdfReader(defense).pages[0]["/Resources"]["/Font"]
    defense_base_fonts = {
        str(reference.get_object().get("/BaseFont", ""))
        for reference in defense_fonts.get_object().values()
    }
    assert any("STSong-Light" in name for name in defense_base_fonts)


def test_committed_sources_are_ascii_hyphen_only_and_pdfs_match_sources():
    forbidden_dashes = {"\u2010", "\u2011", "\u2012", "\u2013", "\u2014"}

    for source_name, pdf_name in DOCUMENTS:
        source = ROOT / "docs" / source_name
        pdf = ROOT / "docs" / pdf_name
        assert source.is_file() and pdf.is_file()
        assert forbidden_dashes.isdisjoint(source.read_text(encoding="utf-8"))
        reader = PdfReader(pdf)
        assert len(reader.pages) == 1
        assert hashlib.sha256(source.read_bytes()).hexdigest() in (
            reader.metadata.subject or ""
        )


def test_ci_and_defense_guides_expose_supported_runtime_and_fallback_contracts():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    demo = (ROOT / "docs/demo-script.md").read_text(encoding="utf-8")

    assert workflow.count("actions/checkout@v5") == 2
    assert workflow.count("actions/setup-python@v6") == 2
    assert "tests/test_defense_documents.py" in workflow
    assert "tests/test_defense_preflight.py" in workflow
    for filename in (
        "docs/defense-one-page.pdf",
        "docs/research-brief-en.pdf",
    ):
        assert filename in readme
        assert filename in demo
