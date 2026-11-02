#!/usr/bin/env python3
"""Probe each candidate turbo backend once: HTTP status, TTFT-ish latency, output tokens/s. Prints no secrets.
Run under dsh-env-exec so keys come from the vault."""
import json, os, ssl, sys, time, urllib.request, urllib.error, concurrent.futures as cf
CTX = ssl.create_default_context()

BACKENDS = [
    ("groq", "https://api.groq.com/openai/v1", "GROQ_API_KEY", "openai/gpt-oss-20b"),
    ("groq", "https://api.groq.com/openai/v1", "GROQ_API_KEY", "openai/gpt-oss-120b"),
    ("groq", "https://api.groq.com/openai/v1", "GROQ_API_KEY", "qwen/qwen3.8-27b"),
    ("cerebras", "https://api.cerebras.ai/v1", "CEREBRAS_API_KEY", "gpt-oss-120b"),
    ("cerebras", "https://api.cerebras.ai/v1", "CEREBRAS_API_KEY", "qwen-3.8-27b"),
    ("nvidia", "https://integrate.api.nvidia.com/v1", "NVIDIA_API_KEY", "openai/gpt-oss-20b"),
    ("openrouter", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", "thinkingmachines/inkling:free"),
    ("openrouter", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", "nvidia/nemotron-3.5-lightning:free"),
    ("openrouter", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", "google/gemma-4-31b-it:free"),
    ("openrouter", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", "nvidia/nemotron-3-ultra-550b-a55b:free"),
]
PROMPT = "List three prime numbers between 10 and 30, comma separated, nothing else."


def probe(b):
    prov, base, env, model = b
    key = os.environ.get(env)
    if not key:
        return {"provider": prov, "model": model, "status": "no-key"}
    body = {"model": model, "messages": [{"role": "user", "content": PROMPT}], "max_tokens": 256, "stream": True}
    if prov == "openrouter":
        body["provider"] = {"max_price": {"prompt": 0, "completion": 0, "request": 0}, "allow_fallbacks": False}
    req = urllib.request.Request(base + "/chat/completions", data=json.dumps(body).encode(), method="POST", headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json", "User-Agent": "turbo-probe/1"})
    ttft = None; chunks = 0; text = ""
    try:
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=60, context=CTX) as r:
            hdr = {k.lower(): v for k, v in r.headers.items() if "ratelimit" in k.lower()}
            for line in r:
                line = line.decode().strip()
                if not line.startswith("data:") or line == "data: [DONE]":
                    continue
                d = json.loads(line[5:])
                delta = (d.get("choices") or [{}])[0].get("delta") or {}
                piece = (delta.get("content") or "") + (delta.get("reasoning") or delta.get("reasoning_content") or "")
                if piece:
                    if ttft is None:
                        ttft = time.time() - t0
                    chunks += 1; text += delta.get("content") or ""
        total = time.time() - t0
        gen = max(total - (ttft or total), 1e-3)
        return {"provider": prov, "model": model, "status": 200, "ttft_s": round(ttft or -1, 3), "total_s": round(total, 3),
                "chunks_per_s": round(chunks / gen, 1), "answer": text.strip()[:40], "ratelimit_headers": sorted(hdr)[:6]}
    except urllib.error.HTTPError as e:
        return {"provider": prov, "model": model, "status": e.code, "body": e.read()[:160].decode(errors="replace")}
    except Exception as e:
        return {"provider": prov, "model": model, "status": "error", "body": str(e)[:160]}


with cf.ThreadPoolExecutor(len(BACKENDS)) as ex:
    rows = list(ex.map(probe, BACKENDS))
out = sys.argv[1] if len(sys.argv) > 1 else None
for r in rows:
    print(json.dumps(r))
if out:
    with open(out, "a") as f:
        for r in rows:
            f.write(json.dumps({"at": time.time(), **r}) + "\n")
