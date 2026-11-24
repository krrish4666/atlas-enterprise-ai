"""tests/test_ingestion.py — Unit tests for document ingestion and Document API.

Tests:
1. Parser: PDF, DOCX, TXT, MD, empty file error handling, unsupported extension error handling.
2. Chunker: text windowing, overlap, metadata preservation, sequential chunk indices.
3. Embedder: Ollama /api/embed client, 768-dim validation, error handling, batching.
4. Document API routes: upload-url, ingest, list, get, delete with mocked dependencies.
"""

import io
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import docx
import pytest
from fastapi.testclient import TestClient

from app.db.models import Document
from app.db.session import get_db
from app.ingestion.chunker import Chunk, chunk_pages
from app.ingestion.embedder import OllamaEmbedder
from app.ingestion.parser import ParsedPage, parse_document
from app.main import app

# ── 1. Parser Tests ───────────────────────────────────────────────────────────


def test_parse_text_file() -> None:
    """UTF-8 text file parsing extracts content into a single page."""
    raw = b"Atlas Enterprise AI is a zero-cost RAG system.\nSecond line."
    pages = parse_document(raw, "notes.txt")
    assert len(pages) == 1
    assert pages[0].page_number == 1
    assert "Atlas Enterprise AI" in pages[0].text
    assert "Second line." in pages[0].text


def test_parse_markdown_file() -> None:
    """Markdown file parsing extracts content into a single page."""
    raw = b"# Title\n\n- Item 1\n- Item 2\n"
    pages = parse_document(raw, "readme.md")
    assert len(pages) == 1
    assert pages[0].page_number == 1
    assert "Item 1" in pages[0].text


def test_parse_docx_file() -> None:
    """DOCX parsing extracts paragraphs into a single page."""
    doc = docx.Document()
    doc.add_heading("Company Policy", level=1)
    doc.add_paragraph("Employees are eligible for 20 days PTO.")
    doc.add_paragraph("Remote work is permitted with supervisor approval.")
    buf = io.BytesIO()
    doc.save(buf)

    pages = parse_document(buf.getvalue(), "handbook.docx")
    assert len(pages) == 1
    assert pages[0].page_number == 1
    assert "Company Policy" in pages[0].text
    assert "20 days PTO" in pages[0].text


def test_parse_pdf_file() -> None:
    """PDF parsing extracts pages with correct 1-indexed page numbers."""
    mock_pdf_bytes = b"%PDF-1.4 mock content"
    with patch("pypdf.PdfReader") as mock_reader_cls:
        mock_p1 = MagicMock()
        mock_p1.extract_text.return_value = "Page 1 Content: Atlas AI"
        mock_p2 = MagicMock()
        mock_p2.extract_text.return_value = "Page 2 Content: Architecture"

        mock_reader = MagicMock()
        mock_reader.pages = [mock_p1, mock_p2]
        mock_reader_cls.return_value = mock_reader

        pages = parse_document(mock_pdf_bytes, "manual.pdf")
        assert len(pages) == 2
        assert pages[0].page_number == 1
        assert "Page 1 Content" in pages[0].text
        assert pages[1].page_number == 2
        assert "Page 2 Content" in pages[1].text


def test_parse_empty_file_raises_error() -> None:
    """Parsing an empty byte string raises ValueError."""
    with pytest.raises(ValueError, match="is empty"):
        parse_document(b"", "empty.txt")


def test_parse_unsupported_extension_raises_error() -> None:
    """Unsupported file formats (.exe, .zip) raise ValueError."""
    with pytest.raises(ValueError, match="Unsupported file format"):
        parse_document(b"binary data", "archive.zip")


# ── 2. Chunker Tests ──────────────────────────────────────────────────────────


