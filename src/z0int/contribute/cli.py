"""``z0int contribute`` — the contributor start-here protocol (spec §1).

The CLI holds the logic; any agent only reads ``--json`` and follows
``next_action``. When ``next_action.kind`` is ``ask_human`` the agent shows
``needs_human`` to the person and stops.

Consent: everything here is C0 (local, inside the checkout and ``~/.z0int``)
except two public writes, which each need the human's explicit yes for that exact
post: ``claim --post`` (one issue comment) and ``pr --open`` (push to the
contributor's fork + a draft PR). Nothing is ever pushed to origin, and the PR
base is the repository default branch (owner decision O-5), never dev, preview or
nightly.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import align as A
from . import claims as C
from . import priorities as P
from . import profile_min, schemas, smoke
from . import state as St
from .gh import GH, GHError

SCHEMA = "z0.contribute.state.v1"
FORBIDDEN_BASES = ("dev", "preview", "nightly")
FULL_PATH_HINT = "optional full path: git clone https://github.com/kvnloo/z0 && ./z0/z0 onboard --auto"
POLL_SECONDS = 120


# ---------------------------------------------------------------- environment

def _git(args: list[str], cwd: Path) -> tuple[int, str]:
    try:
        p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, str(exc)
    return p.returncode, (p.stdout if p.returncode == 0 else p.stderr).strip()


def find_repo_root(start: Path) -> Path | None:
    """The checkout the user works in. Inside a task worktree made by `contribute start`,
    the checkout it was made from (so S0-S3 receipts keep binding to that checkout)."""
    code, top = _git(["rev-parse", "--show-toplevel"], start)
    if code != 0:
        return None
    top_p = Path(top).resolve()
    task_wts = (St.home() / "worktrees").resolve()
    if top_p.is_relative_to(task_wts):
        session_root = St.load_session().get("repo_root")
        if session_root and Path(session_root).is_dir():
            return Path(session_root)
        code, common = _git(["rev-parse", "--path-format=absolute", "--git-common-dir"], start)
        if code == 0:
            return Path(common).resolve().parent
    return top_p


def git_head(path: Path) -> str | None:
    code, out = _git(["rev-parse", "HEAD"], path)
    return out if code == 0 and re.fullmatch(r"[0-9a-f]{40}", out) else None


def default_branch(root: Path) -> str:
    code, out = _git(["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], root)
    if code == 0 and out.startswith("origin/"):
        return out.split("/", 1)[1]
    return "master"


def contributor_base(default: str) -> str:
    """PR base for contributors: the default branch (O-5). Never dev/preview/nightly."""
    if not default or default in FORBIDDEN_BASES:
        raise ValueError(f"refusing contributor base {default!r}: PRs target the default branch only")
    return default


def detect_harness() -> str:
    from ..harness_id import detect_harness_id

    h = os.environ.get("Z0_HARNESS") or detect_harness_id()
    if h == "unknown":
        if any(k.startswith("CODEX_") for k in os.environ):
            h = "codex"
        elif any(k.startswith("OPENCODE") for k in os.environ):
            h = "opencode"
    h = re.sub(r"[^a-z0-9._-]+", "-", h.lower()).strip("-.") or "unknown"
    return h


def repo_slug(root: Path) -> str:
    code, url = _git(["remote", "get-url", "origin"], root)
    m = re.search(r"github\.com[:/]+([^/]+/[^/.]+?)(?:\.git)?/?$", url or "") if code == 0 else None
    return m.group(1) if m else P.DEFAULT_REPO


@dataclass
class Env:
    root: Path
    gh: GH
    runner: smoke.Runner = smoke.default_runner
    now: Callable[[], _dt.datetime] = St.utc_now
    tty: bool = False
    ask: Callable[[str], str] = input
    sleep: Callable[[float], None] = time.sleep
    harness: str = field(default_factory=detect_harness)
    profile_probe: Callable[[], dict[str, Any]] | None = None
    poll_seconds: float = POLL_SECONDS
    warnings: list[str] = field(default_factory=list)

    @property
    def sha(self) -> str | None:
        return git_head(self.root)

    def profile_t0(self) -> dict[str, Any]:
        if self.profile_probe is not None:
            return self.profile_probe()
        try:
            return profile_min.probe_t0(workdir=self.root)
        except Exception as exc:  # noqa: BLE001 - fallback 4: detector failure -> minimal T0
            self.warnings.append(f"profile probe failed ({type(exc).__name__}); using unknown hardware")
            return {"schema": profile_min.SCHEMA, "tier": "T0", "source": "z0int.profile_min",
                    "hw": {"os": "unknown", "arch": "unknown", "ram_gib": None, "disk_free_gib": None,
                           "gpu": {"class": "none", "vram_gib": 0}},
                    "network": None, "toolchains": ["python"], "harnesses": []}

    def host_class(self) -> str:
        return A.host_class(self.profile_t0())


# ---------------------------------------------------------------- output

def envelope(env: Env, *, state: str, task: str | None, kind: str, command: str | None, why: str,
             needs_human: list[str] | None = None, receipts: list[str] | None = None,
             data: dict[str, Any] | None = None, onboard_mode: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "schema": SCHEMA, "state": state, "sha": env.sha, "task": task,
        "receipts": receipts or [], "next_action": {"kind": kind, "command": command, "why": why},
        "needs_human": needs_human or [], "warnings": list(dict.fromkeys(env.warnings)),
    }
    if onboard_mode:
        out["onboard_mode"] = onboard_mode
    if data is not None:
        out["data"] = data
    errors = schemas.validate(out, "contribute.state")
    if errors:  # pragma: no cover - programming error guard
        raise AssertionError(f"contribute output violates its schema: {errors}")
    return out


def _print(out: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(out, indent=2, sort_keys=True))
        return
    na = out["next_action"]
    print(f"z0int contribute  state={out['state']}  sha={(out['sha'] or '-')[:12]}"
          + (f"  task={out['task']}" if out.get("task") else ""))
    sug = (out.get("data") or {}).get("suggestion")
    if sug:
        for label, card in [("pick", sug["pick"]), *[(f"alt{i + 1}", c) for i, c in enumerate(sug["alternatives"])]]:
            print(f"  {label}: {card['task']}  score={card['score']}  {card.get('impact_note', '')}")
            print(f"        reasons: {', '.join(card['reasons'])}")
    for w in out["warnings"]:
        print(f"  warning: {w}")
    for n in out["needs_human"]:
        print(f"  needs you: {n}")
    print(f"  next: [{na['kind']}] {na['command'] or ''}\n        {na['why']}")


def _exit(out: dict[str, Any]) -> int:
    return 1 if out["next_action"]["kind"] == "blocked" else 0


# ---------------------------------------------------------------- shared steps

def _receipts() -> list[dict[str, Any]]:
    return [r for _, r in St.list_receipts()]


def _write(env: Env, *, state: str, command: str, code: int, output: str, result: str, sha: str | None = None,
           task: str | None = None, **kw: Any) -> str:
    r = St.make_receipt(state=state, sha=sha or env.sha or "0" * 40, command=command, exit_code=code,
                        output=output, host_class=env.host_class(), harness=env.harness, result=result,
                        task=task, z0_sha=kw.pop("z0_sha", None), now=env.now(), **kw)
    return str(St.write_receipt(r))


def _z0_home() -> Path | None:
    h = os.environ.get("Z0_HOME")
    return Path(h).expanduser() if h else None


def _z0_onboard_ok() -> tuple[bool, str, str | None]:
    """(ok, evidence text, z0 sha) from kvnloo/z0 host onboarding, when present."""
    home = _z0_home()
    if home is None:
        return False, "", None
    st = St.read_json(home / "onboard" / "state")
    if st is None:
        st = St.read_json(home / "onboard" / "state.json")
    ok = isinstance(st, dict) and (st.get("ok") is True or st.get("status") in ("success", "done", "ok"))
    z0_sha = git_head(home) if (home / ".git").exists() else None
    return ok, json.dumps(st, sort_keys=True) if st is not None else "", z0_sha


def _profile(env: Env, mode: str, prefs: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Consented profile (§3.2). z0int itself only probes T0."""
    tier = {"none": "T0", "minimal": "T1"}.get(mode)
    if tier is None:  # full
        granted = prefs.get("profile_tier", "T1")
        if granted in ("T2", "T3"):
            tier = granted
        elif env.tty:
            ans = env.ask("Allow T2 profile signals (language histogram of your repos, no file contents)? [y/N] ")
            tier = "T2" if ans.strip().lower() in ("y", "yes") else "T1"
            St.save_pref("profile_tier", tier)
        else:
            tier = "T1"
            env.warnings.append("no TTY and no stored grant: using T0+T1 signals only")
    base = env.profile_t0()
    home = _z0_home()
    richer = St.read_json(home / "onboard" / "profile.json") if home else None
    if isinstance(richer, dict) and tier in ("T2", "T3"):
        merged = {**base, **{k: v for k, v in richer.items() if k in ("languages", "recency", "experience", "interests")}}
    else:
        if tier in ("T2", "T3"):
            env.warnings.append("T2/T3 detector is part of kvnloo/z0 host onboarding; using T0 facts")
        merged = base
    prof = profile_min.apply_tier(merged, tier, prefs_interests=prefs.get("interests"))
    errors = schemas.validate(prof, "profile")
    if errors:
        env.warnings.append(f"profile invalid ({errors[0]}); using T0 only")
        prof = profile_min.apply_tier(env.profile_t0(), "T0", prefs_interests=prefs.get("interests"))
    return prof, tier


