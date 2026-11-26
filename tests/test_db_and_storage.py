"""tests/test_db_and_storage.py — Phase 1 tests for DB models, schema, and S3 client.

Tests:
1. ORM model definitions and constraints (table names, columns, 768-dim vector).
2. Foreign key cascade delete invariants (ON DELETE CASCADE, ON DELETE SET NULL).
3. HNSW index definition on document_chunks.embedding.
4. S3Storage dual-endpoint separation (presigned URL uses public endpoint; ops use internal).
5. S3Storage bucket creation, CORS configuration, put, get, and delete operations.
"""

from unittest.mock import MagicMock, patch

from app.db.models import (
    Base,
)
from app.storage.s3_client import S3Storage

# ── Database Model & Schema Tests ─────────────────────────────────────────────


def test_models_registered_in_metadata() -> None:
    """All 6 tables defined in docs/SCHEMA_AND_API.md must exist in Base.metadata."""
    table_names = set(Base.metadata.tables.keys())
    expected = {
        "documents",
        "document_chunks",
        "chat_messages",
        "hitl_approvals",
        "feedback",
        "audit_logs",
    }
    assert expected.issubset(table_names), f"Missing tables: {expected - table_names}"


def test_document_table_columns() -> None:
    """Document table must have required columns and types."""
    table = Base.metadata.tables["documents"]
    col_names = {c.name for c in table.columns}
    required = {
        "id",
        "filename",
        "content_type",
        "file_size",
        "object_key",
        "status",
        "chunk_count",
        "error_message",
        "created_at",
        "updated_at",
    }
    assert required.issubset(col_names)
    assert table.columns["object_key"].unique is True


def test_document_chunks_vector_dimension_is_768() -> None:
    """Vector column on document_chunks must be strictly 768 dimensions."""
    table = Base.metadata.tables["document_chunks"]
    embedding_col = table.columns["embedding"]
    assert embedding_col.type.dim == 768


def test_document_chunks_on_delete_cascade() -> None:
    """document_chunks -> documents FK must specify ON DELETE CASCADE for atomic deletion."""
    table = Base.metadata.tables["document_chunks"]
    fk = list(table.foreign_keys)[0]
    assert fk.column.table.name == "documents"
    assert fk.ondelete is not None
    assert fk.ondelete.upper() == "CASCADE"


def test_feedback_on_delete_cascade() -> None:
    """feedback -> chat_messages FK must specify ON DELETE CASCADE."""
    table = Base.metadata.tables["feedback"]
    fk = list(table.foreign_keys)[0]
    assert fk.column.table.name == "chat_messages"
    assert fk.ondelete is not None
    assert fk.ondelete.upper() == "CASCADE"


def test_hitl_approval_on_delete_set_null() -> None:
    """hitl_approvals -> chat_messages FK must specify ON DELETE SET NULL."""
    table = Base.metadata.tables["hitl_approvals"]
    fk = list(table.foreign_keys)[0]
    assert fk.column.table.name == "chat_messages"
    assert fk.ondelete is not None
    assert fk.ondelete.upper() == "SET NULL"


def test_hnsw_cosine_index_exists() -> None:
    """document_chunks must have an HNSW index configured with vector_cosine_ops."""
    table = Base.metadata.tables["document_chunks"]
    hnsw_index = next(
        (idx for idx in table.indexes if idx.name == "idx_document_chunks_embedding_hnsw"),
        None,
    )
    assert hnsw_index is not None, "Missing idx_document_chunks_embedding_hnsw index"
    assert hnsw_index.dialect_options["postgresql"]["using"] == "hnsw"
    assert hnsw_index.dialect_options["postgresql"]["ops"]["embedding"] == "vector_cosine_ops"


def test_indexes_on_chat_and_audit() -> None:
    """Verify lookup indexes for chat messages, hitl, and audit logs."""
    chat_table = Base.metadata.tables["chat_messages"]
    assert any(idx.name == "idx_chat_messages_session_created" for idx in chat_table.indexes)

    hitl_table = Base.metadata.tables["hitl_approvals"]
    assert any(idx.name == "idx_hitl_approvals_status_created" for idx in hitl_table.indexes)

    audit_table = Base.metadata.tables["audit_logs"]
    assert any(idx.name == "idx_audit_logs_created_at" for idx in audit_table.indexes)


# ── Dual-Endpoint RustFS S3 Storage Tests ────────────────────────────────────


def test_s3_storage_initializes_two_endpoints() -> None:
    """S3Storage must create separate clients for internal and public endpoints."""
    storage = S3Storage(
        internal_endpoint="http://rustfs:9000",
        public_endpoint="http://localhost:9000",
        access_key="minioadmin",
        secret_key="minioadmin",
        bucket_name="test-bucket",
        region_name="us-west-004",
        addressing_style="path",
    )
    assert storage.internal_endpoint == "http://rustfs:9000"
    assert storage.public_endpoint == "http://localhost:9000"
    assert storage.bucket_name == "test-bucket"
    assert storage.region_name == "us-west-004"
    assert storage.addressing_style == "path"
    assert storage.internal_client.meta.region_name == "us-west-004"
    assert storage.public_client.meta.config.s3["addressing_style"] == "path"
    assert storage.public_client.meta.config.signature_version == "s3v4"


