"""app/ingestion/chunker.py — Document text chunking for Atlas Enterprise AI.

Splits document text into overlapping chunks (~800 characters, 150-char overlap)
while preserving page number, source filename, and character offset metadata.
"""

from dataclasses import dataclass

from app.ingestion.parser import ParsedPage

DEFAULT_CHUNK_SIZE = 800
DEFAULT_CHUNK_OVERLAP = 150


@dataclass
class Chunk:
    """Represents a text chunk ready for vector embedding and database insertion."""

    chunk_index: int
    content: str
    metadata: dict  # {"page": int, "source": str, "char_start": int}


def chunk_pages(
    pages: list[ParsedPage],
    filename: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[Chunk]:
    """Chunk extracted pages into overlapping segments with metadata.

    Args:
        pages: List of ParsedPage objects from parser.
        filename: Original filename (stored in chunk metadata['source']).
        chunk_size: Maximum character length per chunk (default: 800).
        chunk_overlap: Overlap in characters between adjacent chunks (default: 150).

    Returns:
        List of Chunk objects with 0-based sequential chunk_index.
    """
    if chunk_overlap >= chunk_size:
        raise ValueError(
            f"chunk_overlap ({chunk_overlap}) must be smaller than chunk_size ({chunk_size})."
        )

    chunks: list[Chunk] = []
    chunk_index = 0

    for page in pages:
        text = page.text.strip()
        if not text:
            continue

        text_len = len(text)
        start = 0

        while start < text_len:
            end = min(start + chunk_size, text_len)

            # Avoid splitting words in half if not at the end of the text
            if end < text_len:
                # Look for a clean boundary (newline or space) within the last 120 chars
                boundary_search_start = max(start, end - 120)
                last_newline = text.rfind("\n", boundary_search_start, end)
                last_space = text.rfind(" ", boundary_search_start, end)
                best_break = max(last_newline, last_space)

                if best_break > start:
                    end = best_break

            chunk_content = text[start:end].strip()

            if chunk_content:
                chunks.append(
                    Chunk(
                        chunk_index=chunk_index,
                        content=chunk_content,
                        metadata={
                            "page": page.page_number,
                            "source": filename,
                            "char_start": start,
                        },
                    )
                )
                chunk_index += 1

            if end >= text_len:
                break

            # Slide window forward, maintaining overlap
            next_start = end - chunk_overlap
            start = next_start if next_start > start else end

    return chunks
