#!/bin/bash
set -e

echo "=== Tailscale Funnel Helper for OpenChatX Claude Gateway ==="
echo ""

TAILSCALE_BIN=""
if command -v tailscale >/dev/null 2>&1; then
    TAILSCALE_BIN="tailscale"
elif [[ -x "/Applications/Tailscale.app/Contents/MacOS/Tailscale" ]]; then
    TAILSCALE_BIN="/Applications/Tailscale.app/Contents/MacOS/Tailscale"
fi

if [[ -z "$TAILSCALE_BIN" ]]; then
    echo "Error: Tailscale is not installed on this machine."
    echo "Download it from: https://tailscale.com/download"
    exit 1
fi

echo "Detected Tailscale: $TAILSCALE_BIN"
echo "Checking Tailscale status..."
STATUS=$("$TAILSCALE_BIN" status 2>/dev/null || true)

if [[ -z "$STATUS" ]]; then
    echo "Error: Tailscale is not logged in or running."
    echo "Please open Tailscale and sign in first."
    exit 1
fi

echo "Current Funnel configuration:"
"$TAILSCALE_BIN" funnel status || true

echo ""
echo "Enabling Tailscale Funnel on port 8765..."
"$TAILSCALE_BIN" funnel --bg --yes 8765

echo ""
echo "=== Success ==="
echo "Tailscale Funnel is now proxying public HTTPS traffic to http://127.0.0.1:8765"
echo ""
echo "Your Funnel domain is:"
"$TAILSCALE_BIN" funnel status 2>&1 | grep "https://" | head -1 || true
echo ""
echo "Copy this HTTPS URL into your config.yaml under 'server.public_url'."
