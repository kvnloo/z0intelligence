"""Immutable candidate artifact import (z0int.candidate_artifact.v1).

Import installs under ~/.z0int/specialists/<artifact_id>/ with status=candidate.
Import alone NEVER promotes. Production registry must point at immutable ids
and only consume promotion.status == \"promoted\".
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from . import paths

SCHEMA = "z0int.candidate_artifact.v1"
ALLOWED_RUNTIMES = frozenset(
    {
        "z0int.backend.mushroom.v1",
        "z0int.backend.nanojev.v1",
        "z0int.backend.local_plasticity.v1",
        "z0int.backend.cascade.v1",
        "z0int.backend.routine.v1",
    }
)

_AID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def specialists_root() -> Path:
    paths.ensure_layout()
    return paths.home() / "specialists"


def _artifact_id(file_hashes: list[str]) -> str:
    """Match Evolution Lab's v1 exporter: sha256 of sorted file digests."""
    return "sha256:" + hashlib.sha256("|".join(sorted(file_hashes)).encode()).hexdigest()


@contextmanager
def _open_source(base: Path, relative: str):
    """Open a regular bundle member without following any symlink component."""
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError("file path must be relative")
    parts = relative.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError("file path must stay inside the bundle")
    directory = os.open(base, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    source = None
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        source = os.fdopen(fd, "rb")
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise ValueError("bundle member must be a regular file")
        yield source
    finally:
        if source is not None:
            source.close()
        os.close(directory)


def load_manifest(path: Path | str) -> dict[str, Any]:
    p = Path(path)
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("manifest must be a JSON object")
    return data


def validate_manifest(manifest: dict[str, Any], *, base_dir: Path | None = None) -> list[str]:
    """Return list of error strings; empty = ok."""
    errs: list[str] = []
    if manifest.get("schema") != SCHEMA:
        errs.append(f"schema must be {SCHEMA}, got {manifest.get('schema')!r}")
    runtime = manifest.get("runtime")
    if not isinstance(runtime, str) or runtime not in ALLOWED_RUNTIMES:
        errs.append(f"unknown runtime: {runtime!r}")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        errs.append("files must be a non-empty list")
        return errs
    base = (base_dir or Path(".")).resolve()
    digests: list[str] = []
    names: set[str] = set()
    for entry in files:
        if not isinstance(entry, dict):
            errs.append("file entry must be object")
            continue
        rel, want = entry.get("path"), entry.get("sha256")
        if not isinstance(rel, str) or not isinstance(want, str):
            errs.append("file entry needs text path + sha256")
            continue
        want = want.lower().removeprefix("sha256:")
        if not re.fullmatch(r"[0-9a-f]{64}", want):
            errs.append(f"invalid file digest: {rel}")
            continue
        name = Path(rel).name
        if name == "manifest.json" or name in names:
            errs.append(f"installed filename collision: {rel}")
            continue
        names.add(name)
        try:
            with _open_source(base, rel) as source:
                got = hashlib.file_digest(source, "sha256").hexdigest()
        except (OSError, ValueError) as exc:
            errs.append(f"unreadable bundle member: {rel} ({type(exc).__name__})")
            continue
        digests.append(got)
        if got != want:
            errs.append(f"hash mismatch: {rel}")
    aid = manifest.get("artifact_id")
    if aid is not None and (not isinstance(aid, str) or not _AID_RE.fullmatch(aid)):
        errs.append("artifact_id must be sha256:<64hex>")
    elif aid is not None and len(digests) == len(files) and aid != _artifact_id(digests):
        errs.append("artifact_id does not match file contents")
    promo = manifest.get("promotion") or {}
    if not isinstance(promo, dict):
        errs.append("promotion must be an object")
    elif promo.get("status") == "promoted":
        errs.append("producer must not emit promotion.status=promoted")
    capability = manifest.get("capability_id")
    if not isinstance(capability, str) or not capability.strip():
        errs.append("capability_id required")
    producer = manifest.get("producer")
    if not isinstance(producer, dict) or not all(
        isinstance(producer.get(key), str) and producer[key].strip() for key in ("repo", "revision")
    ):
        errs.append("producer.repo and producer.revision required")
    for key in ("training", "claims"):
        if manifest.get(key) is not None and not isinstance(manifest[key], dict):
            errs.append(f"{key} must be an object")
    return errs


def inspect_manifest(path: Path | str) -> dict[str, Any]:
    p = Path(path).resolve()
    base = p.parent
    manifest = load_manifest(p)
    errs = validate_manifest(manifest, base_dir=base)
    return {
        "schema": "z0int.artifact_inspect.v1",
        "path": str(p),
        "ok": not errs,
        "errors": errs,
        "manifest": {
            "schema": manifest.get("schema"),
            "artifact_id": manifest.get("artifact_id"),
            "capability_id": manifest.get("capability_id"),
            "runtime": manifest.get("runtime"),
            "producer": manifest.get("producer"),
            "promotion": manifest.get("promotion"),
            "n_files": len(manifest.get("files") or []),
        },
    }


def _safe_id_dir(artifact_id: str) -> str:
    # filesystem-safe: sha256_<hex>
    return artifact_id.replace(":", "_")


def _metadata(manifest: dict[str, Any]) -> dict[str, Any]:
    files = sorted(
        ({"path": Path(entry["path"]).name, "sha256": entry["sha256"].lower().removeprefix("sha256:")}
         for entry in manifest["files"]), key=lambda entry: entry["path"],
    )
    return {
        "schema": SCHEMA,
        "artifact_id": _artifact_id([entry["sha256"] for entry in files]),
        "producer": manifest["producer"],
        "capability_id": manifest["capability_id"],
        "family": manifest.get("family"),
        "runtime": manifest["runtime"],
        "files": files,
        "training": manifest.get("training") or {},
        "claims": manifest.get("claims") or {},
    }


def import_manifest(path: Path | str) -> dict[str, Any]:
    """Publish a verified candidate atomically; an existing identity is never overwritten."""
    p = Path(path).resolve()
    base = p.parent
    try:
        manifest = load_manifest(p)
        errs = validate_manifest(manifest, base_dir=base)
    except (OSError, ValueError) as exc:
        return {"ok": False, "errors": [f"manifest unavailable ({type(exc).__name__})"]}
    if errs:
        return {"ok": False, "errors": errs}
    metadata = _metadata(manifest)
    artifact_id = metadata["artifact_id"]
    root = specialists_root()
    dest_root = root / _safe_id_dir(artifact_id)
    try:
        fd = os.open(root / ".import.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        with os.fdopen(fd, "a+b") as lock:
            if not stat.S_ISREG(os.fstat(lock.fileno()).st_mode):
                raise ValueError("artifact import lock must be a regular file")
            fcntl.flock(lock, fcntl.LOCK_EX)
            if dest_root.exists() or dest_root.is_symlink():
                if dest_root.is_symlink() or (dest_root / "manifest.json").is_symlink():
                    raise ValueError("installed artifact must not be a symlink")
                installed = load_manifest(dest_root / "manifest.json")
                # A local promotion is not a producer claim and must survive exact reimport.
                errors = validate_manifest(dict(installed, promotion={"status": "candidate"}), base_dir=dest_root)
                if (errors or installed.get("artifact_id") != artifact_id
                        or not isinstance(installed.get("promotion"), dict) or _metadata(installed) != metadata):
                    return {"ok": False, "errors": ["installed artifact identity conflicts or is corrupt"]}
                return {"ok": True, "already_installed": True, "artifact_id": artifact_id,
                        "path": str(dest_root), "promotion": installed["promotion"]}
            with tempfile.TemporaryDirectory(prefix=".import-", dir=root) as temporary:
                stage = Path(temporary)
                for entry in manifest["files"]:
                    target = stage / Path(entry["path"]).name
                    with _open_source(base, entry["path"]) as source, target.open("xb") as output:
                        shutil.copyfileobj(source, output, length=1024 * 1024)
                    with target.open("rb") as copied:
                        digest = hashlib.file_digest(copied, "sha256").hexdigest()
                    if digest != entry["sha256"].lower().removeprefix("sha256:"):
                        raise ValueError("bundle changed during import")
                now = time.time()
                installed = {
                    **metadata, "promotion": {"status": "candidate", "imported_at": now},
                    "imported_from": str(p), "imported_at": now,
                }
                (stage / "manifest.json").write_text(json.dumps(installed, indent=2) + "\n", encoding="utf-8")
                os.rename(stage, dest_root)
    except (OSError, ValueError) as exc:
        return {"ok": False, "errors": [str(exc)]}
    return {"ok": True, "already_installed": False, "artifact_id": artifact_id,
            "path": str(dest_root), "promotion": installed["promotion"]}


def list_artifacts() -> list[dict[str, Any]]:
    """Read only published identity directories, never staging or a stale index."""
    root = specialists_root()
    out: list[dict[str, Any]] = []
    for child in sorted(root.iterdir()):
        if not re.fullmatch(r"sha256_[0-9a-f]{64}", child.name):
            continue
        artifact = get_artifact(child.name.replace("_", ":", 1))
        if artifact is not None:
            out.append(artifact)
    return out


def get_artifact(artifact_id: str) -> dict[str, Any] | None:
    """Read installed metadata; runtime weight attestation is a separate consumer gate."""
    if not isinstance(artifact_id, str) or not _AID_RE.fullmatch(artifact_id):
        return None
    d = specialists_root() / _safe_id_dir(artifact_id)
    man = d / "manifest.json"
    if d.is_symlink() or man.is_symlink() or not man.is_file():
        return None
    try:
        manifest = load_manifest(man)
    except (OSError, ValueError):
        return None
    if manifest.get("schema") != SCHEMA or manifest.get("artifact_id") != artifact_id:
        return None
    return manifest
