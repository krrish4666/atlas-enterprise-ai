# Implementation Plan — Atlas Enterprise AI

This document is the living build tracker for **Atlas Enterprise AI**. AI coding agents must check the **Active Phase** before writing code, complete tasks in order, run the phase verification gate, and mark items `[x]` before ending a session.

---

## Current Status

- **Active Phase:** Phase 3 Complete (Awaiting user command to proceed to Phase 4)
- **Last Completed Milestone:** Phase 3 — Core RAG Engine, Memory & Langfuse Tracing (LangGraph state machine, pgvector cosine retrieval, multi-turn session memory, feedback & live stack verified)
- **Blockers / Notes:** None

---

## Phase 0: Repository Bootstrap & Local Infrastructure

**Goal:** Get the entire `$0` Docker Compose stack running locally with a healthy FastAPI skeleton.

- [x] Create `.gitignore`, `.cursorignore`, and `.env.example` (covering Postgres, RustFS internal/public endpoints, Ollama, Gemini API key, Langfuse keys/DB, and guardrail thresholds).
- [x] Create `pyproject.toml` with pinned dependencies (`fastapi`, `uvicorn`, `sqlalchemy`, `psycopg[binary]`, `pgvector`, `boto3`, `pypdf`, `python-docx`, `langgraph`, `langchain-google-genai`, `langfuse`, `httpx`, `pydantic-settings`) and dev tools (`pytest`, `ruff`).
- [x] Create `Dockerfile` (Python 3.12-slim) for the `api` service.
- [x] Create `docker-compose.yml` with the 4 locked services on `atlas-net` (Ollama runs natively on host OS for CPU optimization; do NOT containerize Ollama):
  - `postgres` (`pgvector/pgvector:pg16`) with an init script that creates both `atlas` and `langfuse` databases.
  - `rustfs` (`rustfs/rustfs:latest`) exposing `:9000` (S3 API) and `:9001` (console).
  - `langfuse` (`langfuse/langfuse:2`) exposing `:3000` backed by the `langfuse` Postgres DB.
  - `api` built from `./Dockerfile` exposing `:8000` (connects to host Ollama via `http://host.docker.internal:11434`).
- [x] Create `app/config.py` (`pydantic-settings`) and `app/main.py` with FastAPI lifespan hook and `GET /api/health`.
- [x] Add `tests/conftest.py` and a basic health endpoint smoke test.

**Exit Gate:**
- [x] `docker compose up -d --build` starts all 4 containers cleanly (`postgres`, `rustfs`, `langfuse`, `api`).
- [x] `curl http://localhost:8000/api/health` returns `200 OK` with `postgres`, `rustfs`, and `ollama` reporting `"up"`.
- [x] `ruff check . && ruff format --check . && pytest tests/ -v` passes.

---

## Phase 1: Database Schema & Dual-Endpoint RustFS Storage

**Goal:** Lock down PostgreSQL + `pgvector` tables and verify direct browser-compatible RustFS presigned PUT URLs.

- [x] Implement `app/db/session.py` (SQLAlchemy engine, session dependency, and helper utilities).
- [x] Implement `app/db/models.py` matching `docs/SCHEMA_AND_API.md` exactly:
  - `documents`, `document_chunks` (`vector(768)` + `ON DELETE CASCADE`), `chat_messages`, `hitl_approvals`, `feedback`, `audit_logs`.
- [x] Implement `app/db/init_db.py` to run `CREATE EXTENSION IF NOT EXISTS vector;`, create tables, and ensure the HNSW cosine index (`idx_document_chunks_embedding_hnsw`) and lookup indexes exist on startup.
- [x] Implement `app/storage/s3_client.py` using `boto3` with the **dual-endpoint pattern**:
  - Internal client (`S3_ENDPOINT_URL`) for `ensure_bucket_exists()`, `configure_bucket_cors()`, `get_object_bytes()`, and `delete_object()`.
  - Public client (`S3_PUBLIC_ENDPOINT_URL`) strictly for `generate_presigned_put_url()`.
