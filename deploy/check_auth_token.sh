#!/usr/bin/env bash
# Verifies MCP_AUTH_TOKEN against the deployed /mcp endpoint (Claude Code's
# static secret — separate from the web app's login-based sessions, which
# this script doesn't test; log in through the web UI to check those).
# Usage: ./deploy/check_auth_token.sh <token> [base_url]
set -euo pipefail

TOKEN="${1:?Usage: check_auth_token.sh <token> [base_url]}"
BASE="${2:-https://140.245.244.245.nip.io}"

no_token=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/mcp")
with_token=$(curl -s -o /dev/null -w "%{http_code}" -X POST -H "Authorization: Bearer $TOKEN" "$BASE/mcp")
wrong_token=$(curl -s -o /dev/null -w "%{http_code}" -X POST -H "Authorization: Bearer definitely-wrong" "$BASE/mcp")

echo "no token:      $no_token   (expect 401 if MCP_AUTH_TOKEN is set server-side)"
echo "wrong token:   $wrong_token   (expect 401 if MCP_AUTH_TOKEN is set server-side)"
echo "your token:    $with_token   (expect anything but 401)"

if [ "$with_token" != "401" ] && [ "$no_token" = "401" ]; then
  echo "✅ token is correct and auth is active"
elif [ "$with_token" != "401" ] && [ "$no_token" != "401" ]; then
  echo "⚠️  token accepted, but auth isn't enforced (no_token also passed) — MCP_AUTH_TOKEN may be unset server-side"
else
  echo "❌ token rejected"
fi
