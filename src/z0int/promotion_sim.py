"""Promotion replay simulator v0: how would a promotion policy have done on the factory's real history?

Three stages, all read-only against GitHub (``gh`` GET calls only) and against the repositories
(bare clones under a scratch dir; trees are read with ``git archive``):

  collect   ``gh`` -> raw PR / Actions-run JSON per repo (cached).
  build     one row per historical promotion event (a merged PR, or a direct first-parent commit on
            the default branch) with features *as of merge time* and outcome labels observed after it.
  score     replay policies over the events, route each to AUTO / HUMAN / BLOCK, and count bad
            promotions let through, good promotions delayed and human reviews needed.

Features reuse Promotion Authority v0 (``z0int.promotion``): the import graph for ``wired`` and the
claim/receipt rule for ``receipt``.  ``tested`` cannot be replayed faithfully (it needs each repo's
CI command runnable under coverage at every historical commit), so the simulator uses a static
proxy: every changed source module with added function-body lines is imported by some test module.

Outcome labels (a promotion is *bad* when any holds):
  reverted      a later commit/PR reverts it (``Revert`` + its title, ``#N`` or one of its SHAs);
  fix_Nd        (SZZ) within N days a later landed commit whose subject says fix/bug/regress/broken/hotfix
                deletes or rewrites lines that ``git blame`` attributes to this promotion's commits;
                ``fixfile_Nd`` is the looser file-overlap variant, reported but not used (it mostly
                measures fast iteration on the same files);
  ci_broke      a workflow that was green on the target branch before the merge is red on the
                pushed merge commit.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import re
import subprocess
import sys
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from . import promotion as pa

SCHEMA = "z0int.promotion_sim.v0"
DEFAULT_REPOS = ("z0intelligence", "verified-oss-loop", "aodl", "z0evals", "kerdoios", "tokenomics",
                 "evolution-lab", "z0", "oss-factory")
PRIVATE_REPOS = {"oss-factory"}          # titles/branches/paths redacted in committed datasets
INTEGRATION = {"nightly", "preview", "dev", "develop", "staging"}
NON_CODE_WORKFLOW = re.compile(r"receipt|label|auto-?merge|promote|stale|greet|dependabot|sync|board|triage|pages", re.I)
FIX_RE = re.compile(r"(^|\W)(fix(es|ed)?|hotfix|bug|regress\w*|broken|repair|unbreak)(\W|$)", re.I)
DOC_RE = re.compile(r"(\.md|\.txt|\.rst|LICENSE|\.lock|lock\.json|CHANGELOG[^/]*)$", re.I)
AGENT_BRANCH = re.compile(r"^(cursor|claude|codex|copilot|grok|devin|jules|agent|bot|nightly-fix|dependabot)[/-]", re.I)
AGENT_TRAILER = re.compile(r"co-authored-by:\s*(claude|cursor|codex|copilot|grok|devin|jules|openai|gemini)", re.I)
PR_FIELDS = ("number,title,state,headRefName,baseRefName,createdAt,mergedAt,closedAt,mergeCommit,headRefOid,"
             "author,additions,deletions,changedFiles,files,statusCheckRollup,labels,body")


# ----------------------------------------------------------------------------- collect (gh, read-only)
def _gh_json(args: list[str]) -> Any:
    r = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:3])}: {r.stderr.strip()[:300]}")
    out = r.stdout.strip()
    if args[0] == "api" and "--paginate" in args and "--slurp" in args:
        return json.loads(out or "[]")
    return json.loads(out or "null")


def collect(owner: str, repos: Iterable[str], cache: Path, refresh: bool = False) -> dict[str, Any]:
    """Fetch PRs (all states) and Actions runs per repo, and keep a bare clone with PR head refs."""
    cache.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any] = {}
    for repo in repos:
        d = cache / "raw" / repo
        d.mkdir(parents=True, exist_ok=True)
        if refresh or not (d / "prs.json").exists():
            prs = _gh_json(["pr", "list", "-R", f"{owner}/{repo}", "--state", "all", "--limit", "1000", "--json", PR_FIELDS])
            (d / "prs.json").write_text(json.dumps(prs))
        if refresh or not (d / "runs.json").exists():
            pages = _gh_json(["api", "--paginate", "--slurp", f"repos/{owner}/{repo}/actions/runs?per_page=100"])
            runs = [{k: r.get(k) for k in ("id", "name", "path", "head_branch", "head_sha", "event", "status",
                                            "conclusion", "created_at", "run_attempt")}
                    for p in pages for r in p.get("workflow_runs", [])]
            (d / "runs.json").write_text(json.dumps(runs))
        meta = _gh_json(["repo", "view", f"{owner}/{repo}", "--json", "defaultBranchRef,visibility"])
        (d / "meta.json").write_text(json.dumps(meta))
        bare = cache / "repos" / f"{repo}.git"
        if not bare.exists():
            subprocess.run(["git", "clone", "-q", "--bare", f"https://github.com/{owner}/{repo}.git", str(bare)], check=True)
        subprocess.run(["git", "-C", str(bare), "fetch", "-q", "origin", "+refs/heads/*:refs/heads/*",
                        "+refs/pull/*/head:refs/pull/*/head"], check=True)
        summary[repo] = {"prs": len(json.loads((d / "prs.json").read_text())),
                         "runs": len(json.loads((d / "runs.json").read_text()))}
    return summary


# ----------------------------------------------------------------------------- helpers
def ts(s: str | None) -> float | None:
    if not s:
        return None
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def iso(t: float) -> str:
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def git(repo: Path, *args: str) -> str:
    return pa._git(repo, *args).stdout


def has_obj(repo: Path, sha: str | None) -> bool:
    return bool(sha) and pa._git(repo, "cat-file", "-e", f"{sha}^{{commit}}").returncode == 0


def tier(base: str, default: str) -> str:
    if base == default:
        return "default"
    return "integration" if base in INTEGRATION else "stack"


def agent_kind(branch: str, author: str, is_bot: bool, texts: Iterable[str], labels: Iterable[str]) -> str:
    """Who wrote it: ``dependabot``, ``agent`` (branch prefix, trailer or bot label) or ``human``."""
    if "dependabot" in (author or "") or branch.startswith("dependabot/"):
        return "dependabot"
    if is_bot or AGENT_BRANCH.match(branch or "") or any(AGENT_TRAILER.search(t or "") for t in texts) \
            or any("bot" in (lb or "").lower() for lb in labels):
        return "agent"
    return "human"


def ci_state(checks: list[dict[str, Any]]) -> dict[str, Any]:
    """Latest conclusion per check name -> ``green`` / ``red`` / ``pending`` / ``none``, all and code-only."""
    latest: dict[str, dict[str, Any]] = {}
    for c in checks or []:
        name = f"{c.get('workflowName') or c.get('context') or ''}/{c.get('name') or c.get('context') or ''}"
        when = c.get("completedAt") or c.get("startedAt") or ""
        if name not in latest or when >= (latest[name].get("completedAt") or latest[name].get("startedAt") or ""):
            latest[name] = c

    def verdict(items: list[dict[str, Any]]) -> str:
        if not items:
            return "none"
        concl = [(c.get("conclusion") or c.get("state") or "").upper() for c in items]
        if any(x in ("FAILURE", "ERROR", "TIMED_OUT", "CANCELLED", "STARTUP_FAILURE", "ACTION_REQUIRED") for x in concl):
            return "red"
        if any(x in ("", "PENDING", "EXPECTED", "IN_PROGRESS", "QUEUED") for x in concl):
            return "pending"
        return "green"
    code = [c for n, c in latest.items() if not NON_CODE_WORKFLOW.search(n)]
    return {"all": verdict(list(latest.values())), "code": verdict(code), "n_checks": len(latest),
            "n_code_checks": len(code), "red": sorted(n for n, c in latest.items()
                                                      if (c.get("conclusion") or "").upper() in ("FAILURE", "ERROR", "TIMED_OUT"))}


def runs_to_checks(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"workflowName": r["name"], "name": r["name"], "conclusion": (r.get("conclusion") or "").upper(),
             "completedAt": r["created_at"]} for r in runs]


# ----------------------------------------------------------------------------- cached import graph
_AST_OPS: dict[str, list[tuple]] = {}


def _ast_ops(text: str) -> list[tuple] | None:
    """The import-relevant nodes of one file, memoised by content (history re-parses the same blobs)."""
    key = pa._sha(text, 20)
    if key not in _AST_OPS:
        try:
            tree = pa.ast.parse(text)
        except (SyntaxError, ValueError):
            _AST_OPS[key] = None  # type: ignore[assignment]
            return None
        ops: list[tuple] = []
        for n in pa.ast.walk(tree):
            if isinstance(n, pa.ast.Import):
                ops.append(("import", tuple(a.name for a in n.names)))
            elif isinstance(n, pa.ast.ImportFrom):
                ops.append(("from", n.module or "", n.level, tuple(a.name for a in n.names)))
            elif isinstance(n, pa.ast.Constant) and isinstance(n.value, str):
                ops.append(("const", n.value))
        _AST_OPS[key] = ops
    return _AST_OPS[key]


class CachedImportGraph(pa.ImportGraph):
    """``promotion.ImportGraph`` with the per-file AST walk memoised; edge semantics are unchanged."""

    def _python_edges(self) -> None:
        for mod, rel in list(self.modules.items()):
            try:
                ops = _ast_ops((self.tree / rel).read_text(errors="replace"))
            except OSError:
                continue
            if ops is None:
                continue
            for op in ops:
                if op[0] == "import":
                    for name in op[1]:
                        self._add_prefixes(mod, rel, name, mod)
                elif op[0] == "from":
                    cs, full = self._resolve(rel, op[1], op[2], mod)
                    if full:
                        self._add_prefixes(mod, rel, full, mod)
                    self.edges[mod].update(cs)
                    for a in op[3]:
                        self.edges[mod].update(self._resolve(rel, (full + "." if full else "") + a, 0, mod)[0])
                else:
                    v = op[1]
                    if "." in v and v in self.modules and len(v) < 120:
                        self.edges[mod].add(v)
                    for m in pa.MOD_RE.findall(v):
                        if m in self.modules:
                            self.edges[mod].add(m)
                    if re.search(r"\.(py|sh)$", v) and "/" in v and len(v) < 200:
                        self.edges[mod].update(self.node(t) for t in self._path_targets(v) if t != rel)
            parts = mod.split(".")
            for k in range(1, len(parts)):
                if ".".join(parts[:k]) in self.modules:
                    self.edges[mod].add(".".join(parts[:k]))
            self.edges[mod].discard(mod)


# ----------------------------------------------------------------------------- per-event features
@dataclass
class Landed:
    """A commit that landed on some branch: the universe for revert / fix-follow-up labels."""
    sha: str
    t: float
    subject: str
    body: str
    files: frozenset[str]


def landed_commits(repo: Path, merged_heads: Iterable[str]) -> list[Landed]:
    refs = ["--branches"] + [h for h in merged_heads if h]
    out = git(repo, "log", "--no-merges", "--format=%x00%H%x1f%at%x1f%s%x1f%b%x1e", "--name-only", *refs)
    res: dict[str, Landed] = {}
    for chunk in out.split("\x00")[1:]:
        head, _, names = chunk.partition("\x1e")
        sha, at, subj, body = (head.split("\x1f") + ["", "", "", ""])[:4]
        res[sha] = Landed(sha, float(at), subj, body, frozenset(n for n in names.split("\n") if n.strip()))
    return list(res.values())


def code_files(files: Iterable[str]) -> set[str]:
    return {f for f in files if not DOC_RE.search(f)}


def static_tested(graph: pa.ImportGraph | None, gated: Iterable[str]) -> tuple[str, list[str]]:
    """Proxy for PA ``tested``: each gated module is imported by at least one test module."""
    gated = list(gated)
    if not gated:
        return "observed", []
    if graph is None:
        return "unknown", gated
    tested_targets: set[str] = set()
    for mod, rel in graph.modules.items():
        if pa.is_test_path(rel):
            tested_targets |= graph.edges.get(mod, set())
    missing = [g for g in gated if graph.node(g) not in tested_targets]
    return ("no_match" if missing else "observed"), missing


def tree_facts(repo: Path, base: str | None, sha: str, texts: list[tuple[str, str]], scratch: Path) -> dict[str, Any]:
    """PA v0 criteria (wired, receipt) plus the static tested proxy, at ``sha`` relative to ``base``."""
    out: dict[str, Any] = {}
    changed = pa.changed_files(repo, base, sha) if base else {}
    out["changed"] = changed
    tree = scratch / sha[:12]
    graph, err, files = None, None, []
    try:
        files = pa.extract_tree(repo, sha, tree)
        graph = CachedImportGraph(tree, files, {})
    except Exception as exc:  # noqa: BLE001 - becomes unknown, never a pass
        err = f"{type(exc).__name__}: {exc}"
    roots = pa.src_roots(tree) if files else ["src"]
    added = sorted(p for p, st in changed.items() if st == "A" and files and pa.is_source_module(p, roots, tree))
    fw = pa.non_python_gap(pa.fact_wired(graph, added, err), pa.non_python_code(changed, "A") if files else [], graph)
    src_changed = [p for p, st in changed.items() if st in ("A", "M") and files and pa.is_source_module(p, roots, tree)]
    adds = pa.added_lines(repo, base, sha, src_changed) if base else {}
    gated = []
    for rel in src_changed:
        fl = pa._funclines((tree / rel).read_text(errors="replace")) if (tree / rel).exists() else None
        if adds.get(rel, set()) & (fl or set()):
            gated.append(rel)
    t_status, t_missing = static_tested(graph, gated)
    if t_status == "observed" and pa.non_python_code(changed, "AM") and files:
        t_status = "source_unavailable"
    fr = pa.fact_receipt(texts, True, changed, set(files))
    out.update({
        "wired": fw.status, "wired_unreached": (fw.value or {}).get("unreached", []),
        "added_modules": len(added), "tested_static": t_status, "untested": t_missing, "gated_files": len(gated),
        "receipt": fr.status, "claim_bearing": bool((fr.value or {}).get("claim_bearing")),
        "non_python_code": len(pa.non_python_code(changed, "AM")),
    })
    subprocess.run(["rm", "-rf", str(tree)])
    return out


def pa_verdict(ev: Mapping[str, Any]) -> str:
    """Promotion Authority v0 fixed-priority gate over the replayed facts (no grant: never ACT)."""
    tested = "no_match" if ev["ci_code"] == "red" else ev["tested_static"]
    facts = {"merge": "observed", "wired": ev["wired"], "tested": tested, "receipt": ev["receipt"]}
    st = [s for s in facts.values() if s not in ("observed", "n/a")]
    if "no_match" in st:
        return "ABSTAIN"
    if "unknown" in st:
        return "OBSERVE"
    return "ASK"


# ----------------------------------------------------------------------------- labels (post-merge outcomes)
def szz_origins(repo: Path, fix: Landed) -> set[str]:
    """SZZ: the commits that last touched the lines a fix commit deletes or modifies (``git blame`` at its parent)."""
    parent = git(repo, "rev-parse", f"{fix.sha}^1").strip()
    if not parent:
        return set()
    files = [f for f in fix.files if not DOC_RE.search(f)]
    if not files:
        return set()
    diff = git(repo, "diff", "-U0", "--no-color", "--no-renames", parent, fix.sha, "--", *files)
    spans: dict[str, list[tuple[int, int]]] = defaultdict(list)
    cur = None
    for line in diff.splitlines():
        if line.startswith("--- "):
            cur = line[6:] if line.startswith("--- a/") else None
        elif line.startswith("@@") and cur:
            m = re.search(r"-(\d+)(?:,(\d+))?", line)
            if m and int(m.group(2) or 1) > 0:
                spans[cur].append((int(m.group(1)), int(m.group(1)) + int(m.group(2) or 1) - 1))
    out: set[str] = set()
    for f, rs in spans.items():
        args = [x for a, b in rs[:40] for x in ("-L", f"{a},{b}")]
        blame = git(repo, "blame", "-w", "--porcelain", *args, parent, "--", f)
        out |= {ln.split()[0] for ln in blame.splitlines() if re.match(r"^[0-9a-f]{40} \d+ \d+", ln)}
    return out


def label_repo(cache: Path, repo: str, evs: list[dict[str, Any]], fix_days: Iterable[int], cutoff: float,
               landed: list[Landed] | None = None) -> None:
    """Outcome labels for one repo's events, in place."""
    d = cache / "raw" / repo
    bare = cache / "repos" / f"{repo}.git"
    runs = json.loads((d / "runs.json").read_text())
    if landed is None:
        prs = json.loads((d / "prs.json").read_text())
        landed = landed_commits(bare, [f"refs/pull/{p['number']}/head" for p in prs
                                       if p.get("mergedAt") and has_obj(bare, p.get("headRefOid"))])
    t0 = min((e["t"] for e in evs), default=0.0)
    blamed: dict[str, list[Landed]] = defaultdict(list)       # origin sha -> fix commits that rewrote its lines
    for c in landed:
        if c.t > t0 and FIX_RE.search(c.subject):
            for o in szz_origins(bare, c):
                blamed[o].append(c)
    for ev in evs:
        t, own, files = ev["t"], set(ev["own_shas"]), set(ev["label_files"])
        rv = reverted_by(ev, own, landed, t)
        ev["reverted"], ev["reverted_by"] = bool(rv), rv
        fixers = {c.sha: c for o in own for c in blamed.get(o, []) if c.sha not in own and c.t > t}
        for n in fix_days:
            ev[f"fix_{n}d"] = sorted(s for s, c in fixers.items() if c.t <= t + n * 86400)       # SZZ (primary)
            ev[f"fixfile_{n}d"] = fix_followup(files, own, landed, t, n)                        # file overlap (noisy)
        cb = ci_broke(runs, ev["label_branch"], ev.get("landed_sha"), t)
        ev["ci_observed"], ev["ci_broke"], ev["ci_broke_workflows"] = cb["observed"], cb["broke"], cb["workflows"]
        ev["observed_days"] = round((cutoff - t) / 86400, 2)


