#!/usr/bin/env python3
"""Build the LookFirst capability index without network access by default.

Usage: python scripts/look-first/generate.py [--config roots.json]
       [--output-dir ~/.local/state/z0int] [--refresh-remote]

Import this file with importlib, then call generate(config_path=..., output_dir=...).
The roots JSON accepts repositories [{name,github,paths}], registry_paths,
search_roots, skill_roots, link_roots, units, and wiring. Explicit wiring uses
{path,target,kind: 'link'|'config',contains: 'exact config line'}.
Config wiring is evidence only when the target exists and the exact line occurs.
No installation or harness configuration changes occur. See roots.json for
installation requirements. All subprocesses have bounded timeouts.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sqlite3
from contextlib import closing
import subprocess
import sys
import tempfile
from typing import Any, Iterator

# Loading by filename permits both subprocess use and isolated module import.
_spec = importlib.util.spec_from_file_location("look_first_collect", Path(__file__).with_name("collect.py"))
assert _spec and _spec.loader
_collect = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_collect)
run = _collect.run
dump_json = _collect.dump_json
DEFAULT_CACHE = _collect.DEFAULT_CACHE
DEFAULT_OUTPUT = Path.home() / ".local/state/z0int"
FIELDS = ("method", "source", "status", "wired", "kind", "text")
STATUSES = {"merged", "live", "branch-only", "proposal"}
DOC_SUFFIXES = {".md", ".rst", ".txt", ".adoc"}
CODE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".mjs", ".rs", ".go", ".sh", ".bend"}
MANIFEST_NAMES = {"pyproject.toml", "package.json", "plugin.yaml", "plugin.yml", "fleet.toml", "Cargo.toml", "stack.toml"}
SKIP_PARTS = {"node_modules", ".git", "__pycache__", ".venv", "venv", "vendor", "third_party", "secrets", "credentials"}
DECLARATION = re.compile(r"^\s*(?:(?:export|pub|async|default|declare|static)\s+)*(?:function|class|interface|type|enum|struct|trait|fn|def)\s+([\w.]+)|^\s*(?:export\s+)?(?:const|let)\s+(\w+)\s*=")


def compact(value: str) -> str:
    return " ".join(value.split())


def record(method: str, source: str, status: str, wired: str, kind: str, text: str) -> dict[str, str]:
    if status not in STATUSES:
        raise ValueError(f"Invalid status: {status}")
    return dict(zip(FIELDS, (compact(method), source, status, compact(wired), kind, compact(text))))


def error(manifest: dict[str, Any], scope: str, exc: Exception | str) -> None:
    manifest["errors"].append({"scope": scope, "error": str(exc)})


def git(root: Path, *args: str, timeout: float = 30) -> str:
    return run(["git", "-C", str(root), *args], timeout=timeout)


def expand(value: str, variables: dict[str, str], base: Path) -> Path:
    for name, replacement in variables.items():
        value = value.replace("${" + name + "}", replacement)
    if "${" in value:
        raise ValueError(f"Unknown path variable: {value}")
    path = Path(os.path.expandvars(os.path.expanduser(value)))
    return (base / path).absolute() if not path.is_absolute() else path


def github_slug(value: str) -> str | None:
    value = value.strip().removesuffix(".git")
    match = re.search(r"(?:github\.com[:/])([\w.-]+/[\w.-]+)$", value)
    if match:
        return match.group(1)
    return value if _collect.REPO_RE.fullmatch(value) else None


def config_roots(config_path: Path, checkout: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("The roots config must be an object")
    common = Path(git(checkout, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()).parent
    workspace = Path(os.environ.get("Z0_WORKSPACE", "/workspace/zer0"))
    mount = Path(os.environ.get("Z0_MOUNT", str(common.parent)))
    if common.parent.name in {"z0-wt", "worktrees"}:
        mount = common.parent.parent
    variables = {"HOME": str(Path.home()), "CHECKOUT": str(checkout), "COMMON_PARENT": str(common.parent), "WORKSPACE": str(workspace), "WORKSPACE_PARENT": str(workspace.parent), "MOUNT": str(mount)}
    base = config_path.parent
    for key in ("search_roots", "registry_paths", "skill_roots", "link_roots", "skill_metadata_paths"):
        config[key] = [expand(value, variables, base) for value in config.get(key, [])]
    specs = [dict(item) for item in config.get("repositories", [])]
    scope = set(config.get("scope", [spec["name"] for spec in specs]))
    registries = []
    for path in config["registry_paths"]:
        item = {"path": str(path), "present": path.is_file(), "records": 0}
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                rows = data if isinstance(data, list) else data.get("repositories", data.get("projects", []))
                if not isinstance(rows, list):
                    raise ValueError("Registry repositories/projects must be an array")
                item["records"] = len(rows)
                item["scoped_records"] = 0
                for row in rows:
                    name = row.get("name", row.get("id"))
                    slug = row.get("github", row.get("coordination_repo", row.get("project_repo")))
                    if not slug and _collect.REPO_RE.fullmatch(str(row.get("id", ""))):
                        slug = row["id"]
                    if name not in scope:
                        continue
                    item["scoped_records"] += 1
                    paths = list(row.get("paths") or [])
                    # Reuse the held repository-inventory schema as discovery hints.
                    # Never copy its private descriptions or inventories into Git.
                    for local in row.get("local_paths", []):
                        if isinstance(local, str):
                            paths.append(local)
                        elif local.get("path") and not local.get("content_excluded"):
                            paths.append(local["path"])
                    for key in ("path", "root", "checkout", "local_path", "workspaceRoot"):
                        if row.get(key):
                            paths.append(row[key])
                    specs.append({"name": name, "github": slug, "paths": paths})
            except (OSError, ValueError, TypeError, AttributeError) as exc:
                error(manifest, f"registry:{path}", exc)
        registries.append(item)
    manifest["registries"] = registries
    # Only exact repository names under explicitly bounded search roots are probed.
    # Do not walk a workspace tree or select a similarly named worktree.
    discovered: dict[str, dict[str, Any]] = {}
    for spec in specs:
        name = spec["name"]
        if name not in scope:
            continue
        target = discovered.setdefault(name, {"name": name, "github": spec.get("github"), "paths": []})
        if not target["github"]:
            target["github"] = spec.get("github")
        candidates = [expand(value, variables, base) for value in spec.get("paths", [])]
        candidates += [root / name for root in config["search_roots"]]
        for path in candidates:
            if not path.is_dir():
                continue
            try:
                actual = Path(git(path, "rev-parse", "--show-toplevel").strip()).resolve()
                if actual != path.resolve():
                    continue
                origin = github_slug(git(path, "config", "--get", "remote.origin.url").strip())
                # A repository from an unrelated parent must not enter the corpus.
                if target["github"] and origin != target["github"]:
                    continue
                target["github"] = target["github"] or origin
                if actual not in target["paths"]:
                    target["paths"].append(actual)
            except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
                error(manifest, f"discovery:{path}", exc)
    for name in scope:
        discovered.setdefault(name, {"name": name, "github": None, "paths": []})
    config["repositories"] = [discovered[name] for name in sorted(discovered)]
    for item in config.get("wiring", []):
        item["path"] = expand(item["path"], variables, base)
        item["target"] = expand(item["target"], variables, base)
    return config


def selected(path: str) -> bool:
    value = Path(path)
    if any(part in SKIP_PARTS for part in value.parts) or value.name.startswith(".env"):
        return False
    if value.name in MANIFEST_NAMES or value.name == "SKILL.md":
        return True
    if value.suffix.lower() in DOC_SUFFIXES:
        return True
    return value.suffix.lower() in CODE_SUFFIXES and not any(part in {"tests", "test", "fixtures"} for part in value.parts)


def text_records(text: str, path: Path, *, method: str, status: str, wired: str = "unverified", kind: str = "doc", context: str = "") -> Iterator[dict[str, str]]:
    """Preserve each nonempty source line, with heading context, without truncation."""
    section = ""
    for number, line in enumerate(text.splitlines(), 1):
        line = compact(line)
        if not line:
            continue
        if line.startswith("#"):
            section = line.lstrip("# ")
        yield record(" / ".join(filter(None, (method, section))), f"{path}:{number}", status, wired, kind, " ".join(filter(None, (context, line))))


def file_records(text: str, path: Path, *, name: str, status: str, wired: str, context: str = "") -> Iterator[dict[str, str]]:
    suffix = path.suffix.lower()
    method = f"{name}:{path.stem}"
    if path.name == "SKILL.md":
        method = f"skill:{path.parent.name}"
        yield from text_records(text, path, method=method, status=status, wired=wired, kind="skill", context=context)
    elif suffix in DOC_SUFFIXES or path.name in MANIFEST_NAMES:
        yield from text_records(text, path, method=method, status=status, wired=wired, kind="manifest" if path.name in MANIFEST_NAMES else "doc", context=context)
    elif suffix == ".py":
        try:
            tree = ast.parse(text)
        except SyntaxError:
            # Invalid/older Python still contributes declarations, never disappears.
            for number, line in enumerate(text.splitlines(), 1):
                match = DECLARATION.match(line)
                if match:
                    yield record(f"{method}:{match.group(1) or match.group(2)}", f"{path}:{number}", status, wired, "module", f"{context} {line}")
            return
        doc = ast.get_docstring(tree, clean=False)
        if doc and tree.body:
            yield record(method, f"{path}:{tree.body[0].lineno}", status, wired, "module", f"{context} {doc}")
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                declaration = text.splitlines()[node.lineno - 1]
                doc = ast.get_docstring(node, clean=False) or ""
                yield record(f"{method}:{node.name}", f"{path}:{node.lineno}", status, wired, "module", f"{context} {declaration} {doc}")
    else:
        for number, line in enumerate(text.splitlines(), 1):
            match = DECLARATION.match(line)
            if match:
                yield record(f"{method}:{match.group(1) or match.group(2)}", f"{path}:{number}", status, wired, "module", f"{context} {line}")


def wiring_evidence(config: dict[str, Any], manifest: dict[str, Any]) -> list[tuple[Path, str]]:
    evidence: list[tuple[Path, str]] = []
    details = []
    for root in config["link_roots"]:
        item: dict[str, Any] = {"path": str(root), "links": 0, "present": root.is_dir()}
        if root.is_dir():
            try:
                for path in sorted(root.iterdir()):
                    if path.is_symlink():
                        if path.exists():
                            target = path.resolve()
                            evidence.append((target, f"link:{path} -> {target}"))
                            item["links"] += 1
                        else:
                            error(manifest, f"wiring:{path}", "Broken installation link")
            except OSError as exc:
                error(manifest, f"wiring:{root}", exc)
        details.append(item)
    for item in config.get("wiring", []):
        path, target = item["path"], item["target"]
        try:
            if item["kind"] == "link":
                valid = path.is_symlink() and path.exists() and path.resolve() == target.resolve()
                proof = f"link:{path} -> {target.resolve()}"
            elif item["kind"] == "config":
                lines = path.read_text(encoding="utf-8").splitlines()
                literal = item["contains"]
                # The declaration must reference this target, not unrelated text.
                valid = target.exists() and str(target) in literal and literal in lines
                proof = f"config:{path}:{lines.index(literal) + 1}" if valid else "unverified"
            else:
                raise ValueError("Wiring kind must be link or config")
            if valid:
                evidence.append((target.resolve(), proof))
            else:
                error(manifest, f"wiring:{path}", "Exact target wiring not observed")
        except (OSError, KeyError, ValueError) as exc:
            error(manifest, f"wiring:{path}", exc)
    for unit in sorted(config.get("units", [])):
        item = {"unit": unit, "active": False}
        try:
            raw = run(["systemctl", "--user", "show", unit, "--property=ActiveState,FragmentPath,WorkingDirectory,ExecStart"], timeout=10)
            values = dict(line.split("=", 1) for line in raw.splitlines() if "=" in line)
            item["active"] = values.get("ActiveState") == "active"
            item["fragment"] = values.get("FragmentPath", "")
            if item["active"] and item["fragment"]:
                # Only literal absolute files in the active ExecStart count.
                # A WorkingDirectory does NOT prove that every file is loaded.
                for token in re.findall(r"/[\w./+@-]+", values.get("ExecStart", "")):
                    path = Path(token)
                    if path.is_file():
                        evidence.append((path.resolve(), f"unit:{unit} fragment:{item['fragment']} ExecStart:{path}"))
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            item["error"] = str(exc)
        details.append(item)
    manifest["wiring"] = details
    return evidence


def wired_for(path: Path, text: str, evidence: list[tuple[Path, str]]) -> str:
    actual = path.resolve()
    for target, proof in evidence:
        if actual == target or target.is_dir() and target in actual.parents:
            try:
                # Live attribution requires the indexed bytes, not just a pathname.
                if path.read_text(encoding="utf-8") == text:
                    return proof
            except (OSError, UnicodeError):
                pass
    return "unverified"


def merged_base(root: Path) -> str | None:
    refs = set(git(root, "for-each-ref", "--format=%(refname)", "refs/heads", "refs/remotes/origin").splitlines())
    try:
        default = git(root, "symbolic-ref", "refs/remotes/origin/HEAD").strip()
    except RuntimeError:
        default = ""
    for ref in (default, "refs/remotes/origin/main", "refs/remotes/origin/master", "refs/heads/main", "refs/heads/master"):
        if ref and ref in refs:
            return ref
    return None


def is_ancestor(root: Path, commit: str, base: str) -> bool:
    result = subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", commit, base], capture_output=True, timeout=15)
    if result.returncode not in (0, 1):
        raise RuntimeError(result.stderr.decode("utf-8", errors="replace"))
    return result.returncode == 0


def tree_files(root: Path, commit: str) -> dict[str, str]:
    result = {}
    for row in git(root, "ls-tree", "-r", "-z", commit).split("\0"):
        if not row:
            continue
        metadata, path = row.split("\t", 1)
        mode, kind, oid = metadata.split()
        if kind == "blob" and mode in {"100644", "100755"} and selected(path):
            result[path] = oid
    return result


def read_blobs(root: Path, oids: set[str]) -> dict[str, str | None]:
    """One bounded process reads only previously unseen selected Git objects."""
    if not oids:
        return {}
    request = "".join(oid + "\n" for oid in sorted(oids)).encode("ascii")
    result = subprocess.run(["git", "-C", str(root), "cat-file", "--batch"], input=request, capture_output=True, timeout=60)
    if result.returncode:
        raise RuntimeError(result.stderr.decode("utf-8", errors="replace"))
    data, offset, blobs = result.stdout, 0, {}
    while offset < len(data):
        end = data.index(b"\n", offset)
        oid, kind, size = data[offset:end].decode("ascii").split()
        if kind != "blob":
            raise ValueError(f"Expected blob, received {kind}")
        offset = end + 1
        length = int(size)
        try:
            blobs[oid] = data[offset:offset + length].decode("utf-8")
        except UnicodeDecodeError:
            blobs[oid] = None
        offset += length + 1
    if set(blobs) != oids:
        raise ValueError("Git batch did not return every requested object")
    return blobs


def collect_local(spec: dict[str, Any], cache_dir: Path, evidence: list[tuple[Path, str]], manifest: dict[str, Any]) -> Iterator[dict[str, str]]:
    coverage: dict[str, Any] = {"name": spec["name"], "paths": [str(path) for path in spec["paths"]], "branches": [], "files_enumerated": 0, "files_indexed": 0, "complete": True}
    manifest["local"].append(coverage)
    if not spec["paths"]:
        coverage["complete"] = False
        error(manifest, f"local:{spec['name']}", "No exact local repository discovered")
        return
    seen: set[tuple[str, str, str]] = set()
    seen_commons: set[str] = set()
    for root in spec["paths"]:
        try:
            base = merged_base(root)
            base_files = tree_files(root, base) if base else {}
            blobs: dict[str, str | None] = {}
            common = git(root, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()
            refs = [] if common in seen_commons else git(root, "for-each-ref", "--format=%(refname) %(objectname)", "refs/heads", "refs/remotes/origin").splitlines()
            seen_commons.add(common)
            head = git(root, "rev-parse", "HEAD").strip()
            refs.append(f"HEAD {head}")
            processed: set[str] = set()
            trees: dict[str, dict[str, str]] = {}
            statuses: dict[str, str] = {}
            for row in sorted(set(refs)):
                ref, commit = row.split(" ", 1)
                if commit not in statuses:
                    statuses[commit] = "merged" if base and is_ancestor(root, commit, base) else "branch-only"
                branch_status = statuses[commit]
                if commit not in trees:
                    trees[commit] = tree_files(root, commit)
                paths = trees[commit]
                coverage["branches"].append({"root": str(root), "ref": ref, "commit": commit, "status": branch_status, "files": len(paths), "deduplicated_commit": commit in processed})
                coverage["files_enumerated"] += len(paths)
                if commit in processed:
                    continue
                processed.add(commit)
                blobs.update(read_blobs(root, set(paths.values()) - blobs.keys()))
                for relative, oid in sorted(paths.items()):
                    try:
                        current = root / relative
                        committed = blobs[oid]
                        if committed is None:
                            raise UnicodeError("Selected Git blob is not UTF-8 text")
                        status = "merged" if base_files.get(relative) == oid else branch_status
                        if ref == "HEAD" and current.is_file():
                            text = current.read_text(encoding="utf-8")
                            if text != committed:
                                status = "branch-only"
                            source = current.resolve()
                        else:
                            text = committed
                            source = cache_dir / "local" / spec["name"] / oid / relative
                            identity = (str(source), oid, status)
                            if identity in seen:
                                continue
                            if not source.exists():
                                source.parent.mkdir(parents=True, exist_ok=True)
                                source.write_text(text, encoding="utf-8")
                        wired = wired_for(current, text, evidence) if current.is_file() else "unverified"
                        if wired != "unverified":
                            status = "live"
                        identity = (str(source), oid, status)
                        if identity in seen:
                            continue
                        seen.add(identity)
                        context = f"repository {spec['name']} path {relative}"
                        yield from file_records(text, source, name=spec["name"], status=status, wired=wired, context=context)
                        coverage["files_indexed"] += 1
                    except (OSError, UnicodeError, RuntimeError, subprocess.TimeoutExpired) as exc:
                        coverage["complete"] = False
                        error(manifest, f"local:{root}:{commit}:{relative}", exc)
        except (OSError, RuntimeError, subprocess.TimeoutExpired, ValueError) as exc:
            coverage["complete"] = False
            error(manifest, f"local:{root}", exc)


def walk_skills(root: Path, manifest: dict[str, Any]) -> Iterator[Path]:
    """Follow skill-directory symlinks, detect cycles, include archived metadata."""
    def walk(path: Path, ancestors: frozenset[Path]) -> Iterator[Path]:
        try:
            real = path.resolve(strict=True)
            if real in ancestors:
                error(manifest, f"skills:{path}", "Symlink directory cycle")
                return
            for entry in sorted(path.iterdir()):
                if entry.name in {".git", "node_modules", ".venv", "__pycache__", ".locks"}:
                    continue
                if entry.name == "SKILL.md" and entry.is_file():
                    yield entry
                elif entry.is_dir():
                    yield from walk(entry, ancestors | {real})
                elif entry.is_symlink() and not entry.exists():
                    error(manifest, f"skills:{entry}", "Broken skill link")
                    yield entry / "SKILL.md"
        except (OSError, RuntimeError) as exc:
            error(manifest, f"skills:{path}", exc)
    yield from walk(root, frozenset())


def skill_metadata(paths: list[Path], manifest: dict[str, Any]) -> dict[str, dict[str, str]]:
    """Reuse held metadata only when a current skill link has no readable target."""
    result = {}
    coverage = []
    for path in paths:
        item = {"path": str(path), "present": path.is_file(), "records": 0}
        if path.is_file():
            try:
                text = path.read_text(encoding="utf-8")
                data = json.loads(text)
                rows = data if isinstance(data, list) else data.get("skills", [])
                lines = {}
                for number, line in enumerate(text.splitlines(), 1):
                    match = re.match(r'\s*"path"\s*:\s*("(?:[^"\\]|\\.)*")', line)
                    if match:
                        lines.setdefault(json.loads(match.group(1)), number)
                for row in sorted(rows, key=lambda row: (row.get("path", ""), row.get("name", ""))):
                    if not row.get("name") or not row.get("description"):
                        continue
                    source = f"{path.resolve()}:{lines.get(row.get('path'), 1)}"
                    entry = {"name": row["name"], "description": row["description"], "source": source}
                    result.setdefault(f"name:{row['name']}", entry)
                    if row.get("path"):
                        result.setdefault(f"path:{row['path']}", entry)
                    item["records"] += 1
            except (OSError, ValueError, AttributeError, TypeError) as exc:
                error(manifest, f"skill-metadata:{path}", exc)
        coverage.append(item)
    manifest["skill_metadata"] = coverage
    return result


def collect_skills(roots: list[Path], evidence: list[tuple[Path, str]], manifest: dict[str, Any], metadata: dict[str, dict[str, str]] | None = None, cache_dir: Path = DEFAULT_CACHE) -> Iterator[dict[str, str]]:
    for root in sorted(roots):
        before = len(manifest["errors"])
        coverage: dict[str, Any] = {"root": str(root), "present": root.is_dir(), "enumerated": 0, "indexed": 0, "skills": [], "complete": True}
        manifest["skills"].append(coverage)
        if not root.is_dir():
            error(manifest, f"skills:{root}", "Skill root does not exist")
        else:
            for path in walk_skills(root, manifest):
                coverage["enumerated"] += 1
                if not path.is_file():
                    link = path.parent
                    target = os.readlink(link) if link.is_symlink() else "unavailable"
                    recovered = (metadata or {}).get(f"path:{path}") or (metadata or {}).get(f"name:{link.name}")
                    description = recovered["description"] if recovered else "Description unavailable"
                    observed = {"name": link.name, "path": str(path), "link": str(link), "target": target, "description": description, "description_source": recovered["source"] if recovered else None}
                    digest = hashlib.sha256(json.dumps(observed, sort_keys=True).encode()).hexdigest()
                    source = cache_dir / "dangling-skills" / f"{digest}.json"
                    source.parent.mkdir(parents=True, exist_ok=True)
                    source.write_text(json.dumps(observed, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
                    coverage["skills"].append({**observed, "metadata_recovered": bool(recovered)})
                    yield record(f"skill:{link.name}", f"{source}:1", "branch-only", f"dangling-link:{link} -> {target}", "skill", f"Hermes profile {root.parent.name} skill {link.name}. Historical metadata: {description}. Current target unavailable: {path}")
                    coverage["indexed"] += 1
                    if not recovered:
                        error(manifest, f"skills:{path}", "Description unavailable in metadata snapshots")
                    continue
                try:
                    text = path.read_text(encoding="utf-8")
                    wired = wired_for(path, text, evidence)
                    if wired == "unverified" and ".archive" not in path.relative_to(root).parts:
                        # A real per-skill link proves installation in this profile.
                        # A normal directory alone does not prove live wiring.
                        candidate = path
                        while candidate != root:
                            if candidate.is_symlink() and candidate.exists():
                                wired = f"link:{candidate} -> {candidate.resolve()}"
                                break
                            candidate = candidate.parent
                    status = "live" if wired != "unverified" else "branch-only"
                    relative = str(path.relative_to(root))
                    coverage["skills"].append({"path": str(path), "canonical": str(path.resolve()), "relative": relative, "sha256": hashlib.sha256(text.encode()).hexdigest(), "archived": ".archive" in path.parts})
                    yield from file_records(text, path.resolve(), name="hermes", status=status, wired=wired, context=f"Hermes skill {relative} profile {root.parent.name}")
                    coverage["indexed"] += 1
                except (OSError, UnicodeError) as exc:
                    error(manifest, f"skills:{path}", exc)
        coverage["complete"] = len(manifest["errors"]) == before


def remote_records(spec: dict[str, Any], cache_dir: Path, manifest: dict[str, Any]) -> Iterator[dict[str, str]]:
    repo = spec.get("github")
    coverage: dict[str, Any] = {"repo": repo, "complete": False, "endpoints": [], "counts": {}}
    manifest["github"].append(coverage)
    if not repo or not _collect.REPO_RE.fullmatch(repo):
        error(manifest, f"github:{repo}", "No valid GitHub repository")
        return
    root = cache_dir / "github" / repo
    files = [root / f"{name}.json" for name in ("issues", "pulls", "issue-comments", "review-comments")]
    data: dict[str, list[dict[str, Any]]] = {}
    complete = True
    # Review endpoints are derived from the cached pull inventory, never globbed:
    # old review files left after an incomplete refresh cannot enter the index.
    index = 0
    while index < len(files):
        path = files[index]
        index += 1
        name = f"reviews/{path.stem}" if path.parent.name == "reviews" else path.stem
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
            items = envelope["items"]
            if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
                raise ValueError("Cache items must be an array of objects")
            ok = envelope.get("complete") is True and not envelope.get("error")
            coverage["endpoints"].append({"name": name, "path": str(path), "count": len(items), "pages": envelope.get("pages", 0), "complete": ok})
            coverage["counts"][name] = len(items)
            if not ok:
                complete = False
                error(manifest, f"github:{repo}:{name}", envelope.get("error") or "Incomplete cached endpoint")
            data[name] = items
            if name == "pulls":
                files.extend(root / "reviews" / f"{int(item['number'])}.json" for item in sorted(items, key=lambda row: row["number"]))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            complete = False
            error(manifest, f"github:{repo}:{name}", exc)
    coverage["complete"] = complete
    pulls = {item["number"]: item for item in data.get("pulls", [])}
    issues = {item["number"]: item for item in data.get("issues", [])}
    for name in sorted(data):
        for item in sorted(data[name], key=lambda row: (row.get("number", 0), row.get("id", 0))):
            if name == "issues" and "pull_request" in item:
                continue  # The pulls endpoint has authoritative merged_at.
            number = item.get("number")
            parent = item
            if name == "issue-comments":
                match = re.search(r"/issues/(\d+)$", item.get("issue_url", ""))
                number = int(match.group(1)) if match else None
                parent = pulls.get(number, issues.get(number, {}))
            elif name == "review-comments":
                match = re.search(r"/pulls/(\d+)$", item.get("pull_request_url", ""))
                number = int(match.group(1)) if match else None
                parent = pulls.get(number, {})
            elif name.startswith("reviews/"):
                number = int(name.split("/")[1])
                parent = pulls.get(number, {})
            status = "merged" if parent.get("merged_at") else "branch-only" if number in pulls else "proposal"
            url = item.get("html_url") or (f"https://github.com/{repo}/pull/{number}" if number in pulls else f"https://github.com/{repo}/issues/{number}")
            if not re.match(r"https://github\.com/[^/]+/[^/]+/(?:issues|pull)/\d+", url):
                error(manifest, f"github:{repo}:{name}", "Missing canonical issue/PR URL")
                coverage["complete"] = False
                continue
            title = parent.get("title", item.get("title", name))
            method = f"{repo}#{number}: {title}"
            body = "\n".join(filter(None, (item.get("title", ""), item.get("body") or "")))
            for line in body.splitlines():
                if compact(line):
                    yield record(method, url, status, "unverified", "pr" if name == "pulls" else "issue" if name == "issues" else "comment", line)


def write_index(records: list[dict[str, str]], output_dir: Path) -> dict[str, Any]:
    """Replace deterministic JSONL and SQLite files after successful construction."""
    output_dir.mkdir(parents=True, exist_ok=True)
    unique = {tuple(row[field] for field in FIELDS) for row in records}
    ordered = sorted(unique)
    payload = "".join(json.dumps(dict(zip(FIELDS, row)), ensure_ascii=False, sort_keys=True) + "\n" for row in ordered)
    fd, temporary = tempfile.mkstemp(prefix=".capability-index-", dir=output_dir)
    os.close(fd)
    path = Path(temporary)
    try:
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("PRAGMA journal_mode=DELETE")
            db.execute("CREATE TABLE entries (id INTEGER PRIMARY KEY, method TEXT NOT NULL, source TEXT NOT NULL, status TEXT NOT NULL, wired TEXT NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL)")
            db.execute("CREATE VIRTUAL TABLE entries_fts USING fts5(method, source, status, wired, kind, text, content='entries', content_rowid='id')")
            db.executemany("INSERT INTO entries(method,source,status,wired,kind,text) VALUES (?,?,?,?,?,?)", ordered)
            db.execute("INSERT INTO entries_fts(entries_fts) VALUES ('rebuild')")
            db.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("INSERT INTO metadata VALUES ('schema', 'look-first.index.v1')")
            db.execute("INSERT INTO metadata VALUES ('jsonl_sha256', ?)", (hashlib.sha256(payload.encode()).hexdigest(),))
        jsonl = output_dir / "capability-index.jsonl"
        fd, json_temp = tempfile.mkstemp(prefix=".capability-jsonl-", dir=output_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(payload)
            os.replace(json_temp, jsonl)
        finally:
            if os.path.exists(json_temp):
                os.unlink(json_temp)
        os.replace(path, output_dir / "capability-index.sqlite3")
    finally:
        path.unlink(missing_ok=True)
    return {"entries": len(ordered), "jsonl_sha256": hashlib.sha256(payload.encode()).hexdigest(), "status_counts": dict(sorted(Counter(row[2] for row in ordered).items())), "kind_counts": dict(sorted(Counter(row[4] for row in ordered).items()))}


def generate(*, config_path: Path | str | None = None, output_dir: Path | str = DEFAULT_OUTPUT, cache_dir: Path | str = DEFAULT_CACHE, checkout: Path | str | None = None, refresh_remote: bool = False, timeout: float = 60) -> dict[str, Any]:
    config_path = Path(config_path) if config_path else Path(__file__).with_name("roots.json")
    output_dir, cache_dir = Path(output_dir).expanduser().absolute(), Path(cache_dir).expanduser().absolute()
    checkout = Path(checkout).resolve() if checkout else Path(__file__).resolve().parents[2]
    manifest: dict[str, Any] = {"schema": "look-first.manifest.v1", "complete": False, "remote_refreshed": refresh_remote, "cache_dir": str(cache_dir), "output_dir": str(output_dir), "config": str(config_path.resolve()), "local": [], "github": [], "skills": [], "errors": []}
    config = config_roots(config_path, checkout, manifest)
    manifest["installation"] = {**config.get("installation", {}), "applied": False}
    if refresh_remote:
        for spec in config["repositories"]:
            if spec.get("github"):
                _collect.collect_repo(spec["github"], cache_dir, timeout=timeout)
    evidence = wiring_evidence(config, manifest)
    records = []
    for spec in config["repositories"]:
        records.extend(collect_local(spec, cache_dir, evidence, manifest))
        records.extend(remote_records(spec, cache_dir, manifest))
    metadata = skill_metadata(config["skill_metadata_paths"], manifest)
    records.extend(collect_skills(config["skill_roots"], evidence, manifest, metadata, cache_dir))
    manifest["index"] = write_index(records, output_dir)
    manifest["errors"] = sorted(manifest["errors"], key=lambda item: (item["scope"], item["error"]))
    manifest["complete"] = not manifest["errors"] and all(item["complete"] for category in ("local", "github", "skills") for item in manifest[category])
    dump_json(output_dir / "capability-index.manifest.json", manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--checkout", type=Path)
    parser.add_argument("--refresh-remote", action="store_true")
    parser.add_argument("--timeout", type=float, default=60, help="Per-page GitHub timeout in seconds")
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    try:
        manifest = generate(config_path=args.config, output_dir=args.output_dir, cache_dir=args.cache_dir, checkout=args.checkout, refresh_remote=args.refresh_remote, timeout=args.timeout)
    except (OSError, ValueError, RuntimeError, sqlite3.Error, subprocess.TimeoutExpired) as exc:
        print(f"look-first: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"complete": manifest["complete"], "errors": len(manifest["errors"]), "index": manifest["index"], "manifest": str(Path(args.output_dir).expanduser() / "capability-index.manifest.json")}, sort_keys=True))
    return 0 if manifest["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
