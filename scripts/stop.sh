#!/bin/bash
set -e

echo "Stopping OpenChatX Claude Gateway..."

# 1. Stop LaunchAgent if running
if launchctl list 2>/dev/null | grep -q "com.openchatx.claude-gateway"; then
    echo "Stopping launchctl service com.openchatx.claude-gateway..."
    launchctl stop com.openchatx.claude-gateway 2>/dev/null || true
fi

# 2. Stop any process listening on 8765
PID=$(lsof -tiTCP:8765 -sTCP:LISTEN 2>/dev/null || true)
if [[ -n "$PID" ]]; then
    echo "Terminating process on port 8765 (PID: $PID)..."
    kill "$PID" 2>/dev/null || true
    sleep 1
    if kill -0 "$PID" 2>/dev/null; then
        kill -9 "$PID" 2>/dev/null || true
    fi
    echo "Gateway stopped."
else
    echo "No process running on port 8765."
fi
