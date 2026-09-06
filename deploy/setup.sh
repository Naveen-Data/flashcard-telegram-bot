#!/usr/bin/env bash
# One-time setup script — run this once on a fresh Oracle Cloud VM.
# Usage: bash deploy/setup.sh
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SERVICE_NAME="studybot"

echo "==> Installing system packages..."
if command -v apt-get &>/dev/null; then
  sudo apt-get update -y -q
  sudo apt-get install -y -q python3 python3-pip python3-venv
elif command -v dnf &>/dev/null; then
  sudo dnf install -y -q python3 python3-pip
else
  echo "ERROR: Neither apt-get nor dnf found. Install Python 3 manually." && exit 1
fi

echo "==> Creating virtualenv and installing dependencies..."
python3 -m venv "$REPO_DIR/venv"
"$REPO_DIR/venv/bin/pip" install --quiet -r "$REPO_DIR/requirements.txt"

for VAR in PGHOST PGUSER PGPASSWORD PGDATABASE; do
  if [ ! -f "$REPO_DIR/.env" ] || ! grep -q "^${VAR}=" "$REPO_DIR/.env"; then
    echo "ERROR: $REPO_DIR/.env must contain $VAR pointing at the" \
         "PostgreSQL VM before running this script." >&2
    exit 1
  fi
done

echo "==> Running database migrations..."
(cd "$REPO_DIR" && set -a && source .env && set +a && "$REPO_DIR/venv/bin/alembic" upgrade head)

echo "==> Installing systemd services..."
for SERVICE_FILE_NAME in studybot studybot-mcp; do
  SERVICE_FILE="/etc/systemd/system/${SERVICE_FILE_NAME}.service"
  sudo cp "$REPO_DIR/deploy/${SERVICE_FILE_NAME}.service" "$SERVICE_FILE"
  sudo sed -i "s|USER_PLACEHOLDER|$USER|g" "$SERVICE_FILE"
  sudo sed -i "s|PROJECT_DIR_PLACEHOLDER|$REPO_DIR|g" "$SERVICE_FILE"
done

echo "==> Enabling and starting services..."
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME" studybot-mcp
sudo systemctl restart "$SERVICE_NAME" studybot-mcp

echo ""
echo "Done! Check status with: sudo systemctl status studybot studybot-mcp"
echo "View logs with:          sudo journalctl -u studybot -f    (or -u studybot-mcp)"
