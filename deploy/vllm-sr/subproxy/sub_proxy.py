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
from agent.codex_responses_adapter import _chat_messages_to_responses_input, _responses_tools  # noqa: E402
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


def build_codexchat(entry, body: dict, in_headers):
    """chat.completions request -> Codex Responses request (Hermes' own converters)."""
    msgs = body.get("messages") or []
    system = [m for m in msgs if m.get("role") in ("system", "developer")]
    rest = [m for m in msgs if m.get("role") not in ("system", "developer")]
    instr = []
    for m in system:
        c = m.get("content")
        instr.append(c if isinstance(c, str) else "\n".join(p.get("text", "") for p in c or [] if isinstance(p, dict)))
    rb = {"model": body.get("model"), "instructions": "\n\n".join(t for t in instr if t) or "You are a helpful assistant.",
          "input": _chat_messages_to_responses_input(rest, replay_encrypted_reasoning=False),
          "stream": True, "store": False, "parallel_tool_calls": body.get("parallel_tool_calls", True)}
    tools = _responses_tools(body.get("tools"))
    if tools:
        rb["tools"] = tools
        tc = body.get("tool_choice")
        if isinstance(tc, str):
            rb["tool_choice"] = tc
        elif isinstance(tc, dict) and tc.get("function"):
            rb["tool_choice"] = {"type": "function", "name": tc["function"].get("name")}
    effort = body.get("reasoning_effort") or (body.get("reasoning") or {}).get("effort")
    if effort:
        rb["reasoning"] = {"effort": effort}
    token = _bearer(entry)
    headers = {"authorization": f"Bearer {token}", "content-type": "application/json", "accept": "text/event-stream"}
    headers.update(codex_cloudflare_headers(token, base_url=CODEX_AUX_BASE_URL))
    return f"{CODEX_AUX_BASE_URL}/responses", headers, json.dumps(rb).encode(), True, bool(body.get("stream"))


