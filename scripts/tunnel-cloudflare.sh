#!/bin/bash
set -e

echo "=== Cloudflare Quick Tunnel Helper for OpenChatX Claude Gateway ==="
echo ""

if ! command -v cloudflared >/dev/null 2>&1; then
    echo "cloudflared is not installed."
    echo "Install via Homebrew: brew install cloudflared"
    echo ""
    read -p "Would you like to install cloudflared via brew now? (y/N): " -r CONFIRM
    if [[ "$CONFIRM" =~ ^[Yy]$ ]]; then
        brew install cloudflared
    else
        exit 1
    fi
fi

echo "Starting free Cloudflare Quick Tunnel for http://127.0.0.1:8765..."
echo "Note: Quick Tunnels assign a temporary *.trycloudflare.com URL."
echo "Keep this terminal open, or configure a named Cloudflare Tunnel for permanent URLs."
echo ""

cloudflared tunnel --url http://127.0.0.1:8765
