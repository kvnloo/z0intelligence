"""Resumable z0int onboard.

Deterministic setup lives here. Agents call ``z0int onboard``; they do not
re-invent install steps from README memory.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from . import doctor, models_mgmt, paths

ONBOARD_SCHEMA = "z0int.onboard.v1"
EVOLUTION_LAB_REPO = os.environ.get(
    "EVOLUTION_LAB_REPO", "https://github.com/kvnloo/evolution-lab.git"
)
EVOLUTION_LAB_REF = os.environ.get("EVOLUTION_LAB_REF", "nightly")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_state(root: Path | None = None, *, strict: bool = False) -> dict[str, Any]:
    """Read checkpoints; execution fails closed, status inspection stays tolerant."""
    p = paths.onboard_state_path(root)
    empty = {"schema": ONBOARD_SCHEMA, "steps": {}, "updated_at": None}
    try:
        if not p.exists():
            return empty
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schema") != ONBOARD_SCHEMA:
            raise ValueError("unsupported onboarding state schema")
        if not isinstance(data.get("steps"), dict):
            raise ValueError("invalid onboarding step state")
        if any(
            not isinstance(entry, dict)
            or not isinstance(entry.get("status"), str)
            or entry.get("status") not in {"done", "warn", "error", "skipped", "would_run"}
            for entry in data["steps"].values()
        ):
            raise ValueError("invalid onboarding step status")
        return data
    except (OSError, ValueError) as exc:
        if strict:
            raise ValueError(f"Cannot read onboarding checkpoint: {exc}") from exc
        return empty


def save_state(state: dict[str, Any], root: Path | None = None) -> Path:
    """Atomically replace only the checkpoint; never run the layout step."""
    p = paths.onboard_state_path(root)
    data = dict(state, schema=ONBOARD_SCHEMA, updated_at=time.time())
    payload = json.dumps(data, indent=2) + "\n"
    p.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=p.parent,
            prefix=f".{p.name}.", suffix=".tmp", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, p)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return p


def _mark(state: dict[str, Any], step: str, status: str, detail: str | None = None) -> None:
    entry: dict[str, Any] = {"status": status, "ts": time.time()}
    if detail:
        entry["detail"] = detail
    state.setdefault("steps", {})[step] = entry


def _run(cmd: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


StepFn = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]


def step_layout(state: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    layout = paths.ensure_layout()
    _mark(state, "layout", "done", str(layout["root"]))
    return {"ok": True, "root": str(layout["root"])}


def step_doctor(state: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    rep = doctor.run_doctor()
    ctx["doctor"] = rep.to_dict()
    _mark(state, "doctor", "done" if rep.ok else "warn", f"ok={rep.ok}")
    return {"ok": rep.ok, "unresolved": rep.unresolved}


def step_models_plan(state: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    plan = models_mgmt.plan_models()
    _validate_model_plan(plan)
    ctx["models_plan"] = plan
    cfg = paths.config_path()
    cfg.parent.mkdir(parents=True, exist_ok=True)
    existing = {}
    if cfg.is_file():
        try:
            existing = json.loads(cfg.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}
    existing["models_plan"] = plan
    cfg.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    _mark(state, "models.plan", "done", plan.get("recommendation"))
    return {"ok": True, "plan": plan}


def step_models_sync(state: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    if not ctx.get("sync_models"):
        _mark(state, "models.sync", "skipped", "pass --sync-models to download resident HF weights")
        return {"ok": True, "skipped": True}
    plan = ctx["models_plan"] if "models_plan" in ctx else models_mgmt.plan_models()
    _validate_model_plan(plan)
    ctx["models_plan"] = plan
    # A retry must use the same resident plan, even after a crash in the downloader.
    if "run" in state:
        state["run"]["context"]["models_plan"] = plan
        save_state(state)
    result = models_mgmt.sync_models(plan=plan, which="resident", dry_run=bool(ctx.get("dry_run")))
    bad = [r for r in result.get("results") or [] if r.get("status") in {"error", "missing_pin"}]
    _mark(state, "models.sync", "done" if not bad else "error", json.dumps(result)[:500])
    return {"ok": not bad, "result": result}


def step_evolution_lab(state: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    if ctx.get("skip_evolution_lab"):
        _mark(state, "evolution_lab", "skipped")
        return {"ok": True, "skipped": True}
    if ctx.get("evolution_lab_path"):
        dest = Path(ctx["evolution_lab_path"])
    else:
        info = doctor.discover_evolution_lab()
        dest = Path(info["path"]) if info.get("found") and info.get("path") else _repo_root().parent / "evolution-lab"
        dest = dest.expanduser().resolve()
    if ctx.get("dry_run"):
        _mark(state, "evolution_lab", "would_run", str(dest))
        return {"ok": True, "dry_run": True, "path": str(dest)}
    ctx["evolution_lab_path"] = str(dest)
    if "run" in state:
        state["run"]["context"]["evolution_lab_path"] = str(dest)
        save_state(state)
    if not (dest / ".git").is_dir():
        dest.parent.mkdir(parents=True, exist_ok=True)
        r = _run(["git", "clone", "--branch", EVOLUTION_LAB_REF, EVOLUTION_LAB_REPO, str(dest)])
        if r.returncode != 0:
            # branch might need full clone then checkout
            r2 = _run(["git", "clone", EVOLUTION_LAB_REPO, str(dest)])
            if r2.returncode != 0:
                _mark(state, "evolution_lab", "error", r2.stderr[-400:])
                return {"ok": False, "error": r2.stderr[-400:]}
            _run(["git", "-C", str(dest), "checkout", EVOLUTION_LAB_REF])
    else:
        _run(["git", "-C", str(dest), "fetch", "origin"])
        _run(["git", "-C", str(dest), "checkout", EVOLUTION_LAB_REF])
        _run(["git", "-C", str(dest), "pull", "--ff-only", "origin", EVOLUTION_LAB_REF])
    # editable install into current env
    r = _run([sys.executable, "-m", "pip", "install", "-q", "-e", str(dest)])
    ok = r.returncode == 0
    _mark(state, "evolution_lab", "done" if ok else "error", str(dest) if ok else r.stderr[-400:])
    return {"ok": ok, "path": str(dest), "stderr": r.stderr[-400:] if not ok else None}


def step_omp_links(state: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    selected_paths = ctx.get("omp_paths")
    if selected_paths is None:
        omp = doctor.discover_omp()
        if not omp.get("omp_home"):
            _mark(state, "omp_plugins", "skipped", "OMP not installed")
            return {"ok": True, "skipped": True, "reason": "no_omp"}
        if ctx.get("dry_run"):
            _mark(state, "omp_plugins", "would_run", json.dumps(omp.get("missing_links")))
            return {"ok": True, "dry_run": True, "missing": omp.get("missing_links")}
        selected_paths = {
            "extensions_dir": str(Path(omp["extensions_dir"] or (Path.home() / ".omp" / "agent" / "extensions")).resolve()),
            "repo_extensions": str(Path(omp["repo_extensions"] or (_repo_root() / "omp-extensions")).resolve()),
        }
    if ctx.get("dry_run"):
        _mark(state, "omp_plugins", "would_run", json.dumps(selected_paths))
        return {"ok": True, "dry_run": True, "paths": selected_paths}
    ctx["omp_paths"] = selected_paths
    if "run" in state:
        state["run"]["context"]["omp_paths"] = selected_paths
        save_state(state)
    ext_dir = Path(selected_paths["extensions_dir"])
    repo_ext = Path(selected_paths["repo_extensions"])
    ext_dir.mkdir(parents=True, exist_ok=True)
    linked: list[str] = []
    for child in sorted(repo_ext.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        target = ext_dir / child.name
        if target.exists() or target.is_symlink():
            # refresh symlink if pointing elsewhere
            if target.is_symlink() and Path(os.path.realpath(target)) != child.resolve():
                target.unlink()
            else:
                linked.append(child.name)
                continue
        try:
            target.symlink_to(child.resolve(), target_is_directory=True)
            linked.append(child.name)
        except OSError as exc:
            _mark(state, "omp_plugins", "error", str(exc))
            return {"ok": False, "error": str(exc)}
    _mark(state, "omp_plugins", "done", ",".join(linked))
    return {"ok": True, "linked": linked}


def step_data_discover(state: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    data = doctor.discover_data_sources()
    ctx["data"] = data
    out = paths.home() / "sources" / "discovered.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"schema": "z0int.sources_discovered.v1", "sources": data}, indent=2) + "\n")
    _mark(state, "data.discover", "done", f"n={len(data)}")
    return {"ok": True, "sources": data, "path": str(out)}


def step_privacy(state: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    """Refuse paths that would put personal weights in the repo."""
    bad = [
        _repo_root() / "data" / "next_action" / "champion.npz",
        _repo_root() / "champion.npz",
    ]
    problems = [str(p) for p in bad if p.is_file()]
    gitignore = _repo_root() / ".gitignore"
    gi = gitignore.read_text(encoding="utf-8") if gitignore.is_file() else ""
    need = [".z0int/", "data/private/"]
    missing = [n for n in need if n not in gi]
    ok = not problems
    detail = "ok" if ok else f"personal artifacts in repo: {problems}"
    if missing:
        detail += f"; gitignore missing {missing}"
    _mark(state, "privacy", "done" if ok else "error", detail)
    return {"ok": ok, "problems": problems, "gitignore_missing": missing}


def step_write_env_hints(state: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    el = (ctx.get("doctor") or {}).get("discoveries", {}).get("evolution_lab") or doctor.discover_evolution_lab()
    hints = {
        "schema": "z0int.env_hints.v1",
        "Z0INT_HOME": str(paths.home()),
        "EVOLUTION_LAB_ROOT": el.get("path"),
        "EVOLUTION_LAB_PYTHON": sys.executable,
        "note": "Export these for OMP extensions; onboard does not mutate your shell rc.",
    }
    p = paths.home() / "config" / "env_hints.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(hints, indent=2) + "\n")
    _mark(state, "env_hints", "done", str(p))
    return {"ok": True, "path": str(p), "hints": hints}


STEPS: list[tuple[str, StepFn]] = [
    ("layout", step_layout),
    ("doctor", step_doctor),
    ("models.plan", step_models_plan),
    ("models.sync", step_models_sync),
    ("evolution_lab", step_evolution_lab),
    ("omp_plugins", step_omp_links),
    ("data.discover", step_data_discover),
    ("privacy", step_privacy),
    ("env_hints", step_write_env_hints),
]


RUN_SCHEMA = "z0int.onboard_run.v1"
STEP_EFFECTS = {
    "layout": "Create the private z0int directory tree.",
    "doctor": "Inspect local hardware, dependencies and data-source locations.",
    "models.plan": "Inspect local model availability and write the model plan.",
    "models.sync": "Download pinned resident model weights when --sync-models is set.",
    "evolution_lab": "Clone/update evolution-lab and install it into this Python environment.",
    "omp_plugins": "Create or refresh OMP extension symlinks when OMP is installed.",
    "data.discover": "Discover local data-source locations and write discovered.json.",
    "privacy": "Check that personalized weights are outside the repository.",
    "env_hints": "Write environment hints without changing shell configuration.",
}


def _select_steps(only: list[str] | None) -> list[str]:
    names = [name for name, _ in STEPS]
    if only is None:
        return names
    if not isinstance(only, list) or not only or any(
        not isinstance(name, str) or name not in names for name in only
    ):
        raise ValueError("Select one or more known steps: " + ", ".join(names))
    if len(set(only)) != len(only):
        raise ValueError("Each onboarding step may be selected only once")
    return [name for name in names if name in only]


def _validate_model_plan(plan: Any) -> None:
    if not isinstance(plan, dict) or plan.get("schema") != "z0int.models_plan.v1":
        raise ValueError("invalid saved model plan schema")
    for key in ("resident", "on_demand"):
        values = plan.get(key)
        if not isinstance(values, list) or any(
            not isinstance(value, str) or not value.strip() for value in values
        ):
            raise ValueError(f"invalid model plan {key} selection")


def _saved_run(state: dict[str, Any]) -> dict[str, Any] | None:
    if "run" not in state:
        return None
    run = state["run"]
    if not isinstance(run, dict) or run.get("schema") != RUN_SCHEMA:
        raise ValueError("unsupported onboarding run schema")
    selected = run.get("steps")
    if not isinstance(selected, list) or _select_steps(selected) != selected:
        raise ValueError("invalid saved step selection")
    options = run.get("options")
    if not isinstance(options, dict) or set(options) != {
        "force", "sync_models", "skip_evolution_lab"
    } or any(type(value) is not bool for value in options.values()):
        raise ValueError("invalid saved operation options")
    completed = run.get("completed")
    if not isinstance(completed, list) or completed != selected[:len(completed)]:
        raise ValueError("invalid completed step sequence")
    status = run.get("status")
    if not isinstance(status, str) or status not in {"running", "failed", "done"} or (
        (status == "done") != (completed == selected)
    ):
        raise ValueError("invalid saved run status")
    in_flight = run.get("in_flight")
    if in_flight is not None and (
        len(completed) == len(selected) or in_flight != selected[len(completed)]
    ):
        raise ValueError("invalid in-flight step")
    context = run.get("context")
    if not isinstance(context, dict) or set(context) - {"doctor", "models_plan", "evolution_lab_path", "omp_paths"}:
        raise ValueError("invalid saved operation context")
    if any(not isinstance(context[key], dict) for key in ("doctor", "models_plan") if key in context):
        raise ValueError("invalid saved model or doctor context")
    if "models_plan" in context:
        _validate_model_plan(context["models_plan"])
    omp_paths = context.get("omp_paths")
    if "omp_paths" in context and (
        not isinstance(omp_paths, dict)
        or set(omp_paths) != {"extensions_dir", "repo_extensions"}
        or any(not isinstance(value, str) or not Path(value).is_absolute() for value in omp_paths.values())
    ):
        raise ValueError("invalid saved OMP paths")
    destination = context.get("evolution_lab_path")
    if "evolution_lab_path" in context and (not isinstance(destination, str) or not Path(destination).is_absolute()):
        raise ValueError("invalid saved evolution-lab destination")
    if not isinstance(run.get("inputs"), dict):
        raise ValueError("invalid saved operation inputs")
    return run


def _execution_inputs(selected: list[str], options: dict[str, bool]) -> dict[str, Any]:
    """Bind resumable operations to their source and effective configuration."""
    inputs: dict[str, Any] = {
        "repo": str(_repo_root()), "home": str(paths.home()),
        "user_home": str(Path.home().resolve()), "python": sys.executable,
    }
    if "evolution_lab" in selected and not options["skip_evolution_lab"]:
        inputs["evolution_lab"] = {
            "repo": EVOLUTION_LAB_REPO,
            "ref": EVOLUTION_LAB_REF,
            "root": os.environ.get("EVOLUTION_LAB_ROOT"),
            "dir": os.environ.get("EVOLUTION_LAB_DIR"),
        }
    if "models.sync" in selected and options["sync_models"]:
        manifest = json.dumps(models_mgmt.load_manifest(), sort_keys=True).encode("utf-8")
        inputs["models_manifest_sha256"] = hashlib.sha256(manifest).hexdigest()
    return inputs


def _skip_reason(
    name: str, state: dict[str, Any], options: dict[str, bool], completed: list[str],
) -> str | None:
    if name in completed:
        return "completed_in_run"
    previous = state["steps"].get(name, {})
    if not options["force"] and previous.get("status") == "done" and name != "doctor":
        return "already_done"
    return None


def run_onboard(
    *,
    auto: bool = True,
    force: bool = False,
    dry_run: bool = False,
    sync_models: bool = False,
    skip_evolution_lab: bool = False,
    only: list[str] | None = None,
    resume: bool = False,
) -> dict[str, Any]:
    """Execute a validated plan, or inspect it without running any operation.

    Resume restores the saved selection and options. It is explicit consent to
    retry an interrupted operation; external effects are not exactly-once.
    """
    report: dict[str, Any] = {
        "schema": "z0int.onboard_report.v1",
        "ok": False, "dry_run": dry_run, "resume": resume,
        "selected": [], "options": {}, "completed": [], "pending": [],
        "failed": [], "results": {}, "state_path": None, "next": [],
    }

    def fail(code: str, message: str) -> dict[str, Any]:
        report.update(ok=False, error_code=code, error=message)
        return report

    try:
        selected = _select_steps(only)
    except ValueError as exc:
        return fail("invalid_steps", str(exc))
    report["state_path"] = str(paths.onboard_state_path())
    try:
        state = load_state(strict=True)
        saved = _saved_run(state)
    except ValueError as exc:
        return fail("invalid_state", str(exc))

    options = {
        "force": force,
        "sync_models": sync_models,
        "skip_evolution_lab": skip_evolution_lab or os.environ.get("SKIP_EVOLUTION_LAB") == "1",
    }
    if resume:
        if saved is None:
            return fail("no_saved_run", "No saved run to resume; select steps for a new run.")
        if only is not None and selected != saved["steps"]:
            return fail("resume_mismatch", "Resume cannot change the saved step selection.")
        requested = {"force": force, "sync_models": sync_models, "skip_evolution_lab": skip_evolution_lab}
        if any(value and not saved["options"][key] for key, value in requested.items()):
            return fail("resume_mismatch", "Resume cannot change the saved operation options.")
        if (
            "evolution_lab" in saved["steps"]
            and os.environ.get("SKIP_EVOLUTION_LAB") == "1"
            and not saved["options"]["skip_evolution_lab"]
        ):
            return fail("resume_mismatch", "Evolution-lab is now disabled by the environment; no steps were run.")
        selected = list(saved["steps"])
        options = dict(saved["options"])
    elif saved is not None and saved["status"] != "done":
        report["next"] = ["z0int onboard --resume --dry-run --json", "z0int onboard --resume --json"]
        return fail("resume_required", "An unfinished run exists; use --resume to keep its approved plan.")

    run = saved if resume else {
        "schema": RUN_SCHEMA, "steps": selected, "options": options,
        "context": {}, "completed": [], "in_flight": None, "status": "running", "inputs": {},
    }
    completed = run["completed"]
    report.update(
        selected=list(selected), options=dict(options),
        completed=list(completed), pending=[name for name in selected if name not in completed],
    )
    results = report["results"]

    if dry_run:
        # This boundary is before every operation, discovery probe and write.
        for name in selected:
            reason = _skip_reason(name, state, options, completed)
            if reason is None and name == "models.sync" and not options["sync_models"]:
                reason = "sync_models_not_requested"
            if reason is None and name == "evolution_lab" and options["skip_evolution_lab"]:
                reason = "skip_evolution_lab"
            results[name] = {
                "ok": True, "would_run": reason is None, "skipped": reason is not None,
                "reason": reason, "effect": STEP_EFFECTS[name],
                "network": reason is None and name in {"models.sync", "evolution_lab"},
            }
        report["ok"] = True
        return report

    try:
        inputs = _execution_inputs(selected, options)
    except Exception as exc:  # noqa: BLE001
        return fail("invalid_inputs", str(exc))
    if resume and inputs != run["inputs"]:
        return fail("resume_mismatch", "Operation inputs changed; restore the saved configuration before resuming.")
    run["inputs"] = inputs
    state["run"] = run
    ctx: dict[str, Any] = {"auto": auto, "dry_run": False, **options, **run["context"]}
    checkpointed = list(completed)
    try:
        # Consent is durable before the first operation, even on a fresh home.
        save_state(state)
        for name, fn in STEPS:
            if name not in selected:
                continue
            reason = _skip_reason(name, state, options, completed)
            if reason is not None:
                results[name] = {"ok": True, "skipped": True, "reason": reason}
            else:
                run.update(status="running", in_flight=name)
                save_state(state)
                try:
                    results[name] = fn(state, ctx)
                except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001
                    message = str(exc) or type(exc).__name__
                    _mark(state, name, "error", message)
                    results[name] = {"ok": False, "error": message}
                run["context"] = {
                    key: ctx[key] for key in ("doctor", "models_plan", "evolution_lab_path", "omp_paths") if key in ctx
                }
                if results[name].get("ok") is False:
                    run["status"] = "failed"
                    save_state(state)
                    report["failed"].append(name)
                    break
            if name not in completed:
                completed.append(name)
            run.update(in_flight=None, status="done" if completed == selected else "running")
            save_state(state)
            checkpointed = list(completed)
    except OSError as exc:
        # The durable in-flight checkpoint remains retryable if saving a result fails.
        report.update(completed=checkpointed, pending=[name for name in selected if name not in checkpointed])
        report["next"] = ["z0int onboard --resume --dry-run --json", "z0int onboard --resume --json"]
        return fail("checkpoint_error", str(exc))

    report.update(
        ok=not report["failed"], completed=list(completed),
        pending=[name for name in selected if name not in completed],
    )
    report["next"] = (
        ["z0int onboard --resume --json"] if report["failed"] else _next_actions(results, ctx)
    )
    return report


def _next_actions(results: dict[str, Any], ctx: dict[str, Any]) -> list[str]:
    actions: list[str] = []
    sync = results.get("models.sync") or {}
    if sync.get("skipped") and sync.get("reason") not in {"already_done", "completed_in_run"}:
        actions.append("Optional: z0int onboard --step models.sync --sync-models --json")
    if "data.discover" in results and not results["data.discover"].get("skipped"):
        data = results["data.discover"].get("sources") or {}
        if not data.get("hermes_state_db") and not data.get("episodes_next_action"):
            actions.append("Provide a harness history (Hermes state.db) then: z0int-compile")
    return actions or ["z0int status"]
