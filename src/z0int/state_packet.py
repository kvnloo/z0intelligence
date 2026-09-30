"""State Packet v0 — current-work state from local source-backed evidence (#22 P0 slice).

Three read-only adapters fan out concurrently and return structured
``EvidenceBundle`` objects (never essays):

* ``git``          — branch / HEAD / upstream / dirty tree / worktrees / branches ahead
* ``docs``         — top-level repository docs (priority heading, open checklists,
                     dangling path references, doc freshness)
* ``claude_code``  — local Claude Code transcripts (``~/.claude/projects``),
                     structural facts only (session, branch, files touched,
                     open delegations, a short redacted last-prompt excerpt)
* ``resource``     — ``resource.posture`` fact family (``z0int.posture``): is a
                     budget perishing (BURN), on pace, tight (RESERVE) or running
                     dry (OFFLOAD)? Shadow context only; never gates a transition.

A reducer publishes the packet: current claims, superseded claims,
contradictions, blocking unknowns, open work, source revisions and allowed
next transitions.  Every material claim carries evidence ids that resolve to
``context_resolve.EvidenceRef`` pointers.  A missing required fact forces
``OBSERVE``.  The packet is keyed by its source revisions; any change makes it
stale and a stale packet cannot authorize a transition.

Not a memory DB: raw sources stay authoritative.  The only persisted state is
the last packet + a claims history (for supersession) and a per-transcript
incremental scan cursor, all under ``~/.z0int/state/state_packet``.
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import paths
from .context_resolve import ContextPacket, EvidenceRef, InformationNeed

SCHEMA = "z0int.state_packet.v0"
POLICY_REVISION = "sp-v0.2"
# Cached packets must not outlive the code that built them: a reducer/adapter fix otherwise stays
# invisible until some source revision happens to change.
CODE_REVISION = __import__("hashlib").sha256(Path(__file__).read_bytes()).hexdigest()[:12]

INTENTS: dict[str, tuple[str, ...]] = {
    # intent -> facts required before any ACT transition is legal
    "resume": ("git.head", "git.branch", "git.dirty", "conv.latest_session"),
    "status": ("git.head", "git.branch", "git.dirty"),
    "plan": ("git.head", "docs.priority"),
}

DOC_NAMES = ("AGENTS.md", "CLAUDE.md", "README.md", "ROADMAP.md", "CONTRIBUTING.md")
DOC_MAX_BYTES = 128 * 1024
EXCERPT_CHARS = 160
MAX_SESSIONS = 4
MAX_LIST = 8


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _sha(text: str, n: int = 16) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:n]


_SECRET_RE = re.compile(
    r"(sk-[A-Za-z0-9_\-]{12,}|gh[pousr]_[A-Za-z0-9]{20,}|xox[abpr]-[A-Za-z0-9\-]{10,}|"
    r"AKIA[0-9A-Z]{16}|eyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]+|[A-Fa-f0-9]{40,}|[A-Za-z0-9+/]{48,}={0,2})"
)
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


def redact(text: str, limit: int = EXCERPT_CHARS) -> str:
    """Short, single-line, secret/email-scrubbed excerpt (local display only)."""
    t = " ".join(str(text).split())
    t = _SECRET_RE.sub("[redacted]", t)
    t = _EMAIL_RE.sub("[email]", t)
    return t if len(t) <= limit else t[: limit - 1] + "…"


def approx_tokens(text: str) -> int:
    """Conservative token estimate (~3.5 chars/token for mixed code/prose)."""
    return int(len(text) / 3.5) + 1


class Reader:
    """Counts raw source reads / bytes so packet cost is measurable."""

    def __init__(self) -> None:
        self.reads = 0
        self.bytes = 0
        self.transcript_bytes = 0
        self.log: list[str] = []

    def git(self, repo: Path, *args: str, timeout: float = 10.0) -> str | None:
        env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", LC_ALL="C")
        try:
            proc = subprocess.run(
                ["git", "--no-optional-locks", "-C", str(repo), *args],
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        self.reads += 1
        self.bytes += len(proc.stdout)
        self.log.append("git " + " ".join(args[:3]))
        if proc.returncode != 0:
            return None
        return proc.stdout

    def read_text(self, path: Path, *, max_bytes: int = DOC_MAX_BYTES) -> str | None:
        try:
            with path.open("rb") as fh:
                data = fh.read(max_bytes)
        except OSError:
            return None
        self.reads += 1
        self.bytes += len(data)
        self.log.append(f"read {path.name}")
        return data.decode("utf-8", "replace")


@dataclass
class Claim:
    key: str
    value: Any
    evidence: list[str]
    status: str = "observed"  # observed | provisional | superseded | contradicted | stale
    observed_at: str = ""
    material: bool = True
    note: str | None = None

    @property
    def id(self) -> str:
        return "c:" + _sha(self.key + json.dumps(self.value, sort_keys=True, default=str), 10)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "key": self.key,
            "value": self.value,
            "status": self.status,
            "evidence": list(self.evidence),
            "observed_at": self.observed_at,
            "material": self.material,
        }
        if self.note:
            d["note"] = self.note
        return d


@dataclass
class EvidenceBundle:
    """What one fan-out worker returns: structure, not prose."""

    facet: str
    claims: list[Claim] = field(default_factory=list)
    evidence: dict[str, EvidenceRef] = field(default_factory=dict)
    contradictions: list[dict[str, Any]] = field(default_factory=list)
    unknowns: list[dict[str, Any]] = field(default_factory=list)
    superseded: list[dict[str, Any]] = field(default_factory=list)
    open_work: list[dict[str, Any]] = field(default_factory=list)
    # candidate "what is the priority / critical path" declarations; reducer reconciles
    priority_decls: list[dict[str, Any]] = field(default_factory=list)
    coverage: str = "full"  # full | partial | none
    source_revision: Any = None
    reads: int = 0
    bytes_read: int = 0
    transcript_bytes: int = 0
    wall_ms: float = 0.0

    def ev(self, ref: EvidenceRef) -> str:
        eid = "e:" + _sha(ref.source_id + "|" + ref.source_version + "|" + ref.locator, 10)
        self.evidence[eid] = ref
        return eid

    def claim(self, key: str, value: Any, ref: EvidenceRef, **kw: Any) -> Claim:
        c = Claim(key=key, value=value, evidence=[self.ev(ref)], observed_at=ref.observed_at, **kw)
        self.claims.append(c)
        return c


# ---------------------------------------------------------------------------
# git adapter
# ---------------------------------------------------------------------------


def repo_root(path: str | Path) -> Path | None:
    p = Path(path).expanduser()
    try:
        proc = subprocess.run(
            ["git", "--no-optional-locks", "-C", str(p), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
            env=dict(os.environ, GIT_OPTIONAL_LOCKS="0"),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return Path(proc.stdout.strip()).resolve()


def _git_revision(repo: Path, reader: Reader) -> dict[str, str] | None:
    head = reader.git(repo, "rev-parse", "HEAD")
    if head is None:
        return None
    status = reader.git(repo, "status", "--porcelain=v1", "--untracked-files=normal") or ""
    refs = reader.git(repo, "for-each-ref", "--format=%(refname) %(objectname)", "refs/heads", "refs/remotes") or ""
    return {
        "head": head.strip(),
        "status": _sha(status, 12),
        "refs": _sha(refs, 12),
    }


def adapter_git(repo: Path, reader: Reader) -> EvidenceBundle:
    b = EvidenceBundle(facet="git")
    name = repo.name
    now = _now_iso()
    rev = _git_revision(repo, reader)
    if rev is None:
        b.coverage = "none"
        b.unknowns.append({"key": "git.head", "reason": f"{repo} is not a readable git repository"})
        return b
    b.source_revision = rev
    head = rev["head"]
    sid = f"git:{name}"

    def ref(locator: str, note: str | None = None) -> EvidenceRef:
        return EvidenceRef(
            source_id=sid,
            source_version=head[:12] + (f"+st{rev['status']}" if "status" in locator else ""),
            locator=locator,
            trust_class="code",
            observed_at=now,
            note=note,
        )

    branch = (reader.git(repo, "branch", "--show-current") or "").strip()
    b.claim("git.branch", branch or "(detached)", ref(f"{repo}:git branch --show-current"))

    log = reader.git(repo, "log", "-n", str(MAX_LIST), "--format=%h%x09%cI%x09%s") or ""
    commits = []
    for line in log.splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3:
            commits.append({"sha": parts[0], "date": parts[1], "subject": parts[2][:120]})
    if commits:
        b.claim("git.head", commits[0], ref(f"{repo}:git log -1 {commits[0]['sha']}"))
        b.claim("git.recent_commits", commits, ref(f"{repo}:git log -n {MAX_LIST}"), material=False)

    default = (reader.git(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD") or "").strip()
    if not default:
        for cand in ("origin/main", "origin/master", "main", "master"):
            if reader.git(repo, "rev-parse", "--verify", "--quiet", cand) is not None:
                default = cand
                break
    if default:
        b.claim("git.default_branch", default, ref(f"{repo}:refs/remotes/origin/HEAD"))

    status = reader.git(repo, "status", "--porcelain=v1", "--branch", "--untracked-files=normal") or ""
    modified, untracked, upstream = [], [], None
    for line in status.splitlines():
        if line.startswith("## "):
            head_part, _, track = line[3:].partition(" [")
            if "..." in head_part:
                ahead = re.search(r"ahead (\d+)", track)
                behind = re.search(r"behind (\d+)", track)
                upstream = {
                    "name": head_part.split("...", 1)[1],
                    "ahead": int(ahead.group(1)) if ahead else 0,
                    "behind": int(behind.group(1)) if behind else 0,
                }
            continue
        path = line[3:]
        (untracked if line.startswith("??") else modified).append(path)
    dirty = {
        "clean": not (modified or untracked),
        "modified": modified[:MAX_LIST],
        "untracked": untracked[:MAX_LIST],
        "modified_count": len(modified),
        "untracked_count": len(untracked),
    }
    dirty_claim = b.claim("git.dirty", dirty, ref(f"{repo}:git status --porcelain"))
    if upstream:
        b.claim("git.upstream", upstream, ref(f"{repo}:git status --branch"))
    if modified:
        b.open_work.append(
            {"kind": "uncommitted_changes", "what": f"{len(modified)} modified file(s) on {branch or 'HEAD'}",
             "evidence": dirty_claim.evidence}
        )

    # local branches: tracking + ahead of default + attached worktree
    fmt = "%(refname:short)%09%(objectname:short)%09%(committerdate:iso-strict)%09%(worktreepath)%09%(upstream:short)"
    if default:
        fmt += f"%09%(ahead-behind:{default})"
    refs = reader.git(repo, "for-each-ref", "--sort=-committerdate", f"--format={fmt}", "refs/heads") or ""
    branches = []
    for line in refs.splitlines():
        parts = line.split("\t")
        if len(parts) < 5:
            continue
        row: dict[str, Any] = {"branch": parts[0], "sha": parts[1], "date": parts[2]}
        if parts[3]:
            row["worktree"] = os.path.relpath(parts[3], repo) if parts[3] != str(repo) else "."
        if parts[4]:
            row["upstream"] = parts[4]
        if len(parts) > 5 and parts[5].strip():
            ab = parts[5].split()
            if len(ab) == 2 and ab[0].isdigit():
                row["ahead_of_default"] = int(ab[0])
                row["behind_default"] = int(ab[1])
        branches.append(row)
    ahead = [r for r in branches if r.get("ahead_of_default", 0) > 0]
    ahead_claim = b.claim(
        "git.branches_ahead",
        {"vs": default or None, "complete": True, "local_branches": len(branches),
         "ahead": [{k: r[k] for k in ("branch", "ahead_of_default", "worktree") if k in r} for r in ahead[:MAX_LIST]]},
        ref(f"{repo}:git for-each-ref refs/heads"),
    )
    wts = [r for r in branches if r.get("worktree") not in (None, ".")]
    if wts:
        b.claim("git.worktrees", [{k: r[k] for k in ("branch", "worktree", "sha") if k in r} for r in wts][:MAX_LIST],
                ref(f"{repo}:git for-each-ref %(worktreepath)"))
    for r in ahead[:MAX_LIST]:
        b.open_work.append(
            {"kind": "unmerged_branch", "what": f"{r['branch']} is {r['ahead_of_default']} commit(s) ahead of {default}"
             + (f" (worktree {r['worktree']})" if r.get("worktree") not in (None, ".") else
                (" (checked out here)" if r.get("worktree") == "." else "")),
             "evidence": ahead_claim.evidence}
        )

    stash = reader.git(repo, "stash", "list") or ""
    if stash.strip():
        n = len(stash.splitlines())
        b.claim("git.stash_count", n, ref(f"{repo}:git stash list"))
        b.open_work.append({"kind": "stash", "what": f"{n} stash entr(y/ies)", "evidence": b.claims[-1].evidence})

    fetch_head = repo / ".git" / "FETCH_HEAD"
    if fetch_head.is_file():
        age_h = (time.time() - fetch_head.stat().st_mtime) / 3600.0
        b.claim(
            "git.remote_refs_age_hours",
            round(age_h, 1),
            ref(f"{fetch_head}:mtime"),
            material=False,
            note="ahead/behind is relative to the last fetch, not the live remote",
        )
        if age_h > 24:
            b.unknowns.append({"key": "git.remote_state", "reason": f"remote refs last fetched {age_h:.0f}h ago"})
    b.reads, b.bytes_read = reader.reads, reader.bytes
    return b


# ---------------------------------------------------------------------------
# docs adapter
# ---------------------------------------------------------------------------

_PATH_TOKEN = re.compile(r"`([A-Za-z0-9_.\-]+(?:/[A-Za-z0-9_.\-]+)+/?|[A-Za-z0-9_\-]+\.(?:md|py|json|toml|yaml|yml|ts|js|sh))`")


def _doc_files(repo: Path) -> list[Path]:
    out = [repo / n for n in DOC_NAMES if (repo / n).is_file()]
    return out


def adapter_docs(repo: Path, reader: Reader) -> EvidenceBundle:
    b = EvidenceBundle(facet="docs")
    docs = _doc_files(repo)
    now = _now_iso()
    if not docs:
        b.coverage = "none"
        b.unknowns.append({"key": "docs.any", "reason": "no top-level AGENTS/README/ROADMAP docs"})
        return b
    # one git log pass for doc freshness (last commit touching each doc)
    names = {d.name for d in docs}
    last_touch: dict[str, dict[str, Any]] = {}
    log = reader.git(repo, "log", "-n", "400", "--format=%x00%h %cI", "--name-only", "--", *sorted(names)) or ""
    idx = 0
    for block in log.split("\x00")[1:]:
        lines = [x for x in block.splitlines() if x.strip()]
        if not lines:
            continue
        idx += 1
        sha, _, date = lines[0].partition(" ")
        for f in lines[1:]:
            last_touch.setdefault(f, {"sha": sha, "date": date})
    revs: dict[str, str] = {}
    priority_set = False
    for d in docs:
        text = reader.read_text(d)
        if text is None:
            continue
        version = "sha256:" + _sha(text, 12)
        revs[d.name] = version

        def ref(line: int, d: Path = d, version: str = version) -> EvidenceRef:
            return EvidenceRef(
                source_id=f"doc:{repo.name}/{d.name}",
                source_version=version,
                locator=f"{d.relative_to(repo)}:L{line}",
                trust_class="project_constraint",
                observed_at=now,
            )

        lines = text.splitlines()
        if d.name in last_touch:
            b.claim(f"docs.last_commit[{d.name}]", last_touch[d.name], ref(1), material=False)
        # every heading that declares a P0 / critical path is a candidate priority claim
        heading_i, heading = 0, ""
        for i, line in enumerate(lines):
            hm = re.match(r"^#{1,4}\s+(.*)$", line)
            if hm:
                heading_i, heading = i, hm.group(1).strip()
                if re.search(r"\bP0\b|critical path", heading, re.I):
                    b.priority_decls.append({"value": heading[:120], "evidence": [b.ev(ref(i + 1))], "source": d.name})
            elif heading and re.match(r"^\**Priority:?\**:?\s*critical path", line.strip(), re.I):
                b.priority_decls.append({"value": heading[:120], "evidence": [b.ev(ref(heading_i + 1))], "source": d.name})
        # priority: first P0 heading (ROADMAP first, else any doc)
        if not priority_set and d.name in ("ROADMAP.md", "README.md", "AGENTS.md"):
            for i, line in enumerate(lines):
                if re.match(r"^#{1,4}\s.*\bP0\b", line):
                    heading = line.lstrip("#").strip()
                    status = None
                    for j in range(i + 1, min(i + 6, len(lines))):
                        m = re.match(r"^\**Status:?\**:?\s*(.+)$", lines[j].strip())
                        if m:
                            status = m.group(1).strip("* ")
                            break
                    b.claim("docs.priority", {"heading": heading[:120], "status": status, "doc": d.name}, ref(i + 1))
                    priority_set = True
                    break
        # open checklist items
        todo = [(i + 1, ln.strip()[6:].strip()) for i, ln in enumerate(lines) if re.match(r"^\s*[-*] \[ \]", ln)]
        if todo:
            b.claim(
                f"docs.open_items[{d.name}]",
                {"count": len(todo), "first": [redact(t, 90) for _, t in todo[:3]]},
                ref(todo[0][0]),
            )
            b.open_work.append({"kind": "doc_checklist", "what": f"{d.name}: {len(todo)} unchecked item(s)",
                                "evidence": b.claims[-1].evidence})
        # dangling path references => docs drift contradiction
        seen: set[str] = set()
        for i, ln in enumerate(lines):
            for m in _PATH_TOKEN.finditer(ln):
                tok = m.group(1).rstrip("/")
                if tok in seen or tok.startswith(("http", "~", "/", ".")) or "*" in tok:
                    continue
                seen.add(tok)
                if "/" not in tok:
                    continue  # bare filenames are too ambiguous to call dangling
                first = tok.split("/", 1)[0]
                if not (repo / first).is_dir():
                    continue  # e.g. owner/repo, n/a — not a repo path claim
                if not (repo / tok).exists():
                    doc_ev = b.ev(ref(i + 1))
                    b.contradictions.append(
                        {
                            "key": f"path:{tok}",
                            "kind": "docs_drift",
                            "claims": [
                                {"value": "referenced as existing", "evidence": [doc_ev]},
                                {"value": "absent from working tree", "evidence": [b.ev(EvidenceRef(
                                    source_id=f"git:{repo.name}", source_version="worktree",
                                    locator=f"{repo}/{tok}", trust_class="code", observed_at=now))]},
                            ],
                        }
                    )
        if len(b.contradictions) > 12:
            b.contradictions = b.contradictions[:12]
    if not priority_set:
        b.unknowns.append({"key": "docs.priority", "reason": "no P0 heading found in ROADMAP/README/AGENTS"})
    b.source_revision = revs
    b.reads, b.bytes_read = reader.reads, reader.bytes
    return b


# ---------------------------------------------------------------------------
# GitHub adapter (opt-in: network; read-only `gh` queries)
# ---------------------------------------------------------------------------

_P0_LINE = re.compile(r"\*\*(P0\b[^*]{0,160})\*\*")


def _gh_slug(repo: Path, reader: Reader) -> str | None:
    url = (reader.git(repo, "remote", "get-url", "origin") or "").strip()
    m = re.search(r"github\.com[:/]([^/\s]+)/([^/\s]+?)(?:\.git)?$", url)
    return f"{m.group(1)}/{m.group(2)}" if m else None


def adapter_github(repo: Path, reader: Reader, *, timeout: float = 15.0) -> EvidenceBundle:
    """Open issues that *declare* a P0 (bold ``**P0 …**`` line) + open PRs. Public repo metadata only."""
    b = EvidenceBundle(facet="github")
    slug = _gh_slug(repo, reader)
    if not slug:
        b.coverage = "none"
        b.unknowns.append({"key": "gh.repo", "reason": "origin is not a GitHub remote"})
        return b
    now = _now_iso()

    def gh(*args: str) -> Any:
        try:
            proc = subprocess.run(["gh", *args, "-R", slug], capture_output=True, text=True, timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return None
        reader.reads += 1
        reader.bytes += len(proc.stdout)
        if proc.returncode != 0:
            return None
        try:
            return json.loads(proc.stdout)
        except ValueError:
            return None

    issues = gh("issue", "list", "--state", "open", "--limit", "40", "--json", "number,title,updatedAt,body")
    prs = gh("pr", "list", "--state", "open", "--limit", "20", "--json", "number,title,headRefName,isDraft,updatedAt")
    if issues is None and prs is None:
        b.coverage = "none"
        b.unknowns.append({"key": "gh.issues", "reason": f"gh unavailable/unauthenticated for {slug}"})
        return b
    revs = {}
    for it in issues or []:
        revs[f"#{it['number']}"] = it.get("updatedAt", "")
        m = _P0_LINE.search(it.get("body") or "")
        if not m:
            continue
        ref = EvidenceRef(source_id=f"github:{slug}#{it['number']}", source_version=it.get("updatedAt", ""),
                          locator=f"https://github.com/{slug}/issues/{it['number']}", trust_class="authoritative_task",
                          observed_at=now)
        decl = redact(m.group(1).strip(), 120)
        c = b.claim(f"gh.priority[#{it['number']}]", {"declares": decl, "issue_title": redact(it["title"], 90),
                                                       "updated": it.get("updatedAt")}, ref)
        b.priority_decls.append({"value": decl, "evidence": c.evidence, "source": f"github#{it['number']}"})
    for pr in (prs or [])[:MAX_LIST]:
        revs[f"pr{pr['number']}"] = pr.get("updatedAt", "")
        ref = EvidenceRef(source_id=f"github:{slug}#pr{pr['number']}", source_version=pr.get("updatedAt", ""),
                          locator=f"https://github.com/{slug}/pull/{pr['number']}", trust_class="authoritative_task",
                          observed_at=now)
        b.open_work.append({"kind": "open_pr", "what": f"PR #{pr['number']} {redact(pr['title'], 70)} "
                            f"({pr.get('headRefName')}{', draft' if pr.get('isDraft') else ''})",
                            "evidence": [b.ev(ref)]})
    b.source_revision = {"slug": slug, "items": _sha(json.dumps(revs, sort_keys=True), 12)}
    return b


def _gh_revision(repo: Path, reader: Reader, timeout: float = 15.0) -> dict[str, str] | None:
    """Cheap probe: open issue/PR numbers + updatedAt (no bodies).

    Network calls cost ~1-2s each, so the probe result is reused for
    ``Z0INT_PACKET_GH_TTL`` seconds (default 300): GitHub-side changes are
    detected with at most that lag (declared in the packet as ``github_ttl_s``).
    """
    slug = _gh_slug(repo, reader)
    if not slug:
        return None
    ttl = float(os.environ.get("Z0INT_PACKET_GH_TTL", "300"))
    cache = _state_dir(repo) / "gh_revision.json"
    try:
        cached = json.loads(cache.read_text(encoding="utf-8")) if cache.is_file() else None
    except (OSError, ValueError):
        cached = None
    if cached and cached.get("value", {}).get("slug") == slug and time.time() - float(cached.get("ts", 0)) < ttl:
        return cached["value"]
    value = _gh_revision_live(slug, reader, timeout)
    if value.get("items") != "unavailable":
        try:
            cache.write_text(json.dumps({"ts": time.time(), "value": value}), encoding="utf-8")
        except OSError:
            pass
    return value


def _gh_revision_live(slug: str, reader: Reader, timeout: float) -> dict[str, str]:
    items: dict[str, str] = {}
    for kind, limit in (("issue", "40"), ("pr", "20")):
        try:
            proc = subprocess.run(["gh", kind, "list", "--state", "open", "--limit", limit, "--json", "number,updatedAt",
                                   "-R", slug], capture_output=True, text=True, timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return {"slug": slug, "items": "unavailable"}
        reader.reads += 1
        reader.bytes += len(proc.stdout)
        try:
            for it in json.loads(proc.stdout or "[]"):
                items[f"{kind}{it['number']}"] = it.get("updatedAt", "")
        except ValueError:
            return {"slug": slug, "items": "unavailable"}
    return {"slug": slug, "items": _sha(json.dumps(items, sort_keys=True), 12)}


# ---------------------------------------------------------------------------
# Claude Code transcript adapter (local only; structural facts)
# ---------------------------------------------------------------------------


def _encode_project_dir(path: Path) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


def default_projects_root() -> Path:
    return Path(os.environ.get("Z0INT_CLAUDE_PROJECTS", "~/.claude/projects")).expanduser()


def transcript_candidates(repo: Path, projects_root: Path) -> list[Path]:
    """Transcript files whose project dir is the repo, inside it, or an ancestor of it."""
    if not projects_root.is_dir():
        return []
    enc_repo = _encode_project_dir(repo)
    ancestors = {_encode_project_dir(p) for p in repo.parents if str(p) not in ("/",)}
    out: list[Path] = []
    for d in sorted(projects_root.iterdir()):
        if not d.is_dir():
            continue
        n = d.name
        if n == enc_repo or n.startswith(enc_repo + "-") or n in ancestors:
            out.extend(Path(p) for p in glob.glob(str(d / "*.jsonl")))
            out.extend(Path(p) for p in glob.glob(str(d / "*" / "subagents" / "*.jsonl")))
    return sorted(out)


def _within(path: str | None, repo: Path) -> bool:
    if not path:
        return False
    s = str(path)
    r = str(repo)
    return s == r or s.startswith(r + "/")


def _empty_summary() -> dict[str, Any]:
    return {
        "session_id": None, "hits": 0, "first_ts": None, "last_ts": None, "title": None,
        "branch": None, "cwd": None, "files": [], "delegated": {}, "completed": [],
        "last_prompt": None, "last_prompt_line": None, "last_line": 0, "commits": 0, "is_subagent": False,
        "mentions": 0,
    }


def _scan_lines(lines: list[str], start_line: int, repo: Path, s: dict[str, Any]) -> None:
    home = str(Path.home())
    rs = str(repo)
    tilde = "~" + rs[len(home):] if rs.startswith(home) else None
    for off, line in enumerate(lines):
        lineno = start_line + off
        try:
            r = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(r, dict):
            continue
        t = r.get("type")
        s["session_id"] = s["session_id"] or r.get("sessionId")
        if t == "ai-title" and r.get("aiTitle"):
            s["title"] = str(r["aiTitle"])
        cwd = r.get("cwd")
        in_repo = _within(cwd, repo)
        msg = r.get("message") if isinstance(r.get("message"), dict) else {}
        content = msg.get("content") if isinstance(msg, dict) else None
        if t == "assistant" and isinstance(content, list):
            for c in content:
                if not isinstance(c, dict) or c.get("type") != "tool_use":
                    continue
                inp = c.get("input") if isinstance(c.get("input"), dict) else {}
                name = c.get("name")
                fp = inp.get("file_path") or inp.get("notebook_path") or inp.get("path")
                cmd = str(inp.get("command") or "")
                mentions = _within(fp, repo) or (rs in cmd) or bool(tilde and tilde in cmd)
                if name in ("Edit", "Write", "NotebookEdit", "MultiEdit") and _within(fp, repo):
                    in_repo = True  # writing into the repo counts as working in it
                    rel = os.path.relpath(fp, repo)
                    if rel in s["files"]:
                        s["files"].remove(rel)
                    s["files"].append(rel)
                    s["files"] = s["files"][-20:]
                elif mentions:
                    s["mentions"] = int(s.get("mentions", 0)) + 1
                if name in ("Agent", "Task") and in_repo:
                    s["delegated"][str(c.get("id"))] = {"what": redact(inp.get("description") or "", 80), "line": lineno}
                if name == "Bash" and re.search(r"\bgit\b[^|;&]*\bcommit\b", cmd) and in_repo:
                    s["commits"] += 1
        if t == "user" and isinstance(content, list):
            for c in content:
                if isinstance(c, dict) and c.get("type") == "tool_result" and str(c.get("tool_use_id")) in s["delegated"]:
                    s["completed"].append(str(c.get("tool_use_id")))
        if t == "user" and r.get("isSidechain") is not True and not r.get("isMeta"):
            text = content if isinstance(content, str) else None
            if isinstance(content, list):
                texts = [c.get("text") for c in content if isinstance(c, dict) and c.get("type") == "text"]
                text = texts[0] if texts else None
            if text and not text.lstrip().startswith("<") and in_repo:
                s["last_prompt"] = redact(text)
                s["last_prompt_line"] = lineno
        if in_repo:
            s["hits"] += 1
            ts = r.get("timestamp")
            if ts:
                s["first_ts"] = s["first_ts"] or ts
                s["last_ts"] = ts
            if cwd and _within(cwd, repo):
                s["cwd"] = os.path.relpath(cwd, repo)
                if r.get("gitBranch") and r.get("gitBranch") != "HEAD":  # "HEAD" = detached/unknown
                    s["branch"] = r.get("gitBranch")


def _cursor_path(repo: Path) -> Path:
    d = paths.home() / "state" / "state_packet" / "transcript_cursors"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{_sha(str(repo), 16)}.json"


def summarize_transcript(path: Path, repo: Path, reader: Reader, cursors: dict[str, Any]) -> dict[str, Any] | None:
    """Incremental scan: JSONL is append-only, so only bytes after the cursor are read."""
    try:
        st = path.stat()
    except OSError:
        return None
    key = str(path)
    cur = cursors.get(key)
    offset, summary = 0, _empty_summary()
    if cur and cur.get("ino") == st.st_ino and cur.get("offset", 0) <= st.st_size:
        offset, summary = int(cur["offset"]), cur["summary"]
        if offset == st.st_size:
            return summary
    try:
        with path.open("rb") as fh:
            fh.seek(offset)
            data = fh.read()
    except OSError:
        return None
    reader.reads += 1
    reader.bytes += len(data)
    reader.transcript_bytes += len(data)
    reader.log.append(f"transcript {path.name}@{offset}")
    end = data.rfind(b"\n")
    if end < 0:
        return summary
    chunk = data[: end + 1].decode("utf-8", "replace").splitlines()
    summary["is_subagent"] = "subagents" in path.parts
    _scan_lines(chunk, int(summary.get("last_line", 0)) + 1, repo, summary)
    summary["last_line"] = int(summary.get("last_line", 0)) + len(chunk)
    cursors[key] = {"ino": st.st_ino, "offset": offset + end + 1, "summary": summary}
    return summary


_RELEVANT_FIELDS = ("hits", "last_ts", "branch", "cwd", "title", "files", "delegated", "completed", "last_prompt")


def scan_transcripts(repo: Path, projects_root: Path, reader: Reader) -> tuple[list[Path], list[tuple[Path, dict[str, Any]]]]:
    """Incrementally scan candidate transcripts; return (candidates, relevant sessions)."""
    files = transcript_candidates(repo, projects_root)
    cur_path = _cursor_path(repo)
    try:
        cursors = json.loads(cur_path.read_text(encoding="utf-8")) if cur_path.is_file() else {}
    except (OSError, ValueError):
        cursors = {}
    sessions = []
    for f in files:
        s = summarize_transcript(f, repo, reader, cursors)
        if s and s.get("hits"):
            sessions.append((f, s))
    try:
        cur_path.write_text(json.dumps(cursors), encoding="utf-8")
    except OSError:
        pass
    return files, sessions


def conversation_revision(sessions: list[tuple[Path, dict[str, Any]]]) -> dict[str, str]:
    """Revision of the *repo-relevant* conversation facts.

    Transcripts in ancestor project dirs grow constantly with unrelated work; keying
    on raw file size would invalidate every packet under ~/workspace on every turn.
    Instead the revision is a digest of the extracted relevant summary, so an
    unrelated append (read incrementally, a few KB) does not invalidate.
    """
    out = {}
    for f, s in sessions:
        out[_sha(str(f), 12)] = _sha(json.dumps({k: s.get(k) for k in _RELEVANT_FIELDS}, sort_keys=True, default=str), 12)
    return out


def adapter_claude_code(repo: Path, reader: Reader, projects_root: Path | None = None) -> EvidenceBundle:
    b = EvidenceBundle(facet="claude_code")
    root = projects_root or default_projects_root()
    files, sessions = scan_transcripts(repo, root, reader)
    b.source_revision = conversation_revision(sessions)
    b.reads, b.bytes_read = reader.reads, reader.bytes
    if not sessions:
        b.coverage = "none"
        if files:
            # Source present, complete, and silent: absence is the answer, not a scanner fault.
            b.unknowns.append({
                "key": "conv.latest_session", "source_status": "no_match",
                "reason": f"none recorded: {len(files)} local Claude Code transcript(s) checked and none touched "
                          f"{repo.name}; the index is complete, so answer 'no recorded session' rather than guessing",
            })
        else:
            # Source absent or unreadable on this host: the fact is genuinely unknown.
            b.unknowns.append({
                "key": "conv.latest_session", "source_status": "source_unavailable",
                "reason": "Claude Code history is unavailable on this host (no readable transcripts); session facts "
                          "are UNKNOWN - abstain on questions that depend on them",
            })
        return b
    sessions.sort(key=lambda fs: fs[1].get("last_ts") or "", reverse=True)
    top_level = [fs for fs in sessions if not fs[1].get("is_subagent")]
    for rank, (f, s) in enumerate(sessions[:MAX_SESSIONS]):
        sid = str(s.get("session_id") or f.stem)
        short = sid[:8] + ("/sub:" + f.stem[-8:] if s.get("is_subagent") else "")

        def ref(line: int | None, f: Path = f, sid: str = sid, s: dict[str, Any] = s) -> EvidenceRef:
            return EvidenceRef(
                source_id=f"claude-code:{sid}" + (f"/{f.stem}" if s.get("is_subagent") else ""),
                source_version=f"lines={s.get('last_line')}",
                locator=f"{f.parent.name}/{f.name}" + (f":L{line}" if line else ""),
                trust_class="conversation",
                observed_at=s.get("last_ts") or "",
            )

        info = {
            "session": short,
            "title": redact(s["title"], 80) if s.get("title") else None,
            "first_ts": s.get("first_ts"),
            "last_ts": s.get("last_ts"),
            "cwd": s.get("cwd"),
            "branch": s.get("branch"),
            "subagent": bool(s.get("is_subagent")),
        }
        b.claim(f"conv.session[{short}]", info, ref(None), material=rank == 0)
        if s.get("files"):
            b.claim(f"conv.files_touched[{short}]", s["files"][-MAX_LIST:], ref(None), material=False)
        if s.get("last_prompt"):
            b.claim(f"conv.last_prompt[{short}]", s["last_prompt"], ref(s.get("last_prompt_line")), material=False)
        pending = {k: v for k, v in s.get("delegated", {}).items() if k not in set(s.get("completed", []))}
        for v in list(pending.values())[:MAX_LIST]:
            e = b.ev(ref(v.get("line")))
            b.open_work.append({"kind": "delegation_pending", "what": f"[{short}] {v['what']}", "evidence": [e]})
    # complete index of sessions that worked here (lets a consumer abstain on absence)
    index = [{"session": str(s.get("session_id") or f.stem)[:8] + ("/sub:" + f.stem[-8:] if s.get("is_subagent") else ""),
              "cwd": s.get("cwd"), "last_ts": (s.get("last_ts") or "")[:16]} for f, s in sessions[:20]]
    b.claim("conv.sessions_here", {"complete": len(sessions) <= 20, "count": len(sessions),
                                   "transcripts_scanned": len(files), "sessions": index},
            EvidenceRef(source_id="claude-code:index", source_version=f"files={len(files)}",
                        locator=str(root), trust_class="conversation", observed_at=_now_iso()), material=False)
    # Prefer a top-level session; if only sub-agent sessions touched this repo, the latest one is still a
    # known fact (marked /sub:) — reporting "unknown" when the evidence exists would conflate unknown with absent.
    latest = top_level[:1] or sessions[:1]
    if latest:
        f, s = latest[0]
        value = str(s.get("session_id") or f.stem)[:8] + ("/sub:" + f.stem[-8:] if s.get("is_subagent") else "")
        b.claim("conv.latest_session", value,
                EvidenceRef(source_id=f"claude-code:{s.get('session_id')}", source_version=f"lines={s.get('last_line')}",
                            locator=f"{f.parent.name}/{f.name}", trust_class="conversation",
                            observed_at=s.get("last_ts") or ""))
    return b


# ---------------------------------------------------------------------------
# resource posture adapter (shadow: informs, never gates)
# ---------------------------------------------------------------------------


def _posture_revision() -> dict[str, Any] | None:
    """Verdict-level revision: stable while postures hold, so drifting numbers don't bust the packet cache."""
    try:
        from .posture import current_posture

        p = current_posture()
        return {"posture": p["revision"], "factory": p["factory"]["posture"]}
    except Exception:  # fail-open: posture must never break packet builds
        return None


