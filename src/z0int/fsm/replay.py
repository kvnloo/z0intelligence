"""Deterministic replay checker for the work-item lifecycle.

Reads real evidence, maps it to statechart events, runs them through
`work_item.step`, and reports every refused move and every law the
evidence breaks:

* GitHub PRs (`gh pr list`, check runs on merge commits)
* running services: listening port -> process cwd -> git head, compared
  with the integration ref each service is declared to serve
* Hermes delegation logs (`.../cache/delegation/live/*/task-*.log`) for
  workers that were interrupted or vanished

Output is a JSON report (also the Company OS overlay contract, see
export.py). The checker only reads: no git writes, no service restarts.
Findings are evidence for the owner, never authorization or
verified_success.

    PYTHONPATH=src python -m z0int.fsm.replay --services examples/fsm/services.groot.json \\
        --repos kvnloo/company-os kvnloo/dash kvnloo/noop --out report.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .work_item import (
    Actor, Avail, Event, Item, Phase, Receipt, Role, needs_kevin, refused, step,
)

OWNER_LOGIN = "kvnloo"
CI = Actor("github-checks", Role.CI)
SERVICE_CHECK = Actor("service-branch-check", Role.CI)
NON_TEST_CHECK = re.compile(r"label|stale|triage|greet|welcome|size|dependabot|receipt", re.I)
DEFAULT_TTL_HOURS = 2.0


# ---------------------------------------------------------------- shell helpers

def sh(cmd: list[str], cwd: str | None = None, timeout: int = 120) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, str(exc)
    return p.returncode, p.stdout.strip()


def git(repo: str, *args: str) -> str | None:
    rc, out = sh(["git", "-C", repo, *args])
    return out if rc == 0 else None


def is_ancestor(repo: str, a: str, b: str) -> bool | None:
    rc, _ = sh(["git", "-C", repo, "merge-base", "--is-ancestor", a, b])
    return {0: True, 1: False}.get(rc)


def count(repo: str, rng: str) -> int | None:
    out = git(repo, "rev-list", "--count", rng)
    return int(out) if out and out.isdigit() else None


def parse_time(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


# ---------------------------------------------------------------- findings

@dataclass
class Finding:
    law: int | None  # statechart law number, None for statechart gaps / info
    severity: str  # violation | indeterminate | candidate (breaks a proposed v1 law) | info
    code: str
    message: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class Trace:
    id: str
    kind: str  # pr | service | worker
    title: str
    events: list[Event] = field(default_factory=list)
    final: Item = field(default_factory=Item)
    findings: list[Finding] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def replay(self, shared_identity: bool = False) -> None:
        s = Item()
        for e in self.events:
            if refused(s, e):
                self.findings.append(diagnose(s, e, shared_identity))
            s = step(s, e)
        self.final = s


def diagnose(s: Item, e: Event, shared_identity: bool) -> Finding:
    """Why did the statechart refuse e in state s? Name the law it guards."""
    a, k, p = e.actor, e.kind, s.phase
    ev = {"from": p.value, "event": k, "actor": asdict(a) if a else None, "sha": e.sha,
          "at": e.at, **e.evidence}
    if k == "Merge" and p is not Phase.ACCEPTED:
        return Finding(1, "violation", "merged_without_owner_acceptance",
                       f"merged to the default branch from {p.value}: no recorded owner acceptance "
                       "of a deployed head"
                       + (" (merged by the owner's account; whether Kevin or an agent pressed merge is "
                          "not observable)" if a and a.id == OWNER_LOGIN else ""), ev)
    if k == "Verify" and p is Phase.VERIFYING and a and s.au and a.id == s.au.id:
        return Finding(2, "indeterminate" if shared_identity else "violation", "self_verified",
                       "verifier is the author", ev)
    if k == "Integrate" and p is Phase.DRAFT_PR:
        if a and s.au and a.id == s.au.id:
            sev = "indeterminate" if shared_identity else "violation"
            return Finding(2, sev, "self_integrated",
                           "integrated by the author's own account"
                           + (" (agents act under the owner's GitHub identity, so who merged is not observable)"
                              if shared_identity else ""), ev)
        r = e.receipt
        if r is None or not r.passed or r.sha != e.sha:
            return Finding(3, "violation", "integrated_without_bound_receipt",
                           "no passing check run on the integration head (merge commit)", ev)
    if k == "Integrate" and p in (Phase.VERIFYING, Phase.IMPLEMENTING):
        return Finding(3, "violation", "integrated_without_head_receipt",
                       "integrated while the head had no passing test receipt "
                       "(checks missing, failing, or finished after the merge)", ev)
    if k == "Deploy" and p is Phase.INTEGRATED:
        return Finding(4, "violation", "deploy_not_integrated_head",
                       "service head differs from the integrated head", ev)
    if k == "Abandon" and p in (Phase.VERIFYING, Phase.DRAFT_PR, Phase.IMPLEMENTING):
        return Finding(None, "info", "closed_outside_statechart",
                       "closed unmerged from a PR phase; v0 has no DraftPR->Abandoned edge (proposed for v1)", ev)
    if k in ("Claim", "Resume") and a and a.role not in (Role.OPUS, Role.SOL):
        return Finding(6, "violation", "non_coder_implements", f"{a.role.value} cannot implement", ev)
    return Finding(None, "info", "refused_event", f"{k} is not a move from {p.value}", ev)


# ---------------------------------------------------------------- GitHub PRs

PR_FIELDS = ("number,title,state,isDraft,headRefName,baseRefName,headRefOid,mergedAt,createdAt,"
             "closedAt,author,mergedBy,mergeCommit,reviews,labels,statusCheckRollup,url")


def fetch_prs(repo: str, limit: int = 300) -> list[dict]:
    rc, out = sh(["gh", "pr", "list", "-R", repo, "--state", "all", "--limit", str(limit),
                  "--json", PR_FIELDS], timeout=180)
    if rc != 0:
        raise RuntimeError(f"gh pr list {repo}: {out[:300]}")
    return json.loads(out)


def default_branch(repo: str) -> str:
    rc, out = sh(["gh", "api", f"repos/{repo}", "--jq", ".default_branch"])
    return out if rc == 0 and out else "main"


def check_runs(repo: str, sha: str) -> list[dict]:
    rc, out = sh(["gh", "api", f"repos/{repo}/commits/{sha}/check-runs?per_page=100",
                  "--jq", "[.check_runs[] | {name, conclusion, completed_at}]"])
    return json.loads(out) if rc == 0 and out else []


def receipt_from_checks(sha: str, checks: Iterable[dict], before: str | None,
                        source: str) -> tuple[Receipt | None, dict]:
    """A passing test receipt bound to sha: every test check concluded
    success/neutral/skipped, at least one succeeded, and all finished by
    `before` (the merge time). GitHub keeps only the latest run per check."""
    tests = [c for c in checks
             if not NON_TEST_CHECK.search(f"{c.get('name', '')} {c.get('workflowName', '')}")]
    if not tests:
        return None, {"why": "no test checks on this sha"}
    cut = parse_time(before)
    failed, late = [], []
    for c in tests:
        concl = (c.get("conclusion") or "").upper()
        done = parse_time(c.get("completedAt") or c.get("completed_at"))
        if concl not in ("SUCCESS", "NEUTRAL", "SKIPPED"):
            failed.append(f"{c.get('name')}={concl or 'PENDING'}")
        if cut and (done is None or done > cut):
            late.append(c.get("name"))
    ok = not failed and not late and any((c.get("conclusion") or "").upper() == "SUCCESS" for c in tests)
    detail = {"checks": len(tests), "failed": failed, "finished_after_merge": late}
    return Receipt(sha, ok, source), detail


def author_actor(login: str) -> tuple[Actor, bool]:
    """(actor, inferred). Agents push as the owner's account, so a PR by
    the owner login is attributed to the Opus lane with shared identity."""
    low = login.lower()
    if "codex" in low or "cursor" in low or "copilot" in low:
        return Actor(login, Role.SOL), True
    return Actor(login, Role.OPUS), True


def owner_accepted(pr: dict) -> bool:
    if any(r.get("state") == "APPROVED" and (r.get("author") or {}).get("login") == OWNER_LOGIN
           for r in pr.get("reviews") or []):
        return True
    return any(l.get("name") in ("owner-accepted", "kevin-accepted") for l in pr.get("labels") or [])


def pr_traces(repo: str, prs: list[dict], default: str, live: dict[str, str],
              local_repo: str | None, fetch_merge_checks: bool = True,
              served_heads: Iterable[str] = ()) -> list[Trace]:
    """live: integration ref name -> head sha currently on that branch."""
    out = []
    for pr in prs:
        login = (pr.get("author") or {}).get("login") or "unknown"
        au, _ = author_actor(login)
        head = pr["headRefOid"]
        t = Trace(f"{repo}#{pr['number']}", "pr", pr["title"],
                  meta={"repo": repo, "number": pr["number"], "url": pr.get("url"),
                        "base": pr["baseRefName"], "head_ref": pr["headRefName"], "head": head,
                        "state": pr["state"], "author": login})
        t.events += [Event("Claim", au, at=pr["createdAt"]), Event("Start", au),
                     Event("Push", au, sha=head), Event("Submit", au)]
        r, why = receipt_from_checks(head, pr.get("statusCheckRollup") or [], pr.get("mergedAt"),
                                     "statusCheckRollup")
        t.meta["head_receipt"] = {**asdict(r), **why} if r else why
        if r is not None:
            t.events.append(Event("Verify", CI, receipt=r))
        merger_login = (pr.get("mergedBy") or {}).get("login") or "unknown"
        if pr["state"] == "MERGED":
            mc = (pr.get("mergeCommit") or {}).get("oid")
            if pr["baseRefName"] == default:
                if owner_accepted(pr):
                    t.meta["owner_acceptance"] = "review/label"
                role = Role.OWNER if merger_login == OWNER_LOGIN else Role.CI
                t.events.append(Event("Merge", Actor(merger_login, role), sha=mc, at=pr["mergedAt"],
                                      evidence={"merge_commit": mc, "base": default,
                                                "merged_by": merger_login,
                                                "acceptance_record": t.meta.get("owner_acceptance")}))
            else:
                # merged into an integration branch
                base = pr["baseRefName"]
                ir = None
                if fetch_merge_checks and mc:
                    ir, _ = receipt_from_checks(mc, check_runs(repo, mc), None, "check-runs(merge commit)")
                t.events.append(Event("Integrate", Actor(merger_login, Role.OPUS), sha=mc,
                                      receipt=ir, at=pr["mergedAt"],
                                      evidence={"base": base, "merge_commit": mc,
                                                "head_receipt": t.meta["head_receipt"]}))
                if local_repo and mc:
                    reach = {ref: is_ancestor(local_repo, mc, sha) for ref, sha in live.items()}
                    t.meta["merge_reaches"] = sorted(k for k, v in reach.items() if v)
                    lines = {default: live.get(default), **{f"served:{h[:8]}": h for h in served_heads}}
                    on = [k for k, h in lines.items() if h and is_ancestor(local_repo, mc, h)]
                    t.meta["on_deployed_or_default"] = on
                    if not on and reach:
                        t.findings.append(Finding(None, "candidate", "stranded_integration",
                                                  f"merged into {base} on {pr['mergedAt'][:10]} but the merge commit "
                                                  f"is on neither {default} nor any running service; live branches "
                                                  f"holding it: {t.meta['merge_reaches'] or 'none'}",
                                                  {"merge_commit": mc, "base": base,
                                                   "merged_at": pr["mergedAt"]}))
        elif pr["state"] == "CLOSED":
            t.events.append(Event("Abandon", Actor(login, Role.OPUS), at=pr.get("closedAt")))
        t.replay(shared_identity=(login == OWNER_LOGIN and merger_login in (OWNER_LOGIN, "unknown")))
        out.append(t)
    return out


# ---------------------------------------------------------------- services

def listening() -> dict[int, int]:
    rc, out = sh(["ss", "-ltnpH"])
    ports: dict[int, int] = {}
    for line in out.splitlines():
        m = re.search(r":(\d+)\s.*pid=(\d+)", line)
        if m:
            ports.setdefault(int(m.group(1)), int(m.group(2)))
    return ports


def served_checkout(pid: int) -> dict[str, Any] | None:
    try:
        cwd = os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return None
    top = git(cwd, "rev-parse", "--show-toplevel")
    if not top:
        return None
    status = git(top, "status", "--porcelain", "--untracked-files=no") or ""
    return {"pid": pid, "cwd": cwd, "toplevel": top, "head": git(top, "rev-parse", "HEAD"),
            "branch": git(top, "rev-parse", "--abbrev-ref", "HEAD"),
            "dirty_files": len([l for l in status.splitlines() if l.strip()])}


_REMOTE_HEADS: dict[str, dict[str, str]] = {}


def remote_heads(repo: str) -> dict[str, str]:
    """Branch heads on origin, read with ls-remote (no fetch into the checkout)."""
    url = git(repo, "remote", "get-url", "origin") or ""
    if url not in _REMOTE_HEADS:
        rc, out = sh(["git", "-C", repo, "ls-remote", "--heads", "origin"])
        _REMOTE_HEADS[url] = {ln.split()[1].removeprefix("refs/heads/"): ln.split()[0]
                              for ln in out.splitlines() if rc == 0 and len(ln.split()) == 2}
    return _REMOTE_HEADS[url]


def ref_sha(repo: str, ref: str) -> str | None:
    """origin/<branch> resolves to the live remote head (must exist locally to
    compare ancestry); other refs resolve in the checkout."""
    if ref.startswith("origin/"):
        sha = remote_heads(repo).get(ref.removeprefix("origin/"))
        if sha and git(repo, "cat-file", "-e", sha + "^{commit}") is not None:
            return sha
        return None
    return git(repo, "rev-parse", "--verify", "-q", ref + "^{commit}")


def missing_prs(repo_path: str, served: str, prs: list[dict], bases: set[str]) -> list[dict]:
    """PRs merged into one of `bases` whose head commit is not in `served`."""
    out = []
    for pr in prs:
        if pr["state"] != "MERGED" or pr["baseRefName"] not in bases:
            continue
        h = pr["headRefOid"]
        anc = is_ancestor(repo_path, h, served)
        if anc is None:
            mc = (pr.get("mergeCommit") or {}).get("oid")
            anc = is_ancestor(repo_path, mc, served) if mc else None
        if anc is False:
            out.append({"number": pr["number"], "base": pr["baseRefName"], "title": pr["title"],
                        "merged_at": pr["mergedAt"]})
    return out


def service_traces(cfg: list[dict], ports: dict[int, int], prs_by_repo: dict[str, list[dict]]) -> list[Trace]:
    out = []
    for svc in cfg:
        t = Trace(f"service:{svc['name']}", "service", f"{svc['name']} :{svc['port']}",
                  meta={"port": svc["port"], "repo": svc["repo"], "integration_ref": svc["integration_ref"]})
        pid = ports.get(svc["port"])
        co = served_checkout(pid) if pid else None
        if co is None:
            t.findings.append(Finding(None, "info", "service_not_running",
                                      f"nothing with a git checkout listens on :{svc['port']}", {}))
            out.append(t)
            continue
        repo_path = co["toplevel"]
        t.meta["served"] = co
        ig = ref_sha(repo_path, svc["integration_ref"])
        t.meta["integration_head"] = ig
        if ig is None:
            t.findings.append(Finding(4, "indeterminate", "integration_ref_missing",
                                      f"{svc['integration_ref']} does not resolve in {repo_path}", {}))
            out.append(t)
            continue
        upstream = git(repo_path, "rev-parse", "--abbrev-ref", svc["integration_ref"] + "@{upstream}")
        t.meta["integration_ref_pushed"] = upstream is not None or svc["integration_ref"].startswith("origin/")
        # The service item: integration head -> deploy (by the service-branch check) -> observe
        built = Actor("integration", Role.OPUS)
        t.events += [Event("Claim", built), Event("Start", built), Event("Push", built, sha=ig),
                     Event("Submit", built), Event("Verify", CI, receipt=Receipt(ig, True, "assumed")),
                     Event("Integrate", SERVICE_CHECK, sha=ig, receipt=Receipt(ig, True, "assumed"))]
        served = co["head"]
        t.events.append(Event("Deploy", SERVICE_CHECK, sha=served,
                              evidence={"served_head": served, "integration_head": ig,
                                        "checkout": repo_path, "branch": co["branch"]}))
        t.replay()
        if t.final.phase is Phase.DEPLOYED and co["dirty_files"]:
            t.findings.append(Finding(4, "violation", "served_tree_dirty",
                                      f"service runs {co['dirty_files']} uncommitted change(s) on top of the "
                                      "integrated head: what runs is not a commit", {"checkout": repo_path}))
        if t.final.phase is not Phase.DEPLOYED:
            behind = count(repo_path, f"{served}..{ig}")
            ahead = count(repo_path, f"{ig}..{served}")
            bases = {svc["integration_ref"].split("/", 1)[-1]} | {r.split("/", 1)[-1] for r in svc.get("must_contain", [])}
            miss = missing_prs(repo_path, served, prs_by_repo.get(svc["repo"], []), bases)
            # replace the generic refusal with the stale-service finding
            t.findings = [f for f in t.findings if f.code != "deploy_not_integrated_head"]
            kind = ("stale" if behind and not ahead else "ahead_unintegrated" if ahead and not behind
                    else "diverged")
            t.findings.append(Finding(4, "violation", f"service_{kind}",
                                      f"serves {co['branch']}@{served[:8]} but {svc['integration_ref']} is "
                                      f"{ig[:8]}: {behind} integrated commit(s) not served, {ahead} served "
                                      "commit(s) not integrated",
                                      {"served_head": served, "integration_head": ig, "behind": behind,
                                       "ahead": ahead, "merged_prs_not_served": miss,
                                       "dirty_files": co["dirty_files"]}))
        for ref in svc.get("must_contain", []):
            sha = ref_sha(repo_path, ref)
            if sha and is_ancestor(repo_path, sha, served) is False:
                n = count(repo_path, f"{served}..{sha}")
                t.findings.append(Finding(4, "violation", "service_missing_line",
                                          f"{ref} has {n} commit(s) the service does not run",
                                          {"ref": ref, "ref_head": sha, "served_head": served,
                                           "merged_prs_not_served": missing_prs(
                                               repo_path, served, prs_by_repo.get(svc["repo"], []),
                                               {ref.split('/', 1)[-1]})}))
        if not t.meta["integration_ref_pushed"]:
            t.findings.append(Finding(5, "violation", "integration_ref_local_only",
                                      f"{svc['integration_ref']} has no upstream: the integrated line exists "
                                      "only in one local checkout", {"ref": svc["integration_ref"]}))
        out.append(t)
    return out


def checkout_sweep(cfg: list[dict], ports: dict[int, int], prs_by_repo: dict[str, list[dict]]) -> list[dict]:
    """Counterfactual: for every local worktree of a served repo, which
    integrated PRs would be missing if a service were started from it."""
    rows, seen = [], set()
    for svc in cfg:
        pid = ports.get(svc["port"])
        co = served_checkout(pid) if pid else None
        if not co or co["toplevel"] in seen:
            continue
        repo_path = co["toplevel"]
        common = git(repo_path, "rev-parse", "--path-format=absolute", "--git-common-dir")
        if common in seen:
            continue
        seen.add(common)
        bases = {svc["integration_ref"].split("/", 1)[-1]} | {r.split("/", 1)[-1] for r in svc.get("must_contain", [])}
        wt = git(repo_path, "worktree", "list", "--porcelain") or ""
        for block in wt.split("\n\n"):
            path = re.search(r"^worktree (.+)$", block, re.M)
            head = re.search(r"^HEAD ([0-9a-f]{40})$", block, re.M)
            br = re.search(r"^branch refs/heads/(.+)$", block, re.M)
            if not (path and head):
                continue
            miss = missing_prs(repo_path, head.group(1), prs_by_repo.get(svc["repo"], []), bases)
            if miss:
                rows.append({"repo": svc["repo"], "worktree": path.group(1),
                             "branch": br.group(1) if br else "(detached)", "head": head.group(1),
                             "would_miss_merged_prs": miss})
    return rows


# ---------------------------------------------------------------- workers

WORD = re.compile(r"[a-z0-9][a-z0-9_-]{3,}")


def resumes(old: dict, new: dict) -> bool:
    """Does `new` (a later task) take over `old`? Deterministic evidence only:
    it names old's delegation id, branch or worktree in its kickoff, or it is a
    RESUME/finish task whose goal shares most of old's goal words."""
    deleg = old["id"].split("/")[0]
    kick = new["kickoff"]
    if deleg in kick or any(b in kick for b in old["branches"]) or any(w in kick for w in old["worktrees"]):
        return True
    if old["goal"][:100] and new["goal"][:100] == old["goal"][:100]:
        return True
    if re.match(r"(?i)\s*(resume|finish|continue)", new["goal"]):
        a, b = set(WORD.findall(old["goal"].lower())), set(WORD.findall(new["goal"].lower()))
        return bool(a) and len(a & b) / len(a) >= 0.35
    return False


