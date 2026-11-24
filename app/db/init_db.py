"""app/db/init_db.py — Database and pgvector extension initialization.

Ensures the pgvector extension is enabled and all application tables
and indexes (including HNSW cosine index) are created on startup.
"""

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.models import Base
from app.db.session import engine as default_engine

logger = logging.getLogger(__name__)


async def init_db(engine: AsyncEngine | None = None) -> None:
    """Initialize pgvector extension and create all tables and indexes."""
    target_engine = engine or default_engine
    logger.info("Initializing database schema and pgvector extension…")

    async with target_engine.begin() as conn:
        # 1. Enable pgvector extension
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))

        # 2. Create tables and declarative indexes
        await conn.run_sync(Base.metadata.create_all)

    logger.info("Database schema initialized successfully.")