def relabel(cache: Path, events: list[dict[str, Any]], fix_days: tuple[int, ...] = (3, 7, 14)) -> list[dict[str, Any]]:
    repos = sorted({e["repo"] for e in events})
    cutoff = max((cache / "raw" / r / "runs.json").stat().st_mtime for r in repos)
    for r in repos:
        label_repo(cache, r, [e for e in events if e["repo"] == r], fix_days, cutoff)
    return events


# ----------------------------------------------------------------------------- build
def pr_commits(bare: Path, p: Mapping[str, Any], default: str) -> list[dict[str, Any]]:
    head, mc = p.get("headRefOid"), (p.get("mergeCommit") or {}).get("oid")
    if not has_obj(bare, head):
        return []
    anchor = f"{mc}^1" if has_obj(bare, mc) else (p["baseRefName"] if pa._rev(bare, p["baseRefName"]) else default)
    out = git(bare, "log", "--no-merges", "--format=%H%x1f%aI%x1f%s%x1f%b%x1e", f"{anchor}..{head}")
    res = []
    for rec in out.split("\x1e"):
        rec = rec.strip("\n")
        if rec:
            h, a, subj, body = (rec.split("\x1f") + [""] * 4)[:4]
            res.append({"oid": h, "authoredDate": a, "messageHeadline": subj, "messageBody": body})
    return res


