#!/bin/bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Verify Python Virtual Environment
if [[ -f "$DIR/.venv/bin/activate" ]]; then
    source "$DIR/.venv/bin/activate"
elif [[ -f "$DIR/venv/bin/activate" ]]; then
    source "$DIR/venv/bin/activate"
fi

if ! command -v mcp-gateway >/dev/null 2>&1 && ! command -v openchatx-claude-gateway >/dev/null 2>&1; then
    echo "Error: Gateway CLI not found in PATH or virtual environment."
    echo "Please run: bash scripts/setup.sh"
    exit 1
fi

CONFIG_FILE="$DIR/config.yaml"
if [[ ! -f "$CONFIG_FILE" ]]; then
    echo "Error: config.yaml not found!"
    echo "Please run: bash scripts/setup.sh"
    exit 1
fi

# Load Encryption Key if present
if [[ -f "$DIR/encryption-key" ]]; then
    export MCP_GATEWAY_ENCRYPTION_KEY="$(cat "$DIR/encryption-key")"
fi

# Check OpenChatX App Runtime (Port 8001)
echo "Checking OpenChatX local runtime..."
if ! nc -z 127.0.0.1 8001 >/dev/null 2>&1; then
    echo "Notice: OpenChatX runtime on 127.0.0.1:8001 is not running."
    if [[ -d "/Applications/OpenChatX.app" ]]; then
        echo "Attempting to launch /Applications/OpenChatX.app..."
        open -g -a OpenChatX || true
        for i in {1..30}; do
            if nc -z 127.0.0.1 8001 >/dev/null 2>&1; then
                echo "OpenChatX runtime is now ready!"
                break
            fi
            sleep 1
        done
    fi
fi

echo "Starting OpenChatX Claude Gateway on port 8765..."
CMD="mcp-gateway"
if command -v openchatx-claude-gateway >/dev/null 2>&1; then
    CMD="openchatx-claude-gateway"
fi

exec "$CMD" run -c "$CONFIG_FILE"
