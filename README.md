# Atlas Enterprise AI

Zero-cost, self-hosted Enterprise RAG assistant over private organizational documents with grounded citations, multi-turn chat memory, deterministic input/output guardrails, human-in-the-loop (HITL) approvals, audit logging, and self-hosted Langfuse tracing.

---

## Locked Tech Stack

- **Backend & Orchestration:** Python 3.12 + FastAPI + LangGraph
- **Database & Vector Store:** PostgreSQL 16 + `pgvector` (`vector(768)`, HNSW cosine index, `ON DELETE CASCADE`) — local `pgvector` container in dev, **Neon Free Postgres** in cloud
- **Object Storage:** S3-compatible via `boto3` with direct browser-to-storage presigned PUT uploads — local **RustFS** in dev, **Backblaze B2** in cloud
- **Embeddings (768-dim):** Native host **Ollama** (`nomic-embed-text`) locally (`EMBED_PROVIDER=ollama`), or **Gemini API** (`models/gemini-embedding-001` with `output_dimensionality=768`) in cloud (`EMBED_PROVIDER=gemini`)
- **Chat LLM:** Google Gemini API Free Tier (`langchain-google-genai`, `gemini-3.5-flash-lite`)
- **Observability:** Self-hosted **Langfuse v2** locally, or **Langfuse Cloud** (Free Hobby) in cloud
- **Frontend:** Vanilla HTML, CSS, and JavaScript served directly by FastAPI (zero build pipeline)
- **CI/CD & Hosting:** GitHub Actions -> `ghcr.io` (`linux/amd64`) -> **Render Free Web Service** (or self-hosted Ubuntu VM)

---

## Local Development Quickstart

