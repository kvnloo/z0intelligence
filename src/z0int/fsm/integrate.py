"""Read-only integration inventory. No fetch, checkout, merge, deploy or phone input.

GitHub refs are resolved live via gh; local refs are explicitly separate evidence.
A checkout SHA is NOT a running-process/build SHA. Without a revision-bound
runtime attestation I2 remains unverified, even if the checkout matches.

PYTHONPATH=src python -m z0int.fsm.integrate --config docs/fsm/integration-services.json \
    --output /path/integration-20261021
Optional --decisions accepts local head-bound parking and lease records.
No GitHub login identity is treated as proof that Kevin accepted a change.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
from functools import lru_cache
import json
from pathlib import Path
import subprocess
from urllib.parse import quote
from .integration_owner import overdue, step


class EvidenceError(RuntimeError):
    pass


def command(args, cwd=None):
    try:
        p = subprocess.run(args, cwd=cwd, text=True, capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EvidenceError(f"{args[0]} unavailable: {type(exc).__name__}") from exc
    if p.returncode:
        # Do not copy stderr, which can contain credentials or remote URLs.
        raise EvidenceError(f"{args[0]} {args[1]} returned {p.returncode}")
    return p.stdout.strip()


def gh_api(path):
    return json.loads(command(["gh", "api", path]))


def git(path, *args):
    return command(["git", "-C", path, *args])


def epoch(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


@lru_cache(maxsize=4096)
def contains(repo, ancestor, descendant):
    r = gh_api(f"repos/{repo}/compare/{quote(ancestor, safe='')}...{quote(descendant, safe='')}")
    return r["status"] in ("ahead", "identical")


def parking_valid(record, sha, now):
    try:
        return bool(record and record["head"] == sha and record["reason"].strip()
                    and epoch(record["expires_at"]) > now and record["decided_by"] == "Kevin")
    except (KeyError, ValueError, TypeError):
        return False


def leases_for(records, repo, now):
    active = []
    for r in records:
        try:
            if r["repo"] == repo and r["owner"].strip() and epoch(r["expires_at"]) > now:
                active.append(r)
        except (KeyError, ValueError, TypeError):
            continue
    return active


def classify(pr, contained, parked, stack, runtime_equal=False, accepted=False):
    # Inventory snapshots are not historical event logs: events below establish
    # observed facts, not proof of a past lease or independent review.
    if parked:
        return "Parked"
    if pr.get("mergeable") == "CONFLICTING":
        return "Conflicted"
    if contained:
        if runtime_equal:
            return "OwnerAccepted" if accepted else "Deployed"
        return "OnIntegrationBranch"
    return "Queued" if pr.get("queued", False) else "DraftPR"


def collect_repo(spec, decisions, now):
    repo = spec["repo"]
    out = {"repo": repo, "integration_branch": spec.get("integration_branch"), "changes": [],
           "violations": [], "actions": [], "needs_kevin": [], "errors": []}
    def finding(law, code, target, detail, priority=30):
        out["violations"].append({"law": law, "code": code, "target": target, "detail": detail})
        out["actions"].append({"priority": priority, "target": target, "action": detail, "code": code})
    try:
        prs = json.loads(command(["gh", "pr", "list", "--repo", repo, "--state", "open", "--limit", "1000",
            "--json", "number,isDraft,headRefName,baseRefName,headRefOid,baseRefOid,createdAt,mergeable,url,isCrossRepository"]))
        if len(prs) >= 1000:
            raise EvidenceError("PR cap reached: inventory incomplete")
        branch = spec.get("integration_branch")
        head = gh_api(f"repos/{repo}/commits/{quote(branch, safe='')}")["sha"] if branch else None
        out["integration_head"] = head
        if not branch:
            finding("I1", "NO_INTEGRATION_BRANCH", repo, "Integration agent must designate a canonical integration branch; no default-branch guess.", 5)
        live = leases_for(decisions.get("leases", []), repo, now)
        out["active_leases"] = live
        if len(live) != 1:
            finding("I3", "LEASE_MISSING" if not live else "LEASE_OVERLAP", repo,
                    f"Establish exactly one unexpired integration-owner lease (observed {len(live)}); do not steal an active lease.", 2)
        by_branch = {x["headRefName"]: x for x in prs if not x["isCrossRepository"]}
        for pr in prs:
            if not pr["isDraft"]:
                continue
            target = f"{repo}#{pr['number']}"
            sha = pr["headRefOid"]
            row = {**pr, "id": target, "age_hours": round((now-epoch(pr["createdAt"]))/3600, 2),
                   "contained": None, "stack_consistent": None, "violations": []}
            try:
                if pr["isCrossRepository"]:
                    raise EvidenceError("cross-repository draft requires explicit head-repository comparison")
                contained = contains(repo, sha, head) if head else None
                parent = by_branch.get(pr["baseRefName"])
                stack = contains(repo, parent["headRefOid"], pr["baseRefOid"]) if parent else True
                if parent:
                    row["parent"] = f"{repo}#{parent['number']}"
                    row["parent_head"] = parent["headRefOid"]
                park = decisions.get("parking", {}).get(target)
                parked = parking_valid(park, sha, now)
                row.update(contained=contained, stack_consistent=stack, parked=parked)
                if park and not parked:
                    finding("I1", "PARK_INVALID", target, "Parking expired, lacks an explicit reason/owner decision, or targets another head; requeue it.", 15)
                    row["violations"].append("PARK_INVALID")
                if overdue(max(0, int(now-epoch(pr["createdAt"]))), contained is True, parked):
                    deadline_code = "DRAFT_OVERDUE" if contained is False else "DRAFT_DEADLINE_UNVERIFIABLE"
                    finding("I1", deadline_code, target,
                            f"Integrate independently reviewed head {sha[:12]} or request an explicit expiring park decision; age {row['age_hours']} h.", 20)
                    row["violations"].append(deadline_code)
                elif not contained and not parked:
                    out["actions"].append({"priority": 40, "target": target, "code": "QUEUE_DRAFT", "action": "Queue before its 24 h integration deadline; independently review before landing."})
                if not stack:
                    assert parent is not None
                    finding("I4", "STACK_BASE_STALE", target, f"Restack child: base {pr['baseRefOid'][:12]} does not contain current parent {parent['headRefOid'][:12]}.", 10)
                    row["violations"].append("STACK_BASE_STALE")
                if pr["mergeable"] == "CONFLICTING":
                    finding("I4", "CONFLICTED", target, "Resolve conflicts in an isolated worktree, rerun tests, then requeue for independent review.", 12)
                if pr["mergeable"] == "UNKNOWN":
                    out["errors"].append({"target": target, "error": "GitHub mergeability unknown; do not treat as conflict-free"})
                row["state"] = classify(pr, contained, parked, stack)
                # No generic 'Kevin, triage everything' queue. Only evidence-backed
                # acceptance or a genuinely requested parking decision belongs here.
                if not contained and not parked and row["age_hours"] >= 24 and decisions.get("parking_requested", {}).get(target):
                    out["needs_kevin"].append({"target": target, "decision": "Park with reason+expiry or keep in agent integration queue", "head": sha})
            except EvidenceError as exc:
                row["state"] = "Unknown"
                row["error"] = str(exc)
                out["errors"].append({"target": target, "error": str(exc)})
            out["changes"].append(row)
    except EvidenceError as exc:
        out["errors"].append({"target": repo, "error": str(exc)})
    return out


def inspect_service(spec, repo_row):
    out = dict(spec)
    out.update(checkout_head=None, local_integration_head=None, runtime_sha=None, runtime_status="unattested", violations=[])
    try:
        path = spec["checkout"]
        out["checkout_head"] = git(path, "rev-parse", "HEAD")
        out["checkout_dirty"] = bool(git(path, "status", "--porcelain", "--untracked-files=no"))
        try:
            out["checkout_branch"] = git(path, "symbolic-ref", "--short", "HEAD")
        except EvidenceError:
            out["checkout_branch"] = "DETACHED"
        branch = repo_row.get("integration_branch")
        if branch:
            try:
                out["local_integration_head"] = git(path, "rev-parse", "--verify", f"refs/heads/{branch}")
            except EvidenceError:
                pass
        out["integration_head"] = repo_row.get("integration_head")
        expected = out["integration_head"]
        out["checkout_matches_integration"] = out["checkout_head"] == expected if expected else None
        if out["checkout_matches_integration"] is False:
            out["violations"].append("I2_CHECKOUT_DRIFT")
        if spec.get("unit"):
            props = command(["systemctl", "--user", "show", spec["unit"], "-p", "ActiveState", "-p", "MainPID", "-p", "WorkingDirectory", "-p", "ExecMainStartTimestamp"])
            out["systemd"] = dict(line.split("=", 1) for line in props.splitlines() if "=" in line)
            pid = out["systemd"].get("MainPID", "0")
            try:
                out["process_cwd"] = str(Path(f"/proc/{pid}/cwd").resolve(strict=True))
            except OSError:
                pass
        elif spec.get("port"):
            out["listener"] = command(["ss", "-ltnp", f"sport = :{spec['port']}"])
        # v0 cannot infer served revision from HEAD, process start, or APK version.
        # Attestation support must bind revision to process/build identity, not
        # accept a user-supplied SHA and quietly claim the service is current.
        out["violations"].append("I2_RUNTIME_UNATTESTED")
    except EvidenceError as exc:
        out["error"] = str(exc)
        out["violations"].append("I2_OBSERVATION_FAILED")
    return out


def collect(config, decisions, now):
    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(lambda s: collect_repo(s, decisions, now), config["repos"]))
    by_repo = {r["repo"]: r for r in rows}
    services = [inspect_service(s, by_repo[s["repo"]]) for s in config["services"]]
    violations = [v for r in rows for v in r["violations"]]
    actions = [a for r in rows for a in r["actions"]]
    needs = [a for r in rows for a in r["needs_kevin"]]
    for s in services:
        for code in s["violations"]:
            violations.append({"law": "I2", "target": s["id"], "code": code,
                               "detail": "Checkout drift" if code == "I2_CHECKOUT_DRIFT" else "Running/build SHA cannot be attested from checkout HEAD."})
        actions.append({"priority": 0 if s.get("checkout_matches_integration") is False else 8,
                        "target": s["id"], "code": "ATTEST_DEPLOYMENT",
                        "action": "Capture revision-bound running/build evidence; reconcile checkout to canonical integration head in a reviewed deployment window. Do not restart owner services autonomously."})
    actions.sort(key=lambda a: (a["priority"], a["target"], a["code"]))
    return {"schema": 1, "generated_at": dt.datetime.fromtimestamp(now, dt.timezone.utc).isoformat(),
            "read_only": True, "scope": "Current open drafts; merged history and I5 human acceptance are not independently observed. Snapshot containment does not attest past lease/review. Runtime SHA is unknown without process/build evidence.",
            "repos": rows, "services": services, "violations": violations, "actions": actions,
            "needs_kevin": needs, "errors": [e for r in rows for e in r["errors"]]}


def render(report):
    lines = [f"Integration owner snapshot {report['generated_at']}", report["scope"], "", "CHANGES"]
    for repo in report["repos"]:
        lines.append(f"{repo['repo']}: integration={repo.get('integration_branch')} head={repo.get('integration_head')}")
        for c in repo["changes"]:
            lines.append(f"  {c['id']} {c['state']} age={c['age_hours']}h head={c['headRefOid'][:12]} contained={c['contained']} stack={c['stack_consistent']} {' '.join(c['violations'])}")
    lines += ["", "SERVICES"]
    for s in report["services"]:
        lines.append(f"{s['id']}: checkout={s['checkout_head']} integration={s.get('integration_head')} local-integration={s['local_integration_head']} runtime={s['runtime_status']} {' '.join(s['violations'])}")
    lines += ["", "VIOLATIONS"] + [f"{v['law']} {v['target']} {v['code']}: {v['detail']}" for v in report["violations"]]
    lines += ["", "ORDERED INTEGRATION ACTION LIST"] + [f"{i}. {a['target']} [{a['code']}] {a['action']}" for i,a in enumerate(report["actions"], 1)]
    lines += ["", "BATCHED NEEDS KEVIN"] + ([json.dumps(x) for x in report["needs_kevin"]] or ["None verified: no runtime-bound acceptance evidence or explicit parking requests. Integration/lease/telemetry work stays with agents."])
    lines += ["", "OBSERVATION GAPS"] + [json.dumps(e) for e in report["errors"]]
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", type=Path, default=Path(__file__).resolve().parents[3] / "docs/fsm/integration-services.json")
    ap.add_argument("--decisions", type=Path)
    ap.add_argument("--output", type=Path)
    ap.add_argument("--now", help="ISO8601 clock for replay")
    args = ap.parse_args()
    config = json.loads(args.config.read_text())
    decisions = json.loads(args.decisions.read_text()) if args.decisions else {}
    now = epoch(args.now) if args.now else dt.datetime.now(dt.timezone.utc).timestamp()
    report = collect(config, decisions, now)
    text = render(report)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.with_suffix(".json").write_text(json.dumps(report, indent=2) + "\n")
        args.output.with_suffix(".txt").write_text(text)
    print(text)
    return 2 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
