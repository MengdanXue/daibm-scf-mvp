from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path

import pypdfium2 as pdfium
import pytest
from pypdf import PdfReader

from scripts.render_defense_documents import render_documents


ROOT = Path(__file__).parents[1]
DOCUMENTS = (
    ("defense-one-page.md", "defense-one-page.pdf"),
    ("research-brief-en.md", "research-brief-en.pdf"),
)


def _render_in_temp_root(
    tmp_path: Path,
    *,
    source_date_epoch: int | None = None,
) -> tuple[Path, Path]:
    docs = tmp_path / "docs"
    docs.mkdir(parents=True)
    for source_name, _ in DOCUMENTS:
        shutil.copy2(ROOT / "docs" / source_name, docs / source_name)
    font_assets = ROOT / "assets" / "fonts"
    if font_assets.is_dir():
        shutil.copytree(font_assets, tmp_path / "assets" / "fonts")
    if source_date_epoch is None:
        return render_documents(tmp_path)
    return render_documents(tmp_path, source_date_epoch=source_date_epoch)


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
        "mean ± sample SD across n=5 seeds",
        "does not reproduce the original thesis",
        "does not execute a real bank transfer",
        "Platt calibration candidate",
        "gated automatic activation",
        "does not retrain the TGNN",
        "single-organization Fabric 2.5.16",
        "Circom/Groth16",
        "These are demonstration modules, not production Fabric consensus or a production ZKP service.",
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
    assert cjk_runs and all("NotoSansSC" in font for font in cjk_runs)


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
        "controlled actual-outcome feedback",
        "Platt calibration candidate",
        "gated automatic activation",
        "does not trigger on drift",
        "single-organization Fabric 2.5.16",
        "Circom/Groth16",
        "Next research step",
        "real-enterprise outcomes",
    ):
        assert expected in text


def _font_file(font: dict):
    descriptor = font.get("/FontDescriptor")
    if descriptor is None and font.get("/DescendantFonts"):
        descendant = font["/DescendantFonts"][0].get_object()
        descriptor = descendant.get("/FontDescriptor")
    if descriptor is None:
        return None
    descriptor = descriptor.get_object()
    return descriptor.get("/FontFile") or descriptor.get("/FontFile2") or descriptor.get(
        "/FontFile3"
    )


def test_pdfs_embed_repository_fonts_and_never_reference_stsong(tmp_path):
    defense, brief = _render_in_temp_root(tmp_path)

    for path in (defense, brief):
        fonts = PdfReader(path).pages[0]["/Resources"]["/Font"]
        records = [reference.get_object() for reference in fonts.get_object().values()]
        base_fonts = [str(font.get("/BaseFont", "")) for font in records]
        assert not any("STSong" in name for name in base_fonts)
        assert any("DejaVuSans" in name and _font_file(font) for name, font in zip(base_fonts, records, strict=True))
        if path == defense:
            assert any("NotoSansSC" in name and _font_file(font) for name, font in zip(base_fonts, records, strict=True))


def test_bilingual_sheet_raster_contains_real_chinese_ink(tmp_path):
    defense, _ = _render_in_temp_root(tmp_path)
    scale = 2
    document = pdfium.PdfDocument(defense)
    image = document[0].render(scale=scale).to_pil().convert("L")
    page_height = float(PdfReader(defense).pages[0].mediabox.height)
    chinese_run: tuple[float, float, float] | None = None

    def capture(text, cm, tm, _font, size):
        nonlocal chinese_run
        if "硕士论文答辩速查" in text:
            chinese_run = (float(cm[4] + tm[4]), float(cm[5] + tm[5]), float(size))

    PdfReader(defense).pages[0].extract_text(visitor_text=capture)
    assert chinese_run is not None
    x, baseline, size = chinese_run
    crop = image.crop(
        (
            int(x * scale),
            int((page_height - baseline - size * 1.5) * scale),
            int((x + 130) * scale),
            int((page_height - baseline + size * 0.5) * scale),
        )
    )
    assert sum(pixel < 180 for pixel in crop.get_flattened_data()) > 150


def test_same_sources_and_source_date_epoch_produce_identical_pdf_bytes(tmp_path):
    epoch = 1_704_067_200  # 2024-01-01T00:00:00Z
    first = _render_in_temp_root(tmp_path / "first", source_date_epoch=epoch)
    second = _render_in_temp_root(tmp_path / "second", source_date_epoch=epoch)

    assert [hashlib.sha256(path.read_bytes()).hexdigest() for path in first] == [
        hashlib.sha256(path.read_bytes()).hexdigest() for path in second
    ]
    for path in first:
        assert "generated_date=2024-01-01" in (PdfReader(path).metadata.subject or "")


@pytest.mark.parametrize(
    "font_name",
    ("DejaVuSans.ttf", "NotoSansSC-DefenseSubset.ttf"),
)
def test_renderer_refuses_to_fall_back_when_repository_font_is_missing(
    tmp_path,
    font_name,
):
    docs = tmp_path / "docs"
    docs.mkdir(parents=True)
    for source_name, _ in DOCUMENTS:
        shutil.copy2(ROOT / "docs" / source_name, docs / source_name)
    shutil.copytree(ROOT / "assets" / "fonts", tmp_path / "assets" / "fonts")
    (tmp_path / "assets" / "fonts" / font_name).unlink()

    with pytest.raises(FileNotFoundError, match=font_name):
        render_documents(tmp_path)


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


def test_defense_sources_distinguish_wiring_from_cryptographic_verification():
    for source_name, _ in DOCUMENTS:
        source = (ROOT / "docs" / source_name).read_text(encoding="utf-8")
        assert "wired into trade confirmation" in source
        assert "not cryptographic validity" in source
        assert "chronological 70/30 holdout" in source
        assert "not wired into the default business flow" not in source


def test_ci_and_defense_guides_expose_supported_runtime_and_fallback_contracts():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    demo = (ROOT / "docs/demo-script.md").read_text(encoding="utf-8")

    assert workflow.count("actions/checkout@v5") == 4
    assert workflow.count("actions/setup-python@v6") == 3
    assert workflow.count("actions/setup-node@v6") == 2
    assert 'RUN_ZKP_WORKFLOW_INTEGRATION: "1"' in workflow
    # Static analysis is a release gate, not advice: a job that lints without
    # failing the build is indistinguishable from no job at all.
    assert "python -m ruff check ." in workflow
    assert "python -m mypy" in workflow
    assert "tests/test_defense_documents.py" in workflow
    assert "tests/test_defense_preflight.py" in workflow
    # The optional Fabric and zero-knowledge components ship with their own
    # suites; the defense claim that they are real depends on CI running them.
    assert "advanced/fabric/chaincode" in workflow
    assert "advanced/zkp" in workflow
    assert "npm run verify:artifacts" in workflow
    assert "node:24-bookworm-slim" in workflow
    for filename in (
        "docs/defense-one-page.pdf",
        "docs/research-brief-en.pdf",
    ):
        assert filename in readme
        assert filename in demo