def _done_count() -> int:
    return len({r.get("task") for r in _receipts() if r.get("state") == "S7_pr_ready"
                and (r.get("data") or {}).get("opened")})


def _live_state(env: Env, doc: dict[str, Any]) -> tuple[dict[str, int] | None, dict[int, str] | None]:
    """Read-only claim + issue state. Never cached (spec §2.4 step 6)."""
    if not env.gh.available():
        return None, None
    try:
        rows = env.gh.issues(state="all")
    except GHError as exc:
        env.warnings.append(f"gh read failed: {exc}")
        return None, None
    states = {r["number"]: r["state"].upper() for r in rows}
    labels = {r["number"]: set(r["labels"]) for r in rows}
    live: dict[str, int] = {}
    trusted = C.trusted_authors(env.gh.repo)
    now = env.now()
    for t in doc["tasks"]:
        n = t.get("issue")
        if n and "claimed" in labels.get(n, set()):
            try:
                comments = env.gh.issue(n)["comments"]
            except GHError:
                live[t["id"]] = 10**6  # unknown -> treat as taken; never offer a possibly-held task
                continue
            live[t["id"]] = len(C.active_leases(comments, trusted, now=now, task=t["id"]))
    return live, states


def _load_doc(env: Env) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[str]]:
    try:
        doc, vocab = P.load_priorities(env.root), P.load_vocab(env.root)
    except Exception as exc:  # noqa: BLE001
        return None, None, [f"{type(exc).__name__}: {exc}"]
    errors = P.check_priorities(doc, vocab, repo_root=env.root)
    return (doc, vocab, errors)


def _task(doc: dict[str, Any] | None, task_id: str) -> dict[str, Any] | None:
    return next((t for t in (doc or {}).get("tasks", []) if t["id"] == task_id), None)


# ---------------------------------------------------------------- commands

