# AGENTS.md

## Project Overview
Atlas Enterprise AI is a zero-cost, self-hosted Enterprise RAG assistant over private organizational documents. It provides document-grounded Q&A with citations, multi-turn chat memory, deterministic input/output guardrails, human-in-the-loop (HITL) approvals, audit logging, and execution tracing.

## Commands
Run every command from the repository root. Prefer file-scoped checks while iterating; run the full suite before finishing a task.

- Start full stack: `docker compose up -d --build`
- Stop stack: `docker compose down`
- View API logs: `docker compose logs -f api`
- Lint single file: `ruff check path/to/file.py`
- Format single file: `ruff format path/to/file.py`
- Test single file: `pytest tests/path/to/test_file.py -v`
- Full verification (run before completing any task):
  ```bash
  ruff check . && ruff format --check . && pytest tests/ -v
  ```

## Locked Tech Stack & Hard Boundaries
Do **not** swap these components, introduce paid cloud services, or add heavy frameworks without explicit user approval:

- **Backend & Orchestration:** Python + FastAPI + LangGraph.
- **Object Storage:** RustFS (Docker, S3-compatible) accessed via `boto3`. Never use AWS S3 or deprecated MinIO.
- **Database & Vector Store:** Single PostgreSQL 16 instance with `pgvector`. Never add Pinecone, Qdrant, Chroma, or Redis unless explicitly requested.
- **Embeddings:** Ollama running natively on the host OS (`http://host.docker.internal:11434` from Docker, `http://localhost:11434` from host) running `nomic-embed-text` (**768 dimensions**). Ollama runs natively on the host OS (CPU-only optimization); do NOT add an `ollama` service to `docker-compose.yml`. Never use OpenAI, Bedrock, or Gemini embedding APIs.
- **Chat LLM:** Google Gemini API (Free Tier) via `langchain-google-genai`. Never use AWS Bedrock or OpenAI.
- **Observability:** Self-hosted Langfuse (Docker). Never swap to LangSmith or GlassFlow.
- **Frontend:** Lightweight vanilla HTML, CSS, and JavaScript. Never scaffold React, Next.js, Vue, or Tailwind build pipelines.
- **Container Registry:** GitHub Container Registry (`ghcr.io`) via GitHub Actions. Never configure AWS ECR.

## Architectural Invariants (Silent Failure Prevention)
- **Direct-to-Storage Uploads:** Files must upload directly from the browser to RustFS via presigned PUT URLs. Never stream or buffer raw uploaded files through FastAPI endpoints.
- **Vector Dimensions:** All `pgvector` columns and indexes must match `nomic-embed-text` (`vector(768)`).
- **Atomic Document Deletion:** Foreign keys from `document_chunks` to `documents` must use `ON DELETE CASCADE` so deleting a document removes its vectors atomically.
- **Guardrails & HITL:** Every user query and LLM response must pass through LangGraph guardrail nodes. Low-confidence or sensitive/action-like requests must pause and write to the PostgreSQL HITL approval queue instead of returning an unapproved answer.
- **Secrets:** Never hardcode API keys, credentials, or tokens. Read all config from environment variables documented in `.env.example`.

## Documentation Index (Progressive Disclosure)
Read the relevant doc **before** editing that layer. Do not guess schemas, endpoints, or scope.

| When working on... | Read first |
| :--- | :--- |
| Feature scope, requirements, or what is out of bounds | `docs/SCOPE.md` |
| System flows, ports, Docker services, or LangGraph nodes | `docs/ARCHITECTURE.md` |
| PostgreSQL tables, `pgvector` indexes, or FastAPI routes | `docs/SCHEMA_AND_API.md` |
| Current build phase, next tasks, and completed milestones | `docs/PLAN.md` |
| Rationale behind locked tool/model choices | `docs/DECISIONS.md` |

## Workflow & Definition of Done
1. Check `docs/PLAN.md` to confirm the active phase and task before writing code.
2. Keep diffs small and focused on the current task; avoid unrelated refactors.
3. Add or update `pytest` tests for every new endpoint, ingestion step, guardrail, or LangGraph node.
4. Run `ruff check . && ruff format --check . && pytest tests/ -v` and fix all failures.
5. Update `docs/PLAN.md` (mark completed items) and `.env.example` (if new env vars were added) before concluding.
## Reference Repository Policy
- `_reference/atlas-upstream/` contains the original AWS/Pinecone implementation for reference ONLY.
- NEVER modify files inside `_reference/` and NEVER import from it.
- You MAY adapt its FastAPI route patterns, HTML/CSS/JS UI, LangGraph node topology, guardrail rules, and prompt templates.
- Whenever `_reference/atlas-upstream/` conflicts with `docs/ARCHITECTURE.md` or `docs/DECISIONS.md` (e.g., S3, Bedrock, Pinecone, GlassFlow), ALWAYS follow `docs/ARCHITECTURE.md`.