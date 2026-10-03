#!/usr/bin/env python3
"""Run one benchmark cell: candidate x conversation set x repeat.

  run_arm.py --cand qwen3-8b-q4km --convs S --repeat 1 --run-id R0
  run_arm.py --cand nemotron-super-free --convs A01_lantern_pnpm,F01_env_paste --repeat 1 --run-id smoke
  run_arm.py --cand stub --convs ... --stub      # harness wiring test, no model

Layout: runs/<run-id>/<cand>/r<k>/{job.json, manifest.json, shim.log, shim-ledger.jsonl, calls/, <conv>/result.json}
L2/L3 run only for conversations in subset L23 (paid arms: P_L23), unless --layers l1.
Local candidates: a dedicated llama-server is started on 127.0.0.1:11590 under
`flock -s quiet-lane.lock` + `flock gpu.lock` (Quackles GPU convention) and stopped afterwards.
"""
import argparse
import fcntl
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BENCH = Path(__file__).resolve().parent
ROOT = BENCH.parent                      # model-select/
OPS = ROOT.parent                        # ops/tencentdb/
IMAGE = "sha256:55fec3a6067af7cc4dbb48017f590392cf0085f378b6bcac340a91690a9707ed"
QUIET_LOCK = "/mnt/zer0models/cua-lane-tmp/locks/quiet-lane.lock"
QUIET_LEDGER = "/mnt/zer0models/cua-lane-tmp/locks/quiet-lane-ledger.jsonl"
GPU_LOCK = "/tmp/claude-1000/gpu.lock"
BUDGET = ROOT / "runs" / "paid-budget.json"
CAP_USD = 4.75   # hard total paid cap (task cap $5; Vercel balance $4.9995)


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def wait_http(url, timeout=600, proc=None):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if proc is not None and proc.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(1)
    return False


def vram_free_mib():
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10).stdout.split()
        return int(out[0])
    except Exception:
        return -1


def resolve_convs(spec, corpus):
    if spec in corpus["subsets"]:
        return list(corpus["subsets"][spec])
    if spec == "ALL":
        return [c["id"] for c in corpus["conversations"]]
    return [s for s in spec.split(",") if s]