def cmd_suggest(env: Env, *, llm: str = "none", profile_mode: str = "minimal") -> dict[str, Any]:
    A.check_llm_choice(llm)
    prefs, pw = St.load_prefs()
    env.warnings += pw
    prof, tier = _profile(env, profile_mode, prefs)
    sha = env.sha
    doc, vocab, errors = _load_doc(env)
    session = St.load_session()
    onboard_mode = session.get("onboard_mode", "unknown")
    if doc is not None and not errors:
        live, issue_states = _live_state(env, doc)
        idx = []
        try:
            idx = (P.load_yaml(env.root / ".z0" / "receipts-index.yaml") or {}).get("receipts") or []
        except Exception:  # noqa: BLE001
            env.warnings.append("receipts-index.yaml unreadable; uniq_hw assumes no receipts")
        result = A.align(doc, vocab, prof, prefs, done_count=_done_count(), onboard_mode=onboard_mode,
                         live_claims=live, issue_states=issue_states, receipts_index=idx)
        psha = P.file_sha256(P.priorities_path(env.root))
    else:
        env.warnings.append("no valid .z0/priorities.yaml: " + "; ".join(errors[:3]))
        issues: list[dict[str, Any]] = []
        if env.gh.available():
            try:
                for label in A.FALLBACK_LABELS:
                    issues += env.gh.issues(label=label, state="open")
            except GHError as exc:
                env.warnings.append(f"gh read failed: {exc}")
        result = A.fallback_from_labels(list({i["number"]: i for i in issues}.values()))
        psha = None
    env.warnings += result["warnings"]
    llm_reason = None
    if llm != "none":
        llm_reason = "LLM tie-break is not available yet (CW6); deterministic result used"
        if len(result.get("tie_candidates") or []) > 1:
            env.warnings.append(llm_reason)
    sug = A.to_suggestion(result, sha=sha, priorities_sha256=psha, profile_tier=tier, llm="none",
                          llm_reason=llm_reason)
    root = St.home()
    St.write_json(root / "profile.json", prof)
    St.write_json(root / "suggestion.json", sug)
    text = json.dumps(sug, sort_keys=True)
    rec = _write(env, state="S3_suggested", command=f"z0int contribute suggest --llm={llm} --profile={profile_mode}",
                 code=0, output=text, result="pass", data={"pick": sug["pick"]["task"]})
    pick = sug["pick"]
    alts = ", ".join(a["task"] for a in sug["alternatives"]) or "none"
    question = f"Claim {pick['task']}, choose an alternative ({alts}), or stop?"
    return envelope(env, state="S3_suggested", task=None, kind="ask_human",
                    command=f"z0int contribute claim {pick['task']}", why=question, needs_human=[question],
                    receipts=[rec], data={"suggestion": sug}, onboard_mode=onboard_mode)


def cmd_smoke(env: Env) -> dict[str, Any]:
    ok, parts = smoke.run_smoke(env.root, env.runner)
    out_text = json.dumps(parts, sort_keys=True)
    rec = _write(env, state="S2_smoke_passed", command="z0int contribute smoke", code=0 if ok else 1,
                 output=out_text, result="pass" if ok else "fail", parts=parts)
    if not ok:
        bad = [p["name"] for p in parts if p["exit"] != 0 or p.get("golden_match") is False]
        return envelope(env, state=_current(env)[0], task=None, kind="blocked", command="z0int contribute smoke --json",
                        why=f"smoke failed: {', '.join(bad)} (see receipt)", receipts=[rec], data={"parts": parts})
    return envelope(env, state=_current(env)[0], task=None, kind="run", command="z0int contribute suggest --json",
                    why="smoke passed on this sha", receipts=[rec], data={"parts": parts})


def _current(env: Env) -> tuple[str, str | None, Path | None]:
    session = St.load_session()
    task = session.get("task")
    lease = St.read_lease(task, env.now()) if task else None
    wt = Path(session["worktrees"][task]) if task and task in (session.get("worktrees") or {}) else None
    wt_sha = git_head(wt) if wt and wt.exists() else None
    state = St.derive_state(_receipts(), repo_sha=env.sha, task=task, lease_active=lease is not None,
                            worktree_sha=wt_sha)
    return state, task, wt if wt_sha else None


def _bootstrap(env: Env) -> tuple[bool, str]:
    """S0: interpreter, venv, z0 hub, doctor. Returns (ok, doctor output)."""
    py = [sys.executable, "-m", "z0int", "doctor", "--json"]
    code, out = env.runner(py, env.root, smoke.offline_env(env.root))
    facts = {"python": sys.version.split()[0], "venv": sys.prefix != sys.base_prefix,
             "z0_hub": _z0_home() is not None, "doctor_exit": code}
    _write(env, state="S0_bootstrap", command="z0int doctor --json", code=code, output=out,
           result="pass" if code == 0 else "fail", data=facts)
    return code == 0, out


def _onboard(env: Env) -> tuple[bool, str]:
    ok, text, z0_sha = _z0_onboard_ok()
    if ok:
        _write(env, state="S1_onboarded", command="z0 onboard (host)", code=0, output=text, result="pass",
               onboard_mode="full", z0_sha=z0_sha)
        mode = "full"
    else:
        argv = [sys.executable, "-m", "z0int", "onboard", "--dry-run", "--skip-evolution-lab", "--json"]
        code, out = env.runner(argv, env.root, smoke.offline_env(env.root))
        mode = "repo_only"
        _write(env, state="S1_onboarded", command="z0int onboard --dry-run --skip-evolution-lab --json",
               code=code, output=out, result="pass" if code == 0 else "fail", onboard_mode=mode)
        env.warnings.append(FULL_PATH_HINT)
        if code != 0:
            return False, mode
    session = St.load_session()
    session["onboard_mode"] = mode
    St.save_session(session)
    return True, mode


