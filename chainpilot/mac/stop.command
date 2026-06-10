#!/bin/bash
# ============================================================
# ChainPilot — Stop running backend / frontend on macOS
# Useful if you closed Terminal windows but the ports are stuck.
# ============================================================

echo "Stopping ChainPilot..."

for port in 8002 3002; do
    pids="$(lsof -ti ":$port" 2>/dev/null || true)"
    if [ -n "$pids" ]; then
        echo "  Port $port → killing PID(s): $pids"
        echo "$pids" | xargs kill -9 2>/dev/null || true
    else
        echo "  Port $port → already free"
    fi
done

echo "Done."
read -p "Press Enter to close..."
