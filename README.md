# Atlas Enterprise AI

Zero-cost, self-hosted Enterprise RAG assistant over private organizational documents with grounded citations, multi-turn chat memory, deterministic input/output guardrails, human-in-the-loop (HITL) approvals, audit logging, and self-hosted Langfuse tracing.

---

## Locked Tech Stack

- **Backend & Orchestration:** Python 3.12 + FastAPI + LangGraph
- **Database & Vector Store:** PostgreSQL 16 + `pgvector` (`vector(768)`, HNSW cosine index, `ON DELETE CASCADE`)
- **Object Storage:** RustFS (S3-compatible) with direct browser-to-storage presigned PUT uploads
- **Embeddings:** Native host Ollama running `nomic-embed-text` (768 dimensions)
- **Chat LLM:** Google Gemini API Free Tier (`langchain-google-genai`, `gemini-3.5-flash-lite`)
- **Observability:** Self-hosted Langfuse v2
- **Frontend:** Vanilla HTML, CSS, and JavaScript served directly by FastAPI (zero build pipeline)
- **Production Proxy & CI/CD:** Caddy 2 (automatic HTTPS) + GitHub Actions -> `ghcr.io`

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

## Production Deployment (Azure Ubuntu 24.04 VM via `ghcr.io`)

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
2. **`publish`** *(push to `main`/`master` only)*: Builds [`Dockerfile`](Dockerfile) and pushes `ghcr.io/<owner>/<repo>:latest` and `:<sha>` using `GITHUB_TOKEN`.
3. **`deploy`** *(when `AZURE_VM_HOST` secret is configured)*: SSHes into the Azure Ubuntu VM via `appleboy/ssh-action`, pulls the latest `ghcr.io` image, and runs `docker compose -f docker-compose.prod.yml up -d --remove-orphans`.

### Required GitHub Repository Secrets
Configure these under **Settings -> Secrets and variables -> Actions**:

| Secret Name | Description |
| :--- | :--- |
| `AZURE_VM_HOST` | Public IP address or DNS hostname of the Azure Ubuntu VM (deployment step is skipped automatically if unset) |
| `AZURE_VM_USER` | SSH username on the Ubuntu VM (e.g., `azureuser`) |
| `AZURE_VM_SSH_KEY` | Private SSH key matching `~/.ssh/authorized_keys` on the VM |

*(Note: Ensure the GitHub package visibility on `ghcr.io` or repository workflow permissions allow `packages: write` for `GITHUB_TOKEN`.)*
