"""Explicit Turbo execution adapters. Kerdoios never calls these."""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import time
import urllib.error
import urllib.request

ENDPOINTS = {"groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY"),
             "cerebras": ("https://api.cerebras.ai/v1", "CEREBRAS_API_KEY")}


class Halt(RuntimeError):
    pass


class ProviderError(RuntimeError):
    def __init__(self, status, retry_after=60):
        super().__init__(f"provider_http_{status}")
        self.status, self.retry_after = status, retry_after


def load_keys():
    """Only designated owner credential files; never persist or return values."""
    try:
        from dotenv import dotenv_values
    except ImportError:
        return
    for p in [Path.home() / ".hermes/.env", Path("/workspace/hermes-home/profiles/clean/.env")]:
        if p.is_file():
            for k, v in dotenv_values(p).items():
                if k in {"GROQ_API_KEY", "CEREBRAS_API_KEY", "DEEPSEEK_API_KEY"} and v:
                    os.environ.setdefault(k, v)


def check(deadline, stop):
    if stop.exists():
        raise Halt("cancelled")
    if time.monotonic() >= deadline:
        raise Halt("deadline")


def process(argv, cwd, log, deadline, stop):
    """Own one process group; cancellation never touches another run/session."""
    check(deadline, stop)
    env = dict(os.environ)
    env["TERMINAL_CWD"] = str(cwd)
    with log.open("w", encoding="utf-8") as out:
        os.chmod(log, 0o600)
        proc = subprocess.Popen(argv, cwd=cwd, stdout=out, stderr=subprocess.STDOUT,
                                start_new_session=True, env=env, stdin=subprocess.DEVNULL)
        try:
            while proc.poll() is None:
                check(deadline, stop)
                time.sleep(.1)
        finally:
            # Even a normally exited leader may have left tool descendants.
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            if proc.poll() is None:
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
    if proc.returncode:
        raise RuntimeError(f"subprocess_exit_{proc.returncode}; see private log {log.name}")
    return log.read_text(encoding="utf-8")


def native(prompt, role, cwd, folder, deadline, stop):
    provider, model = (("anthropic", "claude-opus-5-5") if role == "planner"
                       else ("openai-codex", "gpt-6.1-sol"))
    query = folder / f"{role}-prompt.txt"
    query.write_text(prompt)
    os.chmod(query, 0o600)
    start = time.monotonic()
    raw = process([os.environ.get("Z0INT_HERMES", "hermes"), "-p", "clean", "chat", "--safe-mode",
                   "--provider", provider, "--model", model, "--in", str(cwd),
                   "--run-budget", str(max(1, int(deadline - start))), "--max-turns", "12",
                   "--format", "stream-json", "--query-file", str(query)],
                  cwd, folder / f"{role}.jsonl", deadline, stop)
    rows = []
    for line in raw.splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass
    text = ""
    usage = None
    served_model = None
    for row in rows:
        if row.get("type") == "system" and row.get("subtype") == "init":
            served_model = row.get("model")
        if row.get("type") in {"result", "final", "assistant_message"}:
            value = row.get("result") or row.get("text") or row.get("content") or row.get("response")
            if isinstance(value, str):
                text = value
            if row.get("exit_code", 0) != 0:
                raise RuntimeError("native_run_failed")
            usage = row.get("usage") or row.get("tokens") or usage
    if not text:
        # Native Hermes versions expose the terminal answer in an assistant event.
        for row in rows:
            if row.get("type") == "assistant" and isinstance(row.get("content"), str):
                text = row["content"]
    if not text:
        raise RuntimeError("native_missing_terminal_answer")
    if served_model != model:
        raise RuntimeError("native_model_identity_mismatch")
    return {"text": text, "provider": provider, "model": model, "served_model": served_model, "wall_seconds": time.monotonic()-start,
            "input_tokens": usage.get("input_tokens", usage.get("input")) if usage else None,
            "output_tokens": usage.get("output_tokens", usage.get("output")) if usage else None,
            "cost_usd": None, "billing": "subscription_or_unknown", "adapter": "native_hermes"}


