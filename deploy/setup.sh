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

echo "==> Installing systemd service..."
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
sudo cp "$REPO_DIR/deploy/studybot.service" "$SERVICE_FILE"
sudo sed -i "s|USER_PLACEHOLDER|$USER|g" "$SERVICE_FILE"
sudo sed -i "s|PROJECT_DIR_PLACEHOLDER|$REPO_DIR|g" "$SERVICE_FILE"

echo "==> Enabling and starting service..."
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"

echo ""
echo "Done! Check status with: sudo systemctl status studybot"
echo "View logs with:          sudo journalctl -u studybot -f"
