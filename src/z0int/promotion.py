"""Promotion Authority v0: the one promotion rule, nightly -> default, as a DecisionOpportunity.

For each feature branch on a nightly manifest (verified-oss-loop ``.nightly/branches``) this
module gathers evidence for four criteria and hands it to ``z0int.decision_opportunity`` so
graduation uses the same ACT / OBSERVE / ASK / ABSTAIN / ESCALATE contract as every other decision:

  1. ``merge``   merges cleanly onto the default branch AND was kept (not dropped) in the latest
                 nightly rebuild report;
  2. ``wired``   every added source module is reachable from a real entrypoint (import graph
                 ported from research/factory/audit.py);
  3. ``tested``  its added code is executed by a passing run of the repo's CI test command;
  4. ``receipt`` behaviour/perf claims carry an eval receipt (z0evals or benchmarks/*results*.json)
                 referenced by the branch or its PR.

Each criterion is a fact with evidence.  Missing evidence is ``unknown`` (gatherable -> OBSERVE),
a checked negative is ``no_match`` (not ready -> ABSTAIN), and two sources disagreeing is a
contradiction (ESCALATE).  Promotion is the ``privileged`` effect, which no standing authority
grants: the gate can only recommend.  ACT becomes legal only after
``decision_opportunity.with_authority_grant(opp, granted_by_user=..., effect="privileged")`` on an
opportunity whose facts are all observed.

Read-only against the repository: branch trees are read with ``git archive`` into a scratch dir,
and ``git merge-tree`` writes its objects into a scratch object directory (the repo's object
store is only an alternate).  No ref, index or working tree is touched.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import os
import re
import shlex
import subprocess
import sys
import tarfile
import tempfile
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from . import decision_opportunity as do

SCHEMA = "z0int.promotion_check.v0"
REPORT_SCHEMA = "verified-oss-loop.nightly-report.v1"
CRITERIA = ("merge", "wired", "tested", "receipt")
EFFECTS = ("read", "privileged")          # graduation to the default branch is privileged
KEPT = {"MERGED", "WARN", "CONTAINED"}     # nightly outcomes that keep the branch in the tree
NOT_KEPT = {"DROPPED", "GONE", "PENDING"}
HARNESS = "promotion-authority"

CODE_EXT = {".py", ".ts", ".tsx", ".js", ".mjs", ".cjs", ".sh"}
TEXT_REF_RE = re.compile(r"([\w./${}-]+\.(?:py|sh|mjs|js|ts))|-m\s+([A-Za-z_][\w.]+)")
MOD_RE = re.compile(r"-m\s+([A-Za-z_][\w.]+)")
TEST_CMD_LINE = re.compile(r"unittest|pytest|py_compile|compileall")
MANIFEST_NAMES = {"hooks.json", ".mcp.json", "plugin.json"}

# Behaviour/perf claims that need a receipt: a line with a measured quantity AND a claim word
# ("2x faster", "hides 41% of tokens", "2/30 recall failures"), matched on commit and PR text.
# A bare mention of "benchmark" or a metric name is not a claim.
QUANT_RE = re.compile(r"\b\d+(?:\.\d+)?\s?(?:%|x\b|×|ms\b|tok/s|tokens/s|pp\b)|\b\d+/\d+\b", re.I)
CLAIM_WORD_RE = re.compile(
    r"\b(?:faster|slower|speed-?ups?|latency|throughput|accuracy|win[- ]?rate|pass@|recall|precision|auroc|"
    r"outperform\w*|beats?|improv\w*|reduc\w*|fewer|saves?|hides?|cuts?|regress\w*|wins?|strict|better|worse)\b"
    r"|state[- ]of[- ]the[- ]art|\bsota\b", re.I)


def is_claim(line: str) -> bool:
    return bool(QUANT_RE.search(line) and CLAIM_WORD_RE.search(line))


RECEIPT_PATH_RE = re.compile(r"benchmarks/[\w./-]*results[\w.-]*\.json")
Z0EVALS_RE = re.compile(r"z0evals[\w/#:.@-]*", re.I)


# ----------------------------------------------------------------------------- facts
@dataclass
class Fact:
    """One promotion criterion.  status: observed | no_match | unknown | source_unavailable | n/a."""

    key: str
    status: str = "unknown"
    value: Any = None
    evidence: list[str] = field(default_factory=list)
    reason: str = ""
    observe: str | None = None       # OBSERVE action that would gather the missing evidence
    contradiction: str | None = None  # two sources disagree about this fact

    @property
    def ok(self) -> bool:
        return self.status in ("observed", "n/a")

    def as_dict(self) -> dict[str, Any]:
        d = {"status": self.status, "value": self.value, "reason": self.reason, "evidence": self.evidence}
        if self.observe:
            d["observe"] = self.observe
        if self.contradiction:
            d["contradiction"] = self.contradiction
        return d


def _sha(obj: Any, n: int = 10) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:n]


# ----------------------------------------------------------------------------- git (read-only)
def _git(repo: Path, *args: str, env: Mapping[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=env)


def _rev(repo: Path, ref: str) -> str | None:
    r = _git(repo, "rev-parse", "--verify", "-q", f"{ref}^{{commit}}")
    return r.stdout.strip() or None


def resolve_ref(repo: Path, branch: str, remote: str = "origin") -> tuple[str | None, str | None]:
    for ref in (f"refs/remotes/{remote}/{branch}", f"refs/heads/{branch}"):
        sha = _rev(repo, ref)
        if sha:
            return ref, sha
    return None, None


def merge_tree(repo: Path, base_sha: str, sha: str, scratch: Path) -> dict[str, Any]:
    """Would ``sha`` merge onto ``base_sha``?  Objects go to a scratch dir; the repo is untouched."""
    common = _git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()
    objdir = scratch / "merge-objects"
    objdir.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "GIT_OBJECT_DIRECTORY": str(objdir),
           "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(Path(common) / "objects")}
    r = _git(repo, "merge-tree", "--write-tree", "--name-only", "--no-messages", base_sha, sha, env=env)
    lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
    if r.returncode == 0:
        return {"clean": True, "tree": lines[0] if lines else None, "conflicts": []}
    if r.returncode == 1:
        return {"clean": False, "tree": lines[0] if lines else None, "conflicts": lines[1:]}
    return {"clean": None, "error": (r.stderr or r.stdout).strip()[:300]}


def extract_tree(repo: Path, sha: str, dest: Path) -> list[str]:
    """Materialise a commit's tree without a checkout (``git archive``)."""
    raw = subprocess.run(["git", "-C", str(repo), "archive", "--format=tar", sha], capture_output=True, check=True).stdout
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(raw)) as tf:
        members = [m for m in tf.getmembers() if not m.issym() and not m.islnk()]
        tf.extractall(dest, members=members, filter="data") if sys.version_info >= (3, 12) else tf.extractall(dest, members=members)
    return sorted(m.name for m in members if m.isfile())


