# System Architecture — Atlas Enterprise AI

This document is the architectural source of truth for **Atlas Enterprise AI**. AI coding agents must follow these service boundaries, data flows, LangGraph node contracts, and folder conventions strictly.

---

## 1. High-Level Topology (5 Layers)

```text
[Client Layer]
  Browser UI (Vanilla HTML/CSS/JS served by FastAPI on :8000)
      │                                     │
      │ HTTP/JSON (API + Static UI)         │ Direct PUT (Presigned URL)
      ▼                                     ▼
[Gateway & Engines — Docker Compose]   [Storage & Databases — Docker]
  FastAPI Gateway (:8000) ─────────────► RustFS (:9000 API / :9001 UI)
    ├── Document Ingestion Engine ─────► PostgreSQL 16 + pgvector (:5432)
    └── LangGraph RAG + Guardrails ────► (Metadata, Vectors, Memory, HITL, Audit)
            │           │
            │           └──────────────► [Telemetry — Docker]
            ▼                             Langfuse Self-Hosted (:3000)
[LLMs & Host Services]
  ├── Native Host Ollama (:11434) — http://host.docker.internal:11434 (`nomic-embed-text`, 768-dim)
  └── Google Gemini API (Free Tier) — Grounded answer synthesis
```

---

## 2. Docker Compose Services & Port Map

Local infrastructure runs on a single Docker bridge network (`atlas-net`). **Ollama runs natively on the host OS (CPU-only optimization); do NOT add an `ollama` service to `docker-compose.yml`.** FastAPI connects to host Ollama via `http://host.docker.internal:11434` using `extra_hosts`.

| Service Name | Image / Build | Internal Address | Host Port | Responsibility |
| :--- | :--- | :--- | :--- | :--- |
| `api` | `./Dockerfile` (Python 3.12) | `http://api:8000` | `8000` | FastAPI Gateway, serves static Web UI (`/`), Ingestion Engine, LangGraph RAG Engine. Reaches host Ollama at `http://host.docker.internal:11434`. |
| `postgres` | `pgvector/pgvector:pg16` | `postgres:5432` | `5432` | Unified relational + vector DB (`documents`, `document_chunks`, `chat_messages`, `hitl_approvals`, `audit_logs`, `feedback`). |
| `rustfs` | `rustfs/rustfs:latest` | `http://rustfs:9000` | `9000`, `9001` | S3-compatible object storage for raw uploaded files (`:9000` S3 API, `:9001` web console). |
| `langfuse` | `langfuse/langfuse:2` | `http://langfuse:3000` | `3000` | Lightweight self-hosted observability UI (<500 MB RAM). Uses a dedicated `langfuse` database inside `postgres`. |

### Critical Docker Networking Rule: RustFS Dual-Endpoint Pattern
Because the browser runs on the **host machine** while FastAPI runs **inside Docker**, a presigned URL signed with `http://rustfs:9000` will fail in the browser (DNS cannot resolve `rustfs`).
- **Internal S3 client (`S3_ENDPOINT_URL=http://rustfs:9000`):** Used by FastAPI for bucket initialization, downloading files during ingestion, and deleting files.
- **Public S3 client (`S3_PUBLIC_ENDPOINT_URL=http://localhost:9000`):** Used by FastAPI *only* when calling `generate_presigned_url("put_object", ...)` so the browser can upload directly to RustFS.
- **CORS:** On startup, the backend ensures the RustFS bucket exists and applies a CORS policy allowing `PUT` and `OPTIONS` from `http://localhost:8000`.

---

## 3. Core System Flows

### Flow A: Document Ingestion (Direct-to-Storage Pattern)
Raw files **never** pass through FastAPI endpoints.

