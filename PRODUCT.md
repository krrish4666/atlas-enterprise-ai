# PRODUCT.md — Atlas Enterprise AI

## Product & Design Specification (Phase 5: Lightweight Web Dashboard)

---

## 1. Product Vision & Operating Mode

**Atlas Enterprise AI** is a zero-cost, self-hosted Enterprise RAG assistant over private organizational documents.

### Operating Mode: Enterprise Operate
- **Internal Workspace:** This is an operator, reviewer, and employee tool designed for productivity, operational transparency, and high data density.
- **Not a Marketing Site:** No hero banners, marketing copy, decorative illustrations, feature carousels, or customer testimonials.
- **Zero Hallucination Tolerance:** The UI places citations, confidence indicators, and document provenance front-and-center.
- **Human-in-the-Loop (HITL) First:** Low-confidence and sensitive queries are elevated cleanly into supervisor queues rather than silently failing or hallucinating.

---

## 2. Target Roles & User Workflows

| Role | Key Capabilities in UI | UI Controls |
| :--- | :--- | :--- |
| **Employee** | Upload documents, ask natural-language questions, view grounded citations with chunk scores, submit thumbs up/down feedback. | Chat thread, citation drawer, document upload dropzone, document list, feedback buttons. |
| **Reviewer / Supervisor** | Review flagged or low-confidence responses awaiting approval. Approve, edit and approve, or reject with notes. | Approvals tab with pending counter, side-by-side draft vs. retrieved sources, in-place edit textarea, decision actions. |
| **Operator / Admin** | Monitor real-time status of backend services (Postgres, RustFS, Ollama, Langfuse) and inspect immutable audit logs. | Operations tab with health pill grid, filterable audit log table, direct link to Langfuse tracing UI. |

*Role Switching:* The global header includes a lightweight role selector (`Employee` | `Reviewer` | `Admin`) that injects the `X-User-Role` HTTP header on all API calls (defaulting to `employee`, elevated to `reviewer` / `admin` for approval decisions).

---

## 3. UI Design Principles & Aesthetic Constraints

### A. Quiet Monochrome Chrome (Linear / Vercel / Stripe Aesthetic)
- **Neutral Light Grayscale Foundation:** The default palette is strictly locked to **LIGHT MODE**, built on crisp, neutral zinc/slate/gray scales (`#ffffff`, `#fcfcfc`, `#f4f4f5`, `#e4e4e7`, `#d4d4d8`, `#71717a`, `#09090b`).
- **1px Borders Over Drop Shadows:** Use crisp 1px borders (`border: 1px solid var(--border-subtle)`) for visual separation. No heavy or diffused drop shadows.
- **No Nested Cards ("Card Soup"):** Avoid nesting boxes inside boxes. Use clean hairline dividers, tabular rows, and clear vertical rhythm.
- **No Gradients, No Glassmorphism:** Absolutely zero gradient text, gradient buttons, blurred backdrops (`backdrop-filter`), or frosted-glass styling.
- **Strictly No AI Sparkles:** Never use AI sparkle icons, magic wands, glowing stars, or cartoon bot avatars. Icons represent tangible actions (documents, search, send, check, alert, chevron, thumbs).

### B. Semantic Color ONLY for State Signaling
Color is strictly reserved for communicating operational state. It is never used for decorative or branding flourishes.

| State | Palette Token | Hex Values (Light Mode) | Usage |
| :--- | :--- | :--- | :--- |
| **Healthy / Indexed / Approved** | **Emerald** | `bg: #ecfdf5`, `border: #a7f3d0`, `text: #065f46`, `dot: #10b981` | Document status `indexed`, approval status `approved`, service status `up`, positive feedback. |
| **Processing / Pending Review** | **Amber** | `bg: #fffbeb`, `border: #fde68a`, `text: #92400e`, `dot: #f59e0b` | Document status `processing` or `pending_upload`, chat status `pending_approval`, approval queue pending. |
| **Failed / Blocked / Redacted** | **Rose** | `bg: #fff1f2`, `border: #fecdd3`, `text: #9f1239`, `dot: #f43f5e` | Document status `failed`, chat status `blocked`, guardrail violation, PII redacted warning, service status `down`. |

All standard UI chrome (navigation, buttons, inputs, tables, sidebars) remains strictly monochrome/neutral light mode.

### C. Motion & Transitions
- **Snappy & Purposeful:** All transitions must be subtle and finish in **under 200ms** (`transition: all 120ms ease` or `transition: opacity 150ms ease`).
- No bouncy springs, slow swoops, or distracting layout shifts.

### D. Typography & Iconography
- **System Typography:** High-performance native system font stack:
  ```css
  --font-sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  --font-mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
  ```
- **Monospace Precision:** Used for document UUIDs, chunk indices, similarity scores (e.g. `0.84`), timestamps, and trace IDs.
- **Inline SVGs Only:** Stroke-based geometric SVGs (16x16 or 20x20 viewBox, 1.5px/1.75px stroke, no fill).
- **Zero External CDNs:** No FontAwesome, No Lucide CDN, No Google Fonts. The entire application runs 100% offline and air-gapped without internet access.