def changed_files(repo: Path, base: str, sha: str) -> dict[str, str]:
    out = _git(repo, "diff", "--name-status", "--no-renames", base, sha).stdout
    res = {}
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            res[parts[-1]] = parts[0][0]
    return res


def added_lines(repo: Path, base: str, sha: str, paths: Iterable[str]) -> dict[str, set[int]]:
    paths = list(paths)
    if not paths:
        return {}
    out = _git(repo, "diff", "-U0", "--no-color", "--no-renames", base, sha, "--", *paths).stdout
    res: dict[str, set[int]] = defaultdict(set)
    cur = None
    for line in out.splitlines():
        if line.startswith("+++ "):
            cur = line[6:] if line.startswith("+++ b/") else None
        elif line.startswith("@@") and cur:
            m = re.search(r"\+(\d+)(?:,(\d+))?", line)
            if m:
                start, n = int(m.group(1)), int(m.group(2) or 1)
                res[cur].update(range(start, start + n))
    return res


# ----------------------------------------------------------------------------- import graph
def is_test_path(rel: str) -> bool:
    name = Path(rel).name
    return (rel.startswith("tests/") or "/tests/" in rel or "/test/" in rel or "/__tests__/" in rel
            or name.startswith("test_") or name == "conftest.py" or ".test." in name or ".spec." in name)


def non_python_code(changed: Mapping[str, str], statuses: str) -> list[str]:
    return sorted(p for p, st in changed.items() if st in statuses and Path(p).suffix in CODE_EXT - {".py"}
                  and not is_test_path(p) and "node_modules" not in p)


def src_roots(tree: Path) -> list[str]:
    return ["src"] if (tree / "src").is_dir() else ["."]


def is_source_module(rel: str, roots: list[str], tree: Path) -> bool:
    """Gated product code: importable .py modules (under src/ or a top-level package), not tests."""
    if not rel.endswith(".py") or is_test_path(rel):
        return False
    if roots != ["."]:
        return any(rel.startswith(r + "/") for r in roots)
    top = rel.split("/")[0]
    return "/" in rel and (tree / top / "__init__.py").exists()


def _funclines(src: str) -> set[int] | None:
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None
    fl: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for st in node.body:
                for n2 in ast.walk(st):
                    if hasattr(n2, "lineno"):
                        fl.add(n2.lineno)
    return fl