1. **Request Upload URL:** Browser calls `POST /api/documents/upload-url` with `{filename, content_type, file_size}`.
2. **Create Pending Record & Sign URL:** FastAPI validates the file extension (`.pdf`, `.txt`, `.md`, `.docx`), inserts a `documents` row with `status = 'pending_upload'`, generates a short-lived presigned PUT URL via the Public S3 client, and returns `{document_id, upload_url, object_key}`.
3. **Direct Browser Upload:** Browser executes `HTTP PUT` directly to `upload_url` (RustFS).
4. **Confirm & Trigger Ingestion:** Browser calls `POST /api/documents/{document_id}/ingest`. FastAPI marks `status = 'processing'` and triggers the ingestion pipeline (via FastAPI `BackgroundTasks` for MVP simplicity).
5. **Process & Index:**
   - **Read:** Download raw bytes from RustFS via the Internal S3 client.
   - **Extract:** Parse text (`pypdf` for PDF, `python-docx` for DOCX, UTF-8 decode for TXT/MD).
   - **Chunk:** Split text into overlapping chunks (default: ~800 chars, 150-char overlap) preserving page/section metadata.
   - **Embed:** Call Ollama (`nomic-embed-text`) in batches to produce `768`-dim vectors.
   - **Upsert:** Insert rows into `document_chunks` (`vector(768)`), update `documents.status = 'indexed'` (or `'failed'` with `error_message`), and write an event to `audit_logs`.

### Flow B: RAG Query & LangGraph State Machine
Every chat message sent to `POST /api/chat` executes a deterministic LangGraph workflow instrumented with a Langfuse callback handler.

```text
START
  │
  ▼
[load_memory]          Load last N turns for session_id from PostgreSQL
  │
  ▼
[input_guardrail]      Deterministic check: prompt injection / blocked topics / sensitive action keywords
  ├── (blocked) ──────► [blocked_response] ──► [persist_turn] ──► END
  │
  ▼ (allowed)
[retrieve]             Embed query via Ollama -> pgvector cosine similarity search (top-K chunks + scores)
  │
  ▼
[generate]             Send system prompt + history + retrieved chunks to Gemini API -> answer + citations
  │
  ▼
[output_guardrail]     Deterministic PII scanner & redactor (emails, phones, SSNs, credit cards)
  │
  ▼
[hitl_check]           Evaluate HITL triggers: sensitive flag OR top retrieval score < threshold
  ├── (needs_review) ─► [queue_hitl] (write pending approval to Postgres) ──► [persist_turn] ──► END
  │
  ▼ (approved)
[persist_turn]         Save user + assistant turns to `chat_messages` & log event to `audit_logs`
  │
  ▼
END
```

#### Flow B Addendum: Gemini Free Tier Compatibility & Context Optimization Strategy
To ensure consistent sub-4s response latency and prevent `504 Deadline Exceeded` timeouts on the Google Gemini Free Tier (`gemini-3.5-flash-lite`, 5 RPM limit), the generation node and state machine adhere to the following deterministic optimizations:
1. **Bounded Retrieval Top-K (`RETRIEVAL_TOP_K=3`):** Reduces chunk volume from 5 to 3 chunks per query, selecting the highest-scoring vector matches.
2. **Configurable Snippet Truncation (`MAX_CHUNK_CHARS=450`):** In `format_retrieved_context`, chunk text snippets are bounded to `settings.max_chunk_chars` on word boundaries to eliminate prompt context bloat while preserving factual grounding and citations.
3. **Compact Active Memory Window:** In `load_memory_node` and `generate_answer`, active conversation history passed to the LLM is constrained to the most recent turn (last 2 messages), with prior assistant answers truncated to 250 characters. Full history remains saved in PostgreSQL `chat_messages`.
4. **Client-Side Timeout and Retries:** `ChatGoogleGenerativeAI` is instantiated with `timeout=120.0` and `max_retries=3` with exponential backoff.

#### LangGraph State Contract (`RAGState`)
- `session_id: str`
- `user_query: str`
- `chat_history: list[dict]`
- `input_blocked: bool` / `block_reason: str | None`
- `is_sensitive_action: bool`
- `retrieved_chunks: list[dict]` *(includes `chunk_id`, `document_id`, `filename`, `content`, `score`)*
- `max_similarity_score: float`
- `draft_answer: str`
- `citations: list[dict]`
- `pii_redacted: bool`
- `requires_hitl: bool` / `hitl_approval_id: str | None`
- `final_response: str`

