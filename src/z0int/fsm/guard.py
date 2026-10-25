"""z0-guard: the ops-plane gates that real tools call.

Every decision goes through z0int.fsm.ops (the Python twin of
bend/ops_fsm/ops.bend, parity-checked against Bend). This module only gathers
the inputs (git, systemd, adb, broker.db, configs, gh) and acts on the verdict.

    z0-guard run CLASS -- CMD...        O1  heavy classes go to fleet-build; groot runs only its classes
    z0-guard place CLASS --mbp-room 1 --phone-room 0                      O1  pure decision
    z0-guard served mark|unmark|status|install-hooks|uninstall-hooks ...  O2  served-worktree lease + hooks
    z0-guard hook NAME [ARGS]           O2/O4 git hook entry (installed shims call this)
    z0-guard rollout ...                O2+O3 merge --ff-only, idle check, restart, launch check, rollback
    z0-guard prepush REMOTE URL         O4  (stdin: git pre-push lines)
    z0-guard phone --kind tap ...       O5  decision for the owner's phone
    z0-guard a11y --pre S --post S      O6
    z0-guard routing                    O7  provider routing checker (read-only, never prints keys)
    z0-guard health-step ...            O8  one node-health probe through the notify machine
    z0-guard gateway-watch              O9  gateway restart -> Interrupted runs with resume commands
    z0-guard phone-caps --root ROOT [--path PATH ...] O10 source-policy scanner
    z0-guard policy --receipts GLOB --policy PATH --since 24h [--static ROOT] [--json] O11/O12/O13 audit
    z0-guard state [--config PATH | --input PATH] A–E read-only state audit; never activates blocking
    z0-guard mitigate ...               R1/R3 report-only mitigation lease lifecycle
    z0-guard incident ...               R2 two-observation incident closure
    z0-guard attribute ...              R4 same-window pressure attribution
    z0-guard canary ...                 ctx/AgentsView read-only health observations
    z0-guard report-expiry              R7 expired report-only findings and incidents
    z0-guard replay TAPE...             replay incident tapes through the gates
    z0-guard modes                      show each gate's mode

Modes: off | report | enforce. Default enforce for O1, O2, O4, O6, O10 (pure refusals
of known-bad actions); report for O3, O5, O7, O8, O9. Override per gate with
Z0_GUARD_O<n>=MODE or ~/.config/z0-guard/modes.json. A gate's verdict is never
an authorization: a pass means only "this gate did not refuse".
"""
from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import hashlib
import json
import os
import re
import secrets
import shlex
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import ops as P
from .phone_caps import phone_caps_findings, phone_caps_paths

HOME = Path.home()
CONFIG = Path(os.environ.get("Z0_GUARD_CONFIG", HOME / ".config/z0-guard"))
STATE = Path(os.environ.get("Z0_GUARD_STATE", HOME / ".local/state/z0-guard"))
CACHE = Path(os.environ.get("Z0_GUARD_CACHE", HOME / ".cache/z0-guard"))
BROKER_DB = Path(os.environ.get("Z0LIVE_BROKER_DB", "/mnt/zer0models/workspace/zer0/data/z0live/broker.db"))
SOL_RUNS = Path(os.environ.get("Z0_SOL_RUNS", "/mnt/zer0models/workspace/zer0/data/operations/sol-runs"))
DECISIONS = Path(os.environ.get("Z0_WORKER_DECISIONS",
                                "/mnt/zer0models/workspace/zer0/data/operations/fsm/worker-decisions.jsonl"))
HERMES_CHECKOUT = Path(os.environ.get("Z0_HERMES_CHECKOUT", "/workspace/hermes-home/hermes-agent"))
GROOT_NAMES = {"groot"}
CALL_CHAIN_PACKAGES = {"app.lawnchair.play.debug"}
DEFAULT_MODES = {"O1": "enforce", "O2": "enforce", "O3": "report", "O4": "enforce", "O5": "report",
                 "O6": "enforce", "O7": "report", "O8": "report", "O9": "report", "O10": "enforce"}
EXIT_REFUSED = 3
ZERO = "0" * 40


# ------------------------------------------------------------------ plumbing

def mode(gate: str) -> str:
    env = os.environ.get(f"Z0_GUARD_{gate}")
    if env in ("off", "report", "enforce"):
        return env
    try:
        m = json.loads((CONFIG / "modes.json").read_text()).get(gate)
        if m in ("off", "report", "enforce"):
            return m
    except (OSError, ValueError):
        pass
    return DEFAULT_MODES[gate]


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def ledger(rec: dict, name: str = "decisions.jsonl") -> None:
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        with open(STATE / name, "a") as f:
            f.write(json.dumps({"at": now_iso(), **rec}) + "\n")
    except OSError:
        pass


def emit(rec: dict) -> None:
    print(json.dumps(rec, sort_keys=True))


def verdict(gate: str, refused: bool, rec: dict, *, quiet: bool = False) -> int:
    """Apply the gate's mode to a decision; log it; return the exit code."""
    m = mode(gate)
    status = "ok" if not refused else {"enforce": "refused", "report": "would-refuse", "off": "off"}[m]
    out = {"gate": gate, "mode": m, "status": status, **rec}
    ledger(out)
    if not quiet:
        emit(out)
    return EXIT_REFUSED if refused and m == "enforce" else 0