def test_chunk_pages_preserves_metadata_and_indices() -> None:
    """Chunks maintain sequential indices, page number, and source filename."""
    pages = [
        ParsedPage(page_number=1, text="First page content."),
        ParsedPage(page_number=2, text="Second page content."),
    ]
    chunks = chunk_pages(pages, filename="test.pdf", chunk_size=800, chunk_overlap=150)

    assert len(chunks) == 2
    assert chunks[0].chunk_index == 0
    assert chunks[0].content == "First page content."
    assert chunks[0].metadata == {"page": 1, "source": "test.pdf", "char_start": 0}

    assert chunks[1].chunk_index == 1
    assert chunks[1].content == "Second page content."
    assert chunks[1].metadata == {"page": 2, "source": "test.pdf", "char_start": 0}


def test_chunk_pages_sliding_window_with_overlap() -> None:
    """Long text is split into multiple overlapping chunks."""
    long_text = "Sentence number " + " ".join(f"word{i}" for i in range(200))
    pages = [ParsedPage(page_number=1, text=long_text)]
    chunks = chunk_pages(pages, filename="long.txt", chunk_size=200, chunk_overlap=50)

    assert len(chunks) > 1
    for i, c in enumerate(chunks):
        assert isinstance(c, Chunk)
        assert c.chunk_index == i
        assert c.metadata["page"] == 1
        assert c.metadata["source"] == "long.txt"
        assert len(c.content) <= 220  # allowed slight boundary margin


def test_chunk_pages_invalid_overlap_raises_error() -> None:
    """chunk_overlap >= chunk_size raises ValueError."""
    pages = [ParsedPage(page_number=1, text="Text")]
    with pytest.raises(ValueError, match="chunk_overlap"):
        chunk_pages(pages, filename="test.txt", chunk_size=100, chunk_overlap=100)


# ── 3. Embedder Tests ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_embedder_returns_768_dim_vectors() -> None:
    """Embedder calls /api/embed and validates 768 dimensions."""
    mock_vector = [0.01] * 768
    embedder = OllamaEmbedder(base_url="http://localhost:11434", model="nomic-embed-text")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"embeddings": [mock_vector, mock_vector]}

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_resp):
        res = await embedder.embed_texts(["Chunk 1", "Chunk 2"])
        assert len(res) == 2
        assert len(res[0]) == 768
        assert len(res[1]) == 768


@pytest.mark.asyncio
async def test_embedder_dimension_mismatch_raises_error() -> None:
    """If Ollama returns incorrect vector dimensions, ValueError is raised."""
    bad_vector = [0.1] * 512  # Mismatch (expected 768)
    embedder = OllamaEmbedder()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"embeddings": [bad_vector]}

    with (
        patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_resp),
        pytest.raises(ValueError, match="Vector dimension mismatch"),
    ):
        await embedder.embed_texts(["Test"])


@pytest.mark.asyncio
async def test_embedder_empty_input_returns_empty_list() -> None:
    """Calling embed_texts with empty list returns [] without HTTP call."""
    embedder = OllamaEmbedder()
    res = await embedder.embed_texts([])
    assert res == []


# ── 4. Document API Routes Tests ──────────────────────────────────────────────


def test_post_upload_url_valid() -> None:
    """POST /api/documents/upload-url returns 201 with public upload URL."""
    client = TestClient(app)
    mock_session = AsyncMock()

    app.dependency_overrides[get_db] = lambda: mock_session
    try:
        with patch("app.storage.s3_client.storage.generate_presigned_put_url") as mock_gen:
            mock_gen.return_value = "http://localhost:9000/atlas-docs/uploads/test.pdf?sig=123"

            payload = {
                "filename": "handbook.pdf",
                "content_type": "application/pdf",
                "file_size": 10240,
            }
            resp = client.post("/api/documents/upload-url", json=payload)

            assert resp.status_code == 201
            data = resp.json()
            assert "document_id" in data
            assert data["object_key"].endswith("handbook.pdf")
            assert "http://localhost:9000" in data["upload_url"]
            assert data["expires_in"] == 300
            assert mock_session.commit.called
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_post_upload_url_unsupported_format() -> None:
    """POST /api/documents/upload-url rejects unsupported file extensions with 400."""
    client = TestClient(app)
    payload = {
        "filename": "payload.exe",
        "content_type": "application/octet-stream",
        "file_size": 2048,
    }
    resp = client.post("/api/documents/upload-url", json=payload)
    assert resp.status_code == 400
    assert "Unsupported file format" in resp.json()["detail"]


