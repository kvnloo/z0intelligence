"""Replay the 2026-10-21 incident tapes (tests/fixtures/ops_tapes) through the ops gates.

Each tape is real evidence from that day (or says which part is reconstructed).
The replay calls the same decision code the live gates call and prints, per
item, what the gate does: refused / deferred / rolled-back / flagged / allowed.
It exits non-zero if any item's outcome differs from the tape's `expect`.
Nothing here touches a phone, a service, a remote or a served worktree: the O4
items build scratch repos and never push; O2 items are decided, not executed.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from . import guard as G
from . import ops as P


def _o1(t):
    rows = []
    for it in t["items"]:
        c = P.classify_command(it["cmd"])
        if c is None:
            out = "unclassified (z0-guard run needs a declared class)"
        elif P.admits(P.Lane.GROOT, c):
            out = "allowed on groot"
        else:
            lane = P.place(c, t["mbp_room"], t["phone_room"])
            out = f"refused on groot -> {lane.value if lane else 'wait'}"
        rows.append({"at": it["at"], "class": c.value if c else None, "outcome": out, "cmd": it["cmd"][:90]})
    heavy = [r for r in rows if r["outcome"].startswith("refused")]
    return rows, {"items": len(rows), "refused_on_groot": len(heavy),
                  "by_class": {k: sum(1 for r in heavy if r["class"] == k) for k in sorted({r["class"] for r in heavy})},
                  "ok": len(heavy) > 0}


def _reflog_op(action: str) -> P.GitOp:
    if action.startswith("commit"):
        return P.GitOp.COMMIT
    if "Fast-forward" in action:
        return P.GitOp.MERGE_FF
    if action.startswith("merge"):
        return P.GitOp.MERGE
    if action.startswith(("checkout", "reset")):
        return P.GitOp.CHECKOUT
    return P.GitOp.EDIT


def _o2(t):
    rows = []
    for it in t["items"]:
        op = _reflog_op(it["action"])
        ok = P.wt_allows(True, op, False)
        rows.append({"at": it["at"], "sha": it["sha"], "op": op.value,
                     "outcome": "allowed" if ok else "refused", "action": it["action"][:70]})
    return rows, {"items": len(rows), "refused": sum(r["outcome"] == "refused" for r in rows),
                  "ok": all(r["outcome"] == "refused" for r in rows)}


def _o3(t):
    rows, ok = [], True
    for it in t["items"]:
        q = P.Req(**it["req"])
        check = it["check_kind"] == "adb" and it["device_launch_result"].startswith("pass")
        pre = P.admission(q)
        out = pre if pre is not None else P.rollout(q, check)
        why = P.why_refused(q) if out is P.Out.REFUSED else P.why_deferred(q) if out is P.Out.DEFERRED else (
            [] if check else [f"O3b: no passing device launch check ({it['check_kind']}: {it['device_launch_result']})"])
        rows.append({"label": it["label"], "outcome": out.value, "why": why})
        ok &= out.value == it["expect"]
    return rows, {"ok": ok}


def _git(d, *a):
    return subprocess.run(["git", "-C", d, *a], capture_output=True, text=True, check=True).stdout.strip()


def _o4(t):
    rows, ok = [], True
    for it in t["items"]:
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as d:
            _git(d, "init", "-q", "-b", "main")
            _git(d, "config", "user.email", "replay@localhost")
            _git(d, "config", "user.name", "replay")
            _git(d, "remote", "add", "origin", it["origin_url"])
            if it["remote_url"] != it["origin_url"]:
                _git(d, "remote", "add", "public", it["remote_url"])
            for rel, body in it["files"].items():
                p = Path(d) / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(body)
            _git(d, "add", "-A")
            _git(d, "commit", "-qm", "replay")
            sha = _git(d, "rev-parse", "HEAD")
            remote = "public" if it["remote_url"] != it["origin_url"] else "origin"
            env_mode = os.environ.get("Z0_GUARD_O4")
            os.environ["Z0_GUARD_O4"] = "enforce"
            try:
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                        code = G.prepush(remote, it["remote_url"], f"refs/heads/main {sha} refs/heads/main {G.ZERO}\n", d)
            finally:
                if env_mode is None:
                    os.environ.pop("Z0_GUARD_O4", None)
                else:
                    os.environ["Z0_GUARD_O4"] = env_mode
        out = "refused" if code else "allowed"
        rows.append({"label": it["label"], "outcome": out})
        ok &= out == it["expect"]
    return rows, {"ok": ok}


def _o5(t):
    rows, ok = [], True
    for it in t["items"]:
        d = G.phone_decision(it["kind"], utterance=it.get("utterance"), expected_app=it.get("expected_app"),
                             foreground_app=it.get("foreground_app"), package=it.get("package"),
                             call=it.get("call", True))
        out = "allowed" if d["allowed"] else "refused"
        rows.append({"event": it["event"], "outcome": out, "reasons": d["reasons"], "actual": it["actual"]})
        ok &= out == it["expect"]
    return rows, {"ok": ok}


def _o6(t):
    rows, ok = [], True
    for it in t["items"]:
        pre, post, rm = P.parse_a11y(it["pre"]), P.parse_a11y(it["post"]), P.parse_a11y(it["removed"])
        out = "allowed" if P.a11y_ok(pre, post, rm) else "refused"
        rows.append({"label": it["label"], "outcome": out, "missing": P.a11y_missing(pre, post, rm)})
        ok &= out == it["expect"]
    return rows, {"ok": ok}


def _o7(t):
    rows, ok = [], True
    for c in t["configs"]:
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as d:
            p = Path(d) / "omp.yml"
            p.write_text(json.dumps(c["omp_excerpt"]))  # JSON is YAML
            f = G.routing_findings(p, [], Path(d) / "none.yml")
        rows.append({"config": c["label"], "outcome": "flagged" if f else "clean",
                     "findings": [f"{x['where']} -> {x['model'] or x['provider']} ({x['why']})" for x in f]})
        ok &= bool(f) == (c["expect"] == "flagged")
    for it in t["items"]:
        cancelled, sol = P.provider_flags(None, it["model"])
        out = "allowed" if P.route_ok(cancelled, sol, P.design_role(it["role"])) else "refused"
        rows.append({"label": it["label"], "outcome": out})
        ok &= out == it["expect"]
    return rows, {"ok": ok}


def _o8(t):
    st, events, strikes_after_limit = {}, [], 0
    lim = t["limit_s"]
    for it in t["items"]:
        st, ev = G.health_step(st, t["node"], it["healthy"], it["ts"], lim, deliver=lambda _: {"delivered": False},
                               gate_mode="report")
        if ev:
            events.append({"at_ts": it["ts"], "text": ev["text"]})
        cur = st[t["node"]]
        if not it["healthy"] and cur["kind"] != "healthy" and it["ts"] > cur["since"] + lim:
            strikes_after_limit += 1
    actual_notifications = sum(1 for it in t["items"] if it.get("action") not in (None, "none", "paused"))
    rows = [{"notification": e} for e in events]
    return rows, {"probes": len(t["items"]), "bad_probes": sum(not i["healthy"] for i in t["items"]),
                  "gate_notifications": len(events), "strikes_past_limit": strikes_after_limit,
                  "fleet_notifications_that_day": actual_notifications, "ok": len(events) == 1}


def _o9(t):
    recs = G.interrupted_records(t["before"], t["after"]["units"], {k: t["after"][k] for k in
                                 ("gateway_invocation", "hermes_head")}, t["restart_at"], has_report=lambda d: False)
    rows = [{"worker": r["worker"], "outcome": r["action"], "resume": (r["resume"] or "")[:120]} for r in recs]
    return rows, {"interrupted": len(recs), "failed": sum(r["action"] == "failed" for r in recs),
                  "ok": len(recs) == len(t["before"]["units"]) and all(r["action"] == "interrupted" for r in recs)}


def _o16(t):
    from .sqlite_guard import sqlite_decision
    rows, ok = [], True
    for item in t["items"]:
        d = sqlite_decision(**item["inputs"])
        laws = [v["law"] for v in d["violations"]]
        rows.append({"label": item["label"], "outcome": "flagged" if laws else "allowed", "laws": laws})
        ok &= laws == item["expect"]
    return rows, {"items": len(rows), "ok": ok}


KINDS = {"commands": _o1, "reflog": _o2, "rollout": _o3, "push": _o4, "phone": _o5, "a11y": _o6,
         "routing": _o7, "health": _o8, "gateway": _o9, "sqlite-store": _o16}


def replay(path: str) -> dict:
    t = json.loads(Path(path).read_text())
    rows, summary = KINDS[t["kind"]](t)
    return {"tape": Path(path).name, "law": t["law"], "incident": t["incident"], "summary": summary, "rows": rows}


def main(paths: list[str], out: str | None = None) -> int:
    ok, full = True, []
    for p in paths:
        r = replay(p)
        full.append(r)
        ok &= bool(r["summary"].get("ok"))
        print(f"{r['law']}  {r['tape']}\n    {json.dumps(r['summary'])}")
        for row in r["rows"][:40] if r["law"] != "O1" else [x for x in r["rows"] if not x["outcome"].startswith("refused")][:12]:
            print("    - " + json.dumps(row)[:230])
    if out:
        Path(out).write_text(json.dumps(full, indent=1) + "\n")
    print("ALL INCIDENTS REFUSED OR FLAGGED AS EXPECTED" if ok else "SOME TAPES DID NOT MATCH THEIR EXPECTATION")
    return 0 if ok else 1


if __name__ == "__main__":
    args = sys.argv[1:]
    out = None
    if "--out" in args:
        i = args.index("--out")
        out = args[i + 1]
        del args[i:i + 2]
    raise SystemExit(main(args, out))