class ImportGraph:
    """Single-tree port of research/factory/audit.py: modules, edges, entrypoint roots, BFS reach.

    Roots: ``[project.scripts]`` / ``[project.entry-points]``, GitHub workflows (lines that only
    run tests/py_compile are ignored, so CI test jobs are test evidence, not production reach),
    Claude Code plugin manifests (hooks.json / .mcp.json / plugin.json), and caller-supplied live
    roots (e.g. a systemd unit's ``python -m`` module).  Test files never propagate reach.
    """

    def __init__(self, tree: Path, files: list[str], extra_roots: Mapping[str, str] | None = None):
        self.tree, self.files = tree, files
        self.roots_src = src_roots(tree)
        self.modules: dict[str, str] = {}
        self.file2mod: dict[str, str] = {}
        self.edges: dict[str, set[str]] = defaultdict(set)
        self.roots: dict[str, list[str]] = defaultdict(list)
        self.parent: dict[str, str | None] = {}
        self.by_name: dict[str, list[str]] = defaultdict(list)
        for rel in files:
            self.by_name[Path(rel).name].append(rel)
        self._index()
        self._python_edges()
        self._text_edges()
        self._roots(extra_roots or {})
        self._bfs()

    def node(self, rel: str) -> str:
        return self.file2mod.get(rel, f"::{rel}")

    def _index(self) -> None:
        for rel in self.files:
            if not rel.endswith(".py"):
                continue
            root = "."
            for s in self.roots_src:
                if s != "." and rel.startswith(s + "/"):
                    root = s
            sub = rel if root == "." else rel[len(root) + 1:]
            parts = sub[:-3].split("/")
            if parts[-1] == "__init__":
                parts = parts[:-1]
            if not parts:
                continue
            mod = ".".join(parts)
            if mod in self.modules and root == ".":
                continue  # src/ wins a collision
            self.modules[mod] = rel
            self.file2mod[rel] = mod

    def _resolve(self, rel: str, name: str, level: int = 0, cur: str | None = None) -> tuple[list[str], str]:
        if level and cur:
            base = cur.split(".")
            base = base if rel.endswith("__init__.py") else base[:-1]
            base = base[: len(base) - (level - 1)] if level > 1 else base
            name = ".".join(base + ([name] if name else []))
        cands = [name] if name in self.modules else []
        if not cands and name and "." not in name:  # sibling import (sys.path.insert of own dir)
            d = str(Path(rel).parent)
            cands = [m for f, m in self.file2mod.items() if str(Path(f).parent) == d and Path(f).stem == name]
        return cands, name

    def _path_targets(self, s: str) -> list[str]:
        s = s.replace("${CLAUDE_PLUGIN_ROOT}/", "")
        out = []
        for rel in self.by_name.get(Path(s).name, []):
            if rel.endswith(s.lstrip("./")) or s.endswith(rel) or (
                    "/" in s and rel.endswith("/".join(Path(s).parts[-2:]))):
                out.append(rel)
        return out

    def _add_prefixes(self, src: str, rel: str, full: str, cur: str) -> None:
        parts = full.split(".")
        for i in range(1, len(parts) + 1):
            self.edges[src].update(self._resolve(rel, ".".join(parts[:i]), 0, cur)[0])

    def _python_edges(self) -> None:
        for mod, rel in list(self.modules.items()):
            try:
                tree = ast.parse((self.tree / rel).read_text(errors="replace"))
            except (SyntaxError, OSError, ValueError):
                continue
            for n in ast.walk(tree):
                if isinstance(n, ast.Import):
                    for a in n.names:
                        self._add_prefixes(mod, rel, a.name, mod)
                elif isinstance(n, ast.ImportFrom):
                    cs, full = self._resolve(rel, n.module or "", n.level, mod)
                    if full:
                        self._add_prefixes(mod, rel, full, mod)
                    self.edges[mod].update(cs)
                    for a in n.names:
                        self.edges[mod].update(self._resolve(rel, (full + "." if full else "") + a.name, 0, mod)[0])
                elif isinstance(n, ast.Constant) and isinstance(n.value, str):
                    v = n.value
                    if "." in v and v in self.modules and len(v) < 120:
                        self.edges[mod].add(v)  # importlib / registry / ["-m", "pkg.mod"]
                    for m in MOD_RE.findall(v):
                        if m in self.modules:
                            self.edges[mod].add(m)
                    if re.search(r"\.(py|sh)$", v) and "/" in v and len(v) < 200:
                        self.edges[mod].update(self.node(t) for t in self._path_targets(v) if t != rel)
            # importing pkg.sub executes pkg/__init__: a reached submodule reaches its packages
            parts = mod.split(".")
            for k in range(1, len(parts)):
                if ".".join(parts[:k]) in self.modules:
                    self.edges[mod].add(".".join(parts[:k]))
            self.edges[mod].discard(mod)

    def _scan_text(self, src: str, rel: str, txt: str, skip_test_lines: bool = False) -> None:
        for line in txt.splitlines():
            if skip_test_lines and TEST_CMD_LINE.search(line):
                continue
            for a, b in TEXT_REF_RE.findall(line):
                if a:
                    self.edges[src].update(self.node(t) for t in self._path_targets(a) if t != rel)
                if b:
                    for cand in (b, b + ".__main__"):
                        if cand in self.modules:
                            self.edges[src].add(cand)
            for qm in re.findall(r"""['"]([A-Za-z_]\w*\.[\w.]+)['"]""", line):
                if qm in self.modules:
                    self.edges[src].add(qm)
            for binref in re.findall(r"(?:\$\{CLAUDE_PLUGIN_ROOT\}/|\./)?((?:[\w.-]+/)*bin/[\w.-]+)", line):
                self.edges[src].update(self.node(t) for t in self._path_targets(binref) if t != rel)

    def _text_edges(self) -> None:
        for rel in self.files:
            p = Path(rel)
            if "node_modules" in rel or is_test_path(rel):
                continue
            if p.suffix in {".sh", ".mjs", ".js", ".ts", ".cjs"} or p.parent.name == "bin" or p.name in MANIFEST_NAMES:
                try:
                    txt = (self.tree / rel).read_text(errors="replace")
                except OSError:
                    continue
                src = self.node(rel)
                self._scan_text(src, rel, txt)
                if p.name in MANIFEST_NAMES:
                    # ${CLAUDE_PLUGIN_ROOT} is the plugin dir (parent of hooks/ or .claude-plugin/)
                    root = p.parent.parent if p.parent.name in ("hooks", ".claude-plugin") else p.parent
                    for tok in re.findall(r"\$\{CLAUDE_PLUGIN_ROOT\}/([\w./-]+)", txt):
                        cand = str(root / tok) if str(root) != "." else tok
                        if cand in self.files:
                            self.edges[src].add(self.node(cand))

    def _roots(self, extra: Mapping[str, str]) -> None:
        pp = self.tree / "pyproject.toml"
        if pp.exists():
            try:
                import tomllib  # type: ignore[import-not-found]

                d = tomllib.loads(pp.read_text())
                proj = d.get("project", {})
                for name, target in proj.get("scripts", {}).items():
                    self._root(target.split(":")[0], f"console_script `{name}`")
                for grp, eps in proj.get("entry-points", {}).items():
                    for name, target in eps.items():
                        self._root(target.split(":")[0], f"entry-point {grp}:{name}")
            except Exception:  # noqa: BLE001 - unreadable pyproject grants no roots
                pass
        for rel in self.files:
            if rel.startswith(".github/workflows/") and rel.endswith((".yml", ".yaml")):
                wnode = f"::{rel}"
                self._root(wnode, "GitHub workflow")
                txt = (self.tree / rel).read_text(errors="replace")
                self._scan_text(wnode, rel, txt, skip_test_lines=True)
                for mm in re.findall(r"python\S* -m ([\w.]+)", txt):
                    for cand in (mm, mm + ".__main__"):
                        if cand in self.modules:
                            self.edges[wnode].add(cand)
            elif Path(rel).name in MANIFEST_NAMES and not is_test_path(rel):
                self._root(self.node(rel), "Claude Code plugin manifest")
        for mod, why in extra.items():
            self._root(mod, why)

    def _root(self, node: str, why: str) -> None:
        if node in self.modules or node.startswith("::"):
            self.roots[node].append(why)

    def _is_test_node(self, n: str) -> bool:
        rel = self.modules.get(n) or (n[2:] if n.startswith("::") else "")
        return bool(rel) and is_test_path(rel)

    def _bfs(self) -> None:
        q = deque(self.roots)
        for r in self.roots:
            self.parent[r] = None
        while q:
            n = q.popleft()
            for m in self.edges.get(n, ()):
                if m not in self.parent and not self._is_test_node(m):
                    self.parent[m] = n
                    q.append(m)

    def chain(self, node: str, k: int = 6) -> list[str]:
        out: list[str] = []
        n: str | None = node
        while n is not None and len(out) < 64:
            out.append(n)
            n = self.parent.get(n)
        out = out[::-1]
        return out if len(out) <= k else out[:2] + ["…"] + out[-(k - 3):]

    def reach(self, rel: str) -> str | None:
        n = self.node(rel)
        if n not in self.parent:
            return None
        c = self.chain(n)
        return f"{c[0]} [{'; '.join(self.roots.get(c[0], []))}] -> " + " -> ".join(c[1:])


