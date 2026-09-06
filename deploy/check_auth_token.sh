#!/usr/bin/env bash
# Verifies a personal MCP API token against the deployed /mcp endpoint.
# Generate one in the web app (Settings -> MCP tokens) or via
# studybot.db.create_api_token, then check it here.
# Usage: ./deploy/check_auth_token.sh <token> [base_url]
set -euo pipefail

TOKEN="${1:?Usage: check_auth_token.sh <token> [base_url]}"
BASE="${2:-https://140.245.244.245.nip.io}"

no_token=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/mcp")
with_token=$(curl -s -o /dev/null -w "%{http_code}" -X POST -H "Authorization: Bearer $TOKEN" "$BASE/mcp")
wrong_token=$(curl -s -o /dev/null -w "%{http_code}" -X POST -H "Authorization: Bearer definitely-wrong" "$BASE/mcp")

echo "no token:      $no_token   (expect 401)"
echo "wrong token:   $wrong_token   (expect 401)"
echo "your token:    $with_token   (expect anything but 401)"

if [ "$with_token" != "401" ] && [ "$no_token" = "401" ] && [ "$wrong_token" = "401" ]; then
  echo "✅ token is valid and auth is enforced"
else
  echo "❌ token rejected, or auth isn't enforced — check the token and server logs"
fi