def adapter_resource(reader: Reader) -> EvidenceBundle:
    from .posture import current_posture, render_line

    b = EvidenceBundle(facet="resource")
    p = current_posture()
    b.source_revision = {"posture": p["revision"], "factory": p["factory"]["posture"]}
    ok = [s for s in p["sources"] if s.get("status") == "ok"]
    if not any(r["remaining"] is not None for r in p["pools"]):
        b.coverage = "none"
        b.unknowns.append({"key": "resource.posture", "source_status": "source_unavailable",
                           "reason": "no metered budget pool observed (codexbar cache / posture.local.json); "
                                     "budget posture is UNKNOWN"})
        return b
    b.coverage = "full" if all(s.get("status") in ("ok", "absent") for s in p["sources"]) else "partial"
    src = next((s for s in p["sources"] if s["source"] == "codexbar"), {})
    observed = max((r.get("observation_age_hours") or 0) for r in p["pools"] if r["remaining"] is not None)
    ref = EvidenceRef(source_id="posture:" + "+".join(sorted(s["source"] for s in ok)),
                      source_version=p["revision"], locator=str(src.get("path") or "z0int posture"),
                      trust_class="derived_memory", observed_at=p["now"],
                      note=f"derived from usage snapshots up to {observed:.1f}h old; re-run `z0int posture`")
    fac = p["factory"]
    b.claim("resource.posture", {
        "factory": fac["posture"], "action": fac["action"], "prefer": fac["prefer"], "avoid": fac["avoid"],
        "offload_targets": fac["offload_targets"][:6], "earliest_reset": fac.get("earliest_reset"),
        "groups": {g: v["posture"] for g, v in p["groups"].items()
                   if any(r["group"] == g and r["remaining"] is not None for r in p["pools"])},
        "enforce": p["enforce"], "line": render_line(p),
    }, ref, material=False)
    for g, v in p["groups"].items():
        row = next(r for r in p["pools"] if r["id"] == v["binding_pool"])
        if row["remaining"] is None:
            continue
        b.claim(f"resource.posture[{g}]", {"posture": v["posture"], "binding_pool": row["id"],
                                           "arithmetic": row["arithmetic"], "resets_at": row["resets_at"]},
                ref, material=False)
    return b