def cmd_run(env: Env, *, llm: str = "none", profile_mode: str = "minimal") -> dict[str, Any]:
    """Advance through every C0 step up to the first human gate."""
    if env.sha is None:
        return envelope(env, state="none", task=None, kind="blocked", command="./scripts/bootstrap.sh",
                        why="not inside a z0intelligence git checkout")
    receipts = _receipts()
    if St.latest(receipts, "S0_bootstrap", sha=env.sha) is None:
        ok, _ = _bootstrap(env)
        if not ok:
            return envelope(env, state="none", task=None, kind="blocked", command="z0int doctor",
                            why="doctor reports blocking items; fix them (z0int doctor) and re-run")
    if St.latest(_receipts(), "S1_onboarded", sha=env.sha) is None:
        ok, mode = _onboard(env)
        if not ok:
            return envelope(env, state="S0_bootstrap", task=None, kind="blocked",
                            command="z0int onboard --dry-run --skip-evolution-lab --json",
                            why="repo-only onboarding dry run failed", onboard_mode=mode)
    if St.latest(_receipts(), "S2_smoke_passed", sha=env.sha) is None:
        out = cmd_smoke(env)
        if out["next_action"]["kind"] == "blocked":
            return out
    state, task, wt = _current(env)
    if state == "S2_smoke_passed":
        return cmd_suggest(env, llm=llm, profile_mode=profile_mode)
    if state == "S4_claimed" and task:
        return cmd_start(env, task)
    return cmd_status(env)


def cmd_status(env: Env) -> dict[str, Any]:
    state, task, wt = _current(env)
    session = St.load_session()
    sug = St.read_json(St.home() / "suggestion.json")
    data = {"leases": St.active_leases(env.now()), "worktree": str(wt) if wt else None,
            "receipts_dir": str(St.home() / "receipts")}
    if sug and state == "S3_suggested":
        data["suggestion"] = sug
    kind, command, why, needs = "run", "z0int contribute --json", "advance the state machine", []
    if state == "S3_suggested" and sug:
        q = f"Claim {sug['pick']['task']}, choose an alternative, or stop?"
        kind, command, why, needs = "ask_human", f"z0int contribute claim {sug['pick']['task']}", q, [q]
    elif state == "S4_claimed":
        kind, command, why = "run", f"z0int contribute start {task}", "local lease held; make the task worktree"
    elif state == "S5_working":
        kind, command = "run", f"z0int contribute evidence {task}"
        why = f"work only in {wt}; commit, then record evidence on the exact HEAD"
    elif state == "S6_evidenced":
        kind, command, why = "run", f"z0int contribute pr {task}", "evidence holds on HEAD; write the PR body"
    elif state == "S7_pr_ready":
        q = f"Push contrib branch to YOUR fork and open a DRAFT PR for {task}? (public post)"
        kind, command, why, needs = "ask_human", f"z0int contribute pr {task} --open --yes", q, [q]
    return envelope(env, state=state, task=task, kind=kind, command=command, why=why, needs_human=needs,
                    data=data, onboard_mode=session.get("onboard_mode"))


