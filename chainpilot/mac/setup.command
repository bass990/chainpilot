#!/bin/bash
# ============================================================
# ChainPilot — One-time setup for macOS
# Double-click this file in Finder to install dependencies.
# ============================================================

set -e

# Resolve project root: this script lives in chainpilot/mac/, so go up one level.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

clear
echo "============================================================"
echo "  ChainPilot — macOS Setup"
echo "============================================================"
echo "  Project root: $PROJECT_DIR"
echo ""

# ── Step 1: macOS prerequisites ─────────────────────────────
echo "[1/5] Checking prerequisites..."

if ! command -v python3 >/dev/null 2>&1; then
    echo "    ✗ python3 not found."
    echo "      Install it from https://www.python.org/downloads/macos/"
    echo "      (or run: brew install python)"
    read -p "Press Enter to close..."
    exit 1
fi
PY_VER="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
echo "    ✓ python3 ${PY_VER}"

if ! command -v node >/dev/null 2>&1; then
    echo "    ✗ Node.js not found."
    echo "      Install LTS from https://nodejs.org/"
    echo "      (or run: brew install node)"
    read -p "Press Enter to close..."
    exit 1
fi
echo "    ✓ node $(node --version)"
echo "    ✓ npm  $(npm --version)"
echo ""

# ── Step 2: .env file ───────────────────────────────────────
echo "[2/5] Checking .env file..."

if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        cp .env.example .env
        echo "    Created .env from template."
    else
        cat > .env <<'EOF'
ANTHROPIC_API_KEY=your_api_key_here
SLACK_WEBHOOK_URL=
SMTP_SERVER=
SMTP_PORT=
SMTP_USER=
SMTP_PASS=
SMTP_FROM=
EOF
        echo "    Created blank .env file."
    fi
fi

if grep -q "your_api_key_here" .env 2>/dev/null; then
    echo ""
    echo "    ⚠️  ACTION REQUIRED:"
    echo "       Open the .env file in the project root and replace"
    echo "       'your_api_key_here' with your real Anthropic API key."
    echo "       Slack and SMTP entries are optional (demo mode works without them)."
    echo ""
    read -p "    Press Enter once you've saved your API key (or Ctrl+C to exit)..."
fi
echo "    ✓ .env present"
echo ""

# ── Step 3: Python virtual environment ──────────────────────
echo "[3/5] Setting up Python virtual environment..."

if [ ! -f "venv/bin/activate" ]; then
    python3 -m venv venv
    echo "    Created venv/"
else
    echo "    ✓ venv/ already exists"
fi

source venv/bin/activate
python -m pip install --upgrade pip --quiet
echo ""

# ── Step 4: Python dependencies ─────────────────────────────
echo "[4/5] Installing Python dependencies..."

if python -c "import fastapi, anthropic, uvicorn" 2>/dev/null; then
    echo "    ✓ Already installed"
else
    pip install -r requirements.txt
    echo "    ✓ Installed"
fi
echo ""

# ── Step 5: Frontend dependencies ───────────────────────────
echo "[5/5] Installing frontend dependencies..."

if [ ! -d "frontend/node_modules" ]; then
    cd frontend
    npm install
    cd "$PROJECT_DIR"
    echo "    ✓ Installed"
else
    echo "    ✓ Already installed"
fi
echo ""

echo "============================================================"
echo "  ✅  Setup complete."
echo ""
echo "  Next: double-click 'mac/start.command' to launch ChainPilot."
echo "        The dashboard will open at http://localhost:3002"
echo "============================================================"
echo ""
read -p "Press Enter to close..."
