"""app/config.py — Pydantic Settings for Atlas Enterprise AI.

All configuration is read from environment variables (populated from .env).
Never hardcode secrets or endpoints here.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Application ────────────────────────────────────────────────────────────
    app_env: str = "development"
    log_level: str = "INFO"

    # ── PostgreSQL ─────────────────────────────────────────────────────────────
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "atlas"
    postgres_user: str = "atlas"
    postgres_password: str = "atlaspassword"
    database_url: str = "postgresql+psycopg://atlas:atlaspassword@localhost:5432/atlas"

    # ── RustFS (S3-compatible) ─────────────────────────────────────────────────
    # Internal: used by FastAPI inside Docker for reads/deletes/bucket init
    s3_endpoint_url: str = "http://rustfs:9000"
    # Public: used ONLY when signing presigned PUT URLs for the browser
    s3_public_endpoint_url: str = "http://localhost:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_bucket_name: str = "atlas-docs"

    # ── Ollama (Local Embeddings) ──────────────────────────────────────────────
    ollama_base_url: str = "http://ollama:11434"
    embed_model: str = "nomic-embed-text"
    embed_dim: int = 768

    # ── Google Gemini API ──────────────────────────────────────────────────────
    google_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash-lite"

    # ── Langfuse (Self-hosted Observability v2) ────────────────────────────────
    langfuse_enabled: bool = True
    langfuse_host: str = "http://langfuse:3000"
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""

    # ── RAG / Guardrail Thresholds ─────────────────────────────────────────────
    retrieval_top_k: int = 3
    hitl_confidence_threshold: float = 0.40
    max_chunk_chars: int = 450


# Single shared instance imported throughout the app
settings = Settings()