def _claimable_or_block(env: Env, task_id: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    doc, _vocab, errors = _load_doc(env)
    t = _task(doc, task_id) if not errors else None
    if t is None:
        return None, envelope(env, state=_current(env)[0], task=None, kind="blocked", command="z0int contribute suggest",
                              why=f"{task_id} is not a task in .z0/priorities.yaml (do not invent tasks)")
    if not t["newcomer"] or t["needs_owner_approval"] or t["status"] != "open":
        return None, envelope(env, state=_current(env)[0], task=None, kind="blocked", command="z0int contribute suggest",
                              why=f"{task_id} is not open to new contributors")
    return t, None


def _approved(env: Env, yes: bool, question: str) -> bool:
    if yes:
        return True
    if env.tty:
        return env.ask(f"{question} [y/N] ").strip().lower() in ("y", "yes")
    return False


def cmd_claim(env: Env, task_id: str, *, post: bool = False, yes: bool = False,
              login: str | None = None) -> dict[str, Any]:
    t, blocked = _claimable_or_block(env, task_id)
    if blocked:
        return blocked
    taken, lease = St.take_lease(task_id, harness=env.harness, now=env.now())
    session = St.load_session()
    if not taken and session.get("task") != task_id:
        return envelope(env, state=_current(env)[0], task=None, kind="blocked",
                        command=f"z0int contribute release {task_id}",
                        why=f"{task_id} is already leased on this host by {lease.get('harness')} at {lease.get('ts')}")
    recs = []
    if taken:
        session["task"] = task_id
        St.save_session(session)
        recs.append(_write(env, state="S4_claimed", command=f"z0int contribute claim {task_id}", code=0,
                           output=json.dumps(lease, sort_keys=True), result="pass", task=task_id))
    needs_public = bool(t["issue"]) and C.claim_limit(t["claim"]) is not None
    if not needs_public:
        return envelope(env, state=_current(env)[0], task=task_id, kind="run",
                        command=f"z0int contribute start {task_id}", why="local lease taken; no public claim needed",
                        receipts=recs)
    text = C.claim_text(task_id, harness=env.harness, hw=env.host_class())
    ask = f"post claim comment on #{t['issue']} (public post): {text}"
    if not post:
        return envelope(env, state=_current(env)[0], task=task_id, kind="ask_human",
                        command=f"z0int contribute claim {task_id} --post --yes",
                        why="local lease taken. You may work locally now; the PR step needs the public claim.",
                        needs_human=[ask], receipts=recs, data={"claim_text": text})
    prefs, _ = St.load_prefs()
    if prefs.get("github_post") == "never":
        return envelope(env, state=_current(env)[0], task=task_id, kind="ask_human", command=None,
                        why="prefs.github_post is 'never': post the claim text yourself", needs_human=[ask],
                        receipts=recs, data={"claim_text": text})
    if not _approved(env, yes, f"Post `{text}` on issue #{t['issue']} as you?"):
        return envelope(env, state=_current(env)[0], task=task_id, kind="ask_human",
                        command=f"z0int contribute claim {task_id} --post --yes",
                        why="a public post needs your explicit yes", needs_human=[ask], receipts=recs,
                        data={"claim_text": text})
    return _post_and_check(env, t, text, login=login, receipts=recs)


def _post_and_check(env: Env, t: dict[str, Any], text: str, *, login: str | None,
                    receipts: list[str]) -> dict[str, Any]:
    task_id, issue = t["id"], int(t["issue"])
    login = login or St.load_prefs()[0].get("login") or env.gh.login()
    if not login:
        return envelope(env, state=_current(env)[0], task=task_id, kind="blocked", command="gh auth login",
                        why="cannot post: no GitHub login (gh auth login, or set login in prefs.yaml)",
                        receipts=receipts)
    try:
        before = len(env.gh.issue(issue)["comments"])
        env.gh.comment(issue, text)
    except GHError as exc:
        return envelope(env, state=_current(env)[0], task=task_id, kind="blocked", command=None,
                        why=f"posting the claim failed: {exc}", receipts=receipts)
    trusted = C.trusted_authors(env.gh.repo)
    deadline = env.poll_seconds
    waited = 0.0
    outcome = "pending"
    while True:
        try:
            comments = env.gh.issue(issue)["comments"]
            outcome = C.collision_outcome(comments, login=login, task=task_id, trusted=trusted,
                                          now=env.now(), after_index=before)
        except GHError as exc:
            env.warnings.append(f"claim poll failed: {exc}")
        if outcome != "pending" or waited >= deadline:
            break
        step = min(5.0, max(deadline - waited, 0.0)) or 1.0
        env.sleep(step)
        waited += step
    session = St.load_session()
    session.setdefault("public_claims", {})[task_id] = {"login": login, "outcome": outcome}
    St.save_session(session)
    if outcome == "won":
        return envelope(env, state=_current(env)[0], task=task_id, kind="run",
                        command=f"z0int contribute start {task_id}", why="public claim accepted by the claim bot",
                        receipts=receipts, data={"claim": "won", "login": login})
    if outcome == "lost":
        St.release_lease(task_id)
        session = St.load_session()
        session.pop("task", None)
        St.save_session(session)
        receipts.append(_write(env, state="released", command=f"z0int contribute claim {task_id} --post",
                               code=1, output=text, result="fail", task=task_id,
                               data={"reason": "claim collision"}))
        sug = St.read_json(St.home() / "suggestion.json") or {}
        nxt = next((a["task"] for a in sug.get("alternatives", []) if a["task"] != task_id), A.FEEDBACK_TASK)
        q = f"{task_id} was claimed by someone else. Claim {nxt} instead, or stop?"
        return envelope(env, state=_current(env)[0], task=None, kind="ask_human",
                        command=f"z0int contribute claim {nxt}", why=q, needs_human=[q], receipts=receipts,
                        data={"claim": "lost", "next_alternative": nxt})
    env.warnings.append("claim pending maintainer (no claim-bot answer yet)")
    return envelope(env, state=_current(env)[0], task=task_id, kind="run", command=f"z0int contribute start {task_id}",
                    why="claim posted; the bot has not answered. Work on the local lease; the PR step re-checks.",
                    receipts=receipts, data={"claim": "pending", "login": login})


def cmd_release(env: Env, task_id: str) -> dict[str, Any]:
    dropped = St.release_lease(task_id)
    session = St.load_session()
    if session.get("task") == task_id:
        session.pop("task", None)
        St.save_session(session)
    recs = []
    if dropped:
        recs.append(_write(env, state="released", command=f"z0int contribute release {task_id}", code=0,
                           output=task_id, result="pass", task=task_id))
    return envelope(env, state=_current(env)[0], task=None, kind="run", command="z0int contribute --json",
                    why=f"local lease on {task_id} {'released' if dropped else 'was not held'}", receipts=recs)


def _branch(task_id: str, login: str | None) -> str:
    return f"contrib/{task_id}-{(login or 'anon').lower()}"


def _shell_env(worktree: Path) -> dict[str, str]:
    return smoke.offline_env(worktree)


def cmd_start(env: Env, task_id: str, *, baseline: bool = True) -> dict[str, Any]:
    t, blocked = _claimable_or_block(env, task_id)
    if blocked:
        return blocked
    if St.read_lease(task_id, env.now()) is None:
        return envelope(env, state=_current(env)[0], task=None, kind="blocked", command=f"z0int contribute claim {task_id}",
                        why="take the local lease first")
    base = contributor_base(default_branch(env.root))
    prefs, _ = St.load_prefs()
    branch = _branch(task_id, prefs.get("login"))
    wt = St.home() / "worktrees" / task_id
    session = St.load_session()
    recs: list[str] = []
    if not (wt / ".git").exists():
        code, ref_out = _git(["rev-parse", "--verify", f"origin/{base}"], env.root)
        start_ref = f"origin/{base}" if code == 0 else base
        if code != 0:
            env.warnings.append(f"origin/{base} not found locally; branching from local {base} (git fetch origin first)")
        wt.parent.mkdir(parents=True, exist_ok=True)
        code, out = _git(["worktree", "add", "-b", branch, str(wt), start_ref], env.root)
        if code != 0:
            code, out = _git(["worktree", "add", str(wt), branch], env.root)
        if code != 0:
            return envelope(env, state=_current(env)[0], task=task_id, kind="blocked", command=None,
                            why=f"git worktree add failed: {out[:200]}")
        recs.append(_write(env, state="S5_working", command=f"git worktree add -b {branch} <worktree> {start_ref}",
                           code=0, output=out, result="pass", task=task_id, sha=git_head(wt),
                           data={"branch": branch, "base": base}))
        if baseline and t.get("verifier"):
            code, out = env.runner(["bash", "-c", t["verifier"]], wt, _shell_env(wt))
            recs.append(_write(env, state="baseline", command=t["verifier"], code=code, output=out,
                               result="pass" if code == 0 else "fail", task=task_id, sha=git_head(wt)))
    session.setdefault("worktrees", {})[task_id] = str(wt)
    session["task"] = task_id
    session["repo_root"] = str(env.root)
    session.setdefault("bases", {})[task_id] = base
    St.save_session(session)
    why = f"work only in {wt} (branch {branch}); commit, then run evidence"
    if t.get("smoke") and (t["hardware"]["gpu_vram_gib"] > 0 or t["kind"] == "z0evals"):
        why += f". Task smoke first: (cd {wt} && {t['smoke']})"
    return envelope(env, state=_current(env)[0], task=task_id, kind="run", command=f"z0int contribute evidence {task_id}",
                    why=why, receipts=recs, data={"worktree": str(wt), "branch": branch, "base": base})


def cmd_evidence(env: Env, task_id: str) -> dict[str, Any]:
    t, blocked = _claimable_or_block(env, task_id)
    if blocked:
        return blocked
    wt = St.home() / "worktrees" / task_id
    head = git_head(wt) if wt.exists() else None
    if head is None:
        return envelope(env, state=_current(env)[0], task=task_id, kind="blocked", command=f"z0int contribute start {task_id}",
                        why="no task worktree yet")
    code, dirty = _git(["status", "--porcelain", "--untracked-files=no"], wt)
    if code != 0 or dirty:
        return envelope(env, state=_current(env)[0], task=task_id, kind="blocked", command=None,
                        why="commit your changes first: evidence binds to the exact HEAD, not a dirty tree")
    vcode, vout = env.runner(["bash", "-c", t["verifier"]], wt, _shell_env(wt))
    parts = [{"name": "verifier", "command": t["verifier"], "exit": vcode, "output_sha256": St.output_sha256(vout)}]
    parts += smoke.product_smoke(wt, env.runner)
    ok = smoke.smoke_passed(parts)
    rec = _write(env, state="S6_evidenced", command=f"z0int contribute evidence {task_id}", code=0 if ok else 1,
                 output=json.dumps(parts, sort_keys=True), result="pass" if ok else "fail", task=task_id,
                 sha=head, parts=parts)
    if not ok:
        bad = [p["name"] for p in parts if p["exit"] != 0 or p.get("golden_match") is False]
        return envelope(env, state=_current(env)[0], task=task_id, kind="blocked", command=f"z0int contribute evidence {task_id}",
                        why=f"evidence failed on {head[:12]}: {', '.join(bad)}", receipts=[rec], data={"parts": parts})
    return envelope(env, state=_current(env)[0], task=task_id, kind="run", command=f"z0int contribute pr {task_id}",
                    why=f"verifier + product smoke pass on {head[:12]}", receipts=[rec], data={"parts": parts})


def pr_body(*, task: dict[str, Any], login: str | None, head: str, base_rev: str | None, smoke_r: dict | None,
            evidence_r: dict, baseline_r: dict | None, host_class: str, harness: str, model: str | None,
            repo: str) -> str:
    """PR body: task, receipts, hardware class, harness/model, credit. Never the profile (§3.7)."""
    tid, n = task["id"], task.get("issue")
    rows = []
    if smoke_r:
        rows.append(("S2 smoke (checkout)", smoke_r))
    if baseline_r:
        rows.append(("baseline (task base)", baseline_r))
    rows.append(("S6 verifier + product smoke", evidence_r))
    table = "\n".join(
        f"| {label} | `{r['command']}` | {r['exit']} | {r['result']} | `{r['sha'][:12]}` | `{r['output_sha256'][:16]}` |"
        for label, r in rows
    )
    red = ""
    if baseline_r:
        red = f"baseline {baseline_r['sha'][:12]}: {task['verifier']} exit={baseline_r['exit']}"
    green = f"head {head[:12]}: {task['verifier']} exit=0"
    credit = f"Credit: @{login}" if login else "Credit: (contributor handle not given)"
    issue_line = f"Refs #{n}" if n else f"Task `{tid}` (no issue)"
    issue_field = n if n else tid
    lines = [
        f"## {tid}: {task['title']}",
        "",
        issue_line,
        "",
        f"Exit criterion: {task['exit_criterion']}",
        "",
        "### Receipts",
        "",
        "| step | command | exit | result | sha | output sha256 |",
        "|---|---|---|---|---|---|",
        table,
        "",
        f"Hardware class: `{host_class}`  ",
        f"Harness / model: `{harness}` / `{model or 'unspecified'}`",
        "",
        credit,
        "",
        "```yaml",
        f"issue: {issue_field}",
        f"base_revision: {base_rev or ''}",
        f"head_revision: {head}",
        "tests:",
        f"  red: \"{red}\"",
        f"  green: \"{green}\"",
        f"product_smoke: \"doctor + contrastive fixture golden on {head[:12]}\"",
        "```",
        "",
        "- [ ] I have read and agree to the z0intelligence Contributor License Agreement (the CLA bot checks this PR).",
        "",
        f"Generated by `z0int contribute pr` ({repo} contributor layer).",
    ]
    return "\n".join(lines) + "\n"


def _public_claim_ok(env: Env, t: dict[str, Any], login: str | None) -> tuple[bool | None, str]:
    """Re-read the public claim (never cached). None = unknown."""
    if not t.get("issue") or C.claim_limit(t["claim"]) is None or t["claim"].startswith("parallel"):
        return True, "no exclusive public claim needed"
    if not login or not env.gh.available():
        return None, "claim state unknown (no gh or no login)"
    try:
        comments = env.gh.issue(int(t["issue"]))["comments"]
    except GHError as exc:
        return None, f"claim state unknown ({exc})"
    act = C.active_leases(comments, C.trusted_authors(env.gh.repo), now=env.now(), task=t["id"])
    return (login.lower() in act), "public claim active" if login.lower() in act else "no active public claim"


def cmd_pr(env: Env, task_id: str, *, open_: bool = False, yes: bool = False, login: str | None = None,
           model: str | None = None, remote: str = "fork") -> dict[str, Any]:
    t, blocked = _claimable_or_block(env, task_id)
    if blocked:
        return blocked
    wt = St.home() / "worktrees" / task_id
    head = git_head(wt) if wt.exists() else None
    receipts = _receipts()
    ev = St.latest(receipts, "S6_evidenced", sha=head, task=task_id) if head else None
    if ev is None:
        return envelope(env, state=_current(env)[0], task=task_id, kind="blocked",
                        command=f"z0int contribute evidence {task_id}",
                        why="no passing evidence receipt on the worktree HEAD (new commits need new evidence)")
    prefs, _ = St.load_prefs()
    login = login or prefs.get("login") or (env.gh.login() if env.gh.available() else None)
    claim_ok, claim_why = _public_claim_ok(env, t, login)
    if not claim_ok:
        text = C.claim_text(task_id, harness=env.harness, hw=env.host_class())
        return envelope(env, state=_current(env)[0], task=task_id, kind="ask_human",
                        command=f"z0int contribute claim {task_id} --post --yes",
                        why=f"exclusive task: the PR step needs your active public claim ({claim_why})",
                        needs_human=[f"post claim comment on #{t['issue']} (public post): {text}"])
    session = St.load_session()
    base = contributor_base(session.get("bases", {}).get(task_id) or default_branch(env.root))
    code, base_rev = _git(["merge-base", "HEAD", f"origin/{base}"], wt)
    if code != 0:
        code, base_rev = _git(["merge-base", "HEAD", base], wt)
    smoke_r = St.latest(receipts, "S2_smoke_passed", sha=env.sha)
    baseline_r = next((r for r in reversed(receipts) if r.get("state") == "baseline" and r.get("task") == task_id), None)
    body = pr_body(task=t, login=login, head=head, base_rev=base_rev if code == 0 else None, smoke_r=smoke_r,
                   evidence_r=ev, baseline_r=baseline_r, host_class=env.host_class(), harness=env.harness,
                   model=model, repo=env.gh.repo)
    body_path = St.write_private(St.home() / "pr" / f"{task_id}.md", body)
    branch = _branch(task_id, prefs.get("login") or None)
    code_b, cur_branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], wt)
    branch = cur_branch if code_b == 0 and cur_branch.startswith("contrib/") else branch
    data = {"body_path": str(body_path), "base": base, "branch": branch, "claim": claim_why}
    if not open_:
        rec = _write(env, state="S7_pr_ready", command=f"z0int contribute pr {task_id}", code=0, output=body,
                     result="pass", task=task_id, sha=head, data={**data, "opened": False})
        q = f"Push {branch} to YOUR fork and open a DRAFT PR against {base}? (public post)"
        return envelope(env, state=_current(env)[0], task=task_id, kind="ask_human",
                        command=f"z0int contribute pr {task_id} --open --yes", why=q, needs_human=[q],
                        receipts=[rec], data=data)
    if not _approved(env, yes, f"Push {branch} to your fork and open a draft PR against {base}?"):
        return envelope(env, state=_current(env)[0], task=task_id, kind="ask_human",
                        command=f"z0int contribute pr {task_id} --open --yes",
                        why="pushing and opening a PR need your explicit yes", data=data)
    if not login:
        return envelope(env, state=_current(env)[0], task=task_id, kind="blocked", command="gh auth login",
                        why="opening a PR needs your GitHub login", data=data)
    code, url = _git(["remote", "get-url", remote], wt)
    origin_slug = env.gh.repo.lower()
    if code != 0 or origin_slug in url.lower().replace(":", "/"):
        return envelope(env, state=_current(env)[0], task=task_id, kind="blocked",
                        command=f"git -C {wt} remote add {remote} https://github.com/{login}/{origin_slug.split('/')[1]}.git",
                        why=f"push target must be your fork (remote '{remote}'); the CLI never pushes to origin",
                        data=data)
    code, out = _git(["push", remote, f"HEAD:refs/heads/{branch}"], wt)
    if code != 0:
        return envelope(env, state=_current(env)[0], task=task_id, kind="blocked", command=None,
                        why=f"push to your fork failed: {out[:200]}", data=data)
    try:
        url = env.gh.pr_create(base=base, head=f"{login}:{branch}", title=f"{task_id}: {t['title']}"[:240],
                               body_file=str(body_path))
    except GHError as exc:
        return envelope(env, state=_current(env)[0], task=task_id, kind="blocked", command=None,
                        why=f"gh pr create failed: {exc}", data=data)
    rec = _write(env, state="S7_pr_ready", command=f"z0int contribute pr {task_id} --open", code=0, output=body,
                 result="pass", task=task_id, sha=head, data={**data, "opened": True, "url": url})
    return envelope(env, state=_current(env)[0], task=task_id, kind="done", command=None,
                    why=f"draft PR opened: {url}. The owner reviews and merges.", receipts=[rec],
                    data={**data, "url": url})


