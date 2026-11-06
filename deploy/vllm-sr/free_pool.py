#!/usr/bin/env python3
"""Free-model cascade for vllm-sr/free: probe every zero-cost backend, then render the router config.

  dsh-env-exec --allow GROQ_API_KEY --allow NVIDIA_API_KEY --allow OPENROUTER_API_KEY \
      --allow SAMBANOVA_API_KEY --allow SILICONFLOW_API_KEY -- python3 free_pool.py probe   # -> pool.json
  python3 free_pool.py render                                                              # -> config.yaml

CANDIDATES is ordered most capable first (prior: profiles/scores.json hard tier, then size). `render` keeps that
order for every backend whose probe passed a tool call or was only rate-limited, and records the largest prompt it
accepted as the model card's context window. On 408/413/429/5xx the router moves to the next candidate (an oversized
prompt costs one instant 413; `candidate_requirements.context: known_limits` refused every request here, 2026-11-05). No paid route: OpenRouter calls carry
max_price 0, and Cerebras (credit) and Google models (owner rule) are excluded.
"""
import concurrent.futures as cf
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
OR, NIM, KILO = "https://openrouter.ai/api/v1", "https://integrate.api.nvidia.com/v1", "https://api.kilo.ai/api/gateway"
GROQ, SAMBA = "https://api.groq.com/openai/v1", "https://api.sambanova.ai/v1"
OVH, SF = "https://oai.endpoints.kepler.ai.cloud.ovh.net/v1", "https://api.siliconflow.com/v1"
ANON = None  # keyless endpoint

