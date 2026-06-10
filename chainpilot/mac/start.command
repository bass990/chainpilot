#!/bin/bash
# ============================================================
# ChainPilot — Launch backend + frontend on macOS
# Double-click this file in Finder to start the demo.
# ============================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

# ── Pre-flight checks ───────────────────────────────────────
if [ ! -f "venv/bin/activate" ] || [ ! -d "frontend/node_modules" ]; then
    osascript -e 'display alert "Run setup first" message "Open mac/setup.command before starting ChainPilot." buttons {"OK"}'
    exit 1
fi

if grep -q "your_api_key_here" .env 2>/dev/null; then
    osascript -e 'display alert "Missing API Key" message "Edit the .env file in the project root and replace your_api_key_here with your Anthropic API key, then try again." buttons {"OK"}'
    exit 1
fi

# Free the ports if a previous run was killed without cleanup.
for port in 8002 3002; do
    if lsof -ti ":$port" >/dev/null 2>&1; then
        echo "Releasing port $port from a previous run..."
        lsof -ti ":$port" | xargs kill -9 2>/dev/null || true
    fi
done

# ── Launch backend in a new Terminal window ─────────────────
osascript <<EOF
tell application "Terminal"
    activate
    do script "cd '$PROJECT_DIR' && source venv/bin/activate && echo 'ChainPilot — Backend on :8002' && python -m uvicorn backend.main:app --host 0.0.0.0 --port 8002"
    set custom title of front window to "ChainPilot — Backend"
end tell
EOF

# ── Launch frontend in a second Terminal window ─────────────
osascript <<EOF
tell application "Terminal"
    activate
    do script "cd '$PROJECT_DIR/frontend' && echo 'ChainPilot — Frontend on :3002' && npm run dev"
    set custom title of front window to "ChainPilot — Frontend"
end tell
EOF

# ── Wait for the dev server, then open the dashboard ────────
echo "Waiting for the dashboard to become reachable..."
for i in {1..30}; do
    if curl -s -o /dev/null -w "%{http_code}" http://localhost:3002 2>/dev/null | grep -q "200\|304"; then
        break
    fi
    sleep 1
done

open "http://localhost:3002"
echo "ChainPilot launched. Close the two Terminal windows to stop it."
