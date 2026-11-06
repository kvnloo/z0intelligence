#!/usr/bin/env python3
"""Free-model cascade for vllm-sr/free: probe every zero-cost backend, then render the router config.

  dsh-env-exec --allow GROQ_API_KEY --allow NVIDIA_API_KEY --allow OPENROUTER_API_KEY \
      --allow SAMBANOVA_API_KEY --allow SILICONFLOW_API_KEY -- python3 free_pool.py probe   # -> pool.json
  python3 free_pool.py render                                                              # -> config.yaml
  python3 free_pool.py pace     # every minute (free-pace.timer): track limits, pull capped backends, redeploy

`pace` reads the router's own traffic (no probe calls) and counts each quota bucket against limits.json: Groq per
model, OpenRouter :free / Kilo / NIM per account. It parses every upstream 429's own reset ("try again in 7m12s",
X-RateLimit-Reset, Kilo's PROMOTION_MODEL_LIMIT_REACHED), blocks a bucket until then or at 95% of a window, and
re-renders + redeploys without backends blocked >= 10 min, only when that set changes. State:
~/.z0int/state/posture/free-pace.json, which burn-pace reports as lane "free".

CANDIDATES is ordered most capable first (prior: profiles/scores.json hard tier, then size). `render` keeps that
order for every backend whose probe passed a tool call or was only rate-limited, and records the largest prompt it
accepted as the model card's context window. On 408/413/429/5xx the router moves to the next candidate (an oversized
prompt costs one instant 413; `candidate_requirements.context: known_limits` refused every request here, 2026-11-05). No paid route: OpenRouter calls carry
max_price 0, and Cerebras (credit) and Google models (owner rule) are excluded.
"""
import concurrent.futures as cf
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
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


def render(exclude=(), out=HERE / "config.yaml"):
    """Write the router config. *exclude*: backends `pace` found out of quota until their reset."""
    pool = {r["name"]: r for r in json.loads((HERE / "pool.json").read_text())["rows"]}
    live = [c for c in CANDIDATES if pool.get(c[0], {}).get("tool_call") or pool.get(c[0], {}).get("tool_status") in TRANSIENT]
    live = [c for c in live if c[0] not in exclude] or live  # every backend capped: keep the pool, never render empty
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
    Path(out).write_text(yaml.safe_dump(cfg, sort_keys=False))
    print(f"rendered {len(refs)} backends:", ", ".join(f"{r['model']}({c['context_window_size']})" for r, c in zip(refs, cards)))


# --- pace: rate limits from router traffic ----------------------------------------------------------------------
STATE = Path.home() / ".z0int/state/posture"
OUT, LIVE_CFG = STATE / "free-pace.json", STATE / "vllm-sr-live.yaml"
NS, MARGIN, EXCLUDE_S = "vllm-sr", 0.95, 600
MIN, HOUR, DAY = 60, 3600, 86400
WIN = {"rpm": ("req", MIN), "rph": ("req", HOUR), "rpd": ("req", DAY), "tpm": ("tok", MIN), "tpd": ("tok", DAY)}
SHARED = {"or-": "openrouter", "kilo-": "kilo", "nim-": "nim"}


def bucket(backend):
    """Quota bucket of a router backend: a whole account where the provider pools limits, else the model."""
    for prefix, b in SHARED.items():
        if backend.startswith(prefix):
            return b
    return backend  # groq-*, samba-*: limits are per model


def limits(b):
    """{"req"|"tok": {window_s: cap}} for a bucket, from limits.json (the one limits table)."""
    tab = json.loads((HERE / "limits.json").read_text())
    rows = {f"{m['provider']}-{m['model'].split('/')[-1]}": m for m in tab["models"]} | tab.get("accounts", {})
    out = {}
    for k, cap in rows.get(b, {}).items():
        if k in WIN and cap:
            out.setdefault(WIN[k][0], {})[WIN[k][1]] = cap
    return out


def _dur(text):
    """Seconds in Groq/OpenAI-style "try again in 1h2m3.5s" / "250ms"."""
    m = re.search(r"try again in ([\d.hms]+)", text)
    if not m:
        return None
    s = m.group(1).rstrip(".")
    if re.fullmatch(r"[\d.]+ms", s):
        return float(s[:-2]) / 1000
    parts = re.findall(r"([\d.]+)(h|ms|m|s)", s)
    return sum(float(v) * {"h": 3600, "m": 60, "s": 1, "ms": 0.001}[u] for v, u in parts) if parts else None


def reset_after(error, now):
    """Seconds until a 429'd bucket reopens, from the provider's own words."""
    if (d := _dur(error)) is not None:
        return d
    if m := re.search(r"X-RateLimit-Reset\W+(\d{12,})", error):  # OpenRouter: epoch ms
        return max(int(m.group(1)) / 1000 - now, 60)
    if "PROMOTION_MODEL_LIMIT_REACHED" in error:  # Kilo anonymous cap
        return HOUR
    if re.search(r"per day|daily|\bRPD\b|\bTPD\b", error, re.I):
        return DAY - now % DAY  # unknown daily reset: assume UTC midnight
    return MIN


