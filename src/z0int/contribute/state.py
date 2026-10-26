"""Local contributor state under ``~/.z0int/contribute/`` (spec §1.2, §1.6, §2.4).

Everything here is private to the host: directories are 0700 and files 0600.
Receipts are the only evidence of a state; :func:`derive_state` recomputes the
state from receipts on every call, so a receipt on another sha never counts and a
rebase or pull moves the state back.
"""
from __future__ import annotations

import contextvars
import datetime as _dt
import errno
import fcntl
import hashlib
import json
import os
import platform
import re
import secrets as _secrets
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .. import paths
from . import schemas

STATES = (
    "S0_bootstrap", "S1_onboarded", "S2_smoke_passed", "S3_suggested",
    "S4_claimed", "S5_working", "S6_evidenced", "S7_pr_ready",
)
LEASE_TTL_H = 72
_HANDLE_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_TAG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

DEFAULT_PREFS: dict[str, Any] = {
    "schema": "z0.contribute.prefs.v1",
    "interests": [],
    "avoid": [],
    "max_first_task_hours": 4,
    "gpu_use": "ask",
    "github_post": "ask",
    "hf_download": "ask",
    "llm": "local",
    "profile_tier": "T1",
    "login": None,
}
_PREFS_TEMPLATE = """\
# z0int contribute preferences (yours to edit). No personal values are filled in for you.
schema: z0.contribute.prefs.v1
interests: []            # closed vocab tags (.z0/vocab.yaml)
avoid: []                # tags or task ids
max_first_task_hours: 4
gpu_use: ask             # never|ask|ok
github_post: ask         # ask|never  ("ok" is not allowed: every public post needs your yes)
hf_download: ask         # ask|never
llm: local               # none|local|auto (tie-break only; grok and cursor are never used)
profile_tier: T1         # T0..T3 (spec §3.2)
login: null              # GitHub handle for credit; null = ask at PR time
"""
_PREFS_ENUMS = {
    "gpu_use": ("never", "ask", "ok"),
    "github_post": ("ask", "never"),
    "hf_download": ("ask", "never"),
    "llm": ("none", "local", "auto"),
    "profile_tier": ("T0", "T1", "T2", "T3"),
}


def utc_now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)


def iso(ts: _dt.datetime) -> str:
    return ts.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str) -> _dt.datetime:
    return _dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_dt.timezone.utc)


_HOME_OVERRIDE: contextvars.ContextVar[Path | None] = contextvars.ContextVar("z0_contribute_home", default=None)


@contextmanager
def use_home(path: Path) -> Iterator[None]:
    """Run with a different contributor home in this thread/context only (tests, multi-host simulation)."""
    token = _HOME_OVERRIDE.set(Path(path))
    try:
        yield
    finally:
        _HOME_OVERRIDE.reset(token)


def home() -> Path:
    override = _HOME_OVERRIDE.get()
    base = override if override is not None else paths.home()
    root = base / "contribute"
    for d in (base, root, root / "receipts", root / "claims", root / "pr"):
        d.mkdir(parents=True, exist_ok=True)
    for d in (root, root / "receipts", root / "claims", root / "pr"):
        os.chmod(d, 0o700)
    return root


