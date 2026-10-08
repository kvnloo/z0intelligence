"""Resident provenance-preserving context resolver.

First reusable primitive for the evidence-to-action loop. Resolves bounded
information requirements into source-backed evidence without loading GPU
models or inventing a second scheduler.

Does NOT authorize work, flip bridge execution live, or mark verified_success.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from . import paths
from .file_search import search_repository as _fff_search_repository
from .memory_contract import MemoryScope

SCHEMA = "z0int.context_resolve.v1"
RECIPE_SCHEMA = "z0int.resolution_recipe.v1"

TrustClass = Literal[
    "authoritative_task",
    "project_constraint",
    "code",
    "conversation",
    "derived_memory",
    "index_hit",
    "unknown",
]


@dataclass(frozen=True)
class EvidenceRef:
    source_id: str
    source_version: str
    locator: str
    trust_class: TrustClass
    observed_at: str
    excerpt: str | None = None
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if d.get("excerpt") is None:
            d.pop("excerpt", None)
        if d.get("note") is None:
            d.pop("note", None)
        return d


@dataclass(frozen=True)
class InformationNeed:
    id: str
    description: str
    kind: Literal["exact_path", "exact_symbol", "natural_language", "memory"] = "natural_language"
    path: str | None = None
    symbol: str | None = None
    required: bool = True


@dataclass(frozen=True)
class ResolutionRecipe:
    capability_id: str
    request_signature: str
    scope_fingerprint: str
    policy_revision: str
    source_epochs: dict[str, str]
    operations: tuple[dict[str, Any], ...]
    required_evidence_fields: tuple[str, ...]
    verifier_revision: str
    hardware_profile: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": RECIPE_SCHEMA,
            "capability_id": self.capability_id,
            "request_signature": self.request_signature,
            "scope_fingerprint": self.scope_fingerprint,
            "policy_revision": self.policy_revision,
            "source_epochs": dict(self.source_epochs),
            "operations": list(self.operations),
            "required_evidence_fields": list(self.required_evidence_fields),
            "verifier_revision": self.verifier_revision,
            "hardware_profile": self.hardware_profile,
        }


@dataclass
class ContextPacket:
    """Source-backed requirements packet. Map into AODL provenance/evidence — not a new HOTL kind."""

    schema: str = SCHEMA
    task_id: str | None = None
    needs: list[InformationNeed] = field(default_factory=list)
    evidence: list[EvidenceRef] = field(default_factory=list)
    contradictions: list[str] = field(default_factory=list)
    unresolved_gaps: list[str] = field(default_factory=list)
    recipe: ResolutionRecipe | None = None
    measurements: dict[str, Any] = field(default_factory=dict)
    aodl_projection: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "task_id": self.task_id,
            "needs": [asdict(n) for n in self.needs],
            "evidence": [e.to_dict() for e in self.evidence],
            "contradictions": list(self.contradictions),
            "unresolved_gaps": list(self.unresolved_gaps),
            "recipe": self.recipe.to_dict() if self.recipe else None,
            "measurements": dict(self.measurements),
            "aodl_projection": dict(self.aodl_projection),
        }


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _sha16(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _file_snapshot(path: Path) -> tuple[bytes, os.stat_result] | None:
    """Read stable file bytes and metadata from one open file descriptor."""
    for _attempt in range(2):
        try:
            with path.open("rb") as stream:
                before = os.fstat(stream.fileno())
                if not stat.S_ISREG(before.st_mode):
                    return None
                data = stream.read()
                after = os.fstat(stream.fileno())
        except OSError:
            return None
        before_key = (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        after_key = (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if before_key == after_key and len(data) == after.st_size:
            return data, after
    return None


def _file_version(
    path: Path,
    *,
    snapshot: tuple[bytes, os.stat_result] | None = None,
) -> str:
    stable_snapshot = snapshot if snapshot is not None else _file_snapshot(path)
    if stable_snapshot is None:
        return "missing"
    data, st = stable_snapshot
    digest = hashlib.sha256(data).hexdigest()
    return f"mtime_ns={st.st_mtime_ns}:size={len(data)}:sha256={digest}"


def _scope_fingerprint(
    project_root: Path | None,
    collections: tuple[str, ...],
    scope_identity: str | None = None,
) -> str:
    parts = [
        str(project_root.resolve()) if project_root else "",
        ",".join(collections),
        os.environ.get("Z0INT_HOME", ""),
        scope_identity or "",
    ]
    return _sha16("|".join(parts))


def _request_signature(
    needs: list[InformationNeed],
    task_id: str | None,
    canonical_repo: str | None = None,
    *,
    memory_request: dict[str, Any] | None = None,
) -> str:
    request = {
        "task_id": task_id,
        "canonical_repo": canonical_repo,
        "needs": [asdict(n) for n in needs],
    }
    if memory_request is not None:
        request["memory"] = memory_request
    blob = json.dumps(request, sort_keys=True, ensure_ascii=False)
    return _sha16(blob)


def _canonical_slug(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip().removesuffix(".git").strip("/")
    if re.fullmatch(r"[^/\s:]+/[^/\s:]+", text):
        return text.lower()
    return None


def _read_component_registry(
    requested: str,
    registry_path: Path | str | None,
    registry_format: str,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Read one explicitly selected z0 registry representation.

    Returns the orientation base and the components mapping, or ``None`` with
    the reason recorded in ``base["gaps"]``.
    """
    base: dict[str, Any] = {
        "status": "unavailable",
        "requested": requested,
        "root": None,
        "gaps": [],
        "registry_path": None,
        "registry_format": registry_format,
        "registry_schema": None,
        "registry_representation": None,
        "registry_source_version": None,
        "registry_canonical_repo": None,
        "registry_generated_from": None,
        "component_id": None,
        "repo_slug": None,
        "boundaries": {},
    }
    if registry_format not in {"canonical", "generated"}:
        base["gaps"].append(f"unsupported registry format {registry_format!r}")
        return base, None
    if registry_path is None:
        base["gaps"].append("canonical registry path is required for repository orientation")
        return base, None
    unresolved_path = Path(registry_path).expanduser()
    try:
        path = unresolved_path.resolve()
    except (OSError, RuntimeError) as exc:
        base["registry_path"] = str(unresolved_path)
        base["gaps"].append(
            f"registry path unavailable at {unresolved_path}: {type(exc).__name__}: {exc}"
        )
        return base, None
    base["registry_path"] = str(path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        base["gaps"].append(f"registry unavailable at {path}: {type(exc).__name__}: {exc}")
        return base, None
    digest = hashlib.sha256(raw).hexdigest()
    base["registry_source_version"] = f"sha256:{digest}"
    try:
        import yaml

        document = yaml.safe_load(raw) or {}
    except Exception as exc:
        base["gaps"].append(f"registry parse failed: {type(exc).__name__}: {exc}")
        return base, None
    if not isinstance(document, dict):
        base["gaps"].append("registry root must be a mapping")
        return base, None

    if registry_format == "canonical":
        schema = document.get("schema")
        if schema not in (None, "z0.registry.component.v1"):
            base["gaps"].append(f"unsupported canonical registry schema {schema!r}")
            return base, None
        if schema is None and ("version" in document or "generated_from" in document):
            base["gaps"].append(
                "generated registry requires registry_format='generated' and explicit provenance"
            )
            return base, None
        components = document.get("components")
        base["registry_schema"] = str(schema or "components_mapping_without_schema")
        base["registry_representation"] = (
            "z0.registry.component.v1" if schema else "canonical_components_mapping_without_schema"
        )
    else:
        # The flattened generated registry is a separate, explicitly selected
        # representation. Preserve both its own version and source declaration.
        if document.get("version") != 2 or document.get("ecosystem") != "zer0":
            base["gaps"].append("generated registry must declare version 2 and ecosystem zer0")
            return base, None
        base["registry_schema"] = f"generated:v{document.get('version')}"
        base["registry_representation"] = "generated_flattened_v2"
        base["registry_canonical_repo"] = document.get("canonical_repo")
        base["registry_generated_from"] = document.get("generated_from")
        if not isinstance(document.get("generated_from"), list) or not document.get("generated_from"):
            base["gaps"].append("generated registry provenance is missing generated_from")
            return base, None
        components = document.get("components")
    if registry_format == "canonical":
        base["registry_canonical_repo"] = document.get("canonical_repo")
    if not isinstance(components, dict):
        base["gaps"].append("registry components must be a mapping keyed by component ID")
        return base, None
    return base, components


def _load_component_registry(
    canonical_repo: str,
    registry_path: Path | str | None,
    registry_format: str,
) -> dict[str, Any]:
    """Read one explicitly selected z0 registry representation and resolve identity."""
    requested = canonical_repo.strip()
    base, components = _read_component_registry(requested, registry_path, registry_format)
    if components is None:
        return base

    requested_slug = _canonical_slug(requested)
    matches: list[tuple[str, dict[str, Any]]] = []
    if requested in components and isinstance(components[requested], dict):
        matches.append((requested, components[requested]))
    else:
        for component_id, metadata in components.items():
            if isinstance(metadata, dict) and _canonical_slug(metadata.get("repo")) == requested_slug:
                matches.append((str(component_id), metadata))
    if len(matches) != 1:
        if not matches:
            base["gaps"].append(f"canonical repository {requested!r} is not declared in the registry")
        else:
            base["status"] = "ambiguous"
            base["gaps"].append(f"canonical repository {requested!r} maps to multiple components")
        return base
    component_id, component = matches[0]
    repo_slug = _canonical_slug(component.get("repo"))
    if repo_slug is None:
        base["gaps"].append(f"component {component_id!r} has no valid canonical repo slug")
        return base
    boundaries = component.get("boundaries")
    boundaries = dict(boundaries) if isinstance(boundaries, dict) else {}
    for key in ("owns", "not_here"):
        if key in component and key not in boundaries:
            boundaries[key] = component[key]
    base.update(
        {
            "component_id": component_id,
            "repo_slug": repo_slug,
            "boundaries": boundaries,
        }
    )
    return base


_OWNERSHIP_STOPWORDS = frozenset(
    "about above after again against also another around because before being below between both cannot "
    "could does doing done down during each either every existing from have having into itself just "
    "make makes more most must only other over print prints same should small some such than that "
    "their them then there these they this those through under until very were what when where which "
    "while will with within without would your add adds added using used uses use new one two".split()
)


def _ownership_stems(text: Any) -> set[str]:
    """Deterministic vocabulary key: lowercase words, stopwords dropped, six-letter prefix."""
    if not isinstance(text, str):
        return set()
    return {
        word[:6]
        for word in re.findall(r"[a-z][a-z0-9]{3,}", text.lower())
        if word not in _OWNERSHIP_STOPWORDS
    }


def resolve_owning_repository(
    task_text: str,
    registry_path: Path | str | None,
    registry_format: str = "canonical",
) -> dict[str, Any]:
    """Map a natural-language task to the one registry component that owns it.

    The registry is the only input: a component's declared ``owns`` phrases and
    provided interfaces count double, its name and summary once, and anything it
    lists under ``not_here`` never counts for it. A component is selected only
    when it is the sole clear leader; otherwise the caller must observe.
    """
    base, components = _read_component_registry("", registry_path, registry_format)
    result: dict[str, Any] = {
        "status": "unavailable",
        "canonical_repo": None,
        "component_id": None,
        "matched_terms": [],
        "candidates": [],
        "gaps": list(base["gaps"]),
        "registry_path": base["registry_path"],
        "registry_source_version": base["registry_source_version"],
    }
    if components is None:
        return result
    task_stems = _ownership_stems(task_text)
    scored: list[dict[str, Any]] = []
    for component_id, metadata in components.items():
        if not isinstance(metadata, dict):
            continue
        repo_slug = _canonical_slug(metadata.get("repo"))
        if repo_slug is None:
            continue
        boundaries = metadata.get("boundaries") if isinstance(metadata.get("boundaries"), dict) else {}
        owns = boundaries.get("owns", metadata.get("owns")) or []
        not_here = boundaries.get("not_here", metadata.get("not_here")) or []
        provides = metadata.get("provides") or []
        strong: set[str] = set()
        for phrase in [*owns, *provides] if isinstance(owns, list) and isinstance(provides, list) else []:
            strong |= _ownership_stems(phrase)
        weak = _ownership_stems(metadata.get("summary")) | _ownership_stems(metadata.get("name"))
        weak |= _ownership_stems(str(component_id))
        disclaimed: set[str] = set()
        for phrase in not_here if isinstance(not_here, list) else []:
            disclaimed |= _ownership_stems(phrase)
        strong_hits = (task_stems & strong) - disclaimed
        weak_hits = (task_stems & weak) - strong_hits - disclaimed
        scored.append(
            {
                "component_id": str(component_id),
                "canonical_repo": repo_slug,
                "score": 2 * len(strong_hits) + len(weak_hits),
                "matched_terms": sorted(strong_hits | weak_hits),
            }
        )
    scored.sort(key=lambda item: (-item["score"], item["component_id"]))
    result["candidates"] = scored[:5]
    leader = scored[0] if scored else None
    runner_up = scored[1]["score"] if len(scored) > 1 else 0
    if leader is None or leader["score"] < 2:
        result["status"] = "unresolved"
        result["gaps"].append("task names no capability that a registry component declares as owned")
        return result
    if leader["score"] < 2 * runner_up or leader["score"] - runner_up < 2:
        result["status"] = "ambiguous"
        contenders = ", ".join(item["component_id"] for item in scored[:2])
        result["gaps"].append(f"repository ownership is ambiguous between {contenders}")
        return result
    result.update(
        status="resolved",
        canonical_repo=leader["canonical_repo"],
        component_id=leader["component_id"],
        matched_terms=leader["matched_terms"],
    )
    return result


def _nested_repository_fingerprint(
    root: Path,
    reader: Any,
    errors: list[str],
    seen: set[str],
) -> str | None:
    """Hash a gitlink's HEAD and dirty contents without recursing into itself."""
    key = str(root.resolve())
    if key in seen:
        errors.append(f"submodule fingerprint recursion detected at {root}")
        return None
    top = reader.git(root, "rev-parse", "--show-toplevel")
    if not top:
        return None
    repo = Path(top.strip()).resolve()
    if repo != root.resolve():
        return None
    head = reader.git(repo, "rev-parse", "HEAD")
    status = reader.git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    index = reader.git(repo, "ls-files", "--stage", "-z")
    if head is None or status is None or index is None:
        errors.append(f"submodule state unavailable at {repo}")
        return None
    nested = _worktree_content_fingerprint(
        repo,
        status,
        index,
        reader=reader,
        errors=errors,
        seen=seen | {key},
    )
    h = hashlib.sha256()
    h.update(head.strip().encode("ascii", "replace"))
    h.update(b"\0" + nested.encode("ascii", "replace"))
    return f"sha256:{h.hexdigest()}"


def _worktree_content_fingerprint(
    root: Path,
    status_text: str,
    index_text: str = "",
    *,
    reader: Any | None = None,
    errors: list[str] | None = None,
    seen: set[str] | None = None,
) -> str:
    """Hash changed paths, filesystem modes, bytes, and nested Git worktrees."""
    fingerprint_errors = errors if errors is not None else []
    visited = seen if seen is not None else {str(root.resolve())}
    entries = status_text.split("\0")
    records: list[tuple[str, str]] = []
    i = 0
    while i < len(entries):
        record = entries[i]
        i += 1
        if not record:
            continue
        status = record[:2]
        rel = record[3:] if len(record) > 3 else ""
        if not rel:
            continue
        records.append((status, rel))
        if "R" in status or "C" in status:
            if i < len(entries) and entries[i]:
                records.append((status, entries[i]))
                i += 1
    staged: dict[str, str] = {}
    for record in index_text.split("\0"):
        if "\t" not in record:
            continue
        metadata, rel = record.split("\t", 1)
        staged[rel] = metadata
    recorded_paths = {rel for _status, rel in records}
    for rel, metadata in staged.items():
        if metadata.split(" ", 1)[0] == "160000" and rel not in recorded_paths:
            records.append(("gitlink", rel))
    h = hashlib.sha256()
    for status, rel in sorted(set(records)):
        h.update(status.encode("ascii", "replace") + b"\0" + rel.encode("utf-8", "replace") + b"\0")
        index_entry = staged.get(rel, "unindexed")
        is_gitlink = index_entry.split(" ", 1)[0] == "160000"
        h.update(b"index\0" + index_entry.encode("ascii", "replace") + b"\0")
        path = root / rel
        try:
            path_stat = path.lstat()
            h.update(f"mode:{stat.S_IMODE(path_stat.st_mode):o}\0".encode("ascii"))
            if stat.S_ISLNK(path_stat.st_mode):
                h.update(b"symlink\0" + os.readlink(path).encode("utf-8", "replace"))
            elif stat.S_ISREG(path_stat.st_mode):
                h.update(b"file\0" + hashlib.sha256(path.read_bytes()).digest())
            elif stat.S_ISDIR(path_stat.st_mode):
                if is_gitlink and reader is not None:
                    nested = _nested_repository_fingerprint(path, reader, fingerprint_errors, visited)
                    if nested is None:
                        fingerprint_errors.append(f"submodule fingerprint unavailable at {path}")
                        h.update(b"submodule-unavailable\0")
                    else:
                        h.update(b"submodule\0" + nested.encode("ascii"))
                else:
                    h.update(b"directory\0")
            else:
                h.update(b"special-file\0")
        except FileNotFoundError:
            # A deletion is a valid dirty worktree state, not an inability to
            # inspect the checkout. Its path and index metadata above distinguish
            # it from every other tracked/untracked state.
            h.update(b"deleted\0")
        except OSError as exc:
            kind = "submodule" if is_gitlink else "changed path"
            fingerprint_errors.append(f"{kind} fingerprint unavailable at {path}: {exc}")
            h.update(f"unreadable:{type(exc).__name__}:{exc}".encode("utf-8", "replace"))
        h.update(b"\0")
    return f"sha256:{h.hexdigest()}"


def _repo_state(root: Path, reader: Any) -> dict[str, Any]:
    from .state_packet import _gh_slug

    head = (reader.git(root, "rev-parse", "HEAD") or "").strip() or None
    branch = (reader.git(root, "symbolic-ref", "--quiet", "--short", "HEAD") or "").strip() or None
    branch = branch or "DETACHED"
    origin_slug = _canonical_slug(_gh_slug(root, reader))
    status = reader.git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    index = reader.git(root, "ls-files", "--stage", "-z")
    errors: list[str] = []
    if head is None:
        errors.append("git HEAD unavailable")
    if status is None:
        errors.append("git status unavailable")
    if index is None:
        errors.append("git index unavailable")
    if origin_slug is None:
        errors.append("canonical GitHub origin identity unavailable")
    fingerprint_errors: list[str] = []
    if status is not None and index is not None:
        worktree_fingerprint = _worktree_content_fingerprint(
            root,
            status,
            index,
            reader=reader,
            errors=fingerprint_errors,
            seen={str(root.resolve())},
        )
    else:
        worktree_fingerprint = None
    if fingerprint_errors:
        errors.extend(fingerprint_errors)
        worktree_fingerprint = None
    return {
        "root": str(root),
        "origin_slug": origin_slug,
        "git_head": head,
        "branch": branch,
        "dirty": bool(status),
        "worktree_fingerprint": worktree_fingerprint,
        "state_errors": errors,
    }


def _orient_repository(
    canonical_repo: str,
    *,
    registry_path: Path | str | None,
    candidate_roots: list[Path | str] | tuple[Path | str, ...] | None,
    project_root: Path | None,
    registry_format: str,
) -> dict[str, Any]:
    from .state_packet import Reader

    orientation = _load_component_registry(canonical_repo, registry_path, registry_format)
    orientation.update(
        {
            "explicit_project_root": str(project_root) if project_root is not None else None,
            "selection_basis": None,
            "candidate_roots": [],
            "candidate_matches": [],
            "candidate_root_errors": [],
            "ignored_candidate_root_errors": [],
            "worktree_enumeration_errors": [],
            "origin_slug": None,
            "git_head": None,
            "branch": None,
            "dirty": None,
            "worktree_fingerprint": None,
            "worktree": None,
        }
    )
    if orientation["gaps"]:
        return orientation
    if candidate_roots is None:
        roots: list[Path | str] = []
    elif isinstance(candidate_roots, (str, Path)):
        roots = [candidate_roots]
    else:
        roots = list(candidate_roots)
    if project_root is not None:
        roots.insert(0, project_root)
    reader = Reader()
    inspected: dict[str, dict[str, Any]] = {}
    worktree_enumeration_errors: list[str] = []
    candidate_root_errors: list[str] = []
    explicit_root_key: str | None = None

    def unavailable_root(raw_root: Any, message: str) -> None:
        root_text = str(raw_root)
        orientation["candidate_roots"].append(
            {"root": root_text, "status": "unavailable", "error": message}
        )
        candidate_root_errors.append(f"candidate root unavailable at {root_text}: {message}")

    def inspect_root(raw_root: Path | str, *, is_worktree: bool) -> Path | None:
        try:
            given = Path(raw_root).expanduser().resolve()
            info = given.stat()
        except (OSError, RuntimeError) as exc:
            unavailable_root(raw_root, f"{type(exc).__name__}: {exc}")
            return None
        if not stat.S_ISDIR(info.st_mode):
            unavailable_root(given, "candidate path is not a directory")
            return None
        top = reader.git(given, "rev-parse", "--show-toplevel")
        if not top:
            git_metadata = given / ".git"
            if git_metadata.exists() or git_metadata.is_symlink():
                unavailable_root(given, "Git metadata exists but checkout root could not be read")
            else:
                orientation["candidate_roots"].append({"root": str(given), "status": "not_git"})
            return None
        root = Path(top.strip()).resolve()
        key = str(root)
        if key not in inspected:
            state = _repo_state(root, reader)
            state["worktree"] = is_worktree
            inspected[key] = state
            orientation["candidate_roots"].append(state)
            if state.get("origin_slug") is None:
                candidate_root_errors.append(
                    f"candidate root origin identity unavailable at {root}"
                )
        return root

    for candidate_index, raw_root in enumerate(roots):
        root = inspect_root(raw_root, is_worktree=False)
        if candidate_index == 0 and project_root is not None:
            explicit_root_key = str(root) if root is not None else None
        if root is None:
            continue
        worktrees = reader.git(root, "worktree", "list", "--porcelain")
        if worktrees is None:
            worktree_enumeration_errors.append(f"could not enumerate Git worktrees from {root}")
            continue
        worktree_paths = [
            Path(line[len("worktree ") :])
            for line in worktrees.splitlines()
            if line.startswith("worktree ")
        ]
        primary = worktree_paths[0].resolve() if worktree_paths else root
        for worktree_path in worktree_paths:
            is_linked = worktree_path.resolve() != primary
            observed = inspect_root(worktree_path, is_worktree=is_linked)
            if observed is not None:
                inspected[str(observed)]["worktree"] = is_linked

    canonical_slug = orientation["repo_slug"]
    matches = [state for state in inspected.values() if state.get("origin_slug") == canonical_slug]
    orientation["candidate_matches"] = [state["root"] for state in matches]
    orientation["candidate_root_errors"] = list(candidate_root_errors)
    orientation["worktree_enumeration_errors"] = list(worktree_enumeration_errors)
    if project_root is not None:
        orientation["selection_basis"] = "explicit_project_root"
        orientation["ignored_candidate_root_errors"] = list(candidate_root_errors)
        orientation["ignored_worktree_enumeration_errors"] = list(worktree_enumeration_errors)
        explicit = inspected.get(explicit_root_key) if explicit_root_key is not None else None
        if explicit is None or explicit.get("origin_slug") != canonical_slug:
            orientation["status"] = "wrong_root"
            actual = explicit.get("origin_slug") if explicit else None
            orientation["gaps"].append(
                f"wrong root: explicit project_root has origin {actual!r}, expected {canonical_slug!r}"
            )
            return orientation
        selected = explicit
    elif len(matches) > 1:
        orientation["status"] = "ambiguous"
        orientation["gaps"].append(
            f"ambiguous candidates: multiple checkouts have origin {canonical_slug!r}; choose project_root explicitly"
        )
        return orientation
    elif not matches and not candidate_root_errors and not worktree_enumeration_errors:
        orientation["status"] = "missing"
        orientation["gaps"].append(f"no candidate checkout has origin {canonical_slug!r}")
        return orientation
    elif candidate_root_errors or worktree_enumeration_errors:
        orientation["status"] = "unavailable"
        orientation["gaps"].extend(candidate_root_errors)
        orientation["gaps"].extend(worktree_enumeration_errors)
        if not matches:
            orientation["gaps"].append("candidate enumeration was incomplete; repository identity is unresolved")
        return orientation
    else:
        selected = matches[0]
        orientation["selection_basis"] = "sole_origin_match"
    orientation.update(selected)
    orientation["status"] = "partial" if selected.get("state_errors") else "ready"
    if selected.get("state_errors"):
        orientation["gaps"].extend(
            f"selected Git checkout is incomplete: {error}" for error in selected["state_errors"]
        )
    orientation["root"] = selected["root"]
    orientation["worktree"] = selected.get("worktree", False)
    return orientation


def _qmd_bin() -> str | None:
    return shutil.which("qmd")


def _qmd_search(
    query: str, *, limit: int = 5, timeout_s: float = 8.0
) -> dict[str, Any]:
    """Run one QMD query while distinguishing complete-empty from provider failure."""
    bin_path = _qmd_bin()
    if not bin_path:
        return {"status": "unavailable", "coverage": "unavailable", "hits": [], "error": "qmd is not installed"}
    try:
        proc = subprocess.run(
            [bin_path, "search", query, "--json", "-n", str(limit)],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "status": "error",
            "coverage": "unavailable",
            "hits": [],
            "error": f"{type(exc).__name__}: {exc}",
        }
    if proc.returncode != 0 or not proc.stdout.strip():
        first_error = f"qmd search exited {proc.returncode}" if proc.returncode else "qmd returned empty output"
        # Older QMD releases may not support --json. Plain output can retain
        # evidence, but without structured rows it cannot qualify completeness.
        try:
            proc2 = subprocess.run(
                [bin_path, "search", query],
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {
                "status": "error",
                "coverage": "unavailable",
                "hits": [],
                "error": f"{type(exc).__name__}: {exc}",
            }
        if proc2.returncode != 0:
            return {
                "status": "error",
                "coverage": "unavailable",
                "hits": [],
                "error": f"{first_error}; plain search exited {proc2.returncode}",
            }
        hits = [{"raw_preview": proc2.stdout[:200], "transport": "qmd_search_text"}] if proc2.stdout.strip() else []
        return {
            "status": "ready",
            "coverage": "partial",
            "hits": hits,
            "error": "QMD returned unstructured plain-text results",
        }
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {
            "status": "error",
            "coverage": "unavailable",
            "hits": [],
            "error": "QMD returned invalid JSON",
        }
    if isinstance(data, list):
        truncated = len(data) > limit
        return {
            "status": "ready",
            "coverage": "partial" if truncated else "complete",
            "hits": data[:limit],
            "error": None,
        }
    if isinstance(data, dict):
        for key in ("results", "hits", "documents"):
            if key in data:
                hits = data[key]
                if isinstance(hits, list):
                    total = data.get("total")
                    truncated = len(hits) > limit or (
                        isinstance(total, int) and total > min(len(hits), limit)
                    )
                    return {
                        "status": "ready",
                        "coverage": "partial" if truncated else "complete",
                        "hits": hits[:limit],
                        "error": None,
                    }
                return {
                    "status": "error",
                    "coverage": "unavailable",
                    "hits": [],
                    "error": f"QMD JSON field {key!r} is not a list",
                }
        return {
            "status": "error",
            "coverage": "unavailable",
            "hits": [],
            "error": "QMD JSON response has no results field",
        }
    return {
        "status": "error",
        "coverage": "unavailable",
        "hits": [],
        "error": "QMD JSON response must be a list or mapping",
    }


def _fff_evidence_refs(
    result: dict[str, Any],
    project_root: Path,
    *,
    kind: str,
    limit: int = 5,
) -> tuple[list[EvidenceRef], int, list[str]]:
    """Normalize current FFF hits and report verified content plus stale hits."""
    refs: list[EvidenceRef] = []
    seen: set[str] = set()
    snapshots: dict[str, tuple[bytes, os.stat_result] | None] = {}
    stale_hits: list[str] = []
    verified_content_hits = 0
    version = str(result.get("package_version") or "unknown")
    epoch = int(result.get("index_epoch") or 0)

    def safe_path(raw: Any) -> Path | None:
        if not isinstance(raw, str) or not raw:
            return None
        try:
            path = (project_root / raw).resolve()
            path.relative_to(project_root)
            return path
        except (OSError, ValueError):
            return None

    def snapshot_for(path: Path) -> tuple[bytes, os.stat_result] | None:
        key = str(path)
        if key not in snapshots:
            snapshots[key] = _file_snapshot(path)
        return snapshots[key]

    for hit in list(result.get("content_hits") or [])[:limit]:
        if not isinstance(hit, dict):
            stale_hits.append("FFF returned a malformed content hit")
            continue
        path = safe_path(hit.get("path"))
        if path is None:
            stale_hits.append("FFF content hit has an unsafe or unavailable path")
            continue
        snapshot = snapshot_for(path)
        if snapshot is None:
            stale_hits.append(f"stale FFF content hit has no stable file snapshot at {path}")
            continue
        raw_line = hit.get("line")
        line = raw_line if isinstance(raw_line, int) and not isinstance(raw_line, bool) else 0
        lines = snapshot[0].decode("utf-8", errors="replace").splitlines()
        indexed_text = hit.get("text")
        if line <= 0 or line > len(lines) or not isinstance(indexed_text, str):
            stale_hits.append(f"stale FFF content hit has an invalid line/text at {path}:{line}")
            continue
        actual_text = lines[line - 1]
        if actual_text != indexed_text:
            stale_hits.append(f"stale FFF content hit differs from the file snapshot at {path}:{line}")
            continue
        locator = f"{path}:{line}" if line > 0 else str(path)
        if locator in seen:
            continue
        seen.add(locator)
        excerpt = "\\n".join(lines[max(0, line - 2) : min(len(lines), line + 1)])[:400] or None
        refs.append(
            EvidenceRef(
                source_id=f"file:{path}",
                source_version=f"{_file_version(path, snapshot=snapshot)};fff={version};epoch={epoch}",
                locator=locator,
                trust_class="index_hit",
                observed_at=_now_iso(),
                excerpt=excerpt,
                note="resident FFF content hit verified against a file snapshot; retrieval is evidence, not execution authority",
            )
        )
        verified_content_hits += 1

    for hit in list(result.get("path_hits") or [])[:limit]:
        if len(refs) >= limit:
            break
        if not isinstance(hit, dict):
            stale_hits.append("FFF returned a malformed filename hit")
            continue
        path = safe_path(hit.get("path"))
        if path is None:
            stale_hits.append("FFF filename hit has an unsafe or unavailable path")
            continue
        snapshot = snapshot_for(path)
        if snapshot is None:
            stale_hits.append(f"stale FFF filename hit has no stable file snapshot at {path}")
            continue
        locator = str(path)
        if locator in seen:
            continue
        seen.add(locator)
        refs.append(
            EvidenceRef(
                source_id=f"file:{path}",
                source_version=f"{_file_version(path, snapshot=snapshot)};fff={version};epoch={epoch}",
                locator=locator,
                trust_class="index_hit",
                observed_at=_now_iso(),
                note=(
                    f"resident FFF filename hypothesis for {kind}; "
                    "retrieval is evidence, not execution authority"
                ),
            )
        )
    return refs, verified_content_hits, stale_hits


def _fetch_exact_path(path_str: str, project_root: Path | None) -> EvidenceRef | None:
    p = Path(path_str).expanduser()
    if not p.is_absolute() and project_root is not None:
        p = (project_root / p).resolve()
    elif not p.is_absolute():
        return None
    else:
        try:
            p = p.resolve()
        except OSError:
            return None
    snapshot = _file_snapshot(p)
    if snapshot is None:
        return None
    data, _stat_result = snapshot
    text = data.decode("utf-8", errors="replace")
    # Bound excerpt — no full private dump
    excerpt = text[:400].replace("\n", "\\n")
    return EvidenceRef(
        source_id=f"file:{p}",
        source_version=_file_version(p, snapshot=snapshot),
        locator=str(p),
        trust_class="code" if p.suffix in {".py", ".ts", ".tsx", ".js", ".rs", ".go"} else "project_constraint",
        observed_at=_now_iso(),
        excerpt=excerpt,
    )


def _cache_dir() -> Path:
    root = paths.home() / "state" / "context_resolve"
    root.mkdir(parents=True, exist_ok=True)
    return root


def load_recipe_cache(signature: str) -> dict[str, Any] | None:
    path = _cache_dir() / f"recipe_{signature}.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def save_recipe_cache(recipe: ResolutionRecipe, packet_summary: dict[str, Any]) -> Path:
    path = _cache_dir() / f"recipe_{recipe.request_signature}.json"
    blob = {
        "recipe": recipe.to_dict(),
        "packet_summary": packet_summary,
        "saved_at": _now_iso(),
    }
    path.write_text(json.dumps(blob, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def project_to_aodl_fields(packet: ContextPacket) -> dict[str, Any]:
    """Map packet into existing AODL-ish provenance/evidence/constraint bags.

    Does not invent HOTL kinds or top-level intentContract fields.
    """
    return {
        "provenance": {
            "resolver": SCHEMA,
            "task_id": packet.task_id,
            "recipe_signature": packet.recipe.request_signature if packet.recipe else None,
            "source_epochs": dict(packet.recipe.source_epochs) if packet.recipe else {},
        },
        "evidence": [e.to_dict() for e in packet.evidence],
        "constraints": {
            "unresolved_gaps": list(packet.unresolved_gaps),
            "contradictions": list(packet.contradictions),
        },
        "observation": {
            "measurements": dict(packet.measurements),
        },
    }


def resolve_context(
    *,
    needs: list[InformationNeed] | None = None,
    query: str | None = None,
    task_id: str | None = None,
    project_root: Path | str | None = None,
    canonical_repo: str | None = None,
    registry_path: Path | str | None = None,
    candidate_roots: list[Path | str] | tuple[Path | str, ...] | None = None,
    registry_format: Literal["canonical", "generated"] = "canonical",
    collections: tuple[str, ...] = (),
    policy_revision: str = "v0",
    use_cache: bool = True,
    allow_fff: bool = True,
    allow_qmd: bool = True,
    allow_memory: bool = False,
    memory_scope: MemoryScope | None = None,
    memory_subject: str | None = None,
    memory_predicate: str | None = None,
) -> ContextPacket:
    """Resolve information needs into a provenance-preserving packet.

    Memory recall is off by default. Explicit local memory projections remain
    untrusted evidence and never grant instruction or action authority.
    Repository-scoped FFF is the first discovery tier: its process-resident Rust
    index + watcher amortizes repeated codebase searches. QMD remains the later
    lexical/docs tier. Neither retrieval layer grants action authority.
    """
    t0 = time.perf_counter()
    explicit_root = None
    if project_root is not None:
        raw_project_root = Path(project_root).expanduser()
        try:
            explicit_root = raw_project_root.resolve()
        except (OSError, RuntimeError):
            if canonical_repo is None:
                raise
            # Let canonical orientation record an explicit-root inspection gap
            # instead of allowing a symlink or filesystem error to escape.
            explicit_root = raw_project_root
    orientation: dict[str, Any] | None = None
    orientation_gaps: list[str] = []
    root = explicit_root
    if canonical_repo is not None:
        orientation = _orient_repository(
            canonical_repo,
            registry_path=registry_path,
            candidate_roots=candidate_roots,
            project_root=explicit_root,
            registry_format=registry_format,
        )
        if orientation.get("status") in {"ready", "partial"}:
            root = Path(orientation["root"]).resolve()
        else:
            root = None
            orientation_gaps = list(orientation.get("gaps") or [])
    need_list: list[InformationNeed] = list(needs or [])
    if query and not need_list:
        need_list.append(
            InformationNeed(id="q0", description=query, kind="natural_language", required=True)
        )
    if not need_list:
        raise ValueError("resolve_context requires needs or query")

    has_memory_need = any(need.kind == "memory" for need in need_list)
    memory_request = None
    if allow_memory and has_memory_need:
        memory_request = {
            "scope": memory_scope.to_dict() if isinstance(memory_scope, MemoryScope) else None,
            "subject": memory_subject,
            "predicate": memory_predicate,
        }
    sig = _request_signature(
        need_list,
        task_id,
        canonical_repo,
        memory_request=memory_request,
    )
    scope_identity = canonical_repo
    orientation_generation: str | None = None
    if orientation is not None:
        orientation_blob = json.dumps(
            {
                "requested": canonical_repo,
                "registry_path": orientation.get("registry_path"),
                "registry_format": orientation.get("registry_format"),
                "registry_schema": orientation.get("registry_schema"),
                "registry_source_version": orientation.get("registry_source_version"),
                "explicit_project_root": orientation.get("explicit_project_root"),
                "selection_basis": orientation.get("selection_basis"),
                "candidate_root_errors": orientation.get("candidate_root_errors"),
                "ignored_candidate_root_errors": orientation.get("ignored_candidate_root_errors"),
                "worktree_enumeration_errors": orientation.get("worktree_enumeration_errors"),
                "component_id": orientation.get("component_id"),
                "repo_slug": orientation.get("repo_slug"),
                "status": orientation.get("status"),
                "root": orientation.get("root"),
                "git_head": orientation.get("git_head"),
                "branch": orientation.get("branch"),
                "worktree_fingerprint": orientation.get("worktree_fingerprint"),
                "candidate_roots": orientation.get("candidate_roots"),
                "candidate_matches": orientation.get("candidate_matches"),
                "gaps": orientation.get("gaps"),
            },
            sort_keys=True,
            default=str,
        )
        orientation_generation = f"sha256:{hashlib.sha256(orientation_blob.encode('utf-8')).hexdigest()}"
        scope_identity = orientation_generation
    if memory_request is not None:
        scope_blob = json.dumps(memory_request, sort_keys=True, ensure_ascii=False)
        scope_identity = f"{scope_identity or ''}|memory:{_sha16(scope_blob)}"
    scope_fp = _scope_fingerprint(root, collections, scope_identity)
    ops: list[dict[str, Any]] = []
    evidence: list[EvidenceRef] = []
    gaps: list[str] = []
    contradictions: list[str] = []
    epochs: dict[str, str] = {"policy": policy_revision}
    if orientation is not None:
        if orientation_generation:
            epochs[f"orientation:{canonical_repo}"] = orientation_generation
        if orientation.get("registry_path") and orientation.get("registry_source_version"):
            epochs[f"registry:{orientation['registry_path']}"] = str(orientation["registry_source_version"])
        for candidate in orientation.get("candidate_roots", []):
            candidate_root = candidate.get("root")
            if candidate_root and candidate.get("origin_slug"):
                candidate_blob = json.dumps(
                    [
                        candidate.get("origin_slug"),
                        candidate.get("git_head"),
                        candidate.get("branch"),
                        candidate.get("worktree_fingerprint"),
                        candidate.get("state_errors"),
                    ],
                    ensure_ascii=False,
                )
                epochs[f"candidate:{candidate_root}"] = _sha16(candidate_blob)
        if orientation.get("root"):
            oriented_root = str(orientation["root"])
            if orientation.get("origin_slug"):
                epochs[f"repo:{oriented_root}"] = str(orientation["origin_slug"])
            if orientation.get("git_head"):
                epochs[f"head:{oriented_root}"] = str(orientation["git_head"])
            if orientation.get("branch"):
                epochs[f"branch:{oriented_root}"] = str(orientation["branch"])
            if orientation.get("worktree_fingerprint"):
                epochs[f"worktree:{oriented_root}"] = str(orientation["worktree_fingerprint"])
        gaps.extend(orientation_gaps)
        ops.append(
            {
                "op": "repo_orientation",
                "status": orientation.get("status"),
                "error": "; ".join(orientation_gaps) or None,
                "scanning": False,
                "generation": orientation_generation,
                "coverage": (
                    "complete" if orientation.get("status") == "ready"
                    else ("partial" if orientation.get("status") == "partial" else "unavailable")
                ),
                "required": True,
                "component_id": orientation.get("component_id"),
                "repo_slug": orientation.get("repo_slug"),
                "selection_basis": orientation.get("selection_basis"),
                "root": orientation.get("root"),
                "registry_source_version": orientation.get("registry_source_version"),
                "git_head": orientation.get("git_head"),
                "branch": orientation.get("branch"),
                "worktree_fingerprint": orientation.get("worktree_fingerprint"),
                "candidate_root_errors": orientation.get("candidate_root_errors"),
            }
        )

    if use_cache:
        cached = load_recipe_cache(sig)
        if cached and cached.get("recipe", {}).get("scope_fingerprint") == scope_fp:
            ops.append(
                {
                    "op": "recipe_cache_hit",
                    "signature": sig,
                    "status": "ready",
                    "error": None,
                    "scanning": False,
                    "generation": None,
                    "coverage": "complete",
                    "required": False,
                }
            )

    fff_status = "disabled" if not allow_fff else ("no_project_root" if root is None else "idle")
    fff_hits = 0
    fff_wall_ms = 0.0

    # Do not even spawn qmd status on a successful FFF fast path. QMD is a
    # later tier and should pay its process/model costs only when it is needed.
    qmd_status = "disabled" if not allow_qmd else "idle"
    qmd_status_error: str | None = None
    memory_projection_measurements: dict[str, Any] = {}

    def ensure_qmd_status() -> str:
        nonlocal qmd_status, qmd_status_error
        if qmd_status != "idle":
            return qmd_status
        bin_path = _qmd_bin()
        if not bin_path:
            qmd_status = "absent"
            return qmd_status
        qmd_status = "installed"
        try:
            st = subprocess.run(
                [bin_path, "status"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if st.returncode == 0:
                qmd_status = "ready"
                epochs["qmd_status_sha"] = _sha16(st.stdout[:2000])
            else:
                qmd_status = "error"
                qmd_status_error = f"qmd status exited {st.returncode}: {st.stderr[:200]}"
        except (OSError, subprocess.TimeoutExpired) as exc:
            qmd_status = "error"
            qmd_status_error = f"{type(exc).__name__}: {exc}"
        return qmd_status

    def add_source_epoch(ref: EvidenceRef) -> None:
        if ref.source_id.startswith("file:"):
            epochs[ref.source_id] = ref.source_version

    def add_operation(
        op_name: str,
        need: InformationNeed,
        *,
        status: str,
        coverage: str,
        error: str | None = None,
        scanning: bool | None = False,
        generation: str | None = None,
        required: bool | None = None,
        **details: Any,
    ) -> None:
        ops.append(
            {
                "op": op_name,
                "need": need.id,
                "status": status,
                "error": error,
                "scanning": scanning,
                "generation": generation,
                "coverage": coverage,
                "required": need.required if required is None else required,
                **details,
            }
        )

    def provider_coverage(result: dict[str, Any]) -> str:
        coverage = result.get("coverage")
        status = str(result.get("status") or "unknown")
        if result.get("error"):
            return "partial" if coverage == "partial" else "unavailable"
        if status in {"error", "absent", "disabled", "unavailable", "missing_root"}:
            return "unavailable"
        if status in {"warming", "partial"} or result.get("scanning"):
            return "partial"
        generation_status = str(result.get("generation_status") or "").lower()
        if result.get("generation_reliable") is False or generation_status == "unreliable":
            return "partial" if coverage in {"complete", "partial"} else "unavailable"
        if (
            result.get("generation_reliable") is not True
            or generation_status != "tracked"
            or not result.get("generation")
        ):
            return "unavailable"
        if coverage in {"complete", "partial", "unavailable"}:
            return str(coverage)
        if status == "ready":
            return "complete"
        return "unavailable"

    def search_fff(need: InformationNeed, query_text: str, kind: str, op_name: str) -> tuple[str, bool]:
        nonlocal fff_status, fff_hits, fff_wall_ms
        assert root is not None
        try:
            found = _fff_search_repository(root, query_text, kind=kind, limit=5)
        except Exception as exc:
            found = {
                "status": "error",
                "coverage": "unavailable",
                "error": f"{type(exc).__name__}: {exc}",
                "path_hits": [],
                "content_hits": [],
            }
        fff_status = str(found.get("status") or "unknown")
        refs, verified_content_hits, stale_hits = _fff_evidence_refs(
            found,
            root,
            kind=kind,
            limit=5,
        )
        fff_hits += len(refs)
        fff_wall_ms += float(found.get("wall_ms") or 0.0)
        generation = str(
            found.get("generation")
            or f"{found.get('package_version', 'unknown')}:epoch={int(found.get('index_epoch') or 0)}"
        )
        if found.get("generation_reliable") is True:
            epochs[f"fff:{root}"] = generation
        coverage = provider_coverage(found)
        if stale_hits and coverage == "complete":
            coverage = "partial"
        generation_status = str(found.get("generation_status") or "unavailable")
        generation_reliable = found.get("generation_reliable") is True
        operation_errors = [str(error) for error in [found.get("error"), *stale_hits] if error]
        if not generation_reliable and not operation_errors:
            operation_errors.append("FFF source generation is not reliable")
        operation_error = "; ".join(operation_errors) if operation_errors else None
        details = {"symbol": query_text} if kind == "exact_symbol" else {}
        add_operation(
            op_name,
            need,
            status=fff_status,
            coverage=coverage,
            error=str(operation_error) if operation_error else None,
            scanning=bool(found.get("scanning", False)),
            generation=generation,
            hits=len(refs),
            reused=bool(found.get("reused")),
            index_status=found.get("status"),
            generation_status=generation_status,
            generation_reliable=generation_reliable,
            watcher_ready=found.get("watcher_ready"),
            warmup_complete=found.get("warmup_complete"),
            content_scan=found.get("content_scan"),
            **details,
        )
        evidence.extend(refs)
        for ref in refs:
            add_source_epoch(ref)
        if need.required and coverage != "complete":
            gaps.append(f"{need.id}: repository search incomplete (fff={fff_status})")
        satisfied = verified_content_hits > 0 if kind == "exact_symbol" else bool(refs)
        return fff_status, satisfied

    for need in need_list:
        satisfied = False
        if need.kind == "exact_path" or need.path:
            path_s = need.path or need.description
            orientation_blocked = canonical_repo is not None and root is None
            ref = None if orientation_blocked else _fetch_exact_path(path_s, root)
            path_coverage = "complete" if ref is not None else "unavailable"
            add_operation(
                "exact_path",
                need,
                status="blocked_by_orientation" if orientation_blocked else ("ready" if ref else "missing"),
                coverage=path_coverage,
                error="repository orientation is unresolved" if orientation_blocked else None,
                generation=ref.source_version if ref else None,
                path=path_s,
                hit=ref is not None,
            )
            if ref:
                evidence.append(ref)
                add_source_epoch(ref)
                satisfied = True
            elif need.required:
                reason = "repository orientation unresolved" if orientation_blocked else f"missing path {path_s}"
                gaps.append(f"{need.id}: {reason}")
        elif need.kind == "exact_symbol" or need.symbol:
            symbol = need.symbol or need.description
            if allow_fff and root is not None:
                fff_status, satisfied = search_fff(need, symbol, "exact_symbol", "fff_symbol")
            else:
                fff_status = "disabled" if not allow_fff else "no_project_root"
                add_operation(
                    "fff_symbol_skipped", need, status=fff_status, coverage="unavailable",
                    error="no canonical repository root is available",
                    reason="disabled" if not allow_fff else "no_project_root",
                )
            if not satisfied and need.required:
                gaps.append(
                    f"{need.id}: no repository symbol hits for {symbol!r} (fff={fff_status})"
                )
        elif need.kind == "natural_language":
            if allow_fff and root is not None:
                fff_status, satisfied = search_fff(
                    need, need.description, "natural_language", "fff_search"
                )
            elif allow_fff:
                fff_status = "no_project_root"
                add_operation(
                    "fff_search_skipped", need, status=fff_status,
                    coverage="unavailable",
                    error="no canonical repository root is available",
                    reason="no_project_root",
                    required=need.required and not allow_qmd,
                )

            # QMD is complementary rather than competing: use it after the
            # repository fast path misses (or when no repository root exists).
            if not satisfied and allow_qmd:
                ensure_qmd_status()
            if not satisfied and allow_qmd:
                if qmd_status == "ready":
                    result = _qmd_search(need.description, limit=5)
                else:
                    result = {
                        "status": qmd_status,
                        "coverage": "unavailable",
                        "hits": [],
                        "error": qmd_status_error or f"qmd status is {qmd_status}",
                    }
                hits = list(result.get("hits") or [])
                if result.get("status") == "error":
                    qmd_status = "error"
                    qmd_status_error = str(result.get("error") or "qmd search failed")
                qmd_coverage = str(result.get("coverage") or "unavailable")
                qmd_generation = epochs.get("qmd_status_sha")
                add_operation(
                    "qmd_search", need, status=str(result.get("status") or "unknown"),
                    coverage=qmd_coverage,
                    error=str(result.get("error")) if result.get("error") else None,
                    scanning=False, generation=qmd_generation,
                    hits=len(hits), qmd_status=qmd_status,
                )
                if need.required and qmd_coverage != "complete":
                    gaps.append(
                        f"{need.id}: qmd retrieval incomplete ({result.get('status') or qmd_status})"
                    )
                for i, hit in enumerate(hits):
                    if not isinstance(hit, dict):
                        continue
                    loc = str(hit.get("path") or hit.get("file") or hit.get("id") or f"hit:{i}")
                    ver = str(hit.get("version") or hit.get("score") or "unknown")
                    evidence.append(
                        EvidenceRef(
                            source_id=f"qmd:{loc}",
                            source_version=ver,
                            locator=loc,
                            trust_class="index_hit",
                            observed_at=_now_iso(),
                            excerpt=str(hit.get("snippet") or hit.get("text") or hit.get("raw_preview") or "")[:400]
                            or None,
                            note="QMD lexical/docs retrieval; evidence is not execution authority",
                        )
                    )
                    epochs[f"qmd:{loc}"] = ver
                    satisfied = True
            if not satisfied and need.required:
                gaps.append(
                    f"{need.id}: no retrieval hits for {need.description!r} "
                    f"(fff={fff_status}, qmd={qmd_status})"
                )
        elif need.kind == "memory":
            if not allow_memory:
                add_operation(
                    "memory_skipped",
                    need,
                    status="disabled",
                    coverage="unavailable",
                    error="memory recall is disabled unless explicitly requested",
                    reason="allow_memory=False by default",
                )
                if need.required:
                    gaps.append(f"{need.id}: memory recall disabled (avoid double-inject)")
            elif memory_scope is None:
                add_operation(
                    "memory_claims",
                    need,
                    status="blocked",
                    coverage="unavailable",
                    error="an explicit MemoryScope is required for memory recall",
                    reason="missing_scope",
                    authority="untrusted_evidence_only",
                )
                memory_projection_measurements.update(
                    {
                        "memory_scope": None,
                        "memory_coverage": "unavailable",
                        "memory_authority": "untrusted_evidence_only",
                    }
                )
                if need.required:
                    gaps.append(f"{need.id}: explicit memory scope is required")
            elif not isinstance(memory_scope, MemoryScope):
                add_operation(
                    "memory_claims",
                    need,
                    status="error",
                    coverage="unavailable",
                    error="memory_scope must be a MemoryScope",
                    reason="invalid_scope",
                    authority="untrusted_evidence_only",
                )
                memory_projection_measurements.update(
                    {
                        "memory_scope": None,
                        "memory_coverage": "unavailable",
                        "memory_authority": "untrusted_evidence_only",
                    }
                )
                if need.required:
                    gaps.append(f"{need.id}: invalid memory scope")
            else:
                try:
                    from .memory.claims import project_claims

                    projection = project_claims(
                        memory_scope,
                        subject=memory_subject,
                        predicate=memory_predicate,
                    )
                except Exception as exc:  # noqa: BLE001 - memory is an optional evidence provider
                    error = f"{type(exc).__name__}: {exc}"
                    add_operation(
                        "memory_claims",
                        need,
                        status="unavailable",
                        coverage="unavailable",
                        error=error,
                        reason="projection_failed",
                        scope_fingerprint=memory_scope.fingerprint(),
                        authority="untrusted_evidence_only",
                    )
                    memory_projection_measurements.update(
                        {
                            "memory_scope": memory_scope.to_dict(),
                            "memory_coverage": "unavailable",
                            "memory_error": error,
                            "memory_authority": "untrusted_evidence_only",
                        }
                    )
                    if need.required:
                        gaps.append(f"{need.id}: scoped memory projection unavailable ({type(exc).__name__})")
                else:
                    projection_measurements = projection.measurements
                    memory_snapshot_id = projection_measurements.get("memory_snapshot_id")
                    if isinstance(memory_snapshot_id, str) and memory_snapshot_id:
                        epochs["eventlog.memory_snapshot"] = memory_snapshot_id
                    coverage = str(projection_measurements.get("coverage") or "unavailable")
                    if coverage not in {"complete", "partial", "unavailable"}:
                        coverage = "unavailable"
                    source_revision = projection_measurements.get("source_revision")
                    if source_revision:
                        epochs["eventlog.scoped_claims"] = str(source_revision)
                    evidence.extend(projection.evidence)
                    contradictions.extend(projection.contradictions)
                    gaps.extend(projection.unresolved_gaps)
                    selected_claims = projection_measurements.get("selected_claims") or []
                    status = (
                        "unavailable"
                        if coverage == "unavailable"
                        else ("ready" if selected_claims else "ready_empty")
                    )
                    operation_error = None
                    if coverage == "unavailable":
                        operation_error = "; ".join(projection.unresolved_gaps) or "scoped memory ledger unavailable"
                    add_operation(
                        "memory_claims",
                        need,
                        status=status,
                        coverage=coverage,
                        error=operation_error,
                        generation=str(source_revision) if source_revision else None,
                        scope_fingerprint=memory_scope.fingerprint(),
                        selected_claim_count=len(selected_claims),
                        snapshot_id=projection_measurements.get("memory_snapshot_id"),
                        authority="untrusted_evidence_only",
                    )
                    for key in (
                        "selected_claim_ids",
                        "selected_claim_event_ids",
                        "selected_claims",
                        "claim_history",
                        "resolved_lower_trust_conflicts",
                        "included_claim_event_ids",
                        "source_reference_event_ids",
                        "memory_snapshot",
                        "memory_snapshot_id",
                        "memory_use_receipt",
                    ):
                        if key in projection_measurements:
                            memory_projection_measurements[key] = projection_measurements[key]
                    memory_projection_measurements.update(
                        {
                            "memory_scope": memory_scope.to_dict(),
                            "memory_coverage": coverage,
                            "memory_source_revision": source_revision,
                            "memory_authority": "untrusted_evidence_only",
                        }
                    )
        else:
            add_operation(
                "unsupported", need, status="error", coverage="unavailable",
                error=f"unsupported kind {need.kind}",
            )
            gaps.append(f"{need.id}: unsupported kind {need.kind}")

    required_ops = [op for op in ops if op.get("required") is True]
    required_need_ids = {need.id for need in need_list if need.required}
    covered_need_ids = {
        op.get("need") for op in required_ops if op.get("need") in required_need_ids
    }
    missing_required_needs = required_need_ids - covered_need_ids
    required_coverages = [op.get("coverage") for op in required_ops]
    if (
        missing_required_needs
        or any(value not in {"complete", "partial", "unavailable"} for value in required_coverages)
        or "unavailable" in required_coverages
    ):
        aggregate_coverage = "unavailable"
    elif "partial" in required_coverages:
        aggregate_coverage = "partial"
    else:
        aggregate_coverage = "complete"

    recipe = ResolutionRecipe(
        capability_id="context_resolve",
        request_signature=sig,
        scope_fingerprint=scope_fp,
        policy_revision=policy_revision,
        source_epochs=epochs,
        operations=tuple(ops),
        required_evidence_fields=tuple(n.id for n in need_list if n.required),
        verifier_revision="none",
        hardware_profile=os.environ.get("Z0INT_HW_PROFILE", "local"),
    )

    packet = ContextPacket(
        task_id=task_id,
        needs=need_list,
        evidence=evidence,
        contradictions=contradictions,
        unresolved_gaps=gaps,
        recipe=recipe,
        measurements={
            "wall_ms": (time.perf_counter() - t0) * 1000.0,
            "fff_status": fff_status,
            "fff_hits": fff_hits,
            "fff_wall_ms": fff_wall_ms,
            "fff_coverage": next(
                (op["coverage"] for op in reversed(ops) if op.get("op") in {"fff_symbol", "fff_search", "fff_search_skipped", "fff_symbol_skipped"}),
                "unavailable" if allow_fff else "disabled",
            ),
            "coverage": aggregate_coverage,
            "coverage_operations": [
                {
                    "op": op.get("op"),
                    "need": op.get("need"),
                    "status": op.get("status"),
                    "error": op.get("error"),
                    "scanning": op.get("scanning"),
                    "generation": op.get("generation"),
                    "coverage": op.get("coverage"),
                    "required": op.get("required"),
                }
                for op in ops
                if "coverage" in op
            ],
            "repo_orientation": orientation,
            "allow_fff": allow_fff,
            "qmd_status": qmd_status,
            "qmd_status_error": qmd_status_error,
            "qmd_bin": bool(_qmd_bin()),
            "allow_qmd": allow_qmd,
            "allow_memory": allow_memory,
            "gpu_loaded": False,
            "network_model_calls": 0,
        },
    )
    packet.measurements.update(memory_projection_measurements)
    packet.aodl_projection = project_to_aodl_fields(packet)

    if use_cache:
        save_recipe_cache(
            recipe,
            {
                "evidence_count": len(evidence),
                "gaps": gaps,
                "wall_ms": packet.measurements["wall_ms"],
            },
        )
    return packet


def needs_from_mapping(raw: dict[str, Any] | list[Any]) -> list[InformationNeed]:
    if isinstance(raw, list):
        items = raw
    else:
        items = raw.get("needs") or []
    out: list[InformationNeed] = []
    for i, item in enumerate(items):
        if isinstance(item, str):
            out.append(InformationNeed(id=f"n{i}", description=item))
            continue
        if not isinstance(item, dict):
            raise ValueError("need must be string or object")
        out.append(
            InformationNeed(
                id=str(item.get("id") or f"n{i}"),
                description=str(item.get("description") or item.get("query") or ""),
                kind=item.get("kind")
                or (
                    "exact_path"
                    if item.get("path")
                    else ("exact_symbol" if item.get("symbol") else "natural_language")
                ),  # type: ignore[arg-type]
                path=item.get("path"),
                symbol=item.get("symbol"),
                required=bool(item.get("required", True)),
            )
        )
    return out


def context_observation_event(
    *,
    packet: ContextPacket,
    source_hash_hex: str,
    revision: int,
    trace_id: str | None = None,
) -> dict[str, Any]:
    """AODL ``stateUpdate`` carrying resolved context as observation (not a new event type)."""
    if trace_id:
        tid = trace_id
    elif packet.task_id:
        tid = packet.task_id
    elif packet.recipe is not None:
        tid = packet.recipe.request_signature
    else:
        tid = "ctx"
    payload = {
        "traceId": tid,
        "kind": "context_resolve",
        "evidenceCount": len(packet.evidence),
        "unresolvedGaps": list(packet.unresolved_gaps),
        "contradictions": list(packet.contradictions),
        "recipeSignature": packet.recipe.request_signature if packet.recipe else None,
        "evidence": [e.to_dict() for e in packet.evidence],
        "measurements": dict(packet.measurements),
        # Explicit: observation is not verified task success.
        "execution_completed": False,
        "verified_success": None,
    }
    digest = hashlib.blake2b(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8"),
        digest_size=10,
    ).hexdigest()
    return {
        "eventId": f"stateUpdate-ctx-{digest}",
        "type": "stateUpdate",
        "sourceHash": source_hash_hex,
        "revision": int(revision),
        "causalParents": [],
        "payload": payload,
    }


def append_context_packet_event(
    packet: ContextPacket,
    *,
    source: str,
    project: str | None = None,
    session_id: str | None = None,
    event_log: Any | None = None,
) -> Any:
    """Persist one bounded retrieval observation in the canonical EventLog.

    This records what evidence was surfaced. It deliberately does not claim
    task completion, correctness, or action authority.
    """
    from .memory.event_log import EventLog

    log = event_log if event_log is not None else EventLog()
    payload = {
        "schema": SCHEMA,
        "task_id": packet.task_id,
        "recipe": packet.recipe.to_dict() if packet.recipe else None,
        "evidence": [ref.to_dict() for ref in packet.evidence],
        "unresolved_gaps": list(packet.unresolved_gaps),
        "contradictions": list(packet.contradictions),
        "measurements": dict(packet.measurements),
        "execution_completed": False,
        "verified_success": None,
    }
    return log.append(
        "context.resolve",
        payload,
        source=source,
        project=project,
        session_id=session_id,
    )


def attach_context_to_aodl(
    doc: dict[str, Any],
    packet: ContextPacket,
    *,
    trace_id: str | None = None,
) -> dict[str, Any]:
    """Merge a context packet into an existing AODL document without new HOTL kinds.

    - Does **not** rewrite intentGraph or provenance.sourceHash (intent stays stable).
    - Writes runtime projection under ``provenance.runtimeContext`` and
      ``constraints.context`` (open bags, not top-level intentContract).
    - Appends a ``stateUpdate`` observation to eventLog.
    - Never sets verified_success from resolve alone.
    """
    out = json.loads(json.dumps(doc))  # deep copy via json
    sh = str(out.get("provenance", {}).get("sourceHash") or "")
    if not sh:
        raise ValueError("AODL document missing provenance.sourceHash")
    rev = int(out.get("revision") or 0)
    proj = packet.aodl_projection or project_to_aodl_fields(packet)

    prov = out.setdefault("provenance", {})
    # sourceHash unchanged — context is runtime observation, not intent material
    prov["runtimeContext"] = {
        "schema": SCHEMA,
        "task_id": packet.task_id,
        "recipe_signature": packet.recipe.request_signature if packet.recipe else None,
        "attached_at": _now_iso(),
        "source_epochs": dict(packet.recipe.source_epochs) if packet.recipe else {},
    }

    cons = out.setdefault("constraints", {})
    cons["context"] = {
        "unresolved_gaps": list(packet.unresolved_gaps),
        "contradictions": list(packet.contradictions),
        "evidence_count": len(packet.evidence),
    }

    # Plan binding for the resolver as strategy (not intent node)
    plan = out.setdefault("plan", {})
    bindings = plan.setdefault("bindings", {})
    bindings["context-resolver"] = {
        "runtime": "z0int.context_resolve",
        "schema": SCHEMA,
        "implementationStage": "context_resolve",
        "allow_fff": bool(packet.measurements.get("allow_fff")),
        "allow_memory": bool(packet.measurements.get("allow_memory")),
        "gpu_loaded": False,
    }

    event = context_observation_event(
        packet=packet,
        source_hash_hex=sh,
        revision=rev,
        trace_id=trace_id or packet.task_id,
    )
    out.setdefault("eventLog", []).append(event)

    # Keep projection for consumers that read aodl_projection-style bags
    out.setdefault("observation", {})
    out["observation"]["context"] = proj
    return out
