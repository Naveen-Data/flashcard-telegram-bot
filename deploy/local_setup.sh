#!/usr/bin/env bash
# Sets up bot.py and mcp_server.py as persistent launchd services on macOS.
# Run once: bash deploy/local_setup.sh
set -euo pipefail

PROJ="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="$PROJ/venv/bin/python"
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
LOG_DIR="$PROJ/logs"
BOT_LABEL="com.studybot.bot"
MCP_LABEL="com.studybot.mcp"

if [ ! -f "$PROJ/.env" ]; then
  echo "ERROR: $PROJ/.env not found. Create it with TELEGRAM_BOT_TOKEN=your-token"
  exit 1
fi

if [ ! -f "$PYTHON" ]; then
  echo "ERROR: venv not found. Run: python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt"
  exit 1
fi

mkdir -p "$LAUNCH_AGENTS" "$LOG_DIR"

# --- bot.py plist ---
cat > "$LAUNCH_AGENTS/$BOT_LABEL.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$BOT_LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PROJ/deploy/run_bot.sh</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$PROJ</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>$LOG_DIR/bot.log</string>
  <key>StandardErrorPath</key>
  <string>$LOG_DIR/bot.log</string>
  <key>ThrottleInterval</key>
  <integer>10</integer>
</dict>
</plist>
PLIST

# --- mcp_server.py plist ---
cat > "$LAUNCH_AGENTS/$MCP_LABEL.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$MCP_LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PROJ/deploy/run_mcp.sh</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$PROJ</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>$LOG_DIR/mcp.log</string>
  <key>StandardErrorPath</key>
  <string>$LOG_DIR/mcp.log</string>
  <key>ThrottleInterval</key>
  <integer>10</integer>
</dict>
</plist>
PLIST

# --- runner scripts (source .env then exec python) ---
cat > "$PROJ/deploy/run_bot.sh" <<SH
#!/bin/bash
source "$PROJ/.env"
exec "$PYTHON" "$PROJ/bot.py"
SH

cat > "$PROJ/deploy/run_mcp.sh" <<SH
#!/bin/bash
source "$PROJ/.env"
exec "$PYTHON" "$PROJ/mcp_server.py"
SH

chmod +x "$PROJ/deploy/run_bot.sh" "$PROJ/deploy/run_mcp.sh"

# --- unload if already running, then load ---
for LABEL in "$BOT_LABEL" "$MCP_LABEL"; do
  launchctl unload "$LAUNCH_AGENTS/$LABEL.plist" 2>/dev/null || true
  launchctl load "$LAUNCH_AGENTS/$LABEL.plist"
done

echo ""
echo "✅ Services installed and started."
echo ""
echo "Useful commands:"
echo "  View bot logs:  tail -f $LOG_DIR/bot.log"
echo "  View MCP logs:  tail -f $LOG_DIR/mcp.log"
echo "  Stop bot:       launchctl unload $LAUNCH_AGENTS/$BOT_LABEL.plist"
echo "  Stop MCP:       launchctl unload $LAUNCH_AGENTS/$MCP_LABEL.plist"
echo "  Restart bot:    launchctl unload $LAUNCH_AGENTS/$BOT_LABEL.plist && launchctl load $LAUNCH_AGENTS/$BOT_LABEL.plist"
