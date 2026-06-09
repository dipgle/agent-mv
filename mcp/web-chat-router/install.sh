#!/usr/bin/env bash
# install.sh — Mac/Linux setup for web-chat-router MCP server.
# Run once after cloning. Safe to re-run (idempotent).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "[web-chat-router] Checking Node.js version..."
node_major=$(node --version 2>/dev/null | sed 's/v\([0-9]*\).*/\1/')
if [ -z "$node_major" ] || [ "$node_major" -lt 20 ]; then
  echo "ERROR: Node.js >= 20 is required. Got: $(node --version 2>/dev/null || echo 'not found')"
  echo "Install from https://nodejs.org/ or via: brew install node@20"
  exit 1
fi
echo "  Node.js $(node --version) OK"

echo "[web-chat-router] Installing npm dependencies..."
npm install

echo "[web-chat-router] Installing Playwright Chromium browser..."
npx playwright install chromium --with-deps

echo "[web-chat-router] Building TypeScript..."
npm run build

echo ""
echo "[web-chat-router] Install complete."
echo ""
echo "To add to your .mcp.json, paste the entry from .mcp.json.template:"
echo "  $(dirname "$SCRIPT_DIR")/../.mcp.json.template"
echo ""
echo "Manual test:"
echo "  npx tsx src/server.ts"
echo "  (then send a JSON-RPC call on stdin)"
echo ""
echo "Environment variables (optional overrides):"
echo "  WCR_HEADLESS=0              run headed (visible browser) — useful for debugging"
echo "  WCR_PROFILE_DIR=<path>      custom persistent profile directory"
echo "  WCR_QUOTA_PER_HOUR=10       soft quota per provider per hour (default 10)"
echo "  PROJECT_LOG_DIR=<path>      write events to this devlog.sqlite directory"