### Flow C: HITL Approval Lifecycle
1. When `hitl_check` triggers, `queue_hitl` inserts a record into `hitl_approvals` (`status = 'pending'`) containing the query, retrieved sources, draft answer, and trigger reason.
2. FastAPI returns a structured pending response to the user UI (`status: "pending_approval"`, `approval_id`).
3. A Reviewer opens the **Approvals** tab in the Web UI, inspects the draft and sources, and submits `POST /api/approvals/{approval_id}/decision` (`approve`, `edit_and_approve`, or `reject` with optional notes).
4. FastAPI updates `hitl_approvals`, appends the final approved message to `chat_messages` (if approved), and records the reviewer action in `audit_logs`.

### Flow D: Atomic Document Deletion
When `DELETE /api/documents/{document_id}` is called:
1. FastAPI deletes the raw object from RustFS (ignoring `404` if already absent).
2. FastAPI executes `DELETE FROM documents WHERE id = :id`.
3. PostgreSQL `ON DELETE CASCADE` atomically removes all associated rows in `document_chunks`.
4. FastAPI writes a `document_deleted` event to `audit_logs`.

---

## 4. Observability & CI/CD

### Observability (Self-Hosted Langfuse)
- LangGraph invokes the Langfuse `CallbackHandler` asynchronously per request, attaching `session_id` and `trace_name="atlas-rag-query"`.
- If `LANGFUSE_ENABLED=false` or Langfuse is unreachable, tracing degrades gracefully without failing user requests.

### Automated Delivery (GitHub Actions -> `ghcr.io`)
- On push to `main` (and pull requests):
  1. Run `ruff check .`, `ruff format --check .`, and `pytest tests/ -v`.
  2. On `main` merges only: build the production Docker image (`Dockerfile`) and push versioned tags (`latest` and `sha-<short>`) to `ghcr.io/<owner>/atlas-enterprise-ai` using `GITHUB_TOKEN`.

---

## 5. Repository Folder Structure

Keep the codebase modular and flat. Do not create unnecessary abstraction layers.

```text
atlas-enterprise-ai/
├── AGENTS.md                       # Root AI agent instructions
├── .env.example                    # Template for all required env vars
├── docker-compose.yml              # Local $0 stack orchestration
├── Dockerfile                      # Production FastAPI container
├── pyproject.toml                  # Dependencies, ruff, pytest config
├── docs/
│   ├── SCOPE.md
│   ├── ARCHITECTURE.md
│   ├── SCHEMA_AND_API.md
│   ├── PLAN.md
│   └── DECISIONS.md
├── .github/
│   └── workflows/
│       └── ci-cd.yml               # Lint, test, build & push to ghcr.io
├── app/
│   ├── main.py                     # FastAPI entrypoint, lifespan, static mount
│   ├── config.py                   # Pydantic Settings reading from .env
│   ├── db/
│   │   ├── session.py              # SQLAlchemy async/sync engine & session
│   │   ├── models.py               # ORM models + pgvector(768) definitions
│   │   └── init_db.py              # Enable pgvector extension & create tables
│   ├── storage/
│   │   └── s3_client.py            # Dual-endpoint RustFS boto3 wrapper
│   ├── ingestion/
│   │   ├── parser.py               # PDF, DOCX, MD, TXT text extractors
│   │   ├── chunker.py              # Text splitting with overlap & metadata
│   │   ├── embedder.py             # Ollama nomic-embed-text client
│   │   └── pipeline.py             # End-to-end ingestion orchestrator
│   ├── rag/
│   │   ├── state.py                # LangGraph RAGState TypedDict
│   │   ├── guardrails.py           # Deterministic input/output & PII rules
│   │   ├── retriever.py            # pgvector similarity query
│   │   ├── generator.py            # Gemini API grounded prompt + citations
│   │   ├── graph.py                # LangGraph StateGraph assembly
│   │   └── tracing.py              # Optional Langfuse callback factory
│   └── api/
│       ├── routes_documents.py     # Upload URL, ingest, list, delete
│       ├── routes_chat.py          # Chat query, session history, feedback
│       ├── routes_approvals.py     # HITL queue list & decision endpoints
│       └── routes_ops.py           # Health check & audit log viewer
├── frontend/
│   ├── index.html                  # Single-page dashboard shell
│   ├── styles.css                  # Clean minimalist UI styling
│   └── app.js                      # Vanilla JS for upload, chat, HITL, audits
└── tests/
    ├── conftest.py
    ├── test_guardrails.py
    ├── test_ingestion.py
    ├── test_rag_graph.py
    └── test_api.py
```