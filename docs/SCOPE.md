# Project Scope — Atlas Enterprise AI

## 1. Problem Statement
Atlas Enterprise AI is a secure, self-hosted conversational AI assistant designed to act as an intelligent search and reasoning engine over an organization's private documents.

Instead of functioning as a generic chatbot that pulls answers from the public internet or relies solely on pre-trained model memory, this application serves as an internal enterprise portal where employees can securely query company-specific documents, HR policies, technical manuals, and internal knowledge bases.

The application enables organizations to make internal knowledge instantly accessible while enforcing corporate compliance, preventing data leaks, and **minimizing AI hallucinations** through strict document grounding, source citations, automated guardrails, and human-in-the-loop (HITL) review.

---

## 2. Target Roles (Simplified for MVP)
- **Employee (End User):** Uploads documents, asks natural-language questions, conducts multi-turn follow-up chats, views source citations, and submits feedback (thumbs up/down).
- **Reviewer / Supervisor (HITL):** Reviews flagged, sensitive, or low-confidence AI responses in the approval queue and approves, edits, or rejects them before final release.
- **Operator / Admin:** Monitors system health, inspects PostgreSQL audit logs, and reviews execution traces in self-hosted Langfuse.

*(Note: Full enterprise SSO/IAM is out of scope for MVP; role separation in the UI/API uses a lightweight local role/header mechanism.)*

---

## 3. In-Scope Capabilities (MVP)

| Area | Scope Definition |
| :--- | :--- |
| **Document Ingestion** | Direct browser-to-RustFS uploads using S3-compatible presigned PUT URLs. Backend extracts text (`.pdf`, `.txt`, `.md`, `.docx`), chunks content, generates 768-dim embeddings via local Ollama (`nomic-embed-text`), and stores metadata + vectors in PostgreSQL (`pgvector`). Supports atomic document deletion (`ON DELETE CASCADE`). |
| **Document-Grounded Q&A** | Semantic vector retrieval from `pgvector` and grounded answer synthesis via Google Gemini API (Free Tier). Every answer must include source citations (document name and chunk reference) and gracefully decline when no relevant context exists. |
| **Context-Aware Chat** | Session-based conversation memory persisted in PostgreSQL. LangGraph loads recent chat turns so users can ask natural follow-up questions without repeating context. |
| **Automated Guardrails** | Deterministic LangGraph input/output checks: detect prompt injection attempts or blocked topics on input, and intercept/redact Personally Identifiable Information (PII such as emails, phone numbers, SSNs) on output before returning to the UI. |
| **HITL Approval Workflows** | When a query triggers a sensitive/action-like rule or falls below the retrieval/confidence threshold, LangGraph pauses automated release, queues the drafted answer in PostgreSQL, and surfaces it in the Approvals UI for human review. |
| **Audit & Feedback** | Persist structured audit events (uploads, queries, guardrail triggers, HITL decisions) and user feedback ratings in PostgreSQL. |
| **Observability** | Self-hosted Langfuse instrumentation capturing LangGraph execution traces, step latencies, retrieval scores, and errors. |
| **Web Dashboard** | Lightweight vanilla HTML/CSS/JS frontend with views for Document Management, Chat, Session History, HITL Approvals, and Audit/Operational Logs. |
| **Containerization & CI/CD** | Local orchestration via `docker-compose.yml` and automated Docker image builds pushed to GitHub Container Registry (`ghcr.io`) via GitHub Actions. |

---

## 4. Explicitly Out of Scope (Do Not Build)
AI coding agents **must not** introduce any of the following without an explicit scope change from the project owner:

- **Paid Cloud Services or Stack Swaps:** No AWS S3, AWS Bedrock, AWS ECR, Pinecone, Qdrant, OpenAI API, LangSmith, or Render.
- **Frontend Build Frameworks:** No React, Next.js, Vue, Angular, Svelte, or Node/Webpack/Vite build pipelines.
- **Live SQL / External Database Querying:** No Text-to-SQL agents or connectors to external proprietary relational databases (e.g., Snowflake, Salesforce, ERP systems).
- **Enterprise Auth & Multi-Tenancy:** No OAuth2/OIDC/SAML SSO, LDAP integration, JWT identity providers, or multi-organization tenant isolation in MVP.
- **Heavy Distributed Infrastructure:** No Kubernetes, Helm, Terraform, Celery, Kafka, or standalone message brokers.
- **Advanced Media & OCR:** No scanned-image OCR pipelines, audio/video transcription, or multimodal image generation.
- **Public Internet Search:** No web-browsing tools or external search APIs inside the RAG agent.
- **Model Training:** No fine-tuning, LoRA training, or custom embedding model training.

---

## 5. Non-Functional Constraints
- **Zero-Cost Operation ($0):** Every component must run locally via Docker Compose or within strict free-tier limits (Gemini API Free Tier, `ghcr.io` public packages).
- **Reproducibility:** A developer must be able to clone the repository, populate `.env`, and run `docker compose up -d --build` to get a working environment.
- **Groundedness First:** If retrieved chunks do not contain sufficient evidence to answer a question, the system must explicitly state that the information is not found in the uploaded documents rather than guessing.
- **Deterministic Safety:** Input/output guardrails must not rely solely on LLM self-policing; core PII redaction and injection/action triggers must use deterministic code rules.

---

## 6. MVP Acceptance Criteria
The MVP is considered complete when all of the following end-to-end checks pass:
1. A user uploads a PDF via the Web UI; the file lands directly in RustFS via a presigned URL, is chunked/embedded by Ollama, and appears in PostgreSQL + `pgvector`.
2. Asking a question grounded in that PDF returns an accurate answer with explicit document citations.
3. Asking a follow-up question in the same session resolves pronouns/context from PostgreSQL chat history.
4. Deleting the document removes both its metadata and all associated vector chunks atomically.
5. Submitting a prompt-injection attempt or triggering PII in output is intercepted/redacted by the guardrail node and logged to the audit table.
6. Submitting a sensitive or low-confidence query pauses the response, creates a pending record in the HITL approval queue, and allows a reviewer to approve or reject it in the UI.
7. Every RAG execution produces a viewable trace in self-hosted Langfuse.
8. Pushing to `main` triggers GitHub Actions to run tests and publish a versioned Docker image to `ghcr.io`.