def worker_traces(live_dir: Path, prs_by_repo: dict[str, list[dict]], now: dt.datetime,
                  ttl_hours: float = DEFAULT_TTL_HOURS, vanish_minutes: float = 60) -> list[Trace]:
    tasks = []
    for log in sorted(live_dir.glob("*/task-*.log")):
        try:
            text = log.read_text(errors="replace")
        except OSError:
            continue
        head = text[:4000]
        goal = (re.search(r"^goal: (.*)$", head, re.M) or re.search(r"^task: (.*)$", head, re.M))
        started = re.search(r"^started: (.+)$", head, re.M)
        end = re.findall(r"end status=(\w+)", text)
        mtime = dt.datetime.fromtimestamp(log.stat().st_mtime, dt.timezone.utc)
        branches = set(re.findall(r"worktree add -b ([\w./-]+)", text)) | set(
            re.findall(r"git push[^\n\"]*? -u origin ([\w./-]+)", text))
        worktrees = set(re.findall(r"(/mnt/zer0models/[\w./-]*(?:\.worktrees|z0-wt|worktrees)/[\w.-]+)", text))
        tasks.append({"id": f"{log.parent.name}/{log.stem}", "goal": goal.group(1).strip() if goal else "",
                      "started": started.group(1) if started else None, "end": end[-1] if end else None,
                      "mtime": mtime, "branches": sorted(branches), "worktrees": sorted(worktrees),
                      "kickoff": text[:6000], "path": str(log)})
    pr_by_branch = {pr["headRefName"]: (repo, pr) for repo, prs in prs_by_repo.items() for pr in prs}
    out = []
    for tk in tasks:
        vanished = tk["end"] is None and (now - tk["mtime"]).total_seconds() > vanish_minutes * 60
        if tk["end"] != "interrupted" and not vanished:
            continue
        me = Actor(tk["id"], Role.OPUS)
        t = Trace(f"worker:{tk['id']}", "worker", tk["goal"][:140],
                  meta={"log": tk["path"], "started": tk["started"], "end": tk["end"] or "vanished",
                        "branches": tk["branches"], "worktrees": tk["worktrees"],
                        "last_write": tk["mtime"].isoformat()})
        t.events += [Event("Claim", me, at=tk["started"]), Event("Start", me),
                     Event("Interrupt", at=tk["mtime"].isoformat(),
                           evidence={"how": tk["end"] or "log stopped without a final status"})]
        later = [o for o in tasks if o is not tk and (o["started"] or "") > (tk["started"] or "")
                 and resumes(tk, o)]
        prs = [pr_by_branch[b] for b in tk["branches"] if b in pr_by_branch]
        if later:
            t.events.append(Event("Resume", Actor(later[0]["id"], Role.OPUS), at=later[0]["started"]))
            t.meta["resolution"] = f"resumed by {later[0]['id']}"
        elif any(pr["state"] == "MERGED" for _, pr in prs):
            t.meta["resolution"] = "work merged: " + ", ".join(f"{r}#{p['number']}" for r, p in prs)
        elif any(pr["state"] == "OPEN" for _, pr in prs):
            t.meta["resolution"] = "open PR: " + ", ".join(f"{r}#{p['number']}" for r, p in prs)
        t.replay()
        age_h = (now - tk["mtime"]).total_seconds() / 3600
        if "resolution" not in t.meta and age_h > ttl_hours:
            t.findings.append(Finding(5, "violation", "interrupted_dropped",
                                      f"{'interrupted' if tk['end'] else 'vanished'} {age_h:.1f} h ago; no resume, "
                                      "no PR for its branches, no abandon decision",
                                      {"branches": tk["branches"], "log": tk["path"]}))
        elif "resolution" not in t.meta:
            t.findings.append(Finding(5, "info", "interrupted_pending",
                                      f"interrupted {age_h:.1f} h ago (inside the {ttl_hours} h window)", {}))
        out.append(t)
    return out


