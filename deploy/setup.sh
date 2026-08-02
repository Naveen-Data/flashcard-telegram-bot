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

echo "==> Installing systemd services..."
for SVC in studybot studybot-mcp; do
  SERVICE_FILE="/etc/systemd/system/${SVC}.service"
  sudo cp "$REPO_DIR/deploy/${SVC}.service" "$SERVICE_FILE"
  sudo sed -i "s|USER_PLACEHOLDER|$USER|g" "$SERVICE_FILE"
  sudo sed -i "s|PROJECT_DIR_PLACEHOLDER|$REPO_DIR|g" "$SERVICE_FILE"
done

echo "==> Allowing passwordless sudo for service restarts..."
echo "$USER ALL=(ALL) NOPASSWD: /bin/systemctl restart studybot, /bin/systemctl restart studybot-mcp, /bin/systemctl restart studybot studybot-mcp" | sudo tee /etc/sudoers.d/studybot > /dev/null

echo "==> Enabling and starting services..."
sudo systemctl daemon-reload
for SVC in studybot studybot-mcp; do
  sudo systemctl enable "$SVC"
  sudo systemctl restart "$SVC"
done

echo ""
echo "Done!"
echo "Check status:  sudo systemctl status studybot studybot-mcp"
echo "View bot logs: sudo journalctl -u studybot -f"
echo "View MCP logs: sudo journalctl -u studybot-mcp -f"
