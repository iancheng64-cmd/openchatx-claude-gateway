#!/bin/bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "=================================================="
echo "    OpenChatX Claude Gateway — System Status      "
echo "=================================================="
echo ""

# 1. Check OpenChatX App Runtime (Port 8001)
echo -n "[1/4] OpenChatX Core Runtime (127.0.0.1:8001): "
if nc -z 127.0.0.1 8001 >/dev/null 2>&1; then
    echo "ONLINE (Port 8001 is listening)"
else
    echo "OFFLINE"
    echo "      -> Please start the OpenChatX Desktop App (/Applications/OpenChatX.app)"
fi

# 2. Check Gateway Local Listener (Port 8765)
echo -n "[2/4] Gateway Local Server (127.0.0.1:8765):  "
if nc -z 127.0.0.1 8765 >/dev/null 2>&1; then
    HEALTH=$(curl -fsS http://127.0.0.1:8765/healthz 2>/dev/null || echo "error")
    if [[ "$HEALTH" == *"ok"* ]]; then
        echo "ONLINE (/healthz returned 200 OK)"
    else
        echo "ONLINE (Port open, unexpected health check response: $HEALTH)"
    fi
else
    echo "OFFLINE"
    echo "      -> Run: bash scripts/start.sh"
fi

# 3. Check Public Tunnel URL
CONFIG_FILE="$DIR/config.yaml"
if [[ -f "$CONFIG_FILE" ]]; then
    PUBLIC_URL=$(grep "public_url:" "$CONFIG_FILE" | head -1 | awk '{print $2}' | tr -d '"' | tr -d "'")
    echo -n "[3/4] Public Ingress Tunnel ($PUBLIC_URL): "
    if [[ -n "$PUBLIC_URL" && "$PUBLIC_URL" != "https://your-tunnel-url.example.com" ]]; then
        PUB_HEALTH=$(curl -fsS "$PUBLIC_URL/healthz" 2>/dev/null || echo "unreachable")
        if [[ "$PUB_HEALTH" == *"ok"* ]]; then
            echo "REACHABLE (200 OK from internet)"
        else
            echo "UNREACHABLE ($PUB_HEALTH)"
            echo "      -> Ensure Tailscale Funnel or Cloudflare Tunnel is running for port 8765"
        fi
    else
        echo "NOT CONFIGURED in config.yaml"
    fi
else
    echo "[3/4] Public Ingress Tunnel: config.yaml not found (run scripts/setup.sh)"
fi

# 4. Check macOS LaunchAgent Daemon
echo -n "[4/4] macOS Background Daemon (LaunchAgent):  "
if launchctl list 2>/dev/null | grep -q "com.openchatx.claude-gateway"; then
    PID=$(launchctl list | grep "com.openchatx.claude-gateway" | awk '{print $1}')
    echo "ACTIVE (PID: $PID)"
elif launchctl list 2>/dev/null | grep -q "com.ian.openchatx-claude-gateway"; then
    PID=$(launchctl list | grep "com.ian.openchatx-claude-gateway" | awk '{print $1}')
    echo "ACTIVE (com.ian.openchatx-claude-gateway PID: $PID)"
else
    echo "NOT REGISTERED (Runs in foreground or not installed as daemon)"
fi

echo ""
echo "=================================================="
