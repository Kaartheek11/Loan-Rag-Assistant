"""Test helpers: build small PDFs with known content."""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

import yaml
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate


def make_pdf(path: Path, pages: list[str]) -> Path:
    """One PDF page per entry; each entry's lines become paragraphs."""
    style = getSampleStyleSheet()["BodyText"]
    story: list[object] = []
    for i, page in enumerate(pages):
        for line in page.strip().splitlines():
            if line.strip():
                story.append(Paragraph(escape(line.strip()), style))
        if i < len(pages) - 1:
            story.append(PageBreak())
    path.parent.mkdir(parents=True, exist_ok=True)
    SimpleDocTemplate(str(path), pagesize=LETTER).build(story)
    return path


def write_acl(path: Path, acls: dict[str, dict[str, list[str]]]) -> Path:
    path.write_text(yaml.safe_dump({"documents": acls}))
    return path