class _ChatFromResponses:
    """Turns Codex Responses SSE events into chat.completion chunks."""

    def __init__(self, model):
        self.model, self.id, self.created = model, "chatcmpl-subproxy", int(time.time())
        self.tool_index: dict[str, int] = {}
        self.usage = None
        self.saw_tool = False
        self.text, self.calls = [], {}

    def chunk(self, delta, finish=None, usage=None):
        c = {"id": self.id, "object": "chat.completion.chunk", "created": self.created, "model": self.model,
             "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
        if usage is not None:
            c["usage"] = usage
        return c

    def on_event(self, ev):
        t, out = ev.get("type"), []
        if t == "response.created":
            self.id = (ev.get("response") or {}).get("id") or self.id
            out.append(self.chunk({"role": "assistant", "content": ""}))
        elif t == "response.output_text.delta":
            self.text.append(ev.get("delta") or "")
            out.append(self.chunk({"content": ev.get("delta") or ""}))
        elif t == "response.output_item.added" and (ev.get("item") or {}).get("type") == "function_call":
            it = ev["item"]
            i = self.tool_index.setdefault(it.get("id") or it.get("call_id"), len(self.tool_index))
            self.saw_tool = True
            self.calls[i] = {"id": it.get("call_id"), "type": "function", "function": {"name": it.get("name"), "arguments": ""}}
            out.append(self.chunk({"tool_calls": [{"index": i, "id": it.get("call_id"), "type": "function",
                                                   "function": {"name": it.get("name"), "arguments": ""}}]}))
        elif t == "response.function_call_arguments.delta":
            i = self.tool_index.get(ev.get("item_id"), 0)
            if i in self.calls:
                self.calls[i]["function"]["arguments"] += ev.get("delta") or ""
            out.append(self.chunk({"tool_calls": [{"index": i, "function": {"arguments": ev.get("delta") or ""}}]}))
        elif t in ("response.completed", "response.incomplete"):
            u = (ev.get("response") or {}).get("usage") or {}
            self.usage = {"prompt_tokens": u.get("input_tokens", 0), "completion_tokens": u.get("output_tokens", 0),
                          "total_tokens": u.get("total_tokens", 0),
                          "prompt_tokens_details": {"cached_tokens": (u.get("input_tokens_details") or {}).get("cached_tokens", 0)},
                          "completion_tokens_details": {"reasoning_tokens": (u.get("output_tokens_details") or {}).get("reasoning_tokens", 0)}}
            finish = "tool_calls" if self.saw_tool else ("length" if t == "response.incomplete" else "stop")
            out.append(self.chunk({}, finish=finish, usage=self.usage))
            self.finish = finish
        elif t in ("response.failed", "error"):
            err = (ev.get("response") or {}).get("error") or ev.get("error") or ev
            raise RuntimeError(json.dumps(err)[:300])
        return out

    def final(self):
        msg = {"role": "assistant", "content": "".join(self.text) or None}
        if self.calls:
            msg["tool_calls"] = [self.calls[i] for i in sorted(self.calls)]
        return {"id": self.id, "object": "chat.completion", "created": self.created, "model": self.model,
                "choices": [{"index": 0, "message": msg, "finish_reason": getattr(self, "finish", "stop")}],
                "usage": self.usage or {}}


async def _iter_sse(resp):
    buf = b""
    async for chunk in resp.content.iter_any():
        buf += chunk
        while b"\n\n" in buf:
            event, buf = buf.split(b"\n\n", 1)
            for line in event.split(b"\n"):
                if line.startswith(b"data:"):
                    try:
                        yield json.loads(line[5:].strip())
                    except ValueError:
                        pass


LANES = {
    "anthropic": (Lane("anthropic", "anthropic"), {"/messages"}),
    "codex": (Lane("codex", "openai-codex"), {"/responses"}),
    # Chat Completions facade over the Codex backend: the router passes OpenAI SSE through for
    # openai-format backends, but cannot stream a Responses-format backend.
    "codexchat": (Lane("codexchat", "openai-codex"), {"/chat/completions"}),
    "xai": (Lane("xai", "xai-oauth"), {"/chat/completions", "/responses"}),
}


# --- Anthropic response canonicalizer. The router's strict decoder rejects any field outside the
# classic Messages v1 schema (seen: usage.speed, container, stop_details on Opus 5.5).
_A_BLOCK = {"text": ("type", "text"), "tool_use": ("type", "id", "name", "input"),
            "thinking": ("type", "thinking", "signature"), "redacted_thinking": ("type", "data")}
_A_DELTA = {"text_delta": ("type", "text"), "input_json_delta": ("type", "partial_json"),
            "thinking_delta": ("type", "thinking"), "signature_delta": ("type", "signature")}
_A_USAGE = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


def _pick(d, keys):
    return {k: d[k] for k in keys if isinstance(d, dict) and k in d}


def _a_block(b):
    return _pick(b, _A_BLOCK.get(b.get("type"), ("type",))) if isinstance(b, dict) else b


def _a_message(m):
    out = _pick(m, ("id", "type", "role", "model", "stop_reason", "stop_sequence"))
    if "content" in m:
        out["content"] = [_a_block(b) for b in m.get("content") or []]
    if "usage" in m:
        out["usage"] = _pick(m["usage"], _A_USAGE)
    return out


def canon_anthropic_event(ev: dict) -> dict:
    t = ev.get("type")
    if t == "message" or (t is None and "content" in ev):
        return _a_message(ev)
    if t == "message_start":
        return {"type": t, "message": _a_message(ev.get("message") or {})}
    if t == "content_block_start":
        return {"type": t, "index": ev.get("index"), "content_block": _a_block(ev.get("content_block") or {})}
    if t == "content_block_delta":
        d = ev.get("delta") or {}
        return {"type": t, "index": ev.get("index"), "delta": _pick(d, _A_DELTA.get(d.get("type"), ("type",)))}
    if t == "content_block_stop":
        return {"type": t, "index": ev.get("index")}
    if t == "message_delta":
        return {"type": t, "delta": _pick(ev.get("delta") or {}, ("stop_reason", "stop_sequence")),
                "usage": _pick(ev.get("usage") or {}, _A_USAGE)}
    if t in ("message_stop", "ping"):
        return {"type": t}
    if t == "error":
        return {"type": t, "error": _pick(ev.get("error") or {}, ("type", "message"))}
    return ev


async def _relay_anthropic_sse(resp, out):
    buf = b""
    async for chunk in resp.content.iter_any():
        buf += chunk
        while b"\n\n" in buf:
            event, buf = buf.split(b"\n\n", 1)
            lines = []
            for line in event.split(b"\n"):
                if line.startswith(b"data:"):
                    try:
                        line = b"data: " + json.dumps(canon_anthropic_event(json.loads(line[5:].strip()))).encode()
                    except ValueError:
                        pass
                lines.append(line)
            await out.write(b"\n".join(lines) + b"\n\n")
    if buf.strip():
        await out.write(buf)


# --- Responses (Codex) canonicalizer: drop fields the router's strict decoder rejects. Observed on
# chatgpt.com/backend-api/codex (2026-11-03): "attribution", "speed". Extend from router logs
# (`neutral_response_decode_failed ... unknown field "X"`).
_R_DROP = {"attribution", "speed", "prompt_cache_key", "safety_identifier", "service_tier",
           "billing", "moderation", "user_id", "conversation", "prompt_cache_retention", "background",
           "top_logprobs", "max_tool_calls", "store", "content_filter"}


def canon_responses(o):
    if isinstance(o, dict):
        return {k: canon_responses(v) for k, v in o.items() if k not in _R_DROP}
    if isinstance(o, list):
        return [canon_responses(v) for v in o]
    return o


async def _relay_codex_sse(resp, out):
    buf = b""
    async for chunk in resp.content.iter_any():
        buf += chunk
        while b"\n\n" in buf:
            event, buf = buf.split(b"\n\n", 1)
            lines = []
            for line in event.split(b"\n"):
                if line.startswith(b"data:"):
                    try:
                        line = b"data: " + json.dumps(canon_responses(json.loads(line[5:].strip()))).encode()
                    except ValueError:
                        pass
                lines.append(line)
            await out.write(b"\n".join(lines) + b"\n\n")
    if buf.strip():
        await out.write(buf)


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
    return status, json.dumps(canon_responses(final)).encode()


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
        # The router may pass the client's own Authorization through and put its configured key in
        # x-api-key (Anthropic backends), so accept the token from either header.
        auth = request.headers.get("authorization", "")
        presented = [auth[7:] if auth.lower().startswith("bearer ") else "", request.headers.get("x-api-key", "")]
        if not token or not any(p and hmac.compare_digest(p.encode(), token.encode()) for p in presented):
            return web.json_response({"error": {"message": "bad proxy token"}}, status=401)
        lane_name, rel = request.match_info["lane"], "/" + request.match_info["tail"]
        if lane_name not in LANES or rel not in LANES[lane_name][1]:
            log.warning("not forwarded: %s %s", request.method, request.path)
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
            elif lane_name == "codexchat":
                url, headers, data, up_stream, want_stream = build_codexchat(entry, body, request.headers)
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
            if lane_name == "codexchat" and resp.status == 200:
                conv = _ChatFromResponses(raw.get("model"))
                try:
                    if not want_stream:
                        async for ev in _iter_sse(resp):
                            conv.on_event(ev)
                        row.update(secs=round(time.time() - t0, 2))
                        _record(row)
                        return web.json_response(conv.final())
                    out = web.StreamResponse(status=200, headers={"content-type": "text/event-stream", "cache-control": "no-cache"})
                    await out.prepare(request)
                    async for ev in _iter_sse(resp):
                        for c in conv.on_event(ev):
                            await out.write(b"data: " + json.dumps(c).encode() + b"\n\n")
                    await out.write(b"data: [DONE]\n\n")
                    await out.write_eof()
                    row.update(secs=round(time.time() - t0, 2))
                    _record(row)
                    return out
                except RuntimeError as exc:
                    row.update(status=502, detail=str(exc))
                    _record(row)
                    return web.json_response({"error": {"message": str(exc)}}, status=502)
                finally:
                    resp.release()
            # The router decodes Responses-format backends as one JSON object even when the client
            # streams (it re-streams to the client itself), so the codex lane always aggregates.
            if lane_name == "codex" and resp.status == 200:
                status, out = await _aggregate_codex_sse(resp)
                resp.release()
                row.update(status=status, secs=round(time.time() - t0, 2))
                _record(row)
                return web.Response(status=status, body=out, content_type="application/json")
            if lane_name == "anthropic" and resp.status == 200 and not want_stream:
                try:
                    body_out = json.dumps(canon_anthropic_event(json.loads(await resp.read()))).encode()
                finally:
                    resp.release()
                row["secs"] = round(time.time() - t0, 2)
                _record(row)
                return web.Response(status=200, body=body_out, content_type="application/json")
            if resp.status >= 400:
                err = await resp.read()
                resp.release()
                row.update(secs=round(time.time() - t0, 2), detail=err[:400].decode("utf-8", "replace"))
                _record(row)
                return web.Response(status=resp.status, body=err, content_type="application/json")
            out = web.StreamResponse(status=resp.status)
            for k, v in resp.headers.items():
                if k.lower() not in HOP and not k.lower().startswith(("set-cookie", "cf-", "anthropic-organization")):
                    out.headers[k] = v
            await out.prepare(request)
            if lane_name == "anthropic" and resp.status == 200:
                await _relay_anthropic_sse(resp, out)
            elif lane_name == "codex" and resp.status == 200:
                await _relay_codex_sse(resp, out)
            else:
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
    app.router.add_route("*", "/{any:.*}", lambda r: (log.warning("unrouted: %s %s", r.method, r.path), web.json_response({"error": {"message": "unrouted " + r.path}}, status=404))[1])
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