# ---------------------------------------------------------------- report

def needs_kevin_queue(traces: list[Trace], avail: Avail) -> dict[str, Any]:
    """Batched queue: deployed items awaiting acceptance, plus merges that
    lack a recorded acceptance (one decision each, evidence attached)."""
    items = []
    for t in traces:
        if needs_kevin(t.final):
            items.append({"id": t.id, "decision": "accept the deployed head?",
                          "evidence": {"integrated": t.final.ig, "served": t.final.dp}})
    held = avail is Avail.OFF
    return {"owner_availability": avail.value, "held_until_next_block": held,
            "size": len(items), "items": items,
            "note": "queued owner decisions never block other items (law 7, owner_off_holds/queue_never_blocks)"}


def trace_json(t: Trace) -> dict[str, Any]:
    return {"id": t.id, "kind": t.kind, "title": t.title, "phase": t.final.phase.value,
            "needs_kevin": needs_kevin(t.final), "head": t.final.hd, "integrated": t.final.ig,
            "deployed": t.final.dp, "meta": t.meta,
            "findings": [asdict(f) for f in t.findings],
            "events": [{"kind": e.kind, "actor": asdict(e.actor) if e.actor else None, "sha": e.sha,
                        "receipt": asdict(e.receipt) if e.receipt else None, "at": e.at}
                       for e in t.events]}


