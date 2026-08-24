from __future__ import annotations

import argparse
import hashlib
import html
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from pypdf import PdfReader
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    FrameBreak,
    PageTemplate,
    Paragraph,
    Spacer,
)


ROOT = Path(__file__).resolve().parents[1]
DOCUMENTS = (
    ("defense-one-page.md", "defense-one-page.pdf"),
    ("research-brief-en.md", "research-brief-en.pdf"),
)
DEFAULT_SOURCE_DATE_EPOCH = 1_787_529_600  # 2026-08-24T00:00:00Z
DEJAVU_FONT = "DejaVuSans.ttf"
CJK_FONT = "NotoSansSC-DefenseSubset.ttf"
NAVY = colors.HexColor("#10264A")
BLUE = colors.HexColor("#0072B2")
ORANGE = colors.HexColor("#D55E00")
INK = colors.HexColor("#16243A")
MUTED = colors.HexColor("#526176")
PALE = colors.HexColor("#EEF4FA")


def _font_asset(root: Path, filename: str) -> Path:
    path = root / "assets" / "fonts" / filename
    if not path.is_file():
        raise FileNotFoundError(f"repository font asset is missing: {path}")
    return path


def _register_fonts(root: Path) -> None:
    dejavu_path = _font_asset(root, DEJAVU_FONT)
    cjk_path = _font_asset(root, CJK_FONT)
    if "DAIBM-DejaVu" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("DAIBM-DejaVu", str(dejavu_path)))
        pdfmetrics.registerFontFamily(
            "DAIBM-DejaVu",
            normal="DAIBM-DejaVu",
            bold="DAIBM-DejaVu",
            italic="DAIBM-DejaVu",
            boldItalic="DAIBM-DejaVu",
        )
    if "DAIBM-NotoSansSC" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("DAIBM-NotoSansSC", str(cjk_path)))


def _clean_inline(text: str) -> str:
    plain = text.replace("**", "").replace("`", "")
    escaped = html.escape(plain, quote=False)
    return re.sub(
        r"[\u3000-\u303f\u3400-\u9fff\uff00-\uffef]+",
        lambda match: f'<font name="DAIBM-NotoSansSC">{match.group(0)}</font>',
        escaped,
    )


def _styles() -> dict[str, ParagraphStyle]:
    common = dict(
        textColor=INK,
        fontSize=7.25,
        leading=9.15,
        spaceAfter=2.1,
        splitLongWords=True,
        allowWidows=0,
        allowOrphans=0,
    )
    return {
        "body": ParagraphStyle("body", fontName="DAIBM-DejaVu", **common),
        "bullet": ParagraphStyle(
            "bullet",
            fontName="DAIBM-DejaVu",
            leftIndent=8,
            firstLineIndent=-6,
            bulletIndent=0,
            **common,
        ),
        "section": ParagraphStyle(
            "section",
            fontName="DAIBM-DejaVu",
            fontSize=9.2,
            leading=11,
            textColor=BLUE,
            spaceBefore=5,
            spaceAfter=3,
            keepWithNext=True,
        ),
        "lead": ParagraphStyle(
            "lead",
            fontName="DAIBM-DejaVu",
            fontSize=8.2,
            leading=10.2,
            textColor=MUTED,
            borderColor=PALE,
            borderWidth=0.5,
            borderPadding=4,
            backColor=PALE,
            spaceAfter=4,
        ),
    }


def _story(markdown: str) -> tuple[str, list[object]]:
    styles = _styles()
    title = "DAIBM-SCF"
    story: list[object] = []
    lead_count = 0
    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("# "):
            title = line[2:].strip()
            continue
        if line == "<!-- column-break -->":
            story.append(FrameBreak)
            continue
        if line.startswith("## "):
            story.append(Paragraph(_clean_inline(line[3:]), styles["section"]))
            continue
        numbered = re.match(r"^(\d+)\.\s+(.*)$", line)
        if line.startswith("- ") or numbered:
            if numbered:
                prefix, content = f"{numbered.group(1)}.", numbered.group(2)
            else:
                prefix, content = "-", line[2:]
            story.append(
                Paragraph(f"{prefix} {_clean_inline(content)}", styles["bullet"])
            )
            continue
        if lead_count < 2:
            key = "lead"
            lead_count += 1
        else:
            key = "body"
        story.append(Paragraph(_clean_inline(line), styles[key]))
    return title, story


