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
order (vllm-sr/free) for every backend whose probe passed a tool call or was only rate-limited, and records the largest prompt it
accepted as the model card's context window. On 408/413/429/5xx the router moves to the next candidate (an oversized
prompt costs one instant 413; `candidate_requirements.context: known_limits` refused every request here, 2026-11-05). No paid route: OpenRouter calls carry
max_price 0; Google models are excluded (owner rule). Cerebras is owner-approved prepaid credit: turbo only (CREDIT).
Order: models on the Hermes Index (HERMES_INDEX) lead, best score first; the rest follow in CANDIDATES order.
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
CEREBRAS = "https://api.cerebras.ai/v1"
COHERE, ZAI, LLM7 = "https://api.cohere.ai/compatibility/v1", "https://api.z.ai/api/paas/v4", "https://api.llm7.io/v1"
OVH, SF = "https://oai.endpoints.kepler.ai.cloud.ovh.net/v1", "https://api.siliconflow.com/v1"
ANON = None  # keyless endpoint

# (router name, base_url, key env or ANON, provider model id, claimed context window)
CANDIDATES = [
    # On the Hermes Index and free on NIM (render orders these by HERMES_INDEX).
    ("nim-deepseek-v4.1-flash", NIM, "NVIDIA_API_KEY", "deepseek-ai/deepseek-v4.1-flash", 1000000),
    ("nim-glm-5.3-flash", NIM, "NVIDIA_API_KEY", "z-ai/glm-5.3-flash", 200000),
    ("nim-step-5-preview", NIM, "NVIDIA_API_KEY", "stepfun-ai/step-5-preview", 262144),
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
    # 2026-11-07 owner "wire every free model and provider": NIM catalog additions, new OpenRouter :free,
    # Cohere trial, Z.ai flash, SambaNova free tier and llm7 (open-weight only; closed models there are
    # unvetted resales and GPT/Gemini/Claude have their own owner-mandated routes).
    ("nim-kimi-k2.6", NIM, "NVIDIA_API_KEY", "moonshotai/kimi-k2.6", 262144),
    ("nim-minimax-m3.1-flash", NIM, "NVIDIA_API_KEY", "minimaxai/minimax-m3.1-flash", 1000000),
    ("nim-mistral-large-4", NIM, "NVIDIA_API_KEY", "mistralai/mistral-large-4-1t-a52b", 262144),
    ("nim-nemotron-3-super", NIM, "NVIDIA_API_KEY", "nvidia/nemotron-3-super-120b-a12b", 1000000),
    ("nim-gpt-oss-20b", NIM, "NVIDIA_API_KEY", "openai/gpt-oss-20b", 131072),
    ("nim-laguna-xs-2.1", NIM, "NVIDIA_API_KEY", "poolside/laguna-xs-2.1", 131072),
    ("cohere-command-a-reasoning", COHERE, "COHERE_API_KEY", "command-a-reasoning-08-2025", 256000),
    ("cohere-command-a-plus", COHERE, "COHERE_API_KEY", "command-a-plus-05-2026", 256000),
    ("cohere-north-mini-code", COHERE, "COHERE_API_KEY", "north-mini-code-1.0", 256000),
    ("zai-glm-5.3-flash", ZAI, "ZAI_API_KEY", "glm-5.3-flash", 200000),
    ("samba-glm-5.3", SAMBA, "SAMBANOVA_API_KEY", "GLM-5.3", 131072),
    ("samba-minimax-m3.1-flash", SAMBA, "SAMBANOVA_API_KEY", "MiniMax-M3.1-Flash", 131072),
    ("llm7-deepseek-v4-pro", LLM7, ANON, "deepseek-v4-pro", 131072),
    ("llm7-glm-5.3", LLM7, ANON, "glm-5.3", 131072),
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


def probe(c, sizes=True):
    name, base, env, model, _ctx = c
    st, body, dt = call(base, env, model, {"messages": [{"role": "user", "content": "What's the weather in Paris? Use the tool."}],
                                           "tools": [TOOL], "max_tokens": 512})
    msg = ((body or {}).get("choices") or [{}])[0].get("message") or {} if isinstance(body, dict) else {}
    row = {"name": name, "tool_status": st, "tool_call": bool(msg.get("tool_calls")), "tool_s": round(dt, 2)}
    if not isinstance(body, dict):
        row["error"] = body
    if row["tool_call"] and sizes:
        for n in SIZES:
            st, b, dt = call(base, env, model, {"messages": [{"role": "user", "content": FILLER * (n // 13) +
                                                              "\nReply with the single word OK."}], "max_tokens": 64}, 180)
            if st != 200:
                row[f"ctx_{n}"] = st if st != "error" else b
                break
            row["max_ok_tokens"], row[f"ctx_{n}_s"] = n, round(dt, 2)
    return row


# Hermes Index (portal.nousresearch.com/bench, read 2026-11-05): mean pass@1 over Hermes Bench, TerminalBench 4,
# TerminalBench Science and SkillsBench, every model run in the Hermes Agent harness. Only models we can reach free.
# Keyed by bare model id (provider prefix and ":free" dropped). Scored models lead every free cascade, best first.
HERMES_INDEX = {"deepseek-v4.1-flash": 36.91, "glm-5.3-flash": 34.95, "step-5-preview": 32.83,
                "nemotron-3.5-super-vl": 24.10}
# Owner-approved prepaid credit (not free): turbo only, ahead of the free pool, for throughput.
CREDIT = []  # Cerebras answered 402 (credit used up) 2026-11-05; re-add its two models here after a top-up.
TRANSIENT = (408, 429, 500, 502, 503, 504)  # quota/overload: still a free backend. A probe timeout (>90 s) is not: it would stall the cascade.
SMALL_CAP = 7000  # unprobed context (e.g. Groq's 8K TPM free tier): only small prompts go there


# In a pool of independent providers, one backend's error says nothing about the next: 400 context too long,
# 401/402 key or balance, 403 Cloudflare 1010, 404 model gone, 413/429 quota. Move on; max_attempts bounds it.
NEXT_ON = [400, 401, 402, 403, 404, 408, 409, 413, 422, 429, 500, 502, 503, 504, 529]


MAX_REFS = 32  # router: "per-request upstream-call limit of 32" per decision's modelRefs


def _cascade(name, refs, priority=100, signal=None):
    """One decision: the first backend is drawn by weight (patched random selector), the fallback then walks
    *refs* in order. *signal*: keyword signal that must match for this decision (None = match all).
    The router refuses a decision over MAX_REFS candidates (crash loop, 11-15): keep the best and the last resort."""
    if len(refs) > MAX_REFS:
        refs = refs[:MAX_REFS - 1] + refs[-1:]
    fallback = {"enabled": True, "max_attempts": len(refs), "per_attempt_timeout": "120s", "total_timeout": "600s",
                "retryable_status_codes": NEXT_ON}
    conds = [{"type": "keyword", "name": signal}] if signal else []
    return {"name": name, "priority": priority, "rules": {"operator": "OR" if signal else "AND", "conditions": conds},
            "description": "First backend drawn by capacity x eval weight; then every free backend in order.",
            "algorithm": {"type": "random"}, "modelRefs": refs, "fallback": fallback}


# Owner (11-04): "use all available free models in parallel while respecting rate limits"; (11-02) "choose the best
# model for each job". A model draws first traffic in proportion to its quota bucket's per-minute limit (split across
# that bucket's models) times its eval score for the task. Below QUALITY_FLOOR, or with no published limit, it is
# fallback-only (weight 0): unproven models never take first traffic, and unknown limits are never pushed.
QUALITY_FLOOR = 0.7
# Groq's Cloudflare front answers 403 (1010) to some client User-Agents (Python-urllib); the router forwards the
# client's, so pin our own on every backend.
UA = {"User-Agent": "vllm-sr-free/1"}
# Last resort when every hosted free backend is out (owner 11-05: "work regardless of whats available… even local
# small models"): the z0 farm llama.cpp router on groot. Fallback-only, 8K context, swaps models (models-max 1).
LOCAL = [("local-qwen3-8b", "http://100.113.138.100:11530/v1", None, "qwen3-8b-q4km", 8192)]


def weigh(refs, quality):
    rpm = {r["model"]: limits(bucket(r["model"])).get("req", {}).get(MIN) for r in refs}
    q = {r["model"]: quality(r["model"]) for r in refs}
    first = [m for m in rpm if rpm[m] and q[m] >= QUALITY_FLOOR]
    per_bucket = {}
    for m in first:
        per_bucket[bucket(m)] = per_bucket.get(bucket(m), 0) + 1
    out = []
    for r in refs:
        m = r["model"]
        w = round(rpm[m] / per_bucket[bucket(m)] * q[m], 3) if m in first else 0
        out.append({**r, "weight": w} if w else dict(r))
    return out


# Task signals for vllm-sr/free: the last user message picks the family's eval-ranked decision. Plain substring
# keywords, no model download (the router runs in minimal CPU mode). Unmatched requests use free-quality.
TASK_KEYWORDS = {
    "classify": ["classify", "categorize", "categorise", "which category", "label each", "sentiment of"],
    "extract": ["extract", "pull out", "list every", "list all", "find all", "parse the"],
    "format": ["as json", "in json", "json schema", "as yaml", "reformat", "format as", "convert to", "markdown table"],
    "verify": ["verify", "double-check", "double check", "check whether", "is this correct", "fact-check", "review this"],
}


FAMILIES = ("reason", "verify", "longctx", "extract", "classify", "format")  # profiles/tasks.py families


def eval_scores():
    """Per-model eval scores from profiles/results.jsonl (profiles/eval_free.py), keyed by bare model id.

    Quality is the model's, not the host's, so one score covers every provider serving that model."""
    sys.path.insert(0, str(HERE / "profiles"))
    from score import score
    res = HERE / "profiles/results.jsonl"  # local eval output, not in git: a fresh install ranks by headroom only
    rows = [json.loads(l) for l in res.read_text().splitlines() if l.strip()] if res.exists() else []
    out = {}
    for key, v in score(rows).items():
        if v["provider"] != "router" and (v["n_hard"] or 0) >= 8:
            k = key.split("/")[-1].removesuffix(":free")
            if k not in out or v["reliability"] > out[k]["reliability"]:  # best-behaved host's run
                out[k] = v
    return out


# A 429 is transient only when it is a rate limit. These bodies mean the account has no free quota at all
# (no balance, promo cap, plan required), which retries never fix: Z.ai 1113, Kilo anonymous cap.
DEAD_429 = re.compile(r"insufficient balance|recharge|no resource package|PROMOTION_MODEL_LIMIT_REACHED|balance_units", re.I)
SLOW_S = 30  # a tool turn slower than this stalls every cascade behind it (owner: timeouts are not transient)


# ---- autoscale: every free model the daily inventory sees joins the pool, re-probed with no hand edits ----
# free-model-inventory.timer (salvage/free-lanes/freelane.py) classes every OpenRouter model by price each day.
INVENTORY = Path("/mnt/zer0models/z0-wt/salvage/free-lanes/inventory")
POOL = Path.home() / ".z0int/state/posture/pool.json"  # live re-probe; HERE/pool.json is the committed seed
REPROBE_S = 6 * 3600
# GPT/Claude/Grok have owner-mandated routes of their own; Google is banned from the router (no_google_guard).
CLOSED = re.compile(r"^(openai/(?!gpt-oss)|anthropic/|x-ai/)|gemini|google|gemma|palm|bard", re.I)


CATALOGS = Path.home() / ".z0int/state/posture/catalogs.json"  # provider /models lists, refreshed by reprobe
NOT_CHAT = re.compile(r"embed|guard|safety|parse|clip|detector|retriever|rerank|translate|calibration|reward|"
                      r"starcoder|whisper|tts|asr|riva", re.I)


def refresh_catalogs():
    """Free providers' own model lists (a GET, no tokens). NIM is free per account, so every chat model counts."""
    out = {}
    for prov, base, env in (("nim", NIM, "NVIDIA_API_KEY"),):
        key = os.environ.get(env)
        if not key:
            continue
        try:
            req = urllib.request.Request(base + "/models", headers={"Authorization": f"Bearer {key}", **UA})
            out[prov] = [m["id"] for m in json.load(urllib.request.urlopen(req, timeout=30))["data"]]
        except (OSError, ValueError, KeyError):
            continue
    if out:
        CATALOGS.parent.mkdir(parents=True, exist_ok=True)
        CATALOGS.write_text(json.dumps(out | {k: v for k, v in _json_file(CATALOGS).items() if k not in out}))
    return out


def _json_file(p):
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {}


def discovered():
    """Free models the daily inventory or a provider catalog lists that CANDIDATES lacks, as candidate tuples:
    every OpenRouter :free model (also via Kilo's anonymous mirror, its own quota) and every NIM chat model."""
    names, hosted = {c[0] for c in CANDIDATES}, {(c[1], c[3]) for c in CANDIDATES}  # same model on the same host
    out = []

    def add(name, base, env, mid, ctx):
        if name not in names and (base, mid) not in hosted and not CLOSED.search(mid) and not NOT_CHAT.search(mid):
            names.add(name), hosted.add((base, mid))
            out.append((name, base, env, mid, ctx))
    files = sorted(INVENTORY.glob("*.json")) if INVENTORY.is_dir() else []
    models = _json_file(files[-1]).get("providers", {}).get("openrouter", {}).get("models", []) if files else []
    for m in models:
        mid = m.get("id", "")
        if m.get("class") in ("free", "free-preview") and mid.endswith(":free"):
            slug, ctx = mid.split("/")[-1].removesuffix(":free"), int(m.get("context_length") or 131072)
            add("or-" + slug, OR, "OPENROUTER_API_KEY", mid, ctx)
            add("kilo-" + slug, KILO, ANON, mid, ctx)
    for mid in _json_file(CATALOGS).get("nim", []):
        add("nim-" + mid.split("/")[-1], NIM, "NVIDIA_API_KEY", mid, 131072)
    return out


# ---- scout: keep finding new free providers (owner 11-15: "keep running that so we keep accumulating") ----
RESEARCH = Path("/mnt/zer0models/workspace/zer0/data/operations/free-tier-research")
QUEUE = RESEARCH / "20261102/signup-queue.json"  # the one signup queue (state todo/done/needs-kevin/...)


def merge_scan(queue, providers, today):
    """Append providers a scan found that the queue lacks, as todo rows; return the new rows."""
    known = {r["domain"] for r in queue}
    routed = {".".join((c[1].split("/")[2]).split(".")[-2:]) for c in CANDIDATES}  # e.g. nvidia.com, kilo.ai
    new = []
    for r in providers:
        if r["domain"] in known or r["domain"] == "localhost" or r.get("status") not in ("open_catalog", "needs_key") or CLOSED.search(r["domain"]):
            continue
        free = [i for i in r.get("free_ids", []) if not CLOSED.search(i)]
        new.append({"domain": r["domain"], "base_url": r.get("base_url"), "lists": r.get("lists", 0),
                    "free_models": len(free), "state": "routed" if r["domain"] in routed else "todo", "vault_handle": None, "bws_key": None,
                    "blocker": None, "found": today, "keyless": r["status"] == "open_catalog"})
    return new


def scout():
    """Harvest free-LLM lists again (scan.py: one unauthenticated GET /models per domain, no signups),
    queue every provider not seen before, and push the owner one note when there is something to sign up for."""
    today = time.strftime("%Y%m%d")
    out = RESEARCH / today
    r = subprocess.run([sys.executable, "-I", str(RESEARCH / "20261102/scan.py"), str(out)], capture_output=True,
                       text=True, timeout=1800)
    if r.returncode:
        sys.exit(f"scan failed rc={r.returncode}: {r.stderr[-400:]}")
    queue = json.loads(QUEUE.read_text())
    new = merge_scan(queue, json.loads((out / "providers.json").read_text()), today)
    if new:
        QUEUE.with_suffix(".tmp").write_text(json.dumps(queue + new, indent=1))
        QUEUE.with_suffix(".tmp").replace(QUEUE)
        top = sorted((x for x in new if x["state"] == "todo"), key=lambda x: (-x["free_models"], -x["lists"]))[:8]
        subprocess.run(["kevin-push", "decision", f"{len(new)} new free LLM providers queued",
                        "Sign-ups wanted (queue " + str(QUEUE) + "): " +
                        ", ".join(f"{x['domain']} ({x['free_models']} free)" for x in top)], timeout=60)
    print(f"scout: {len(new)} new providers queued; report {out / 'REPORT.md'}")


def candidates():
    return CANDIDATES + discovered()


def pool_rows():
    for p in (POOL, HERE / "pool.json"):
        try:
            return {r["name"]: r for r in json.loads(p.read_text())["rows"]}
        except (OSError, ValueError, KeyError):
            continue
    return {}


def ctx_open(row):
    """The cap is unmeasured: the next size up stopped on a rate limit/overload (429, 5xx), or left no status
    at all (rows rewritten before the evidence was kept). Only a real size error there proves the cap."""
    ok = row.get("max_ok_tokens")
    if ok is None or ok >= SIZES[-1]:
        return False
    nxt = row.get(f"ctx_{min(n for n in SIZES if n > ok)}")
    return nxt is None or nxt in TRANSIENT


def reprobe(st, now, cands=None):
    """Tool-probe every candidate again; a bucket blocked now keeps its last row (never poll an exhausted
    provider), a known context window is reused, only new backends get the 6K/30K/100K sizing."""
    if cands is None:
        refresh_catalogs()
    old, cands = pool_rows(), cands if cands is not None else candidates()
    blocked = {b for b, v in st.get("blocked", {}).items() if v["until"] > now}
    todo = [c for c in cands if bucket(c[0]) not in blocked or c[0] not in old]
    with cf.ThreadPoolExecutor(8) as ex:
        fresh = dict(zip([c[0] for c in todo], ex.map(lambda c: probe(c, sizes="max_ok_tokens" not in old.get(c[0], {})
                                                                                        or ctx_open(old.get(c[0], {}))), todo)))
    rows = []
    for c in cands:
        r = fresh.get(c[0])
        if not r or r.get("tool_status") == "no-key":  # the prober's env lacks the key: that says nothing about the backend
            r = old.get(c[0], {"name": c[0]})
        if r.get("tool_call") and "max_ok_tokens" not in r and "max_ok_tokens" in old.get(c[0], {}):
            r.update({k: v for k, v in old[c[0]].items() if k == "max_ok_tokens" or k.startswith("ctx_")})  # + evidence
        rows.append(r)
    return rows


def alive(row):
    """Probe row -> deployable: passed the tool probe in time, or failed only on a real rate limit."""
    if row.get("tool_call"):
        return row.get("tool_s", 0) <= SLOW_S
    return row.get("tool_status") in TRANSIENT and not DEAD_429.search(str(row.get("error", "")))


def render(exclude=(), out=HERE / "config.yaml"):
    """Write the router config. *exclude*: backends `pace` found out of quota until their reset."""
    pool = pool_rows()
    live = [c for c in candidates() if alive(pool.get(c[0], {}))]
    live = [c for c in live if c[0] not in exclude] or live  # every backend capped: keep the pool, never render empty
    bare = lambda c: c[3].split("/")[-1].removesuffix(":free")
    live.sort(key=lambda c: -HERMES_INDEX.get(bare(c), 0))  # stable: unscored keep CANDIDATES order
    if not live:
        sys.exit("no candidate passed the tool probe; refusing to render an empty pool")
    models, cards, refs = [], [], []
    for name, base, env, model, claimed in live:
        # Backend = quota bucket, so the circuit breaker trips per bucket: one Groq model (own limits) never
        # benches its siblings, while NIM/OpenRouter models share their account's breaker.
        backend = {"name": bucket(name), "provider": "vllm", "openai_compat_gateway": True,
                   "base_url": base, "weight": 100, "extra_headers": UA}
        if env:
            backend["api_key_env"] = env
        else:
            backend["api_key_env"] = "FREE_ANON_API_KEY"  # keyless endpoints; deploy.sh sets it to "anonymous"
        models.append({"name": name, "provider_model_id": model, "backend_refs": [backend]})
        ok = pool[name].get("max_ok_tokens", 0)
        # A probe that passed the largest size proved nothing past it: trust the published window there.
        cap = claimed if ok == SIZES[-1] or ctx_open(pool[name]) else (max(ok, 4000) if pool[name].get("tool_call") else SMALL_CAP)
        cards.append({"name": name, "context_window_size": cap})
        refs.append({"model": name, "use_reasoning": False})
    # turbo = same pool, fastest measured tool turn first (unmeasured last).
    speed = sorted(refs, key=lambda r: pool[r["model"]]["tool_s"] if pool[r["model"]].get("tool_call") else 1e9)
    credit = []
    for name, base, env, model, ctx in CREDIT:
        models.append({"name": name, "provider_model_id": model, "backend_refs": [
            {"name": "cerebras", "provider": "vllm", "openai_compat_gateway": True, "base_url": base, "api_key_env": env,
             "weight": 100}]})
        cards.append({"name": name, "context_window_size": ctx})
        credit.append({"model": name, "use_reasoning": False})
    speed = credit + speed
    local = []
    for name, base, env, model, ctx in LOCAL:
        models.append({"name": name, "provider_model_id": model, "backend_refs": [
            {"name": "local", "provider": "vllm", "openai_compat_gateway": True, "base_url": base,
             "api_key_env": "FREE_ANON_API_KEY", "weight": 100, "extra_headers": UA}]})
        cards.append({"name": name, "context_window_size": ctx})
        local.append({"model": name, "use_reasoning": False})
    # Per-task profiles: the same pool, ordered by eval accuracy x reliability on that task family (profiles/eval_free.py).
    # Models the eval has not scored keep CANDIDATES order after the scored ones, so the cascade never ends early.
    ev, ids = eval_scores(), {c[0]: c[3].split("/")[-1].removesuffix(":free") for c in live}

    def by(metric):
        def key(i_r):
            v = ev.get(ids[i_r[1]["model"]])
            hi = HERMES_INDEX.get(ids[i_r[1]["model"]], 0)  # the Hermes Index outranks our small task eval
            return (-hi, -(metric(v) * v["reliability"]) if v else 1, i_r[0])
        return [r for _, r in sorted(enumerate(refs), key=key)]
    task = {"quality": by(lambda v: v["quality_hard"] or 0)}
    task |= {f: by(lambda v, f=f: v["family_acc"].get(f, 0)) for f in FAMILIES}

    def qual(f):
        def q(model):
            v = ev.get(ids.get(model, ""))
            if v:
                return (v["family_acc"].get(f, 0) if f != "quality" else (v["quality_hard"] or 0)) * v["reliability"]
            return 0.9 if HERMES_INDEX.get(ids.get(model, "")) else 0  # unevaluated: fallback-only
        return q
    task = {f: weigh(rs, qual(f)) + local for f, rs in task.items()}
    fastest = min((pool[m]["tool_s"] for m in ids if pool[m].get("tool_call")), default=1)
    speed = weigh(speed, lambda m: min(1.0, fastest / pool[m]["tool_s"]) * 2 * qual("quality")(m)
                  if m in ids and pool[m].get("tool_call") else 0) + local
    # Owner 11-15: an endpoint per free model, so callers can ask for x lanes on y models (plan_lanes).
    # vllm-sr/m/<model>: that model on any host serving it, first host drawn by bucket capacity, falling over only to
    # its other hosts. vllm-sr/b/<backend>: that one host, no fallback, for evals and A/B arms that must not be scored
    # on a substitute model.
    groups = {}
    for m in models:
        groups.setdefault(model_key(m["name"]), []).append({"model": m["name"], "use_reasoning": False})
    pins = [(f"m-{k}", f"vllm-sr/m/{k}", f"{k} on any of its free hosts.", _cascade(f"m-{k}", weigh(rs, lambda _: 1.0)))
            for k, rs in groups.items()]
    pins += [(f"b-{m['name']}", f"vllm-sr/b/{m['name']}", f"{m['name']} only, no fallback.",
              {**_cascade(f"b-{m['name']}", [{"model": m["name"], "use_reasoning": False}]), "fallback": {"enabled": False}})
             for m in models]
    cfg = {
        "version": "v0.3",
        "entrypoints": [{"model_names": ["vllm-sr/free", "vllm-sr/auto", "vllm-sr/free-quality"], "recipe": "default"},
                        {"model_names": ["vllm-sr/turbo"], "recipe": "turbo"}]
                       + [{"model_names": [f"vllm-sr/free-{f}"], "recipe": f} for f in FAMILIES]
                       + [{"model_names": [ep], "recipe": rn} for rn, ep, _, _ in pins],
        "recipes": [{"name": "turbo", "description": "Free pool, fastest first.",
                     "routing": {"decisions": [_cascade("free-fastest", speed)]}}]
                   + [{"name": f, "description": f"Free pool, best eval score on {f} tasks first.",
                       "routing": {"decisions": [_cascade(f"free-{f}", task[f])]}} for f in FAMILIES]
                   + [{"name": rn, "description": desc, "routing": {"decisions": [d]}} for rn, _, desc, d in pins],
        "listeners": [{"name": "http-8899", "address": "0.0.0.0", "port": 8899, "timeout": "1800s"}],
        "global": {"router": {"fallback": {"enabled": True, "max_attempts": len(speed),
                                          # Gentle: 2 failures/120 s turned a restart's DNS warm-up into 2 min
                                          # of every circuit open (whole request fails with circuit_open).
                                          "circuit_breaker": {"consecutive_failures": 5, "cooldown_period": "30s",
                                                              "half_open_probes": 1}}},
                   "services": {"observability": {"tracing": {}}, "response_api": {}},
                   "stores": {"response_cache": {}}},
        "providers": {"defaults": {"model": refs[0]["model"]}, "models": models},
        "routing": {
            "modelCards": cards,
            "signals": {"keywords": [{"name": f"task_{f}", "operator": "OR", "case_sensitive": False, "keywords": kw}
                                     for f, kw in TASK_KEYWORDS.items()]},
            "decisions": [_cascade(f"auto-{f}", task[f], priority=200, signal=f"task_{f}") for f in TASK_KEYWORDS]
                         + [_cascade("free-quality", task["quality"])],
        },
    }
    import yaml
    Path(out).write_text(yaml.safe_dump(cfg, sort_keys=False))
    print(f"rendered {len(refs)} backends:", ", ".join(f"{r['model']}({c['context_window_size']})" for r, c in zip(refs, cards)))
    for f, rs in list(task.items()) + [("turbo", speed)]:
        w = sorted((r for r in rs if r.get("weight")), key=lambda r: -r["weight"])
        print(f"  {f}: first draw", ", ".join(f"{r['model']}={r['weight']}" for r in w) or "NONE",
              f"| fallback-only {len(rs) - len(w)}")


# --- pace: rate limits from router traffic ----------------------------------------------------------------------
STATE = Path.home() / ".z0int/state/posture"
OUT, LIVE_CFG = STATE / "free-pace.json", STATE / "vllm-sr-live.yaml"
# Excluding a backend means a redeploy, and each pod restart fails requests for ~3 min while DNS warms up.
# The cascade already skips a 429ing backend in-band, so only pull buckets capped for an hour or more.
NS, MARGIN, EXCLUDE_S = "vllm-sr", 0.95, 3600
NO_BALANCE_S = 6 * 3600  # a 402 / dead-balance 429 keeps the backend out this long, then free-pace lets it retry
MIN, HOUR, DAY = 60, 3600, 86400
WIN = {"rpm": ("req", MIN), "rph": ("req", HOUR), "rpd": ("req", DAY), "tpm": ("tok", MIN), "tpd": ("tok", DAY)}
SHARED = {"or-": "openrouter", "kilo-": "kilo", "nim-": "nim", "cohere-": "cohere", "zai-": "zai", "llm7-": "llm7"}


def model_key(backend):
    """Model a backend serves, provider prefix dropped: nim-nemotron-3-ultra and or-nemotron-3-ultra -> nemotron-3-ultra."""
    return backend.split("-", 1)[1]


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
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if r.returncode:  # no running router pod: fail the unit so service-watch sees it, never "pace" silently
        sys.exit(f"ROUTER DOWN: {r.stderr.strip()[:200]}")
    return r.stdout.splitlines()


def _decode_failures(lines):
    """request_id -> (status, routed backend) for upstream errors the router could not decode. Their execution_record
    has attempts: null, and pinned (no-fallback) requests write no execution_record at all, only routing_decision."""
    status, routed = {}, {}
    for line in lines:
        if "neutral_transport_error_decode_failed" not in line and '"routing_decision"' not in line:
            continue
        try:
            d = json.loads(line)
            if d.get("msg") == "routing_decision":
                routed[d["request_id"]] = d.get("selected_model", "")
            else:
                status[d["request_id"]] = int(d["status"])
        except (ValueError, KeyError, TypeError):
            pass
    return {rid: (s, routed.get(rid, "")) for rid, s in status.items()}


def ingest(st, lines, now):
    seen = st.setdefault("seen", {})
    undecoded = _decode_failures(lines)
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
            rec = json.loads(d["execution_record"])
            attempts = rec.get("attempts") or ([_undecoded_attempt(rid, undecoded.pop(rid)[0], rec.get("initial_model", ""))]
                                               if rid in undecoded else [])
            _attempts(st, seen, attempts, ts, now)
    for rid, (code, model) in undecoded.items():  # pinned requests: no execution_record to hang the status on
        if model:
            _attempts(st, seen, [_undecoded_attempt(rid, code, model)], now, now)
    st["seen"] = {k: v for k, v in seen.items() if now - v < 2 * HOUR}


def _undecoded_attempt(rid, code, model):
    return {"attempt_id": f"undecoded:{rid}", "model": model, "status_code": code, "error": "undecodable upstream error"}


def _attempts(st, seen, attempts, ts, now):
    for a in attempts:
        key = f"a:{a.get('attempt_id')}"
        if key in seen or a.get("status_code") is None:
            continue
        seen[key] = now
        b = bucket(a.get("model", ""))
        if a.get("status_code") != 200:
            st["events"].append([ts, b, 0])  # a refused call still spends a request
        code, err = a.get("status_code"), a.get("error") or ""
        if code == 402 or (code == 429 and DEAD_429.search(err)):
            # no free balance at all (insufficient funds): not free until it answers again; recheck in 6 h
            until, why = ts + NO_BALANCE_S, f"{code} no balance: " + err[:150]
        elif code == 429:
            until, why = ts + reset_after(err, ts), "429: " + err[:160]
        else:
            continue
        if until > st["blocked"].get(b, {}).get("until", 0):
            st["blocked"][b] = {"until": until, "why": why}


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


FAILED = {"candidates_exhausted", "circuit_open", "side_effect_failure", "non_retryable", "max_attempts_exceeded",
          "total_deadline_exceeded", "fallback_halted", "body_not_replayable"}


def audit(lines):
    """REQUIREMENTS.md R1-R3 measured from router traffic: spread of first draws, failed requests, task decisions."""
    recs = []
    for line in lines:
        if "fallback_execution_complete" not in line:
            continue
        try:
            recs.append(json.loads(json.loads(line)["execution_record"]))
        except (ValueError, KeyError):
            continue
    if not recs:
        return {"requests": 0, "ok": True, "why": "no traffic"}
    first = {}
    for r in recs:
        b = bucket((r.get("visited_models") or ["?"])[0])
        first[b] = first.get(b, 0) + 1
    failed = sum(1 for r in recs if r.get("final_status") in FAILED)
    decisions = sorted({r.get("decision_name") for r in recs})
    top = max(first.values()) / len(recs)
    why = []
    if failed:
        why.append(f"R2: {failed}/{len(recs)} requests failed")
    if len(recs) >= 20 and (len(first) < 2 or top > 0.8):
        why.append(f"R1: first draws not spread ({len(first)} buckets, top {top:.0%})")
    return {"requests": len(recs), "failed": failed, "first_draw_buckets": first, "decisions": decisions,
            "ok": not why, "why": "; ".join(why) or "R1 spread, R2 no failures"}


def cards():
    """The per-backend context the router is told (alive rows only): a change here must redeploy."""
    return {n: r.get("max_ok_tokens") for n, r in sorted(pool_rows().items()) if alive(r)}


def pace():
    now = time.time()
    try:
        st = json.loads(OUT.read_text())
    except (OSError, ValueError):
        st = {}
    st.setdefault("events", []), st.setdefault("blocked", {})
    st.setdefault("cards", cards())  # baseline before any re-probe, so a re-sized card below redeploys
    since = min(now - st.get("cursor", now - HOUR), DAY) + 30
    lines = pod_log(since)
    ingest(st, lines, now)
    st["audit"] = audit(pod_log(HOUR))  # last hour, read each minute
    if not st["audit"]["ok"]:
        print("REQUIREMENTS FAIL:", st["audit"]["why"])
    st["cursor"] = now
    usage = assess(st, now)
    want = excluded(st, now)
    if now - st.get("probed_at", 0) > REPROBE_S and "--dry-run" not in sys.argv:
        rows = reprobe(st, now)
        names = sorted(r["name"] for r in rows if alive(r))
        before = [n for n, r in pool_rows().items() if alive(r)]
        if len(names) < len(before) // 2:  # half the pool died at once: the prober broke (keys, network), not the providers
            print(f"re-probe kept the old pool: only {len(names)} of {len(before)} backends answered")
        else:
            POOL.parent.mkdir(parents=True, exist_ok=True)
            POOL.with_suffix(".tmp").write_text(json.dumps({"probed_at": time.strftime("%FT%TZ", time.gmtime()), "rows": rows}, indent=1))
            POOL.with_suffix(".tmp").replace(POOL)
            print("re-probed:", len(names), "backends alive")
        st["probed_at"] = now
    # Desired vs deployed, every minute: a deploy that failed (helm timeout) is retried next tick, not in 6 h.
    pool_names, now_cards = sorted(n for n, r in pool_rows().items() if alive(r)), cards()
    # A re-probe that only re-sizes a card (6K -> 128K) keeps the same names, and must still redeploy.
    changed = want != st.get("excluded", []) or pool_names != st.get("pool", []) or now_cards != st["cards"]
    if changed and "--dry-run" not in sys.argv:
        render(exclude=set(want), out=LIVE_CFG)
        r = subprocess.run([str(HERE / "deploy.sh"), str(LIVE_CFG)], capture_output=True, text=True, timeout=600)
        st["deploy"] = {"at": now, "rc": r.returncode, "excluded": want, "tail": r.stdout[-300:] + r.stderr[-300:]}
        if r.returncode == 0:  # a failed deploy keeps the old state, so the next minute retries it
            st["excluded"], st["pool"], st["cards"] = want, pool_names, now_cards
    st["at"] = datetime.fromtimestamp(now).astimezone().isoformat(timespec="seconds")
    st["usage"] = usage
    STATE.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(st))
    tmp.replace(OUT)
    for b, v in sorted(st["blocked"].items()):
        print(f"blocked {b} for {int(v['until'] - now)}s: {v['why'][:100]}")
    print("excluded from router:", st.get("excluded", []))


# ---- consumers: call deployed backends by name through the router (diversity / cross-model replay) ----
# Every vllm-sr/free-* entrypoint is a static cascade (first healthy backend answers), so replay and
# fan-out must address backends by name. Moved here from chatgpt-driver/free-lane/free_llm.py (2026-11-07)
# so the router, its limits and its clients live in one place.
import re as _re
from concurrent.futures import ThreadPoolExecutor as _TPE
# The stable NodePort URL on groot's tailnet IP (deploy.sh); the old ClusterIP died with cluster 1.
ROUTER_BASE = os.environ.get("VLLM_SR_URL", "http://100.113.138.100:30899/v1")
ROUTER = ROUTER_BASE + "/chat/completions"
# Strong, diverse families first; each provider bucket appears at most twice.
# Deployed backends only (semantic-router-config); see deployed(). Order: strong, diverse families first.
REASONERS = ["nim-nemotron-3-ultra", "groq-gpt-oss-120b", "or-dots-3-note", "nim-step-5-preview",
             "or-nemotron-3-ultra", "or-xing4.0-29b", "groq-qwen3.8-27b", "or-flint-v1"]
FAST = ["groq-gpt-oss-20b", "nim-nemotron-3.5-lightning", "or-nemotron-3.5-lightning", "groq-qwen3.8-27b"]
# Groq free tier is 8K tokens/minute per model: keep any single prompt under ~6K tokens (~24K chars).
MAX_PROMPT_CHARS = 24000


def deployed():
    """Backend names in the live router config (falls back to the static lists)."""
    import subprocess
    r = subprocess.run(["kubectl", "-n", "vllm-sr", "get", "cm", "semantic-router-config", "-o",
                        "jsonpath={.data.config\\.yaml}"], capture_output=True, text=True, timeout=30)
    names = set(_re.findall(r"\b(?:nim|groq|or|kilo|ovh|samba|sf|cerebras)-[a-z0-9.\-]+", r.stdout))
    return names or set(REASONERS + FAST)


def _json(text):
    if not text:
        return None
    m = _re.search(r"\{.*\}", text, _re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except ValueError:
        return None


def ask(model, prompt, system=None, max_tokens=8000, timeout=300):
    prompt = prompt[:MAX_PROMPT_CHARS]
    msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    body = json.dumps({"model": model, "messages": msgs, "max_tokens": max_tokens, "temperature": 0}).encode()
    t0 = time.time()
    try:
        req = urllib.request.Request(ROUTER, data=body, headers={"Content-Type": "application/json",
                                     # Groq's Cloudflare front returns 403 to the default Python-urllib agent.
                                     "User-Agent": "vllm-sr-free-pool/1"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            served = r.headers.get("X-Vsr-Selected-Model") or model
            d = json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"model": model, "ok": False, "error": f"http {e.code}", "secs": round(time.time() - t0, 1)}
    except Exception as e:  # timeouts, resets
        return {"model": model, "ok": False, "error": type(e).__name__, "secs": round(time.time() - t0, 1)}
    msg = (d.get("choices") or [{}])[0].get("message") or {}
    text = msg.get("content") or ""
    j = _json(text) or _json(msg.get("reasoning_content") or "")
    u = d.get("usage") or {}
    return {"model": served, "ok": j is not None, "json": j, "text": text[-2000:], "secs": round(time.time() - t0, 1),
            "tokens": [u.get("prompt_tokens"), u.get("completion_tokens")],
            "error": None if j is not None else ("empty content" if not text else "no json")}


# ---- inventory + lane plans: "give me x lanes on y models" from what the endpoint can serve right now ----
DECISION_URL = "http://127.0.0.1:11532/v1"  # sr:decision (d1 shim on groot), outside the router


def model_quality():
    """{model key: {family: score}}: eval accuracy x reliability (best host's run); Hermes Index only = 0.9 prior."""
    ev, out = eval_scores(), {}
    for name, _, _, pid, _ in CANDIDATES + LOCAL + CREDIT:
        v, bare = ev.get(pid.split("/")[-1].removesuffix(":free")), pid.split("/")[-1].removesuffix(":free")
        fam = {f: (v["family_acc"].get(f, 0) * v["reliability"]) for f in FAMILIES} if v else {}
        fam["quality"] = (v["quality_hard"] or 0) * v["reliability"] if v else (0.9 if HERMES_INDEX.get(bare) else 0)
        old = out.get(model_key(name), {})
        out[model_key(name)] = {f: max(fam.get(f, fam["quality"]), old.get(f, 0)) for f in ("quality",) + FAMILIES}
    return out


def _live_cfg():
    import yaml
    r = subprocess.run(["kubectl", "-n", NS, "get", "cm", "semantic-router-config", "-o",
                        "jsonpath={.data.config\\.yaml}"], capture_output=True, text=True, timeout=30,
                       env={**os.environ, "KUBECONFIG": os.environ.get("KUBECONFIG", str(Path.home() / ".kube/k3s.yaml"))})
    return yaml.safe_load(r.stdout) if r.returncode == 0 and r.stdout.strip() else None


def _frac(cell):
    used, cap = (int(x) for x in cell.split("/"))
    return used, cap


def inventory(cfg=None, st=None, now=None):
    """Every endpoint by workflow, with per-model rpm headroom from free-pace's live counts (no probe calls)."""
    import yaml
    cfg = cfg or _live_cfg() or yaml.safe_load((HERE / "config.yaml").read_text())  # cluster unreachable: last render
    now = now or time.time()
    if st is None:
        try:
            st = json.loads(OUT.read_text())
        except (OSError, ValueError):
            st = {}
    usage, blocked = st.get("usage", {}), {b: v for b, v in st.get("blocked", {}).items() if v["until"] > now}
    names = [m["name"] for m in cfg["providers"]["models"]]
    per_bucket = {}
    for n in names:
        per_bucket[bucket(n)] = per_bucket.get(bucket(n), 0) + 1
    quality, groups = model_quality(), {}
    for n in names:
        b = bucket(n)
        lim, row, why = limits(b), usage.get(b, {}), blocked.get(b, {}).get("why")
        caps = {k: lim.get(kind, {}).get(w) for k, (kind, w) in WIN.items()}
        cell = lambda k: f"{WIN[k][0]}_{WIN[k][1]}s"
        left = {k: (caps[k] - (_frac(row[cell(k)])[0] if cell(k) in row else 0)) if caps[k] else None for k in caps}
        cap = caps["rpm"]
        if not why and "req_86400s" in row:
            u, c = _frac(row["req_86400s"])
            why = f"daily {u}/{c}" if u >= MARGIN * c else None
        # this minute's headroom, but never more requests than the hour/day windows have left
        room = min(max(0, v) for v in (left["rpm"], left["rph"], left["rpd"]) if v is not None) if cap else None
        head = None if not cap else 0 if why else round(room / per_bucket[b], 2)
        groups.setdefault(model_key(n), []).append(
            {"backend": n, "endpoint": f"vllm-sr/b/{n}", "bucket": b, "rpm_cap_shared": cap, "bucket_models": per_bucket[b],
             "headroom_rpm": head, "blocked": why or False,
             "caps": caps, "left": left, "until": blocked.get(b, {}).get("until")})
    models = sorted(({"model": k, "endpoint": f"vllm-sr/m/{k}", "quality": quality.get(k, {}), "hosts": hs,
                      "headroom_rpm": round(sum(h["headroom_rpm"] or 0 for h in hs), 2)} for k, hs in groups.items()),
                    key=lambda m: (-m["quality"].get("quality", 0), -m["headroom_rpm"]))
    eps = [n for e in cfg["entrypoints"] for n in e["model_names"]]
    try:
        with urllib.request.urlopen(DECISION_URL + "/models", timeout=3) as r:
            decision_up = r.status == 200
    except Exception:
        decision_up = False
    try:
        pick = json.loads((STATE / "burn-gate-pick.json").read_text())
    except (OSError, ValueError):
        pick = None
    return {
        "base_url": ROUTER_BASE, "at": datetime.fromtimestamp(now).astimezone().isoformat(timespec="seconds"),
        "pace_at": st.get("at"),
        "workflows": {
            "free": {"endpoints": [e for e in eps if e.startswith("vllm-sr/free") or e == "vllm-sr/auto"],
                     "use": "delegation, z0live smol lanes, background units: cascade over every free model"},
            "turbo": {"endpoints": ["vllm-sr/turbo"], "use": "burst small units, fastest first"},
            "model": {"endpoints": [m["endpoint"] for m in models],
                      "use": "lanes pinned to one model (GEPA arms, diversity): falls over only to that model's other hosts"},
            "pinned": {"endpoints": [h["endpoint"] for m in models for h in m["hosts"]],
                       "use": "evals and A/B arms: one host, no fallback, never scored on a substitute"},
            "decision": {"url": DECISION_URL, "up": decision_up, "outside_router": True,
                         "use": "local d1 decisions; native /v1/systemone only (vllm-sr-turbo-router rule 23)"},
            "paid": {"outside_router": True, "pick": pick,
                     "use": "Claude subs via burn-gate --pick / t3pace.py pick, Sol via fx; never through vllm-sr"},
            "opencode": {"outside_router": True, "use": "opencode run -m opencode/<free-model> (CLI-only free models)"},
        },
        "models": models,
    }


def _alloc(weights, n):
    """Split n into len(weights) integer shares in proportion to weights, each >= 1 (needs n >= len(weights))."""
    total, k = sum(weights), len(weights)
    raw = [1 + (n - k) * w / total for w in weights]
    out = [int(r) for r in raw]
    for i in sorted(range(k), key=lambda i: out[i] - raw[i])[:n - sum(out)]:
        out[i] += 1
    return out


def plan_lanes(inv, lanes, models=None, family="quality", pinned=False):
    """*lanes* parallel workers over the *models* best free models (by *family* eval) that have rpm headroom now.

    Lanes follow headroom, so no model or bucket is planned past its published limit. pinned=True hands each lane
    a single host (vllm-sr/b/...) instead of the model's host cascade (vllm-sr/m/...)."""
    score = lambda m: m["quality"].get(family, m["quality"].get("quality", 0))
    cands = sorted((m for m in inv["models"] if m["headroom_rpm"] > 0), key=lambda m: (-score(m), -m["headroom_rpm"]))
    want = models or lanes
    chosen = cands[:min(want, lanes, len(cands))]
    warnings = [] if len(chosen) >= min(want, lanes) else [f"only {len(chosen)} free models have headroom now"]
    out = []
    for m, c in zip(chosen, _alloc([m["headroom_rpm"] for m in chosen], lanes) if chosen else []):
        if pinned:
            hosts = sorted((h for h in m["hosts"] if (h["headroom_rpm"] or 0) > 0), key=lambda h: -h["headroom_rpm"])
            hosts = hosts[:c]
            for h, hc in zip(hosts, _alloc([h["headroom_rpm"] for h in hosts], c)):
                out += [{"model": m["model"], "endpoint": h["endpoint"], "bucket": h["bucket"],
                         "rpm_budget": round(h["headroom_rpm"] / hc, 2)} for _ in range(hc)]
        else:
            out += [{"model": m["model"], "endpoint": m["endpoint"], "rpm_budget": round(m["headroom_rpm"] / c, 2)}
                    for _ in range(c)]
    for i, l in enumerate(out):
        l["lane"] = i
    if any(l["rpm_budget"] < 1 for l in out):
        warnings.append("some lanes get < 1 request/min: fewer lanes or more models, or expect 429 fallovers")
    return {"base_url": inv.get("base_url", ROUTER_BASE), "family": family, "pinned": pinned, "lanes": out,
            "total_rpm": round(sum(l["rpm_budget"] for l in out), 2), "warnings": warnings}


DEAD = set()  # backends that answered 401/404 this process (missing key / not deployed): never retried


def fanout(prompt, models=REASONERS, n=3, system=None, workers=3):
    """First n distinct models that return parseable JSON; failures fall through to the next model."""
    live = deployed()
    out, tried, queue = [], [], [m for m in models if m in live and m not in DEAD]
    with _TPE(workers) as ex:
        while len(out) < n and queue:
            batch = [queue.pop(0) for _ in range(min(n - len(out), len(queue)))]
            for r in ex.map(lambda m: ask(m, prompt, system), batch):
                tried.append(r)
                if r.get("error") in ("http 401", "http 404"):
                    DEAD.add(r["model"])
                if r["ok"]:
                    out.append(r)
    return out, tried



if __name__ == "__main__":
    if sys.argv[1:] == ["probe"]:
        with cf.ThreadPoolExecutor(8) as ex:
            rows = list(ex.map(probe, candidates()))
        (HERE / "pool.json").write_text(json.dumps({"probed_at": time.strftime("%FT%TZ", time.gmtime()), "rows": rows}, indent=1))
        for r in rows:
            print(json.dumps(r)[:240])
    elif sys.argv[1:] == ["scout"]:  # free_pool.py scout: daily free-provider scan into the signup queue
        scout()
    elif sys.argv[1:2] == ["pace"]:
        pace()
    elif sys.argv[1:2] == ["ask"]:  # free_pool.py ask <backend|any> "<prompt>"
        m = sys.argv[2]
        r = fanout(sys.argv[3], n=1)[0][0] if m == "any" else ask(m, sys.argv[3])
        print(json.dumps(r))
    elif sys.argv[1:] == ["render"]:
        render()
    elif sys.argv[1:2] == ["inventory"]:  # free_pool.py inventory: every endpoint by workflow + live headroom
        print(json.dumps(inventory(), indent=1))
    elif sys.argv[1:2] == ["limits"]:  # free_pool.py limits: per-backend caps and what is left now (window: left/cap)
        for m in inventory()["models"]:
            for h in m["hosts"]:
                cells = " ".join(f"{k}={h['left'][k]}/{c}" for k, c in h["caps"].items() if c)
                print(f"{h['backend']:30} {h['bucket']:22} head={h['headroom_rpm']} {cells}"
                      + (f" BLOCKED {h['blocked']}" if h["blocked"] else ""))
    elif sys.argv[1:2] == ["lanes"]:  # free_pool.py lanes <x> [--models y] [--family f] [--pinned]
        args = sys.argv[2:]
        opt = lambda k, d: args[args.index(k) + 1] if k in args else d
        p = plan_lanes(inventory(), int(args[0]), models=int(opt("--models", "0")) or None,
                       family=opt("--family", "quality"), pinned="--pinned" in args)
        print(json.dumps(p, indent=1))
        sys.exit(1 if not p["lanes"] else 0)
    elif sys.argv[1:2] == ["audit"]:  # free_pool.py audit [hours]: exit 1 when REQUIREMENTS R1/R2 fail live
        a = audit(pod_log(float(sys.argv[2] if len(sys.argv) > 2 else 1) * HOUR))
        print(json.dumps(a))
        sys.exit(0 if a["ok"] else 1)
    else:
        sys.exit(__doc__)