def start_local_server(cand, cfg, outdir):
    ls = cfg["local_server"]
    log = open(outdir / "llama-server.log", "w")
    for ctx in ls["ctx_ladder"]:
        args = [ls["binary"], "-m", cand["gguf"], "--alias", cand["id"], "-c", str(ctx),
                "--host", ls["host"], "--port", str(ls["port"])] + ls["common_args"] + cand.get("server_args", [])
        log.write(f"# {now()} start ctx={ctx}: {' '.join(args)}\n")
        log.flush()
        p = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT)
        if wait_http(f"http://{ls['host']}:{ls['port']}/health", timeout=300, proc=p):
            return p, ctx
        p.terminate()
        try:
            p.wait(30)
        except subprocess.TimeoutExpired:
            p.kill()
        log.write(f"# ctx={ctx} failed to come up (rc={p.returncode})\n")
    raise RuntimeError("local server failed at every ctx in the ladder")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cand", required=True)
    ap.add_argument("--convs", required=True)
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--layers", default="auto", choices=["auto", "l1"])
    ap.add_argument("--stub", action="store_true")
    ap.add_argument("--force", action="store_true", help="delete an existing cell dir first")
    a = ap.parse_args()

    cfg = json.loads((BENCH / "candidates.json").read_text())
    corpus_path = BENCH / "corpus" / "corpus.json"
    corpus = json.loads(corpus_path.read_text())
    corpus_sha = hashlib.sha256(corpus_path.read_bytes()).hexdigest()
    cand = {"id": "stub", "kind": "stub", "callable": True} if a.stub else next(c for c in cfg["candidates"] if c["id"] == a.cand)
    if not cand.get("callable") or cand.get("why_not"):
        sys.exit(f"candidate {cand['id']} not runnable: {cand.get('why_not')}")
    convs = resolve_convs(a.convs, corpus)
    l23 = set(corpus["subsets"]["P_L23" if cand.get("paid") else "L23"])
    layers = {c: ("l1l2l3" if (a.layers == "auto" and c in l23) else "l1") for c in convs}

    outdir = ROOT / "runs" / a.run_id / cand["id"] / f"r{a.repeat}"
    if outdir.exists():
        if not a.force:
            sys.exit(f"{outdir} exists (use --force to redo the cell)")
        import shutil
        shutil.rmtree(outdir)
    outdir.mkdir(parents=True)
    run_tag = f"{a.run_id}-{cand['id']}-r{a.repeat}".replace(".", "_")
    job = {"corpus": "/bench-corpus/corpus.json", "convIds": convs, "outDir": "/out", "layers": layers, "runTag": run_tag}
    (outdir / "job.json").write_text(json.dumps(job, indent=1))
    manifest = {"cand": cand, "convs": convs, "layers": layers, "repeat": a.repeat, "run_id": a.run_id,
                "corpus_sha256": corpus_sha, "image": IMAGE, "started": now(), "host": socket.gethostname()}
    try:
        manifest["bench_git_head"] = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    except Exception:
        pass

    locks = []
    server = None
    shim = None
    ledger_label = None
    try:
        if cand["kind"] == "local":
            for path, mode in ((QUIET_LOCK, fcntl.LOCK_SH), (GPU_LOCK, fcntl.LOCK_EX)):
                f = open(path, "a")
                print(f"[run_arm] waiting for {'shared' if mode == fcntl.LOCK_SH else 'exclusive'} lock {path}", flush=True)
                fcntl.flock(f, mode)
                locks.append(f)
            ledger_label = {"lane": "TDAI-MODELSEL", "label": f"tdai-{run_tag}", "mode": "shared", "pid": os.getpid(),
                            "acquired": now(), "loadavg_at_acquire": " ".join(f"{x:.2f}" for x in os.getloadavg())}
            manifest["vram_free_mib_before"] = vram_free_mib()
            server, ctx = start_local_server(cand, cfg, outdir)
            manifest["local_ctx"] = ctx
            ls = cfg["local_server"]
            upstream, model, key_env, inject, paid = f"http://{ls['host']}:{ls['port']}/v1", cand["id"], "", {}, False
            price = {"in": 0, "out": 0}
            min_iv = 0.0
        elif cand["kind"] == "stub":
            upstream, model, key_env, inject, paid, price, min_iv = "", "stub", "", {}, False, {"in": 0, "out": 0}, 0.0
        else:
            upstream, model, key_env, inject = cand["endpoint"], cand["model"], cand["key_env"], cand["inject"]
            paid, price, min_iv = cand["paid"], cand["price"], cand.get("min_interval_s", 0.0)

        port = free_port()
        shim_cmd = [sys.executable, str(BENCH / "harness" / "shim.py"), "--port", str(port), "--upstream", upstream,
                    "--model", model, "--inject", json.dumps(inject), "--price-in", str(price["in"]),
                    "--price-out", str(price["out"]), "--ledger", str(outdir / "shim-ledger.jsonl"),
                    "--calls-dir", str(outdir / "calls"), "--arm", cand["id"], "--min-interval-s", str(min_iv)]
        if "cache_read" in price:
            shim_cmd += ["--price-cache-read", str(price["cache_read"])]
        if paid:
            shim_cmd += ["--paid", "--budget-file", str(BUDGET), "--cap-usd", str(CAP_USD),
                         "--arm-cap-usd", str(cand.get("arm_cap_usd", 0))]
        if a.stub:
            shim_cmd.append("--stub")
        if key_env:
            # key fetched from BWS into the shim's env only (never argv, never logged)
            shim_cmd = [str(BENCH / "bin" / "with-secrets"), key_env, "--"] + shim_cmd + ["--key-env", key_env]
        shim_log = open(outdir / "shim.log", "w")
        shim = subprocess.Popen(shim_cmd, stdout=shim_log, stderr=subprocess.STDOUT)
        if not wait_http(f"http://127.0.0.1:{port}/v1/models", timeout=60, proc=shim):
            raise RuntimeError("shim did not come up (see shim.log)")

        cname = f"tdai-bench-{run_tag}"[:60]
        docker = ["docker", "run", "--rm", "--pull", "never", "--name", cname, "--network", "host",
                  "--log-driver", "none", "--memory", "2g", "--cpus", "2",
                  "--user", f"{os.getuid()}:{os.getgid()}", "-e", "HOME=/tmp",
                  "-e", "TDAI_GATEWAY_CONFIG=/cfg/tdai-gateway.yaml",
                  "-e", f"TDAI_LLM_BASE_URL=http://127.0.0.1:{port}/v1",
                  "-e", f"TDAI_LLM_MODEL={model}",
                  "-e", "TDAI_LLM_API_KEY=shim-holds-the-real-key",
                  "-v", f"{BENCH / 'harness'}:/app/bench:ro",
                  "-v", f"{BENCH / 'corpus'}:/bench-corpus:ro",
                  "-v", f"{OPS / 'tdai-gateway.yaml'}:/cfg/tdai-gateway.yaml:ro",
                  "-v", f"{outdir}:/out",
                  "--entrypoint", "/usr/bin/tini", IMAGE, "--",
                  "node", "--import", "tsx", "/app/bench/driver.ts", "/out/job.json"]
        t0 = time.time()
        with open(outdir / "driver.stdout", "w") as dl:
            rc = subprocess.run(docker, stdout=dl, stderr=subprocess.STDOUT).returncode
        manifest.update(driver_rc=rc, driver_wall_s=round(time.time() - t0, 1))
    finally:
        if shim:
            shim.terminate()
            try:
                shim.wait(10)
            except subprocess.TimeoutExpired:
                shim.kill()
        if server:
            server.terminate()
            try:
                server.wait(30)
            except subprocess.TimeoutExpired:
                server.kill()
        if ledger_label:
            ledger_label.update(released=now(), rc=manifest.get("driver_rc"))
            try:
                with open(QUIET_LEDGER, "a") as f:
                    f.write(json.dumps(ledger_label) + "\n")
            except Exception:
                pass
        for f in locks:
            fcntl.flock(f, fcntl.LOCK_UN)
            f.close()
        manifest["finished"] = now()
        if BUDGET.exists():
            manifest["paid_budget_after"] = json.loads(BUDGET.read_text() or "{}")
        (outdir / "manifest.json").write_text(json.dumps(manifest, indent=1))
    print(f"[run_arm] {cand['id']} r{a.repeat} rc={manifest.get('driver_rc')} wall={manifest.get('driver_wall_s')}s -> {outdir}")


if __name__ == "__main__":
    main()
