#!/usr/bin/env bash
# ── Atlas Enterprise AI — Ubuntu 24.04 LTS Server Bootstrap Script ────────────
# Installs:
#   1. Docker Engine + Docker Compose v2 plugin
#   2. Native host Ollama configured to listen on 0.0.0.0:11434 (for Docker bridge access)
#   3. Embedding model: nomic-embed-text (768-dim)
#   4. Creates /opt/atlas deployment directory
#
# Usage on fresh Azure Ubuntu 24.04 VM:
#   chmod +x scripts/setup_vm.sh
#   sudo ./scripts/setup_vm.sh

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Please run this script with sudo: sudo $0"
  exit 1
fi

TARGET_USER="${SUDO_USER:-$USER}"
APP_DIR="/opt/atlas"

echo "==> [1/5] Updating apt packages and installing prerequisites..."
apt-get update -y
apt-get install -y ca-certificates curl gnupg git

echo "==> [2/5] Installing Docker Engine & Docker Compose plugin..."
if ! command -v docker >/dev/null 2>&1; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc

  . /etc/os-release
  echo \
    "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
    ${VERSION_CODENAME} stable" > /etc/apt/sources.list.d/docker.list

  apt-get update -y
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
else
  echo "Docker already installed; ensuring compose plugin is present..."
  apt-get install -y docker-compose-plugin
fi

systemctl enable docker
systemctl start docker

if id "$TARGET_USER" >/dev/null 2>&1; then
  usermod -aG docker "$TARGET_USER" || true
  echo "Added user '$TARGET_USER' to docker group."
fi

echo "==> [3/5] Installing native Ollama..."
if ! command -v ollama >/dev/null 2>&1; then
  curl -fsSL https://ollama.com/install.sh | sh
else
  echo "Ollama already installed."
fi

echo "==> [4/5] Configuring Ollama to bind 0.0.0.0:11434 for Docker container access..."
mkdir -p /etc/systemd/system/ollama.service.d
cat <<'EOF' > /etc/systemd/system/ollama.service.d/override.conf
[Service]
Environment="OLLAMA_HOST=0.0.0.0:11434"
EOF

systemctl daemon-reload
systemctl enable ollama
systemctl restart ollama

echo "Waiting for Ollama API on http://127.0.0.1:11434..."
for i in $(seq 1 30); do
  if curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

echo "==> [5/5] Pulling nomic-embed-text embedding model..."
ollama pull nomic-embed-text

mkdir -p "$APP_DIR"
chown -R "$TARGET_USER":"$TARGET_USER" "$APP_DIR"

echo ""
echo "=================================================================="
echo " Bootstrap complete!"
echo " - Docker & Compose: $(docker compose version)"
echo " - Ollama endpoint:  0.0.0.0:11434 (keep port 11434 closed in Azure NSG!)"
echo " - Deploy directory: ${APP_DIR}"
echo "=================================================================="
echo "Next steps:"
echo "  1. Log out and back in (or run 'newgrp docker') for group changes."
echo "  2. Clone your repo into ${APP_DIR} and create ${APP_DIR}/.env from .env.example."
echo "  3. Open ports 22, 80, and 443 ONLY in your Azure Network Security Group."
echo "  4. Start the stack: docker compose -f docker-compose.prod.yml up -d"
