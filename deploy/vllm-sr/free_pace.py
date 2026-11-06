#!/usr/bin/env python3
"""free-pace: track every free backend's rate limits from the router's own traffic and pull capped ones out.

Run every minute (free-pace.timer). Reads the vllm-sr pod log since the last run (no probe calls), then:
- counts requests and tokens per quota bucket against the provider's published limits (LIMITS);
- reads every upstream 429 for its reset ("try again in 7m12s", X-RateLimit-Reset, known daily-cap codes);
- blocks a bucket at its reset or when it reaches 95% of a window;
- re-renders the router config without backends blocked >= EXCLUDE_S and redeploys, only when that set changes,
  so the router stops re-trying a capped model every circuit-breaker cooldown and falls straight through.
State: ~/.z0int/state/posture/free-pace.json (burn-pace reports it as lane "free").
"""
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE = Path.home() / ".z0int/state/posture"
OUT, LIVE_CFG = STATE / "free-pace.json", STATE / "vllm-sr-live.yaml"
NS, MARGIN, EXCLUDE_S = "vllm-sr", 0.95, 600
MIN, HOUR, DAY = 60, 3600, 86400
# Published free-tier limits, per quota bucket. Groq: console.groq.com/docs/rate-limits read 2026-11-05 (per model,
# per org; cached tokens do not count). NIM: ~40 RPM per account. Kilo: ~200 req/h per IP across its :free models.
# OpenRouter :free: 1000 req/day per account (key reports free_model_daily_requests), 20 RPM. Unknown -> learned from 429s.
GROQ = {"req": {MIN: 30, DAY: 1000}, "tok": {MIN: 8000, DAY: 200000}}
LIMITS = {"openrouter": {"req": {MIN: 20, DAY: 1000}}, "kilo": {"req": {HOUR: 200}}, "nim": {"req": {MIN: 40, DAY: 10000}}}
SHARED = {"or-": "openrouter", "kilo-": "kilo", "nim-": "nim"}


def bucket(backend):
    """Quota bucket of a router backend: a whole account where the provider pools limits, else the model."""
    for prefix, b in SHARED.items():
        if backend.startswith(prefix):
            return b
    return backend  # groq-*, samba-*: limits are per model


def limits(b):
    return GROQ if b.startswith("groq-") else LIMITS.get(b, {})


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
    for b in sorted({e[1] for e in st["events"]} | set(LIMITS)):
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
    import free_pool
    long = {b for b, v in st["blocked"].items() if v["until"] - now >= EXCLUDE_S}
    return sorted(c[0] for c in free_pool.CANDIDATES if bucket(c[0]) in long)


def main():
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
        sys.path.insert(0, str(HERE))
        import free_pool
        free_pool.render(exclude=set(want), out=LIVE_CFG)
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
    sys.path.insert(0, str(HERE))
    main()
