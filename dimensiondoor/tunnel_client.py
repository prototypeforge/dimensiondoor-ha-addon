#!/usr/bin/env python3
"""
DimensionDoor Tunnel Client for Home Assistant

This runs as an HA add-on. It establishes a persistent WebSocket connection
to the DimensionDoor tunnel server, receives HTTP requests, proxies them
to the local HA instance, and returns responses through the tunnel.
"""

import argparse
import asyncio
import logging
import signal
import sys
from typing import Dict, Optional

import aiohttp
import msgpack
import websockets
import websockets.client

logger = logging.getLogger("dimensiondoor")


class TunnelClient:
    def __init__(self, token: str, server_url: str, ha_url: str):
        self.token = token
        self.server_url = server_url
        self.ha_url = ha_url.rstrip("/")
        self._ws: Optional[websockets.client.WebSocketClientProtocol] = None
        self._http_session: Optional[aiohttp.ClientSession] = None
        self._ws_connections: Dict[str, aiohttp.ClientWebSocketResponse] = {}
        self._running = True
        self._reconnect_delay = 1  # seconds, with exponential backoff

    async def start(self):
        """Main entry point - connects and handles reconnection."""
        self._http_session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=60)
        )

        while self._running:
            try:
                await self._connect()
            except (
                websockets.exceptions.ConnectionClosed,
                websockets.exceptions.InvalidStatusCode,
                ConnectionRefusedError,
                OSError,
            ) as e:
                logger.warning(f"Connection lost: {e}")
            except Exception as e:
                logger.exception(f"Unexpected error: {e}")

            if self._running:
                logger.info(f"Reconnecting in {self._reconnect_delay}s...")
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(self._reconnect_delay * 2, 60)

        await self._cleanup()

    async def _connect(self):
        """Establish WebSocket connection to the tunnel server."""
        # Pass token both as header and query param (some proxies strip Authorization)
        url = self.server_url
        separator = "&" if "?" in url else "?"
        url_with_token = f"{url}{separator}token={self.token}"

        extra_headers = {"Authorization": f"Bearer {self.token}"}

        logger.info(f"Connecting to {self.server_url}...")

        try:
            async with websockets.client.connect(
                url_with_token,
                extra_headers=extra_headers,
                max_size=10 * 1024 * 1024,  # 10MB
                ping_interval=20,
                ping_timeout=30,
                close_timeout=10,
            ) as ws:
                self._ws = ws

                # Read the welcome message
                welcome = await ws.recv()
                if isinstance(welcome, str):
                    import json
                    data = json.loads(welcome)
                    if data.get("error"):
                        logger.error(f"Server rejected connection: {data['error']}")
                        self._running = False
                        return
                    logger.info(f"Connected! URL: {data.get('url', 'unknown')}")
                    self._reconnect_delay = 1  # Reset backoff only after confirmed success
                else:
                    logger.info("Connected to tunnel server")
                    self._reconnect_delay = 1

                # Process incoming messages
                async for message in ws:
                    if isinstance(message, bytes):
                        await self._handle_message(message)
        except websockets.exceptions.InvalidStatusCode as e:
            logger.error(f"Server returned HTTP {e.status_code} - check tunnel server logs")
            raise

    async def _handle_message(self, data: bytes):
        """Handle an incoming message from the tunnel server."""
        try:
            msg = msgpack.unpackb(data, raw=False)
        except Exception as e:
            logger.error(f"Failed to unpack message: {e}")
            return

        msg_type = msg.get("type", "")

        if msg_type == "http_request":
            asyncio.create_task(self._handle_http_request(msg))
        elif msg_type == "ws_open":
            asyncio.create_task(self._handle_ws_open(msg))
        elif msg_type == "ws_data":
            asyncio.create_task(self._handle_ws_data(msg))
        elif msg_type == "ws_close":
            asyncio.create_task(self._handle_ws_close(msg))
        else:
            logger.warning(f"Unknown message type: {msg_type}")

    async def _handle_http_request(self, msg: dict):
        """Proxy an HTTP request to the local HA instance."""
        request_id = msg.get("request_id", "")
        method = msg.get("method", "GET")
        path = msg.get("path", "/")
        query_string = msg.get("query_string", "")
        headers = msg.get("headers", {})
        body = msg.get("body", b"")

        url = f"{self.ha_url}{path}"
        if query_string:
            url = f"{url}?{query_string}"

        # Remove headers that shouldn't be forwarded
        # - Proxy headers (X-Forwarded-*): triggers 400 if HA doesn't trust us
        # - Accept-Encoding: aiohttp auto-decompresses, so we must not tell HA
        #   to compress (otherwise Content-Encoding header won't match the body)
        forward_headers = {}
        skip = {
            "host", "connection", "upgrade", "transfer-encoding", "content-length",
            "x-forwarded-for", "x-forwarded-proto", "x-forwarded-host",
            "x-real-ip", "x-forwarded-server",
            "accept-encoding",
        }
        for k, v in headers.items():
            if k.lower() not in skip:
                forward_headers[k] = v

        logger.debug(f"Proxying {method} {url}")

        try:
            async with self._http_session.request(
                method=method,
                url=url,
                headers=forward_headers,
                data=body if body else None,
                allow_redirects=False,
                ssl=False,
            ) as resp:
                resp_body = await resp.read()
                resp_headers = dict(resp.headers)

                logger.debug(f"HA responded: {resp.status} ({len(resp_body)} bytes) for {method} {path}")

                if resp.status == 400:
                    logger.warning(
                        f"HA returned 400 Bad Request for {path}. "
                        "Make sure your HA configuration.yaml has: "
                        "http: {{ use_x_forwarded_for: true, trusted_proxies: [172.30.33.0/24] }}"
                    )

                # Remove hop-by-hop headers and Content-Encoding
                # (aiohttp auto-decompresses, so the body is already plain text
                #  but the header would still say gzip - causing browser issues)
                for h in ("Transfer-Encoding", "Connection", "Keep-Alive",
                           "Content-Length", "Content-Encoding"):
                    resp_headers.pop(h, None)

                response = {
                    "type": "http_response",
                    "request_id": request_id,
                    "status": resp.status,
                    "headers": resp_headers,
                    "body": resp_body,
                }

        except aiohttp.ClientError as e:
            logger.error(f"HA request failed: {e}")
            response = {
                "type": "http_response",
                "request_id": request_id,
                "status": 502,
                "headers": {"Content-Type": "text/plain"},
                "body": b"Home Assistant is not responding",
            }
        except Exception as e:
            logger.exception(f"Unexpected proxy error: {e}")
            response = {
                "type": "http_response",
                "request_id": request_id,
                "status": 500,
                "headers": {"Content-Type": "text/plain"},
                "body": b"Internal tunnel error",
            }

        # Send response back through the tunnel
        if self._ws and not self._ws.closed:
            packed = msgpack.packb(response, use_bin_type=True)
            await self._ws.send(packed)

    async def _handle_ws_open(self, msg: dict):
        """Open a WebSocket connection to HA for proxying browser WebSocket."""
        ws_id = msg.get("ws_id", "")
        path = msg.get("path", "/api/websocket")
        query_string = msg.get("query_string", "")

        url = f"{self.ha_url}{path}"
        if query_string:
            url = f"{url}?{query_string}"

        # Convert http:// to ws://
        ws_url = url.replace("http://", "ws://").replace("https://", "wss://")

        try:
            ws_conn = await self._http_session.ws_connect(ws_url)
            self._ws_connections[ws_id] = ws_conn

            # Start reading from HA WebSocket and forwarding to tunnel
            asyncio.create_task(self._relay_ws_from_ha(ws_id, ws_conn))

        except Exception as e:
            logger.error(f"Failed to open WS to HA: {e}")
            # Send close to server
            if self._ws and not self._ws.closed:
                close_msg = msgpack.packb({
                    "type": "ws_close",
                    "ws_id": ws_id,
                }, use_bin_type=True)
                await self._ws.send(close_msg)

    async def _relay_ws_from_ha(self, ws_id: str, ws_conn: aiohttp.ClientWebSocketResponse):
        """Read from HA WebSocket and forward to tunnel server."""
        try:
            async for msg in ws_conn:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    relay = msgpack.packb({
                        "type": "ws_data",
                        "ws_id": ws_id,
                        "data": msg.data.encode() if isinstance(msg.data, str) else msg.data,
                        "is_text": True,
                    }, use_bin_type=True)
                    if self._ws and not self._ws.closed:
                        await self._ws.send(relay)

                elif msg.type == aiohttp.WSMsgType.BINARY:
                    relay = msgpack.packb({
                        "type": "ws_data",
                        "ws_id": ws_id,
                        "data": msg.data,
                        "is_text": False,
                    }, use_bin_type=True)
                    if self._ws and not self._ws.closed:
                        await self._ws.send(relay)

                elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                    break
        except Exception as e:
            logger.error(f"WS relay error for {ws_id}: {e}")
        finally:
            self._ws_connections.pop(ws_id, None)
            if self._ws and not self._ws.closed:
                close_msg = msgpack.packb({
                    "type": "ws_close",
                    "ws_id": ws_id,
                }, use_bin_type=True)
                await self._ws.send(close_msg)

    async def _handle_ws_data(self, msg: dict):
        """Forward WebSocket data from browser to HA."""
        ws_id = msg.get("ws_id", "")
        data = msg.get("data", b"")
        is_text = msg.get("is_text", False)

        ws_conn = self._ws_connections.get(ws_id)
        if not ws_conn or ws_conn.closed:
            return

        try:
            if is_text:
                text = data.decode() if isinstance(data, bytes) else data
                await ws_conn.send_str(text)
            else:
                await ws_conn.send_bytes(data if isinstance(data, bytes) else data.encode())
        except Exception as e:
            logger.error(f"Failed to send WS data to HA: {e}")

    async def _handle_ws_close(self, msg: dict):
        """Close a proxied WebSocket connection to HA."""
        ws_id = msg.get("ws_id", "")
        ws_conn = self._ws_connections.pop(ws_id, None)
        if ws_conn and not ws_conn.closed:
            await ws_conn.close()

    async def _cleanup(self):
        """Clean up all connections."""
        for ws_id, ws_conn in list(self._ws_connections.items()):
            if not ws_conn.closed:
                await ws_conn.close()
        self._ws_connections.clear()

        if self._http_session and not self._http_session.closed:
            await self._http_session.close()

    def stop(self):
        self._running = False


def main():
    parser = argparse.ArgumentParser(description="DimensionDoor Tunnel Client")
    parser.add_argument("--token", required=True, help="Auth token from DimensionDoor")
    parser.add_argument("--server", default="wss://tunnel.dimensiondoor.cloud/ws/tunnel", help="Tunnel server URL")
    parser.add_argument("--ha-url", default="http://localhost:8123", help="Local Home Assistant URL")
    parser.add_argument("--log-level", default="info", choices=["debug", "info", "warning", "error"])
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    client = TunnelClient(
        token=args.token,
        server_url=args.server,
        ha_url=args.ha_url,
    )

    loop = asyncio.new_event_loop()

    def signal_handler():
        logger.info("Shutting down...")
        client.stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, signal_handler)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            signal.signal(sig, lambda s, f: signal_handler())

    try:
        loop.run_until_complete(client.start())
    except KeyboardInterrupt:
        pass
    finally:
        loop.close()


if __name__ == "__main__":
    main()
