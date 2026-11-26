# ── Atlas Enterprise AI — Production Dockerfile ───────────────────────────────
# Multi-stage build: builder installs deps, final image stays lean.
# Base: Python 3.12-slim (matches AGENTS.md requirement).

# ─── Stage 1: dependency builder ──────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

# Install build tools needed for psycopg binary wheels
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ─── Stage 2: production image ────────────────────────────────────────────────
FROM python:3.12-slim AS final

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

LABEL org.opencontainers.image.title="Atlas Enterprise AI" \
      org.opencontainers.image.description="Zero-cost self-hosted Enterprise RAG assistant"

# Non-root user for security
RUN addgroup --system atlas && adduser --system --ingroup atlas atlas

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application source
COPY app/ ./app/
COPY frontend/ ./frontend/

# Set ownership
RUN chown -R atlas:atlas /app

USER atlas

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["sh", "-c", "python -c \"import os, urllib.request; urllib.request.urlopen(f'http://localhost:{os.environ.get(\\\"PORT\\\", \\\"8000\\\")}/health')\""]

# Bind dynamic $PORT (Render sets PORT=10000; local defaults to 8000) and honor proxy headers
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --proxy-headers --forwarded-allow-ips '*'"]
