#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

echo "=== ChainPilot Setup ==="
echo ""

# Check .env
if grep -q "your_api_key_here" .env 2>/dev/null; then
    echo "⚠️  WARNING: .env still has placeholder API key."
    echo "   Edit .env and replace 'your_api_key_here' with your Anthropic API key."
    echo "   Then re-run this script."
    echo ""
    read -p "Press Enter to continue anyway, or Ctrl+C to exit..."
    echo ""
fi

# Python venv
if [ ! -f "venv/bin/activate" ]; then
    echo "Creating Python virtual environment..."
    python3 -m venv venv
    echo "Done."
else
    echo "✓ Python virtual environment exists"
fi

source venv/bin/activate

# Python dependencies
if ! python -c "import fastapi, anthropic, uvicorn" 2>/dev/null; then
    echo "Installing Python dependencies..."
    pip install -r requirements.txt
    echo "Done."
else
    echo "✓ Python dependencies installed"
fi

# Frontend node_modules
if [ ! -d "frontend/node_modules" ]; then
    echo "Installing frontend dependencies..."
    cd frontend && npm install
    cd "$DIR"
    echo "Done."
else
    echo "✓ Frontend dependencies installed"
fi

echo ""
echo "=== Setup complete ==="
echo "Run start.command to launch ChainPilot."
echo ""
read -p "Press Enter to close..."
