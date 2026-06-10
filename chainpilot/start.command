#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

# Check .env
if grep -q "your_api_key_here" .env 2>/dev/null; then
    osascript -e 'display alert "Missing API Key" message "Edit .env and replace your_api_key_here with your Anthropic API key, then try again." buttons {"OK"}'
    exit 1
fi

# Check setup has been run
if [ ! -f "venv/bin/activate" ] || [ ! -d "frontend/node_modules" ]; then
    osascript -e 'display alert "Run setup first" message "Open setup.command before starting." buttons {"OK"}'
    exit 1
fi

# Start backend in a new Terminal window
osascript -e "tell application \"Terminal\"
    do script \"cd '$DIR' && source venv/bin/activate && python -m uvicorn backend.main:app --host 0.0.0.0 --port 8002\"
    set custom title of front window to \"ChainPilot — Backend\"
end tell"

# Start frontend in a new Terminal window
osascript -e "tell application \"Terminal\"
    do script \"cd '$DIR/frontend' && npm run dev\"
    set custom title of front window to \"ChainPilot — Frontend\"
end tell"

# Wait for servers to start then open browser
sleep 4
open "http://localhost:3002"
