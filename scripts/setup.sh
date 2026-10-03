#!/bin/bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$DIR"

echo "=================================================="
echo "    OpenChatX Claude Gateway — Setup Wizard       "
echo "=================================================="
echo ""

# 1. Check Python
PYTHON_BIN=""
for cmd in python3 python3.14 python3.13 python3.12 python3.11 python; do
    if command -v "$cmd" >/dev/null 2>&1; then
        VER=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
        MAJOR=$(echo "$VER" | cut -d. -f1)
        MINOR=$(echo "$VER" | cut -d. -f2)
        if [[ "$MAJOR" -ge 3 && "$MINOR" -ge 10 ]]; then
            PYTHON_BIN="$cmd"
            break
        fi
    fi
done

if [[ -z "$PYTHON_BIN" ]]; then
    echo "Error: Python 3.10 or higher is required."
    echo "Please install Python via Homebrew: brew install python"
    exit 1
fi
echo "Using Python: $($PYTHON_BIN --version) ($PYTHON_BIN)"

# 2. Virtual Environment
if [[ ! -d ".venv" ]]; then
    echo "Creating Python virtual environment (.venv)..."
    "$PYTHON_BIN" -m venv .venv
fi
source .venv/bin/activate
echo "Virtual environment activated."

# 3. Install Dependencies
echo "Installing dependencies from requirements.txt..."
pip install --upgrade pip -q
pip install -r requirements.txt -q
pip install -e . -q
echo "Dependencies installed successfully."

# 4. Encryption Key
KEY_FILE="$DIR/encryption-key"
if [[ ! -f "$KEY_FILE" ]]; then
    echo "Generating database encryption key..."
    python3 -c "import secrets, base64; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())" > "$KEY_FILE"
    chmod 600 "$KEY_FILE"
    echo "Saved to $KEY_FILE"
else
    echo "Existing encryption key found at $KEY_FILE."
fi
ENC_KEY=$(cat "$KEY_FILE")

# 5. User Credentials
echo ""
echo "--- Step 1: Admin Account Setup ---"
echo "This account is used to log in and approve Claude's OAuth authorization request in your browser."
read -p "Username [default: openchatx]: " -r INPUT_USER
USERNAME="${INPUT_USER:-openchatx}"

read -s -p "Password [default: openchatx]: " -r INPUT_PASS
echo ""
PASSWORD="${INPUT_PASS:-openchatx}"

echo "Generating secure bcrypt password hash..."
HASH_VAL=$(python3 -c "import bcrypt; print(bcrypt.hashpw('$PASSWORD'.encode(), bcrypt.gensalt(12)).decode())")

# 6. Public Tunnel Domain
echo ""
echo "--- Step 2: Public Ingress Domain ---"
DETECTED_TS_URL=""
TAILSCALE_BIN=""
if command -v tailscale >/dev/null 2>&1; then
    TAILSCALE_BIN="tailscale"
elif [[ -x "/Applications/Tailscale.app/Contents/MacOS/Tailscale" ]]; then
    TAILSCALE_BIN="/Applications/Tailscale.app/Contents/MacOS/Tailscale"
fi

if [[ -n "$TAILSCALE_BIN" ]]; then
    DETECTED_TS_URL=$("$TAILSCALE_BIN" funnel status 2>/dev/null | grep "https://" | head -1 | awk '{print $1}' || true)
fi

if [[ -n "$DETECTED_TS_URL" ]]; then
    echo "Detected Tailscale Funnel URL: $DETECTED_TS_URL"
    read -p "Use this URL for server.public_url? (Y/n): " -r USE_TS
    if [[ "$USE_TS" =~ ^[Nn]$ ]]; then
        read -p "Enter your public HTTPS URL (e.g., https://my-tunnel.trycloudflare.com): " -r PUBLIC_URL
    else
        PUBLIC_URL="$DETECTED_TS_URL"
    fi
else
    echo "No active Tailscale Funnel detected."
    echo "You can set up Tailscale Funnel with: bash scripts/tunnel-tailscale.sh"
    echo "Or use Cloudflare Tunnel with: bash scripts/tunnel-cloudflare.sh"
    read -p "Enter your public HTTPS URL [or press enter to configure later]: " -r INPUT_URL
    PUBLIC_URL="${INPUT_URL:-https://your-tunnel-url.example.com}"
fi

# 7. Write config.yaml
echo ""
echo "Generating config.yaml..."
cat << EOF > "$DIR/config.yaml"
server:
  public_url: $PUBLIC_URL
  host: 127.0.0.1
  port: 8765
  trusted_proxy_ips: 127.0.0.1

auth:
  encryption_key: \${MCP_GATEWAY_ENCRYPTION_KEY:-$ENC_KEY}
  users:
    - username: $USERNAME
      password_hash: "$HASH_VAL"
  access_token_expiry_seconds: 3600
  refresh_token_expiry_seconds: 2592000
  login_session_expiry_seconds: 28800
  allowed_client_redirect_uris:
    - "https://claude.ai/api/mcp/auth_callback"
    - "https://claude.com/api/mcp/auth_callback"
    - "http://localhost:*/callback"
    - "http://127.0.0.1:*/callback"

storage:
  path: $DIR/gateway.db

backends:
  openchatx:
    url: http://127.0.0.1:8001/mcp
    auth:
      type: none
EOF
chmod 600 "$DIR/config.yaml"
echo "config.yaml written successfully."

# 8. Check OpenChatX App
echo ""
echo "--- Step 3: Verifying Local OpenChatX Runtime ---"
if nc -z 127.0.0.1 8001 >/dev/null 2>&1; then
    echo "OpenChatX is running and listening on 127.0.0.1:8001!"
else
    echo "OpenChatX is not currently running."
    if [[ -d "/Applications/OpenChatX.app" ]]; then
        echo "Found /Applications/OpenChatX.app. Launching it now..."
        open -g -a OpenChatX || true
        for i in {1..15}; do
            if nc -z 127.0.0.1 8001 >/dev/null 2>&1; then
                echo "OpenChatX is now ready!"
                break
            fi
            sleep 1
        done
    else
        echo "Please make sure OpenChatX Desktop App is installed and running."
    fi
fi

# 9. macOS LaunchAgent Option
echo ""
echo "--- Step 4: Background Service ---"
read -p "Would you like to install the Gateway as a macOS LaunchAgent (auto-starts on boot)? (Y/n): " -r INSTALL_DAEMON
if [[ ! "$INSTALL_DAEMON" =~ ^[Nn]$ ]]; then
    bash "$DIR/launchd/install-service.sh"
else
    echo "You can start the gateway manually at any time by running:"
    echo "  bash scripts/start.sh"
fi

echo ""
echo "=================================================="
echo "           Setup Complete!                        "
echo "=================================================="
echo "Next step: Add OpenChatX to your Claude account!"
echo "1. Go to Claude Settings -> Connectors -> Yours"
echo "2. Click 'Add connector' -> 'Add custom connector'"
echo "3. Name: OpenChatX"
echo "4. MCP server URL: $PUBLIC_URL/mcp"
echo "5. Select 'Register automatically (DCR)' and click Continue"
echo "6. In the pop-up, sign in with ($USERNAME / $PASSWORD) and click Approve"
echo ""
echo "To check system health at any time, run:"
echo "  bash scripts/status.sh"
echo "=================================================="