### Prerequisites
- Docker Desktop / Docker Engine + Docker Compose v2
- [Ollama](https://ollama.com/) installed natively on the host OS
- Google Gemini API key ([Google AI Studio](https://aistudio.google.com/))

### 1. Pull the embedding model on the host
```bash
ollama pull nomic-embed-text
```

### 2. Configure environment variables
```bash
cp .env.example .env
# Edit .env and set GOOGLE_API_KEY=...
```

### 3. Start the Docker Compose stack
```bash
docker compose up -d --build
```

Verify service health:
```bash
curl http://localhost:8000/api/health
```

Open the dashboard at **http://localhost:8000** (and Langfuse UI at **http://localhost:3000**).

### 4. Run Lint, Format & Tests
```bash
ruff check . && ruff format --check . && pytest tests/ -v
```

---

## Deploying to Render (Free Tier — 24/7 Live Recruiter Demo)

Atlas supports a zero-cost, zero-credit-card 24/7 cloud profile using **Render Free Web Service** (image-backed from `ghcr.io`) + **Neon Free Postgres** + **Backblaze B2** + **Gemini API** + **Langfuse Cloud**.

> [!NOTE]
> **Cold-Start Behavior:** Render's Free Web Service spins down after 15 minutes of inactivity, so the first visit after an idle window takes **~30–50 seconds** to wake up (plus ~1–3s if Neon Postgres is waking from auto-suspend). Subsequent requests respond in ~1.5–3.5 seconds.

### 1. Create Free Cloud Dependencies
1. **Neon Free Postgres:** Create a project, copy the connection string, and change the scheme prefix to `postgresql+psycopg://...` (keeping `?sslmode=require`).
2. **Backblaze B2 (10 GB Free, no credit card for private buckets; SMS verification required):**
   - Create a **Private** bucket (e.g., `atlas-docs`).
   - Create an **Application Key** scoped to that bucket (note `keyID`, `applicationKey`, and your S3 endpoint e.g. `https://s3.us-west-004.backblazeb2.com` + region e.g. `us-west-004`).
   - **CORS Rule Note:** Under **Bucket Settings -> CORS Rules**, either let Atlas apply S3 CORS automatically on startup (`PUT`, `GET`, `HEAD` — note B2 S3 rejects `"OPTIONS"` in `AllowedMethods`) OR select **"Share everything in this bucket with every origin *"** -> **S3 Compatible API** (or restrict `AllowedOrigins` to `https://<your-service>.onrender.com`).
3. **Langfuse Cloud (Free Hobby):** Create a project and copy `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, and `LANGFUSE_HOST` (`https://cloud.langfuse.com` or `https://us.cloud.langfuse.com`).
4. **Google Gemini API Key:** Reuse your Google AI Studio key for both `gemini-3.5-flash-lite` chat and `models/gemini-embedding-001` (`768`-dim embeddings).

### 2. Configure Render Web Service (Existing Image)
In the Render Dashboard: **New -> Web Service -> Existing Image**:
- **Image URL:** `ghcr.io/<lowercase-github-owner>/<lowercase-repo-name>:latest` *(make sure the package on GitHub Packages is set to **Public** so Render pulls without extra registry credentials)*
- **Instance Type:** `Free`
- **Health Check Path:** `/health`
- **Environment Variables:**
  ```ini
  APP_ENV=production
  DATABASE_URL=postgresql+psycopg://<user>:<pass>@<neon-host>/<db>?sslmode=require
  EMBED_PROVIDER=gemini
  GEMINI_EMBED_MODEL=models/gemini-embedding-001
  GEMINI_MODEL=gemini-3.5-flash-lite
  GOOGLE_API_KEY=<your-gemini-api-key>
  S3_ENDPOINT_URL=https://s3.<your-b2-region>.backblazeb2.com
  S3_PUBLIC_ENDPOINT_URL=https://s3.<your-b2-region>.backblazeb2.com
  S3_REGION=<your-b2-region>
  S3_ADDRESSING_STYLE=path
  S3_ACCESS_KEY=<your-b2-key-id>
  S3_SECRET_KEY=<your-b2-application-key>
  S3_BUCKET_NAME=atlas-docs
  S3_CORS_ORIGINS=https://<your-render-app>.onrender.com
  LANGFUSE_ENABLED=true
  LANGFUSE_HOST=https://cloud.langfuse.com
  LANGFUSE_PUBLIC_KEY=<pk-lf-...>
  LANGFUSE_SECRET_KEY=<sk-lf-...>
  CHAT_RATE_LIMIT_PER_MINUTE=20
  ```

### 3. Enable Auto-Deploy from GitHub Actions
In Render (**Settings -> Deploy Hook**), copy your Deploy Hook URL, then add it to GitHub (**Settings -> Secrets and variables -> Actions**):

| Secret Name | Description |
| :--- | :--- |
| `RENDER_DEPLOY_HOOK_URL` | Render service Deploy Hook URL (triggered automatically by `.github/workflows/ci-cd.yml` after pushing `linux/amd64` to `ghcr.io`) |

---

## Optional Self-Hosted Deployment (Azure Ubuntu 24.04 VM)

Production runs using [`docker-compose.prod.yml`](docker-compose.prod.yml) with:
- Pre-built API image pulled from `ghcr.io/${GITHUB_REPOSITORY}:latest`
- Native host Ollama listening on `0.0.0.0:11434` (reached from Docker via `host.docker.internal:11434`)
- `caddy:2-alpine` ([`Caddyfile`](Caddyfile)) exposing ports `80` and `443` with automatic Let's Encrypt HTTPS, proxying UI/API to FastAPI and `/atlas-docs/*` (plus `/s3/*`) to RustFS so browser presigned PUT uploads stay on HTTPS with zero mixed-content errors
- Per-IP rate limiting on `POST /api/chat` (`CHAT_RATE_LIMIT_PER_MINUTE`, default `20`) to protect Gemini Free Tier quota

### Step 1: Bootstrap the Azure Ubuntu 24.04 VM
SSH into your fresh Ubuntu 24.04 VM, clone the repository into `/opt/atlas`, and run the bootstrap script:

```bash
sudo mkdir -p /opt/atlas
sudo chown "$USER":"$USER" /opt/atlas
git clone https://github.com/<OWNER>/<REPO>.git /opt/atlas
cd /opt/atlas

chmod +x scripts/setup_vm.sh
sudo ./scripts/setup_vm.sh
```

`scripts/setup_vm.sh` installs Docker, Docker Compose v2, native Ollama configured with `OLLAMA_HOST=0.0.0.0:11434`, and pulls `nomic-embed-text`.

### Step 2: Configure Firewall / Azure Network Security Group (NSG)
- **Open publicly:** TCP `22` (SSH), `80` (HTTP / ACME challenge), `443` (HTTPS)
- **Keep closed publicly:** `5432` (Postgres), `9000`/`9001` (RustFS), `3000` (Langfuse), `8000` (FastAPI), `11434` (Ollama)

### Step 3: Create Production `.env` on the VM
```bash
cd /opt/atlas
cp .env.example .env
```

Update the production values in `/opt/atlas/.env`:
```ini
APP_ENV=production
GITHUB_REPOSITORY=<lowercase-github-owner>/<lowercase-repo-name>
GOOGLE_API_KEY=<your-gemini-api-key>
POSTGRES_PASSWORD=<strong-postgres-password>
DATABASE_URL=postgresql+psycopg://atlas:<strong-postgres-password>@postgres:5432/atlas

# Domain & HTTPS (leave ATLAS_DOMAIN unset to serve plain HTTP on :80)
ATLAS_DOMAIN=atlas.yourdomain.com
S3_PUBLIC_ENDPOINT_URL=https://atlas.yourdomain.com
S3_CORS_ORIGINS=https://atlas.yourdomain.com

# Demo quota protection
CHAT_RATE_LIMIT_PER_MINUTE=20

# Langfuse secrets
LANGFUSE_NEXTAUTH_SECRET=<random-32-char-secret>
LANGFUSE_SALT=<random-32-char-salt>
```

### Step 4: Start the Production Stack
```bash
cd /opt/atlas
docker compose -f docker-compose.prod.yml up -d --remove-orphans
```

Verify locally on the VM:
```bash
curl http://127.0.0.1:8000/health
```

---

## Automated GitHub Actions CI/CD (`.github/workflows/ci-cd.yml`)

Every push or pull request to `main` / `master` runs:
1. **`verify`**: `ruff check .`, `ruff format --check .`, and `pytest tests/ -v`.
2. **`publish`** *(push to `main`/`master` only)*: Builds [`Dockerfile`](Dockerfile) for `linux/amd64` and pushes lowercase `ghcr.io/<owner>/<repo>:latest` and `:<sha>` using `GITHUB_TOKEN`.
3. **`deploy`** *(when `RENDER_DEPLOY_HOOK_URL` secret is set)*: Triggers the Render Deploy Hook via `curl -fsS` to roll out the new `ghcr.io` image automatically.

### Required GitHub Repository Secrets
Configure under **Settings -> Secrets and variables -> Actions**:

| Secret Name | Description |
| :--- | :--- |
| `RENDER_DEPLOY_HOOK_URL` | Render Web Service Deploy Hook URL (deployment step is skipped if unset) |

*(Ensure workflow permissions allow `packages: write` for `GITHUB_TOKEN`, and set the published `ghcr.io` package visibility to **Public** for free Render pulls.)*