def summarize(traces: list[Trace]) -> dict[str, Any]:
    by: dict[str, dict[str, int]] = {}
    for t in traces:
        for f in t.findings:
            key = f"law{f.law}:{f.code}" if f.law else f.code
            by.setdefault(key, {"violation": 0, "indeterminate": 0, "candidate": 0, "info": 0})[f.severity] += 1
    return dict(sorted(by.items()))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--repos", nargs="*", default=["kvnloo/company-os", "kvnloo/dash", "kvnloo/noop"])
    ap.add_argument("--services", type=Path)
    ap.add_argument("--delegation-dir", type=Path,
                    default=Path("/workspace/hermes-home/profiles/clean/cache/delegation/live"))
    ap.add_argument("--owner-availability", choices=[a.value for a in Avail], default=Avail.OFF.value)
    ap.add_argument("--ttl-hours", type=float, default=DEFAULT_TTL_HOURS)
    ap.add_argument("--no-merge-checks", action="store_true", help="skip check-run lookups on merge commits")
    ap.add_argument("--now", help="ISO time to judge ages against (default: now)")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    now = parse_time(args.now) if args.now else dt.datetime.now(dt.timezone.utc)
    assert now is not None
    conf = json.loads(args.services.read_text()) if args.services else {}
    cfg = conf.get("services", [])
    ports = listening()
    local_for = dict(conf.get("local_repos", {}))
    for s in cfg:
        co = served_checkout(ports[s["port"]]) if s["port"] in ports else None
        if co:
            local_for.setdefault(s["repo"], co["toplevel"])
    prs_by_repo: dict[str, list[dict]] = {}
    traces: list[Trace] = []
    for repo in args.repos:
        prs = fetch_prs(repo)
        prs_by_repo[repo] = prs
        default = default_branch(repo)
        lp = local_for.get(repo)
        # live branch heads straight from the remote (read-only; no fetch into the checkout)
        live = {}
        rc, out = sh(["git", "ls-remote", "--heads", f"https://github.com/{repo}.git"])
        for line in out.splitlines() if rc == 0 else []:
            sha, ref = line.split()
            live[ref.removeprefix("refs/heads/")] = sha
        served_heads = [co["head"] for s in cfg if s["repo"] == repo and s["port"] in ports
                        for co in [served_checkout(ports[s["port"]])] if co]
        traces += pr_traces(repo, prs, default, live, lp, fetch_merge_checks=not args.no_merge_checks,
                            served_heads=served_heads)
    traces += service_traces(cfg, ports, prs_by_repo)
    traces += worker_traces(args.delegation_dir, prs_by_repo, now, args.ttl_hours)
    report = {
        "schema": "z0.fsm.work-item.replay/v0",
        "generated_at": now.isoformat(),
        "sources": {"repos": args.repos, "services": str(args.services) if args.services else None,
                    "delegation_dir": str(args.delegation_dir)},
        "summary": summarize(traces),
        "needs_kevin": needs_kevin_queue(traces, Avail(args.owner_availability)),
        "checkout_sweep": checkout_sweep(cfg, ports, prs_by_repo),
        "items": [trace_json(t) for t in traces],
        "scope": "replay of observed evidence through the v0 statechart; findings are evidence, "
                 "not authorization or verified_success",
    }
    text = json.dumps(report, indent=1, default=str)
    if args.out:
        args.out.write_text(text + "\n")
    print(json.dumps({"summary": report["summary"], "needs_kevin_size": report["needs_kevin"]["size"],
                      "items": len(traces)}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
