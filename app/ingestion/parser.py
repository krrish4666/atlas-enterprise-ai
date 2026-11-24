"""app/ingestion/parser.py — Document text extraction for Atlas Enterprise AI.

Extracts text content and page metadata from supported document formats:
- PDF (.pdf via pypdf)
- Microsoft Word (.docx via python-docx)
- Markdown (.md via utf-8 decode)
- Plain Text (.txt via utf-8 decode)
"""

import io
import os
from dataclasses import dataclass

import docx
import pypdf

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".md", ".txt"}


@dataclass
class ParsedPage:
    """Represents text extracted from a single page or logical section."""

    page_number: int  # 1-indexed
    text: str


def parse_document(content_bytes: bytes, filename: str) -> list[ParsedPage]:
    """Parse raw document bytes into a list of ParsedPage objects.

    Args:
        content_bytes: Raw bytes of the document.
        filename: Original filename (used to determine extension and parser).

    Returns:
        List of ParsedPage objects with non-empty text.

    Raises:
        ValueError: If file is empty, format is unsupported, or no text could be extracted.
    """
    if not content_bytes:
        raise ValueError(f"Document '{filename}' is empty (0 bytes).")

    ext = os.path.splitext(filename.lower())[1]
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"Unsupported file format '{ext}' for document '{filename}'. "
            f"Supported formats: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )

    if ext == ".pdf":
        return _parse_pdf(content_bytes, filename)
    elif ext == ".docx":
        return _parse_docx(content_bytes, filename)
    else:  # .txt, .md
        return _parse_text(content_bytes, filename)


def _parse_pdf(content_bytes: bytes, filename: str) -> list[ParsedPage]:
    """Extract text page-by-page from PDF bytes."""
    pages: list[ParsedPage] = []
    try:
        reader = pypdf.PdfReader(io.BytesIO(content_bytes))
        for page_idx, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            text = text.strip()
            if text:
                pages.append(ParsedPage(page_number=page_idx + 1, text=text))
    except Exception as exc:
        raise ValueError(f"Failed to parse PDF document '{filename}': {exc}") from exc

    if not pages:
        raise ValueError(f"PDF document '{filename}' contains no extractable text.")

    return pages


def _parse_docx(content_bytes: bytes, filename: str) -> list[ParsedPage]:
    """Extract text from DOCX bytes."""
    try:
        doc = docx.Document(io.BytesIO(content_bytes))
        paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
        full_text = "\n\n".join(paragraphs).strip()
    except Exception as exc:
        raise ValueError(f"Failed to parse DOCX document '{filename}': {exc}") from exc

    if not full_text:
        raise ValueError(f"DOCX document '{filename}' contains no extractable text.")

    return [ParsedPage(page_number=1, text=full_text)]


def _parse_text(content_bytes: bytes, filename: str) -> list[ParsedPage]:
    """Decode plain text or markdown bytes."""
    try:
        text = content_bytes.decode("utf-8").strip()
    except UnicodeDecodeError:
        try:
            text = content_bytes.decode("latin-1").strip()
        except Exception as exc:
            raise ValueError(f"Failed to decode text document '{filename}': {exc}") from exc

    if not text:
        raise ValueError(f"Text document '{filename}' is empty.")

    return [ParsedPage(page_number=1, text=text)]