# ---------------------------------------------------------------------------
# reducer
# ---------------------------------------------------------------------------


def _state_dir(repo: Path) -> Path:
    d = paths.home() / "state" / "state_packet" / _sha(str(repo), 16)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load_prior(repo: Path) -> dict[str, Any] | None:
    p = _state_dir(repo) / "latest.json"
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None
    except (OSError, ValueError):
        return None


def _cache_key(intent: str, required: tuple[str, ...], repo: Path, revisions: dict[str, Any]) -> str:
    return _sha(json.dumps(
        {"schema": SCHEMA, "policy": POLICY_REVISION, "code": CODE_REVISION, "intent": intent, "required": list(required),
         "scope": str(repo), "rev": revisions}, sort_keys=True), 20)


def _transitions(claims: dict[str, dict[str, Any]], blocking: list[dict[str, Any]],
                 contradictions: list[dict[str, Any]], unknowns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [
        {"kind": "OBSERVE", "action": "refresh_packet", "why": "always legal; re-reads changed sources only"}
    ]
    for u in blocking:
        out.append({"kind": "OBSERVE", "action": f"resolve_unknown:{u['key']}", "why": u["reason"]})
    for c in contradictions[:3]:
        out.append({"kind": "OBSERVE", "action": f"reconcile:{c['key']}", "why": c.get("kind", "conflict")})
    if any(u["key"] == "git.remote_state" for u in unknowns):
        out.append({"kind": "OBSERVE", "action": "git_fetch", "why": "ahead/behind computed from stale remote refs"})
    blocked = [u["key"] for u in blocking]
    branch = (claims.get("git.branch") or {}).get("value")
    default = str((claims.get("git.default_branch") or {}).get("value") or "")
    dirty = (claims.get("git.dirty") or {}).get("value") or {}
    upstream = (claims.get("git.upstream") or {}).get("value") or {}
    on_default = bool(branch) and default.split("/")[-1] == branch

    def act(action: str, why: str, pre: list[str], kind: str = "ACT") -> None:
        row = {"kind": kind, "action": action, "why": why,
               "preconditions": [claims[k]["id"] for k in pre if k in claims]}
        if blocked and kind == "ACT":
            row["kind"] = "BLOCKED"
            row["blocked_by"] = blocked
        out.append(row)

    if branch:
        if dirty and dirty.get("modified_count"):
            if on_default:
                act("branch_before_commit", f"{dirty.get('modified_count', 0)} modified on default branch {branch}",
                    ["git.branch", "git.dirty"], kind="ESCALATE")
            else:
                act("continue_on_branch", f"{branch} has uncommitted work", ["git.branch", "git.dirty"])
                act("commit_local", f"commit on {branch} (local only)", ["git.branch", "git.dirty"])
        elif not on_default:
            act("continue_on_branch", f"{branch} has no modified tracked files", ["git.branch", "git.dirty"])
        if upstream.get("ahead"):
            act("push_or_open_pr", f"{branch} is {upstream['ahead']} ahead of {upstream.get('name')}; needs human authorization",
                ["git.branch", "git.upstream"], kind="ESCALATE")
        if upstream.get("behind"):
            act("rebase_or_merge_upstream", f"{branch} is {upstream['behind']} behind {upstream.get('name')}",
                ["git.branch", "git.upstream"])
    for t in out:
        t["authorizes"] = False
    return out


def reduce_bundles(
    bundles: list[EvidenceBundle],
    *,
    repo: Path,
    intent: str,
    required: tuple[str, ...],
    prior: dict[str, Any] | None,
) -> dict[str, Any]:
    evidence: dict[str, dict[str, Any]] = {}
    claims: dict[str, dict[str, Any]] = {}
    contradictions: list[dict[str, Any]] = []
    unknowns: list[dict[str, Any]] = []
    open_work: list[dict[str, Any]] = []
    superseded: list[dict[str, Any]] = []
    for b in bundles:
        for eid, ref in b.evidence.items():
            evidence[eid] = ref.to_dict()
        for c in b.claims:
            claims[c.key] = c.to_dict()
        contradictions.extend(b.contradictions)
        unknowns.extend(b.unknowns)
        open_work.extend(b.open_work)

    # priority declarations from docs / GitHub: more than one distinct declaration is an
    # explicit contradiction (never silently resolved by picking the first heading)
    decls: list[dict[str, Any]] = []
    for b in bundles:
        for d in b.priority_decls:
            if all(d["value"].lower() != x["value"].lower() for x in decls):
                decls.append(d)
    if len(decls) > 1:
        contradictions.insert(0, {
            "key": "priority",
            "kind": "priority_conflict",
            "contests": "docs.priority",
            "claims": [{"value": f"{d['value']} ({d['source']})", "evidence": d["evidence"]} for d in decls[:6]],
        })

    # cross-source temporal supersession: a conversation's branch observation vs git now
    git_branch = claims.get("git.branch")
    if git_branch:
        for k, c in list(claims.items()):
            if not k.startswith("conv.session["):
                continue
            v = c["value"]
            if v.get("cwd") == "." and v.get("branch") and v["branch"] != git_branch["value"]:
                superseded.append({
                    "key": "git.branch",
                    "old_value": v["branch"],
                    "old_observed_at": v.get("last_ts"),
                    "old_evidence": c["evidence"],
                    "current_value": git_branch["value"],
                    "superseded_by": git_branch["id"],
                    "why": "newer git observation supersedes branch recorded in conversation",
                })

    # packet-history supersession: claim values that changed since the prior packet
    if prior and prior.get("policy_revision") == POLICY_REVISION:  # different policy => not comparable
        for k, old in (prior.get("claims_index") or {}).items():
            new = claims.get(k)
            if new is None or old.get("id") == new["id"] or not old.get("material"):
                continue
            if k.startswith("conv."):
                continue  # conversation facts are already histories; their growth is not supersession
            superseded.append({
                "key": k,
                "old_value": old.get("value"),
                "old_observed_at": old.get("observed_at"),
                "old_packet": prior.get("packet_id"),
                "current_value": new["value"],
                "superseded_by": new["id"],
                "why": "value changed since prior packet (source revision moved)",
            })
        seen = {(s["key"], json.dumps(s["old_value"], default=str), s.get("old_observed_at")) for s in superseded}
        for s in (prior.get("superseded_claims") or [])[:6]:  # history is kept, not overwritten
            sig = (s["key"], json.dumps(s["old_value"], default=str), s.get("old_observed_at"))
            if sig not in seen:
                seen.add(sig)
                superseded.append(s)
    superseded = superseded[:12]

    # blocking unknowns: required facts with no current claim
    blocking = []
    for k in required:
        if k not in claims:
            src = next((u for u in unknowns if u["key"] == k), {})
            row = {"key": k, "reason": src.get("reason", "no source produced this fact"), "required_by": intent}
            if src.get("source_status"):
                row["source_status"] = src["source_status"]
            blocking.append(row)
    for c in contradictions:
        contested = c.get("contests") or c["key"]
        if contested in required:
            blocking.append({"key": contested, "reason": f"contradictory evidence ({c['kind']})", "required_by": intent})
    non_blocking = [u for u in unknowns if u["key"] not in {x["key"] for x in blocking}]

    transitions = _transitions(claims, blocking, contradictions, non_blocking)
    mode = "OBSERVE" if blocking else "ACT"
    return {
        "claims_index": claims,
        "evidence": evidence,
        "contradictions": contradictions,
        "blocking_unknowns": blocking,
        "unknowns": non_blocking,
        "open_work": open_work,
        "superseded_claims": superseded,
        "allowed_transitions": transitions,
        "decision": {
            "mode": mode,
            "missing": [x["key"] for x in blocking],
            "reason": ("required fact(s) missing/contested: " + ", ".join(x["key"] for x in blocking))
            if blocking else "all required facts observed",
        },
    }


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------


def source_revisions(repo: Path, projects_root: Path | None = None, reader: Reader | None = None,
                     *, github: bool = False, resource: bool = True) -> dict[str, Any]:
    """Cheap revision probe used for invalidation: 3 git calls, doc hashes, incremental transcript scan
    (+ 2 metadata-only ``gh`` calls when the GitHub adapter is enabled)."""
    r = reader or Reader()
    out_gh = {"github": _gh_revision(repo, r)} if github else {}
    git = _git_revision(repo, r)
    docs = {}
    for d in _doc_files(repo):
        try:
            data = d.read_bytes()[:DOC_MAX_BYTES]
            r.reads += 1
            r.bytes += len(data)
        except OSError:
            continue
        docs[d.name] = "sha256:" + hashlib.sha256(data).hexdigest()[:12]
    _, sessions = scan_transcripts(repo, projects_root or default_projects_root(), r)
    return {"git": git, "docs": docs, "claude_code": conversation_revision(sessions),
            **({"resource": _posture_revision()} if resource else {}), **out_gh}


def build_state_packet(
    repo: str | Path,
    *,
    intent: str = "resume",
    require: tuple[str, ...] | list[str] | None = None,
    projects_root: str | Path | None = None,
    use_cache: bool = True,
    store: bool = True,
    adapters: tuple[str, ...] = ("git", "docs", "claude_code", "resource"),
    github: bool | None = None,
) -> dict[str, Any]:
    """Build (or reuse) the State Packet for ``repo``. Read-only against sources.

    ``github`` (default: env ``Z0INT_PACKET_GH=1``) adds the network ``gh`` adapter.
    """
    t0 = time.perf_counter()
    root = repo_root(repo) or Path(repo).expanduser().resolve()
    proj = Path(projects_root).expanduser() if projects_root else default_projects_root()
    required = tuple(require) if require else INTENTS.get(intent, INTENTS["resume"])
    if github is None:
        github = os.environ.get("Z0INT_PACKET_GH") == "1"
    if github and "github" not in adapters:
        adapters = (*adapters, "github")
    probe = Reader()
    revisions = source_revisions(root, proj, probe, github=bool(github), resource="resource" in adapters)
    key = _cache_key(intent, required, root, revisions)
    prior = _load_prior(root) if (use_cache or store) else None
    if use_cache and prior and prior.get("packet_id") == key:
        out = dict(prior)
        m = dict(out.get("measurements") or {})
        m.update({"cache_hit": True, "wall_ms": round((time.perf_counter() - t0) * 1000, 2),
                  "raw_source_reads": probe.reads, "bytes_read": probe.bytes,
                  "transcript_bytes_read": probe.transcript_bytes, "cold_wall_ms": m.get("wall_ms")})
        out["measurements"] = m
        return out

    runners: dict[str, Callable[[Reader], EvidenceBundle]] = {
        "git": lambda rd: adapter_git(root, rd),
        "docs": lambda rd: adapter_docs(root, rd),
        "claude_code": lambda rd: adapter_claude_code(root, rd, proj),
        "github": lambda rd: adapter_github(root, rd),
        "resource": lambda rd: adapter_resource(rd),
    }

    def run(name: str) -> EvidenceBundle:
        rd = Reader()
        s = time.perf_counter()
        try:
            bundle = runners[name](rd)
        except Exception as exc:  # adapter failure is an unknown, not a crash
            bundle = EvidenceBundle(facet=name, coverage="none")
            bundle.unknowns.append({"key": f"{name}.adapter", "reason": f"adapter error: {type(exc).__name__}: {exc}"})
        bundle.wall_ms = (time.perf_counter() - s) * 1000
        bundle.reads, bundle.bytes_read, bundle.transcript_bytes = rd.reads, rd.bytes, rd.transcript_bytes
        return bundle

    names = [a for a in adapters if a in runners]
    with ThreadPoolExecutor(max_workers=len(names) or 1) as pool:
        bundles = list(pool.map(run, names))
    reduced = reduce_bundles(bundles, repo=root, intent=intent, required=required, prior=prior)
    claims = reduced.pop("claims_index")
    current = [c for c in claims.values()]
    packet: dict[str, Any] = {
        "schema": SCHEMA,
        "packet_id": key,
        "built_at": _now_iso(),
        "intent": {"name": intent, "required": list(required)},
        "scope": {"repo": str(root), "repo_name": root.name},
        "decision": reduced.pop("decision"),
        "current_claims": current,
        **reduced,
        "source_revisions": revisions,
        "policy_revision": POLICY_REVISION,
        "coverage": {b.facet: b.coverage for b in bundles},
        "freshness": {"github_probe_ttl_s": float(os.environ.get("Z0INT_PACKET_GH_TTL", "300"))} if github else {},
        "claims_index": {k: {"id": v["id"], "value": v["value"], "observed_at": v["observed_at"],
                             "material": v["material"]} for k, v in claims.items()},
        "cache": {"key": key, "prior_packet_id": (prior or {}).get("packet_id")},
        "privacy": {"local_only": True, "excerpt_max_chars": EXCERPT_CHARS,
                    "note": "conversation content limited to redacted short excerpts/titles"},
    }
    total_reads = probe.reads + sum(b.reads for b in bundles)
    total_bytes = probe.bytes + sum(b.bytes_read for b in bundles)
    transcript_bytes = probe.transcript_bytes + sum(b.transcript_bytes for b in bundles)
    body = json.dumps(packet, ensure_ascii=False)
    packet["measurements"] = {
        "cache_hit": False,
        "wall_ms": round((time.perf_counter() - t0) * 1000, 2),
        "adapters": {b.facet: {"wall_ms": round(b.wall_ms, 2), "reads": b.reads, "bytes_read": b.bytes_read,
                               "claims": len(b.claims)} for b in bundles},
        "raw_source_reads": total_reads,
        "bytes_read": total_bytes,
        "transcript_bytes_read": transcript_bytes,
        "packet_bytes": len(body.encode("utf-8")),
        "concurrent_adapters": len(bundles),
        "network_calls": 0,
    }
    if store:
        d = _state_dir(root)
        (d / "latest.json").write_text(json.dumps(packet, ensure_ascii=False, indent=1), encoding="utf-8")
        with (d / "history.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"packet_id": key, "built_at": packet["built_at"],
                                 "prior": packet["cache"]["prior_packet_id"],
                                 "claims": {k: v["id"] for k, v in packet["claims_index"].items()}}) + "\n")
    return packet


