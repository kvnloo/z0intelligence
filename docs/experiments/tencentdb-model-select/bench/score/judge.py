#!/usr/bin/env python3
"""SECONDARY local LLM judge (PREREG §4.4). Reported separately; never used for gates/ranking.

  judge.py runs/<run-id>/report.json --out runs/<run-id>/judge.json [--port 11591]

Fixed judge: qwen3-14b-q4km (local, reasoning off, temperature 0, seed 0) on a dedicated
llama-server started here under the same locks as local arms. Synthetic corpus only.
Per record: SUPPORTED / UNSUPPORTED / MISATTRIBUTED given the conversation.
Per gold fact: COVERED / NOT_COVERED given the stored records.
"""
import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B))
import run_arm  # noqa: E402  (reuse local-server + lock helpers)

JUDGE_ID = "qwen3-14b-q4km"
SYS = ("You are a strict evaluator of a personal-memory system. Answer with exactly one word from the allowed set, "
       "no explanation.")
REC_TMPL = """Conversation between a user and an AI assistant (messages may be truncated):
<conversation>
{conv}
</conversation>

Stored memory: "{mem}"

Is this memory supported by the conversation AND correctly attributed (about the user, the user's own projects/decisions/
preferences, or an explicit instruction from the user; not a hypothetical, not someone else's preference, not an assistant
suggestion the user rejected, not outdated after a later correction)?
Allowed answers: SUPPORTED, UNSUPPORTED, MISATTRIBUTED"""
GOLD_TMPL = """Stored memories:
{mems}

Fact: "{fact}"

Does at least one stored memory express this fact (same meaning; wording may differ)?
Allowed answers: COVERED, NOT_COVERED"""


def conv_text(conv, limit=1200, total=24000):
    out = []
    for s in conv["sessions"]:
        for r in s["rounds"]:
            for role in ("user", "assistant"):
                c = r[role]["content"]
                out.append(f"[{role}] {c[:limit]}{' …' if len(c) > limit else ''}")
    t = "\n".join(out)
    return t[:total]


def ask(port, prompt, allowed):
    body = {"model": JUDGE_ID, "temperature": 0, "seed": 0, "max_tokens": 8,
            "messages": [{"role": "system", "content": SYS}, {"role": "user", "content": prompt}]}
    req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        txt = json.loads(r.read())["choices"][0]["message"]["content"] or ""
    up = txt.upper()
    for a in sorted(allowed, key=len, reverse=True):
        if re.search(rf"\b{a}\b", up):
            return a
    return "INVALID"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("report")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    report = json.loads(Path(a.report).read_text())
    corpus = {c["id"]: c for c in json.loads((B / "corpus" / "corpus.json").read_text())["conversations"]}
    cfg = json.loads((B / "candidates.json").read_text())
    cand = next(c for c in cfg["candidates"] if c["id"] == JUDGE_ID)
    outdir = Path(a.out).parent
    import fcntl
    locks = []
    for path, mode in ((run_arm.QUIET_LOCK, fcntl.LOCK_SH), (run_arm.GPU_LOCK, fcntl.LOCK_EX)):
        f = open(path, "a")
        fcntl.flock(f, mode)
        locks.append(f)
    server, ctx = run_arm.start_local_server(cand, cfg, outdir)
    port = cfg["local_server"]["port"]
    res = {"judge": JUDGE_ID, "ctx": ctx, "candidates": {}}
    try:
        for cid, c in report["candidates"].items():
            tot = {"records": 0, "SUPPORTED": 0, "UNSUPPORTED": 0, "MISATTRIBUTED": 0, "INVALID": 0, "gold": 0, "COVERED": 0}
            for rep in c["per_conv"]:
                for conv_id, pc in rep.items():
                    conv = corpus[conv_id]
                    ct = conv_text(conv)
                    for r in pc["records"]:
                        v = ask(port, REC_TMPL.format(conv=ct, mem=r["content"][:600]), ["SUPPORTED", "UNSUPPORTED", "MISATTRIBUTED"])
                        tot["records"] += 1
                        tot[v] += 1
                    mems = "\n".join(f"- {r['content'][:400]}" for r in pc["records"]) or "(none)"
                    for g in conv["gold"]:
                        tot["gold"] += 1
                        if pc["records"] and ask(port, GOLD_TMPL.format(mems=mems, fact=g["statement"]), ["COVERED", "NOT_COVERED"]) == "COVERED":
                            tot["COVERED"] += 1
            n = max(1, tot["records"])
            p = tot["SUPPORTED"] / n
            rcl = tot["COVERED"] / max(1, tot["gold"])
            res["candidates"][cid] = {**tot, "judge_precision": p, "judge_recall": rcl,
                                      "judge_f1": (2 * p * rcl / (p + rcl)) if (p + rcl) else 0.0,
                                      "judge_halluc_rate": (tot["UNSUPPORTED"] + tot["MISATTRIBUTED"]) / n}
    finally:
        server.terminate()
        server.wait(30)
        for f in locks:
            fcntl.flock(f, fcntl.LOCK_UN)
    Path(a.out).write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
