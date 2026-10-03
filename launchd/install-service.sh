#!/bin/bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLIST_NAME="com.openchatx.claude-gateway.plist"
TARGET_DIR="$HOME/Library/LaunchAgents"
TARGET_PLIST="$TARGET_DIR/$PLIST_NAME"

echo "=== Installing OpenChatX Claude Gateway LaunchAgent ==="

mkdir -p "$TARGET_DIR"

# Unload existing if loaded
launchctl bootout "gui/$(id -u)/com.openchatx.claude-gateway" 2>/dev/null || true

# Replace placeholders
sed "s|{{PROJECT_DIR}}|$DIR|g" "$DIR/launchd/com.openchatx.claude-gateway.plist.template" > "$TARGET_PLIST"
chmod 644 "$TARGET_PLIST"

echo "Loading LaunchAgent into launchctl..."
launchctl bootstrap "gui/$(id -u)" "$TARGET_PLIST"
launchctl kickstart -k "gui/$(id -u)/com.openchatx.claude-gateway"

sleep 2
echo "Service status:"
launchctl list | grep "com.openchatx.claude-gateway" || echo "Notice: Service starting..."

echo ""
echo "=== Success ==="
echo "OpenChatX Claude Gateway is now installed as a background service and will start automatically upon login."
echo "Log file: $DIR/gateway.err.log"
echo "To uninstall: bash launchd/uninstall-service.sh"