def _latest_by_workflow(runs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for r in sorted(runs, key=lambda r: (r["created_at"], r.get("run_attempt") or 1)):
        out[r["name"]] = r
    return out


def ci_broke(runs: list[dict[str, Any]], branch: str, sha: str | None, t: float) -> dict[str, Any]:
    """Did a code workflow that was green on ``branch`` just before ``t`` go red on the pushed ``sha``?"""
    if not sha:
        return {"observed": False, "broke": False, "workflows": []}
    push = [r for r in runs if r.get("event") == "push" and r.get("head_branch") == branch
            and r.get("status") == "completed" and not NON_CODE_WORKFLOW.search(r.get("name") or "")]
    at = _latest_by_workflow([r for r in push if r["head_sha"] == sha])
    if not at:
        return {"observed": False, "broke": False, "workflows": []}
    first = min(ts(r["created_at"]) for r in at.values())
    before = _latest_by_workflow([r for r in push if r["head_sha"] != sha and ts(r["created_at"]) < min(first, t + 60)])
    broke = sorted(n for n, r in at.items() if r.get("conclusion") in ("failure", "timed_out", "startup_failure")
                   and (before.get(n) or {}).get("conclusion") == "success")
    red = sorted(n for n, r in at.items() if r.get("conclusion") in ("failure", "timed_out", "startup_failure"))
    return {"observed": True, "broke": bool(broke), "workflows": broke, "red_after": red}


def reverted_by(ev: Mapping[str, Any], own: set[str], landed: list[Landed], t: float) -> str | None:
    title = (ev.get("title") or "").strip()
    keys = [s[:7] for s in own if s] + ([f"#{ev['number']}"] if ev.get("number") else [])
    for c in landed:
        if c.t <= t or c.sha in own or not c.subject.lower().startswith("revert"):
            continue
        blob = c.subject + "\n" + c.body
        if (title and len(title) > 8 and title in blob) or any(k in blob for k in keys):
            return c.sha
    return None


def fix_followup(files: set[str], own: set[str], landed: list[Landed], t: float, days: float) -> list[str]:
    hits = []
    for c in landed:
        if c.sha in own or not (t < c.t <= t + days * 86400) or not FIX_RE.search(c.subject):
            continue
        if code_files(c.files) & files:
            hits.append(c.sha)
    return sorted(hits)


def _redact(repo: str, s: str | None) -> str | None:
    if s is None or repo not in PRIVATE_REPOS:
        return s
    return "redacted:" + pa._sha(s, 8)


def build(cache: Path, repos: Iterable[str], fix_days: tuple[int, ...] = (3, 7, 14), log: Callable[[str], None] = print) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    repos = list(repos)
    cutoff = max((cache / "raw" / r / "runs.json").stat().st_mtime for r in repos)   # history is observed up to collection
    scratch_root = cache / "trees"
    scratch_root.mkdir(parents=True, exist_ok=True)
    for repo in repos:
        d = cache / "raw" / repo
        bare = cache / "repos" / f"{repo}.git"
        prs = json.loads((d / "prs.json").read_text())
        runs = json.loads((d / "runs.json").read_text())
        default = json.loads((d / "meta.json").read_text())["defaultBranchRef"]["name"]
        merged = [p for p in prs if p.get("mergedAt")]
        for p in merged:   # PR commits from git (the GraphQL list cannot nest commits->authors at this size)
            p["commits"] = pr_commits(bare, p, default)
        landed = landed_commits(bare, [f"refs/pull/{p['number']}/head" for p in merged
                                        if has_obj(bare, p.get("headRefOid"))])
        pr_commit_keys = {(c.get("messageHeadline"), c.get("authoredDate")) for p in merged for c in p.get("commits") or []}
        merge_shas = {(p.get("mergeCommit") or {}).get("oid") for p in merged}
        raw: list[dict[str, Any]] = []
        with tempfile.TemporaryDirectory(prefix="promsim-", dir=scratch_root) as tmp:
            scratch = Path(tmp)
            for p in merged:
                t = ts(p["mergedAt"])
                head = p.get("headRefOid")
                mc = (p.get("mergeCommit") or {}).get("oid")
                own = {head, mc} | {c.get("oid") for c in p.get("commits") or []}
                texts = [("commits", "\n".join(f"{c.get('messageHeadline', '')}\n{c.get('messageBody', '')}" for c in p.get("commits") or [])),
                         (f"PR #{p['number']}", f"{p.get('title', '')}\n{p.get('body') or ''}")]
                base_sha = None
                if has_obj(bare, head):
                    anchor = f"{mc}^1" if has_obj(bare, mc) else None
                    anchor = anchor or (p["baseRefName"] if pa._rev(bare, p["baseRefName"]) else default)
                    base_sha = git(bare, "merge-base", anchor, head).strip() or None
                tf = tree_facts(bare, base_sha, head, texts, scratch) if has_obj(bare, head) and base_sha else None
                checks = [c for c in p.get("statusCheckRollup") or []
                          if (c.get("completedAt") or c.get("startedAt") or "") <= p["mergedAt"] or not c.get("completedAt")]
                ci = ci_state(checks)
                files = {f["path"] for f in p.get("files") or []} | set((tf or {}).get("changed", {}))
                src_files = [f for f in files if Path(f).suffix in pa.CODE_EXT and not pa.is_test_path(f)]
                ev = {
                    "id": f"{repo}#{p['number']}", "repo": repo, "kind": "pr", "number": p["number"],
                    "title": _redact(repo, p.get("title")), "head_branch": _redact(repo, p.get("headRefName")),
                    "base": p["baseRefName"], "tier": tier(p["baseRefName"], default), "t": t, "merged_at": p["mergedAt"],
                    "hours_open": round((t - ts(p["createdAt"])) / 3600, 2),
                    "author": p["author"]["login"],
                    "agent": agent_kind(p.get("headRefName") or "", p["author"]["login"], p["author"].get("is_bot", False),
                                        [t_ for _, t_ in texts], [lb.get("name") for lb in p.get("labels") or []]),
                    "additions": p.get("additions", 0), "deletions": p.get("deletions", 0),
                    "size": p.get("additions", 0) + p.get("deletions", 0), "n_files": p.get("changedFiles", len(files)),
                    "n_commits": len(p.get("commits") or []),
                    "touches_src": bool(src_files), "touches_tests": any(pa.is_test_path(f) for f in files),
                    "docs_only": bool(files) and all(DOC_RE.search(f) for f in files),
                    "ci_all": ci["all"], "ci_code": ci["code"], "n_checks": ci["n_checks"], "ci_red": ci["red"],
                    "has_receipt_block": "evidence receipt" in (p.get("body") or "").lower(),
                    "landed_sha": mc, "tree_ok": tf is not None,
                }
                ev.update({k: v for k, v in (tf or {"wired": "unknown", "tested_static": "unknown", "receipt": "unknown",
                                                    "claim_bearing": False, "added_modules": 0, "gated_files": 0,
                                                    "non_python_code": 0, "wired_unreached": [], "untested": []}).items()
                           if k != "changed"})
                raw.append((ev, own, code_files(files), p["baseRefName"]))
            # direct first-parent commits on the default branch that are not PR merges
            fp = git(bare, "log", "--first-parent", "--format=%H%x1f%P%x1f%at%x1f%an%x1f%s%x1f%b%x1e", default)
            for rec in fp.split("\x1e"):
                rec = rec.strip("\n")
                if not rec:
                    continue
                sha, parents, at, an, subj, body = (rec.split("\x1f") + [""] * 6)[:6]
                parents = parents.split()
                if sha in merge_shas or not parents:
                    continue
                if subj.startswith("Merge pull request #"):
                    continue
                t = float(at)
                if any(k[0] == subj for k in pr_commit_keys) and len(parents) == 1:
                    continue  # rebase-merged commit of a PR already counted
                texts = [("commits", git(bare, "log", "--format=%s%n%b", f"{parents[0]}..{sha}"))]
                tf = tree_facts(bare, parents[0], sha, texts, scratch)
                files = set(tf["changed"])
                num = git(bare, "diff", "--numstat", parents[0], sha)
                adds = dels = 0
                for ln in num.splitlines():
                    a, b_, *_ = ln.split("\t")
                    adds += int(a) if a.isdigit() else 0
                    dels += int(b_) if b_.isdigit() else 0
                src_files = [f for f in files if Path(f).suffix in pa.CODE_EXT and not pa.is_test_path(f)]
                own = {sha} | set(git(bare, "rev-list", f"{parents[0]}..{sha}").split())
                ev = {
                    "id": f"{repo}@{sha[:10]}", "repo": repo, "kind": "merge" if len(parents) > 1 else "direct",
                    "number": None, "title": _redact(repo, subj), "head_branch": None, "base": default, "tier": "default",
                    "t": t, "merged_at": iso(t), "hours_open": 0.0, "author": an,
                    "agent": agent_kind("", an, False, [body], []),
                    "additions": adds, "deletions": dels, "size": adds + dels, "n_files": len(files),
                    "n_commits": len(own), "touches_src": bool(src_files),
                    "touches_tests": any(pa.is_test_path(f) for f in files),
                    "docs_only": bool(files) and all(DOC_RE.search(f) for f in files),
                    "ci_all": "none", "ci_code": "none", "n_checks": 0, "ci_red": [],   # no pre-merge gate on a push
                    "has_receipt_block": False, "landed_sha": sha, "tree_ok": True,
                }
                ev.update({k: v for k, v in tf.items() if k != "changed"})
                raw.append((ev, own, code_files(files), default))
        for ev, own, files, branch in raw:
            ev["own_shas"], ev["label_files"], ev["label_branch"] = sorted(x for x in own if x), sorted(files), branch
            ev["pa_v0"] = pa_verdict(ev)
        label_repo(cache, repo, [r[0] for r in raw], fix_days, cutoff, landed)
        events.extend(r[0] for r in raw)
        log(f"{repo}: {sum(1 for e in events if e['repo'] == repo)} events")
    events.sort(key=lambda e: e["t"])
    return events


# ----------------------------------------------------------------------------- labels
def is_bad(ev: Mapping[str, Any], fix_days: int = 3, strict: bool = False) -> bool:
    """Primary label: reverted, broke the target's CI, or (unless ``strict``) a fix rewrote its lines (SZZ) within N days."""
    bad = bool(ev.get("reverted")) or bool(ev.get("ci_broke"))
    if not strict:
        bad = bad or bool(ev.get(f"fix_{fix_days}d"))
    return bad


def scorable(ev: Mapping[str, Any], fix_days: int = 3) -> bool:
    """An event can be labelled only when its whole fix-follow-up window has been observed."""
    return ev.get("observed_days", 0) >= fix_days


# ----------------------------------------------------------------------------- policies
AUTO, HUMAN, BLOCK = "AUTO", "HUMAN", "BLOCK"
Policy = Callable[[Mapping[str, Any]], str]


def human_all(ev: Mapping[str, Any]) -> str:
    """Status quo: a human merges everything."""
    return HUMAN


def pa_v0(ev: Mapping[str, Any]) -> str:
    """Promotion Authority v0 as specified: never ACT without a grant; ABSTAIN sends the branch back."""
    return BLOCK if ev["pa_v0"] == "ABSTAIN" else HUMAN


def pa_v0_auto(ev: Mapping[str, Any]) -> str:
    """PA v0 with a standing grant for clean ASKs: every criterion observed and code CI green."""
    if ev["pa_v0"] == "ABSTAIN":
        return BLOCK
    clean = (ev["pa_v0"] == "ASK" and ev["wired"] == "observed" and ev["tested_static"] == "observed"
             and ev["receipt"] in ("observed", "n/a") and ev["ci_code"] == "green")
    return AUTO if clean else HUMAN


def auto_on_green(ev: Mapping[str, Any]) -> str:
    """Auto-merge when every check (receipt included) is green; otherwise a human."""
    return AUTO if ev["ci_all"] == "green" else HUMAN


def auto_on_code_green(ev: Mapping[str, Any]) -> str:
    """Auto-merge when the code checks are green (receipt/label/automerge workflows ignored)."""
    return AUTO if ev["ci_code"] == "green" else HUMAN


def decision7(ev: Mapping[str, Any]) -> str:
    """Decision #7 as drafted: agent branches auto-merge to nightly on green; the default branch only via PA."""
    if ev["tier"] != "default" and ev["agent"] == "agent":
        return auto_on_code_green(ev)
    return pa_v0(ev)


def threshold(max_size: float, green: str = "code", need_tests: bool = False, need_wired: bool = False,
              need_receipt: bool = False, docs_free: bool = True) -> Policy:
    """Auto-merge family: CI green (``all`` / ``code`` / ``any``), size <= max_size, optional PA criteria."""
    def pol(ev: Mapping[str, Any]) -> str:
        if docs_free and ev.get("docs_only") and green != "any" and ev["ci_" + green] != "red":
            return AUTO
        if green != "any" and ev["ci_" + green] != "green":
            return HUMAN
        if ev["size"] > max_size:
            return HUMAN
        if need_tests and ev["touches_src"] and not ev["touches_tests"]:
            return HUMAN
        if need_wired and ev["wired"] not in ("observed",):
            return HUMAN
        if need_receipt and ev["receipt"] not in ("observed", "n/a"):
            return HUMAN
        return AUTO
    pol.__doc__ = (f"auto if ci_{green} green, size<={max_size:g}" + (", tests touched" if need_tests else "")
                   + (", wired" if need_wired else "") + (", receipt ok" if need_receipt else "")
                   + (" (docs-only auto unless red)" if docs_free else ""))
    return pol


def policies() -> dict[str, Policy]:
    pols: dict[str, Policy] = {"human_all": human_all, "pa_v0": pa_v0, "pa_v0_auto": pa_v0_auto,
                               "auto_on_green": auto_on_green, "auto_on_code_green": auto_on_code_green,
                               "decision7_draft": decision7}
    for size in (50, 200, 500, 1000, math.inf):
        for green in ("all", "code"):
            for tests in (False, True):
                for wired in (False, True):
                    for receipt in (False, True):
                        name = (f"auto[{green},<={'inf' if size == math.inf else int(size)}"
                                f"{',tests' if tests else ''}{',wired' if wired else ''}{',receipt' if receipt else ''}]")
                        pols[name] = threshold(size, green, tests, wired, receipt)
    return pols


# ----------------------------------------------------------------------------- scoring
def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (max(0.0, c - h), min(1.0, c + h))


def score(events: list[Mapping[str, Any]], pol: Policy, bad: Callable[[Mapping[str, Any]], bool]) -> dict[str, Any]:
    """Route each event; human catch rate is taken as 0 (every historical bad promotion was human-merged)."""
    c = defaultdict(int)
    for ev in events:
        r, b = pol(ev), bad(ev)
        c[r] += 1
        c[("bad" if b else "good", r)] += 1
    n = len(events)
    n_bad = sum(c[("bad", r)] for r in (AUTO, HUMAN, BLOCK))
    auto = c[AUTO]
    lo, hi = wilson(c[("bad", AUTO)], auto)
    return {
        "n": n, "bad": n_bad, "auto": auto, "human_reviews": c[HUMAN], "blocked": c[BLOCK],
        "bad_auto": c[("bad", AUTO)], "bad_through": c[("bad", AUTO)] + c[("bad", HUMAN)], "bad_blocked": c[("bad", BLOCK)],
        "good_blocked": c[("good", BLOCK)], "good_waiting_human": c[("good", HUMAN)], "good_auto": c[("good", AUTO)],
        "auto_bad_rate": round(c[("bad", AUTO)] / auto, 4) if auto else None,
        "auto_bad_rate_ci95": [round(lo, 4), round(hi, 4)],
        "base_bad_rate": round(n_bad / n, 4) if n else None,
        "auto_share": round(auto / n, 4) if n else 0.0,
    }


def split_by_time(events: list[Mapping[str, Any]], holdout: float = 0.3) -> tuple[list, list, float]:
    evs = sorted(events, key=lambda e: e["t"])
    k = int(round(len(evs) * (1 - holdout)))
    return evs[:k], evs[k:], evs[k]["t"] if k < len(evs) else math.inf


# ----------------------------------------------------------------------------- selection + pre-registration
def select(events: list[Mapping[str, Any]], pols: Mapping[str, Policy], bad: Callable[[Mapping[str, Any]], bool],
           ratio: float = 0.5, min_auto: int = 5) -> dict[str, Any]:
    """Pre-registered criterion: most automation such that the auto-merged set's bad rate is at most
    ``ratio`` x the pool's bad rate, with at least ``min_auto`` auto-merges.  Ties: fewer good blocked,
    then fewer bad let through, then the shorter (simpler) rule name."""
    table = {name: score(events, pol, bad) for name, pol in pols.items()}
    base = next(iter(table.values()))["base_bad_rate"] or 0.0
    ok = {n: m for n, m in table.items() if m["auto"] >= min_auto and (m["auto_bad_rate"] or 0.0) <= ratio * base}
    pick = min(ok, key=lambda n: (-ok[n]["auto"], ok[n]["good_blocked"], ok[n]["bad_through"], len(n)), default=None)
    return {"base_bad_rate": base, "ratio": ratio, "min_auto": min_auto, "eligible": sorted(ok), "pick": pick, "table": table}


def dataset_rows(events: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Committed dataset: features and labels, no PR bodies (and private repos already redacted)."""
    drop = {"wired_unreached", "untested"}
    return [{k: v for k, v in e.items() if k not in drop} for e in events]


def _load(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text())


def _bad_fn(fix_days: int, strict: bool) -> Callable[[Mapping[str, Any]], bool]:
    return lambda e: is_bad(e, fix_days, strict)


def _fmt_table(table: Mapping[str, Mapping[str, Any]], names: Iterable[str]) -> str:
    rows = ["| policy | auto | human | blocked | bad auto | bad through | good blocked | auto bad rate (95% CI) |",
            "|---|---|---|---|---|---|---|---|"]
    for n in names:
        m = table[n]
        r = "—" if m["auto_bad_rate"] is None else f"{m['auto_bad_rate']:.2f} ({m['auto_bad_rate_ci95'][0]:.2f}–{m['auto_bad_rate_ci95'][1]:.2f})"
        rows.append(f"| `{n}` | {m['auto']} | {m['human_reviews']} | {m['blocked']} | {m['bad_auto']} | {m['bad_through']} "
                    f"| {m['good_blocked']} | {r} |")
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="z0int promote sim", description="Replay promotion policies over real history.")
    ap.add_argument("stage", choices=["collect", "build", "relabel", "score", "prereg", "holdout"])
    ap.add_argument("--owner", default="kvnloo")
    ap.add_argument("--repo", action="append", dest="repos", help="repeatable; default: the z0 repos")
    ap.add_argument("--cache", default=str(Path.home() / ".cache" / "promotion-sim"))
    ap.add_argument("--events", help="events JSON (default <cache>/events.json)")
    ap.add_argument("--out", default="benchmarks/promotion_sim")
    ap.add_argument("--holdout", type=float, default=0.3)
    ap.add_argument("--fix-days", type=int, default=3)
    ap.add_argument("--strict", action="store_true", help="bad = reverted or ci_broke only")
    ap.add_argument("--refresh", action="store_true")
    a = ap.parse_args(argv)
    cache, out = Path(a.cache).expanduser(), Path(a.out)
    repos = a.repos or list(DEFAULT_REPOS)
    evp = Path(a.events) if a.events else cache / "events.json"
    if a.stage == "collect":
        print(json.dumps(collect(a.owner, repos, cache, a.refresh), indent=1))
        return 0
    if a.stage == "build":
        ev = build(cache, repos, log=lambda s: print(s, file=sys.stderr))
        evp.write_text(json.dumps(ev, indent=1))
        out.mkdir(parents=True, exist_ok=True)
        (out / "dataset.json").write_text(json.dumps({"schema": SCHEMA + ".dataset", "n": len(ev),
                                                      "events": dataset_rows(ev)}, indent=1))
        print(f"{len(ev)} events -> {evp}, {out / 'dataset.json'}")
        return 0
    if a.stage == "relabel":
        ev = relabel(cache, _load(evp))
        evp.write_text(json.dumps(ev, indent=1))
        out.mkdir(parents=True, exist_ok=True)
        (out / "dataset.json").write_text(json.dumps({"schema": SCHEMA + ".dataset", "n": len(ev),
                                                      "events": dataset_rows(ev)}, indent=1))
        print(f"relabelled {len(ev)} events")
        return 0
    events = [e for e in _load(evp) if scorable(e, a.fix_days)]
    train, hold, t_split = split_by_time(events, a.holdout)
    bad = _bad_fn(a.fix_days, a.strict)
    pols = policies()
    if a.stage in ("score", "prereg"):
        sel = select(train, pols, bad)
        named = ["human_all", "pa_v0", "pa_v0_auto", "auto_on_green", "auto_on_code_green", "decision7_draft"]
        print(f"train: {len(train)} events ({iso(train[0]['t'])} .. {iso(train[-1]['t'])}); holdout: {len(hold)} from {iso(t_split)}")
        print(f"base bad rate (train) {sel['base_bad_rate']:.3f}; eligible {len(sel['eligible'])}; pick {sel['pick']}")
        print(_fmt_table(sel["table"], named + ([sel["pick"]] if sel["pick"] and sel["pick"] not in named else [])))
        if a.stage == "prereg":
            out.mkdir(parents=True, exist_ok=True)
            pre = {"schema": SCHEMA + ".prereg", "events_file_sha": pa._sha(_load(evp), 16), "n_scorable": len(events),
                   "split": {"holdout_fraction": a.holdout, "holdout_from": iso(t_split), "n_train": len(train), "n_holdout": len(hold)},
                   "label": {"fix_days": a.fix_days, "strict": a.strict,
                             "bad": "reverted or ci_broke" + ("" if a.strict else f" or fix_{a.fix_days}d")},
                   "criterion": select.__doc__.strip(), "pick": sel["pick"],
                   "pick_rule": pols[sel["pick"]].__doc__ if sel["pick"] else None,
                   "holdout_pass": ("pick's auto bad rate on holdout <= 0.5 x holdout base bad rate AND pick auto-merges "
                                    ">= 20% of holdout events AND pick's bad_auto <= human_all's bad_through x 0.5"),
                   "compare": named, "train": {n: sel["table"][n] for n in named + [sel["pick"]] if n}}
            (out / "prereg.json").write_text(json.dumps(pre, indent=1))
            print(f"pre-registered -> {out / 'prereg.json'} (commit it before running `holdout`)")
        return 0
    pre = json.loads((out / "prereg.json").read_text())
    if pre["events_file_sha"] != pa._sha(_load(evp), 16):
        print("events changed since pre-registration; refusing to score the holdout", file=sys.stderr)
        return 2
    pick = pre["pick"]
    names = list(dict.fromkeys(pre["compare"] + [pick]))
    table = {n: score(hold, pols[n], bad) for n in names}
    m, base = table[pick], table["human_all"]["base_bad_rate"] or 0.0
    passed = (m["auto_bad_rate"] is not None and m["auto_bad_rate"] <= 0.5 * base and m["auto_share"] >= 0.2
              and m["bad_auto"] <= 0.5 * table["human_all"]["bad_through"])
    res = {"schema": SCHEMA + ".holdout", "prereg": pre["pick"], "n_holdout": len(hold), "base_bad_rate": base,
           "pass": passed, "table": table}
    (out / "results_holdout.json").write_text(json.dumps(res, indent=1))
    print(f"holdout: {len(hold)} events, base bad rate {base:.3f}; pick {pick}: {'PASS' if passed else 'FAIL'}")
    print(_fmt_table(table, names))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