def test_ensure_bucket_exists_skips_create_when_present() -> None:
    """When head_bucket succeeds (e.g. pre-created Backblaze B2 bucket), create_bucket is skipped."""
    storage = S3Storage(
        internal_endpoint="https://s3.us-west-004.backblazeb2.com",
        public_endpoint="https://s3.us-west-004.backblazeb2.com",
        access_key="b2key",
        secret_key="b2secret",
        bucket_name="atlas-docs",
        region_name="us-west-004",
    )
    with (
        patch.object(storage.internal_client, "head_bucket") as mock_head,
        patch.object(storage.internal_client, "create_bucket") as mock_create,
    ):
        storage.ensure_bucket_exists()
        mock_head.assert_called_once_with(Bucket="atlas-docs")
        mock_create.assert_not_called()


def test_generate_presigned_put_url_uses_public_endpoint() -> None:
    """Presigned PUT URL must be generated by the public client pointing to localhost:9000."""
    storage = S3Storage(
        internal_endpoint="http://rustfs:9000",
        public_endpoint="http://localhost:9000",
        access_key="minioadmin",
        secret_key="minioadmin",
        bucket_name="atlas-docs",
    )

    with patch.object(
        storage.public_client,
        "generate_presigned_url",
        return_value="http://localhost:9000/atlas-docs/test.pdf?X-Amz-Signature=abc",
    ) as mock_gen:
        url = storage.generate_presigned_put_url(
            object_key="uploads/123/test.pdf",
            content_type="application/pdf",
            expires_in=300,
        )

        mock_gen.assert_called_once_with(
            ClientMethod="put_object",
            Params={
                "Bucket": "atlas-docs",
                "Key": "uploads/123/test.pdf",
                "ContentType": "application/pdf",
            },
            ExpiresIn=300,
        )
        assert "http://localhost:9000" in url


def test_ensure_bucket_exists_creates_when_missing() -> None:
    """When bucket does not exist (404), ensure_bucket_exists calls create_bucket."""
    storage = S3Storage(
        internal_endpoint="http://rustfs:9000",
        public_endpoint="http://localhost:9000",
        access_key="minioadmin",
        secret_key="minioadmin",
        bucket_name="atlas-docs",
    )

    from botocore.exceptions import ClientError

    not_found_err = ClientError(
        {"Error": {"Code": "404", "Message": "Not Found"}},
        "HeadBucket",
    )

    with (
        patch.object(storage.internal_client, "head_bucket", side_effect=not_found_err),
        patch.object(storage.internal_client, "create_bucket") as mock_create,
    ):
        storage.ensure_bucket_exists()
        mock_create.assert_called_once_with(Bucket="atlas-docs")


def test_configure_bucket_cors() -> None:
    """configure_bucket_cors applies CORS configuration allowing PUT from localhost:8000."""
    storage = S3Storage(
        internal_endpoint="http://rustfs:9000",
        public_endpoint="http://localhost:9000",
        access_key="minioadmin",
        secret_key="minioadmin",
        bucket_name="atlas-docs",
    )

    with patch.object(storage.internal_client, "put_bucket_cors") as mock_cors:
        storage.configure_bucket_cors()
        mock_cors.assert_called_once()
        call_kwargs = mock_cors.call_args.kwargs
        assert call_kwargs["Bucket"] == "atlas-docs"
        rules = call_kwargs["CORSConfiguration"]["CORSRules"]
        assert any("http://localhost:8000" in r["AllowedOrigins"] for r in rules)
        assert any("PUT" in r["AllowedMethods"] for r in rules)


def test_get_put_delete_object_bytes() -> None:
    """Verify internal client get, put, and delete methods."""
    storage = S3Storage(
        internal_endpoint="http://rustfs:9000",
        public_endpoint="http://localhost:9000",
        access_key="minioadmin",
        secret_key="minioadmin",
        bucket_name="atlas-docs",
    )

    # Test put
    with patch.object(storage.internal_client, "put_object") as mock_put:
        storage.put_object_bytes("test.txt", b"hello world", "text/plain")
        mock_put.assert_called_once()

    # Test get
    mock_body = MagicMock()
    mock_body.read.return_value = b"hello world"
    with patch.object(storage.internal_client, "get_object", return_value={"Body": mock_body}):
        data = storage.get_object_bytes("test.txt")
        assert data == b"hello world"

    # Test delete
    with patch.object(storage.internal_client, "delete_object") as mock_del:
        ok = storage.delete_object("test.txt")
        assert ok is True
        mock_del.assert_called_once_with(Bucket="atlas-docs", Key="test.txt")