FEEDBACK_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["schema", "onboard_mode", "steps", "blockers", "would_recommend"],
    "properties": {
        "schema": {"const": "z0.contribute.feedback.v1"},
        "onboard_mode": {"enum": ["full", "repo_only", "unknown"]},
        "harness": {"type": "string", "pattern": "^[a-z0-9][a-z0-9._-]*$"},
        "steps": {
            "type": "array", "minItems": 1,
            "items": {
                "type": "object", "additionalProperties": False, "required": ["step", "minutes", "friction"],
                "properties": {
                    "step": {"type": "string", "minLength": 1, "maxLength": 80},
                    "minutes": {"type": "number", "minimum": 0, "maximum": 600},
                    "friction": {"type": "integer", "minimum": 0, "maximum": 3},
                    "note": {"type": "string", "maxLength": 280},
                },
            },
        },
        "blockers": {"type": "array", "items": {"type": "string", "maxLength": 280}},
        "would_recommend": {"type": ["boolean", "null"]},
    },
}


def cmd_feedback(env: Env, *, check: str | None = None) -> dict[str, Any]:
    path = Path(check) if check else St.home() / "feedback.json"
    if check is not None or path.exists():
        doc = St.read_json(path)
        errors = schemas.validate_instance(doc, FEEDBACK_SCHEMA) if doc is not None else ["unreadable JSON"]
        if check is not None or errors:
            kind = "done" if not errors else "blocked"
            return envelope(env, state=_current(env)[0], task=A.FEEDBACK_TASK, kind=kind, command=None,
                            why="feedback.json is valid" if not errors else f"fill in {path}: {errors[0]}",
                            data={"path": str(path), "errors": errors})
    if not path.exists():
        template = {"schema": "z0.contribute.feedback.v1",
                    "onboard_mode": St.load_session().get("onboard_mode", "unknown"),
                    "harness": env.harness, "steps": [], "blockers": [], "would_recommend": None}
        St.write_json(path, template)
    return envelope(env, state=_current(env)[0], task=A.FEEDBACK_TASK, kind="ask_human",
                    command="z0int contribute feedback --check",
                    why=f"fill in {path} (one row per onboarding step: minutes, friction 0-3, short note)",
                    needs_human=[f"write your onboarding friction report in {path}"], data={"path": str(path)})


