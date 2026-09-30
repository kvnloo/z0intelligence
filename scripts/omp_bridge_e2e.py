"""End-to-end proof that the z0int bridge works inside a real ``omp`` binary.

Everything runs in a sandbox: ``HOME``, the OMP agent dir and ``Z0INT_HOME`` all
point under ``--sandbox`` (default ``~/.cache/omp-bridge-sandbox``), and the
model is a local deterministic OpenAI-compatible server
(``scripts/omp_bridge_fake_llm.py``). No real credentials are read and the
live ``~/.omp`` / ``~/.z0int`` are never touched.

Phases:
  1. baseline   ``omp -p`` N times without the extension (wall time)
  2. install    ``scripts/omp_bridge_install.py install`` into the sandbox agent dir
  3. bridged    ``omp -p`` N times with the extension (wall time + receipts)
  4. rpc        two concurrent RPC sessions A and B. A: prompt,
                /z0int-bridge-status, /z0int-bridge-reload (A -> generation 2).
                Then B (still generation 1) prompts: its writes must NOT be
                quarantined as stale by A's generation pointer.
  5. uninstall  link removed; one more ``omp -p`` produces no new receipts.

    python scripts/omp_bridge_e2e.py [--runs 5] [--sandbox DIR] [--omp omp]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import omp_bridge_fake_llm as fake  # noqa: E402
import omp_bridge_install as installer  # noqa: E402

MODEL = "sandbox/bridge-fake"


def models_yml(port: int) -> str:
    return f"""providers:
  sandbox:
    baseUrl: http://127.0.0.1:{port}/v1
    apiKey: SANDBOX_FAKE_KEY
    api: openai-completions
    models:
      - id: {fake.MODEL_ID}
        name: Bridge Fake
        reasoning: false
        input: [text]
        contextWindow: 32000
        maxTokens: 1024
        cost: {{input: 0, output: 0, cacheRead: 0, cacheWrite: 0}}