def check_packet(packet: dict[str, Any], projects_root: str | Path | None = None) -> dict[str, Any]:
    """Is the packet still current? Compares stored vs live source revisions."""
    root = Path(packet["scope"]["repo"])
    proj = Path(projects_root).expanduser() if projects_root else default_projects_root()
    old = packet.get("source_revisions") or {}
    live = source_revisions(root, proj, github="github" in old, resource="resource" in old)
    changed = [k for k in ("git", "docs", "claude_code", "resource", "github") if (old.get(k) or {}) != (live.get(k) or {})]
    return {"valid": not changed, "changed_sources": changed, "packet_id": packet.get("packet_id")}


def authorize_transition(packet: dict[str, Any], action: str, *, projects_root: str | Path | None = None) -> dict[str, Any]:
    """Gate a transition on packet freshness + legality. Never grants what the packet does not list."""
    chk = check_packet(packet, projects_root)
    if not chk["valid"]:
        return {"allowed": False, "mode": "OBSERVE", "reason": "stale packet: " + ",".join(chk["changed_sources"]),
                "action": action}
    row = next((t for t in packet.get("allowed_transitions", []) if t["action"] == action), None)
    if row is None:
        return {"allowed": False, "mode": "OBSERVE", "reason": "transition not in packet's legal set", "action": action}
    if row["kind"] in ("BLOCKED",):
        return {"allowed": False, "mode": "OBSERVE", "reason": "blocked by " + ",".join(row.get("blocked_by", [])),
                "action": action}
    if row["kind"] == "ESCALATE":
        return {"allowed": False, "mode": "ESCALATE", "reason": row["why"], "action": action}
    return {"allowed": True, "mode": row["kind"], "reason": row["why"], "action": action,
            "note": "legal given current evidence; not verified success"}