def test_get_documents_list() -> None:
    """GET /api/documents returns list of document records."""
    client = TestClient(app)
    doc_id = uuid.uuid4()
    mock_doc = Document(
        id=doc_id,
        filename="policy.pdf",
        content_type="application/pdf",
        file_size=5000,
        object_key=f"uploads/{doc_id}/policy.pdf",
        status="indexed",
        chunk_count=12,
        error_message=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_doc]
    mock_session.execute.return_value = mock_result

    app.dependency_overrides[get_db] = lambda: mock_session
    try:
        resp = client.get("/api/documents")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["documents"]) == 1
        item = data["documents"][0]
        assert item["id"] == str(doc_id)
        assert item["filename"] == "policy.pdf"
        assert item["status"] == "indexed"
        assert item["chunk_count"] == 12
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_get_document_by_id_found() -> None:
    """GET /api/documents/{id} returns single document detail."""
    client = TestClient(app)
    doc_id = uuid.uuid4()
    mock_doc = Document(
        id=doc_id,
        filename="guide.md",
        content_type="text/markdown",
        file_size=1200,
        object_key=f"uploads/{doc_id}/guide.md",
        status="indexed",
        chunk_count=3,
        error_message=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_doc
    mock_session.execute.return_value = mock_result

    app.dependency_overrides[get_db] = lambda: mock_session
    try:
        resp = client.get(f"/api/documents/{doc_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == str(doc_id)
        assert data["filename"] == "guide.md"
        assert data["status"] == "indexed"
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_get_document_by_id_not_found() -> None:
    """GET /api/documents/{id} returns 404 when document does not exist."""
    client = TestClient(app)
    doc_id = uuid.uuid4()

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute.return_value = mock_result

    app.dependency_overrides[get_db] = lambda: mock_session
    try:
        resp = client.get(f"/api/documents/{doc_id}")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_post_ingest_enqueues_background_task() -> None:
    """POST /api/documents/{id}/ingest transitions status to processing and returns 202."""
    client = TestClient(app)
    doc_id = uuid.uuid4()
    mock_doc = Document(
        id=doc_id,
        filename="doc.pdf",
        content_type="application/pdf",
        file_size=4096,
        object_key=f"uploads/{doc_id}/doc.pdf",
        status="pending_upload",
        chunk_count=0,
    )

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_doc
    mock_session.execute.return_value = mock_result

    app.dependency_overrides[get_db] = lambda: mock_session
    try:
        with patch("app.api.routes_documents.run_ingestion_pipeline") as mock_pipeline:
            resp = client.post(f"/api/documents/{doc_id}/ingest")
            assert resp.status_code == 202
            data = resp.json()
            assert data["document_id"] == str(doc_id)
            assert data["status"] == "processing"
            assert mock_session.commit.called
            # Verify background task was executed
            mock_pipeline.assert_called_once_with(doc_id)
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_delete_document_success() -> None:
    """DELETE /api/documents/{id} deletes object from storage and row from DB."""
    client = TestClient(app)
    doc_id = uuid.uuid4()
    mock_doc = Document(
        id=doc_id,
        filename="remove_me.pdf",
        content_type="application/pdf",
        file_size=2048,
        object_key=f"uploads/{doc_id}/remove_me.pdf",
        status="indexed",
        chunk_count=5,
    )

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_doc
    mock_session.execute.return_value = mock_result

    app.dependency_overrides[get_db] = lambda: mock_session
    try:
        with patch("app.storage.s3_client.storage.delete_object") as mock_del:
            resp = client.delete(f"/api/documents/{doc_id}")
            assert resp.status_code == 200
            data = resp.json()
            assert data["deleted"] is True
            assert data["document_id"] == str(doc_id)
            mock_del.assert_called_once_with(f"uploads/{doc_id}/remove_me.pdf")
            assert mock_session.delete.called
            assert mock_session.commit.called
    finally:
        app.dependency_overrides.pop(get_db, None)
