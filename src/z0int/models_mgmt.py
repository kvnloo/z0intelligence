"""Hardware-aware model planning and optional download.

Deterministic: never invent unpinned HF IDs. Sync is opt-in.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore
except ImportError:
    yaml = None  # type: ignore


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_manifest(path: Path | None = None) -> dict[str, Any]:
    root = _repo_root()
    if path is None:
        # Prefer JSON twin (no PyYAML). YAML remains human source-of-truth.
        candidates = [
            root / "manifests" / "models.z0int.json",
            root / "manifests" / "models.yaml",
            root / "manifests" / "models.json",
        ]
    else:
        candidates = [path]
    for cand in candidates:
        if not cand.is_file():
            continue
        text = cand.read_text(encoding="utf-8")
        if cand.suffix in {".yaml", ".yml"}:
            if yaml is None:
                continue
            data = yaml.safe_load(text)
            return data if isinstance(data, dict) else {}
        data = json.loads(text)
        if cand.name == "models.json" and isinstance(data.get("models"), list):
            return _legacy_json_to_manifest(data)
        return data if isinstance(data, dict) else {}
    raise FileNotFoundError("no models manifest found under manifests/")


def _legacy_json_to_manifest(raw: dict[str, Any]) -> dict[str, Any]:
    models: dict[str, Any] = {}
    for row in raw.get("models") or []:
        role = str(row.get("role") or "unknown")
        models[role] = {
            "hf": row.get("source"),
            "revision": row.get("revision"),
            "dtype": row.get("dtype"),
            "min_vram_gb": 10,
            "roles": [role],
        }
    return {"schema": "z0int.models.v1", "models": models, "policies": {}}


def detect_vram_gb() -> float | None:
    """Best-effort VRAM detection; None if no GPU. nvidia-smi memory.total is MiB."""
    if shutil.which("nvidia-smi") is None:
        return None
    try:
        out = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=memory.total",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            timeout=5,
        )
        vals = [float(x.strip()) for x in out.strip().splitlines() if x.strip()]
        if not vals:
            return None
        return max(vals) / 1024.0  # MiB → GiB
    except (subprocess.SubprocessError, ValueError, OSError):
        return None


def detect_gpu_name() -> str | None:
    if shutil.which("nvidia-smi") is None:
        return None
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            text=True,
            timeout=5,
        )
        line = out.strip().splitlines()[0].strip() if out.strip() else ""
        return line or None
    except (subprocess.SubprocessError, OSError):
        return None


def select_policy(vram_gb: float | None, policies: dict[str, Any]) -> str:
    if vram_gb is None or vram_gb <= 0:
        return "cpu_only" if "cpu_only" in policies else "twelve_gb"
    if vram_gb >= 20 and "high_vram" in policies:
        return "high_vram"
    if vram_gb >= 8 and "twelve_gb" in policies:
        return "twelve_gb"
    return "cpu_only" if "cpu_only" in policies else "twelve_gb"


def decision_roster(*, manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    """Canonical Jev/System-One decision backend roster from manifests/models.*."""
    man = manifest or load_manifest()
    roster = man.get("decision_roster") or {}
    candidates = list(roster.get("candidates") or [])
    models = man.get("models") or {}
    entries = {
        cid: {
            "family": (models.get(cid) or {}).get("family"),
            "hf": (models.get(cid) or {}).get("hf"),
            "revision": (models.get(cid) or {}).get("revision"),
            "type": (models.get(cid) or {}).get("type", "hf"),
            "platforms": (models.get(cid) or {}).get("platforms"),
            "license": (models.get(cid) or {}).get("license"),
            "commercial_use": (models.get(cid) or {}).get("commercial_use"),
            "optional": (models.get(cid) or {}).get("optional"),
            "roles": (models.get(cid) or {}).get("roles") or [],
        }
        for cid in candidates
        if cid in models
    }
    return {
        "schema": roster.get("schema") or "z0int.decision_roster.v1",
        "description": roster.get("description"),
        "candidates": candidates,
        "entries": entries,
    }


def plan_models(*, manifest: dict[str, Any] | None = None, vram_gb: float | None = None) -> dict[str, Any]:
    man = manifest or load_manifest()
    models = man.get("models") or {}
    policies = man.get("policies") or {}
    if vram_gb is None:
        vram_gb = detect_vram_gb()
    policy_name = select_policy(vram_gb, policies)
    policy = policies.get(policy_name) or {}
    resident = list(policy.get("resident") or [])
    on_demand = list(policy.get("on_demand") or [])

    def ok(mid: str) -> bool:
        m = models.get(mid) or {}
        if m.get("type") == "generated":
            return True
        need = float(m.get("min_vram_gb") or 0)
        if vram_gb is None:
            return m.get("type") == "generated"
        return need <= float(vram_gb) + 0.5

    resident = [m for m in resident if m in models and ok(m)]
    on_demand = [m for m in on_demand if m in models and ok(m)]
    never = policy.get("never_coreside") or []
    return {
        "schema": "z0int.models_plan.v1",
        "gpu_name": detect_gpu_name() if vram_gb not in (None, 0.0) or vram_gb is None else None,
        "vram_gb": vram_gb,
        "policy": policy_name,
        "resident": resident,
        "on_demand": on_demand,
        "never_coreside": never,
        "models": {
            mid: {
                "hf": (models[mid] or {}).get("hf"),
                "revision": (models[mid] or {}).get("revision"),
                "type": (models[mid] or {}).get("type", "hf"),
                "roles": (models[mid] or {}).get("roles") or [],
                "min_vram_gb": (models[mid] or {}).get("min_vram_gb"),
                "local_dir": (
                    str(managed_model_dir(mid))
                    if (models[mid] or {}).get("type") == "hf_bundle"
                    else None
                ),
                "present": model_present(mid, models[mid] or {}),
            }
            for mid in models
        },
        "recommendation": _human(policy_name, resident, on_demand, never),
    }


def _human(policy: str, resident: list[str], on_demand: list[str], never: list) -> str:
    lines = [
        f"policy={policy}",
        f"resident: {', '.join(resident) or '(none)'}",
        f"on-demand: {', '.join(on_demand) or '(none)'}",
    ]
    if never:
        lines.append(f"do not co-reside: {never}")
    return "; ".join(lines)


def model_cached(hf_id: str, revision: str | None = None) -> bool:
    """True if huggingface hub cache appears to hold the revision (best-effort)."""
    hf_home = Path.home() / ".cache" / "huggingface" / "hub"
    if not hf_home.is_dir():
        return False
    slug = "models--" + hf_id.replace("/", "--")
    root = hf_home / slug
    if not root.is_dir():
        return False
    if not revision:
        return True
    refs = root / "refs"
    if refs.is_dir():
        for p in refs.iterdir():
            try:
                if p.read_text(encoding="utf-8").strip() == revision:
                    return True
            except OSError:
                continue
    return (root / "snapshots" / revision).is_dir()


def require_cached_snapshot(hf_id: str, revision: str) -> Path:
    """Resolve an already-downloaded snapshot, or fail. **Never downloads.**

    Inventory operations must not be able to pull weights. Before this guard,
    both the `laya` and `decider` adapters resolved their model directory with
    ``snapshot_download(repo_id=..., revision=...)`` *without*
    ``local_files_only=True`` on the cache-miss path, and
    ``health(load=False)`` calls that resolver. So a plain
    ``registry.backend_status()`` — the command used to inventory the fleet —
    could start a multi-gigabyte download for any backend whose weights were not
    yet present.

    Callers that genuinely want to fetch weights opt in explicitly by setting
    ``Z0INT_ALLOW_DOWNLOAD=1``.
    """
    import os

    from huggingface_hub import snapshot_download

    if model_cached(hf_id, revision):
        return Path(snapshot_download(repo_id=hf_id, revision=revision, local_files_only=True))

    if os.environ.get("Z0INT_ALLOW_DOWNLOAD") == "1":
        return Path(snapshot_download(repo_id=hf_id, revision=revision))

    raise FileNotFoundError(
        f"{hf_id}@{revision} is not in the local cache; refusing to download during "
        f"inventory (set Z0INT_ALLOW_DOWNLOAD=1 to fetch it explicitly)"
    )




def managed_model_dir(model_id: str) -> Path:
    """Canonical managed bundle path under ~/.z0int/models/<id>/."""
    from . import paths

    return paths.home() / "models" / model_id


def bundle_complete(model_id: str, meta: dict[str, Any] | None = None) -> bool:
    """True when a managed hf_bundle has the required NanoJev-style layout."""
    meta = meta or {}
    root = managed_model_dir(model_id)
    # Prefer NanoJev validator when available; fall back to allow_patterns files.
    try:
        from z0int.backends.nanojev_runtime import validate_checkpoint_dir

        return root.is_dir() and not validate_checkpoint_dir(root)
    except Exception:
        patterns = meta.get("allow_patterns") or []
        if not root.is_dir() or not patterns:
            return False
        for pat in patterns:
            if pat.endswith("/*"):
                if not (root / pat[:-2]).is_dir():
                    return False
            elif not (root / pat).is_file():
                return False
        return True


def model_present(model_id: str, meta: dict[str, Any]) -> bool:
    """Whether weights appear available for plan/sync status."""
    mtype = meta.get("type") or "hf"
    if mtype == "generated":
        return True
    if mtype == "hf_bundle":
        return bundle_complete(model_id, meta)
    hf = meta.get("hf")
    rev = meta.get("revision")
    if not hf:
        return False
    return model_cached(str(hf), str(rev) if rev else None)

def sync_models(
    *,
    plan: dict[str, Any] | None = None,
    which: str = "resident",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Download planned HF models. which=resident|on_demand|all."""
    plan = plan or plan_models()
    man = load_manifest()
    models = man.get("models") or {}
    ids: list[str] = []
    if which in ("resident", "all"):
        ids.extend(plan.get("resident") or [])
    if which in ("on_demand", "all"):
        ids.extend(plan.get("on_demand") or [])
    seen: set[str] = set()
    ordered: list[str] = []
    for x in ids:
        if x not in seen:
            seen.add(x)
            ordered.append(x)
    results: list[dict[str, Any]] = []
    for mid in ordered:
        meta = models.get(mid) or {}
        mtype = meta.get("type") or "hf"
        if mtype == "generated":
            results.append({"id": mid, "status": "skip_generated"})
            continue
        hf = meta.get("hf")
        rev = meta.get("revision")
        if not hf or not rev:
            results.append({"id": mid, "status": "missing_pin"})
            continue
        if mtype == "hf_bundle":
            local_dir = managed_model_dir(mid)
            if bundle_complete(mid, meta):
                results.append(
                    {
                        "id": mid,
                        "status": "cached",
                        "hf": hf,
                        "revision": rev,
                        "path": str(local_dir),
                        "type": mtype,
                    }
                )
                continue
            if dry_run:
                results.append(
                    {
                        "id": mid,
                        "status": "would_download",
                        "hf": hf,
                        "revision": rev,
                        "path": str(local_dir),
                        "type": mtype,
                        "allow_patterns": list(meta.get("allow_patterns") or []),
                    }
                )
                continue
            try:
                from huggingface_hub import snapshot_download

                local_dir.mkdir(parents=True, exist_ok=True)
                path = snapshot_download(
                    repo_id=str(hf),
                    revision=str(rev),
                    local_dir=str(local_dir),
                    allow_patterns=(list(meta.get("allow_patterns")) if meta.get("allow_patterns") else None),
                )
                results.append(
                    {
                        "id": mid,
                        "status": "downloaded",
                        "hf": hf,
                        "revision": rev,
                        "path": str(path),
                        "type": mtype,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                results.append(
                    {
                        "id": mid,
                        "status": "error",
                        "hf": hf,
                        "revision": rev,
                        "error": str(exc),
                        "type": mtype,
                    }
                )
            continue
        if model_cached(str(hf), str(rev)):
            results.append({"id": mid, "status": "cached", "hf": hf, "revision": rev})
            continue
        if dry_run:
            results.append({"id": mid, "status": "would_download", "hf": hf, "revision": rev})
            continue
        try:
            from huggingface_hub import snapshot_download

            path = snapshot_download(repo_id=str(hf), revision=str(rev))
            results.append(
                {"id": mid, "status": "downloaded", "hf": hf, "revision": rev, "path": path}
            )
        except Exception as exc:  # noqa: BLE001
            results.append(
                {"id": mid, "status": "error", "hf": hf, "revision": rev, "error": str(exc)}
            )
    return {"schema": "z0int.models_sync.v1", "which": which, "dry_run": dry_run, "results": results}


# ---------------------------------------------------------------------------
# Source-of-truth reconciliation
# ---------------------------------------------------------------------------

RECONCILE_SCHEMA = "z0int.models_reconcile.v1"


def projection_path() -> Path:
    """The generated projection the runtime reads."""
    from . import paths

    return paths.home() / "config" / "z0int.json"


def manifest_digest(manifest: dict[str, Any] | None = None) -> str:
    import hashlib

    man = manifest if manifest is not None else load_manifest()
    blob = json.dumps(man, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _hf_snapshot_revisions(hf_id: str) -> list[str]:
    root = Path.home() / ".cache" / "huggingface" / "hub" / ("models--" + hf_id.replace("/", "--"))
    snaps = root / "snapshots"
    if not snaps.is_dir():
        return []
    return sorted(p.name for p in snaps.iterdir() if p.is_dir())


def _serving_json_resident() -> dict[str, Any]:
    """The generative plane's supervisor, read live. Never from a local claim.

    `serving.json` has no `resident` field by design: the supervisor is the only
    thing that can say what is loaded now. This reads that answer.
    """
    from .backends.lifecycle import generative_plane_status

    st = generative_plane_status()
    return {
        "runtime_id": st.get("runtime_id"),
        "reachable": st.get("reachable"),
        "reported_by_live_runtime": st.get("resident"),
        "config_file_declares_resident": None,  # by design: config states endpoints only
        "detail": st.get("detail"),
    }


def reconcile(*, manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    """Compare the canonical manifest against disk and against the projection.

    Three artifacts disagreed about the same models on 2026-09-22:

    * `manifests/models.z0int.json` pinned `openjev_06b` at
      `c1899de289a04d12100db370d81485cdf75e47ca`, which is what is on disk;
    * `~/.z0int/config/z0int.json` (a generated projection, written
      2026-09-18) pinned the same model at `c1899deebe5c…`, which is not on disk
      at all;
    * the same projection claimed `resident: [openjev_06b, local_mb]` while the
      manifest policy said `[nanojev_06b, local_mb]`, and the live generative
      supervisor reported `hammer2.1_3b` — a third answer, in a third id space.

    Nothing read all three, so nothing noticed. This does.
    """
    man = manifest if manifest is not None else load_manifest()
    models = man.get("models") or {}
    policies = man.get("policies") or {}

    proj: dict[str, Any] = {}
    ppath = projection_path()
    if ppath.is_file():
        try:
            proj = json.loads(ppath.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            proj = {}
    proj_models = ((proj.get("models_plan") or {}).get("models")) or {}
    proj_plan = proj.get("models_plan") or {}

    rows: list[dict[str, Any]] = []
    drift: list[dict[str, Any]] = []

    for mid in sorted(models):
        meta = models[mid] or {}
        hf = meta.get("hf")
        rev = meta.get("revision")
        # `hf_bundle` models (NanoJev) live in the managed bundle directory under
        # ~/.z0int/models, not in the HF hub cache, so a cache probe on its own
        # reports a present artifact as missing. Presence first, then the cache
        # check only for models that really are HF-cache-resident.
        on_disk = model_present(mid, meta)
        if on_disk and hf and (meta.get("type") or "hf") == "hf":
            on_disk = bool(rev) and model_cached(hf, rev)
        snaps = _hf_snapshot_revisions(hf) if hf else []
        proj_rev = (proj_models.get(mid) or {}).get("revision")

        flags: list[str] = []
        if hf and rev:
            if not on_disk:
                flags.append("manifest_revision_not_on_disk")
            elif snaps and rev not in snaps:
                flags.append("disk_holds_a_different_revision")
        if proj_rev and rev and proj_rev != rev:
            flags.append("projection_revision_differs_from_manifest")
        if proj_rev and not proj_models.get(mid, {}).get("hf"):
            pass  # local/generated artifact; revision legitimately absent

        row = {
            "model_id": mid,
            "hf": hf,
            "manifest_revision": rev,
            "projection_revision": proj_rev,
            "on_disk": on_disk,
            "disk_snapshot_revisions": snaps,
            "type": meta.get("type"),
            "roles": meta.get("roles") or [],
            "flags": flags,
        }
        rows.append(row)
        for f in flags:
            drift.append({"model_id": mid, "kind": f, "manifest": rev, "projection": proj_rev, "disk": snaps})

    # --- residency, namespaced by plane -----------------------------------
    # `resident` was ambiguous: the manifest policy meant "in-process decision
    # backend", the projection copied it, and the generative supervisor means
    # something else entirely in a different id space. Name the plane.
    # The canonical residency answer comes from applying the manifest's own
    # hardware policy — not from a union across every policy, which conflates a
    # 12 GB box with a CPU-only one and reports drift against a plan nobody runs.
    canonical_plan = plan_models(manifest=man)
    policy_resident = sorted(canonical_plan.get("resident") or [])
    # Accept the legacy key so an old projection is *reported* rather than read as
    # empty, but prefer `planned_resident`: a bare `resident` in a generated file
    # is exactly the ambiguity this reconciliation exists to remove.
    proj_resident = sorted(proj_plan.get("planned_resident") or proj_plan.get("resident") or [])
    if proj_plan and "resident" in proj_plan and "planned_resident" not in proj_plan:
        drift.append(
            {
                "model_id": None,
                "kind": "projection_states_bare_resident_without_naming_the_plane",
                "manifest": "planned_resident",
                "projection": "resident",
                "disk": None,
            }
        )
    proj_stamp = proj.get("generated_manifest_sha256")
    if proj and proj_stamp != manifest_digest(man):
        drift.append(
            {
                "model_id": None,
                "kind": "projection_is_unstamped_or_stale_against_the_manifest",
                "manifest": manifest_digest(man)[:16],
                "projection": (proj_stamp or "(no digest)")[:16],
                "disk": None,
            }
        )
    if proj_resident and proj_resident != policy_resident:
        drift.append(
            {
                "model_id": None,
                "kind": "projection_residency_differs_from_manifest_policy",
                "manifest": policy_resident,
                "projection": proj_resident,
                "disk": None,
            }
        )

    residency = {
        "generative": _serving_json_resident(),
        "decision": {
            "policy": canonical_plan.get("policy"),
            "planned_resident": policy_resident,
            "declared_in_projection": proj_resident,
            "reported_by_live_runtime": None,
            "detail": (
                "`planned_resident` is intent, not residency. The decision plane has no "
                "supervisor yet, so actual residency is whatever the in-process "
                "DecisionRuntime reports — and nothing has loaded anything, which is why "
                "`reported_by_live_runtime` is null rather than a model name."
            ),
        },
    }

    return {
        "schema": RECONCILE_SCHEMA,
        "canonical_source": "manifests/models.z0int.json",
        "canonical_manifest_sha256": manifest_digest(man),
        "projection_path": str(ppath),
        "projection_present": ppath.is_file(),
        "models": rows,
        "residency": residency,
        "drift": drift,
        "drift_count": len(drift),
        "verdict": "consistent" if not drift else "drift",
        "rule": (
            "one canonical identity per model/revision in the manifest; projections are "
            "derived and must be regenerated, never hand-edited. `resident` always names "
            "its plane and is only ever what a live runtime reports is loaded now."
        ),
    }


def write_projection(*, manifest: dict[str, Any] | None = None, path: Path | None = None) -> Path:
    """Regenerate the projection from the canonical manifest.

    Stamps the manifest digest so a stale projection is identifiable on sight
    rather than by diffing revisions by hand.
    """
    man = manifest if manifest is not None else load_manifest()
    target = path or projection_path()
    plan = dict(plan_models(manifest=man))
    # `resident` in a generated file is an intention, and an intention is not
    # residency. Rename on the way out so nothing can read this file and conclude
    # a model is loaded now. Actual residency comes from the runtime, always.
    planned_resident = list(plan.pop("resident", []) or [])
    planned_on_demand = list(plan.pop("on_demand", []) or [])
    plan["planned_resident"] = planned_resident
    plan["planned_on_demand"] = planned_on_demand
    plan["residency_note"] = (
        "intent only. Actual residency is reported by the live runtime "
        "(`z0int cognition runtime-status`), never by this file."
    )
    payload = {
        "schema": "z0int.models_plan.v1",
        "generated_from": "manifests/models.z0int.json",
        "generated_manifest_sha256": manifest_digest(man),
        "models_plan": plan,
        "models": {
            mid: {
                "hf": (meta or {}).get("hf"),
                "revision": (meta or {}).get("revision"),
                "type": (meta or {}).get("type"),
                "roles": (meta or {}).get("roles") or [],
            }
            for mid, meta in (man.get("models") or {}).items()
        },
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    return target