def provenance_complete(packet: dict[str, Any]) -> list[str]:
    """Return ids of material claims whose evidence does not resolve (empty list = complete)."""
    ev = packet.get("evidence") or {}
    bad = []
    for c in packet.get("current_claims", []):
        if c.get("material") and (not c.get("evidence") or any(e not in ev for e in c["evidence"])):
            bad.append(c["id"])
    return bad


def as_context_packet(packet: dict[str, Any]) -> ContextPacket:
    """Project onto the existing ``z0int.context_resolve.v1`` surface (AODL attach, receipts)."""
    refs = [EvidenceRef(**{k: v for k, v in e.items()}) for e in (packet.get("evidence") or {}).values()]
    cp = ContextPacket(
        task_id=packet.get("packet_id"),
        needs=[InformationNeed(id=k, description=k, kind="natural_language") for k in packet["intent"]["required"]],
        evidence=refs,
        contradictions=[f"{c['kind']}:{c['key']}" for c in packet.get("contradictions", [])],
        unresolved_gaps=[f"{u['key']}: {u['reason']}" for u in packet.get("blocking_unknowns", [])],
        measurements=dict(packet.get("measurements") or {}),
    )
    from .context_resolve import project_to_aodl_fields

    cp.aodl_projection = project_to_aodl_fields(cp)
    return cp


