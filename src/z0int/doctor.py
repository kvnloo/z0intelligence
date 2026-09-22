"""Environment inspection for onboarding agents and humans.

Always emits structured JSON (``--json``) so agents never parse prose.
"""

from __future__ import annotations

import importlib.util
import json
import os
import platform
import shutil
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from . import models_mgmt, paths


@dataclass
class Check:
    id: str
    ok: bool
    detail: str
    required: bool = False
    fix: str | None = None


@dataclass
class DoctorReport:
    schema: str = "z0int.doctor.v1"
    ok: bool = True
    checks: list[Check] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    hardware: dict[str, Any] = field(default_factory=dict)
    models_plan: dict[str, Any] = field(default_factory=dict)
    discoveries: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "ok": self.ok,
            "checks": [asdict(c) for c in self.checks],
            "unresolved": list(self.unresolved),
            "hardware": self.hardware,
            "models_plan": self.models_plan,
            "discoveries": self.discoveries,
        }


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _add(report: DoctorReport, check: Check) -> None:
    report.checks.append(check)
    if check.required and not check.ok:
        report.ok = False
        report.unresolved.append(f"{check.id}: {check.detail}" + (f" → {check.fix}" if check.fix else ""))


def _mod_ok(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def discover_evolution_lab() -> dict[str, Any]:
    candidates: list[Path] = []
    env = os.environ.get("EVOLUTION_LAB_ROOT") or os.environ.get("EVOLUTION_LAB_DIR")
    if env:
        candidates.append(Path(env))
    root = _repo_root()
    candidates.extend(
        [
            root.parent / "evolution-lab",
            Path.home() / "src" / "evolution-lab",
            Path.home() / "code" / "evolution-lab",
        ]
    )
    for c in candidates:
        try:
            p = c.expanduser().resolve()
        except OSError:
            continue
        if (p / "pyproject.toml").is_file() and (p / "evolution_lab").is_dir():
            import subprocess

            rev = None
            try:
                rev = subprocess.check_output(
                    ["git", "-C", str(p), "rev-parse", "--abbrev-ref", "HEAD"],
                    text=True,
                    timeout=5,
                ).strip()
            except (subprocess.SubprocessError, OSError):
                rev = None
            return {"found": True, "path": str(p), "branch": rev, "importable": _mod_ok("evolution_lab")}
    return {"found": False, "path": None, "branch": None, "importable": _mod_ok("evolution_lab")}


def discover_omp() -> dict[str, Any]:
    omp_home = Path.home() / ".omp"
    ext = omp_home / "agent" / "extensions"
    repo_ext = _repo_root() / "omp-extensions"
    linked: list[str] = []
    missing: list[str] = []
    if repo_ext.is_dir():
        for child in sorted(repo_ext.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            target = ext / child.name
            if target.exists() or target.is_symlink():
                linked.append(child.name)
            else:
                missing.append(child.name)
    return {
        "omp_home": str(omp_home) if omp_home.exists() else None,
        "extensions_dir": str(ext) if ext.exists() else None,
        "repo_extensions": str(repo_ext) if repo_ext.is_dir() else None,
        "linked": linked,
        "missing_links": missing,
    }


def discover_data_sources() -> dict[str, Any]:
    """Best-effort known locations; never opens secret-bearing files deeply."""
    hits: dict[str, Any] = {}
    hermes_candidates = [
        Path("/workspace/hermes-home/state.db"),
        Path.home() / ".hermes" / "state.db",
        Path.home() / ".hermes" / "profiles" / "chiefstaff" / "state.db",
    ]
    for p in hermes_candidates:
        if p.is_file():
            hits["hermes_state_db"] = {"path": str(p), "size_mb": round(p.stat().st_size / 1e6, 1)}
            break
    omp_hist = Path.home() / ".omp" / "agent" / "history.db"
    if omp_hist.is_file():
        hits["omp_history_db"] = {"path": str(omp_hist), "size_mb": round(omp_hist.stat().st_size / 1e6, 1)}
    codex = Path.home() / ".codex"
    if codex.is_dir():
        hits["codex_home"] = {"path": str(codex), "present": True}
    # ChatGPT export zips in Downloads
    dl = Path.home() / "Downloads"
    exports = []
    if dl.is_dir():
        for pat in ("*chatgpt*", "*ChatGPT*", "*claude*export*", "*takeout*"):
            exports.extend(str(p) for p in dl.glob(pat) if p.is_file() or p.is_dir())
    if exports:
        hits["download_exports"] = exports[:20]
    z0 = paths.home()
    ep = z0 / "episodes" / "next_action.jsonl"
    if ep.is_file():
        hits["episodes_next_action"] = {
            "path": str(ep),
            "size_mb": round(ep.stat().st_size / 1e6, 1),
            "lines_est": "present",
        }
    return hits


def discover_kerdoios() -> dict[str, Any]:
    candidates = [
        Path.home() / ".hermes" / "profiles" / "chiefstaff" / "plugins" / "kerdoios",
        Path("/workspace/hermes-home/profiles/chiefstaff/plugins/kerdoios"),
    ]
    for c in candidates:
        if (c / "kerdoios").is_dir() or (c / "pyproject.toml").is_file():
            return {"found": True, "path": str(c), "importable": _mod_ok("kerdoios")}
    return {"found": False, "path": None, "importable": _mod_ok("kerdoios")}


def run_doctor() -> DoctorReport:
    report = DoctorReport()
    vram = models_mgmt.detect_vram_gb()
    gpu = models_mgmt.detect_gpu_name()
    report.hardware = {
        "os": platform.platform(),
        "python": sys.version.split()[0],
        "executable": sys.executable,
        "gpu_name": gpu,
        "vram_gb": vram,
        "cuda_visible": os.environ.get("CUDA_VISIBLE_DEVICES"),
    }

    _add(
        report,
        Check(
            id="python",
            ok=sys.version_info >= (3, 10),
            detail=f"{sys.version.split()[0]}",
            required=True,
            fix="Use Python 3.10–3.13",
        ),
    )
    _add(
        report,
        Check(id="git", ok=shutil.which("git") is not None, detail=shutil.which("git") or "missing", required=True, fix="Install git"),
    )
    _add(
        report,
        Check(
            id="repo_root",
            ok=(_repo_root() / "pyproject.toml").is_file(),
            detail=str(_repo_root()),
            required=True,
        ),
    )
    disk = shutil.disk_usage(Path.home())
    free_gb = disk.free / (1024**3)
    _add(
        report,
        Check(
            id="disk_free",
            ok=free_gb >= 5.0,
            detail=f"{free_gb:.1f} GiB free on home",
            required=False,
            fix="Free disk space for HF caches / specialists",
        ),
    )

    for mod, req in (("torch", False), ("numpy", True), ("transformers", False), ("jax", False)):
        _add(
            report,
            Check(
                id=f"py.{mod}",
                ok=_mod_ok(mod),
                detail="importable" if _mod_ok(mod) else "missing",
                required=req,
                fix=f"pip install -e '.[test]' from repo root" if req else f"optional: install {mod}",
            ),
        )

    el = discover_evolution_lab()
    report.discoveries["evolution_lab"] = el
    _add(
        report,
        Check(
            id="evolution_lab",
            ok=bool(el.get("found") or el.get("importable")),
            detail=json.dumps({k: el.get(k) for k in ("found", "path", "branch", "importable")}),
            required=False,
            fix="z0int onboard will clone evolution-lab @ nightly if missing",
        ),
    )

    omp = discover_omp()
    report.discoveries["omp"] = omp
    _add(
        report,
        Check(
            id="omp",
            ok=omp.get("omp_home") is not None,
            detail=f"linked={omp.get('linked')} missing={omp.get('missing_links')}",
            required=False,
            fix="Install OMP or run z0int onboard to symlink omp-extensions",
        ),
    )

    kerd = discover_kerdoios()
    report.discoveries["kerdoios"] = kerd
    _add(
        report,
        Check(
            id="kerdoios",
            ok=bool(kerd.get("found") or kerd.get("importable")),
            detail=json.dumps(kerd),
            required=False,
            fix="Optional: install kerdoios for residual token allocation",
        ),
    )

    data = discover_data_sources()
    report.discoveries["data"] = data
    _add(
        report,
        Check(
            id="data_sources",
            ok=bool(data),
            detail=json.dumps({k: (v.get("path") if isinstance(v, dict) else v) for k, v in data.items()}),
            required=False,
            fix="Export harness histories or place state.db; z0int data discover",
        ),
    )

    zhome = paths.home()
    _add(
        report,
        Check(
            id="z0int_home",
            ok=True,
            detail=str(zhome),
            required=False,
            fix="Created on onboard",
        ),
    )

    try:
        plan = models_mgmt.plan_models(vram_gb=vram)
    except Exception as exc:  # noqa: BLE001
        plan = {"error": str(exc)}
    report.models_plan = plan
    _add(
        report,
        Check(
            id="models_plan",
            ok="error" not in plan,
            detail=plan.get("recommendation") if isinstance(plan, dict) else str(plan),
            required=False,
        ),
    )


    # Decision backends (filesystem only — never load GPU weights here)
    try:
        from z0int.backends.registry import backend_status

        be_rows = backend_status(load=False)
        report.discoveries["backends"] = be_rows
        for row in be_rows:
            _add(
                report,
                Check(
                    id=f"backend.{row['id']}",
                    ok=True,  # optional — missing weights is not a doctor failure
                    detail=(
                        f"configured={row.get('configured')} ready={row.get('ready')} "
                        f"loaded={row.get('loaded')} model={row.get('model')} "
                        f"checkpoint={row.get('checkpoint')} | {row.get('detail')}"
                    ),
                    required=False,
                    fix=(
                        None
                        if row.get("ready")
                        else "z0int models sync  # downloads pinned NanoJev bundle when requested"
                    ),
                ),
            )
    except Exception as exc:  # noqa: BLE001
        report.discoveries["backends"] = {"error": str(exc)}
        _add(
            report,
            Check(
                id="backends",
                ok=True,
                detail=f"backends unavailable: {type(exc).__name__}: {exc}",
                required=False,
            ),
        )

    # privacy: ensure repo has no personal champion path committed expectation
    bad = _repo_root() / "data" / "next_action" / "champion.npz"
    _add(
        report,
        Check(
            id="privacy_no_repo_champion",
            ok=not bad.is_file(),
            detail="ok" if not bad.is_file() else f"found {bad} — move under ~/.z0int/specialists",
            required=True,
            fix="Keep personalized weights only under ~/.z0int/",
        ),
    )
    return report


def format_human(report: DoctorReport) -> str:
    lines = ["z0int doctor", ""]
    hw = report.hardware
    lines.append(f"Hardware  python={hw.get('python')}  gpu={hw.get('gpu_name')}  vram_gb={hw.get('vram_gb')}")
    lines.append("")
    for c in report.checks:
        mark = "✓" if c.ok else ("✗" if c.required else "○")
        lines.append(f"{mark} {c.id:28} {c.detail}")
        if not c.ok and c.fix:
            lines.append(f"    fix: {c.fix}")
    if report.unresolved:
        lines.append("")
        lines.append("Unresolved required:")
        for u in report.unresolved:
            lines.append(f"  - {u}")
    mp = report.models_plan or {}
    if mp and "error" not in mp:
        lines.append("")
        lines.append(f"Models plan: {mp.get('recommendation')}")
    lines.append("")
    lines.append(f"Overall: {'OK' if report.ok else 'NEEDS_ATTENTION'}")
    return "\n".join(lines)