class _MetadataCanvas(canvas.Canvas):
    def __init__(
        self,
        *args,
        title: str,
        subject: str,
        **kwargs,
    ) -> None:
        kwargs["invariant"] = 1
        super().__init__(*args, **kwargs)
        self.setTitle(title)
        self.setAuthor("DAIBM-SCF research MVP")
        self.setSubject(subject)
        self.setKeywords("master thesis, supply chain finance, synthetic evidence")


def _page_chrome(source_name: str, title: str):
    def draw(page_canvas: canvas.Canvas, document: BaseDocTemplate) -> None:
        del document
        width, height = A4
        page_canvas.saveState()
        page_canvas.setFillColor(NAVY)
        page_canvas.rect(0, height - 24 * mm, width, 24 * mm, fill=1, stroke=0)
        page_canvas.setFillColor(colors.white)
        page_canvas.setFont("DAIBM-DejaVu", 15)
        page_canvas.drawString(11 * mm, height - 13 * mm, title)
        page_canvas.setFillColor(ORANGE)
        page_canvas.rect(11 * mm, height - 18 * mm, 34 * mm, 1.2 * mm, fill=1, stroke=0)
        page_canvas.setFillColor(MUTED)
        page_canvas.setFont("DAIBM-DejaVu", 6.4)
        page_canvas.drawString(
            11 * mm,
            7 * mm,
            f"DAIBM-SCF | Canonical source: docs/{source_name}",
        )
        page_canvas.drawRightString(width - 11 * mm, 7 * mm, "SYNTHETIC DEMO")
        page_canvas.restoreState()

    return draw


def _generated_date(source_date_epoch: int | None) -> str:
    raw_value: int | str = source_date_epoch
    if raw_value is None:
        raw_value = os.environ.get(
            "SOURCE_DATE_EPOCH",
            str(DEFAULT_SOURCE_DATE_EPOCH),
        )
    try:
        epoch = int(raw_value)
        if epoch < 0:
            raise ValueError
        return datetime.fromtimestamp(epoch, timezone.utc).date().isoformat()
    except (OverflowError, OSError, TypeError, ValueError) as error:
        raise ValueError("SOURCE_DATE_EPOCH must be a non-negative integer") from error


def _render_one(source: Path, destination: Path, generated_date: str) -> None:
    markdown = source.read_text(encoding="utf-8")
    title, story = _story(markdown)
    source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    subject = (
        f"source_sha256={source_sha256}; generated_date={generated_date}; "
        "canonical_source=markdown"
    )
    width, height = A4
    left = 11 * mm
    right = 11 * mm
    bottom = 11 * mm
    top = 27 * mm
    gap = 5 * mm
    frame_width = (width - left - right - gap) / 2
    frame_height = height - top - bottom
    frames = (
        Frame(left, bottom, frame_width, frame_height, id="left", showBoundary=0),
        Frame(
            left + frame_width + gap,
            bottom,
            frame_width,
            frame_height,
            id="right",
            showBoundary=0,
        ),
    )
    document = BaseDocTemplate(
        str(destination),
        pagesize=A4,
        leftMargin=left,
        rightMargin=right,
        topMargin=top,
        bottomMargin=bottom,
        title=title,
        author="DAIBM-SCF research MVP",
        subject=subject,
        keywords="master thesis, supply chain finance, synthetic evidence",
    )
    document.addPageTemplates(
        PageTemplate(
            id="two-column",
            frames=frames,
            onPage=_page_chrome(source.name, title),
        )
    )
    document.build(
        story,
        canvasmaker=lambda *args, **kwargs: _MetadataCanvas(
            *args, title=title, subject=subject, **kwargs
        ),
    )
    if len(PdfReader(destination).pages) != 1:
        raise RuntimeError(f"{destination} must render to exactly one page")


def render_documents(
    root: Path,
    *,
    source_date_epoch: int | None = None,
) -> tuple[Path, Path]:
    active_root = Path(root)
    docs = active_root / "docs"
    _register_fonts(active_root)
    generated_date = _generated_date(source_date_epoch)
    outputs: list[Path] = []
    for source_name, pdf_name in DOCUMENTS:
        source = docs / source_name
        if not source.is_file():
            raise FileNotFoundError(f"canonical source is missing: {source}")
        destination = docs / pdf_name
        _render_one(source, destination, generated_date)
        outputs.append(destination)
    return tuple(outputs)  # type: ignore[return-value]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render one-page defense PDFs")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--source-date-epoch", type=int)
    args = parser.parse_args(argv)
    for output in render_documents(
        args.root,
        source_date_epoch=args.source_date_epoch,
    ):
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
