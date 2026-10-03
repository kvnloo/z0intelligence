#!/usr/bin/env python3
"""Loopback OpenAI-compatible shim between the gateway's StandaloneLLMRunner and a provider.

Why it exists
  * The gateway sends no reasoning parameter; the owner-named candidates are defined by an
    effort setting ("grok 4.7 low", "luna xhigh"). The shim injects pre-registered extra body
    fields (e.g. reasoning_effort) -- this is also the deployable way to run them behind the
    gateway (a production shim would do the same).
  * It is the single choke point for: the provider key (read from env, never logged), per-call
    ledger (latency, usage, cost), the hard paid-spend cap, and request pacing for free tiers.
  * --stub answers locally with a contract-valid empty result (harness wiring tests, no model).

The request body is otherwise forwarded unchanged (model name rewritten to the upstream id).
Ledger lines never contain the key. Request/response bodies (synthetic corpus only) are stored
per call for audit under --calls-dir.
"""
import argparse
import fcntl
import hashlib
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ap = argparse.ArgumentParser()
ap.add_argument("--port", type=int, required=True)
ap.add_argument("--upstream", default="", help="base URL ending in /v1 (or /api/v1)")
ap.add_argument("--model", default="", help="upstream model id")
ap.add_argument("--key-env", default="", help="env var holding the provider key (empty = none)")
ap.add_argument("--inject", default="{}", help="JSON object merged into every request body")
ap.add_argument("--price-in", type=float, default=0.0, help="USD per 1M input tokens")
ap.add_argument("--price-out", type=float, default=0.0, help="USD per 1M output tokens")
ap.add_argument("--price-cache-read", type=float, default=None, help="USD per 1M cached input tokens")
ap.add_argument("--paid", action="store_true", help="count spend against the global cap")
ap.add_argument("--budget-file", default="", help="JSON file holding cumulative paid spend (shared, flock)")
ap.add_argument("--cap-usd", type=float, default=0.0, help="refuse a paid call if spend >= cap")
ap.add_argument("--arm-cap-usd", type=float, default=0.0, help="per-arm allocation (0 = no arm cap)")
ap.add_argument("--arm", default="")
ap.add_argument("--min-interval-s", type=float, default=0.0, help="pacing between upstream requests")
ap.add_argument("--ledger", required=True)
ap.add_argument("--calls-dir", required=True)
ap.add_argument("--stub", action="store_true")
ap.add_argument("--timeout-s", type=float, default=330.0)
A = ap.parse_args()

KEY = os.environ.get(A.key_env, "") if A.key_env else ""
INJECT = json.loads(A.inject)
os.makedirs(A.calls_dir, exist_ok=True)
_lock = threading.Lock()
_last = [0.0]
_n = [0]


def classify(body: dict) -> str:
    msgs = body.get("messages") or []
    sys_txt = next((m.get("content") for m in msgs if m.get("role") == "system"), "") or ""
    if isinstance(sys_txt, list):
        sys_txt = " ".join(p.get("text", "") for p in sys_txt if isinstance(p, dict))
    if "情境切分" in sys_txt and "记忆提取" in sys_txt:
        return "l1-extraction"
    if "冲突检测器" in sys_txt:
        return "l1-dedup"
    if "Persona Architect" in sys_txt or "Operating Doctrine Architect" in sys_txt:
        return "l3-persona"
    if "记忆整合架构师" in sys_txt:
        return "l2-scene"
    if body.get("tools"):
        return "tools-other"
    return "other"


