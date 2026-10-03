#!/bin/bash
set -e

PLIST_NAME="com.openchatx.claude-gateway.plist"
TARGET_PLIST="$HOME/Library/LaunchAgents/$PLIST_NAME"

echo "=== Uninstalling OpenChatX Claude Gateway LaunchAgent ==="

if launchctl list 2>/dev/null | grep -q "com.openchatx.claude-gateway"; then
    echo "Stopping and unloading launchctl service..."
    launchctl bootout "gui/$(id -u)/com.openchatx.claude-gateway" 2>/dev/null || true
fi

if [[ -f "$TARGET_PLIST" ]]; then
    rm -f "$TARGET_PLIST"
    echo "Removed $TARGET_PLIST"
fi

echo "LaunchAgent uninstalled successfully."
