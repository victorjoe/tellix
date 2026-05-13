"""Render HELP.md and RELEASE.md to polished PDFs.

Usage (from the project root, with the venv activated):

    pip install reportlab
    python tools/render_docs.py

Output:
    Tellix-Help.pdf
    Tellix-Release-Notes-1.0.0.pdf

The conversion handles the markdown subset used in the source docs:
H1/H2/H3 headings, paragraphs, pipe tables, fenced code blocks,
inline **bold** and `code`.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Paragraph,
    Preformatted,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


# ---------- Styles ----------

_styles = getSampleStyleSheet()

TITLE_STYLE = ParagraphStyle(
    "TellixTitle", parent=_styles["Title"],
    fontName="Helvetica-Bold", fontSize=24, leading=28,
    spaceAfter=18, spaceBefore=0,
    textColor=colors.HexColor("#1a1a1a"), alignment=TA_LEFT,
)
H1_STYLE = ParagraphStyle(
    "TellixH1", parent=_styles["Heading1"],
    fontName="Helvetica-Bold", fontSize=15, leading=19,
    spaceBefore=18, spaceAfter=8,
    textColor=colors.HexColor("#1a1a1a"),
)
H2_STYLE = ParagraphStyle(
    "TellixH2", parent=_styles["Heading2"],
    fontName="Helvetica-Bold", fontSize=12, leading=16,
    spaceBefore=12, spaceAfter=6,
    textColor=colors.HexColor("#333333"),
)
BODY_STYLE = ParagraphStyle(
    "TellixBody", parent=_styles["Normal"],
    fontName="Helvetica", fontSize=10.5, leading=15,
    spaceAfter=7, alignment=TA_JUSTIFY,
    textColor=colors.HexColor("#222222"),
)
CELL_STYLE = ParagraphStyle(
    "TellixCell", parent=_styles["Normal"],
    fontName="Helvetica", fontSize=9, leading=12,
    textColor=colors.HexColor("#222222"),
)
CELL_HEADER_STYLE = ParagraphStyle(
    "TellixCellHdr", parent=_styles["Normal"],
    fontName="Helvetica-Bold", fontSize=9, leading=12,
    textColor=colors.HexColor("#1a1a1a"),
)
CODE_STYLE = ParagraphStyle(
    "TellixCode", parent=_styles["Code"],
    fontName="Courier", fontSize=9, leading=12,
    leftIndent=10, rightIndent=10,
    spaceBefore=4, spaceAfter=10,
    backColor=colors.HexColor("#f3f3f3"),
    borderColor=colors.HexColor("#dddddd"),
    borderWidth=0.5, borderPadding=6,
)


# ---------- Markdown -> reportlab ----------

def _xml_escape(text: str) -> str:
    return (text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;"))


def _inline_md(text: str) -> str:
    """Convert inline `code` and **bold** to reportlab XML."""
    text = _xml_escape(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(
        r"`([^`]+)`",
        r'<font name="Courier" backColor="#f0f0f0">\1</font>',
        text,
    )
    return text


def _parse_table_row(line: str):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _make_table(header_cells, data_rows):
    header = [Paragraph(_inline_md(c), CELL_HEADER_STYLE) for c in header_cells]
    body = [[Paragraph(_inline_md(c), CELL_STYLE) for c in row] for row in data_rows]
    t = Table([header] + body, repeatRows=1, hAlign="LEFT")
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eaeaea")),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#bbbbbb")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return t


def md_to_story(md_text: str):
    lines = md_text.split("\n")
    story = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if line.startswith("# "):
            story.append(Paragraph(_inline_md(line[2:].strip()), TITLE_STYLE))
            i += 1; continue
        if line.startswith("## "):
            story.append(Paragraph(_inline_md(line[3:].strip()), H1_STYLE))
            i += 1; continue
        if line.startswith("### "):
            story.append(Paragraph(_inline_md(line[4:].strip()), H2_STYLE))
            i += 1; continue

        if line.startswith("```"):
            i += 1
            code = []
            while i < len(lines) and not lines[i].startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1
            story.append(Preformatted("\n".join(code), CODE_STYLE))
            continue

        if (line.startswith("|") and i + 1 < len(lines)
                and lines[i + 1].startswith("|") and "---" in lines[i + 1]):
            header = _parse_table_row(line)
            i += 2
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append(_parse_table_row(lines[i]))
                i += 1
            story.append(_make_table(header, rows))
            story.append(Spacer(1, 8))
            continue

        if not stripped:
            i += 1
            continue

        para_lines = [line]
        while i + 1 < len(lines):
            nxt = lines[i + 1]
            if (not nxt.strip()
                    or nxt.startswith(("#", "```", "|", "    "))):
                break
            i += 1
            para_lines.append(lines[i])
        para = " ".join(p.strip() for p in para_lines)
        story.append(Paragraph(_inline_md(para), BODY_STYLE))
        i += 1

    return story


# ---------- Render ----------

def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#888888"))
    canvas.drawString(0.6 * inch, 0.4 * inch, doc._tellix_title)
    canvas.drawRightString(letter[0] - 0.6 * inch, 0.4 * inch,
                           f"Page {doc.page}")
    canvas.setStrokeColor(colors.HexColor("#dddddd"))
    canvas.setLineWidth(0.4)
    canvas.line(0.6 * inch, 0.6 * inch,
                letter[0] - 0.6 * inch, 0.6 * inch)
    canvas.restoreState()


def render(md_path: Path, pdf_path: Path, title: str):
    story = md_to_story(md_path.read_text(encoding="utf-8"))
    doc = SimpleDocTemplate(
        str(pdf_path), pagesize=letter,
        leftMargin=0.75 * inch, rightMargin=0.75 * inch,
        topMargin=0.7 * inch, bottomMargin=0.85 * inch,
        title=title, author="Tellix", subject=title,
    )
    doc._tellix_title = title
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    print(f"  wrote {pdf_path.name} ({pdf_path.stat().st_size // 1024} KB)")


def main():
    project_root = Path(__file__).resolve().parent.parent
    targets = [
        (project_root / "HELP.md",
         project_root / "Tellix-Help.pdf",
         "Tellix - User Guide"),
        (project_root / "RELEASE.md",
         project_root / "Tellix-Release-Notes-1.0.0.pdf",
         "Tellix 1.0.0 - Release Notes"),
    ]
    missing = [md for md, _, _ in targets if not md.exists()]
    if missing:
        print(f"ERROR: missing source files: {', '.join(str(m) for m in missing)}",
              file=sys.stderr)
        return 2
    print("Rendering Tellix docs:")
    for md, pdf, title in targets:
        render(md, pdf, title)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
