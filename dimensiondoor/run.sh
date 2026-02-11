#!/usr/bin/env bash
set -e

# Read config from Home Assistant add-on options
if [ -f /data/options.json ]; then
    AUTH_TOKEN=$(python3 -c "import json; print(json.load(open('/data/options.json'))['auth_token'])")
    SERVER_URL=$(python3 -c "import json; print(json.load(open('/data/options.json'))['server_url'])")
    HA_URL=$(python3 -c "import json; print(json.load(open('/data/options.json'))['ha_url'])")
    LOG_LEVEL=$(python3 -c "import json; print(json.load(open('/data/options.json')).get('log_level', 'info'))")
else
    # Fallback to environment variables for standalone testing
    AUTH_TOKEN="${AUTH_TOKEN:-}"
    SERVER_URL="${SERVER_URL:-wss://tunnel.dimensiondoor.cloud/ws/tunnel}"
    HA_URL="${HA_URL:-http://localhost:8123}"
    LOG_LEVEL="${LOG_LEVEL:-info}"
fi

echo "======================================"
echo "  DimensionDoor Tunnel Client"
echo "======================================"
echo "Server:   ${SERVER_URL}"
echo "HA URL:   ${HA_URL}"
echo "Log:      ${LOG_LEVEL}"
echo ""

# Ensure HA configuration.yaml has the required http: trusted_proxies config
echo "Checking Home Assistant configuration..."
python3 /app/configure_ha.py || echo "WARNING: Could not verify HA configuration (non-fatal)"
echo ""

if [ -z "$AUTH_TOKEN" ]; then
    echo "ERROR: auth_token is not configured!"
    echo "Please set your auth token in the add-on configuration."
    echo "You can get your token from https://app.dimensiondoor.cloud/tunnel"
    exit 1
fi

RESTART_DELAY=30

while true; do
    set +e
    python3 /app/tunnel_client.py \
        --token "$AUTH_TOKEN" \
        --server "$SERVER_URL" \
        --ha-url "$HA_URL" \
        --log-level "$LOG_LEVEL"
    EXIT_CODE=$?
    set -e

    if [ $EXIT_CODE -eq 0 ]; then
        echo "Tunnel client exited cleanly. Stopping."
        break
    fi

    echo "Tunnel client exited with code ${EXIT_CODE}. Restarting in ${RESTART_DELAY}s..."
    sleep "$RESTART_DELAY"
done
