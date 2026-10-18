"""Per-page text extraction: pdfplumber, pypdf fallback, Tesseract OCR for empty pages (§5.4).

Pages are processed one at a time so callers can report progress on long files
(extension.md §6.1 step 4, §17.4).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

OCR_MIN_CHARS = 20
OCR_DPI = 300

# Fixed failure reasons (extension.md §6.1). Details stay in server-side logs only.
UNREADABLE_PDF = "unreadable_pdf"
OCR_FAILED = "ocr_failed"
NO_TEXT_FOUND = "no_text_found"


class ExtractionError(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class ExtractedDocument:
    pages: list[str]
    ocr_pages: list[int] = field(default_factory=list)  # 1-based page numbers


def _pdfplumber_pages(path: Path) -> Iterator[tuple[int, str]]:
    import pdfplumber

    with pdfplumber.open(path) as pdf:
        total = len(pdf.pages)
        for page in pdf.pages:
            yield total, page.extract_text() or ""


def _pypdf_pages(path: Path) -> Iterator[tuple[int, str]]:
    from pypdf import PdfReader

    reader = PdfReader(path)
    total = len(reader.pages)
    for page in reader.pages:
        yield total, page.extract_text() or ""


def _ocr_page(path: Path, page_no: int) -> str:
    import pytesseract
    from pdf2image import convert_from_path

    images = convert_from_path(path, dpi=OCR_DPI, first_page=page_no, last_page=page_no)
    return "\n".join(pytesseract.image_to_string(im) for im in images)


def _extract(
    path: Path, pages_iter: Iterator[tuple[int, str]], on_page: Callable[[int, int], None] | None
) -> ExtractedDocument:
    pages: list[str] = []
    ocr_pages: list[int] = []
    for i, (total, text) in enumerate(pages_iter, start=1):
        if len(text.strip()) < OCR_MIN_CHARS:
            try:
                text = _ocr_page(path, i)
            except Exception as exc:
                raise ExtractionError(OCR_FAILED) from exc
            ocr_pages.append(i)
        pages.append(text)
        if on_page is not None:
            on_page(i, total)
    return ExtractedDocument(pages=pages, ocr_pages=ocr_pages)


def extract_pdf(path: Path, on_page: Callable[[int, int], None] | None = None) -> ExtractedDocument:
    """Text per page. `on_page(page_no, total)` is called after each page."""
    try:
        doc = _extract(path, _pdfplumber_pages(path), on_page)
    except ExtractionError:
        raise
    except Exception:  # noqa: BLE001 - any pdfplumber failure falls back to pypdf
        try:
            doc = _extract(path, _pypdf_pages(path), on_page)
        except ExtractionError:
            raise
        except Exception as exc:
            raise ExtractionError(UNREADABLE_PDF) from exc
    if not doc.pages:
        raise ExtractionError(UNREADABLE_PDF)
    if not any(len(p.strip()) >= OCR_MIN_CHARS for p in doc.pages):
        raise ExtractionError(NO_TEXT_FOUND)
    return doc