def pod_log(since_s):
    cmd = ["kubectl", "-n", NS, "logs", "deploy/semantic-router", f"--since={int(since_s)}s"]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout.splitlines()


def ingest(st, lines, now):
    seen = st.setdefault("seen", {})
    for line in lines:
        if '"llm_usage"' not in line and "fallback_execution_complete" not in line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        rid = d.get("request_id", "")
        ts = datetime.fromisoformat(d["ts"].rstrip("Z") + "+00:00").timestamp() if "ts" in d else now  # router logs UTC
        if d.get("msg") == "llm_usage":
            key = f"u:{rid}:{d.get('model')}"
            if key in seen:
                continue
            seen[key] = now
            toks = (d.get("prompt_tokens") or 0) - (d.get("cached_prompt_tokens") or 0) + (d.get("completion_tokens") or 0)
            st["events"].append([ts, bucket(d.get("model", "")), max(toks, 0)])
        elif "execution_record" in d:
            for a in (json.loads(d["execution_record"]).get("attempts") or []):
                key = f"a:{a.get('attempt_id')}"
                if key in seen or a.get("status_code") is None:
                    continue
                seen[key] = now
                b = bucket(a.get("model", ""))
                if a.get("status_code") != 200:
                    st["events"].append([ts, b, 0])  # a refused call still spends a request
                if a.get("status_code") == 429:
                    until = ts + reset_after(a.get("error") or "", ts)
                    if until > st["blocked"].get(b, {}).get("until", 0):
                        st["blocked"][b] = {"until": until, "why": "429: " + (a.get("error") or "")[:160]}
    st["seen"] = {k: v for k, v in seen.items() if now - v < 2 * HOUR}


def assess(st, now):
    st["events"] = [e for e in st["events"] if now - e[0] < DAY]
    st["blocked"] = {b: v for b, v in st["blocked"].items() if v["until"] > now}
    usage = {}
    for b in sorted({e[1] for e in st["events"]} | set(json.loads((HERE / "limits.json").read_text()).get("accounts", {}))):
        lim, row = limits(b), {}
        for kind, idx in (("req", None), ("tok", 2)):
            for win, cap in lim.get(kind, {}).items():
                evs = [e for e in st["events"] if e[1] == b and now - e[0] < win]
                used = len(evs) if idx is None else sum(e[2] for e in evs)
                row[f"{kind}_{win}s"] = f"{used}/{cap}"
                if used >= MARGIN * cap and evs:
                    # Reopens when enough of the window's oldest events age out to fall back under the margin.
                    acc, until = used, now + win
                    for e in sorted(evs):
                        acc -= 1 if idx is None else e[2]
                        if acc < MARGIN * cap:
                            until = e[0] + win
                            break
                    if until > st["blocked"].get(b, {}).get("until", 0):
                        st["blocked"][b] = {"until": until, "why": f"{kind} {used}/{cap} per {win}s (pre-emptive)"}
        usage[b] = row
    return usage


def excluded(st, now):
    long = {b for b, v in st["blocked"].items() if v["until"] - now >= EXCLUDE_S}
    return sorted(c[0] for c in CANDIDATES if bucket(c[0]) in long)


def pace():
    now = time.time()
    try:
        st = json.loads(OUT.read_text())
    except (OSError, ValueError):
        st = {}
    st.setdefault("events", []), st.setdefault("blocked", {})
    since = min(now - st.get("cursor", now - HOUR), DAY) + 30
    ingest(st, pod_log(since), now)
    st["cursor"] = now
    usage = assess(st, now)
    want = excluded(st, now)
    if want != st.get("excluded", []) and "--dry-run" not in sys.argv:
        render(exclude=set(want), out=LIVE_CFG)
        r = subprocess.run([str(HERE / "deploy.sh"), str(LIVE_CFG)], capture_output=True, text=True, timeout=600)
        st["deploy"] = {"at": now, "rc": r.returncode, "excluded": want, "tail": r.stdout[-300:] + r.stderr[-300:]}
        if r.returncode == 0:
            st["excluded"] = want
    st["at"] = datetime.fromtimestamp(now).astimezone().isoformat(timespec="seconds")
    st["usage"] = usage
    STATE.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(st))
    tmp.replace(OUT)
    for b, v in sorted(st["blocked"].items()):
        print(f"blocked {b} for {int(v['until'] - now)}s: {v['why'][:100]}")
    print("excluded from router:", st.get("excluded", []))



if __name__ == "__main__":
    if sys.argv[1:] == ["probe"]:
        with cf.ThreadPoolExecutor(8) as ex:
            rows = list(ex.map(probe, CANDIDATES))
        (HERE / "pool.json").write_text(json.dumps({"probed_at": time.strftime("%FT%TZ", time.gmtime()), "rows": rows}, indent=1))
        for r in rows:
            print(json.dumps(r)[:240])
    elif sys.argv[1:2] == ["pace"]:
        pace()
    elif sys.argv[1:] == ["render"]:
        render()
    else:
        sys.exit(__doc__)