- [x] Add unit tests verifying schema creation, `ON DELETE CASCADE` behavior, and presigned URL generation using the public endpoint.

**Exit Gate:**
- [x] Starting `api` initializes all 6 tables and `pgvector` HNSW index in Postgres and creates the `atlas-docs` bucket with CORS in RustFS.
- [x] `ruff check . && ruff format --check . && pytest tests/ -v` passes.

---

## Phase 2: Document Ingestion Pipeline & Document API

**Goal:** Complete Flow A (Presigned URL upload -> RustFS -> Parse -> Chunk -> Ollama Embed -> `pgvector`) and Flow D (Atomic Delete).

- [x] Implement `app/ingestion/parser.py` for `.pdf` (`pypdf`), `.docx` (`python-docx`), `.md`, and `.txt` with clean error handling for empty/corrupt files.
- [x] Implement `app/ingestion/chunker.py` (~800 chars, 150-char overlap) preserving `page`, `source`, and `char_start` metadata.
- [x] Implement `app/ingestion/embedder.py` calling Ollama `/api/embed` (`nomic-embed-text`) and validating `len(vector) == 768`.
- [x] Implement `app/ingestion/pipeline.py` to orchestrate download -> parse -> chunk -> embed -> upsert `document_chunks` -> update `documents.status` (`indexed` or `failed`) -> write `audit_logs`.
- [x] Implement `app/api/routes_documents.py`:
  - `POST /api/documents/upload-url`
  - `POST /api/documents/{document_id}/ingest` (runs pipeline via `BackgroundTasks`)
  - `GET /api/documents`
  - `GET /api/documents/{document_id}`
  - `DELETE /api/documents/{document_id}` (RustFS delete + Postgres `ON DELETE CASCADE` + audit log)
- [x] Add `tests/test_ingestion.py` (mocking Ollama/RustFS for fast unit tests + optional integration test).

**Exit Gate:**
- [x] End-to-end manual test: request presigned URL -> `curl -X PUT` a sample PDF directly to RustFS -> trigger `/ingest` -> verify chunks & 768-dim vectors in Postgres -> `DELETE` removes file + chunks atomically.
- [x] `ruff check . && ruff format --check . && pytest tests/ -v` passes.

---

## Phase 3: Core RAG Engine, Memory & Langfuse Tracing

**Goal:** Build the grounded Q&A pipeline with multi-turn session memory and Langfuse observability.

- [x] Implement `app/rag/state.py` defining `RAGState` (`TypedDict`) matching `docs/ARCHITECTURE.md`.
- [x] Implement `app/rag/retriever.py` executing `pgvector` cosine similarity search (`1 - (embedding <=> :query_vector)`) returning top-K chunks, similarity scores, and document filenames.
- [x] Implement `app/rag/generator.py` using `langchain-google-genai` (Gemini API Free Tier) with strict grounding instructions, inline citation formatting (`[1]`, `[2]`), and graceful "not found in uploaded documents" fallback.
- [x] Implement `app/rag/tracing.py` creating the optional Langfuse `CallbackHandler` (gracefully no-ops when `LANGFUSE_ENABLED=false` or Langfuse is unreachable).
- [x] Implement initial `app/rag/graph.py` wiring `load_memory -> retrieve -> generate -> persist_turn`.
- [x] Implement `app/api/routes_chat.py`:
  - `POST /api/chat`
  - `GET /api/chat/sessions`
  - `GET /api/chat/sessions/{session_id}/messages`
  - `POST /api/chat/feedback`

**Exit Gate:**
- [x] Asking a question over an indexed PDF returns a grounded answer with citations.
- [x] Asking a follow-up question in the same `session_id` resolves context from `chat_messages`.
- [x] Execution trace appears in self-hosted Langfuse (`http://localhost:3000`).
- [x] `ruff check . && ruff format --check . && pytest tests/ -v` passes.

---

## Phase 4: Deterministic Guardrails, HITL Approvals & Auditing

**Goal:** Add enterprise safety controls, human-in-the-loop review workflows, and operational audit logs.

