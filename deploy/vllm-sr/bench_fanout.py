#!/usr/bin/env python3
"""Fanout bench: N independent small tasks through the router at concurrency C, vs. the same N serially.
Records per-call model, status, latency, and peak in-flight overlap. Usage: bench_fanout.py N C [out.jsonl]"""
import json, ssl, sys, threading, time, urllib.request, urllib.error, concurrent.futures as cf
from collections import Counter

ROUTER = __import__("os").environ.get("VSR_URL", "http://10.43.216.55:8899/v1") + "/chat/completions"
N, C = int(sys.argv[1]), int(sys.argv[2])
OUT = sys.argv[3] if len(sys.argv) > 3 else None
TASKS = [f"Classify the sentiment of this review as positive, negative or neutral. Answer with one word. Review #{i}: "
         + ["The battery died after two days.", "Absolutely love the screen.", "It arrived on Tuesday.",
            "Customer support never answered.", "Best purchase this year!", "The box was blue."][i % 6] for i in range(N)]
lock = threading.Lock(); inflight = 0; peak = 0


def call(i):
    global inflight, peak
    body = json.dumps({"model": "vllm-sr/auto", "messages": [{"role": "user", "content": TASKS[i]}], "max_tokens": 300}).encode()
    req = urllib.request.Request(ROUTER, data=body, method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "turbo-bench/1"})
    with lock:
        inflight += 1; peak = max(peak, inflight)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            d = json.loads(r.read()); model = r.headers.get("X-Vsr-Selected-Model"); status = r.status
            text = (d["choices"][0]["message"].get("content") or "").strip()
            usage = d.get("usage") or {}
    except urllib.error.HTTPError as e:
        model, status, text, usage = e.headers.get("X-Vsr-Selected-Model"), e.code, e.read()[:120].decode(errors="replace"), {}
    except Exception as e:
        model, status, text, usage = None, "error", str(e)[:120], {}
    finally:
        with lock:
            inflight -= 1
    return {"i": i, "status": status, "model": model, "latency_s": round(time.time() - t0, 3),
            "answer": text[:30], "completion_tokens": usage.get("completion_tokens")}


def run(conc):
    global peak
    peak = 0; t0 = time.time()
    with cf.ThreadPoolExecutor(conc) as ex:
        rows = list(ex.map(call, range(N)))
    return rows, round(time.time() - t0, 2), peak


report = {}
# Rate-limit preflight: what this run asks of each model bucket vs its published/observed limit.
try:
    LIM = {(m["provider"] + "-" + m["model"].split("/")[-1]): m for m in json.load(open(__import__("os").path.join(__import__("os").path.dirname(__file__) or ".", "limits.json")))["models"]}
except (OSError, ValueError, KeyError):
    LIM = {}
EST_TOK = int(__import__("os").environ.get("EST_TOKENS_PER_CALL", "350"))
if LIM:
    pool = [k for k in LIM if k in ("groq-gpt-oss-20b", "groq-gpt-oss-120b", "groq-qwen3.8-27b", "cerebras-gpt-oss-120b", "cerebras-qwen-3.8-27b")]
    per = 2 * N / len(pool)  # both runs, uniform random share
    pre = {k: {"calls": round(per, 1), "rpm_limit": LIM[k].get("rpm"), "tpm_limit": LIM[k]["tpm"], "rpd_limit": LIM[k]["rpd"],
               "rpd_used_pct": round(100 * per / LIM[k]["rpd"], 2),
               "rpm_overrun_if_within_1min": bool(LIM[k].get("rpm")) and per > LIM[k]["rpm"],
               "tpm_overrun_if_within_1min": per * EST_TOK > LIM[k]["tpm"]} for k in pool}
    report["preflight"] = {"est_tokens_per_call": EST_TOK, "per_model": pre,
                           "capacity_share_rpm": {k: round((LIM[k].get("rpm") or 0) / sum((LIM[j].get("rpm") or 0) for j in pool), 3) for k in pool}}
for label, conc in (("serial", 1), (f"fanout_c{C}", C)):
    rows, wall, pk = run(conc)
    ok = [r for r in rows if r["status"] == 200 and r["answer"]]
    lat = sorted(r["latency_s"] for r in ok)
    toks = sum(r["completion_tokens"] or 0 for r in ok)
    report[label] = {"wall_s": wall, "peak_inflight": pk, "ok": len(ok), "n": N,
                     "statuses": dict(Counter(str(r["status"]) for r in rows)),
                     "models": dict(Counter(r["model"] for r in ok)),
                     "p50_s": lat[len(lat) // 2] if lat else None, "p95_s": lat[int(len(lat) * 0.95) - 1] if lat else None,
                     "agg_completion_tok_per_s": round(toks / wall, 1),
                     "tokens_spent": sum((r.get("completion_tokens") or 0) for r in ok)}
    if OUT:
        with open(OUT, "a") as f:
            for r in rows:
                f.write(json.dumps({"run": label, "at": time.time(), **r}) + "\n")
print(json.dumps(report, indent=1))
