import io
import logging
from typing import NamedTuple

import fitz  # PyMuPDF
import pdfplumber
import pytesseract
from PIL import Image

logger = logging.getLogger(__name__)

# Minimum character count to consider a strategy successful for a page
_MIN_CHARS_PER_PAGE = 100


class PageResult(NamedTuple):
    page_num: int
    text: str
    strategy: str  # "pymupdf" | "pdfplumber" | "ocr" | "empty"


def _extract_page_pymupdf(page: fitz.Page) -> str:
    """Extract text from a single page using PyMuPDF."""
    return page.get_text("text").strip()


def _extract_page_pdfplumber(pl_page) -> str:
    """Extract text from a single pdfplumber page object."""
    text = pl_page.extract_text()
    return (text or "").strip()


def _extract_page_ocr(page: fitz.Page) -> str:
    """
    Render a fitz page to an image then run Tesseract OCR (German language).
    Used as last resort for scanned / image-only pages.
    """
    # Render at 2x scale for better OCR accuracy
    mat = fitz.Matrix(2.0, 2.0)
    pix = page.get_pixmap(matrix=mat)
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    text = pytesseract.image_to_string(img, lang="deu")
    return text.strip()


def extract_text(pdf_bytes: bytes) -> str:
    """
    Extract text from a PDF using a three-strategy cascade per page.

    Strategy priority (per page):
      1. PyMuPDF  — fastest, best for clean digital PDFs
      2. pdfplumber — better for complex table/column layouts
      3. Tesseract OCR — fallback for scanned/image pages

    The strategy that produces the most characters (above the minimum
    threshold) wins for each page.

    Args:
        pdf_bytes: Raw PDF file content as bytes.

    Returns:
        Combined extracted text for the entire document, pages separated
        by double newlines.
    """
    page_results: list[PageResult] = []

    fitz_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pl_doc = pdfplumber.open(io.BytesIO(pdf_bytes))

    try:
        num_pages = len(fitz_doc)
        logger.info("PDF has %d page(s). Extracting text…", num_pages)

        for page_num in range(num_pages):
            fitz_page = fitz_doc[page_num]
            pl_page = pl_doc.pages[page_num]

            # Strategy 1: PyMuPDF
            text_pymupdf = _extract_page_pymupdf(fitz_page)

            # Strategy 2: pdfplumber
            text_pdfplumber = _extract_page_pdfplumber(pl_page)

            # Pick the best of the two digital strategies
            candidates = [
                (text_pymupdf, "pymupdf"),
                (text_pdfplumber, "pdfplumber"),
            ]
            best_text, best_strategy = max(candidates, key=lambda c: len(c[0]))

            if len(best_text) >= _MIN_CHARS_PER_PAGE:
                page_results.append(PageResult(page_num, best_text, best_strategy))
                logger.debug(
                    "Page %d: %s yielded %d chars",
                    page_num + 1, best_strategy, len(best_text),
                )
            else:
                # Strategy 3: OCR fallback
                logger.info(
                    "Page %d: digital extraction yielded only %d chars — trying OCR",
                    page_num + 1, len(best_text),
                )
                try:
                    text_ocr = _extract_page_ocr(fitz_page)
                    if text_ocr:
                        page_results.append(PageResult(page_num, text_ocr, "ocr"))
                        logger.info(
                            "Page %d: OCR yielded %d chars", page_num + 1, len(text_ocr)
                        )
                    else:
                        page_results.append(PageResult(page_num, "", "empty"))
                        logger.warning("Page %d: no text extracted by any strategy", page_num + 1)
                except Exception as exc:
                    logger.error("Page %d: OCR failed — %s", page_num + 1, exc)
                    # Fall back to whatever digital extraction got, even if sparse
                    page_results.append(PageResult(page_num, best_text, best_strategy))

    finally:
        pl_doc.close()
        fitz_doc.close()

    strategy_summary = {}
    for r in page_results:
        strategy_summary[r.strategy] = strategy_summary.get(r.strategy, 0) + 1
    logger.info("Extraction complete. Strategy usage: %s", strategy_summary)

    combined = "\n\n".join(r.text for r in page_results if r.text)
    return combined