# ----------------------------------------------------------------------------- inputs
def parse_manifest(text: str) -> dict[str, Any]:
    entries = []
    for line in text.splitlines():
        body, _, note = line.partition("#")
        toks = body.split()
        if not toks:
            continue
        entries.append({"branch": toks[0], "mode": toks[1] if len(toks) > 1 else "required",
                        "owner": toks[2] if len(toks) > 2 else None, "note": note.strip()})
    m = re.search(r"\(default:\s*([\w./-]+)\)", text)
    tc = re.search(r"NIGHTLY_TEST_CMD[^\n]*\n#\s+(.+)", text)
    return {"entries": entries, "default": m.group(1) if m else None,
            "test_cmd": tc.group(1).strip() if tc else None}


def load_report(path: str | Path | None) -> dict[str, Any] | None:
    if not path:
        return None
    d = json.loads(Path(path).read_text())
    if d.get("schema") != REPORT_SCHEMA:
        raise ValueError(f"{path}: expected schema {REPORT_SCHEMA}, got {d.get('schema')!r}")
    return d


def pr_index(repo: Path) -> dict[str, list[dict[str, Any]]] | None:
    """Read-only ``gh pr list``; None when gh is unavailable (PR text is then unknown, not empty)."""
    try:
        r = subprocess.run(["gh", "pr", "list", "--state", "all", "--limit", "400", "--json",
                            "number,headRefName,title,body,url,state,baseRefName"],
                           cwd=repo, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    idx: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for pr in json.loads(r.stdout or "[]"):
        idx[pr["headRefName"]].append(pr)
    return idx


# ----------------------------------------------------------------------------- CI test run
def instrument(cmd: str, data_file: Path, include: str) -> tuple[list[str] | None, dict[str, str], list[str]]:
    """``[ENV=..] python -m X ...`` -> the same under ``coverage run``.  Shell pipelines are not instrumented."""
    toks = shlex.split(cmd)
    env: dict[str, str] = {}
    while toks and re.match(r"^[A-Za-z_]\w*=", toks[0]):
        k, v = toks.pop(0).split("=", 1)
        env[k] = v
    if not toks or any(t in ("&&", "||", ";", "|") for t in toks):
        return None, env, toks
    if re.fullmatch(r"(?:.*/)?python(?:3(?:\.\d+)?)?", toks[0]) and len(toks) > 1:
        return ([sys.executable, "-m", "coverage", "run", "--data-file", str(data_file), "--include", include,
                 *toks[1:]], env, toks)
    return None, env, toks


def run_ci_tests(tree: Path, cmd: str, scratch: Path, timeout: int) -> dict[str, Any]:
    try:
        import coverage  # noqa: F401
    except ImportError:
        return {"ran": False, "reason": "coverage.py not installed in this interpreter"}
    df = scratch / ".coverage"
    argv, env, _ = instrument(cmd, df, f"{tree}/*")
    if argv is None:
        return {"ran": False, "reason": f"CI test command is not a single `python ...` invocation: {cmd!r}"}
    full_env = {**os.environ, **env}
    full_env.pop("COVERAGE_PROCESS_START", None)
    try:
        r = subprocess.run(argv, cwd=tree, env=full_env, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ran": False, "reason": f"CI test command timed out after {timeout}s"}
    tail = (r.stdout + r.stderr).strip().splitlines()[-4:]
    executed: dict[str, set[int]] = {}
    if df.exists():
        import coverage

        data = coverage.CoverageData(basename=str(df))
        data.read()
        for f in data.measured_files():
            executed[os.path.relpath(f, tree)] = set(data.lines(f) or [])
    return {"ran": True, "passed": r.returncode == 0, "returncode": r.returncode, "tail": tail, "executed": executed}


# ----------------------------------------------------------------------------- criteria
def fact_merge(branch: str, sha: str, default_sha: str, repo: Path, scratch: Path,
               report: Mapping[str, Any] | None, report_path: str | None) -> tuple[Fact, dict[str, Any] | None]:
    f = Fact("promote.merge")
    mt = merge_tree(repo, default_sha, sha, scratch)
    if mt.get("clean") is True:
        f.evidence.append(f"git merge-tree {default_sha[:12]} {sha[:12]}: clean (tree {str(mt.get('tree'))[:12]})")
    elif mt.get("clean") is False:
        f.evidence.append(f"git merge-tree {default_sha[:12]} {sha[:12]}: CONFLICT in {', '.join(mt['conflicts'][:6]) or '?'}")
    else:
        f.evidence.append(f"git merge-tree failed: {mt.get('error')}")
    entry = None
    kept: bool | None = None
    if report is None:
        f.observe = "run scripts/nightly-rebuild.sh --dry-run and pass --report"
        kept_ev = "no nightly report supplied"
    else:
        entry = next((e for e in report.get("entries") or [] if e.get("branch") == branch), None)
        if entry is None:
            f.observe = "add the branch to the nightly manifest and rebuild"
            kept_ev = f"{report_path}: branch not in report entries"
        elif entry.get("sha") and entry["sha"] != sha:
            f.observe = f"re-run the nightly rebuild (report saw {entry['sha'][:12]}, branch is now {sha[:12]})"
            kept_ev = f"{report_path}: stale — report entry sha {entry['sha'][:12]} != branch tip {sha[:12]}"
            entry = {**entry, "_stale": True}
        else:
            st = entry.get("status")
            kept = True if st in KEPT else False if st in NOT_KEPT else None
            gate = (report.get("result") or {}).get("final_gate")
            kept_ev = (f"{report_path}: {st}{' — ' + entry['reason'] if entry.get('reason') else ''}"
                       f" (mode {report.get('mode')}, final_gate {gate}, {report.get('finished_at')})")
            if kept and gate not in (None, "green", "red-advisory"):
                kept = None
                f.observe = "re-run the nightly rebuild: final gate was not green"
    f.evidence.append(kept_ev)
    clean = mt.get("clean")
    f.value = {"clean": clean, "kept": kept, "merge_tree": mt.get("tree")}
    if clean is False and kept is True:
        f.status = "unknown"
        f.contradiction = ("nightly kept the branch, but it does not merge onto the default branch alone "
                           "(it only integrates on top of earlier nightly entries)")
    elif clean is False or kept is False:
        f.status = "no_match"
        f.reason = "conflicts with the default branch" if clean is False else "dropped from the latest nightly"
    elif clean is True and kept is True:
        f.status = "observed"
    else:
        f.status = "unknown"
        f.reason = "merge or nightly evidence missing"
        f.observe = f.observe or "re-check the merge"
    return f, entry


def fact_wired(graph: ImportGraph | None, added: list[str], err: str | None) -> Fact:
    f = Fact("promote.wired")
    if graph is None:
        f.reason, f.observe = f"import graph unavailable: {err}", "rebuild the import graph"
        return f
    if not added:
        f.status, f.value = "observed", {"added_modules": 0}
        f.evidence.append("no added source modules")
        return f
    reached = {rel: graph.reach(rel) for rel in added}
    dead = sorted(r for r, c in reached.items() if c is None)
    f.value = {"added_modules": len(added), "reached": len(added) - len(dead), "unreached": dead}
    for rel, c in sorted(reached.items()):
        if c:
            f.evidence.append(f"{rel}: {c}")
            if len(f.evidence) >= 3:
                break
    if dead:
        f.status, f.reason = "no_match", f"{len(dead)}/{len(added)} added module(s) unreachable from any entrypoint"
        f.evidence.insert(0, "unreached: " + ", ".join(dead[:8]) + (" …" if len(dead) > 8 else ""))
    else:
        f.status = "observed"
        f.evidence.insert(0, f"all {len(added)} added module(s) reachable ({len(graph.roots)} roots)")
    return f


def non_python_gap(f: Fact, files: list[str], graph: ImportGraph | None) -> Fact:
    """v0 judges Python only.  Non-Python code it cannot judge is missing evidence, never a pass."""
    if not files or not f.ok:
        return f
    if f.key == "promote.wired":
        files = [p for p in files if graph is None or graph.reach(p) is None]
        why = ("added non-Python code not reachable in the v0 graph (loaders such as omp or Hermes "
               "extension dirs are invisible to it)")
    else:
        why = "the CI test command is Python-only; changed non-Python code has no coverage evidence"
    if not files:
        return f
    f.status, f.reason = "source_unavailable", f"{len(files)} file(s): {why}"
    f.value = {**(f.value or {}), "unjudged": files}
    f.evidence.append("unjudged: " + ", ".join(files[:6]) + (" …" if len(files) > 6 else ""))
    return f


def fact_tested(test_cmd: str | None, cmd_src: str, run: dict[str, Any] | None,
                gated: dict[str, set[int]], ungated: list[str], entry: Mapping[str, Any] | None) -> Fact:
    f = Fact("promote.tested")
    if not test_cmd:
        f.reason, f.observe = "no CI test command known", "pass --test-cmd or a nightly report with test_cmd"
        f.status = "source_unavailable"
        return f
    f.evidence.append(f"CI test command ({cmd_src}): {test_cmd}")
    if not gated and run is None:
        f.status, f.value = "observed", {"gated_files": 0}
        f.evidence.append("no added function-body code in source modules")
        return f
    if run is None or not run.get("ran"):
        f.reason = (run or {}).get("reason", "tests not run")
        f.observe = "run the CI test command under coverage"
        return f
    same_sha = entry is not None and not entry.get("_stale")
    nightly_status = entry.get("status") if same_sha and entry else None
    if not run["passed"]:
        f.status, f.value = "no_match", {"passed": False, "returncode": run["returncode"]}
        f.reason = "CI test command fails on the branch tree"
        f.evidence.append("tail: " + " | ".join(run["tail"]))
        if nightly_status == "MERGED":
            f.contradiction = "nightly smoke was green at this sha, but the CI test command fails on the branch tree"
        return f
    if nightly_status in ("WARN",) or (nightly_status == "DROPPED" and "test" in str(entry.get("reason", ""))):
        f.contradiction = f"nightly reported {nightly_status} (tests failed) at this sha, but the CI test command passes"
    executed = run["executed"]
    uncovered, covered = [], []
    for rel, lines in sorted(gated.items()):
        hit = lines & executed.get(rel, set())
        (covered if hit else uncovered).append((rel, len(hit), len(lines)))
    f.value = {"passed": True, "gated_files": len(gated), "covered_files": len(covered), "uncovered": [u[0] for u in uncovered],
               "ungated_files": len(ungated)}
    tot = sum(n for _, _, n in covered + uncovered)
    hit = sum(h for _, h, _ in covered)
    f.evidence.append(f"CI command passed; {len(covered)}/{len(gated)} changed source file(s) have added "
                      f"function-body lines executed ({hit}/{tot} lines)")
    if ungated:
        f.evidence.append(f"{len(ungated)} changed file(s) add no function-body code (not gated)")
    if uncovered:
        f.status, f.reason = "no_match", f"{len(uncovered)} changed source file(s) not exercised by the CI test command"
        f.evidence.append("uncovered: " + ", ".join(u[0] for u in uncovered[:8]) + (" …" if len(uncovered) > 8 else ""))
    else:
        f.status = "observed"
    return f


def fact_receipt(texts: list[tuple[str, str]], pr_known: bool, changed: Mapping[str, str], tree_files: set[str]) -> Fact:
    f = Fact("promote.receipt")
    claims = []
    for src, t in texts:
        for line in t.splitlines():
            if is_claim(line) and not line.lower().startswith("merge "):
                claims.append(f"{src}: {line.strip()[:140]}")
    receipt_files = sorted(p for p, st in changed.items() if st != "D" and RECEIPT_PATH_RE.search(p))
    refs, missing_refs, z0 = [], [], []
    for src, t in texts:
        for p in RECEIPT_PATH_RE.findall(t):
            (refs if p in tree_files else missing_refs).append(f"{src}: {p}")
        z0 += [f"{src}: {m}" for m in Z0EVALS_RE.findall(t)]
    receipts = [f"branch adds {p}" for p in receipt_files] + refs + [f"{z} (external, not verified here)" for z in z0]
    f.value = {"claim_bearing": bool(claims), "receipts": len(receipts)}
    if not pr_known:
        f.evidence.append("PR text unavailable (gh); claims judged on commit messages only")
    if missing_refs:
        f.status = "unknown"
        f.contradiction = "branch/PR cites a receipt file that is not in the branch tree: " + ", ".join(missing_refs[:3])
        f.evidence += missing_refs[:3]
        return f
    if not claims:
        f.status, f.value["claim_bearing"] = "n/a", False
        f.evidence.append("no behaviour/perf claim in commit or PR text")
        f.evidence += receipts[:2]
        return f
    f.evidence += claims[:3]
    if receipts:
        f.status = "observed"
        f.evidence += receipts[:3]
    else:
        # Rule 4: a claim with no receipt is a question for the human, not a negative fact.
        f.status, f.reason = "source_unavailable", "claim-bearing branch has no eval receipt (z0evals or benchmarks/*results*.json)"
    return f


# ----------------------------------------------------------------------------- DecisionOpportunity
def build_packet(branch: str, sha: str | None, default: str, default_sha: str, facts: list[Fact],
                 revisions: Mapping[str, Any], extra_claims: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    evidence, claims, unknowns, contradictions, observe = [], [], [], [], []
    base_claims = [("promote.branch", {"name": branch, "sha": sha}), ("promote.default", {"name": default, "sha": default_sha})]
    for k, v in base_claims:
        eid = f"e:{_sha([k, v])}"
        evidence.append({"id": eid, "source": "git", "detail": f"{k}={v}"})
        claims.append({"key": k, "value": v, "status": "observed", "evidence": [eid]})
    for c in extra_claims or []:
        claims.append(c)
    for f in facts:
        ids = []
        for ev in f.evidence:
            eid = f"e:{_sha([f.key, ev])}"
            evidence.append({"id": eid, "source": f.key, "detail": ev})
            ids.append(eid)
        if f.ok:
            claims.append({"key": f.key, "value": f.value if f.value is not None else True, "status": "observed", "evidence": ids})
        else:
            # no_match: checked and negative (an answer).  unknown / source_unavailable: evidence missing.
            unknowns.append({"key": f.key, "source_status": f.status, "reason": f.reason or f.contradiction or ""})
        if f.contradiction:
            contradictions.append({"key": f.key, "kind": "evidence_conflict", "contests": f.key, "detail": f.contradiction})
        if f.observe and f.status == "unknown":
            observe.append({"kind": "OBSERVE", "action": f.observe, "why": f"gather evidence for {f.key}"})
    return {
        "schema": "z0int.state_packet.v0", "packet_id": f"promote:{branch}@{(sha or '')[:12]}",
        "current_claims": claims, "superseded_claims": [], "contradictions": contradictions,
        "blocking_unknowns": unknowns, "unknowns": [], "allowed_transitions": observe,
        "source_revisions": dict(revisions), "evidence": evidence, "built_at": None,
    }


def build_opportunity(repo: Path, branch: str, sha: str | None, default: str, packet: Mapping[str, Any],
                      aodl_doc: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return do.build_decision_opportunity(
        repo, f"Promote {branch}@{(sha or 'missing')[:12]} from nightly to {default}", effects=EFFECTS,
        aodl_doc=aodl_doc, packet=packet, harness=HARNESS, trace_id=f"promote:{branch}", scoped=False)


def promotion_verdict(opp: Mapping[str, Any]) -> str:
    """Fixed-priority promotion gate over the opportunity's legal action space.

    ACT (only after a user grant) > ESCALATE (contradiction) > ABSTAIN (a criterion checked
    negative) > OBSERVE (evidence we can gather) > ASK (ready, or a claim needs a receipt —
    only the human can supply either the receipt or the privileged grant).
    """
    legal = {a["kind"] for a in opp["action_space"] if a["legal"]}
    if "ACT" in legal:
        return "ACT"
    if "ESCALATE" in legal:
        return "ESCALATE"
    blocking = [u for u in opp["state"]["unknowns"] if u["blocking"]]
    if any(u["status"] == "no_match" for u in blocking):
        return "ABSTAIN"
    if any(u["status"] == "unknown" for u in blocking) and "OBSERVE" in legal:
        return "OBSERVE"
    return "ASK" if "ASK" in legal else "ABSTAIN"


def grant(opp: Mapping[str, Any], *, granted_by_user: str) -> dict[str, Any]:
    """The only way a promotion becomes ACT: an identified human grants the privileged effect."""
    return do.with_authority_grant(opp, granted_by_user=granted_by_user, effect="privileged")


# ----------------------------------------------------------------------------- per-branch check
def check_branch(repo: Path, entry: Mapping[str, Any], *, default: str, default_sha: str, remote: str,
                 report: Mapping[str, Any] | None, report_path: str | None, test_cmd: str | None, cmd_src: str,
                 prs: Mapping[str, list[dict[str, Any]]] | None, extra_roots: Mapping[str, str],
                 run_tests: bool, test_timeout: int, aodl_doc: Mapping[str, Any] | None) -> dict[str, Any]:
    branch = entry["branch"]
    ref, sha = resolve_ref(repo, branch, remote)
    revisions: dict[str, Any] = {"branch": sha, "default": default_sha,
                                 "report": (report or {}).get("finished_at"), "test_cmd": test_cmd}
    facts: list[Fact] = []
    extra: list[dict[str, Any]] = []
    if sha is None:
        f = Fact("promote.exists", "no_match", reason=f"branch not found on {remote} or locally")
        facts.append(f)
    elif _git(repo, "merge-base", "--is-ancestor", sha, default_sha).returncode == 0:
        facts.append(Fact("promote.pending", "no_match", reason=f"already contained in {default}; remove it from the manifest",
                          evidence=[f"{sha[:12]} is an ancestor of {default}@{default_sha[:12]}"]))
    else:
        with tempfile.TemporaryDirectory(prefix="z0promote-") as tmp:
            scratch = Path(tmp)
            fm, rentry = fact_merge(branch, sha, default_sha, repo, scratch, report, report_path)
            default_tree = _git(repo, "rev-parse", f"{default_sha}^{{tree}}").stdout.strip()
            if fm.value.get("merge_tree") and fm.value["merge_tree"] == default_tree:
                # squash-merged / cherry-picked: merging changes nothing, so there is nothing to promote
                facts.append(Fact("promote.pending", "no_match", value={"merge_tree": default_tree},
                                  reason=f"content already in {default} (merge onto it is a no-op); remove it from the manifest",
                                  evidence=[f"merge-tree result == {default}^{{tree}} {default_tree[:12]}", *fm.evidence[1:]]))
            else:
                facts.append(fm)
                base = _git(repo, "merge-base", default_sha, sha).stdout.strip() or default_sha
                changed = changed_files(repo, base, sha)
                tree = scratch / "tree"
                graph, err, files = None, None, []
                try:
                    files = extract_tree(repo, sha, tree)
                    graph = ImportGraph(tree, files, extra_roots)
                except Exception as exc:  # noqa: BLE001 - becomes an unknown, never a pass
                    err = f"{type(exc).__name__}: {exc}"
                roots = src_roots(tree) if files else ["src"]
                added = sorted(p for p, st in changed.items() if st == "A" and files and is_source_module(p, roots, tree))
                facts.append(non_python_gap(fact_wired(graph, added, err), non_python_code(changed, "A") if files else [], graph))
                # tested: added function-body lines in changed source modules must be executed
                src_changed = [p for p, st in changed.items() if st in ("A", "M") and files and is_source_module(p, roots, tree)]
                adds = added_lines(repo, base, sha, src_changed)
                gated, ungated = {}, []
                for rel in src_changed:
                    fl = _funclines((tree / rel).read_text(errors="replace")) if (tree / rel).exists() else None
                    lines = adds.get(rel, set())
                    g = lines & fl if fl else (lines if fl == set() else set())
                    if g:
                        gated[rel] = g
                    else:
                        ungated.append(rel)
                run = None
                if test_cmd and run_tests and gated and files:
                    run = run_ci_tests(tree, test_cmd, scratch, test_timeout)
                elif test_cmd and gated and not run_tests:
                    run = {"ran": False, "reason": "--no-tests"}
                facts.append(non_python_gap(fact_tested(test_cmd, cmd_src, run, gated, ungated, rentry),
                                            non_python_code(changed, "AM") if files else [], graph))
                log = _git(repo, "log", "--no-merges", "--format=%s%n%b", f"{base}..{sha}").stdout
                texts = [("commits", log)]
                pr_list = (prs or {}).get(branch, [])
                for pr in pr_list:
                    texts.append((f"PR #{pr['number']}", f"{pr.get('title', '')}\n{pr.get('body') or ''}"))
                    extra.append({"key": f"promote.pr[{pr['number']}]", "value": {"state": pr.get("state"), "base": pr.get("baseRefName"),
                                  "url": pr.get("url")}, "status": "observed", "evidence": []})
                facts.append(fact_receipt(texts, prs is not None, changed, set(files)))
                revisions["commits"] = int(_git(repo, "rev-list", "--count", f"{base}..{sha}").stdout.strip() or 0)
    packet = build_packet(branch, sha, default, default_sha, facts, revisions, extra)
    opp = build_opportunity(repo, branch, sha, default, packet, aodl_doc)
    verdict = promotion_verdict(opp)
    failing = [f.key.split(".", 1)[1] + (f"({f.status})" if f.status != "no_match" else "") for f in facts if not f.ok]
    return {
        "branch": branch, "ref": ref, "sha": sha, "mode": entry.get("mode"), "owner": entry.get("owner"),
        "verdict": verdict, "failing": failing,
        "criteria": {f.key.split(".", 1)[1]: f.as_dict() for f in facts},
        "ask_about": next((a.get("about", []) for a in opp["action_space"] if a["kind"] == "ASK"), []),
        "observe": [a["action"] for a in opp["action_space"] if a["kind"] == "OBSERVE"],
        "opportunity": opp,
    }


def check(repo: str | Path, manifest: str | Path, *, report: str | Path | None = None, test_cmd: str | None = None,
          remote: str = "origin", default: str | None = None, extra_roots: Mapping[str, str] | None = None,
          use_gh: bool = True, run_tests: bool = True, test_timeout: int = 600, branches: Iterable[str] | None = None,
          aodl_doc: Mapping[str, Any] | None = None) -> dict[str, Any]:
    repo = Path(repo).expanduser().resolve()
    man = parse_manifest(Path(manifest).read_text())
    rep = load_report(report)
    default = default or ((rep or {}).get("base") or {}).get("branch") or man["default"]
    if not default:
        head = _git(repo, "symbolic-ref", "--short", f"refs/remotes/{remote}/HEAD").stdout.strip()
        default = head.split("/", 1)[1] if "/" in head else None
    if not default:
        raise ValueError("cannot determine the default branch (pass --default)")
    _, default_sha = resolve_ref(repo, default, remote)
    if not default_sha:
        raise ValueError(f"default branch {default!r} not found in {repo}")
    if test_cmd:
        cmd, cmd_src = test_cmd, "--test-cmd"
    elif rep and rep.get("test_cmd"):
        cmd, cmd_src = rep["test_cmd"], "nightly report test_cmd"
    elif man["test_cmd"]:
        cmd, cmd_src = man["test_cmd"], "manifest NIGHTLY_TEST_CMD"
    else:
        cmd, cmd_src = None, "none"
    prs = pr_index(repo) if use_gh else None
    want = set(branches or [])
    rows = []
    for e in man["entries"]:
        if want and e["branch"] not in want:
            continue
        rows.append(check_branch(repo, e, default=default, default_sha=default_sha, remote=remote, report=rep,
                                 report_path=str(report) if report else None, test_cmd=cmd, cmd_src=cmd_src, prs=prs,
                                 extra_roots=extra_roots or {}, run_tests=run_tests, test_timeout=test_timeout,
                                 aodl_doc=aodl_doc))
    counts: dict[str, int] = defaultdict(int)
    for r in rows:
        counts[r["verdict"]] += 1
    return {
        "schema": SCHEMA, "repo": str(repo), "default": {"branch": default, "sha": default_sha},
        "manifest": str(manifest), "report": {"path": str(report) if report else None,
                                              "finished_at": (rep or {}).get("finished_at"), "mode": (rep or {}).get("mode"),
                                              "result": (rep or {}).get("result")},
        "test_cmd": {"cmd": cmd, "source": cmd_src}, "pr_text": "gh" if prs is not None else "unavailable",
        "authority": "shadow: promotion is `privileged`; this check only recommends. ACT needs "
                     "decision_opportunity.with_authority_grant(granted_by_user=..., effect='privileged').",
        "counts": dict(counts), "branches": rows,
    }


def render_text(res: Mapping[str, Any]) -> str:
    out = [f"promotion check: {res['repo']}  default {res['default']['branch']}@{res['default']['sha'][:12]}  "
           f"counts {res['counts']}"]
    for r in res["branches"]:
        out.append(f"{r['verdict']:<9} {r['branch']:<48} {(r['sha'] or '-')[:10]}  "
                   f"{'failing: ' + ', '.join(r['failing']) if r['failing'] else 'all criteria observed'}")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="z0int promote check", description=__doc__.split("\n\n")[0])
    ap.add_argument("--repo", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--report", default=None, help="nightly report.json (verified-oss-loop.nightly-report.v1)")
    ap.add_argument("--test-cmd", default=None, help="CI test command (default: report test_cmd, then manifest header)")
    ap.add_argument("--remote", default="origin")
    ap.add_argument("--default", default=None)
    ap.add_argument("--root", action="append", default=[], metavar="MOD[=WHY]",
                    help="extra live entrypoint module (e.g. a systemd unit's python -m target)")
    ap.add_argument("--branch", action="append", default=[], help="only check these manifest entries")
    ap.add_argument("--no-gh", action="store_true", help="do not read PR text with gh")
    ap.add_argument("--no-tests", action="store_true", help="do not run the CI test command (tested -> unknown)")
    ap.add_argument("--test-timeout", type=int, default=600)
    ap.add_argument("--full", action="store_true", help="include the full DecisionOpportunity per branch in --json")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    roots = {}
    for r in a.root:
        mod, _, why = r.partition("=")
        roots[mod] = why or "live entrypoint (--root)"
    res = check(a.repo, a.manifest, report=a.report, test_cmd=a.test_cmd, remote=a.remote, default=a.default,
                extra_roots=roots, use_gh=not a.no_gh, run_tests=not a.no_tests, test_timeout=a.test_timeout,
                branches=a.branch or None)
    if a.json:
        if not a.full:
            for r in res["branches"]:
                opp = r.pop("opportunity")
                r["semantic_id"] = opp["semantic_id"]
                r["action_space"] = [{k: v for k, v in x.items() if k in ("kind", "legal", "blocked_by", "about")}
                                     for x in opp["action_space"]]
        print(json.dumps(res, indent=2, default=str))
    else:
        print(render_text(res))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