---

## 4. Layout Architecture & Functional Views

The UI is a single-page application served at `http://localhost:8000/` consisting of a persistent global header and four tabbed views:

```text
+------------------------------------------------------------------------------------------+
|  [ATLAS] Enterprise AI      [Chat]  [Documents]  [Approvals (2)]  [Operations]   Role:[Reviewer v] |
+------------------------------------------------------------------------------------------+
|                                                                                          |
|  ACTIVE VIEW CONTENT (Single-Page Switcher via data-view)                                |
|                                                                                          |
+------------------------------------------------------------------------------------------+
```

### View 1: Chat & Session Memory (`#view-chat`)
- **Two-Column Layout:**
  - **Left Rail (Session History, 260px):**
    - "New Chat" primary action button.
    - List of recent sessions from `GET /api/chat/sessions` (last query snippet, relative time).
    - Active session highlighted with subtle background and left border indicator.
  - **Right Main (Conversation Thread):**
    - Message history from `GET /api/chat/sessions/{session_id}/messages` or current turn.
    - User message: Right-aligned, dark subtle container.
    - Assistant message: Left-aligned, light surface, 1px border.
    - **Citations Badge & Drawer:** Expandable section displaying source document, chunk index, vector similarity score (`score: 0.84`), and text snippet.
    - **Guardrail / PII Notice:** When `pii_redacted: true` or `status: "blocked"`, a Rose alert badge details the interception without breaking flow.
    - **Pending Approval State:** When `status: "pending_approval"`, display an Amber status banner with `approval_id` explaining the query is in human review.
    - **Message Feedback:** Inline thumbs-up (`+1`) and thumbs-down (`-1`) buttons calling `POST /api/chat/feedback`.
    - **Composer:** Monospaced/clean input textarea with `Cmd/Ctrl + Enter` to send, clear send button with inline SVG arrow, and loading spinner state.

### View 2: Document Management (`#view-documents`)
- **Direct-to-Storage Presigned Upload Zone:**
  - Drag-and-drop zone with file picker button.
  - Allowed types: `.pdf`, `.txt`, `.md`, `.docx`.
  - Step 1: Request presigned URL from `POST /api/documents/upload-url`.
  - Step 2: Upload direct to RustFS via `PUT` with `XMLHttpRequest` to render a 1px progress bar (`0% - 100%`).
  - Step 3: Trigger background processing via `POST /api/documents/{id}/ingest`.
- **Live Ingestion Polling:** Automatically polls `GET /api/documents/{id}` every 2 seconds while status is `processing` until transitioning to `indexed` or `failed`.
- **Document Inventory Table:**
  - Columns: Filename, Format, Size (human-readable KB/MB), Status (Emerald/Amber/Rose pill), Chunk Count, Upload Date, Actions.
  - **Atomic Delete:** Red-hover trash button calling `DELETE /api/documents/{id}` with a confirmation prompt. Cascades to vectors atomically.

### View 3: HITL Approvals Queue (`#view-approvals`)
- **Header & Filter Tabs:**
  - Tab filters: `Pending` (default), `Approved`, `Edited & Approved`, `Rejected`.
  - Active pending counter badge in tab header.
- **Approval Ticket Cards:**
  - Header: Session ID, Queued Timestamp, Trigger Reason (`low_confidence (score=0.31)` or `sensitive_action_detected`).
  - Top Retrieval Score badge (`0.31` in Amber/Rose).
  - Original Employee Query.
  - Retrieved Context Snippets (collapsible).
  - Draft Answer generated by Gemini (post-PII redaction).
  - **Decision Controls (Reviewer/Admin only):**
    - **Approve:** One-click approval, calls `POST /api/approvals/{id}/decision` with `{decision: "approve"}`.
    - **Edit & Approve:** Expands an in-place textarea pre-filled with the draft answer, allowing the supervisor to correct it and submit `{decision: "edit_and_approve", edited_answer: "..."}`.
    - **Reject:** Prompts for optional rejection notes and submits `{decision: "reject", reviewer_notes: "..."}`.
  - **Permission Gate:** If current role is `Employee`, decision buttons are disabled with a quiet tooltip explaining reviewer privileges are required.

### View 4: Operations & Audits (`#view-ops`)
- **Service Health Grid (4 Cards):**
  - PostgreSQL 16 + pgvector (`status: "up"` -> Emerald).
  - RustFS S3 Storage (`status: "up"` -> Emerald).
  - Host Ollama `nomic-embed-text` (`status: "up"` -> Emerald).
  - Langfuse Tracing (`enabled: true` -> Emerald with link to `http://localhost:3000`).
  - "Refresh Health" button with live pinging.
- **Audit Log Viewer:**
  - Filter controls: Limit dropdown (25, 50, 100), Event Type filter (`all`, `document_upload_requested`, `document_indexed`, `document_deleted`, `chat_completed`, `guardrail_blocked`, `pii_redacted`, `hitl_queued`, `hitl_decided`, `feedback_submitted`).
  - Structured Data Table: Timestamp (`YYYY-MM-DD HH:mm:ss`), Event Type, Actor (`employee`, `reviewer`, `system`), Resource ID, Details (expandable modal/inline JSON viewer).

