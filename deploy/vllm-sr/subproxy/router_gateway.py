"""Tailnet gateway to the k3s router for other fleet hosts (MBP).

The router Service is ClusterIP-only and has no auth of its own. This gateway listens on groot's
Tailscale address, requires `Authorization: Bearer $VLLM_SR_CLIENT_TOKEN`, and streams requests
to the router unchanged. Only /v1/chat/completions, /v1/responses and /v1/models are forwarded.
"""
from __future__ import annotations

import hmac
import logging
import os
import sys

sys.path.insert(0, os.environ.get("HERMES_AGENT_DIR", "/workspace/hermes-home/hermes-agent"))
import hermes_bootstrap  # noqa: E402,F401

import aiohttp  # noqa: E402
from aiohttp import web  # noqa: E402

log = logging.getLogger("router_gateway")
ALLOWED = {"/v1/chat/completions", "/v1/responses", "/v1/models"}
HOP = {"host", "content-length", "transfer-encoding", "connection", "keep-alive", "authorization", "x-api-key"}


def make_app(token: str, upstream: str) -> web.Application:
    state: dict = {}

    async def on_startup(app):
        state["s"] = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, sock_connect=15, sock_read=1800),
                                           auto_decompress=False)

    async def on_cleanup(app):
        await state["s"].close()

    async def handle(request: web.Request):
        if request.path == "/health":
            return web.json_response({"status": "ok"})
        auth = request.headers.get("authorization", "")
        presented = auth[7:] if auth.lower().startswith("bearer ") else ""
        if not presented or not hmac.compare_digest(presented.encode(), token.encode()):
            return web.json_response({"error": {"message": "bad gateway token"}}, status=401)
        if request.path not in ALLOWED:
            return web.json_response({"error": {"message": f"not forwarded: {request.path}"}}, status=404)
        headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP}
        body = await request.read()
        async with state["s"].request(request.method, upstream + request.path_qs, data=body or None,
                                      headers=headers) as resp:
            out = web.StreamResponse(status=resp.status)
            for k, v in resp.headers.items():
                if k.lower() not in HOP:
                    out.headers[k] = v
            await out.prepare(request)
            async for chunk in resp.content.iter_any():
                await out.write(chunk)
            await out.write_eof()
            return out

    app = web.Application(client_max_size=32 * 1024 * 1024)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    app.router.add_route("*", "/{tail:.*}", handle)
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    token = os.environ.get("VLLM_SR_CLIENT_TOKEN", "")
    if not token:
        sys.exit("VLLM_SR_CLIENT_TOKEN is required")
    upstream = os.environ.get("ROUTER_UPSTREAM", "http://10.43.216.55:8899").rstrip("/")
    host = os.environ.get("GATEWAY_HOST", "100.113.138.100")
    port = int(os.environ.get("GATEWAY_PORT", "8898"))
    web.run_app(make_app(token, upstream), host=host, port=port, access_log=None)


if __name__ == "__main__":
    main()
