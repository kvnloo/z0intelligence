#!/usr/bin/env python3
"""Local route/feasibility probe (not a benchmark result): for each runnable local candidate,
start the dedicated llama-server (same ctx ladder/flags as run_arm), record which ctx loaded and
VRAM used, then send (1) "Reply with exactly: OK" and (2) one tool-call request (synthetic), and
stop the server. Holds quiet-lane (shared) + gpu.lock (exclusive) for the whole probe.

  probe_local.py [cand ...]
"""
import fcntl
import json
import sys
import time
import urllib.request
from pathlib import Path

B = Path(__file__).resolve().parent
sys.path.insert(0, str(B))
import run_arm  # noqa: E402

TOOL = {"type": "function", "function": {"name": "read", "description": "Read the contents of a file at the given relative path.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}


def post(port, body):
    req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"content-type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read()), round(time.time() - t0, 2)


cfg = json.loads((B / "candidates.json").read_text())
ids = sys.argv[1:] or [c["id"] for c in cfg["candidates"] if c["kind"] == "local" and c["callable"] and not c.get("why_not")]
out = run_arm.ROOT / "runs" / "probes" / "local" / time.strftime("%Y%m%dT%H%M%S")
out.mkdir(parents=True)
locks = []
for path, mode in ((run_arm.QUIET_LOCK, fcntl.LOCK_SH), (run_arm.GPU_LOCK, fcntl.LOCK_EX)):
    f = open(path, "a")
    fcntl.flock(f, mode)
    locks.append(f)
results = {}
port = cfg["local_server"]["port"]
try:
    for cid in ids:
        cand = next(c for c in cfg["candidates"] if c["id"] == cid)
        d = out / cid
        d.mkdir()
        r = {"vram_free_before_mib": run_arm.vram_free_mib()}
        try:
            srv, ctx = run_arm.start_local_server(cand, cfg, d)
        except Exception as e:
            r["error"] = str(e)
            results[cid] = r
            continue
        try:
            r["ctx"] = ctx
            r["vram_free_loaded_mib"] = run_arm.vram_free_mib()
            j, s = post(port, {"model": cid, "messages": [{"role": "user", "content": "Reply with exactly: OK"}], "max_tokens": 64})
            r["ok_reply"] = (j["choices"][0]["message"].get("content") or "")[:40]
            r["ok_s"] = s
            j, s = post(port, {"model": cid, "max_tokens": 256, "tools": [TOOL], "tool_choice": "auto",
                               "messages": [{"role": "user", "content": "Use the read tool to read the file notes/today.md."}]})
            tc = j["choices"][0]["message"].get("tool_calls") or []
            r["tool_call"] = tc[0]["function"] if tc else None
            r["tool_s"] = s
        except Exception as e:
            r["error"] = f"{type(e).__name__}: {e}"
        finally:
            srv.terminate()
            srv.wait(30)
            time.sleep(2)
        results[cid] = r
        print(cid, json.dumps(r), flush=True)
finally:
    for f in locks:
        fcntl.flock(f, fcntl.LOCK_UN)
    (out / "summary.json").write_text(json.dumps(results, indent=1))
    with open(run_arm.QUIET_LEDGER, "a") as f:
        f.write(json.dumps({"lane": "TDAI-MODELSEL", "label": "tdai-local-route-probe", "mode": "shared",
                            "released": run_arm.now(), "rc": 0}) + "\n")
