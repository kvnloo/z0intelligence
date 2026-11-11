"""claimcheck: find the typed claims in a message and dereference each one in this process.

check(text, profile_home=None) -> [{type, locator, ok, detail}]
  ok True   the claim was checked and holds
  ok False  the claim was checked and does not hold
  ok None   the claim could not be checked: no locator, or detail starts with "infra" (gh/network/time budget)

The writer never supplies the command. A claim names a locator and this module runs the fixed check for its type:
  path      /abs/path on a done/receipt/report/log line    exists on this host (lines about another host are skipped)
  pushed    owner/repo@sha or /repo/path@sha               a remote branch (or GitHub) has the commit
  merged    PR URL, or repo@sha                            PR merged, or the default branch contains the commit
  pr        https://github.com/O/R/pull/N or O/R#N         PR is open or merged; head sha matches when one is given
  tests     /abs/junit.xml or /abs/pytest.log, or O/R@sha  0 failures, 0 errors, count equals the claim, under 24 h old
  deployed  URL, optional "sha256 <hex>"                   HTTP 200 and the served body hash matches
  service   unit name                                      active for 60 s or more, or a oneshot that exited 0
A line is skipped when it starts with "unverified:" or ends with "?"; a trigger is skipped when its clause, or
one of the four words before it, negates it or puts it in the future (not, pending, will, needs, after, when, if, ...).
Quality, UX and research claims have no type here and are never checked.
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import time
import xml.etree.ElementTree as ET

try:  # the kanban gate's PR pattern and per-profile gh login, when running inside Hermes
    from hermes_cli.kanban_pr_acceptance import _PR, _gh_env
except Exception:  # standalone (tests, ledger)
    _PR = re.compile(r"https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/pull/([1-9][0-9]*)")
    _gh_env = None

TIMEOUT_S, CACHE_S, MAX_CLAIMS, STALE_S, MIN_ACTIVE_S = 15, 60, 12, 24 * 3600, 60
_cache: dict[tuple, tuple[float, tuple]] = {}

_SHA = r"[0-9a-f]{7,40}"
_REPOSHA = re.compile(r"(?<![\w/.~-])([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+|~?(?:/[\w.+-]+)+)@(" + _SHA + r")\b")
_DONE = re.compile(r"(?i)\b(done|fixed|finished|complete[d]?|works now|verified|shipped|resolved)\b")
_NEG = re.compile(r"(?i)(\b(not|never|no|none|without|\w+n't|cannot|un(pushed|merged|verified|deployed|tested|checked)|"
                  r"pending|awaiting|will|would|to be|needs?|after|once|when|if|before|until|unless|should|must|"
                  r"please|whether|then|claim\w*|said|false|wrong)\b|=\s*K\d+|\b(?:0|zero)\s*$)")
_PATH = re.compile(r"(?<![\w:/.@~-])(~?(?:/[\w.+-]+){2,})(?![\w/]*[@*<{$])")
_PATH_SKIP = re.compile(r"(?i)\b(deleted?|removed?|rm|pruned?|moved|renamed|gone|missing|absent|not found|no such|"
                        r"not exist|cleaned|purged|will|would|should|create|write|put|save|to be)\b")
_PUSHED = re.compile(r"(?i)\b(pushed|landed on (?:origin|main|master|preview))\b")
_MERGED = re.compile(r"(?i)\b(merged|on main)\b")
# "merged" is a PR/branch claim only next to one; "merged into its version" is a file edit
_MERGE_CTX = re.compile(r"(?i)\bPRs?\b|#\d+|/pull/|squash|\b(main|master|preview|upstream|origin)\b|@" + _SHA)
_PR_SHORT = re.compile(r"(?<![\w/.-])([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+) ?#([1-9][0-9]*)\b")  # owner/repo#N
_EVIDENCE_NOUN = re.compile(r"(?i)\b(receipt|report|evidence|proof|log|backup|wrote|written|saved)\b")
_ELSEWHERE = re.compile(r"(?i)\b(mbp|macbook|ssh|scp|rsync|remote|phone|s2[25]|adb)\b")
_PRWORD = re.compile(r"(?i)\bPRs?\s+(is\s+)?(opened|open|created|filed|up)\b|\b(opened|created|filed)\s+(a\s+|the\s+)?PR\b")
_TESTS = re.compile(r"(?i)\b(?:(\d+)\s*/\s*(\d+)\s+(?:tests?\s+)?(?:passed|pass|passing|green|ok)|"
                    r"(\d+)\s+(?:tests?\s+)?(?:passed|pass|passing|green)|"
                    r"tests?\s+(?:all\s+)?(?:are\s+)?(?:pass(?:ed|ing)?|green)|suite\s+(?:is\s+)?(?:passed|passes|green))\b")
_FAILED = re.compile(r"(?i)\b[1-9]\d*\s+(failed|errors?)\b")
_RESULT_FILE = re.compile(r"(?<![\w:/.@~-])(~?(?:/[\w.+-]+)+\.(?:xml|log|txt))\b")
_DEPLOYED = re.compile(r"(?i)\b(deployed|live at|is live|now live)\b")
_URL = re.compile(r"https?://[^\s)>\]\"'`,]+")
_SHA256 = re.compile(r"(?i)sha-?256\W{0,3}([0-9a-f]{8,64})\b")
_SERVICE = re.compile(r"(?i)\b(healthy|active \(running\)|restarted|is-active|is active|is running)\b")
_UNIT = re.compile(r"(?<![\w/.-])([\w@-]+(?:\.[\w@-]+)*\.(?:service|timer))\b")
_FILLER = {"and", "is", "are", "was", "now", "been", "again", "the", "service", "unit", "then", "both", "all"}


def _run(cmd: list[str], env: dict | None = None) -> tuple[int | None, bytes, str]:
    """(rc, stdout, stderr); rc None when the command could not run or timed out."""
    try:
        r = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, timeout=TIMEOUT_S, env=env)
        return r.returncode, r.stdout, r.stderr.decode("utf-8", "replace")
    except (subprocess.TimeoutExpired, OSError) as e:
        return None, b"", type(e).__name__


RUN = _run  # tests replace this
NOW_MONO = lambda: time.clock_gettime(time.CLOCK_MONOTONIC)  # noqa: E731


def _gh(endpoint: str, jq: str, home: str | None) -> tuple[bool | None, str]:
    """(found, value): found None = infra, False = GitHub says no such thing."""
    env = None
    if _gh_env and home:
        try:
            env = _gh_env(home)
        except Exception:
            env = None
    rc, out, err = RUN(["gh", "api", endpoint, "--hostname", "github.com", "--jq", jq], env)
    if rc == 0:
        return True, out.decode("utf-8", "replace").strip()
    if rc is not None and re.search(r"HTTP (404|422)", err):
        return False, "GitHub has no " + endpoint.split("repos/", 1)[-1]
    return None, "infra: gh " + (re.search(r"HTTP \d+", err) or [err.strip()[:40] or "failed"])[0]


def _local(repo: str) -> str | None:
    p = os.path.expanduser(repo)
    return p if p.startswith("/") else None


def _remote_branches(repo: str, sha: str) -> tuple[bool | None, list[str], str]:
    rc, out, err = RUN(["git", "-C", repo, "branch", "-r", "--contains", sha])
    if rc is None:
        return None, [], "infra: git " + err
    if rc != 0:
        return False, [], f"{sha} is not a commit in {repo}"
    return True, [b.strip() for b in out.decode("utf-8", "replace").splitlines() if b.strip()], ""


def _check_pushed(repo: str, sha: str, home: str | None) -> tuple[bool | None, str]:
    local = _local(repo)
    if local:
        ok, branches, why = _remote_branches(local, sha)
        if not ok:
            return ok, why
        return (True, branches[0]) if branches else (False, f"no remote branch contains {sha}; push it")
    ok, val = _gh(f"repos/{repo}/commits/{sha}", ".sha", home)
    return ok, val[:12] if ok else val


def _check_merged(repo: str, sha: str, home: str | None) -> tuple[bool | None, str]:
    local = _local(repo)
    if local:
        ok, branches, why = _remote_branches(local, sha)
        if not ok:
            return ok, why
        main = [b for b in branches if re.fullmatch(r"origin/(main|master)|origin/HEAD.*", b)]
        return (True, main[0]) if main else (False, f"origin main does not contain {sha}")
    ok, branch = _gh(f"repos/{repo}", ".default_branch", home)
    if not ok:
        return ok, branch
    ok, status = _gh(f"repos/{repo}/compare/{sha}...{branch}", ".status", home)
    if not ok:
        return ok, status
    return (True, branch) if status in ("ahead", "identical") else (False, f"{branch} does not contain {sha} ({status})")


def _check_pr(repo: str, number: str, sha: str, merged: bool, home: str | None) -> tuple[bool | None, str]:
    ok, val = _gh(f"repos/{repo}/pulls/{number}", '[.state, (.merged|tostring), .head.sha] | join(" ")', home)
    if not ok:
        return ok, val
    state, is_merged, head = (val.split() + ["", "", ""])[:3]
    if merged and is_merged != "true":
        return False, f"PR #{number} is {state}, not merged"
    if state != "open" and is_merged != "true":
        return False, f"PR #{number} is closed and unmerged"
    if sha and not head.startswith(sha):
        return False, f"PR #{number} head is {head[:9]}, not {sha}"
    return True, f"#{number} {'merged' if is_merged == 'true' else state} {head[:9]}"


def _counts(path: str) -> tuple[int, int] | None:
    """(passed, failed+errors) from a junit xml or a pytest-style log."""
    if os.path.getsize(path) > 20_000_000:
        return None
    if path.endswith(".xml"):
        root = ET.parse(path).getroot()
        suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
        n = lambda k: sum(int(float(s.get(k) or 0)) for s in suites)  # noqa: E731
        bad = n("failures") + n("errors")
        return n("tests") - n("skipped") - bad, bad
    with open(path, errors="replace") as f:
        tail = f.read()[-4000:]
    lines = [ln for ln in tail.splitlines() if re.search(r"\b\d+ passed\b", ln)]
    if not lines:
        return None
    last = lines[-1]
    bad = sum(int(m[1]) for m in re.finditer(r"\b(\d+) (?:failed|errors?)\b", last))
    return int(re.search(r"\b(\d+) passed\b", last)[1]), bad


def _check_tests(path: str, claimed: int | None) -> tuple[bool | None, str]:
    p = os.path.expanduser(path)
    if not os.path.isfile(p):
        return False, f"{path} does not exist"
    if time.time() - os.path.getmtime(p) > STALE_S:
        return False, f"{path} is older than 24 h; rerun"
    try:
        counts = _counts(p)
    except (ET.ParseError, OSError, ValueError):
        counts = None
    if counts is None:
        return False, f"{path} has no test summary"
    passed, bad = counts
    if bad:
        return False, f"{path} has {bad} failures/errors"
    if passed <= 0:
        return False, f"{path} ran 0 tests"
    if claimed is None:
        return False, f"no count claimed; {path} shows {passed} passed"
    if claimed != passed:
        return False, f"claimed {claimed}, {path} shows {passed} passed"
    return True, f"{passed} passed"


def _check_ci(repo: str, sha: str, home: str | None) -> tuple[bool | None, str]:
    ok, val = _gh(f"repos/{repo}/commits/{sha}/check-runs",
                  '[.check_runs[] | (.conclusion // .status)] | join(" ")', home)
    if not ok:
        return ok, val
    runs = val.split()
    bad = [r for r in runs if r not in ("success", "skipped", "neutral")]
    if not runs or bad:
        return False, f"CI on {sha}: " + (f"{len(bad)} of {len(runs)} runs not success ({bad[0]})" if runs else "no runs")
    return True, f"{len(runs)} CI runs success"


def _check_deployed(url: str, want: str) -> tuple[bool | None, str]:
    rc, body, err = RUN(["curl", "-fsSk", "--max-time", "10", url])
    if rc in (None, 28):  # a timeout says nothing about the claim
        return None, f"infra: {url} timed out"
    if rc != 0:
        return False, f"{url} did not return 200 (curl rc={rc})"
    got = hashlib.sha256(body).hexdigest()
    if want and not got.startswith(want.lower()):
        return False, f"served sha256 is {got[:12]}, not {want[:12]}"
    return True, "HTTP 200" + (f" sha256 {got[:12]}" if want else ", no hash claimed")


def _unit_state(unit: str, scopes=(["--user"], [])) -> dict | None:
    for scope in scopes:
        rc, out, _ = RUN(["systemctl", *scope, "show", unit, "-p",
                          "LoadState,ActiveState,SubState,Result,ExecMainStatus,Type,ActiveEnterTimestampMonotonic"])
        st = dict(ln.split("=", 1) for ln in out.decode("utf-8", "replace").splitlines() if "=" in ln)
        if rc == 0 and st.get("LoadState") == "loaded":
            return st
    return None


def _check_service(unit: str) -> tuple[bool | None, str]:
    st = _unit_state(unit)
    if st is None:
        return False, f"{unit} is not a loaded unit"
    state = f"{st.get('ActiveState')}/{st.get('SubState')}"
    if st.get("Type") == "oneshot" and st.get("ActiveState") in ("inactive", "active"):
        ok = st.get("Result") == "success" and st.get("ExecMainStatus") == "0"
        return ok, f"{unit} oneshot result={st.get('Result')} status={st.get('ExecMainStatus')}"
    if st.get("ActiveState") != "active":
        return False, f"{unit} is {state} result={st.get('Result')}"
    if unit.endswith(".timer"):
        return True, f"{unit} {state}"
    age = NOW_MONO() - int(st.get("ActiveEnterTimestampMonotonic") or 0) / 1e6
    if age < MIN_ACTIVE_S:
        return False, f"{unit} active only {int(age)} s; recheck after {MIN_ACTIVE_S} s"
    return True, f"{unit} {state} {int(age)} s"


def _clause(line: str, start: int, comma: bool = True) -> str:
    """The text between the last clause break and the trigger. comma=False keeps a comma list in one clause
    ("1 failed, 2 passed" is one summary; "6 failed → 6 passed" and "x; y" are two)."""
    return re.split(r"[" + ("," if comma else "") + r";:.()]\s|\s[-–—→>]+\s", line[:start])[-1]


def _negated(line: str, start: int) -> bool:
    return bool(_NEG.search(_clause(line, start)) or _NEG.search(" ".join(_clause(line, start, False).split()[-4:])))


def _one(rx: re.Pattern, line: str, text: str):
    """The match in this line, else the only distinct one in the whole message."""
    m = rx.search(line)
    if m:
        return m
    found = {x.group(0): x for x in rx.finditer(text)}
    return next(iter(found.values())) if len(found) == 1 else None


def extract(text: str) -> list[tuple]:
    """[(type, locator, check_args)]; locator "" means the claim names nothing checkable."""
    claims: dict[tuple, tuple] = {}
    add = lambda t, loc, *a: claims.setdefault((t, loc), (t, loc, a))  # noqa: E731
    done = bool(_DONE.search(text))
    for raw in text.splitlines():
        line = raw.strip().lstrip("-*•> ").strip()
        if not line or line.lower().startswith("unverified:") or line.endswith("?"):
            continue
        live = lambda rx: next((m for m in rx.finditer(line) if not _negated(line, m.start())), None)  # noqa: E731
        repo = _one(_REPOSHA, line, text)
        short = None if _PR.search(line) else _PR_SHORT.search(line)
        if short:  # same check as the URL form
            line = line.replace(short.group(0), f"https://github.com/{short.group(1)}/pull/{short.group(2)}")
        pr = _PR.search(line)
        merged = live(_MERGED) if _MERGE_CTX.search(line) else None
        if pr:
            sha = re.search(r"\b" + _SHA + r"\b", _PR.sub(" ", line))
            add("merged" if merged else "pr", pr.group(0), pr.group(1), pr.group(2), sha.group(0) if sha else "", bool(merged))
        elif merged:
            add("merged", repo.group(0) if repo else "", *(repo.groups() if repo else ()))
        elif live(_PRWORD):
            pr = _one(_PR, line, text)
            add("pr", pr.group(0) if pr else "", *((pr.group(1), pr.group(2), "", False) if pr else ()))
        if live(_PUSHED):
            add("pushed", repo.group(0) if repo else "", *(repo.groups() if repo else ()))
        t = live(_TESTS)
        if t and not _FAILED.search(_clause(line, t.start(), False)) and (t.group(1) is None or t.group(1) == t.group(2)):
            n = t.group(1) or t.group(3)
            f = _one(_RESULT_FILE, line, text)
            if f:
                add("tests", f.group(1), "file", f.group(1), int(n) if n else None)
            elif repo and not _local(repo.group(1)):
                add("tests", repo.group(0), "ci", *repo.groups())
            else:
                add("tests", "")
        if live(_DEPLOYED):
            u = _one(_URL, line, text)
            h = _SHA256.search(line)
            add("deployed", u.group(0).rstrip(".") if u else "", *((u.group(0).rstrip("."), h.group(1) if h else "") if u else ()))
        s = live(_SERVICE)
        if s:
            u = _UNIT.search(line)
            if u:
                add("service", u.group(1), u.group(1), True)
            else:
                words = [w.strip(".,:;()`'\"") for w in line[:s.start()].split()]
                words = [w for w in words if w and w.lower() not in _FILLER]
                if words and re.fullmatch(r"[\w@-]+", words[-1]):
                    add("service", words[-1] + ".service", words[-1] + ".service", False)
        if (done and (_DONE.search(line) or _EVIDENCE_NOUN.search(line)) and not _ELSEWHERE.search(line)
                and not _PATH_SKIP.search(_PATH.sub(" ", line))):
            for m in _PATH.finditer(line):
                p = m.group(1).rstrip(".")
                full = os.path.expanduser(p)
                if "..." not in p and os.path.exists("/" + full.split("/")[1]):
                    add("path", p, full)
    return list(claims.values())[:MAX_CLAIMS * 4]


_NEEDS = {"pushed": "owner/repo@sha or /repo/path@sha", "merged": "the PR URL or owner/repo@sha", "pr": "the full PR URL",
          "tests": "an absolute junit .xml or pytest .log path (or owner/repo@sha for CI) and the count",
          "deployed": "the URL", "service": "the unit name"}


def _verify(kind: str, args: tuple, home: str | None) -> tuple[bool | None, str] | None:
    if kind == "path":
        return os.path.exists(args[0]), "exists" if os.path.exists(args[0]) else f"{args[0]} does not exist"
    if kind == "pushed":
        return _check_pushed(args[0], args[1], home)
    if kind == "merged" and len(args) == 2:
        return _check_merged(args[0], args[1], home)
    if kind in ("pr", "merged"):
        return _check_pr(*args, home)
    if kind == "tests":
        return _check_tests(args[1], args[2]) if args[0] == "file" else _check_ci(args[1], args[2], home)
    if kind == "deployed":
        return _check_deployed(*args)
    if kind == "service":
        unit, explicit = args
        if not explicit and _unit_state(unit, (["--user"],)) is None:
            return None  # the word before the trigger is not one of our units: not a typed claim
        return _check_service(unit)
    raise ValueError(kind)


def check(text: str, profile_home: str | None = None, budget_s: float = 45.0) -> list[dict]:
    out, deadline, checked = [], time.time() + budget_s, 0
    for kind, loc, args in extract(text or ""):
        if not loc:
            out.append({"type": kind, "locator": "", "ok": None, "detail": "no locator: name " + _NEEDS[kind]})
            continue
        hit = _cache.get((kind, loc, args))
        if hit and time.time() - hit[0] < CACHE_S:
            res = hit[1]
        elif checked >= MAX_CLAIMS or time.time() > deadline:
            res = (None, "infra: check budget used up")
        else:
            checked += kind != "path"
            try:
                res = _verify(kind, args, profile_home)
            except Exception as e:  # a checker bug must not look like a false claim
                res = (None, f"infra: {type(e).__name__}")
            if res is not None and not str(res[1]).startswith("infra"):
                _cache[(kind, loc, args)] = (time.time(), res)
        if res is not None:
            out.append({"type": kind, "locator": loc, "ok": res[0], "detail": str(res[1])[:160]})
    return out


def infra(claim: dict) -> bool:
    return claim["ok"] is None and claim["detail"].startswith("infra")


def summary(claims: list[dict]) -> str:
    bad = [c for c in claims if c["ok"] is not True]
    return ", ".join(f"{c['type']}: {c['detail']}" for c in bad[:4]) + (f", +{len(bad) - 4} more" if len(bad) > 4 else "")
