# Changelog

## 1.0.5

- Auto-configure HA `configuration.yaml` with required `http:` trusted_proxies on startup
- Validate HA configuration via Supervisor API after changes
- Auto-revert if config validation fails

## 1.0.4

- Auto-configure HA `configuration.yaml` with required `http:` trusted_proxies on startup
- Added `pyyaml` dependency for safe YAML parsing

## 1.0.3

- Fixed blank page issue caused by Content-Encoding mismatch
- Strip `Accept-Encoding` from requests to HA
- Strip `Content-Encoding` from HA responses

## 1.0.2

- Strip proxy headers (X-Forwarded-*) to avoid HA 400 Bad Request errors
- No manual `trusted_proxies` configuration needed

## 1.0.1

- Fixed `additional_headers` compatibility with websockets library
- Changed default `ha_url` to `http://homeassistant:8123`

## 1.0.0

- Initial release
- WebSocket tunnel client for remote HA access
- Auto-reconnect with exponential backoff
- WebSocket proxying for HA frontend real-time updates
