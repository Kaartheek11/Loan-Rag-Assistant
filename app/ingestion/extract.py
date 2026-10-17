"""Per-page text extraction: pdfplumber, pypdf fallback, Tesseract OCR for empty pages (§5.4)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

OCR_MIN_CHARS = 20
OCR_DPI = 300


class ExtractionError(RuntimeError):
    pass


@dataclass
class ExtractedDocument:
    pages: list[str]
    ocr_pages: list[int] = field(default_factory=list)  # 1-based page numbers


def _pdfplumber_pages(path: Path) -> list[str]:
    import pdfplumber

    with pdfplumber.open(path) as pdf:
        return [page.extract_text() or "" for page in pdf.pages]


def _pypdf_pages(path: Path) -> list[str]:
    from pypdf import PdfReader

    return [page.extract_text() or "" for page in PdfReader(path).pages]


def _ocr_page(path: Path, page_no: int) -> str:
    import pytesseract
    from pdf2image import convert_from_path

    images = convert_from_path(path, dpi=OCR_DPI, first_page=page_no, last_page=page_no)
    return "\n".join(pytesseract.image_to_string(im) for im in images)


def extract_pdf(path: Path) -> ExtractedDocument:
    try:
        pages = _pdfplumber_pages(path)
    except Exception:  # noqa: BLE001 - any pdfplumber failure falls back to pypdf
        try:
            pages = _pypdf_pages(path)
        except Exception as exc:
            raise ExtractionError(type(exc).__name__) from exc
    if not pages:
        raise ExtractionError("no_pages")
    ocr_pages: list[int] = []
    for i, page_text in enumerate(pages, start=1):
        if len(page_text.strip()) < OCR_MIN_CHARS:
            try:
                pages[i - 1] = _ocr_page(path, i)
            except Exception as exc:
                raise ExtractionError(f"ocr_failed:{type(exc).__name__}") from exc
            ocr_pages.append(i)
    return ExtractedDocument(pages=pages, ocr_pages=ocr_pages)