- [ ] Implement `app/rag/guardrails.py` with deterministic rules:
  - Input checks: prompt-injection patterns, blocked topics, and sensitive/action-like request detection.
  - Output checks: regex-based PII redaction (emails, phone numbers, SSNs, credit cards) replacing matches with `[REDACTED_<TYPE>]`.
  - HITL trigger evaluation: `is_sensitive_action == True` OR `max_similarity_score < HITL_CONFIDENCE_THRESHOLD`.
- [ ] Upgrade `app/rag/graph.py` to the full state machine:
  - `load_memory -> input_guardrail -> (blocked_response | retrieve -> generate -> output_guardrail -> hitl_check -> [queue_hitl]) -> persist_turn`.
- [ ] Implement `app/api/routes_approvals.py`:
  - `GET /api/approvals?status=pending`
  - `POST /api/approvals/{approval_id}/decision` (enforcing `X-User-Role: reviewer|admin`, updating `hitl_approvals` & `chat_messages`, and logging `hitl_decided`).
- [ ] Implement `app/api/routes_ops.py`:
  - `GET /api/health` (moved/refined here)
  - `GET /api/audit-logs?limit=50&event_type=...`
- [ ] Add `tests/test_guardrails.py` and `tests/test_rag_graph.py` covering prompt injection blocking, PII redaction, low-confidence HITL queueing, and reviewer approval decisions.

**Exit Gate:**
- Prompt injections are blocked and logged in `audit_logs`.
- Simulated PII in output is redacted before reaching the API response.
- Low-confidence / sensitive queries return `status: "pending_approval"` and can be approved/edited/rejected via `/api/approvals/{id}/decision`.
- `ruff check . && ruff format --check . && pytest tests/ -v` passes.

---

## Phase 5: Lightweight Web Dashboard (Vanilla HTML/CSS/JS)

**Goal:** Build the single-page enterprise dashboard served directly by FastAPI with zero frontend build tools.

- [ ] Mount `frontend/` static files in `app/main.py` at `/`.
- [ ] Build `frontend/index.html` and `frontend/styles.css` with a clean, responsive layout and top navigation tabs:
  - **Chat** (session sidebar, message thread, expandable citation badges, thumbs-up/down feedback, pending-approval badge)
  - **Documents** (file picker, upload progress bar via direct PUT to RustFS presigned URL, status polling, atomic delete button)
  - **Approvals (HITL)** (role selector `Employee` / `Reviewer`, pending queue cards with draft answer + sources, Approve / Edit & Approve / Reject controls)
  - **Operations & Audits** (live service health pills + filterable audit log table + link to Langfuse UI)
- [ ] Build `frontend/app.js` connecting all views cleanly to the contracts in `docs/SCHEMA_AND_API.md`.

**Exit Gate:**
- Every user flow in `docs/SCOPE.md` (Section 6: Acceptance Criteria #1–#7) works interactively through `http://localhost:8000` with zero browser console errors.

---

## Phase 6: End-to-End Tests, CI/CD (`ghcr.io`) & Documentation Polish

**Goal:** Automate testing and Docker image publishing to GitHub Container Registry.

- [ ] Complete `tests/test_api.py` covering all FastAPI endpoints, error cases (`400`, `403`, `404`), and role enforcement.
- [ ] Create `.github/workflows/ci-cd.yml`:
  - On PR & push to `main`: run `ruff check .`, `ruff format --check .`, and `pytest tests/ -v`.
  - On push to `main`: build `Dockerfile` and push `latest` and `sha-<short>` tags to `ghcr.io/<owner>/atlas-enterprise-ai` using `GITHUB_TOKEN`.
- [ ] Write a concise root `README.md` with quickstart instructions (`cp .env.example .env`, `docker compose up -d --build`, `ollama pull nomic-embed-text`).
- [ ] Verify all 8 Acceptance Criteria in `docs/SCOPE.md`.

**Exit Gate:**
- Full test suite passes locally and in GitHub Actions.
- Docker image successfully publishes to `ghcr.io`.