# (router name, base_url, key env or ANON, provider model id, claimed context window)
CANDIDATES = [
    ("kilo-nemotron-3-ultra", KILO, ANON, "nvidia/nemotron-3-ultra-550b-a55b:free", 1000000),
    ("or-nemotron-3-ultra", OR, "OPENROUTER_API_KEY", "nvidia/nemotron-3-ultra-550b-a55b:free", 1000000),
    ("nim-nemotron-3-ultra", NIM, "NVIDIA_API_KEY", "nvidia/nemotron-3-ultra-550b-a55b", 1000000),
    ("ovh-qwen3.5-397b", OVH, ANON, "Qwen3.5-397B-A17B", 262144),
    ("kilo-nemotron-3.5-super-vl", KILO, ANON, "nvidia/nemotron-3.5-super-vl:free", 1000000),
    ("or-nemotron-3.5-super-vl", OR, "OPENROUTER_API_KEY", "nvidia/nemotron-3.5-super-vl:free", 1000000),
    ("kilo-dots-3-note", KILO, ANON, "dots-studio/dots-3-note-preview:free", 512000),
    ("or-dots-3-note", OR, "OPENROUTER_API_KEY", "dots-studio/dots-3-note-preview:free", 512000),
    ("or-flint-v1", OR, "OPENROUTER_API_KEY", "recursive/flint-v1.0:free", 262144),
    ("samba-gpt-oss-120b", SAMBA, "SAMBANOVA_API_KEY", "gpt-oss-120b", 131072),
    ("ovh-gpt-oss-120b", OVH, ANON, "gpt-oss-120b", 131072),
    ("groq-gpt-oss-120b", GROQ, "GROQ_API_KEY", "openai/gpt-oss-120b", 131072),
    ("nim-nemotron-3.5-lightning", NIM, "NVIDIA_API_KEY", "nvidia/nemotron-3.5-lightning-30b-a3b", 1000000),
    ("kilo-nemotron-3.5-lightning", KILO, ANON, "nvidia/nemotron-3.5-lightning:free", 1000000),
    ("or-nemotron-3.5-lightning", OR, "OPENROUTER_API_KEY", "nvidia/nemotron-3.5-lightning:free", 1000000),
    ("kilo-north-mini-code", KILO, ANON, "cohere/north-mini-code:free", 256000),
    ("or-xing4.0-29b", OR, "OPENROUTER_API_KEY", "xingchen-agi/xing4.0-29b-a4b:free", 262144),
    ("groq-qwen3.8-27b", GROQ, "GROQ_API_KEY", "qwen/qwen3.8-27b", 131072),
    ("or-nemotron-nano-omni", OR, "OPENROUTER_API_KEY", "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free", 256000),
    ("groq-gpt-oss-20b", GROQ, "GROQ_API_KEY", "openai/gpt-oss-20b", 131072),
    ("sf-qwen3-8b", SF, "SILICONFLOW_API_KEY", "Qwen/Qwen3-8B", 131072),
]
TOOL = {"type": "function", "function": {"name": "get_weather", "description": "Weather for a city",
        "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}}
FILLER = "The quick brown fox jumps over the lazy dog near the river bank. "  # ~13 tokens
SIZES = (6000, 30000, 100000)  # prompt tokens tried in order; largest accepted becomes the context cap


def call(base, env, model, body, timeout=90):
    key = os.environ.get(env) if env else "anonymous"
    if env and not key:
        return "no-key", None, 0.0
    if base == OR:
        body["provider"] = {"max_price": {"prompt": 0, "completion": 0, "request": 0}, "allow_fallbacks": True}
    req = urllib.request.Request(base + "/chat/completions", data=json.dumps({"model": model, **body}).encode(),
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                                          "User-Agent": "vllm-sr-free-pool/1"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.load(r), time.time() - t0
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:200].decode(errors="replace"), time.time() - t0
    except Exception as e:  # timeouts, TLS, DNS
        return "error", str(e)[:200], time.time() - t0


def probe(c):
    name, base, env, model, _ctx = c
    st, body, dt = call(base, env, model, {"messages": [{"role": "user", "content": "What's the weather in Paris? Use the tool."}],
                                           "tools": [TOOL], "max_tokens": 512})
    msg = ((body or {}).get("choices") or [{}])[0].get("message") or {} if isinstance(body, dict) else {}
    row = {"name": name, "tool_status": st, "tool_call": bool(msg.get("tool_calls")), "tool_s": round(dt, 2)}
    if not isinstance(body, dict):
        row["error"] = body
    if row["tool_call"]:
        for n in SIZES:
            st, b, dt = call(base, env, model, {"messages": [{"role": "user", "content": FILLER * (n // 13) +
                                                              "\nReply with the single word OK."}], "max_tokens": 64}, 180)
            if st != 200:
                row[f"ctx_{n}"] = st if st != "error" else b
                break
            row["max_ok_tokens"], row[f"ctx_{n}_s"] = n, round(dt, 2)
    return row


TRANSIENT = (408, 429, 500, 502, 503, 504, "error")  # quota/overload: still a free backend, retried later
SMALL_CAP = 7000  # unprobed context (e.g. Groq's 8K TPM free tier): only small prompts go there


def _cascade(name, refs):
    fallback = {"enabled": True, "max_attempts": len(refs), "per_attempt_timeout": "120s", "total_timeout": "600s",
                "retryable_status_codes": [408, 413, 429, 500, 502, 503, 504]}
    return {"name": name, "priority": 100, "rules": {"operator": "AND", "conditions": []},
            "description": "Every probed free backend in order; next one on 408/413/429/5xx.",
            "algorithm": {"type": "static"}, "modelRefs": refs, "fallback": fallback}


def render():
    pool = {r["name"]: r for r in json.loads((HERE / "pool.json").read_text())["rows"]}
    live = [c for c in CANDIDATES if pool.get(c[0], {}).get("tool_call") or pool.get(c[0], {}).get("tool_status") in TRANSIENT]
    if not live:
        sys.exit("no candidate passed the tool probe; refusing to render an empty pool")
    models, cards, refs = [], [], []
    for name, base, env, model, claimed in live:
        backend = {"name": name.split("-")[0], "provider": "vllm", "openai_compat_gateway": True,
                   "base_url": base, "weight": 100}
        if env:
            backend["api_key_env"] = env
        else:
            backend["api_key_env"] = "FREE_ANON_API_KEY"  # keyless endpoints; deploy.sh sets it to "anonymous"
        models.append({"name": name, "provider_model_id": model, "backend_refs": [backend]})
        ok = pool[name].get("max_ok_tokens", 0)
        # A probe that passed the largest size proved nothing past it: trust the published window there.
        cap = claimed if ok == SIZES[-1] else (max(ok, 4000) if pool[name].get("tool_call") else SMALL_CAP)
        cards.append({"name": name, "context_window_size": cap})
        refs.append({"model": name, "use_reasoning": False})
    # turbo = same pool, fastest measured tool turn first (unmeasured last).
    speed = sorted(refs, key=lambda r: pool[r["model"]]["tool_s"] if pool[r["model"]].get("tool_call") else 1e9)
    cfg = {
        "version": "v0.3",
        "entrypoints": [{"model_names": ["vllm-sr/free", "vllm-sr/auto"], "recipe": "default"},
                        {"model_names": ["vllm-sr/turbo"], "recipe": "turbo"}],
        "recipes": [{"name": "turbo", "description": "Free pool, fastest first.",
                     "routing": {"decisions": [_cascade("free-fastest", speed)]}}],
        "listeners": [{"name": "http-8899", "address": "0.0.0.0", "port": 8899, "timeout": "1800s"}],
        "global": {"router": {"fallback": {"enabled": True, "max_attempts": len(refs),
                                          "circuit_breaker": {"consecutive_failures": 2, "cooldown_period": "120s",
                                                              "half_open_probes": 1}}},
                   "services": {"observability": {"tracing": {}}, "response_api": {}},
                   "stores": {"response_cache": {}}},
        "providers": {"defaults": {"model": refs[0]["model"]}, "models": models},
        "routing": {
            "modelCards": cards,
            "decisions": [_cascade("free-capable", refs)],
        },
    }
    import yaml
    (HERE / "config.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
    print(f"rendered {len(refs)} backends:", ", ".join(f"{r['model']}({c['context_window_size']})" for r, c in zip(refs, cards)))


if __name__ == "__main__":
    if sys.argv[1:] == ["probe"]:
        with cf.ThreadPoolExecutor(8) as ex:
            rows = list(ex.map(probe, CANDIDATES))
        (HERE / "pool.json").write_text(json.dumps({"probed_at": time.strftime("%FT%TZ", time.gmtime()), "rows": rows}, indent=1))
        for r in rows:
            print(json.dumps(r)[:240])
    elif sys.argv[1:] == ["render"]:
        render()
    else:
        sys.exit(__doc__)
