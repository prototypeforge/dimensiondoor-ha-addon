# DimensionDoor - Home Assistant Remote Access

## Setup Instructions

### 1. Create a DimensionDoor Account

1. Go to [app.dimensiondoor.cloud](https://app.dimensiondoor.cloud)
2. Create an account
3. Navigate to **Tunnel Settings**
4. Click **Create Tunnel** to get your subdomain and auth token

### 2. Install the Add-on

1. In Home Assistant, go to **Settings > Add-ons > Add-on Store**
2. Click the three-dot menu in the top right
3. Select **Repositories**
4. Add the DimensionDoor repository URL
5. Find "DimensionDoor Remote Access" and click **Install**

### 3. Configure

1. Go to the add-on **Configuration** tab
2. Enter your **Auth Token** from step 1
3. Leave other settings as default unless you have a custom setup:
   - `server_url`: The tunnel server (default: `wss://tunnel.dimensiondoor.cloud/ws/tunnel`)
   - `ha_url`: Your local HA URL (default: `http://homeassistant.local:8123`)
   - `log_level`: Logging verbosity (default: `info`)

### 4. Start

1. Click **Start** on the add-on
2. Check the **Log** tab for connection status
3. Your Home Assistant is now accessible at your subdomain URL!

## How It Works

The add-on establishes an outbound WebSocket connection to the DimensionDoor
tunnel server. No port forwarding or VPN is needed - the connection goes
outbound from your network, just like a normal web request.

When someone visits your subdomain URL, the request travels:

```
Browser -> DimensionDoor Server -> WebSocket Tunnel -> This Add-on -> Home Assistant
```

All traffic is encrypted with TLS. The tunnel only carries HTTP/WebSocket
traffic for your Home Assistant instance.

## Troubleshooting

- **"Invalid or inactive tunnel token"**: Your token may be expired or incorrect. Generate a new one at app.dimensiondoor.cloud
- **Connection keeps dropping**: Check your internet connection. The add-on will auto-reconnect with exponential backoff
- **Slow performance**: Check your subscription tier's bandwidth limit at app.dimensiondoor.cloud
