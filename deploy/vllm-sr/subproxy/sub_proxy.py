"""Subscription proxy for the vLLM Semantic Router "paid" lanes.

The router (k3s pod) speaks each provider's wire format; this proxy only attaches the owner's
subscription credentials from the Hermes credential pool, the same way Hermes itself does:

  /anthropic/v1/messages      -> api.anthropic.com   (Claude OAuth pool: ultra, sub2, ANTHROPIC_TOKEN)
  /codex/v1/responses         -> chatgpt.com/backend-api/codex/responses (ChatGPT OAuth)
  /xai/v1/chat/completions    -> xAI Grok OAuth base (OpenAI-compatible)

Credential selection, refresh and 429 cooldown/rotation are Hermes' own CredentialPool code, so
this process never forks a refresh-token family. Every request must carry the shared bearer in
SUBPROXY_TOKEN (the router's k8s Secret); the upstream never sees it.

Run with Hermes' interpreter (see sub-proxy.service).
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import sys
import time

sys.path.insert(0, os.environ.get("HERMES_AGENT_DIR", "/workspace/hermes-home/hermes-agent"))
import hermes_bootstrap  # noqa: E402,F401  (Hermes dependency paths)

import aiohttp  # noqa: E402
from aiohttp import web  # noqa: E402

from agent.anthropic_adapter import (  # noqa: E402
    _CLAUDE_CODE_SYSTEM_PREFIX, _OAUTH_ONLY_BETAS, _common_betas_for_base_url, _get_claude_code_version,
)
from agent.codex_headers import CODEX_AUX_BASE_URL, codex_cloudflare_headers  # noqa: E402
from agent.credential_pool import load_pool  # noqa: E402
from hermes_cli.auth import DEFAULT_XAI_OAUTH_BASE_URL  # noqa: E402

log = logging.getLogger("sub_proxy")
ANTHROPIC_BASE = "https://api.anthropic.com"
HOP = {"host", "content-length", "transfer-encoding", "connection", "keep-alive", "authorization",
       "x-api-key", "proxy-authorization", "accept-encoding", "content-encoding"}
LEDGER = os.environ.get("SUBPROXY_LEDGER", os.path.expanduser("~/.local/state/sub-proxy/requests.jsonl"))


def _record(row: dict) -> None:
    try:
        os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
        with open(LEDGER, "a") as f:
            f.write(json.dumps(row) + "\n")
    except OSError:
        pass


class Lane:
    """One subscription pool. ``select``/``rotate`` run in a thread: pool calls take file locks
    and may POST a token refresh."""

    def __init__(self, name: str, pool_provider: str):
        self.name, self.pool_provider = name, pool_provider
        self.lock = asyncio.Lock()

    def _select(self):
        pool = load_pool(self.pool_provider)
        entry = pool.select()
        return pool, entry

    async def select(self):
        return await asyncio.to_thread(self._select)

    async def rotate(self, pool, entry, status: int):
        def _do():
            if status == 401:
                fresh = pool.try_refresh_current() if hasattr(pool, "try_refresh_current") else None
                if fresh is not None:
                    return fresh
            return pool.mark_exhausted_and_rotate(status_code=status, credential_id=entry.id)
        return await asyncio.to_thread(_do)


def _bearer(entry) -> str:
    return str(getattr(entry, "runtime_api_key", None) or entry.access_token or "").strip()


# --- per-lane request builders: (url, headers, body_bytes, upstream_streams, client_wants_stream)

def build_anthropic(entry, body: dict, in_headers):
    system = body.get("system")
    prefix = {"type": "text", "text": _CLAUDE_CODE_SYSTEM_PREFIX}
    if isinstance(system, str):
        body["system"] = [prefix] + ([{"type": "text", "text": system}] if system else [])
    elif isinstance(system, list):
        if not (system and isinstance(system[0], dict) and system[0].get("text") == _CLAUDE_CODE_SYSTEM_PREFIX):
            body["system"] = [prefix] + system
    else:
        body["system"] = [prefix]
    betas = _common_betas_for_base_url(ANTHROPIC_BASE) + _OAUTH_ONLY_BETAS
    extra = [b.strip() for b in (in_headers.get("anthropic-beta") or "").split(",") if b.strip()]
    headers = {
        "authorization": f"Bearer {_bearer(entry)}",
        "anthropic-version": in_headers.get("anthropic-version") or "2023-06-01",
        "anthropic-beta": ",".join(dict.fromkeys(betas + extra)),
        "user-agent": f"claude-code/{_get_claude_code_version()} (external, cli)",
        "x-app": "cli",
        "content-type": "application/json",
    }
    stream = bool(body.get("stream"))
    return f"{ANTHROPIC_BASE}/v1/messages", headers, json.dumps(body).encode(), stream, stream


def build_codex(entry, body: dict, in_headers):
    token = _bearer(entry)
    wants_stream = bool(body.get("stream"))
    body["stream"] = True  # the ChatGPT Codex backend only streams
    body["store"] = False
    if not body.get("instructions"):
        # Lift system/developer turns into `instructions` (the backend requires it).
        inp = body.get("input")
        lifted, rest = [], []
        if isinstance(inp, list):
            for item in inp:
                if isinstance(item, dict) and item.get("role") in ("system", "developer"):
                    c = item.get("content")
                    if isinstance(c, str):
                        lifted.append(c)
                    elif isinstance(c, list):
                        lifted.extend(p.get("text", "") for p in c if isinstance(p, dict))
                else:
                    rest.append(item)
            body["input"] = rest
        elif isinstance(inp, str):
            body["input"] = [{"role": "user", "content": [{"type": "input_text", "text": inp}]}]
        body["instructions"] = "\n\n".join(t for t in lifted if t) or "You are a helpful assistant."
    for k in ("max_output_tokens", "max_tokens", "temperature", "top_p", "service_tier"):
        body.pop(k, None)  # rejected by the subscription backend
    headers = {"authorization": f"Bearer {token}", "content-type": "application/json",
               "accept": "text/event-stream"}
    headers.update(codex_cloudflare_headers(token, base_url=CODEX_AUX_BASE_URL))
    return f"{CODEX_AUX_BASE_URL}/responses", headers, json.dumps(body).encode(), True, wants_stream


def build_xai(entry, body: dict, in_headers, rel: str):
    base = str(getattr(entry, "runtime_base_url", None) or entry.base_url or DEFAULT_XAI_OAUTH_BASE_URL).rstrip("/")
    headers = {"authorization": f"Bearer {_bearer(entry)}", "content-type": "application/json"}
    stream = bool(body.get("stream"))
    return f"{base}{rel}", headers, json.dumps(body).encode(), stream, stream


LANES = {
    "anthropic": (Lane("anthropic", "anthropic"), {"/messages"}),
    "codex": (Lane("codex", "openai-codex"), {"/responses"}),
    "xai": (Lane("xai", "xai-oauth"), {"/chat/completions", "/responses"}),
}


async def _aggregate_codex_sse(resp) -> tuple[int, bytes]:
    """Collapse a Codex SSE stream into the final Responses JSON object."""
    final, buf = None, b""
    async for chunk in resp.content.iter_any():
        buf += chunk
        while b"\n\n" in buf:
            event, buf = buf.split(b"\n\n", 1)
            for line in event.split(b"\n"):
                if line.startswith(b"data:"):
                    try:
                        data = json.loads(line[5:].strip())
                    except ValueError:
                        continue
                    if data.get("type") in ("response.completed", "response.incomplete", "response.failed"):
                        final = data.get("response")
    if final is None:
        return 502, json.dumps({"error": {"message": "codex stream ended without a final response"}}).encode()
    status = 200 if final.get("status") in ("completed", "incomplete") else 502
    return status, json.dumps(final).encode()


def make_app(token: str) -> web.Application:
    session: dict[str, aiohttp.ClientSession] = {}

    async def on_startup(app):
        session["s"] = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=600),
                                             auto_decompress=True)

    async def on_cleanup(app):
        await session["s"].close()

    async def health(request):
        return web.json_response({"status": "ok", "lanes": sorted(LANES)})

    async def handle(request: web.Request):
        auth = request.headers.get("authorization", "")
        presented = auth[7:] if auth.lower().startswith("bearer ") else request.headers.get("x-api-key", "")
        if not token or not hmac.compare_digest(presented.encode(), token.encode()):
            return web.json_response({"error": {"message": "bad proxy token"}}, status=401)
        lane_name, rel = request.match_info["lane"], "/" + request.match_info["tail"]
        if lane_name not in LANES or rel not in LANES[lane_name][1]:
            return web.json_response({"error": {"message": f"not forwarded: {lane_name}{rel}"}}, status=404)
        lane = LANES[lane_name][0]
        try:
            raw = json.loads(await request.read() or b"{}")
        except ValueError:
            return web.json_response({"error": {"message": "body is not JSON"}}, status=400)
        t0 = time.time()
        pool, entry = await lane.select()
        tried = []
        for attempt in range(3):
            if entry is None:
                break
            body = json.loads(json.dumps(raw))
            if lane_name == "anthropic":
                url, headers, data, up_stream, want_stream = build_anthropic(entry, body, request.headers)
            elif lane_name == "codex":
                url, headers, data, up_stream, want_stream = build_codex(entry, body, request.headers)
            else:
                url, headers, data, up_stream, want_stream = build_xai(entry, body, request.headers, rel)
            tried.append(getattr(entry, "label", None) or entry.id)
            resp = await session["s"].post(url, data=data, headers=headers)
            if resp.status in (401, 429) and attempt < 2:
                detail = (await resp.read())[:300]
                log.warning("%s %s via %s -> %s %s; rotating", lane_name, rel, tried[-1], resp.status, detail)
                resp.release()
                entry = await lane.rotate(pool, entry, resp.status)
                continue
            row = {"ts": t0, "lane": lane_name, "model": raw.get("model"), "credential": tried[-1],
                   "tried": tried, "status": resp.status, "stream": want_stream}
            if lane_name == "codex" and not want_stream and resp.status == 200:
                status, out = await _aggregate_codex_sse(resp)
                resp.release()
                row.update(status=status, secs=round(time.time() - t0, 2))
                _record(row)
                return web.Response(status=status, body=out, content_type="application/json")
            out = web.StreamResponse(status=resp.status)
            for k, v in resp.headers.items():
                if k.lower() not in HOP and not k.lower().startswith(("set-cookie", "cf-", "anthropic-organization")):
                    out.headers[k] = v
            await out.prepare(request)
            async for chunk in resp.content.iter_any():
                await out.write(chunk)
            await out.write_eof()
            resp.release()
            row["secs"] = round(time.time() - t0, 2)
            _record(row)
            return out
        _record({"ts": t0, "lane": lane_name, "model": raw.get("model"), "tried": tried, "status": 429})
        return web.json_response({"error": {"type": "rate_limit_error",
                                            "message": f"every {lane_name} subscription is cooling down"}}, status=429)

    app = web.Application(client_max_size=32 * 1024 * 1024)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    app.router.add_get("/health", health)
    app.router.add_post("/{lane}/v1/{tail:.*}", handle)
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    token = os.environ.get("SUBPROXY_TOKEN") or os.environ.get("VLLM_SR_SUBPROXY_TOKEN", "")
    if not token:
        sys.exit("SUBPROXY_TOKEN is required")
    host = os.environ.get("SUBPROXY_HOST", "10.42.0.1")
    port = int(os.environ.get("SUBPROXY_PORT", "18800"))
    web.run_app(make_app(token), host=host, port=port, access_log=None)


if __name__ == "__main__":
    main()