def write_private(path: Path, text: str) -> Path:
    """Atomic write with mode 0600 (spec §3.7)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{_secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()
    return path


def write_json(path: Path, data: Any) -> Path:
    return write_private(path, json.dumps(data, indent=2, sort_keys=True) + "\n")


def read_json(path: Path) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------- prefs

def validate_prefs(raw: Any) -> tuple[dict[str, Any], list[str]]:
    """Merge with defaults; an invalid value falls back to its default with a warning."""
    prefs = dict(DEFAULT_PREFS)
    warnings: list[str] = []
    if not isinstance(raw, dict):
        return prefs, (["prefs.yaml is not a mapping; using defaults"] if raw is not None else [])
    for key, val in raw.items():
        if key not in DEFAULT_PREFS:
            warnings.append(f"prefs: unknown key {key!r} ignored")
            continue
        if key in _PREFS_ENUMS and val not in _PREFS_ENUMS[key]:
            warnings.append(f"prefs: {key}={val!r} not allowed; using {DEFAULT_PREFS[key]!r}")
            continue
        if key in ("interests", "avoid"):
            if not isinstance(val, list) or not all(isinstance(v, str) and _TAG_RE.match(v) for v in val):
                warnings.append(f"prefs: {key} must be a list of tags/task ids")
                continue
        if key == "max_first_task_hours" and not (isinstance(val, (int, float)) and 0 < val <= 40):
            warnings.append("prefs: max_first_task_hours must be in (0, 40]")
            continue
        if key == "login" and val is not None and not (isinstance(val, str) and _HANDLE_RE.match(val)):
            warnings.append("prefs: login must be a GitHub handle or null")
            continue
        prefs[key] = val
    return prefs, warnings


def load_prefs() -> tuple[dict[str, Any], list[str]]:
    path = home() / "prefs.yaml"
    if not path.exists():
        write_private(path, _PREFS_TEMPLATE)
    try:
        import yaml

        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return dict(DEFAULT_PREFS), [f"prefs.yaml unreadable ({type(exc).__name__}); using defaults"]
    return validate_prefs(raw)


def save_pref(key: str, value: Any) -> None:
    import yaml

    path = home() / "prefs.yaml"
    prefs, _ = load_prefs()
    prefs[key] = value
    prefs, _ = validate_prefs(prefs)
    write_private(path, yaml.safe_dump(prefs, sort_keys=False))


# ---------------------------------------------------------------- receipts

def output_sha256(text: str | bytes) -> str:
    data = text.encode("utf-8") if isinstance(text, str) else text
    return hashlib.sha256(data).hexdigest()


def make_receipt(*, state: str, sha: str, command: str, exit_code: int, output: str | bytes,
                 host_class: str, harness: str, result: str, task: str | None = None,
                 z0_sha: str | None = None, onboard_mode: str | None = None,
                 parts: list[dict[str, Any]] | None = None, data: dict[str, Any] | None = None,
                 now: _dt.datetime | None = None) -> dict[str, Any]:
    r: dict[str, Any] = {
        "schema": "z0.contrib.receipt.v1", "state": state, "task": task, "sha": sha, "z0_sha": z0_sha,
        "host_class": host_class, "harness": harness, "command": command, "exit": int(exit_code),
        "output_sha256": output_sha256(output), "ts": iso(now or utc_now()), "result": result,
    }
    if onboard_mode:
        r["onboard_mode"] = onboard_mode
    if parts is not None:
        r["parts"] = parts
    if data is not None:
        r["data"] = data
    return r


def write_receipt(receipt: dict[str, Any]) -> Path:
    errors = schemas.validate(receipt, "contrib.receipt")
    if errors:
        raise ValueError(f"refusing to write an invalid receipt: {errors}")
    stamp = receipt["ts"].replace("-", "").replace(":", "")
    name = f"{stamp}-{receipt['state']}-{_secrets.token_hex(3)}.json"
    return write_json(home() / "receipts" / name, receipt)


def list_receipts() -> list[tuple[Path, dict[str, Any]]]:
    out = []
    for p in sorted((home() / "receipts").glob("*.json")):
        r = read_json(p)
        if isinstance(r, dict) and r.get("schema") == "z0.contrib.receipt.v1":
            out.append((p, r))
    out.sort(key=lambda pr: (pr[1].get("ts", ""), pr[0].name))
    return out


def latest(receipts: list[dict[str, Any]], state: str, *, sha: str | None, task: str | None = None,
           result: str | None = "pass") -> dict[str, Any] | None:
    """Newest receipt for ``state`` bound to exactly ``sha`` (and ``task`` when given)."""
    for r in reversed(receipts):
        if r.get("state") != state or r.get("sha") != sha:
            continue
        if task is not None and r.get("task") != task:
            continue
        if result is not None and r.get("result") != result:
            continue
        return r
    return None


def derive_state(receipts: list[dict[str, Any]], *, repo_sha: str | None, task: str | None,
                 lease_active: bool, worktree_sha: str | None) -> str:
    """The highest state whose receipt chain holds on the current shas (pure).

    S0-S3 bind to the checkout sha. S4 needs S2 plus an active local lease. S5 needs
    the task worktree. S6 and S7 bind to the worktree HEAD, so any new commit there
    (or a rebase) drops the state back to S5.
    """
    if not repo_sha:
        return "none"
    state = "none"
    for s in ("S0_bootstrap", "S1_onboarded", "S2_smoke_passed"):
        if latest(receipts, s, sha=repo_sha) is None:
            return state
        state = s
    if task and lease_active:
        state = "S4_claimed"
        if worktree_sha is None:
            return state
        state = "S5_working"
        if latest(receipts, "S6_evidenced", sha=worktree_sha, task=task) is None:
            return state
        state = "S6_evidenced"
        if latest(receipts, "S7_pr_ready", sha=worktree_sha, task=task) is None:
            return state
        return "S7_pr_ready"
    if latest(receipts, "S3_suggested", sha=repo_sha) is not None:
        state = "S3_suggested"
    return state


# ---------------------------------------------------------------- local lease (§2.4 step 1)

def host_hash() -> str:
    return hashlib.sha256(f"z0-contrib:{platform.node()}".encode()).hexdigest()[:16]


@contextmanager
def _claims_lock() -> Iterator[None]:
    lock = home() / "claims" / ".lock"
    fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def lease_path(task: str) -> Path:
    if not _TAG_RE.match(task):
        raise ValueError(f"bad task id {task!r}")
    return home() / "claims" / f"{task}.json"


def lease_expired(lease: dict[str, Any], now: _dt.datetime | None = None) -> bool:
    try:
        start = parse_iso(lease["ts"])
        ttl = float(lease.get("ttl_h", LEASE_TTL_H))
    except (KeyError, ValueError, TypeError):
        return True
    return (now or utc_now()) >= start + _dt.timedelta(hours=ttl)


def read_lease(task: str, now: _dt.datetime | None = None) -> dict[str, Any] | None:
    lease = read_json(lease_path(task))
    if not isinstance(lease, dict) or lease_expired(lease, now):
        return None
    return lease


def take_lease(task: str, *, harness: str, now: _dt.datetime | None = None,
               ttl_h: int = LEASE_TTL_H) -> tuple[bool, dict[str, Any]]:
    """Take the local lease with flock + O_EXCL. Returns (taken, lease-on-disk)."""
    path = lease_path(task)
    now = now or utc_now()
    with _claims_lock():
        old = read_json(path)
        if isinstance(old, dict) and not lease_expired(old, now):
            return False, old
        if path.exists():
            path.unlink()
        lease = {"task": task, "host_hash": host_hash(), "harness": harness, "pid": os.getpid(),
                 "ts": iso(now), "ttl_h": ttl_h}
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except OSError as exc:  # pragma: no cover - lock makes this unreachable on one host
            if exc.errno == errno.EEXIST:
                return False, read_json(path) or {}
            raise
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(lease, fh, indent=2, sort_keys=True)
        return True, lease


def release_lease(task: str) -> bool:
    path = lease_path(task)
    with _claims_lock():
        if path.exists():
            path.unlink()
            return True
    return False


def active_leases(now: _dt.datetime | None = None) -> list[dict[str, Any]]:
    out = []
    for p in sorted((home() / "claims").glob("*.json")):
        lease = read_json(p)
        if isinstance(lease, dict) and not lease_expired(lease, now):
            out.append(lease)
    return out


def load_session() -> dict[str, Any]:
    return read_json(home() / "state.json") or {}


def save_session(data: dict[str, Any]) -> None:
    write_json(home() / "state.json", data)
