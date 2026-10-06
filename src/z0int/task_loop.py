"""Authorized verified-loop task family with worktree + checkpoint resume.

Family: ``coding.bounded_worktree_patch``

Reference shape of live context-recovery:
  resolve requirements → bounded reversible patch in isolated worktree →
  independent verifier → durable checkpoint (restart-safe).

Does **not**:
  - flip z0int-bridge execution live
  - set verified_success from execution_completed alone
  - auto-merge or push
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import re
import sys
from contextlib import contextmanager
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from . import paths
from .continuation import (
    SCHEMA as CONTINUATION_SCHEMA, ContinuationCheckpoint, ContinuationContext,
    InvalidCheckpoint, PendingOperation, StaleCheckpoint, canonical_json,
    plan_restore, read_json, record_checkpoint, write_atomic,
)

FAMILY_ID = "coding.bounded_worktree_patch"
CHECKPOINT_SCHEMA = "z0int.task_checkpoint.v1"
CAPABILITY_ID = FAMILY_ID

TaskStatus = Literal[
    "authorized",
    "resolved",
    "worktree_ready",
    "patched",
    "execution_completed",
    "verified",
    "failed",
    "abandoned",
]


@dataclass
class PatchSpec:
    """Bounded, reversible file edit. Worker claim is not proof."""

    relative_path: str
    find: str
    replace: str
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Mappingish) -> "PatchSpec":
        return cls(
            relative_path=str(d["relative_path"]),
            find=str(d["find"]),
            replace=str(d["replace"]),
            description=str(d.get("description") or ""),
        )


# typing helper without importing Mapping everywhere in from_dict
Mappingish = dict[str, Any]


@dataclass
class TaskCheckpoint:
    schema: str = CHECKPOINT_SCHEMA
    task_id: str = ""
    family_id: str = FAMILY_ID
    status: TaskStatus = "authorized"
    authorized_at: float = 0.0
    updated_at: float = 0.0
    base_repo: str | None = None
    base_ref: str | None = None
    worktree_path: str | None = None
    branch: str | None = None
    requirement_paths: list[str] = field(default_factory=list)
    patch: dict[str, Any] | None = None
    context_packet_path: str | None = None
    aodl_path: str | None = None
    aodl_source_hash: str | None = None
    verifier_kind: str = "content_predicate"
    execution_completed: bool | None = None
    verified_success: bool | None = None
    last_error: str | None = None
    measurements: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    pending_patch: dict[str, str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TaskCheckpoint":
        if type(d) is not dict or set(d) - set(cls.__dataclass_fields__):
            raise InvalidCheckpoint("unknown task checkpoint fields")
        cp = cls(**d)
        _validate_task(cp)
        return cp


_NEXT = {
    "authorized": "resolve", "resolved": "worktree", "worktree_ready": "apply_patch",
    "patched": "verify", "execution_completed": "verify", "verified": "stop",
    "failed": "stop", "abandoned": "stop",
}
_TERMINAL = {"verified", "failed", "abandoned"}


def _validate_task(cp: TaskCheckpoint) -> None:
    if (cp.schema != CHECKPOINT_SCHEMA or cp.family_id != FAMILY_ID
            or type(cp.status) is not str or cp.status not in _NEXT):
        raise InvalidCheckpoint("unsupported task schema, family or status")
    if type(cp.task_id) is not str or not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", cp.task_id):
        raise InvalidCheckpoint("task_id must be a safe filename component")
    for flag in (cp.execution_completed, cp.verified_success):
        if flag is not None and type(flag) is not bool:
            raise InvalidCheckpoint("task outcome must be boolean or null")
    if cp.pending_patch is not None:
        op = cp.pending_patch
        if (type(op) is not dict or set(op) != {"status", "before", "after"}
                or type(op["status"]) is not str
                or op["status"] not in {"started", "unknown", "completed"}
                or any(type(op[k]) is not str or not re.fullmatch(r"[0-9a-f]{64}", op[k])
                       for k in ("before", "after"))):
            raise InvalidCheckpoint("invalid pending patch")
    canonical_json(cp.to_dict())


def _source_revisions(cp: TaskCheckpoint) -> dict[str, str]:
    revisions = {}
    for name in ("aodl_path", "context_packet_path"):
        value = getattr(cp, name)
        if value is not None:
            try:
                revisions[name] = hashlib.sha256(Path(value).read_bytes()).hexdigest()
            except OSError as exc:
                raise StaleCheckpoint(f"required task source unavailable: {name}") from exc
    return revisions


def _context(cp: TaskCheckpoint) -> ContinuationContext:
    return ContinuationContext(
        runtime="z0int.task_loop", runtime_revision=f"python:{sys.version_info.major}.{sys.version_info.minor}",
        code_revision=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        policy_revision=cp.aodl_source_hash or CHECKPOINT_SCHEMA,
        authority_ref=cp.aodl_path or f"task:{cp.task_id}", scope=cp.base_repo or "local:task",
        trace_id=cp.task_id, work_item_id=cp.task_id, attempt_lineage_id=cp.task_id,
    )


def task_continuation(cp: TaskCheckpoint) -> ContinuationCheckpoint:
    """Export the existing task state, not a new scheduler or authorization."""
    _validate_task(cp)
    pending = ()
    if cp.pending_patch:
        op = cp.pending_patch
        pending = (PendingOperation(
            f"{cp.task_id}:patch", "bounded_file_patch", op["status"], "readback",
            durable_ref=cp.worktree_path,
            result_ref=f"sha256:{op['after']}" if op["status"] == "completed" else None,
        ),)
    verifier_ref = cp.measurements.get("verifier_ref")
    return ContinuationCheckpoint.build(
        continuation_id=cp.task_id, context=_context(cp), phase=cp.status,
        resume_entrypoint=_NEXT[cp.status], state=cp.to_dict(),
        source_revisions=_source_revisions(cp),
        durable_refs={k: v for k in ("base_repo", "base_ref", "worktree_path", "aodl_path")
                      if (v := getattr(cp, k)) is not None},
        pending=pending, terminal=cp.status in _TERMINAL,
        execution_completed=cp.execution_completed,
        # Legacy true flags without verifier evidence remain unverified in the new contract.
        verified_success=cp.verified_success if cp.verified_success is not True or verifier_ref else None,
        verifier_ref=verifier_ref,
    )


def record_task_checkpoint(task_id: str, log: Any) -> Any:
    """Opt-in admission into the existing EventLog; never changes task scheduling."""
    return record_checkpoint(log, task_continuation(load_checkpoint(task_id)))


def _tasks_dir() -> Path:
    d = paths.home() / "state" / "tasks"
    d.mkdir(parents=True, exist_ok=True)
    return d


def checkpoint_path(task_id: str) -> Path:
    _validate_task(TaskCheckpoint(task_id=task_id))
    return _tasks_dir() / f"{task_id}.json"


def save_checkpoint(cp: TaskCheckpoint) -> Path:
    cp.updated_at = time.time()
    continuation = task_continuation(cp)
    # Keep legacy top-level fields readable without duplicating the task state.
    metadata = continuation.body
    metadata.pop("state")
    row = cp.to_dict()
    row["_continuation"] = {
        "schema": CONTINUATION_SCHEMA, "checkpoint_id": continuation.checkpoint_id,
        "metadata": metadata,
    }
    path = checkpoint_path(cp.task_id)
    write_atomic(path, row)
    return path


def load_checkpoint(task_id: str) -> TaskCheckpoint:
    path = checkpoint_path(task_id)
    row = read_json(path)
    if type(row) is not dict:
        raise InvalidCheckpoint("task checkpoint must be an object")
    sealed = row.pop("_continuation", None)
    cp = TaskCheckpoint.from_dict(row)
    if cp.task_id != task_id:
        raise InvalidCheckpoint("task identity mismatch")
    if sealed is not None:
        if (type(sealed) is not dict or set(sealed) != {"schema", "checkpoint_id", "metadata"}
                or type(sealed["metadata"]) is not dict or "state" in sealed["metadata"]):
            raise InvalidCheckpoint("invalid task continuation metadata")
        generic = ContinuationCheckpoint.from_dict({
            "schema": sealed["schema"], "checkpoint_id": sealed["checkpoint_id"],
            "payload": canonical_json({**sealed["metadata"], "state": row}),
        })
        plan_restore(generic, context=_context(cp), source_revisions=_source_revisions(cp),
                     allowed_entrypoints=set(_NEXT.values()))
        if generic.checkpoint_id != task_continuation(cp).checkpoint_id:
            raise InvalidCheckpoint("task state and continuation metadata disagree")
    # Unsealed v1 files remain readable; next save migrates them. They have no integrity proof.
    return cp


def list_checkpoints() -> list[dict[str, Any]]:
    out = []
    for p in sorted(_tasks_dir().glob("*.json")):
        try:
            d = load_checkpoint(p.stem).to_dict()
        except (OSError, ValueError):
            continue
        out.append({
            "task_id": d.get("task_id"),
            "status": d.get("status"),
            "family_id": d.get("family_id"),
            "verified_success": d.get("verified_success"),
            "execution_completed": d.get("execution_completed"),
            "path": str(p),
        })
    return out


def _run_git(args: list[str], *, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        check=check,
    )


def _family_cascade() -> Any:
    from .cascade import CascadePolicy, StagePolicy

    # Shadow-capable cascade for the reference family (not a live bridge flip).
    return CascadePolicy(
        capability_id=CAPABILITY_ID,
        stages=(
            StagePolicy("context_resolve", 0.0),
            StagePolicy("worktree_patch", 0.0),
            StagePolicy("independent_verifier", 0.0, terminal=True),
        ),
        final_stage="independent_verifier",
        min_success_rate=0.99,
        max_success_regression=0.01,
        objective="premium_tokens_per_verified_success",
        status="promoted",
    )


def compile_family_aodl() -> dict[str, Any]:
    from .aodl import AodlBindingConfig, AodlBudgets, compile_aodl

    return compile_aodl(
        capability_id=CAPABILITY_ID,
        cascade=_family_cascade(),
        routines=(),
        config=AodlBindingConfig(
            harness_id="omp",
            executor_id="z0int-runtime",
            verifier_id="independent-content-verifier",
            receipt_store_id="decision-receipts",
            include_routine_slot=False,
            budgets=AodlBudgets(tokens=8000, premium_tokens=0, attention=1),
            precision_floor=0.95,
            source="z0int.task_loop",
        ),
    )


def authorize_task(
    *,
    base_repo: Path | str,
    patch: PatchSpec,
    requirement_paths: list[str] | None = None,
    task_id: str | None = None,
    base_ref: str | None = None,
) -> TaskCheckpoint:
    """Create an authorized checkpoint. Does not execute."""
    repo = Path(base_repo).expanduser().resolve()
    if not (repo / ".git").exists() and not (repo / ".git").is_file():
        # allow bare or worktree
        if not (repo / ".git").exists():
            raise ValueError(f"base_repo is not a git repo: {repo}")
    tid = task_id or f"twp-{uuid.uuid4().hex[:12]}"
    ref = base_ref
    try:
        ref = _run_git(["rev-parse", "--verify", f"{base_ref or 'HEAD'}^{{commit}}"], cwd=repo).stdout.strip()
    except subprocess.CalledProcessError as exc:
        raise ValueError(f"cannot resolve base ref in {repo}: {exc.stderr}") from exc
    cp = TaskCheckpoint(
        task_id=tid,
        family_id=FAMILY_ID,
        status="authorized",
        authorized_at=time.time(),
        updated_at=time.time(),
        base_repo=str(repo),
        base_ref=ref,
        requirement_paths=list(requirement_paths or []),
        patch=patch.to_dict(),
        notes=["authorized; not executed; verified_success=null"],
    )
    save_checkpoint(cp)
    return cp


def step_resolve(cp: TaskCheckpoint, *, allow_qmd: bool = False) -> TaskCheckpoint:
    from .aodl import assert_basic_aodl_invariants
    from .context_resolve import InformationNeed, attach_context_to_aodl, resolve_context

    if cp.status not in {"authorized", "resolved", "failed"}:
        # allow re-resolve from authorized/resolved
        if cp.status not in {"authorized", "resolved"}:
            raise ValueError(f"cannot resolve from status={cp.status}")
    t0 = time.perf_counter()
    needs: list[InformationNeed] = []
    root = Path(cp.base_repo) if cp.base_repo else None
    for i, path in enumerate(cp.requirement_paths):
        needs.append(InformationNeed(id=f"req{i}", description=path, kind="exact_path", path=path))
    if cp.patch and cp.patch.get("relative_path"):
        needs.append(
            InformationNeed(
                id="target",
                description=str(cp.patch["relative_path"]),
                kind="exact_path",
                path=str(cp.patch["relative_path"]),
            )
        )
    if not needs:
        needs.append(
            InformationNeed(
                id="family",
                description=f"authorized family {FAMILY_ID}",
                kind="natural_language",
            )
        )
    packet = resolve_context(
        needs=needs,
        task_id=cp.task_id,
        project_root=root,
        allow_qmd=allow_qmd,
        allow_memory=False,
        use_cache=True,
    )
    task_dir = _tasks_dir() / cp.task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    pkt_path = task_dir / "context_packet.json"
    pkt_path.write_text(json.dumps(packet.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    doc = compile_family_aodl()
    assert_basic_aodl_invariants(doc)
    doc = attach_context_to_aodl(doc, packet, trace_id=cp.task_id)
    assert_basic_aodl_invariants(doc)
    # intent sourceHash must survive attach
    aodl_path = task_dir / "aodl.json"
    aodl_path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    cp.context_packet_path = str(pkt_path)
    cp.aodl_path = str(aodl_path)
    cp.aodl_source_hash = str(doc["provenance"]["sourceHash"])
    cp.status = "resolved"
    cp.measurements["resolve_wall_ms"] = (time.perf_counter() - t0) * 1000.0
    cp.measurements["unresolved_gaps"] = list(packet.unresolved_gaps)
    cp.notes.append(f"resolved; gaps={len(packet.unresolved_gaps)}; evidence={len(packet.evidence)}")
    save_checkpoint(cp)
    return cp


def step_worktree(cp: TaskCheckpoint, *, worktrees_root: Path | str | None = None) -> TaskCheckpoint:
    if cp.status not in {"resolved", "worktree_ready", "patched", "failed"}:
        if cp.status != "resolved" and cp.status != "worktree_ready":
            raise ValueError(f"need resolved before worktree; got {cp.status}")
    if not cp.base_repo or not cp.base_ref:
        raise ValueError("checkpoint missing base_repo/base_ref")
    repo = Path(cp.base_repo)
    root = Path(worktrees_root) if worktrees_root else (paths.home() / "state" / "worktrees")
    root.mkdir(parents=True, exist_ok=True)
    branch = cp.branch or f"z0int/{cp.task_id}"
    wt = Path(cp.worktree_path) if cp.worktree_path else (root / cp.task_id)
    if wt.exists() and (wt / ".git").exists():
        cp.worktree_path = str(wt)
        cp.branch = branch
        cp.status = "worktree_ready"
        cp.notes.append("worktree already present; resumed")
        save_checkpoint(cp)
        return cp
    if wt.exists():
        raise ValueError(f"worktree path exists but is not a git worktree: {wt}")
    # create branch from base_ref without checking out main
    _run_git(["branch", branch, cp.base_ref], cwd=repo, check=False)  # may already exist
    try:
        _run_git(["worktree", "add", str(wt), branch], cwd=repo, check=True)
    except subprocess.CalledProcessError as exc:
        # try force path if branch exists elsewhere
        cp.last_error = (exc.stderr or exc.stdout or str(exc))[:500]
        cp.status = "failed"
        save_checkpoint(cp)
        raise
    cp.worktree_path = str(wt)
    cp.branch = branch
    cp.status = "worktree_ready"
    cp.notes.append(f"worktree ready at {wt}")
    save_checkpoint(cp)
    return cp


def _patch_target(cp: TaskCheckpoint) -> Path:
    if not cp.worktree_path or not cp.patch or not cp.base_repo:
        raise InvalidCheckpoint("missing isolated worktree or patch")
    wt = Path(cp.worktree_path).resolve()
    relative = Path(cp.patch["relative_path"])
    target = (wt / relative).resolve()
    if (relative.is_absolute() or ".." in relative.parts or not relative.parts
            or wt == Path(cp.base_repo).resolve() or not target.is_relative_to(wt)):
        raise InvalidCheckpoint("patch target escapes the isolated worktree")
    return target


def _reconcile_patch(cp: TaskCheckpoint) -> bool:
    """Under the task owner's lock, reconcile this ONE bounded file edit.

    Never infer arbitrary tool success from a transcript or retry an unknown
    write. Only exact before/after byte hashes permit this local recovery.
    Returns true when the after-image is already present.
    """
    if not cp.pending_patch:
        return False
    op = cp.pending_patch
    target = _patch_target(cp)
    if op["status"] == "started":
        op["status"] = "unknown"
        save_checkpoint(cp)
    try:
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
    except OSError as exc:
        raise InvalidCheckpoint("pending patch requires readback reconciliation") from exc
    if actual == op["after"]:
        op["status"] = "completed"
        cp.status = "patched"
        cp.execution_completed = True
        cp.verified_success = None
        cp.notes.append("after-image reconciled; no patch replay; git commit not inferred")
        save_checkpoint(cp)
        return True
    if actual == op["before"] and op["status"] != "completed":
        cp.pending_patch = None
        save_checkpoint(cp)
        return False
    raise InvalidCheckpoint("ambiguous patch outcome; manual reconciliation required")


def step_apply_patch(cp: TaskCheckpoint) -> TaskCheckpoint:
    if cp.status not in {"worktree_ready", "patched", "execution_completed"}:
        raise ValueError(f"need worktree_ready before patch; got {cp.status}")
    if not cp.worktree_path or not cp.patch:
        raise ValueError("missing worktree or patch")
    wt = Path(cp.worktree_path)
    spec = PatchSpec.from_dict(cp.patch)
    target = _patch_target(cp)
    if _reconcile_patch(cp):
        return cp
    if not target.is_file():
        cp.status = "failed"
        cp.last_error = f"target missing: {target}"
        cp.execution_completed = False
        save_checkpoint(cp)
        raise FileNotFoundError(cp.last_error)
    before_bytes = target.read_bytes()
    text = before_bytes.decode("utf-8")
    if spec.find not in text:
        cp.status = "failed"
        cp.last_error = "patch find-string not present (not applied)"
        cp.execution_completed = False
        save_checkpoint(cp)
        raise ValueError(cp.last_error)
    if text.count(spec.find) != 1:
        cp.status = "failed"
        cp.last_error = f"patch find-string not unique (count={text.count(spec.find)})"
        cp.execution_completed = False
        save_checkpoint(cp)
        raise ValueError(cp.last_error)
    new_text = text.replace(spec.find, spec.replace, 1)
    cp.pending_patch = {
        "status": "started", "before": hashlib.sha256(before_bytes).hexdigest(),
        "after": hashlib.sha256(new_text.encode("utf-8")).hexdigest(),
    }
    save_checkpoint(cp)  # Intent MUST be durable before touching the target.
    target.write_bytes(new_text.encode("utf-8"))
    # commit inside worktree for durability (still no merge)
    try:
        _run_git(["add", "--", spec.relative_path], cwd=wt)
        _run_git(
            ["-c", "user.email=z0int@local", "-c", "user.name=z0int-task-loop",
             "commit", "-m", f"z0int task {cp.task_id}: bounded patch"],
            cwd=wt,
        )
    except subprocess.CalledProcessError as exc:
        cp.last_error = f"commit failed: {(exc.stderr or '')[:300]}"
        # still mark patched on disk
    cp.status = "patched"
    cp.execution_completed = True  # worker finished applying; NOT verified
    cp.verified_success = None
    cp.pending_patch["status"] = "completed"
    cp.notes.append("patch applied; execution_completed=true; verified_success=null")
    save_checkpoint(cp)
    return cp


def step_verify(cp: TaskCheckpoint) -> TaskCheckpoint:
    """Independent verifier: content predicate on worktree, not worker claim."""
    if not cp.worktree_path or not cp.patch:
        raise ValueError("missing worktree or patch for verify")
    if cp.status not in {"patched", "execution_completed", "verified", "failed"}:
        # allow verify after patch only
        if cp.status != "patched" and cp.execution_completed is not True:
            raise ValueError(f"cannot verify from status={cp.status}")
    wt = Path(cp.worktree_path)
    spec = PatchSpec.from_dict(cp.patch)
    target = _patch_target(cp)
    ok = False
    detail = ""
    try:
        text = target.read_text(encoding="utf-8")
        if spec.replace in text and spec.find not in text:
            ok = True
            detail = "replace present and find absent"
        else:
            detail = "predicate failed: replace missing or find still present"
    except OSError as exc:
        detail = f"read failed: {exc}"
    # Also ensure base repo still has OLD content (isolation / no silent main edit)
    base_ok = True
    if cp.base_repo:
        base_target = Path(cp.base_repo) / spec.relative_path
        if base_target.is_file():
            base_text = base_target.read_text(encoding="utf-8")
            if spec.replace in base_text and spec.find not in base_text:
                # main already has replace — may be ok if same checkout; flag
                if Path(cp.base_repo).resolve() == wt.resolve():
                    base_ok = False
                    detail += "; base==worktree (not isolated)"
    cp.execution_completed = True
    cp.verified_success = bool(ok and base_ok)
    cp.status = "verified" if cp.verified_success else "failed"
    cp.measurements["verify_detail"] = detail
    cp.measurements.pop("verifier_ref", None)
    if ok and base_ok:
        cp.measurements["verifier_ref"] = "content_predicate:sha256:" + hashlib.sha256(
            target.read_bytes()
        ).hexdigest()
    cp.notes.append(f"verify {cp.verified_success}: {detail}")
    if not cp.verified_success:
        cp.last_error = detail
    save_checkpoint(cp)
    return cp


def _run_until(
    cp: TaskCheckpoint,
    *,
    until: TaskStatus = "verified",
    allow_qmd: bool = False,
    worktrees_root: Path | str | None = None,
) -> TaskCheckpoint:
    """Advance checkpoint through the pipeline until status or failure."""
    order = ["authorized", "resolved", "worktree_ready", "patched", "verified"]
    # map status to next step
    while True:
        if cp.status == "failed":
            return cp
        if cp.status == until or (
            until == "verified" and cp.status == "verified"
        ):
            return cp
        if cp.status == "authorized":
            cp = step_resolve(cp, allow_qmd=allow_qmd)
            if until == "resolved":
                return cp
            continue
        if cp.status == "resolved":
            cp = step_worktree(cp, worktrees_root=worktrees_root)
            if until == "worktree_ready":
                return cp
            continue
        if cp.status == "worktree_ready":
            cp = step_apply_patch(cp)
            if until == "patched":
                return cp
            continue
        if cp.status == "patched" or cp.status == "execution_completed":
            cp = step_verify(cp)
            return cp
        # verified / abandoned
        return cp


@contextmanager
def _task_owner(task_id: str):
    # This is the existing local task runtime's ownership, not an OMP scheduler.
    if os.name != "posix":
        raise RuntimeError("task resume ownership currently requires POSIX flock")
    import fcntl

    lock_path = checkpoint_path(task_id).with_suffix(".lock")
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def run_until(
    cp: TaskCheckpoint, *, until: TaskStatus = "verified", allow_qmd: bool = False,
    worktrees_root: Path | str | None = None,
) -> TaskCheckpoint:
    """Serialize local task owners and reject stale caller state before effects."""
    with _task_owner(cp.task_id):
        current = load_checkpoint(cp.task_id)
        if current.to_dict() != cp.to_dict():
            raise StaleCheckpoint("task changed; reload before continuing")
        return _run_until(cp, until=until, allow_qmd=allow_qmd, worktrees_root=worktrees_root)


def resume_task(task_id: str, **kwargs: Any) -> TaskCheckpoint:
    with _task_owner(task_id):
        cp = load_checkpoint(task_id)
        return _run_until(cp, **kwargs)


def make_fixture_repo(root: Path) -> tuple[Path, PatchSpec]:
    """Create a tiny git repo with a deliberate defect for the reference family."""
    root = root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    if not (root / ".git").exists():
        _run_git(["init"], cwd=root)
        _run_git(["-c", "user.email=z0int@local", "-c", "user.name=z0int", "commit", "--allow-empty", "-m", "init"], cwd=root)
    req = root / "REQUIREMENT.md"
    req.write_text(
        "# Requirement\n\nTarget must print READY not BROKEN.\nVerifier: content predicate independent of worker.\n",
        encoding="utf-8",
    )
    src = root / "app.py"
    src.write_text("STATUS = \"BROKEN\"\nprint(STATUS)\n", encoding="utf-8")
    _run_git(["add", "REQUIREMENT.md", "app.py"], cwd=root)
    _run_git(
        ["-c", "user.email=z0int@local", "-c", "user.name=z0int", "commit", "-m", "fixture broken"],
        cwd=root,
        check=False,
    )
    patch = PatchSpec(
        relative_path="app.py",
        find='STATUS = "BROKEN"',
        replace='STATUS = "READY"',
        description="context-recovery reference: flip BROKEN→READY",
    )
    return root, patch