# ---------------------------------------------------------------- argparse

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="z0int contribute", description=__doc__.split("\n\n")[0])
    p.add_argument("--json", action="store_true", dest="as_json")
    p.add_argument("--repo", default=None, help="z0intelligence checkout (default: from cwd)")
    p.add_argument("--llm", choices=("none", "local", "auto"), default="none")
    p.add_argument("--profile", choices=("none", "minimal", "full"), default="minimal", dest="profile_mode")
    sub = p.add_subparsers(dest="sub")

    def js(sp: argparse.ArgumentParser) -> argparse.ArgumentParser:
        sp.add_argument("--json", action="store_true", dest="as_json", default=argparse.SUPPRESS)
        return sp

    js(sub.add_parser("status", help="current state, receipts, active claim (read-only)"))
    js(sub.add_parser("smoke", help="offline smoke eval; writes a receipt"))
    sg = js(sub.add_parser("suggest", help="run the alignment engine; writes suggestion.json"))
    sg.add_argument("--llm", choices=("none", "local", "auto"), default=argparse.SUPPRESS)
    sg.add_argument("--profile", choices=("none", "minimal", "full"), default=argparse.SUPPRESS, dest="profile_mode")
    cl = js(sub.add_parser("claim", help="local lease; --post prints/posts the public claim (needs your yes)"))
    cl.add_argument("task")
    cl.add_argument("--post", action="store_true")
    cl.add_argument("--yes", action="store_true", help="the human approved this exact public post")
    cl.add_argument("--login", default=None)
    rl = js(sub.add_parser("release", help="drop the local lease"))
    rl.add_argument("task")
    st = js(sub.add_parser("start", help="make the task worktree from the default branch"))
    st.add_argument("task")
    st.add_argument("--no-baseline", action="store_true")
    ev = js(sub.add_parser("evidence", help="verifier + product smoke on the worktree HEAD"))
    ev.add_argument("task")
    pr = js(sub.add_parser("pr", help="write the PR body; --open pushes to YOUR fork + draft PR (needs your yes)"))
    pr.add_argument("task")
    pr.add_argument("--open", action="store_true", dest="open_")
    pr.add_argument("--yes", action="store_true", help="the human approved this push + PR")
    pr.add_argument("--login", default=None)
    pr.add_argument("--model", default=None, help="model name for the PR body (optional)")
    pr.add_argument("--remote", default="fork", help="git remote of YOUR fork (never origin)")
    fb = js(sub.add_parser("feedback", help="the always-available task: onboarding friction report"))
    fb.add_argument("--check", nargs="?", const="", default=None, metavar="PATH")
    return p