def sh(*args: str, cwd: str | None = None, check: bool = False, timeout: int = 60, env: dict | None = None,
       inp: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(list(args), cwd=cwd, capture_output=True, text=True, check=check, timeout=timeout,
                          env=env, input=inp)


def git(wt: str, *args: str, env: dict | None = None, check: bool = True) -> str:
    return sh("git", "-C", wt, *args, env=env, check=check).stdout.strip()


def on_groot() -> bool:
    return os.environ.get("Z0_GUARD_HOST", socket.gethostname()) in GROOT_NAMES


# ------------------------------------------------------------------ O1

FLEET_FLAGS = {P.Cls.GRADLE: ["--android"], P.Cls.TYPECHECK: ["--light"]}


def cmd_place(a) -> int:
    c = P.parse_class(a.cls)
    lane = P.place(c, bool(a.mbp_room), bool(a.phone_room))
    emit({"gate": "O1", "class": c.value, "lane": lane.value if lane else "wait",
          "groot_admits": P.admits(P.Lane.GROOT, c)})
    return 0


def cmd_classify(a) -> int:
    cmd = a.cmd[1:] if a.cmd and a.cmd[0] == "--" else a.cmd
    c = P.classify_command(cmd) if cmd else None
    print(c.value if c else "unknown")
    return 0


def cmd_run(a) -> int:
    cmd = a.cmd[1:] if a.cmd and a.cmd[0] == "--" else a.cmd
    if not cmd:
        print("z0-guard run: no command", file=sys.stderr)
        return 2
    if a.cls == "auto":
        c = P.classify_command(cmd)
        if c is None:
            print("z0-guard run: cannot classify this command; declare a class "
                  f"({', '.join(x.value for x in P.Cls)})", file=sys.stderr)
            return 2
    else:
        c = P.parse_class(a.cls)
    rec = {"class": c.value, "cmd": shlex.join(cmd)[:300], "host": socket.gethostname()}
    if not on_groot() or P.admits(P.Lane.GROOT, c):
        verdict("O1", False, {**rec, "lane": "local"}, quiet=True)
        if c is P.Cls.TINY:
            try:
                return subprocess.run(cmd, timeout=P.TINY_SECONDS).returncode
            except subprocess.TimeoutExpired:
                verdict("O1", True, {**rec, "lane": "groot", "reason": f"'tiny' job ran past {P.TINY_SECONDS} s; "
                                     "declare its real class so it goes to fleet-build"})
                return 124
        os.execvp(cmd[0], cmd)
    # heavy class on groot: the only admitted path is a non-groot lane through fleet-build (O1)
    m = mode("O1")
    if m != "enforce":
        verdict("O1", True, {**rec, "lane": "groot", "reason": "heavy class run on groot"})
        sys.stdout.flush()
        os.execvp(cmd[0], cmd)
    fb = os.environ.get("Z0_GUARD_FLEET_BUILD") or shutil.which("fleet-build") or str(HOME / ".local/bin/fleet-build")
    argv = [fb, *FLEET_FLAGS.get(c, []), "--wait", str(a.wait), *a.fleet_arg, "--", *cmd]
    verdict("O1", False, {**rec, "lane": "fleet", "routed": shlex.join(argv)[:400]}, quiet=True)
    print(f"z0-guard: O1 routes {c.value} off groot: {shlex.join(argv)}", file=sys.stderr)
    sys.stdout.flush()
    sys.stderr.flush()
    os.execvp(argv[0], argv)
    return 0  # unreachable


# ------------------------------------------------------------------ O2 served worktree

def git_dir(wt: str | None = None) -> Path:
    if wt is None and os.environ.get("GIT_DIR"):
        return Path(os.environ["GIT_DIR"]).resolve()
    return Path(git(wt or ".", "rev-parse", "--absolute-git-dir"))


def served_info(gd: Path) -> dict | None:
    try:
        return json.loads((gd / "z0-served.json").read_text())
    except (OSError, ValueError):
        return None


def txn_valid(gd: Path) -> bool:
    tok = os.environ.get("Z0_ROLLOUT_TOKEN", "")
    try:
        lock = json.loads((gd / "z0-rollout.lock").read_text())
    except (OSError, ValueError):
        return False
    return bool(tok) and hashlib.sha256(tok.encode()).hexdigest() == lock.get("token_sha256")


HOOKS = ["pre-commit", "pre-merge-commit", "pre-rebase", "pre-applypatch", "reference-transaction", "post-checkout"]
SHIM = """#!/bin/sh
# z0-guard O2 shim (served worktree guard). Unserved worktrees exit at once.
gd=${GIT_DIR:-$(git rev-parse --absolute-git-dir 2>/dev/null)}
if [ -f "$gd/z0-served.json" ]; then
  if [ "$Z0_GUARD_O2" != off ]; then
    Z0G=${Z0_GUARD_BIN:-%(bin)s}
    if [ "%(hook)s" = reference-transaction ]; then
      input=$(cat); printf '%%s\\n' "$input" | "$Z0G" hook %(hook)s "$@" || exit $?
      [ -x "$0.z0-orig" ] && { printf '%%s\\n' "$input" | "$0.z0-orig" "$@" || exit $?; }
      exit 0
    fi
    "$Z0G" hook %(hook)s "$@" || exit $?
  fi
fi
[ -x "$0.z0-orig" ] && exec "$0.z0-orig" "$@"
exit 0
"""


def hooks_dir(wt: str) -> Path:
    """The repository's own hooks dir (<common dir>/hooks), never a core.hooksPath template:
    the O4 template in .files chains to this directory, so both guards run."""
    return Path(git(wt, "rev-parse", "--path-format=absolute", "--git-common-dir")) / "hooks"


def cmd_served(a) -> int:
    wt = str(Path(a.worktree).resolve())
    gd = git_dir(wt)
    if a.action == "mark":
        rec = {"worktree": wt, "unit": a.unit, "port": a.port, "health": a.health, "deeplink": a.deeplink,
               "serial": a.serial, "owner_device": not a.scratch, "marked_at": now_iso()}
        (gd / "z0-served.json").write_text(json.dumps(rec, indent=1) + "\n")
        emit({"served": "marked", **rec})
    elif a.action == "unmark":
        (gd / "z0-served.json").unlink(missing_ok=True)
        emit({"served": "unmarked", "worktree": wt})
    elif a.action == "status":
        hd = hooks_dir(wt)
        emit({"worktree": wt, "served": served_info(gd), "hooks_dir": str(hd),
              "hooks": {h: (hd / h).exists() and "z0-guard O2 shim" in (hd / h).read_text(errors="ignore")
                        for h in HOOKS}})
    elif a.action == "install-hooks":
        hd = hooks_dir(wt)
        hd.mkdir(parents=True, exist_ok=True)
        binp = os.environ.get("Z0_GUARD_BIN") or shutil.which("z0-guard") or str(HOME / ".local/bin/z0-guard")
        for h in HOOKS:
            f = hd / h
            if f.exists() and "z0-guard O2 shim" not in f.read_text(errors="ignore"):
                f.rename(hd / f"{h}.z0-orig")
            f.write_text(SHIM % {"bin": binp, "hook": h})
            f.chmod(0o755)
        hp = git(wt, "config", "--get", "core.hooksPath", check=False)
        emit({"installed": HOOKS, "hooks_dir": str(hd), "bin": binp,
              **({"warning": f"core.hooksPath={hp}: these hooks run only if that dir chains to {hd} "
                             "(the .files z0-hooks template does)"} if hp else {})})
    elif a.action == "uninstall-hooks":
        hd = hooks_dir(wt)
        for h in HOOKS:
            f = hd / h
            if f.exists() and "z0-guard O2 shim" in f.read_text(errors="ignore"):
                f.unlink()
                if (hd / f"{h}.z0-orig").exists():
                    (hd / f"{h}.z0-orig").rename(f)
        emit({"uninstalled": HOOKS, "hooks_dir": str(hd)})
    return 0


GUARDED_REF = re.compile(r"^(HEAD|refs/heads/.+|refs/stash|ORIG_HEAD|MERGE_HEAD)$")


def ref_txn_op(lines: list[str], wt: str, rollback_to: str | None = None) -> P.GitOp | None:
    """Classify a reference-transaction batch in a served worktree; None = no guarded ref moves.
    A branch moving forward is MERGE_FF; moving back to exactly the revision the open rollout
    replaced is ROLLBACK; anything else (stash, branch switch, creation, deletion, rewind) is
    CHECKOUT. A plain commit also moves forward: it counts as MERGE_FF and is refused outside
    the rollout transaction like every other op."""
    ops = []
    for ln in lines:
        parts = ln.split()
        if len(parts) != 3:
            continue
        old, new, ref = parts
        if not GUARDED_REF.match(ref) or old == new or ref in ("ORIG_HEAD", "MERGE_HEAD"):
            continue
        if ref == "refs/stash":
            ops.append(P.GitOp.STASH)
        elif old.strip("0") == "" or new.strip("0") == "" or new.startswith("ref:") or old.startswith("ref:"):
            ops.append(P.GitOp.CHECKOUT)
        elif rollback_to and new == rollback_to:
            ops.append(P.GitOp.ROLLBACK)
        elif sh("git", "-C", wt, "merge-base", "--is-ancestor", old, new).returncode == 0:
            ops.append(P.GitOp.MERGE_FF)
        else:
            ops.append(P.GitOp.CHECKOUT)
    if not ops:
        return None
    for o in (P.GitOp.STASH, P.GitOp.CHECKOUT, P.GitOp.ROLLBACK):
        if o in ops:
            return o
    return P.GitOp.MERGE_FF


def restore_after_abort(gd: Path, wt: str, lines: list[str]) -> None:
    """A fast-forward or branch switch writes the working tree BEFORE it updates refs, so a
    refused ref update leaves the new files behind (and Metro would serve them). After the
    refusal, put back exactly the files that differ between HEAD and the refused target, once
    the refused command has released the index. Other paths are not touched."""
    clean = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    head = sh("git", "-C", wt, "rev-parse", "HEAD", env=clean).stdout.strip()
    files: set[str] = set()
    for ln in lines:
        parts = ln.split()
        if len(parts) != 3 or not GUARDED_REF.match(parts[2]) or parts[2] in ("ORIG_HEAD", "MERGE_HEAD"):
            continue
        new = parts[1]
        if new.startswith("ref:"):
            new = sh("git", "-C", wt, "rev-parse", "--verify", "-q", new[4:].strip(), env=clean).stdout.strip()
        if not new or new.strip("0") == "" or new == head:
            continue
        files |= set(sh("git", "-C", wt, "diff", "--name-only", head, new, env=clean).stdout.split("\n")) - {""}
    if not files:
        return
    lock = gd / "index.lock"
    script = ('l=$1; w=$2; shift 2; for i in $(seq 75); do [ -e "$l" ] || break; sleep 0.2; done; '
              'for f; do git -C "$w" restore --source=HEAD --staged --worktree -- "$f" 2>/dev/null; done')
    subprocess.Popen(["sh", "-c", script, "z0-restore", str(lock), wt, *sorted(files)], env=clean,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    ledger({"gate": "O2", "worktree": wt, "restored_after_refusal": sorted(files)[:50], "head": head})
    print(f"z0-guard O2: restoring {len(files)} file(s) in {wt} to HEAD {head[:12]} after the refused update.",
          file=sys.stderr)


def rollout_prev(gd: Path) -> str | None:
    try:
        return json.loads((gd / "z0-rollout.lock").read_text()).get("prev")
    except (OSError, ValueError):
        return None


def cmd_hook(a) -> int:
    name, args = a.name, a.args
    if name == "pre-push":
        return prepush(args[0] if args else "origin", args[1] if len(args) > 1 else "", sys.stdin.read())
    gd = git_dir()
    info = served_info(gd)
    if not info:
        return 0
    wt = info.get("worktree") or os.getcwd()
    txn = txn_valid(gd)
    if name == "reference-transaction":
        phase = (args[:1] or [""])[0]
        if phase == "aborted" and not txn:
            restore_after_abort(gd, wt, sys.stdin.read().splitlines())
            return 0
        if phase != "prepared":
            return 0
        lines = sys.stdin.read().splitlines()
        op = ref_txn_op(lines, wt, rollout_prev(gd) if txn else None)
        if op is None:
            return 0
    else:
        op = P.git_op(name, args)
    allowed = P.wt_allows(True, op, txn)
    rec = {"worktree": wt, "unit": info.get("unit"), "hook": name, "op": op.value, "rollout_txn": txn}
    if name == "post-checkout":
        if not allowed:
            print(f"z0-guard O2: {wt} is served by {info.get('unit')}; a checkout here changes what the "
                  "owner's device loads. Restore it and use `z0-guard rollout`.", file=sys.stderr)
            verdict("O2", True, {**rec, "reason": "checkout in a served worktree (post-checkout cannot undo it)"},
                    quiet=True)
        return 0
    if allowed:
        return 0
    print(f"z0-guard O2: refused {op.value} in {wt}: it serves {info.get('unit')} (port {info.get('port')}). "
          "Build in another worktree and promote with `z0-guard rollout`.", file=sys.stderr)
    return verdict("O2", True, {**rec, "reason": "served worktree accepts only merge --ff-only inside z0-guard rollout"},
                   quiet=True)


# ------------------------------------------------------------------ O3 rollout

def owner_call_live(window_s: int = 120) -> bool | None:
    """A z0live call is live: an un-ended broker session updated within the window. None = unknown."""
    if os.environ.get("Z0_GUARD_CALL") in ("live", "idle"):
        return os.environ["Z0_GUARD_CALL"] == "live"
    try:
        with sqlite3.connect(f"file:{BROKER_DB}?mode=ro", uri=True, timeout=2) as db:
            cols = {r[1] for r in db.execute("PRAGMA table_info(sessions)")}
            q = "SELECT count(*) FROM sessions WHERE updated >= ?" + (" AND ended = 0" if "ended" in cols else "")
            return db.execute(q, (time.time() - window_s,)).fetchone()[0] > 0
    except sqlite3.Error:
        return None


def service_idle(port: int, samples: int = 3, gap: float = 0.5) -> bool:
    """No request in flight: every established TCP connection on the port has empty queues,
    over a few samples. A long-lived idle websocket (Metro HMR) does not count as busy."""
    for i in range(samples):
        out = sh("ss", "-Htn", "state", "established", f"( sport = :{port} )").stdout.splitlines()
        for ln in out:
            f = ln.split()
            if len(f) >= 2 and (f[0] != "0" or f[1] != "0"):
                return False
        if i + 1 < samples:
            time.sleep(gap)
    return True


def candidate_clean(cand: str, served_wt: str, sha: str) -> tuple[bool, str]:
    cand = str(Path(cand).resolve())
    if cand == served_wt:
        return False, "candidate is the served worktree"
    try:
        head = git(cand, "rev-parse", "HEAD")
        dirty = git(cand, "status", "--porcelain", "--untracked-files=no")
    except subprocess.CalledProcessError as e:
        return False, f"candidate worktree unreadable: {e}"
    if head != sha:
        return False, f"candidate HEAD {head[:12]} is not the ref being promoted {sha[:12]}"
    if dirty:
        return False, "candidate worktree has uncommitted changes (what was tested is not what is promoted)"
    return True, "ok"


def review_receipt_ok(path: str | None, sha: str) -> bool:
    if not path:
        return False
    try:
        r = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return False
    return r.get("sha") == sha and r.get("verdict") in ("approve", "approved", "pass") and bool(r.get("reviewer"))


def wait_http(url: str, timeout: float, fail_on_5xx: bool = False) -> tuple[bool, str]:
    """Poll until HTTP 200. Connection errors mean "not up yet". With fail_on_5xx, a server
    that answers 5xx has answered: that is the result (Metro serves a transform error as 500)."""
    end = time.time() + timeout
    last = ""
    while time.time() < end:
        try:
            with urllib.request.urlopen(url, timeout=min(120, max(10, end - time.time()))) as r:
                body = r.read(400_000).decode("utf-8", "replace")
                if r.status == 200:
                    return True, body
                last = f"HTTP {r.status}"
        except urllib.error.HTTPError as e:
            last = f"HTTP Error {e.code}: {e.reason}"
            if fail_on_5xx and e.code >= 500:
                return False, last
        except Exception as e:  # noqa: BLE001 (any other failure means not up yet)
            last = str(e)[:200]
        time.sleep(1)
    return False, last


ERROR_LINE = re.compile(r"\b(ERROR|FATAL EXCEPTION|Uncaught|TransformError|SyntaxError|Unable to resolve)\b"
                        r"|AndroidRuntime: FATAL|ReactNativeJS.*\b(Error|Invariant Violation)\b")


def journal_errors(unit: str, since: float) -> list[str]:
    out = sh("journalctl", "--user", "-u", unit, "--since", f"@{int(since)}", "--no-pager", "-o", "cat").stdout
    return [ln for ln in out.splitlines() if ERROR_LINE.search(ln)][:20]


def device_launch_check(info: dict, since: float, out_dir: Path, mode_: str) -> tuple[bool, dict]:
    """O3(b). adb: open the Expo deep link on the real runtime, then screencap and scan logcat
    and the service journal for errors within 30 s. web (scratch services only): request the
    Android bundle from the dev server and scan the journal. Never runs during an owner call
    (the caller checks O5 first)."""
    ev: dict = {"mode": mode_}
    unit = info["unit"]
    if mode_ == "web":
        if info.get("owner_device", True):
            return False, {**ev, "error": "web check is not a device check; refused for an owner-device service"}
        port = info["port"]
        ok, body = wait_http(f"http://localhost:{port}/index.bundle?platform=android&dev=true&minify=false", 240,
                             fail_on_5xx=True)
        errs = journal_errors(unit, since)
        ev.update({"bundle_ok": ok, "bundle_bytes": len(body) if ok else 0,
                   "bundle_error": None if ok else body[:300], "journal_errors": errs})
        return ok and not errs, ev
    serial, link = info.get("serial"), info.get("deeplink")
    if not serial or not link:
        return False, {**ev, "error": "no device serial/deep link recorded for this served worktree"}
    env = {k: v for k, v in os.environ.items() if k != "ADB_SERVER_SOCKET"}
    adb = ["adb", "-s", serial]
    t0 = sh(*adb, "shell", "date", "+%m-%d %H:%M:%S.000", env=env).stdout.strip()
    st = sh(*adb, "shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", link, env=env, timeout=30)
    ev["am_start"] = (st.stdout + st.stderr).strip()[-300:]
    time.sleep(30)
    out_dir.mkdir(parents=True, exist_ok=True)
    shot = out_dir / f"launch-{int(time.time())}.png"
    with open(shot, "wb") as f:
        subprocess.run([*adb, "exec-out", "screencap", "-p"], stdout=f, env=env, timeout=30)
    log = sh(*adb, "logcat", "-d", "-T", t0, "*:E", env=env, timeout=30).stdout if t0 else ""
    errs = [ln for ln in log.splitlines() if ERROR_LINE.search(ln)][:20]
    jerr = journal_errors(unit, since)
    ev.update({"screencap": str(shot), "screencap_bytes": shot.stat().st_size if shot.exists() else 0,
               "logcat_errors": errs, "journal_errors": jerr})
    ok = "Error" not in ev["am_start"] and not errs and not jerr and ev["screencap_bytes"] > 0
    return ok, ev


def restart_unit(unit: str) -> bool:
    return sh("systemctl", "--user", "restart", unit, timeout=120).returncode == 0


def pending(rec: dict) -> None:
    ledger(rec, "pending-rollouts.jsonl")


def wait_idle(port: int, budget_s: int) -> bool:
    end = time.time() + budget_s
    while True:
        if service_idle(port):
            return True
        if time.time() >= end:
            return False
        time.sleep(2)


def cmd_rollout(a) -> int:
    """O2 (enforce by default): the served worktree changes only by merge --ff-only inside this
    transaction, into an idle service, restarted at once. O3 (report by default): candidate
    from a clean non-served worktree, review receipt for gated PRs, device launch check after
    restart, automatic rollback. In O3 report mode the transaction still runs; what O3 would
    have refused, deferred or rolled back is recorded, not done. The launch check never runs
    while an owner call is live (O5), in any mode."""
    wt = str(Path(a.worktree).resolve())
    gd = git_dir(wt)
    info = served_info(gd)
    if not info:
        print(f"z0-guard rollout: {wt} is not marked served (z0-guard served mark ...)", file=sys.stderr)
        return 2
    m3 = mode("O3")
    sha = git(wt, "rev-parse", a.ref)
    prev = git(wt, "rev-parse", "HEAD")
    clean, why_c = candidate_clean(a.candidate, wt, sha)
    ff = sh("git", "-C", wt, "merge-base", "--is-ancestor", prev, sha).returncode == 0
    dirty = git(wt, "status", "--porcelain", "--untracked-files=no")
    call = owner_call_live() if info.get("owner_device", True) else False
    no_device = a.check == "adb" and not (info.get("serial") and info.get("deeplink"))
    device_blocked = call is not False or no_device      # unknown call state counts as live
    idle = wait_idle(int(info["port"]), a.idle_wait)
    q = P.Req(clean=clean and not dirty, ff=ff, gated=a.gated, receipt=review_receipt_ok(a.review_receipt, sha),
              call=device_blocked, idle=idle)
    base = {"worktree": wt, "unit": info["unit"], "ref": a.ref, "sha": sha, "prev": prev, "inputs": q.__dict__,
            "o3_mode": m3, "candidate": a.candidate, "candidate_note": why_c, "served_dirty": bool(dirty),
            "owner_call_live": call, "device_check_available": not no_device}
    retry = (f"z0-guard rollout --worktree {shlex.quote(wt)} --ref {sha} --candidate {shlex.quote(a.candidate)}"
             + (" --gated" if a.gated else "") + (f" --review-receipt {shlex.quote(a.review_receipt)}" if a.review_receipt else ""))
    # O2 is enforced in every O3 mode: no rewrite, no edits underneath, no busy restart
    if not ff or dirty:
        reasons = (["O2: served worktree does not fast-forward to the candidate"] if not ff else []) + (
            ["O2: served worktree has uncommitted edits"] if dirty else [])
        return verdict("O2", True, {**base, "out": "refused", "reasons": reasons})
    if not idle:
        rec = {**base, "out": "deferred", "reasons": ["O2: service not idle"], "retry": retry}
        pending(rec)
        emit({"gate": "O2", "status": "pending", **rec})
        return 75
    pre = P.admission(q)
    would: list[str] = []
    if pre is P.Out.REFUSED:
        if m3 == "enforce":
            return verdict("O3", True, {**base, "out": "refused", "reasons": P.why_refused(q)})
        would += [f"would-refuse: {r}" for r in P.why_refused(q)]
    if q.call:
        reason = ("owner z0live call live (O5)" if call else "owner call state unknown (fail closed)" if call is None
                  else "no device serial/deep link recorded")
        if m3 == "enforce":
            rec = {**base, "out": "deferred", "reasons": P.why_deferred(q) + [reason], "retry": retry}
            pending(rec)
            emit({"gate": "O3", "status": "pending", **rec})
            return 75
        would.append(f"would-defer: device launch check cannot run: {reason}")
    if a.dry_run:
        emit({"gate": "O3", "status": "would-run", "would": would, **base})
        return 0
    token = secrets.token_hex(16)
    lock = gd / "z0-rollout.lock"
    try:
        fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        print(f"z0-guard rollout: another rollout holds {lock}", file=sys.stderr)
        return EXIT_REFUSED
    with os.fdopen(fd, "w") as f:
        json.dump({"token_sha256": hashlib.sha256(token.encode()).hexdigest(), "pid": os.getpid(),
                   "sha": sha, "prev": prev, "at": now_iso()}, f)
    env = {**os.environ, "Z0_ROLLOUT_TOKEN": token}
    out_dir = STATE / "rollouts" / f"{int(time.time())}-{info['unit']}"
    health = info.get("health") or f"http://localhost:{info['port']}/status"
    ev: dict = {}
    try:
        m = sh("git", "-C", wt, "merge", "--ff-only", sha, env=env, timeout=120)
        if m.returncode:
            return verdict("O2", True, {**base, "out": "refused", "reasons": ["merge --ff-only failed: " + m.stderr[-300:]]})
        t_restart = time.time()
        restarted = restart_unit(info["unit"])
        up, _ = wait_http(health, 120)
        ev.update({"restart_ok": restarted, "health_up": up})
        if q.call:   # report mode only: never run the device check during a call or without a device
            check = None
        elif restarted and up:
            check, cev = device_launch_check(info, t_restart, out_dir, a.check)
            ev.update(cev)
        else:
            check = False
        out = P.rollout(q, bool(check)) if check is not None else None
        if out is P.Out.ROLLED_BACK and m3 != "enforce":
            would.append("would-roll-back: device launch check failed")
            out = None
        if out is P.Out.ROLLED_BACK:
            r = sh("git", "-C", wt, "reset", "--keep", prev, env=env, timeout=120)
            rb_restart = restart_unit(info["unit"])
            rb_up, _ = wait_http(health, 120)
            ev["rollback"] = {"reset_ok": r.returncode == 0, "reset_err": r.stderr[-200:], "restart_ok": rb_restart,
                              "health_up": rb_up, "head": git(wt, "rev-parse", "HEAD")}
        status = out.value if out else ("merged-check-pending" if check is None else
                                        "merged-check-passed" if check else "merged-check-failed")
        if check is None:
            pending({**base, "out": "check-pending", "reasons": would, "retry_check":
                     f"z0-guard rollout --worktree {shlex.quote(wt)} --ref {sha} --candidate {shlex.quote(a.candidate)} --check {a.check}"})
        rec = {**base, "out": status, "check": check, "would": would, "evidence": ev}
        ledger({"gate": "O3", **rec}, "rollouts.jsonl")
        emit({"gate": "O3", "status": status, **rec})
        if out is P.Out.ROLLED_BACK or not (restarted and up):
            return 1
        return 0
    finally:
        lock.unlink(missing_ok=True)


# ------------------------------------------------------------------ O4 privacy

def gh_visibility(slug: str, ttl: int = 6 * 3600) -> P.Vis:
    cache = CACHE / "visibility.json"
    try:
        c = json.loads(cache.read_text())
    except (OSError, ValueError):
        c = {}
    hit = c.get(slug)
    if hit and time.time() - hit["at"] < ttl:
        return P.Vis(hit["vis"])
    r = sh("gh", "repo", "view", slug, "--json", "visibility", "-q", ".visibility", timeout=20)
    v = {"PUBLIC": P.Vis.PUBLIC, "PRIVATE": P.Vis.PRIVATE, "INTERNAL": P.Vis.PRIVATE}.get(r.stdout.strip(), P.Vis.UNKNOWN)
    if v is not P.Vis.UNKNOWN:
        c[slug] = {"vis": v.value, "at": time.time()}
        CACHE.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(c, indent=1))
    return v


def remote_slug(url: str) -> str | None:
    m = re.search(r"github\.com[:/]+([^/\s]+/[^/\s]+?)(\.git)?/?$", url or "")
    return m.group(1) if m else None


def remote_vis(url: str) -> P.Vis:
    slug = remote_slug(url)
    if slug:
        return gh_visibility(slug)
    if url.startswith(("/", "file://", "./", "../")):
        return P.Vis.PRIVATE  # a path on this machine is not public
    return P.Vis.UNKNOWN


def private_globs() -> list[str]:
    try:
        return [ln.strip() for ln in (CONFIG / "private-paths").read_text().splitlines()
                if ln.strip() and not ln.startswith("#")]
    except OSError:
        return []


def private_paths(wt: str, tip: str, paths: list[str]) -> list[str]:
    out, globs = [], private_globs()
    marked_dirs = set(git(wt, "ls-tree", "-r", "--name-only", tip, check=False).splitlines())
    markers = {str(Path(p).parent) for p in marked_dirs if Path(p).name == ".z0-private"}
    attrs = {}
    if paths:
        r = sh("git", "-C", wt, "check-attr", "--stdin", "z0-private", inp="\n".join(paths) + "\n")
        for ln in r.stdout.splitlines():
            p, _, v = ln.rpartition(": z0-private: ")
            attrs[p] = v.strip() in ("set", "true")
    for p in paths:
        parents = {str(x) for x in Path(p).parents}
        if attrs.get(p) or parents & markers or any(fnmatch.fnmatch(p, g) for g in globs):
            out.append(p)
    return out


def repo_private(wt: str) -> tuple[bool, str]:
    if git(wt, "config", "--get", "z0.private", check=False) == "true":
        return True, "git config z0.private=true"
    top = git(wt, "rev-parse", "--show-toplevel")
    if (Path(top) / ".z0-private").exists():
        return True, ".z0-private at repo root"
    origin = git(wt, "remote", "get-url", "origin", check=False)
    if origin and remote_vis(origin) is P.Vis.PRIVATE:
        return True, f"origin {remote_slug(origin) or origin} is private"
    return False, ""


def prepush(remote: str, url: str, stdin: str, wt: str = ".") -> int:
    vis = remote_vis(url)
    rp, why_repo = repo_private(wt)
    offending, rec_refs = [], []
    for ln in stdin.splitlines():
        f = ln.split()
        if len(f) != 4 or f[1].strip("0") == "":
            continue
        local_sha, remote_sha = f[1], f[3]
        excl = ["--not", f"--remotes={remote}"] + ([remote_sha] if remote_sha.strip("0") and
                                                    sh("git", "-C", wt, "cat-file", "-e", remote_sha).returncode == 0 else [])
        commits = git(wt, "rev-list", local_sha, *excl, check=False).split()
        paths = sorted(set(git(wt, "log", "--name-only", "--format=", local_sha, *excl, check=False).split("\n")) - {""})
        priv = paths if (rp and commits) else private_paths(wt, local_sha, paths)
        rec_refs.append({"ref": f[2], "commits": len(commits), "paths": len(paths), "private_paths": len(priv)})
        offending += priv
    private = bool(offending)
    allowed = P.push_allows(private, vis)
    rec = {"remote": remote, "url_repo": remote_slug(url) or url, "visibility": vis.value, "private_content": private,
           "repo_private": why_repo or None, "refs": rec_refs, "examples": offending[:8]}
    if not allowed:
        print(f"z0-guard O4: refused push of private content to {rec['url_repo']} ({vis.value}). "
              f"Examples: {', '.join(offending[:5])}", file=sys.stderr)
    return verdict("O4", not allowed, rec, quiet=allowed)


def cmd_prepush(a) -> int:
    return prepush(a.remote, a.url, sys.stdin.read(), a.worktree)


# ------------------------------------------------------------------ O5 owner phone

def phone_decision(kind: str, *, utterance: str | None = None, intent: bool | None = None,
                   expected_app: str | None = None, foreground_app: str | None = None,
                   package: str | None = None, chain: bool | None = None, call: bool | None = None) -> dict:
    """The O5 decision for callers (the z0live broker, phone-harness wrappers, install scripts).
    Pure apart from reading broker.db when `call` is None."""
    k = P.PKind(kind)
    if intent is None:
        intent = P.owner_phone_intent(utterance or "")
    fg = bool(expected_app) and expected_app == foreground_app
    if chain is None:
        chain = (package or "") in CALL_CHAIN_PACKAGES
    if call is None:
        live = owner_call_live()
        call = True if live is None else live
    ok = P.phone_allows(k, intent=intent, fg=fg, chain=chain, call=call)
    reasons = []
    if not ok and k is P.PKind.TAP:
        reasons += [] if intent else ["not_a_phone_request"]
        reasons += [] if fg else ["foreground_mismatch"]
    elif not ok:
        reasons.append("call_chain_app_during_live_call")
    return {"allowed": ok, "kind": k.value, "intent": intent, "fg_expected": fg, "chain": chain, "call_live": call,
            "reasons": reasons}


def cmd_phone(a) -> int:
    call = {"auto": None, "live": True, "idle": False}[a.call]
    d = phone_decision(a.kind, utterance=a.utterance, expected_app=a.expected_app, foreground_app=a.foreground_app,
                       package=a.package, chain=True if a.chain else None, call=call)
    return verdict("O5", not d["allowed"], {"decision": d, "utterance": (a.utterance or "")[:160]})


# ------------------------------------------------------------------ O6 accessibility

def cmd_a11y(a) -> int:
    pre, post, rm = P.parse_a11y(a.pre), P.parse_a11y(a.post), P.parse_a11y(a.removed)
    ok = P.a11y_ok(pre, post, rm)
    code = verdict("O6", not ok, {"pre": pre, "post": post, "removed_by_owner": rm,
                                  "missing": P.a11y_missing(pre, post, rm)})
    return 5 if code else 0


# ------------------------------------------------------------------ O7 routing

def _walk(x, path=""):
    if isinstance(x, dict):
        for k, v in x.items():
            yield from _walk(v, f"{path}.{k}" if path else str(k))
    elif isinstance(x, list):
        for i, v in enumerate(x):
            yield from _walk(v, f"{path}[{i}]")
    else:
        yield path, x


SECRETISH = re.compile(r"(key|token|secret|password|auth|cookie)", re.I)


def routing_findings(omp: Path, hermes_files: list[Path], dash: Path) -> list[dict]:
    try:
        import yaml  # noqa: PLC0415
    except ImportError:
        return [{"source": "z0-guard", "error": "PyYAML missing"}]
    found = []

    def check(source: str, role: str, provider: str | None, model: str | None, where: str):
        cancelled, sol = P.provider_flags(provider, model)
        design = P.design_role(role)
        if not P.route_ok(cancelled, sol, design):
            found.append({"source": source, "where": where, "role": role, "provider": provider, "model": model,
                          "law": "O7", "why": "cancelled provider" if cancelled else "design role routed to Sol"})

    def load(p: Path):
        try:
            return yaml.safe_load(p.read_text()) or {}
        except (OSError, yaml.YAMLError):
            return None

    for src, path in (("omp", omp), ("dash", dash)):
        d = load(path)
        if not isinstance(d, dict):
            continue
        for role, sel in (d.get("modelRoles") or {}).items():
            if isinstance(sel, str) and not sel.startswith("@"):
                check(src, role, None, sel, f"modelRoles.{role}")
        for role, chain in ((d.get("retry") or {}).get("fallbackChains") or {}).items():
            for i, sel in enumerate(chain or []):
                if isinstance(sel, str) and not sel.startswith("@"):
                    check(src, role, None, sel, f"retry.fallbackChains.{role}[{i}]")
        for path_, v in _walk(d):
            if SECRETISH.search(path_) or not isinstance(v, str):
                continue
            if path_.endswith(".provider") and not path_.startswith(("modelRoles", "retry")):
                check(src, path_.rsplit(".", 2)[-2], v, None, path_)
    for f in hermes_files:
        d = load(f)
        if not isinstance(d, dict):
            continue
        for path_, v in _walk(d):
            if SECRETISH.search(path_) or not isinstance(v, str):
                continue
            leaf = path_.rsplit(".", 1)[-1]
            parent = path_.rsplit(".", 1)[0] if "." in path_ else ""
            if leaf == "provider" and not parent.startswith("providers"):
                model = None
                for k2, v2 in _walk(d):
                    if k2 == f"{parent}.model" or k2 == f"{parent}.default":
                        model = v2 if isinstance(v2, str) else None
                check(str(f), parent or "model", v, model, path_)
    return found


def cmd_routing(a) -> int:
    hermes = [Path(p) for p in a.hermes] if a.hermes else (
        [HOME / ".hermes/config.yaml", *sorted((HOME / ".hermes/profiles").glob("*/config.yaml"))])
    f = routing_findings(Path(a.omp), hermes, Path(a.dash))
    return verdict("O7", bool(f), {"findings": f, "checked": [a.omp, a.dash, *map(str, hermes)][:20]})


# ------------------------------------------------------------------ O8 node health

def notify_owner(text: str) -> dict:
    """Deliver one owner notification. The fleet has no notifier of its own; groot's existing
    owner-push path is KDE Connect to the paired S25 (a notification, never a UI input)."""
    r = sh("kdeconnect-cli", "-a", "--id-only", timeout=10)
    dev = (r.stdout.split() or [None])[0]
    if not dev:
        return {"delivered": False, "via": "kdeconnect", "error": "no reachable paired device"}
    s = sh("kdeconnect-cli", "-d", dev, "--ping-msg", text[:300], timeout=15)
    return {"delivered": s.returncode == 0, "via": "kdeconnect", "device": dev[:8]}


def health_step(state: dict, node: str, ok: bool, now: int, lim: int = P.NODE_LIMIT_S,
                deliver=notify_owner, gate_mode: str | None = None) -> tuple[dict, dict | None]:
    """One probe through the O8 machine. state[node] = {"kind", "since"}. Returns (state, event)."""
    cur = state.get(node) or {}
    s = P.NSt(cur.get("kind", "healthy"), int(cur.get("since", 0)))
    fire = P.neff(s, ok, now, lim)
    s2 = P.nstep(s, ok, now, lim)
    state[node] = {"kind": s2.kind, "since": s2.since}
    if not fire:
        return state, None
    m = gate_mode or mode("O8")
    text = f"fleet: {node} unhealthy for {(now - s.since) // 60} min (since {dt.datetime.fromtimestamp(s.since):%H:%M})"
    ev = {"gate": "O8", "node": node, "since": s.since, "at_ts": now, "mode": m, "text": text}
    ev["delivery"] = deliver(text) if m == "enforce" else {"delivered": False, "via": "report-only"}
    ledger(ev, "o8-notifications.jsonl")
    return state, ev


def cmd_health_step(a) -> int:
    sf = Path(a.state)
    try:
        st = json.loads(sf.read_text())
    except (OSError, ValueError):
        st = {}
    st, ev = health_step(st, a.node, bool(a.ok), int(a.now or time.time()), a.limit)
    sf.parent.mkdir(parents=True, exist_ok=True)
    sf.write_text(json.dumps(st, indent=1))
    emit({"node": a.node, "state": st[a.node], "notification": ev})
    return 0


# ------------------------------------------------------------------ O9 gateway restart

def gateway_identity() -> dict:
    out = {}
    r = sh("systemctl", "--user", "show", "hermes-gateway", "-p", "InvocationID", "--value")
    out["gateway_invocation"] = r.stdout.strip()
    h = sh("git", "-C", str(HERMES_CHECKOUT), "rev-parse", "HEAD")
    out["hermes_head"] = h.stdout.strip()
    return out


def active_sol_units() -> dict[str, list[str]]:
    r = sh("systemctl", "--user", "list-units", "sol-*", "--type=service", "--state=active", "--plain", "--no-legend")
    units = {}
    for ln in r.stdout.splitlines():
        u = ln.split()[0] if ln.split() else ""
        if not u.endswith(".service"):
            continue
        ex = sh("systemctl", "--user", "show", u, "-p", "ExecStart", "--value").stdout
        m = re.search(r"argv\[\]=(.*?) ;", ex)
        units[u] = shlex.split(m.group(1)) if m else []
    return units


def run_dir_for(unit: str, argv: list[str]) -> Path | None:
    if "--query-file" in argv:
        i = argv.index("--query-file")
        if i + 1 < len(argv):
            return Path(argv[i + 1]).parent
    name = unit.removeprefix("sol-").removesuffix(".service")
    hits = sorted(SOL_RUNS.glob(f"*-{name}"))
    return hits[-1] if hits else None


def interrupted_records(prev: dict, cur_units: dict, ident: dict, at: str, has_report=None) -> list[dict]:
    """O9: every run active before a gateway restart that is gone now without a report is
    Interrupted (rstep(Active, GwRestart) = Interrupted), with its resume command."""
    out = []
    restarted = any(prev.get(k) and prev.get(k) != ident.get(k) for k in ("gateway_invocation", "hermes_head"))
    if not restarted:
        return out
    for unit, argv in (prev.get("units") or {}).items():
        if unit in cur_units:
            continue
        d = run_dir_for(unit, argv)
        if d and (has_report(d) if has_report else (d / "REPORT.md").exists()):
            continue
        s = P.rstep(P.RSt("active", 1), "gw-restart", 1)
        resume = ("systemd-run --user --unit=" + unit.removesuffix(".service") + "-resume " + shlex.join(argv)) if argv else None
        out.append({"worker": f"sol-run/{unit.removesuffix('.service').removeprefix('sol-')}", "action": s.kind, "by": "z0-guard-o9",
                    "at": at, "cause": "gateway restart", "gateway_before": {k: prev.get(k) for k in ident},
                    "gateway_after": ident, "run_dir": str(d) if d else None,
                    "resume": resume, "resume_note": "write RESUME.txt in the run dir first (continue, do not redo); "
                                                    "or report the run abandoned"})
    return out


def cmd_gateway_watch(a) -> int:
    sf = Path(a.state)
    try:
        prev = json.loads(sf.read_text())
    except (OSError, ValueError):
        prev = {}
    ident, units = gateway_identity(), active_sol_units()
    recs = interrupted_records(prev, units, ident, now_iso())
    m = mode("O9")
    for r in recs:
        ledger(r, "o9-interrupted.jsonl")
        if m == "enforce":
            DECISIONS.parent.mkdir(parents=True, exist_ok=True)
            with open(DECISIONS, "a") as f:
                f.write(json.dumps(r) + "\n")
    sf.parent.mkdir(parents=True, exist_ok=True)
    sf.write_text(json.dumps({**ident, "units": units, "at": now_iso()}, indent=1))
    emit({"gate": "O9", "mode": m, "gateway": ident, "active_runs": len(units), "interrupted": recs})
    return 0


# ------------------------------------------------------------------ O10 source policy

def cmd_phone_caps(a) -> int:
    root = Path(a.root)
    paths = [Path(p) for p in a.path] if a.path is not None else None
    findings = phone_caps_findings(root, paths)
    return verdict("O10", bool(findings), {"root": str(root.resolve()), "findings": findings,
                                         "checked": list(map(str, phone_caps_paths(root, paths)))})


# ------------------------------------------------------------------ O11/O12/O13 policy evidence

def cmd_policy(a) -> int:
    from .policy_guard import audit, static_findings

    try:
        report, findings = audit(a.receipts, Path(a.policy).expanduser() if a.policy else None, a.since)
    except ValueError as exc:
        print(f"z0-guard policy: {exc}", file=sys.stderr)
        return 2
    if a.static:
        findings.extend(static_findings(Path(a.static).expanduser()))
    static_bad = any(f["rule"].startswith("hardcoded-") for f in findings)
    if any(f["rule"] == "static-unavailable" for f in findings):
        report["available"] = False
        report["unavailable_reason"] = "; ".join(filter(None, (
            report["unavailable_reason"], "static scan unavailable")))
    destination = Path(os.environ.get("Z0INT_HOME", str(HOME / ".z0int"))).expanduser() / "policy/guard-report.json"
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".guard-report.{os.getpid()}.{secrets.token_hex(4)}.json")
        try:
            temporary.write_text(json.dumps(report, sort_keys=True) + "\n")
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
    except OSError as exc:
        report["available"] = False
        report["unavailable_reason"] = "; ".join(filter(None, (
            report["unavailable_reason"], f"cannot persist guard report: {exc}")))
    if findings:
        print(json.dumps({"findings": findings, "static_findings": sum(
            f["rule"].startswith("hardcoded-") for f in findings)}, sort_keys=True), file=sys.stderr)
    emit(report)
    if static_bad or any(report["counts"].values()):
        return EXIT_REFUSED
    return 0 if report["available"] else 4