"""


def sandbox_env(sb: Path) -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("PI_", "OMP_", "Z0INT_", "KERDOIOS", "EVOLUTION_LAB"))
    }
    env.update(
        HOME=str(sb / "home"),
        Z0INT_HOME=str(sb / "z0home"),
        SANDBOX_FAKE_KEY="sandbox-not-a-secret",
        XDG_CACHE_HOME=str(sb / "home" / ".cache"),
        XDG_STATE_HOME=str(sb / "home" / ".local" / "state"),
        XDG_DATA_HOME=str(sb / "home" / ".local" / "share"),
        XDG_CONFIG_HOME=str(sb / "home" / ".config"),
    )
    venv_py = ROOT / ".venv" / "bin" / "python"
    if venv_py.exists():
        env["Z0INT_PYTHON"] = str(venv_py)
    return env


def run_print(omp: str, sb: Path, env: dict[str, str], prompt: str) -> dict[str, Any]:
    t0 = time.perf_counter()
    proc = subprocess.run(
        [omp, "-p", "--model", MODEL, prompt],
        cwd=sb / "work",
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=180,
    )
    return {
        "wall_ms": (time.perf_counter() - t0) * 1000.0,
        "rc": proc.returncode,
        "stdout": proc.stdout.strip()[-200:],
        "stderr_tail": proc.stderr.strip()[-400:],
    }


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


class Rpc:
    def __init__(self, omp: str, sb: Path, env: dict[str, str]) -> None:
        self.proc = subprocess.Popen(
            [omp, "--mode", "rpc", "--model", MODEL],
            cwd=sb / "work",
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        self.frames: list[dict[str, Any]] = []
        self._lock = threading.Condition()
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        assert self.proc.stdout
        for line in self.proc.stdout:
            try:
                frame = json.loads(line)
            except json.JSONDecodeError:
                continue
            with self._lock:
                self.frames.append(frame)
                self._lock.notify_all()

    def wait(self, pred, timeout: float = 90.0, start: int = 0) -> dict[str, Any]:
        deadline = time.time() + timeout
        with self._lock:
            seen = start
            while True:
                for f in self.frames[seen:]:
                    if pred(f):
                        return f
                seen = len(self.frames)
                left = deadline - time.time()
                if left <= 0:
                    raise TimeoutError("rpc wait timed out")
                self._lock.wait(left)

    def mark(self) -> int:
        with self._lock:
            return len(self.frames)

    def send(self, obj: dict[str, Any]) -> None:
        assert self.proc.stdin
        self.proc.stdin.write(json.dumps(obj) + "\n")
        self.proc.stdin.flush()

    def notifies_since(self, mark: int) -> list[str]:
        with self._lock:
            out = []
            for f in self.frames[mark:]:
                if f.get("type") == "extension_ui_request" and f.get("method") == "notify":
                    out.append(str(f.get("message")))
            return out

    def close(self) -> int:
        try:
            assert self.proc.stdin
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            return self.proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            return -9


def _cmd(rpc: Rpc, cmd: str) -> list[str]:
    idx = rpc.mark()
    rpc.send({"id": cmd, "type": "prompt", "message": cmd})
    rpc.wait(lambda f: f.get("type") == "extension_ui_request" and f.get("method") == "notify", 60, idx)
    return rpc.notifies_since(idx)


def _turn(rpc: Rpc, text: str) -> None:
    idx = rpc.mark()
    rpc.send({"id": text[:20], "type": "prompt", "message": text})
    rpc.wait(lambda f: f.get("type") == "agent_end", 120, idx)
    time.sleep(0.5)  # let the agent_end handler flush turn_close


def rpc_turn_times(omp: str, sb: Path, env: dict[str, str], n: int) -> list[float]:
    """Per-turn latency inside one long-lived session (prompt sent -> agent_end)."""
    rpc = Rpc(omp, sb, env)
    times: list[float] = []
    try:
        rpc.wait(lambda f: f.get("type") == "ready", 60)
        _turn(rpc, "warm turn")
        for i in range(n):
            idx = rpc.mark()
            t0 = time.perf_counter()
            rpc.send({"id": f"t{i}", "type": "prompt", "message": f"timed turn {i}: refactor the parser"})
            rpc.wait(lambda f: f.get("type") == "agent_end", 120, idx)
            times.append((time.perf_counter() - t0) * 1000.0)
            time.sleep(0.3)
    finally:
        rpc.close()
    return times


def rpc_phase(omp: str, sb: Path, env: dict[str, str]) -> dict[str, Any]:
    """Two concurrent OMP sessions. B is already running when A hot-reloads."""
    a = Rpc(omp, sb, env)
    b = Rpc(omp, sb, env)
    out: dict[str, Any] = {}
    try:
        a.wait(lambda f: f.get("type") == "ready", 60)
        b.wait(lambda f: f.get("type") == "ready", 60)
        _turn(a, "session A: first turn")
        out["b_status_before"] = _cmd(b, "/z0int-bridge-status")  # B's worker is at generation 1
        out["/z0int-bridge-status"] = _cmd(a, "/z0int-bridge-status")
        out["/z0int-bridge-reload"] = _cmd(a, "/z0int-bridge-reload")  # A -> generation 2
        out["/z0int-bridge-status#2"] = _cmd(a, "/z0int-bridge-status")
        _turn(b, "session B: turn after A reloaded")  # B still at generation 1
        _turn(a, "session A: turn after reload")
    finally:
        out["rc_a"] = a.close()
        out["rc_b"] = b.close()
    return out


def live_fingerprint() -> dict[str, Any]:
    """Cheap evidence that the live OMP/z0int homes were not written by this run."""
    home = Path(os.environ.get("HOME", str(Path.home())))
    ext = home / ".omp" / "agent" / "extensions"
    out: dict[str, Any] = {"live_extensions": sorted(p.name for p in ext.iterdir()) if ext.is_dir() else None}
    for rel in ("stream/bridge.jsonl", "stream/bridge_heart.jsonl", "runtime/bridge-current.json"):
        f = home / ".z0int" / rel
        out[rel] = f.stat().st_size if f.exists() else None
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sandbox", type=Path, default=Path.home() / ".cache" / "omp-bridge-sandbox")
    ap.add_argument("--omp", default=shutil.which("omp") or "omp")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument(
        "--ext-root",
        type=Path,
        default=ROOT,
        help="z0intelligence checkout to install the extension from (default: this one)",
    )
    args = ap.parse_args(argv)

    sb: Path = args.sandbox.expanduser().resolve()
    live_home = Path.home().resolve()
    if sb == live_home or sb in (live_home / ".omp", live_home / ".z0int") or not str(sb).startswith(str(live_home)):
        raise SystemExit(f"refusing sandbox path {sb}")
    if sb.exists():
        shutil.rmtree(sb)
    agent = sb / "home" / ".omp" / "agent"
    for d in (agent, sb / "work", sb / "z0home"):
        d.mkdir(parents=True, exist_ok=True)

    httpd = fake.serve(0)
    port = httpd.server_address[1]
    (agent / "models.yml").write_text(models_yml(port), encoding="utf-8")
    env = sandbox_env(sb)
    z0 = sb / "z0home"
    version = subprocess.run([args.omp, "--version"], capture_output=True, text=True, env=env).stdout.strip()

    live_before = live_fingerprint()
    summary: dict[str, Any] = {"omp": args.omp, "omp_version": version, "sandbox": str(sb), "fake_port": port}

    # warm-up (first run extracts natives / builds caches)
    run_print(args.omp, sb, env, "warm up")
    baseline = [run_print(args.omp, sb, env, f"baseline {i}") for i in range(args.runs)]
    baseline_turns = rpc_turn_times(args.omp, sb, env, args.runs * 2)
    summary["install"] = installer.install(agent, args.ext_root.expanduser().resolve())
    run_print(args.omp, sb, env, "warm up bridged")
    bridged = [run_print(args.omp, sb, env, f"bridged turn {i}: refactor the parser") for i in range(args.runs)]
    bridged_turns = rpc_turn_times(args.omp, sb, env, args.runs * 2)
    summary["rpc"] = rpc_phase(args.omp, sb, env)
    summary["uninstall"] = installer.uninstall(agent)
    before = len(read_jsonl(z0 / "receipts" / "decisions.jsonl"))
    after_uninstall = run_print(args.omp, sb, env, "after uninstall")
    after_rows = len(read_jsonl(z0 / "receipts" / "decisions.jsonl"))

    bridge_rows = read_jsonl(z0 / "stream" / "bridge.jsonl")
    hearts = read_jsonl(z0 / "stream" / "bridge_heart.jsonl")
    quarantine = read_jsonl(z0 / "stream" / "bridge_quarantine.jsonl")
    opened = {r.get("trace_id") for r in bridge_rows}
    closed = {h.get("trace_id") for h in hearts if h.get("ok")}
    b_rows = [r for r in bridge_rows if str(r.get("prompt", "")).startswith("session B")]
    b_traces = {r.get("trace_id") for r in b_rows}
    open_lat = [float(r["latency_ms"]) for r in bridge_rows if isinstance(r.get("latency_ms"), (int, float))]

    def med(rows: list[dict[str, Any]]) -> float:
        return round(statistics.median(r["wall_ms"] for r in rows), 1)

    checks = {
        "all_runs_rc0": all(r["rc"] == 0 for r in baseline + bridged + [after_uninstall]),
        "fake_reply_seen": all(fake.REPLY in r["stdout"] for r in bridged),
        "one_open_per_bridged_prompt": len(opened) >= args.runs + 1 + len(bridged_turns),
        "every_open_closed": opened <= closed,
        "provider_usage_complete": all(h.get("measurement_state") == "complete" for h in hearts if h.get("ok")),
        "session_id_from_ompsession": all(not str(r.get("omp_session_id", "")).startswith("omp-") for r in bridge_rows),
        "reload_went_to_gen2": any("generation 2" in m for m in summary["rpc"].get("/z0int-bridge-reload", [])),
        "session_b_opened": bool(b_traces),
        "session_b_not_quarantined": not any(
            q.get("trace_id") in b_traces or str(q.get("prompt", "")).startswith("session B") for q in quarantine
        ),
        "session_b_closed": bool(b_traces) and b_traces <= closed,
        "no_receipts_after_uninstall": after_rows == before,
        "live_homes_untouched": live_fingerprint() == live_before,
    }
    summary.update(
        checks=checks,
        ok=all(checks.values()),
        counts={
            "bridge_open_rows": len(bridge_rows),
            "hearts": len(hearts),
            "quarantine_rows": len(quarantine),
            "decision_receipt_rows": after_rows,
        },
        overhead={
            "runs": args.runs,
            "baseline_median_ms": med(baseline),
            "bridged_median_ms": med(bridged),
            "delta_median_ms": round(med(bridged) - med(baseline), 1),
            "baseline_ms": [round(r["wall_ms"], 1) for r in baseline],
            "bridged_ms": [round(r["wall_ms"], 1) for r in bridged],
            "rpc_turn_baseline_median_ms": round(statistics.median(baseline_turns), 1),
            "rpc_turn_bridged_median_ms": round(statistics.median(bridged_turns), 1),
            "rpc_turn_delta_median_ms": round(statistics.median(bridged_turns) - statistics.median(baseline_turns), 1),
            "turn_open_latency_ms_median": round(statistics.median(open_lat), 2) if open_lat else None,
            "turn_open_latency_ms_max": round(max(open_lat), 2) if open_lat else None,
        },
        sample_receipt=next((r.get("receipt") for r in bridge_rows if r.get("receipt")), None),
        sample_heart=hearts[0] if hearts else None,
    )
    httpd.shutdown()
    (sb / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