def main(argv: list[str] | None = None, env: Env | None = None) -> int:
    args = build_parser().parse_args(argv)
    if env is None:
        start = Path(args.repo).resolve() if args.repo else Path.cwd()
        root = find_repo_root(start) or start
        env = Env(root=root, gh=GH(repo_slug(root)), tty=sys.stdin.isatty() and sys.stdout.isatty())
    sub = args.sub
    try:
        if sub is None:
            out = cmd_run(env, llm=args.llm, profile_mode=args.profile_mode)
        elif sub == "status":
            out = cmd_status(env)
        elif sub == "smoke":
            out = cmd_smoke(env)
        elif sub == "suggest":
            out = cmd_suggest(env, llm=args.llm, profile_mode=args.profile_mode)
        elif sub == "claim":
            out = cmd_claim(env, args.task, post=args.post, yes=args.yes, login=args.login)
        elif sub == "release":
            out = cmd_release(env, args.task)
        elif sub == "start":
            out = cmd_start(env, args.task, baseline=not args.no_baseline)
        elif sub == "evidence":
            out = cmd_evidence(env, args.task)
        elif sub == "pr":
            out = cmd_pr(env, args.task, open_=args.open_, yes=args.yes, login=args.login, model=args.model,
                         remote=args.remote)
        elif sub == "feedback":
            check = None if args.check is None else (args.check or str(St.home() / "feedback.json"))
            out = cmd_feedback(env, check=check)
        else:  # pragma: no cover
            raise SystemExit(2)
    except ValueError as exc:
        out = envelope(env, state="none", task=None, kind="blocked", command=None, why=str(exc))
    _print(out, args.as_json)
    return _exit(out)


if __name__ == "__main__":
    raise SystemExit(main())