# ------------------------------------------------------------------ modes, main

def cmd_modes(a) -> int:
    emit({g: mode(g) for g in DEFAULT_MODES})
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="z0-guard", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    r = sub.add_parser("run", help="O1: run a job of a declared class (heavy classes go to fleet-build)")
    r.add_argument("cls", help="job class or 'auto'")
    r.add_argument("--wait", type=int, default=1800)
    r.add_argument("--fleet-arg", action="append", default=[])
    r.add_argument("cmd", nargs=argparse.REMAINDER)
    r.set_defaults(fn=cmd_run)

    cl = sub.add_parser("classify", help="O1: best-effort job class of a command")
    cl.add_argument("cmd", nargs=argparse.REMAINDER)
    cl.set_defaults(fn=cmd_classify)

    p = sub.add_parser("place", help="O1: pure placement decision")
    p.add_argument("cls")
    p.add_argument("--mbp-room", type=int, default=1)
    p.add_argument("--phone-room", type=int, default=0)
    p.set_defaults(fn=cmd_place)

    s = sub.add_parser("served", help="O2: served-worktree lease and hooks")
    s.add_argument("action", choices=["mark", "unmark", "status", "install-hooks", "uninstall-hooks"])
    s.add_argument("--worktree", required=True)
    s.add_argument("--unit")
    s.add_argument("--port", type=int)
    s.add_argument("--health")
    s.add_argument("--deeplink")
    s.add_argument("--serial")
    s.add_argument("--scratch", action="store_true", help="not serving the owner's device (allows the web check)")
    s.set_defaults(fn=cmd_served)

    h = sub.add_parser("hook", help="git hook entry")
    h.add_argument("name")
    h.add_argument("args", nargs="*")
    h.set_defaults(fn=cmd_hook)

    ro = sub.add_parser("rollout", help="O2+O3: guarded promotion of a served worktree")
    ro.add_argument("--worktree", required=True)
    ro.add_argument("--ref", required=True)
    ro.add_argument("--candidate", required=True, help="the non-served worktree the candidate was built and tested in")
    ro.add_argument("--gated", action="store_true", help="PR is marked review-gated")
    ro.add_argument("--review-receipt")
    ro.add_argument("--check", choices=["adb", "web"], default="adb")
    ro.add_argument("--idle-wait", type=int, default=60, help="seconds to wait for the service to go idle")
    ro.add_argument("--dry-run", action="store_true")
    ro.set_defaults(fn=cmd_rollout)

    pp = sub.add_parser("prepush", help="O4: privacy boundary (stdin: pre-push lines)")
    pp.add_argument("remote")
    pp.add_argument("url")
    pp.add_argument("--worktree", default=".")
    pp.set_defaults(fn=cmd_prepush)

    ph = sub.add_parser("phone", help="O5: owner phone decision")
    ph.add_argument("--kind", choices=[k.value for k in P.PKind], required=True)
    ph.add_argument("--utterance")
    ph.add_argument("--expected-app")
    ph.add_argument("--foreground-app")
    ph.add_argument("--package")
    ph.add_argument("--chain", action="store_true")
    ph.add_argument("--call", choices=["auto", "live", "idle"], default="auto")
    ph.set_defaults(fn=cmd_phone)

    ay = sub.add_parser("a11y", help="O6: accessibility preservation")
    ay.add_argument("--pre", required=True)
    ay.add_argument("--post", required=True)
    ay.add_argument("--removed", default="")
    ay.set_defaults(fn=cmd_a11y)

    rt = sub.add_parser("routing", help="O7: provider routing checker")
    rt.add_argument("--omp", default=str(HOME / ".omp/agent/config.yml"))
    rt.add_argument("--dash", default=str(HOME / ".dash/model-roles.yml"))
    rt.add_argument("--hermes", action="append")
    rt.set_defaults(fn=cmd_routing)

    hs = sub.add_parser("health-step", help="O8: one probe through the notify machine")
    hs.add_argument("--state", default=str(STATE / "o8-state.json"))
    hs.add_argument("--node", required=True)
    hs.add_argument("--ok", type=int, required=True)
    hs.add_argument("--now", type=int)
    hs.add_argument("--limit", type=int, default=P.NODE_LIMIT_S)
    hs.set_defaults(fn=cmd_health_step)

    gw = sub.add_parser("gateway-watch", help="O9: detect gateway restarts, mark runs Interrupted")
    gw.add_argument("--state", default=str(STATE / "o9-state.json"))
    gw.set_defaults(fn=cmd_gateway_watch)

    pc = sub.add_parser("phone-caps", help="O10: scan phone-capable sources and agent policy for resource caps")
    pc.add_argument("--root", required=True, help=".files checkout root (never inferred from the process cwd)")
    pc.add_argument("--path", action="append", help="explicit file, relative to root or absolute; replaces discovery")
    pc.set_defaults(fn=cmd_phone_caps)

    pg = sub.add_parser("policy", help="O11/O12/O13: audit receipts, option configurations and literal routing overrides")
    pg.add_argument("--receipts", help="JSON/JSONL receipt glob (quote it to avoid shell expansion)")
    pg.add_argument("--policy", help="z0.decision_policy.v1 file")
    pg.add_argument("--since", default="24h", help="audit window: seconds/minutes/hours/days, e.g. 24h")
    pg.add_argument("--static", metavar="ROOT", help="also scan source outside policy loader files")
    pg.add_argument("--json", action="store_true", help="emit z0.policy_guard.v1 JSON (also the default)")
    pg.set_defaults(fn=cmd_policy)

    st = sub.add_parser("state", help="A–E: read-only claim, manifest, revocation, outcome and measurement audit")
    state_input = st.add_mutually_exclusive_group()
    state_input.add_argument("--input", help="finite replay JSON; not a live sensor")
    state_input.add_argument("--config", default=str(CONFIG / "state.json"), help="live sensor inventory JSON")
    st.set_defaults(fn=lambda a: __import__("z0int.fsm.state_laws", fromlist=["command"]).command(a))

    from . import root_cause, root_canaries
    root_cause.register(sub)
    root_canaries.register(sub)

    rp = sub.add_parser("replay", help="replay incident tapes through the gates")
    rp.add_argument("tapes", nargs="+")
    rp.set_defaults(fn=lambda a: __import__("z0int.fsm.ops_replay", fromlist=["main"]).main(a.tapes))

    md = sub.add_parser("modes")
    md.set_defaults(fn=cmd_modes)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