# ---------------------------------------------------------------------------
# SessionStart renderer
# ---------------------------------------------------------------------------


def _fmt_value(key: str, v: Any) -> str:
    if key == "git.head" and isinstance(v, dict):
        return f"{v.get('sha')} {v.get('date', '')[:16]} \"{v.get('subject')}\""
    if key == "git.dirty" and isinstance(v, dict):
        if v.get("clean"):
            return "clean"
        parts = []
        if v.get("modified_count"):
            parts.append(f"{v['modified_count']} modified: " + ", ".join(v.get("modified", [])[:5]))
        if v.get("untracked_count"):
            parts.append(f"{v['untracked_count']} untracked: " + ", ".join(v.get("untracked", [])[:4]))
        return "; ".join(parts)
    if key == "git.upstream" and isinstance(v, dict):
        return f"{v.get('name')} ahead {v.get('ahead')} behind {v.get('behind')}"
    if key == "git.branches_ahead" and isinstance(v, dict):
        rows = [f"{r['branch']}(+{r.get('ahead_of_default')}"
                + (f", worktree {r['worktree']})" if r.get("worktree") else ")") for r in v.get("ahead", [])]
        n_other = int(v.get("local_branches", 0)) - len(rows)
        return (f"vs {v.get('vs')}: " + (", ".join(rows) or "none")
                + f"; the other {n_other} local branch(es) are 0 ahead (complete list)")
    if key == "conv.sessions_here" and isinstance(v, dict):
        rows = [f"{r['session']}(cwd {r.get('cwd')}, last {r.get('last_ts')})" for r in v.get("sessions", [])[:8]]
        return (f"{v.get('count')} session(s) worked in this repo ({'complete' if v.get('complete') else 'truncated'};"
                f" {v.get('transcripts_scanned')} transcripts scanned): " + "; ".join(rows))
    if key == "resource.posture" and isinstance(v, dict):
        return str(v.get("line") or v.get("factory")) + ("" if v.get("enforce") else " (shadow; not enforced)")
    if key.startswith("resource.posture[") and isinstance(v, dict):
        return f"{v.get('posture')}: {v.get('binding_pool')} {v.get('arithmetic')}"
    if key.startswith("gh.priority[") and isinstance(v, dict):
        return f"\"{v.get('declares')}\" (issue: {v.get('issue_title')}, updated {str(v.get('updated'))[:10]})"
    if key == "git.worktrees" and isinstance(v, list):
        return ", ".join(f"{r['branch']}@{r.get('worktree')}" for r in v)
    if key == "git.recent_commits" and isinstance(v, list):
        return " | ".join(f"{r['sha']} {r['subject'][:60]}" for r in v[1:5])
    if key == "docs.priority" and isinstance(v, dict):
        return f"{v.get('doc')}: \"{v.get('heading')}\"" + (f" (status: {v['status']})" if v.get("status") else "")
    if key.startswith("conv.session[") and isinstance(v, dict):
        bits = [f"last {str(v.get('last_ts') or '')[:16]}"]
        if v.get("branch"):
            bits.append(f"branch {v['branch']}")
        if v.get("cwd") and v["cwd"] != ".":
            bits.append(f"cwd {v['cwd']}")
        if v.get("title"):
            bits.append(f"title \"{v['title']}\"")
        return ", ".join(bits)
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)[:160]
    return str(v)