---

## 5. Technical Implementation Boundaries

### A. Strict Invariants (ADR-008 & SCOPE.md Section 4)
- **Zero Frontend Build Pipeline:**
  - NO React, Next.js, Vue, Svelte.
  - NO Tailwind, Vite, Webpack, Parcel, esbuild, Babel.
  - NO `node_modules` or `package.json` in root or frontend.
- **File Structure:**
  ```text
  frontend/
  ├── index.html     # Semantic single-page markup with tab sections
  ├── styles.css     # CSS custom properties, responsive layout, component classes
  └── app.js         # Vanilla ES6 module: router, API client, direct RustFS uploader
  ```
- **Delivery:** Served directly by FastAPI from `app/main.py`:
  ```python
  app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
  ```

### B. Direct-to-Storage Presigned Upload Implementation
To adhere to ADR-002 and ensure files never buffer through FastAPI:
1. Resolve the `Content-Type` once upfront:
   ```javascript
   const resolvedContentType = file.type || "application/octet-stream";
   ```
2. Request presigned upload URL from FastAPI, passing `resolvedContentType`:
   ```javascript
   const res = await fetch("/api/documents/upload-url", {
     method: "POST",
     headers: { "Content-Type": "application/json", ...getAuthHeaders() },
     body: JSON.stringify({
       filename: file.name,
       content_type: resolvedContentType,
       file_size: file.size,
     }),
   });
   const { document_id, upload_url } = await res.json();
   ```
3. Direct `PUT` to RustFS, setting the exact same `Content-Type`:
   ```javascript
   const xhr = new XMLHttpRequest();
   xhr.open("PUT", upload_url);
   xhr.setRequestHeader("Content-Type", resolvedContentType);
   xhr.upload.onprogress = (e) => {
     if (e.lengthComputable) {
       const pct = Math.round((e.loaded / e.total) * 100);
       updateProgressBar(pct);
     }
   };
   xhr.onload = async () => {
     if (xhr.status === 200) {
       await fetch(`/api/documents/${document_id}/ingest`, {
         method: "POST",
         headers: getAuthHeaders(),
       });
       startPolling(document_id);
     }
   };
   xhr.send(file);
   ```

---

## 6. CSS Design Token Architecture (Locked Default: Light Mode)

```css
:root {
  /* Surfaces & Backgrounds (Default: Quiet Light Monochrome) */
  --bg-app: #fcfcfc;
  --bg-surface: #ffffff;
  --bg-subtle: #f4f4f5;
  --bg-input: #ffffff;
  --bg-hover: #f1f1f4;
  --bg-active: #e4e4e7;

  /* Borders & Dividers */
  --border-subtle: #e4e4e7;
  --border-strong: #d4d4d8;

  /* Typography */
  --text-main: #09090b;
  --text-muted: #71717a;
  --text-dim: #a1a1aa;

  /* State: Emerald (Success / Indexed / Approved / Healthy Up) */
  --emerald-bg: #ecfdf5;
  --emerald-border: #a7f3d0;
  --emerald-text: #065f46;
  --emerald-dot: #10b981;

  /* State: Amber (Warning / Pending / Processing) */
  --amber-bg: #fffbeb;
  --amber-border: #fde68a;
  --amber-text: #92400e;
  --amber-dot: #f59e0b;

  /* State: Rose (Error / Blocked / Redacted / Down) */
  --rose-bg: #fff1f2;
  --rose-border: #fecdd3;
  --rose-text: #9f1239;
  --rose-dot: #f43f5e;

  /* Sizing, Typography & Transitions */
  --radius-sm: 4px;
  --radius-md: 6px;
  --font-sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  --font-mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
  --transition-fast: 120ms ease;
  --transition-normal: 180ms ease;
}
```

---

## 7. Verification & Definition of Done for Phase 5

1. [ ] **Navigation & Shell:** Tab switching works seamlessly with zero page reloads and maintains active state.
2. [ ] **Direct S3 Upload:** A `.pdf` or `.docx` file uploads directly to RustFS presigned URL with visible 0–100% progress bar and transitions from `processing` to `indexed`.
3. [ ] **Atomic Deletion:** Deleting a document removes it from the table and cascades to vector chunks in Postgres.
4. [ ] **Grounded Chat:** Asking questions produces assistant answers with expandable citation badges showing document name, chunk index, score, and snippet.
5. [ ] **Multi-turn Memory:** Asking a follow-up in the same session maintains context and history.
6. [ ] **Guardrails & PII:** Injected attacks show blocked notice; simulated PII answers display rose redaction badges.
7. [ ] **HITL Review Workflow:** Low-confidence or sensitive queries show `pending_approval` in chat and appear in the Approvals queue. Switching role to `Reviewer` enables Approve / Edit & Approve / Reject, updating the session.
8. [ ] **Operations & Audits:** System health grid shows live service pings; audit log table filters and updates on every action.
9. [ ] **Performance & Zero Errors:** Zero console errors; transitions are under 200ms; zero external CDN dependencies.
