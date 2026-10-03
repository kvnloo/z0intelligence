"""Parity gate (no timing): every Bend configuration must give byte-identical verdicts.

1. Reference = configuration B exactly as the README measured it: BendGate adapter,
   native `bend main.bend -o gate` binary, --threads 1, persistent pipe, one line per call.
   Its verdicts are scored against canonical validate() on all 22,079 cases with the
   frozen harness rules (false allow / false deny / issue-code multisets).
2. Each other configuration is run over the same 10,019 kernel-routed request lines and
   its reply lines must equal the reference replies exactly.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import Counter

from common import BIN, GATE_WT, ROOT, WORK, bg, load

BEND = "/mnt/zer0models/bend-stack/official/bend/bin/bend"
ENV = dict(os.environ, BEND_NO_TELEMETRY="1")
C = load()
rows, lines = C["rows"], C["lines"]
LINES_FILE = WORK / "kernel_lines.txt"


def ref_replies() -> list[bytes]:
    out = []
    with bg.BendGate(BIN / "gate_ref", timeout_s=30.0, threads=1) as g:
        for ln in lines:
            ok, codes = g.raw(ln.decode().split())
            out.append(b"ALLOW" if ok else ("DENY " + " ".join(map(str, codes))).encode())
    return out


def batch(cmd, cwd=None) -> list[bytes]:
    with open(LINES_FILE, "rb") as f:
        r = subprocess.run(cmd, stdin=f, capture_output=True, cwd=cwd, env=ENV, timeout=1800)
    if r.returncode != 0:
        raise RuntimeError(f"{cmd} rc={r.returncode} {r.stderr[-500:]!r}")
    return r.stdout.splitlines()


ref = ref_replies()
# score the reference against canonical validate()
fa = fd = dis = dis_unexpl = codes_cmp = codes_dis = 0
for r in rows:
    if r["route"] == "kernel":
        rep = ref[r["line_index"]].decode().split()
        b_ok = rep == ["ALLOW"]
        b_codes = Counter(bg.RULE_CODES.get(int(c), f"?{c}") for c in rep[1:])
    else:
        b_ok, b_codes = False, None
    if b_ok and not r["python_ok"]:
        fa += 1
    if r["python_ok"] and not b_ok:
        fd += 1
    if b_ok != r["python_ok"]:
        dis += 1
        if r["route"] != "unsupported":
            dis_unexpl += 1
    if r["route"] == "kernel" and r["python_exception"] is None:
        codes_cmp += 1
        codes_dis += Counter(r["python_codes"]) != b_codes
reference = {"config": "B: BendGate adapter, gate_ref, --threads 1, persistent pipe", "cases": len(rows),
             "kernel_routed": len(lines), "false_allow": fa, "false_deny": fd, "verdict_disagree": dis,
             "verdict_disagree_unexplained": dis_unexpl, "codes_compared": codes_cmp, "codes_disagree": codes_dis,
             "allow_replies": sum(x == b"ALLOW" for x in ref)}
print(json.dumps(reference), flush=True)
(WORK / "ref_replies.txt").write_bytes(b"\n".join(ref) + b"\n")

configs = {
    "batch_ref_t1": [str(BIN / "gate_ref"), "--threads", "1"],
    "batch_ref_default_threads": [str(BIN / "gate_ref")],
    "batch_ref_native_t1": [str(BIN / "gate_ref_native"), "--threads", "1"],
    "batch_par_t1": [str(BIN / "gate_par"), "--threads", "1"],
    "batch_par_t10": [str(BIN / "gate_par"), "--threads", "10"],
    "batch_par_default_threads": [str(BIN / "gate_par")],
}
if "--js" in sys.argv:
    configs["batch_js_run_mode"] = [BEND, "main.bend"]
variants = {}
for name, cmd in configs.items():
    cwd = str(GATE_WT / "bend" / "aodl_gate") if name == "batch_js_run_mode" else None
    try:
        got = batch(cmd, cwd)
        ok = got == ref
        variants[name] = {"cmd": cmd, "replies": len(got), "identical_to_reference": ok,
                          "first_mismatch": None if ok else next((i for i, (a, b) in enumerate(zip(got, ref)) if a != b), min(len(got), len(ref)))}
    except Exception as exc:  # noqa: BLE001
        variants[name] = {"cmd": cmd, "error": repr(exc)[:400], "identical_to_reference": False}
    print(name, json.dumps(variants[name]), flush=True)
# pipe-mode single-request parity for the parallel kernel (one line per write)
for name, binary in (("pipe_par_t1", BIN / "gate_par"), ("pipe_ref_native_t1", BIN / "gate_ref_native")):
    got = []
    with bg.BendGate(binary, timeout_s=30.0, threads=1) as g:
        for ln in lines:
            ok, codes = g.raw(ln.decode().split())
            got.append(b"ALLOW" if ok else ("DENY " + " ".join(map(str, codes))).encode())
    variants[name] = {"identical_to_reference": got == ref, "replies": len(got)}
    print(name, json.dumps(variants[name]), flush=True)
out = {"corpus_sha256": C["sha"], "reference": reference, "variants": variants,
       "binaries": {p.name: subprocess.run(["sha256sum", str(p)], capture_output=True, text=True).stdout.split()[0]
                    for p in sorted(BIN.iterdir()) if p.is_file()}}
(ROOT / "results" / "parity.json").write_text(json.dumps(out, indent=1) + "\n")