def _ptr(packet: dict[str, Any], eids: list[str]) -> str:
    ev = packet.get("evidence") or {}
    e = ev.get(eids[0]) if eids else None
    if not e:
        return "?"
    loc = str(e.get("locator", ""))
    if e.get("trust_class") == "code" and str(e.get("source_id", "")).startswith("git:"):
        if e.get("source_version") == "worktree":
            return f"{e['source_id']}:worktree"
        return f"{e['source_id']}@{str(e['source_version'])[:7]}"
    if str(e.get("source_id", "")).startswith("posture:"):
        return f"{e['source_id']}@{str(e.get('source_version'))[:8]}"
    if e.get("trust_class") == "conversation":
        sid = str(e["source_id"]).split(":", 1)[-1]
        sub = sid.split("/", 1)
        tag = "cc:" + sub[0][:8] + ("/" + sub[1][-8:] if len(sub) > 1 else "")
        return tag + (loc[loc.rfind(':'):] if ":L" in loc else "")
    return f"{loc}@{str(e.get('source_version'))[:15]}"


_RENDER_ORDER = ("git.branch", "git.head", "git.dirty", "git.upstream", "git.default_branch", "git.branches_ahead",
                 "git.worktrees", "git.remote_refs_age_hours", "conv.latest_session", "conv.sessions_here",
                 "docs.priority", "resource.posture", "git.stash_count", "git.recent_commits")


