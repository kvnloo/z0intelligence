"""O17 GitHub access gate: pollers read through ghcache, backoff refuses writes.

The decision is ops.gh_read_allows / ops.gh_write_allows (twin of ops.bend O17).
This module gathers the host facts the kernel takes as inputs:

  poller   the gh process runs in a systemd unit started by a timer
           (TriggeredBy non-empty), or GH_CALLER=poller. The env can only
           escalate to poller, never clear it: a timer job cannot opt out.
  cached   a `gh api` call to api.github.com is never cached. The shim's safe
           alternative is to re-run a compatible read through `ghc` (ghcache).
  backoff  ~/.z0int/state/gh-feedback/BACKOFF.json names the target repo
           (written by gh-feedback-watch when a maintainer objects to how we
           contribute; lifted only by `gh-feedback-watch ack REPO --lesson`).

Kept import-light (no guard.py) because ~/.local/bin/gh calls it per invocation:

    python3 -m z0int.fsm.gh_route shim -- ARGV...   exit 0 direct, 10 route via ghc, 3 refused
    python3 -m z0int.fsm.gh_route decide --op read --poller --direct   pure decision (JSON)
    python3 -m z0int.fsm.gh_route scan [--json]     timer units whose scripts reach GitHub directly

Scope: classifying argv, the unit and the backoff file is heuristic and not
proven. Tools that call /usr/bin/gh or api.github.com themselves bypass the
shim; `scan` reports those among timer units, it does not block them.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from . import ops as P

HOME = Path.home()
BACKOFF = Path(os.environ.get("GH_FEEDBACK_BACKOFF", HOME / ".z0int/state/gh-feedback/BACKOFF.json"))
CONFIG = Path(os.environ.get("Z0_GUARD_CONFIG", HOME / ".config/z0-guard"))
EXIT_REFUSED, EXIT_ROUTE = 3, 10
WRITE_SUBCMDS = {"create", "comment", "review", "edit", "reopen", "close", "merge", "ready", "delete",
                 "lock", "unlock", "transfer", "pin", "unpin", "develop"}
# `gh api` flags ghc understands; anything else cannot be routed faithfully.
GHC_FLAGS_VALUE = {"-X", "--method", "-f", "--raw-field", "-F", "--field", "-q", "--jq", "-H", "--header"}
GHC_FLAGS_BARE = {"--paginate"}


def mode() -> str:
    """Same lookup as guard.mode('O17'), without importing guard.py."""
    m = os.environ.get("Z0_GUARD_O17")
    if not m:
        try:
            m = json.loads((CONFIG / "modes.json").read_text()).get("O17")
        except (OSError, ValueError):
            m = None
    return m if m in ("off", "report", "enforce") else "enforce"


# ------------------------------------------------------------------ host facts

def unit_of(pid: int) -> str | None:
    try:
        cg = Path(f"/proc/{pid}/cgroup").read_text()
    except OSError:
        return None
    leaf = cg.strip().rsplit("/", 1)[-1]
    return leaf if leaf.endswith(".service") else None


def timer_started(unit: str | None) -> bool:
    if not unit:
        return False
    try:
        out = subprocess.run(["systemctl", "--user", "show", "-p", "TriggeredBy", "--value", unit],
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return ".timer" in out


def is_poller(pid: int, env: dict) -> tuple[bool, str | None]:
    unit = unit_of(pid)
    return env.get("GH_CALLER") == "poller" or timer_started(unit), unit


def backoff_repos() -> set[str]:
    try:
        return {k.lower() for k in json.loads(BACKOFF.read_text())}
    except (OSError, ValueError, AttributeError):
        return set()


def target_repo(argv: list[str], cwd: str) -> str | None:
    for i, x in enumerate(argv):
        if x in ("-R", "--repo") and i + 1 < len(argv):
            return argv[i + 1].lower()
        if x.startswith("--repo="):
            return x.split("=", 1)[1].lower()
    a = " ".join(argv)
    m = re.search(r"github\.com/([\w.-]+/[\w.-]+)/(pull|issues)/", a) or re.search(r"(?:^| |/)repos/([\w.-]+/[\w.-]+)/", a)
    if m:
        return m.group(1).lower()
    try:
        url = subprocess.run(["git", "-C", cwd, "remote", "get-url", "origin"], capture_output=True,
                             text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"github\.com[:/]([\w.-]+/[\w.-]+?)(\.git)?\s*$", url)
    return m.group(1).lower() if m else None


def classify(argv: list[str]) -> dict:
    """op: read | write | other; rest: a `gh api` REST/GraphQL call; routable: ghc can replay it."""
    if not argv:
        return {"op": "other", "rest": False, "routable": False}
    if argv[0] in ("pr", "issue", "release", "label", "repo", "gist", "workflow", "run", "secret", "variable"):
        write = len(argv) > 1 and argv[1] in WRITE_SUBCMDS | {"fork", "sync", "rerun", "cancel", "run", "set", "upload"}
        return {"op": "write" if write else "other", "rest": False, "routable": False}
    if argv[0] != "api":
        return {"op": "other", "rest": False, "routable": False}
    method, fields, routable, endpoint, i = None, [], True, None, 1
    while i < len(argv):
        x = argv[i]
        flag, _, inline = x.partition("=") if x.startswith("--") else (x, "", "")
        if flag in GHC_FLAGS_VALUE:
            val = inline or (argv[i + 1] if i + 1 < len(argv) else "")
            i += 0 if inline else 1
            if flag in ("-X", "--method"):
                method = val.upper()
            elif flag in ("-f", "--raw-field", "-F", "--field"):
                fields.append(val)
        elif flag in GHC_FLAGS_BARE:
            pass
        elif x.startswith("-"):
            routable = False                                  # --input, -i, --template, --cache ...
            if x in ("--input", "-t", "--template", "--hostname", "-p", "--preview", "--cache"):
                i += 1
            if x == "--input":
                method = method or "POST"
        elif endpoint is None:
            endpoint = x
        i += 1
    endpoint = (endpoint or "").lstrip("/")
    if endpoint == "graphql":
        q = next((f.split("=", 1)[1] for f in fields if f.startswith("query=")), "")
        write = bool(re.match(r"\s*mutation\b", q))
        return {"op": "write" if write else "read", "rest": True, "routable": routable and not write,
                "endpoint": endpoint}
    method = method or ("POST" if fields else "GET")
    op = "read" if method in ("GET", "HEAD") else "write"
    return {"op": op, "rest": True, "routable": routable and op == "read" and endpoint != "", "endpoint": endpoint}


# ------------------------------------------------------------------ decision

def decision(op: str, poller: bool, cached: bool, backoff: bool) -> dict:
    if op == "write":
        allowed = P.gh_write_allows(backoff)
        law = None if allowed else "o17_backoff_write_refused"
    elif op == "read":
        allowed = P.gh_read_allows(cached, poller)
        law = None if allowed else "o17_poller_direct_read_refused"
    else:
        allowed, law = True, None
    return {"gate": "O17", "op": op, "poller": poller, "cached": cached, "backoff": backoff,
            "allowed": allowed, "law": law}


def shim(argv: list[str], pid: int, cwd: str, env: dict) -> tuple[int, dict]:
    c = classify(argv)
    d = {"gate": "O17", "op": c["op"], "allowed": True, "law": None}
    if c["op"] == "write":
        repos = backoff_repos()
        repo = target_repo(argv, cwd) if repos else None
        d = decision("write", False, False, bool(repo and repo.removesuffix(".git") in repos))
        d["repo"] = repo
        if repos and not repo:
            d["note"] = f"backoff active on {', '.join(sorted(repos))}; target repo unknown"
    elif c["op"] == "read" and c["rest"]:
        poller, unit = is_poller(pid, env)
        d = decision("read", poller, False, False)
        d["unit"] = unit
        if not d["allowed"] and c["routable"]:
            d["route"] = "ghc"                    # the safe alternative: same read via ghcache
    d["mode"] = m = mode()
    if d["allowed"] or m == "off":
        return 0, d
    if d.get("route"):
        return EXIT_ROUTE, d
    return (EXIT_REFUSED if m == "enforce" else 0), d


def refusal_text(d: dict) -> str:
    if d["law"] == "o17_backoff_write_refused":
        r = d.get("repo")
        return (f"gh: refused by O17 (backoff on {r}): a maintainer objected to our contribution style there.\n"
                f"  Read ~/.z0int/state/gh-feedback/digest.md, change approach, then\n"
                f"  gh-feedback-watch ack {r} --lesson '<what we do differently>'")
    return (f"gh: refused by O17: {d.get('unit') or 'this poller'} is timer-started, so GitHub REST reads must go\n"
            f"  through ghcache. Use `ghc api ...` or ghclient.py (no direct fallback when ghcache is down).")


# ------------------------------------------------------------------ static scan

DIRECT = re.compile(r"api\.github\.com|gh\s+auth\s+token|/usr/bin/gh\b")
EXEMPT = re.compile(r"ghcache\.py|gh-pace")      # the proxy itself; gh-pace reads only free /rate_limit


def scan() -> list[dict]:
    out = subprocess.run(["systemctl", "--user", "list-timers", "--all", "--no-legend", "--plain"],
                         capture_output=True, text=True, timeout=20).stdout
    units = sorted({w for line in out.splitlines() for w in line.split() if w.endswith(".service")})
    rows = []
    for u in units:
        ex = subprocess.run(["systemctl", "--user", "show", "-p", "ExecStart", "--value", u],
                            capture_output=True, text=True, timeout=10).stdout
        paths = re.findall(r"argv\[\]=([^;]+);", ex)
        files = [Path(t) for a in paths for t in a.split() if t.startswith("/") and Path(t).is_file()]
        for f in files:
            if f.suffix in ("", ".py", ".sh", ".mjs", ".js", ".ts") and f.stat().st_size < 2_000_000:
                try:
                    text = f.read_text(errors="replace")
                except OSError:
                    continue
                if text.startswith("\x7fELF") or EXEMPT.search(str(f)):
                    continue
                hits = sorted({m.group(0) for m in DIRECT.finditer(text)})
                if hits:
                    rows.append({"unit": u, "file": str(f), "hits": hits})
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gh_route")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("shim")
    s.add_argument("--pid", type=int, default=os.getppid())
    s.add_argument("rest", nargs=argparse.REMAINDER)
    d = sub.add_parser("decide")
    d.add_argument("--op", choices=["read", "write"], required=True)
    d.add_argument("--poller", action="store_true")
    d.add_argument("--cached", action="store_true")
    d.add_argument("--backoff", action="store_true")
    sc = sub.add_parser("scan")
    sc.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "shim":
        rest = a.rest[1:] if a.rest[:1] == ["--"] else a.rest
        code, dec = shim(rest, a.pid, os.getcwd(), dict(os.environ))
        if code == EXIT_REFUSED:
            print(refusal_text(dec), file=sys.stderr)
        elif dec.get("note"):
            print("gh: note: " + dec["note"], file=sys.stderr)
        elif not dec["allowed"] and dec.get("mode") == "report":
            print("gh: O17 report-only: " + refusal_text(dec).splitlines()[0], file=sys.stderr)
        return code
    if a.cmd == "decide":
        dec = decision(a.op, a.poller, a.cached, a.backoff)
        print(json.dumps(dec))
        return 0 if dec["allowed"] else EXIT_REFUSED
    rows = scan()
    if a.json:
        print(json.dumps(rows, indent=1))
    else:
        for r in rows:
            print(f"{r['unit']}: {r['file']} {' '.join(r['hits'])}")
        print(f"{len(rows)} timer unit script(s) reach GitHub outside ghcache")
    return 1 if rows else 0


if __name__ == "__main__":
    raise SystemExit(main())