def budget_spend(delta: float = 0.0, check_only: bool = False):
    """Return (total_spent, arm_spent) after optionally adding delta; flock-protected."""
    if not A.budget_file:
        return 0.0, 0.0
    os.makedirs(os.path.dirname(A.budget_file) or ".", exist_ok=True)
    with open(A.budget_file, "a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        raw = f.read().strip()
        st = json.loads(raw) if raw else {"total": 0.0, "arms": {}}
        if not check_only and delta:
            st["total"] = round(st["total"] + delta, 8)
            st["arms"][A.arm] = round(st["arms"].get(A.arm, 0.0) + delta, 8)
            f.seek(0)
            f.truncate()
            f.write(json.dumps(st))
        fcntl.flock(f, fcntl.LOCK_UN)
        return st["total"], st["arms"].get(A.arm, 0.0)


def cost_of(usage: dict) -> float:
    if not usage:
        return 0.0
    pt = usage.get("prompt_tokens") or 0
    ct = usage.get("completion_tokens") or 0
    cached = ((usage.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0
    pc = A.price_cache_read if A.price_cache_read is not None else A.price_in
    return ((pt - cached) * A.price_in + cached * pc + ct * A.price_out) / 1e6


def stub_reply(body: dict) -> dict:
    kind = classify(body)
    if kind == "l1-extraction":
        import re as _re
        prompt = next((m.get("content") for m in body.get("messages", []) if m.get("role") == "user"), "") or ""
        new = prompt.split("【待提取的新消息】", 1)[-1]
        m = _re.search(r"\[(msg-[0-9a-f]+)\] \[user\] \[[^\]]+\]: (.{0,80})", new)
        mems = [] if not m else [{"content": "STUB: " + m.group(2).replace('"', "'"), "type": "episodic",
                                  "priority": 60, "source_message_ids": [m.group(1)], "metadata": {}}]
        content = json.dumps([{"scene_name": "stub scene", "message_ids": [m.group(1)] if m else [], "memories": mems}])
    elif kind == "l1-dedup":
        content = "[]"
    else:
        content = "done"
    return {"id": "stub", "object": "chat.completion", "created": int(time.time()), "model": "stub",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code: int, obj: dict):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._send(200, {"object": "list", "data": [{"id": A.model or "stub", "object": "model"}]})

    def do_POST(self):
        n = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(n)
        try:
            body = json.loads(raw)
        except Exception:
            return self._send(400, {"error": {"message": "bad json"}})
        with _lock:
            _n[0] += 1
            cid = _n[0]
        kind = classify(body)
        gw_model = body.get("model")
        req_view = {k: v for k, v in body.items() if k not in ("messages", "tools")}
        rec = {"call": cid, "arm": A.arm, "kind": kind, "ts": time.time(), "gateway_model": gw_model,
               "upstream_model": A.model, "inject": INJECT, "req_params": req_view,
               "n_messages": len(body.get("messages") or []), "n_tools": len(body.get("tools") or []),
               "req_sha256": hashlib.sha256(raw).hexdigest(), "stream": bool(body.get("stream"))}
        if body.get("stream"):
            rec.update(status=400, error="streaming not supported by shim")
            self._ledger(rec)
            return self._send(400, {"error": {"message": "stream not supported"}})

        if A.stub:
            resp = stub_reply(body)
            rec.update(status=200, latency_ms=0, usage=resp["usage"], cost_usd=0.0)
            self._save(cid, body, resp)
            self._ledger(rec)
            return self._send(200, resp)

        if A.paid:
            total, arm = budget_spend(check_only=True)
            if total >= A.cap_usd or (A.arm_cap_usd and arm >= A.arm_cap_usd):
                rec.update(status=402, error=f"budget cap reached total={total:.4f} arm={arm:.4f}")
                self._ledger(rec)
                return self._send(402, {"error": {"message": "budget cap reached", "type": "budget"}})

        body["model"] = A.model or body.get("model")
        body.update(INJECT)
        data = json.dumps(body).encode()
        if A.min_interval_s:
            with _lock:
                wait = _last[0] + A.min_interval_s - time.time()
                _last[0] = max(time.time(), _last[0] + A.min_interval_s)
            if wait > 0:
                time.sleep(wait)
        headers = {"content-type": "application/json"}
        if KEY:
            headers["authorization"] = f"Bearer {KEY}"
        req = urllib.request.Request(A.upstream.rstrip("/") + "/chat/completions", data=data, headers=headers, method="POST")
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=A.timeout_s) as r:
                code, payload = r.status, r.read()
        except urllib.error.HTTPError as e:
            code, payload = e.code, e.read()
        except Exception as e:  # timeout / connection
            rec.update(status=599, latency_ms=int((time.time() - t0) * 1000), error=f"{type(e).__name__}: {e}")
            self._ledger(rec)
            return self._send(502, {"error": {"message": f"shim upstream error: {type(e).__name__}"}})
        lat = int((time.time() - t0) * 1000)
        try:
            resp = json.loads(payload)
        except Exception:
            resp = {"raw": payload[:2000].decode("utf-8", "replace")}
        usage = resp.get("usage") if isinstance(resp, dict) else None
        cost = cost_of(usage) if code == 200 else 0.0
        if A.paid and cost:
            budget_spend(cost)
        ch = (resp.get("choices") or [{}])[0] if isinstance(resp, dict) else {}
        msg = ch.get("message") or {}
        rec.update(status=code, latency_ms=lat, usage=usage, cost_usd=round(cost, 8),
                   finish_reason=ch.get("finish_reason"), provider=resp.get("provider") if isinstance(resp, dict) else None,
                   n_tool_calls=len(msg.get("tool_calls") or []), content_chars=len(msg.get("content") or ""),
                   reasoning_chars=len(msg.get("reasoning") or msg.get("reasoning_content") or ""),
                   error=(resp.get("error") if code != 200 and isinstance(resp, dict) else None))
        self._save(cid, body, resp)
        self._ledger(rec)
        out = json.dumps(resp).encode() if isinstance(resp, dict) and "raw" not in resp else payload
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def _save(self, cid, body, resp):
        with open(os.path.join(A.calls_dir, f"{cid:05d}.json"), "w") as f:
            json.dump({"request": body, "response": resp}, f, ensure_ascii=False)

    def _ledger(self, rec):
        with _lock, open(A.ledger, "a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("127.0.0.1", A.port), H)
    print(f"shim: arm={A.arm} port={A.port} upstream={A.upstream or '(stub)'} model={A.model} "
          f"key={'set' if KEY else 'none'} inject={json.dumps(INJECT)} paid={A.paid}", file=sys.stderr, flush=True)
    srv.serve_forever()
