#!/usr/bin/env python3
"""Rate-limit inventory for Groq and Cerebras: list every model, send one tiny chat call to each chat model,
and record the provider's own rate-limit headers (per-model buckets). Prints no secrets.
Run under dsh-env-exec with GROQ_API_KEY and CEREBRAS_API_KEY. Usage: limits_inventory.py OUT.json"""
import json, os, ssl, sys, time, urllib.request, urllib.error, concurrent.futures as cf

CTX = ssl.create_default_context()
UA = "turbo-limits/1"
PROVIDERS = {
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    "cerebras": ("https://api.cerebras.ai/v1", "CEREBRAS_API_KEY"),
}
NON_CHAT = ("whisper", "tts", "guard", "embed", "orpheus", "playai", "distil", "prompt-guard", "safeguard", "compound")


def get(url, key):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30, context=CTX) as r:
        return json.loads(r.read())


def probe(prov, base, key, model):
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
                       "max_tokens": 4096}).encode()
    req = urllib.request.Request(base + "/chat/completions", data=body, method="POST", headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json", "User-Agent": UA})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=60, context=CTX) as r:
            h = r.headers; status = r.status; d = json.loads(r.read())
            ok = bool((d["choices"][0]["message"].get("content") or "").strip())
            usage = d.get("usage") or {}
    except urllib.error.HTTPError as e:
        h = e.headers; status = e.code; ok = False; usage = {}
        err = e.read()[:200].decode(errors="replace")
    else:
        err = None
    limits = {k.lower(): v for k, v in h.items() if "ratelimit" in k.lower() or k.lower() == "retry-after"}
    return {"provider": prov, "model": model, "status": status, "content_ok": ok, "latency_s": round(time.time() - t0, 3),
            "usage": {k: usage.get(k) for k in ("prompt_tokens", "completion_tokens", "total_tokens")},
            "limits": limits, "error": err}


rows = []
for prov, (base, env) in PROVIDERS.items():
    key = os.environ.get(env)
    if not key:
        rows.append({"provider": prov, "error": f"{env} not set"}); continue
    models = [m["id"] for m in get(base + "/models", key).get("data", [])
              if m.get("active", True) and not any(x in m["id"].lower() for x in NON_CHAT)]
    with cf.ThreadPoolExecutor(8) as ex:
        rows += list(ex.map(lambda m: probe(prov, base, key, m), sorted(models)))
out = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "rows": rows}
json.dump(out, open(sys.argv[1], "w"), indent=1)
for r in rows:
    lim = r.get("limits", {})
    print(r.get("provider"), r.get("model"), r.get("status"), "ok" if r.get("content_ok") else "-",
          {k.replace("x-ratelimit-", ""): v for k, v in lim.items() if k.startswith("x-ratelimit-limit")}, (r.get("error") or "")[:80])
