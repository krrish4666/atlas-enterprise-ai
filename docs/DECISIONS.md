# Architecture Decision Records (ADRs) — Atlas Enterprise AI

This document records the rationale behind every major technical choice in **Atlas Enterprise AI**. AI coding agents must read this before proposing dependency changes or refactors—do **not** revert these deliberate decisions.

---

## ADR-001: Docker Compose for Local Orchestration (Instead of EC2 / Kubernetes / Proxmox Storage)

- **Status:** Accepted
- **Context:** The reference enterprise design uses Amazon EC2. We need a `$0`, reproducible runtime that works identically on a developer laptop or inside a Proxmox VE Linux VM.
- **Decision:** Orchestrate all five runtime services (`api`, `postgres`, `rustfs`, `ollama`, `langfuse`) via a single `docker-compose.yml` on a shared `atlas-net` bridge network.
- **Consequences:**
  - Single-command startup (`docker compose up -d --build`).
  - No Kubernetes manifests, Helm charts, Terraform, or Proxmox-specific storage plugins are permitted in the MVP.

---

## ADR-002: RustFS with Dual-Endpoint Presigned URLs (Instead of AWS S3 or MinIO)

- **Status:** Accepted
- **Context:** The ingestion flow requires S3-compatible presigned PUT URLs so browsers upload raw files directly to object storage without buffering through FastAPI. AWS S3 is paid, and open-source MinIO is deprecated.
- **Decision:** Use **RustFS** (`rustfs/rustfs:latest`, Apache 2.0) accessed through standard `boto3`. Maintain two `boto3` clients: an internal client (`http://rustfs:9000`) for backend reads/deletes, and a public client (`http://localhost:9000`) strictly for signing browser-accessible presigned PUT URLs.
- **Consequences:**
  - Zero-cost, drop-in S3 compatibility that mirrors production AWS S3 code.
  - **Agent Rule:** Never route raw file uploads through FastAPI multipart endpoints, and never sign browser URLs with the internal Docker hostname `rustfs`.

---

## ADR-003: Unified PostgreSQL 16 + `pgvector` (Instead of Separate Pinecone or Qdrant)

- **Status:** Accepted
- **Context:** The system needs relational storage (document metadata, chat history, HITL approvals, feedback, audit logs) and vector similarity search. Running a separate vector database adds RAM overhead and creates distributed consistency bugs when deleting documents.
- **Decision:** Use a single `pgvector/pgvector:pg16` container with an HNSW cosine index on `document_chunks.embedding` (`vector(768)`).
- **Consequences:**
  - One fewer container to operate and debug.
  - **Atomic Deletes:** `ON DELETE CASCADE` from `documents` to `document_chunks` guarantees zero orphaned vectors when a file is deleted.
  - Supports future hybrid search (`pgvector` + Postgres `tsvector`) in one SQL query without adding another service.

---

## ADR-004: Local Ollama `nomic-embed-text` for Embeddings (Instead of Bedrock Titan or Cloud APIs)

- **Status:** Accepted
- **Context:** Chunking and embedding large PDFs generates many consecutive requests, which quickly hits free-tier cloud API rate limits or incurs per-token costs.
- **Decision:** Run **Ollama** locally with `nomic-embed-text` (**768 dimensions**) for both document ingestion and query embedding.
- **Consequences:**
  - Unlimited, zero-cost, CPU-friendly local embeddings with zero API rate-limit failures during bulk uploads.
  - **Agent Rule:** Every vector column, index, and validation check must strictly enforce `768` dimensions. Never swap to OpenAI (`1536`) or Gemini (`768`/`3072`) embedding APIs.

---

## ADR-005: Google Gemini API Free Tier for Chat Generation (Instead of Bedrock Nova or Local Chat LLMs)

- **Status:** Accepted
- **Context:** Running a capable chat LLM locally on a CPU-only machine is slow during iterative development and testing, while AWS Bedrock requires a paid AWS account.
- **Decision:** Use **Google Gemini API (Free Tier)** via `langchain-google-genai` strictly for grounded answer synthesis in the LangGraph `generate` node.
- **Consequences:**
  - Fast response times, strong citation following, and a large context window at `$0`.
  - Embeddings remain local (ADR-004), so Gemini quota is reserved exclusively for chat generation.

---

## ADR-006: Self-Hosted Langfuse v2 (Instead of LangSmith, GlassFlow, or Langfuse v3)

- **Status:** Accepted
- **Context:** We need open-source, self-hosted observability for LangGraph traces. LangSmith is closed-source (self-hosting is paid Enterprise-only), GlassFlow Rius is external, and Langfuse v3 requires a heavy multi-container stack (ClickHouse + Redis + S3 + Worker, ~16 GB RAM).
- **Decision:** Run **Langfuse v2** (`langfuse/langfuse:2`) backed by a dedicated `langfuse` database inside the existing `postgres` container (<500 MB RAM).
- **Consequences:**
  - 100% open-source, self-hosted tracing UI at `http://localhost:3000` with minimal memory footprint.
  - Tracing must fail-open: if `LANGFUSE_ENABLED=false` or Langfuse is temporarily down, chat requests still succeed.

---

## ADR-007: Deterministic Code Guardrails + LangGraph State Machine

- **Status:** Accepted
- **Context:** Relying solely on LLM system prompts to block prompt injections, redact PII, or trigger human approvals is non-deterministic and prone to bypasses.
- **Decision:** Orchestrate every query through an explicit **LangGraph** `StateGraph` with deterministic Python nodes for input safety checks, regex-based output PII redaction, and threshold/keyword-based HITL queueing.
- **Consequences:**
  - Predictable, unit-testable safety guarantees (`tests/test_guardrails.py`).
  - Clear audit trail in `audit_logs` and structured review tickets in `hitl_approvals`.

---

## ADR-008: Vanilla HTML/CSS/JS Served by FastAPI (Instead of React / Next.js / Vite)

- **Status:** Accepted
- **Context:** Adding a JavaScript build toolchain introduces Node.js containers, npm dependency churn, CORS complexity, and extra cognitive load unrelated to mastering Enterprise RAG.
- **Decision:** Serve a clean, single-page vanilla HTML/CSS/JS dashboard (`frontend/`) directly from FastAPI at `/`.
- **Consequences:**
  - Zero frontend build steps; instant reload; single container serves both UI and API.

---

## ADR-009: GitHub Container Registry (`ghcr.io`) via GitHub Actions (Instead of AWS ECR or Docker Hub)

- **Status:** Accepted
- **Context:** Automated CI/CD needs a free container registry without AWS billing or Docker Hub's strict pull rate limits.
- **Decision:** Publish versioned Docker images (`latest` and `sha-<short>`) to `ghcr.io/<owner>/atlas-enterprise-ai` using GitHub Actions and the built-in `GITHUB_TOKEN`.
- **Consequences:**
  - Zero extra registry credentials to manage and no Docker Hub free-tier pull caps for public packages.