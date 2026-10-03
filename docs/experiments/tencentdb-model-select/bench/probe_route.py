#!/usr/bin/env python3
"""Pre-registered route/effort probe (PREREG §6): one synthetic call through the shim.

  probe_route.py --cand luna-xhigh [--inject-override '{"reasoning":{"effort":"xhigh"}}']

Prompt: "Reply with exactly: OK". Records status, usage (incl. reasoning tokens) and cost to
runs/probes/<cand>/. Paid calls count against runs/paid-budget.json like any other.
"""
import argparse
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

B = Path(__file__).resolve().parent
sys.path.insert(0, str(B))
import run_arm  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--cand", required=True)
ap.add_argument("--inject-override")
a = ap.parse_args()
cfg = json.loads((B / "candidates.json").read_text())
c = next(x for x in cfg["candidates"] if x["id"] == a.cand)
inject = json.loads(a.inject_override) if a.inject_override else c["inject"]
out = run_arm.ROOT / "runs" / "probes" / c["id"] / time.strftime("%Y%m%dT%H%M%S")
out.mkdir(parents=True)
port = run_arm.free_port()
cmd = [str(B / "bin" / "with-secrets"), c["key_env"], "--", sys.executable, str(B / "harness" / "shim.py"),
       "--port", str(port), "--upstream", c["endpoint"], "--model", c["model"], "--key-env", c["key_env"],
       "--inject", json.dumps(inject), "--price-in", str(c["price"]["in"]), "--price-out", str(c["price"]["out"]),
       "--ledger", str(out / "ledger.jsonl"), "--calls-dir", str(out / "calls"), "--arm", c["id"]]
if "cache_read" in c["price"]:
    cmd += ["--price-cache-read", str(c["price"]["cache_read"])]
if c.get("paid"):
    cmd += ["--paid", "--budget-file", str(run_arm.BUDGET), "--cap-usd", str(run_arm.CAP_USD),
            "--arm-cap-usd", str(c.get("arm_cap_usd", 0))]
shim = subprocess.Popen(cmd, stdout=open(out / "shim.log", "w"), stderr=subprocess.STDOUT)
try:
    assert run_arm.wait_http(f"http://127.0.0.1:{port}/v1/models", 60, shim), "shim down"
    body = {"model": c["model"], "messages": [{"role": "user", "content": "Reply with exactly: OK"}], "max_tokens": 32000}
    req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            code, resp = r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        code, resp = e.code, json.loads(e.read() or b"{}")
finally:
    shim.terminate()
    shim.wait(10)
led = [json.loads(x) for x in (out / "ledger.jsonl").read_text().splitlines()]
summary = {"cand": c["id"], "inject": inject, "status": code,
           "content": ((resp.get("choices") or [{}])[0].get("message") or {}).get("content") if code == 200 else None,
           "usage": resp.get("usage"), "error": resp.get("error") if code != 200 else None,
           "ledger": [{k: r.get(k) for k in ("status", "latency_ms", "cost_usd", "finish_reason", "reasoning_chars")} for r in led]}
(out / "summary.json").write_text(json.dumps(summary, indent=1))
print(json.dumps(summary, indent=1))