def render_additional_context(packet: dict[str, Any], *, max_tokens: int = 1500) -> str:
    """Compact, pointer-bearing text for Claude Code SessionStart ``additionalContext`` (<= max_tokens)."""
    name = packet["scope"]["repo_name"]
    dec = packet["decision"]
    head = (packet.get("source_revisions") or {}).get("git") or {}
    lines = [
        f"<z0-state-packet repo={name} id={packet['packet_id'][:12]} built={packet['built_at']} head={str(head.get('head', ''))[:10]}>",
        "Derived at session start from " + ", ".join(sorted((packet.get("coverage") or {}).keys()))
        + ". Facts carry [source@revision]; memory is evidence, not truth. Listed branches, worktrees and"
        " Claude Code sessions are complete as of build; re-read a source only for facts not listed here."
        " Packet is stale once any source revision changes.",
        f"DECISION: {dec['mode']} — {dec['reason']}",
        "NOW:",
    ]
    claims = {c["key"]: c for c in packet.get("current_claims", [])}
    keys = [k for k in _RENDER_ORDER if k in claims]
    keys += sorted(k for k in claims if k.startswith("gh.priority["))
    latest = claims.get("conv.latest_session", {}).get("value")
    keys += [k for k in claims if k.startswith("conv.session[") and latest and k == f"conv.session[{latest}]"]
    for k in keys:
        c = claims[k]
        lines.append(f"- {k}: {_fmt_value(k, c['value'])} [{_ptr(packet, c['evidence'])}]")
    # sections in decision-priority order; lower sections are dropped first under the budget
    sections: list[tuple[str, list[str]]] = []
    blocking = packet.get("blocking_unknowns") or []
    sections.append(("BLOCKING UNKNOWNS (OBSERVE before acting):", [
        f"- {u['key']}: {u['reason']}" for u in blocking] + ([
        "- RULE: if a question depends on a blocking unknown and you cannot observe it with the tools you actually"
        " have, answer that it is unknown (abstain). Do not guess, and do not call tools you were not given."]
        if blocking else [])))
    sections.append(("CONTRADICTIONS (unresolved; do not pick a winner silently):", [
        f"- {c['kind']} {c['key']}: " + " vs ".join(f"{x['value']} [{_ptr(packet, x['evidence'])}]" for x in c["claims"])
        for c in (packet.get("contradictions") or [])[:5]]))
    sections.append(("LEGAL NEXT TRANSITIONS (candidates, none pre-authorized):", [
        f"- {t['kind']} {t['action']}: {t['why']}" + (f" (blocked by {','.join(t['blocked_by'])})" if t.get("blocked_by") else "")
        for t in packet.get("allowed_transitions") or []]))
    sections.append(("OPEN WORK:", [
        f"- {w['kind']}: {w['what']} [{_ptr(packet, w.get('evidence') or [])}]" for w in (packet.get("open_work") or [])[:8]]))
    sup = packet.get("superseded_claims") or []
    sections.append(("SUPERSEDED (history, not current):", [
        f"- {s['key']} was {_fmt_value(s['key'], s['old_value'])[:90]} (as of {str(s.get('old_observed_at') or '')[:16]})"
        f" -> now {_fmt_value(s['key'], s['current_value'])[:60]}" for s in sup[:4]]))
    sections.append(("OTHER UNKNOWNS:", [f"- {u['key']}: {u['reason']}" for u in (packet.get("unknowns") or [])[:4]]))
    recent = [f"- {k}: {_fmt_value(k, claims[k]['value'])}" for k in claims if k.startswith("conv.last_prompt[")][:2]
    sections.append(("LAST USER ASKS (redacted excerpts):", recent))
    sessions = [k for k in claims if k.startswith("conv.session[") and k not in keys]
    sections.append(("RESOURCE POSTURE (shadow; binding window per budget):", [
        f"- {k}: {_fmt_value(k, claims[k]['value'])}" for k in sorted(claims) if k.startswith("resource.posture[")][:5]))
    sections.append(("OTHER SESSIONS HERE:", [f"- {k}: {_fmt_value(k, claims[k]['value'])}" for k in sessions[:3]]))
    footer = f"Refresh/verify: z0int context packet --repo {packet['scope']['repo']} --check\n</z0-state-packet>"
    budget = int(max_tokens * 3.5)
    text = "\n".join(lines)
    for title, rows in sections:
        if not rows:
            continue
        block = "\n" + title
        for r in rows:
            if len(text) + len(block) + len(r) + len(footer) + 2 > budget:
                break
            block += "\n" + r
        if block != "\n" + title:
            text += block
    text += "\n" + footer
    if len(text) > budget:  # hard cap (never exceed the budget)
        text = text[: budget - len(footer) - 20] + "\n…(truncated)\n" + footer
    return text


def session_start_hook(stdin_text: str | None = None, *, repo: str | None = None, max_tokens: int = 1500,
                       projects_root: str | None = None) -> dict[str, Any]:
    """Claude Code SessionStart hook payload. Fail-open: empty context on any error."""
    cwd = repo
    if cwd is None and stdin_text:
        try:
            cwd = json.loads(stdin_text).get("cwd")
        except (ValueError, AttributeError):
            cwd = None
    cwd = cwd or os.getcwd()
    ctx = ""
    try:
        root = repo_root(cwd)
        if root is not None:
            pkt = build_state_packet(root, projects_root=projects_root)
            ctx = render_additional_context(pkt, max_tokens=max_tokens)
    except Exception as exc:  # never break session start
        ctx = f"<z0-state-packet error=\"{type(exc).__name__}\"/>"
    return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": ctx}}


def _main(argv: list[str] | None = None) -> int:
    """Lightweight entry (skips the full z0int CLI import): ``python -m z0int.state_packet --hook``."""
    import argparse
    import sys

    ap = argparse.ArgumentParser(prog="python -m z0int.state_packet")
    ap.add_argument("--hook", action="store_true", help="SessionStart hook JSON (reads hook stdin for cwd)")
    ap.add_argument("--repo", default=None)
    ap.add_argument("--render", action="store_true")
    ap.add_argument("--max-tokens", type=int, default=1500)
    ap.add_argument("--projects-root", default=None)
    a = ap.parse_args(argv)
    if a.hook:
        stdin_text = None if sys.stdin is None or sys.stdin.isatty() else sys.stdin.read()
        print(json.dumps(session_start_hook(stdin_text, repo=a.repo, max_tokens=a.max_tokens,
                                            projects_root=a.projects_root), ensure_ascii=False))
        return 0
    pkt = build_state_packet(a.repo or os.getcwd(), projects_root=a.projects_root)
    print(render_additional_context(pkt, max_tokens=a.max_tokens) if a.render else json.dumps(pkt, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