def chat(offer, prompt, max_tokens, deadline, stop):
    base, env = ENDPOINTS[offer.provider]
    check(deadline, stop)
    payload = {"model": offer.model, "messages": [{"role": "user", "content": prompt}],
               "max_tokens": max_tokens, "stream": True, "stream_options": {"include_usage": True}}
    if "gpt-oss" in offer.model:
        payload["reasoning_effort"] = "low"
    elif offer.provider == "cerebras" and offer.model == "qwen-3.8-27b":
        payload["reasoning_effort"] = "none"
    req = urllib.request.Request(base + "/chat/completions", data=json.dumps(payload).encode(),
                                 headers={"Authorization": "Bearer " + os.environ[env],
                                          "Content-Type": "application/json", "User-Agent": "z0int-turbo/0.1"})
    start = time.monotonic()
    first = None
    text, usage = [], {}
    finish = None
    try:
        with urllib.request.urlopen(req, timeout=min(20, max(.1, deadline-start))) as response:
            headers = {k.lower(): v for k, v in response.headers.items()
                       if k.lower().startswith(("x-ratelimit", "retry-after"))}
            for line in response:
                check(deadline, stop)
                if not line.startswith(b"data: ") or line.strip() == b"data: [DONE]":
                    continue
                row = json.loads(line[6:])
                usage = row.get("usage") or usage
                choices = row.get("choices") or []
                if choices and choices[0].get("finish_reason"):
                    finish = choices[0]["finish_reason"]
                delta = choices[0].get("delta", {}).get("content") if choices else None
                if choices and any(choices[0].get("delta", {}).get(k) for k in ("content", "reasoning", "reasoning_content")):
                    first = first or time.monotonic()
                if delta:
                    text.append(delta)
    except urllib.error.HTTPError as exc:
        try:
            retry = float(exc.headers.get("retry-after", "60"))
        except ValueError:
            retry = 60
        raise ProviderError(exc.code, max(60, retry)) from None
    elapsed = time.monotonic() - start
    output = usage.get("completion_tokens")
    return {"text": "".join(text), "provider": offer.provider, "model": offer.model,
            "wall_seconds": elapsed, "latency_ms": (first-start)*1000 if first else None,
            "input_tokens": usage.get("prompt_tokens"), "output_tokens": output,
            "output_tok_s": output/elapsed if isinstance(output, int) and elapsed else None,
            "rate_headers": headers, "cost_usd": None, "billing": "catalog_free_tier_billing_unconfirmed",
            "adapter": "openai_compatible_sse", "finish_reason": finish}


def opencode(offer, prompt, cwd, folder, deadline, stop):
    start = time.monotonic()
    raw = process([os.environ.get("Z0INT_OPENCODE", "/usr/bin/opencode"), "run", "--standalone",
                   "--format", "json", "--model", offer.id, prompt], cwd,
                  folder / "opencode.jsonl", deadline, stop)
    texts, inp, out, cost = [], 0, 0, 0.0
    accounted = False
    for line in raw.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        part = row.get("part", {})
        if row.get("type") == "text":
            texts.append(part.get("text", ""))
        if row.get("type") == "step_finish":
            tokens = part.get("tokens", {})
            if tokens:
                accounted = True
                inp += tokens.get("input", 0)
                out += tokens.get("output", 0)
                cost += part.get("cost", 0)
    if not texts:
        raise RuntimeError("opencode_missing_terminal_answer")
    return {"text": "\n".join(texts), "provider": "opencode", "model": offer.model,
            "wall_seconds": time.monotonic()-start, "input_tokens": inp if accounted else None,
            "output_tokens": out if accounted else None, "cost_usd": cost if accounted else None,
            "billing": "opencode_free_catalog", "adapter": "opencode",
            "latency_ms": None,
            "output_tok_s": out/(time.monotonic()-start) if accounted else None}


def object_from_text(text):
    decoder = json.JSONDecoder()
    for i, c in enumerate(text):
        if c == "{":
            try:
                obj, _ = decoder.raw_decode(text[i:])
                return obj
            except ValueError:
                pass
    raise ValueError("model_answer_missing_json_object")
