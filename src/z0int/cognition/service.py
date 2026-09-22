"""Bring the local z0intelligence model service up and down.

The service is one HTTP supervisor (``z0int cognition serve``) that owns at most
one resident ``llama-server`` child, which in turn holds the whole GPU.

Three properties make an ad-hoc ``python -m z0int cognition serve`` hard to run
and stop safely, and they are the reason this module exists:

1. **Children are started in their own session**, so a process-group signal
   cannot reach them.  Only the supervisor's own signal handler reaps them; if
   the supervisor is killed with ``SIGKILL`` the child survives and keeps the
   card, while the port is free and everything *looks* stopped.
2. **A pid is not an identity.**  A pidfile plus ``kill(pid)`` will happily
   signal whatever unrelated process inherited the number.  Every signal sent
   here is preceded by a check of ``/proc/<pid>/cmdline``.
3. **There are two ways to want it off.**  ``unload`` gives the GPU back and
   leaves the supervisor listening (cheap to resume); ``stop`` takes the whole
   service down.  Conflating them is how a GPU stays busy "after shutdown".

So: start, stop, restart, status, unload -- and a stop that verifies rather than
assumes.
"""

from __future__ import annotations

import atexit
import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .serving import DEFAULT_GGUF_DIR, DEFAULT_LLAMA_SERVER

SCHEMA = "z0int.service.v1"
STATE_SCHEMA = "z0int.service.state.v1"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 11500
DEFAULT_CONTEXT = 4096
DEFAULT_IDLE_UNLOAD_S = 600.0

# The command-line shape that identifies *our* supervisor, and nothing else.
SUPERVISOR_TOKENS = ("cognition", "serve")


def default_state_dir() -> Path:
    return Path(os.environ.get("Z0INT_HOME") or (Path.home() / ".z0int")) / "run"


def default_state_file() -> Path:
    return default_state_dir() / "z0intelligence.json"


def default_log_file() -> Path:
    base = Path(os.environ.get("Z0INT_HOME") or (Path.home() / ".z0int"))
    return base / "logs" / "z0intelligence.log"


# --- process inspection -------------------------------------------------


@dataclass(frozen=True)
class ProcInfo:
    pid: int
    ppid: int
    argv: tuple[str, ...]

    @property
    def cmdline(self) -> str:
        return " ".join(self.argv)

    @property
    def binary(self) -> str:
        return Path(self.argv[0]).name if self.argv else ""


def read_proc(pid: int, *, proc_root: Path | str = "/proc") -> ProcInfo | None:
    """One process, or None if it is gone or unreadable."""
    base = Path(proc_root) / str(pid)
    try:
        raw = (base / "cmdline").read_bytes()
    except OSError:
        return None
    argv = tuple(part.decode("utf-8", "replace") for part in raw.split(b"\0") if part)
    if not argv:  # kernel threads and zombies
        return None
    ppid = 0
    try:
        stat = (base / "stat").read_text()
        # comm may contain spaces and parentheses; ppid is the 4th field after it.
        ppid = int(stat[stat.rindex(")") + 2 :].split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return ProcInfo(pid=pid, ppid=ppid, argv=argv)


def iter_procs(*, proc_root: Path | str = "/proc") -> Iterator[ProcInfo]:
    root = Path(proc_root)
    try:
        entries = list(root.iterdir())
    except OSError:
        return
    for entry in entries:
        if not entry.name.isdigit():
            continue
        info = read_proc(int(entry.name), proc_root=proc_root)
        if info is not None:
            yield info


def _argv_looks_like_z0int(argv: Sequence[str]) -> bool:
    joined = " ".join(argv)
    if "z0int" in joined:
        return True
    return any(Path(a).name in ("z0int", "z0int.exe") for a in argv)


def is_supervisor_argv(argv: Sequence[str], *, port: int | None = None) -> bool:
    """True only for a ``… cognition serve`` command line, optionally on ``port``.

    Deliberately strict: this predicate is the sole gate between "stop the
    service" and "signal a stranger's process".
    """
    if not _argv_looks_like_z0int(argv):
        return False
    for i, tok in enumerate(argv):
        if tok == SUPERVISOR_TOKENS[0] and i + 1 < len(argv) and argv[i + 1] == SUPERVISOR_TOKENS[1]:
            break
    else:
        return False
    if port is None:
        return True
    if "--port" in argv:
        i = list(argv).index("--port")
        if i + 1 < len(argv):
            try:
                return int(argv[i + 1]) == int(port)
            except ValueError:
                return False
    return int(port) == DEFAULT_PORT


def parse_llama_argv(argv: Sequence[str]) -> tuple[str, str | None]:
    """(binary basename, ``-m`` model path) for a llama.cpp server command line."""
    model: str | None = None
    for i, tok in enumerate(argv):
        if tok in ("-m", "--model") and i + 1 < len(argv):
            model = argv[i + 1]
            break
    return (Path(argv[0]).name if argv else "", model)


def is_alive(pid: int) -> bool:
    """True only for a *running* process.

    A zombie answers ``kill(pid, 0)`` successfully, so a bare signal-0 probe
    reports a stopped process as alive.  That is not a cosmetic difference here:
    this check decides whether SIGTERM worked and whether the GPU was released.
    """
    if pid <= 0:
        return False
    try:
        raw = (Path("/proc") / str(pid) / "stat").read_text()
        return raw[raw.rindex(")") + 2] != "Z"
    except (FileNotFoundError, ProcessLookupError):
        return False
    except (OSError, ValueError, IndexError):
        pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# Handles for processes started in-process, so a library caller (a script, a
# test) does not accumulate zombies that then read as "still running".
_SPAWNED: dict[int, subprocess.Popen] = {}


def reap_finished() -> list[int]:
    """Non-blocking reap of children this module started."""
    reaped: list[int] = []
    for pid, proc in list(_SPAWNED.items()):
        if proc.poll() is not None:
            _SPAWNED.pop(pid, None)
            reaped.append(pid)
    return reaped


def _reap_at_exit() -> None:
    for proc in list(_SPAWNED.values()):
        try:
            proc.wait(timeout=0)
        except Exception:  # noqa: BLE001 - best effort at interpreter shutdown
            pass


atexit.register(_reap_at_exit)


# --- discovery ----------------------------------------------------------


def discover_supervisors(
    *,
    port: int = DEFAULT_PORT,
    proc_root: Path | str = "/proc",
    state_file: Path | None = None,
    extra_pids: Iterable[int] = (),
) -> list[ProcInfo]:
    """Every live supervisor for ``port``, found by identity rather than by pidfile.

    Scanning is the primary source on purpose: a supervisor started under
    ``systemd-run`` or by a script never wrote our state file.
    """
    found: dict[int, ProcInfo] = {}
    for info in iter_procs(proc_root=proc_root):
        if is_supervisor_argv(info.argv, port=port):
            found[info.pid] = info
    for pid in extra_pids:
        if pid not in found:
            info = read_proc(pid, proc_root=proc_root)
            if info is not None and is_supervisor_argv(info.argv, port=port):
                found[pid] = info
    return sorted(found.values(), key=lambda p: p.pid)


def is_z0int_llama_argv(argv: Sequence[str], *, gguf_dir: Path | str = DEFAULT_GGUF_DIR) -> bool:
    """True for a llama.cpp server serving a GGUF out of our own model directory.

    The ``-m`` path test is what keeps this from claiming a stranger's
    llama-server that happens to be running on the same machine.
    """
    binary, model = parse_llama_argv(argv)
    if binary != Path(DEFAULT_LLAMA_SERVER).name and binary != "llama-server":
        return False
    if not model:
        return False
    try:
        Path(model).resolve().relative_to(Path(gguf_dir).resolve())
    except (ValueError, OSError):
        return False
    return True


def discover_llama_servers(
    *,
    gguf_dir: Path | str = DEFAULT_GGUF_DIR,
    proc_root: Path | str = "/proc",
) -> list[ProcInfo]:
    return sorted(
        (p for p in iter_procs(proc_root=proc_root) if is_z0int_llama_argv(p.argv, gguf_dir=gguf_dir)),
        key=lambda p: p.pid,
    )


def is_init_like(proc: ProcInfo) -> bool:
    """True for the process a daemon is reparented to when its parent dies.

    This matters because "orphaned" is not the same as ``ppid == 1``.  Where a
    systemd user instance is running it acts as a subreaper, so a GPU-holding
    child whose supervisor was killed ends up parented to ``systemd --user``,
    which is very much alive.  A rule that only looked for ``ppid == 1`` would
    call that process "spared" and leave the card busy.
    """
    return proc.binary in ("init", "systemd")


def classify_llama_targets(
    servers: Iterable[ProcInfo],
    *,
    stopped_pids: Iterable[int] = (),
    include_live_parents: bool = False,
    proc_root: Path | str = "/proc",
) -> tuple[list[ProcInfo], list[ProcInfo]]:
    """Split llama-servers into (safe to stop, spared).

    Safe means it has been reparented to init/a systemd instance, its parent is
    gone, or its parent is a supervisor we just stopped.  A server with a live,
    unrelated parent (a shell, a terminal) is reported but not signalled -- that
    may be someone's deliberate foreground run.
    """
    stopped = set(stopped_pids)
    targets: list[ProcInfo] = []
    spared: list[ProcInfo] = []
    for srv in servers:
        parent = read_proc(srv.ppid, proc_root=proc_root) if srv.ppid > 0 else None
        orphaned = srv.ppid <= 1 or parent is None or is_init_like(parent)
        child_of_stopped = srv.ppid in stopped
        if include_live_parents or orphaned or child_of_stopped:
            targets.append(srv)
        else:
            spared.append(srv)
    return targets, spared


# --- state file ---------------------------------------------------------


@dataclass
class ServiceState:
    pid: int
    host: str
    port: int
    started_at: float
    argv: tuple[str, ...] = ()
    log_file: str | None = None
    schema: str = STATE_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "pid": self.pid,
            "host": self.host,
            "port": self.port,
            "started_at": self.started_at,
            "argv": list(self.argv),
            "log_file": self.log_file,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ServiceState:
        return cls(
            pid=int(d["pid"]),
            host=str(d.get("host") or DEFAULT_HOST),
            port=int(d.get("port") or DEFAULT_PORT),
            started_at=float(d.get("started_at") or 0.0),
            argv=tuple(d.get("argv") or ()),
            log_file=d.get("log_file"),
        )


def read_state(path: Path | None = None) -> ServiceState | None:
    path = path or default_state_file()
    try:
        return ServiceState.from_dict(json.loads(path.read_text()))
    except (OSError, ValueError, KeyError):
        return None


def write_state(state: ServiceState, path: Path | None = None) -> Path:
    path = path or default_state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state.to_dict(), indent=2) + "\n")
    tmp.replace(path)
    return path


def clear_state(path: Path | None = None) -> bool:
    path = path or default_state_file()
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


# --- health -------------------------------------------------------------


def http_json(
    url: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    timeout: float = 2.0,
) -> dict[str, Any] | None:
    data = json.dumps(body).encode() if body is not None else None
    req = Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode() or "{}")
    except (HTTPError, URLError, OSError, ValueError, json.JSONDecodeError):
        return None


def probe_health(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, *, timeout: float = 1.5) -> dict[str, Any] | None:
    payload = http_json(f"http://{host}:{port}/health", timeout=timeout)
    if isinstance(payload, dict) and payload.get("status") == "ok":
        return payload
    return None


def gpu_compute_apps() -> list[dict[str, Any]] | None:
    """Best-effort: which pids hold GPU memory.  None when nvidia-smi is absent."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=pid,used_memory", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    apps: list[dict[str, Any]] = []
    for line in out.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        pid_s, _, mem = line.partition(",")
        try:
            apps.append({"pid": int(pid_s.strip()), "used_memory": mem.strip()})
        except ValueError:
            continue
    return apps


# --- argv ---------------------------------------------------------------


def build_supervisor_argv(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    context: int = DEFAULT_CONTEXT,
    idle_unload: float = DEFAULT_IDLE_UNLOAD_S,
    llama_server: str | None = None,
    gguf_dir: str | None = None,
    python: str | None = None,
) -> list[str]:
    argv = [
        python or sys.executable, "-m", "z0int", "cognition", "serve",
        "--host", host, "--port", str(port),
        "--context", str(context),
        "--idle-unload", str(int(idle_unload)),
    ]
    if llama_server:
        argv += ["--llama-server", llama_server]
    if gguf_dir:
        argv += ["--gguf-dir", gguf_dir]
    return argv


# --- results ------------------------------------------------------------


@dataclass
class ServiceResult:
    ok: bool
    action: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "ok": self.ok,
            "action": self.action,
            "message": self.message,
            **self.details,
        }


def _stopped_pids(targets: Iterable[ProcInfo], *, grace: float, alive=is_alive, signal_fn=os.kill) -> tuple[list[int], list[int]]:
    """SIGTERM then SIGKILL; returns (terminated, killed)."""
    targets = list(targets)
    for proc in targets:
        try:
            signal_fn(proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
    deadline = time.time() + max(grace, 0.0)
    while time.time() < deadline:
        if not any(alive(p.pid) for p in targets):
            break
        time.sleep(0.1)
    terminated = [p.pid for p in targets if not alive(p.pid)]
    killed: list[int] = []
    for proc in targets:
        if alive(proc.pid):
            try:
                signal_fn(proc.pid, signal.SIGKILL)
                killed.append(proc.pid)
            except (ProcessLookupError, PermissionError):
                pass
    if killed:
        deadline = time.time() + 5.0
        while time.time() < deadline and any(alive(p) for p in killed):
            time.sleep(0.1)
    return terminated, killed


# --- verbs --------------------------------------------------------------


def service_status(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    gguf_dir: Path | str = DEFAULT_GGUF_DIR,
    proc_root: Path | str = "/proc",
    state_file: Path | None = None,
    gpu_apps=gpu_compute_apps,
) -> ServiceResult:
    state_file = state_file or default_state_file()
    supervisors = discover_supervisors(port=port, proc_root=proc_root, state_file=state_file)
    health = probe_health(host, port)
    servers = discover_llama_servers(gguf_dir=gguf_dir, proc_root=proc_root)
    targets, spared = classify_llama_targets(servers, proc_root=proc_root)
    state = read_state(state_file)

    running = bool(supervisors)
    details: dict[str, Any] = {
        "running": running,
        "supervisors": [
            {"pid": p.pid, "ppid": p.ppid, "cmdline": p.cmdline} for p in supervisors
        ],
        "endpoint": f"http://{host}:{port}",
        "healthy": health is not None,
        "resident": (health or {}).get("resident"),
        "llama_servers": [
            {"pid": p.pid, "ppid": p.ppid, "model": parse_llama_argv(p.argv)[1]} for p in servers
        ],
        "orphan_llama_servers": [p.pid for p in targets],
        "llama_servers_with_live_parent": [p.pid for p in spared],
        "state_file": str(state_file),
        "state_file_matches_live_process": bool(state and any(p.pid == state.pid for p in supervisors)),
        "gpu_compute_apps": gpu_apps(),
    }
    if not running:
        message = "z0intelligence is stopped"
        if targets:
            message += f"; {len(targets)} orphaned llama-server(s) still hold the GPU (run: z0int service stop)"
        return ServiceResult(True, "status", message, details)
    pids = ",".join(str(p.pid) for p in supervisors)
    return ServiceResult(
        True, "status",
        f"z0intelligence is running (pid {pids}) at http://{host}:{port}"
        + (f", resident {health.get('resident')}" if health and health.get("resident") else ""),
        details,
    )


def start_service(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    context: int = DEFAULT_CONTEXT,
    idle_unload: float = DEFAULT_IDLE_UNLOAD_S,
    llama_server: str | None = None,
    gguf_dir: str | None = None,
    state_file: Path | None = None,
    log_file: Path | None = None,
    wait_health: bool = True,
    health_timeout: float = 60.0,
    force: bool = False,
    proc_root: Path | str = "/proc",
    command: Sequence[str] | None = None,
) -> ServiceResult:
    state_file = state_file or default_state_file()
    log_file = log_file or default_log_file()

    existing = discover_supervisors(port=port, proc_root=proc_root, state_file=state_file)
    if existing and not force:
        pids = [p.pid for p in existing]
        return ServiceResult(
            True, "already_running",
            f"z0intelligence already running (pid {','.join(str(p) for p in pids)}) at http://{host}:{port}",
            {"running": True, "supervisors": pids, "endpoint": f"http://{host}:{port}"},
        )
    if existing and force:
        stop_service(host=host, port=port, gguf_dir=gguf_dir or DEFAULT_GGUF_DIR,
                     state_file=state_file, proc_root=proc_root)

    argv = list(command) if command else build_supervisor_argv(
        host=host, port=port, context=context, idle_unload=idle_unload,
        llama_server=llama_server, gguf_dir=gguf_dir,
    )
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("ab") as log:
        log.write(f"\n--- z0int service start {time.strftime('%Y-%m-%dT%H:%M:%S')} ---\n".encode())
        proc = subprocess.Popen(  # noqa: S603 - argv is built here, not user input
            argv, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            start_new_session=True, cwd=str(Path(__file__).resolve().parents[3]),
        )
    reap_finished()
    _SPAWNED[proc.pid] = proc

    write_state(
        ServiceState(pid=proc.pid, host=host, port=port, started_at=time.time(),
                     argv=tuple(argv), log_file=str(log_file)),
        state_file,
    )

    if not wait_health:
        return ServiceResult(
            True, "started",
            f"started z0intelligence (pid {proc.pid}) at http://{host}:{port}; log {log_file}",
            {"pid": proc.pid, "endpoint": f"http://{host}:{port}", "log_file": str(log_file), "ready": None},
        )

    deadline = time.time() + max(health_timeout, 0.0)
    while time.time() < deadline:
        if proc.poll() is not None:
            clear_state(state_file)
            return ServiceResult(
                False, "start_failed",
                f"supervisor exited during startup with code {proc.returncode}; see {log_file}",
                {"pid": proc.pid, "log_file": str(log_file), "exit_code": proc.returncode,
                 "log_tail": _tail(log_file)},
            )
        if probe_health(host, port):
            return ServiceResult(
                True, "started",
                f"z0intelligence ready (pid {proc.pid}) at http://{host}:{port}",
                {"pid": proc.pid, "endpoint": f"http://{host}:{port}", "log_file": str(log_file),
                 "ready": True, "state_file": str(state_file)},
            )
        time.sleep(0.25)

    stop_service(host=host, port=port, gguf_dir=gguf_dir or DEFAULT_GGUF_DIR,
                 state_file=state_file, proc_root=proc_root)
    return ServiceResult(
        False, "start_timeout",
        f"supervisor did not become healthy within {health_timeout:.0f}s; rolled back. See {log_file}",
        {"pid": proc.pid, "log_file": str(log_file), "log_tail": _tail(log_file)},
    )


def stop_service(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    gguf_dir: Path | str = DEFAULT_GGUF_DIR,
    state_file: Path | None = None,
    grace: float = 15.0,
    proc_root: Path | str = "/proc",
    alive=is_alive,
    signal_fn=os.kill,
    gpu_apps=gpu_compute_apps,
) -> ServiceResult:
    """Stop the supervisor *and* any llama-server it orphaned.  Verifies afterwards."""
    state_file = state_file or default_state_file()
    state = read_state(state_file)
    reap_finished()  # a finished child is not a running service

    supervisors = discover_supervisors(port=port, proc_root=proc_root, state_file=state_file)
    stale_state = False
    if state is not None and not any(p.pid == state.pid for p in supervisors):
        # The state file names a pid that is not a supervisor for this port: it is
        # stale, or the number now belongs to somebody else.  Say so; never signal.
        stale_state = True

    terminated, killed = _stopped_pids(supervisors, grace=grace, alive=alive, signal_fn=signal_fn)

    servers = discover_llama_servers(gguf_dir=gguf_dir, proc_root=proc_root)
    targets, spared = classify_llama_targets(servers, stopped_pids=terminated, proc_root=proc_root)
    if targets:
        _stopped_pids(targets, grace=grace, alive=alive, signal_fn=signal_fn)

    leftover_sup = discover_supervisors(port=port, proc_root=proc_root, state_file=state_file)
    leftover_srv = [p for p in discover_llama_servers(gguf_dir=gguf_dir, proc_root=proc_root) if p.pid not in {s.pid for s in spared}]

    if not leftover_sup and not leftover_srv:
        clear_state(state_file)

    details: dict[str, Any] = {
        "stopped_supervisors": terminated,
        "killed_supervisors": killed,
        "stopped_llama_servers": [p.pid for p in targets],
        "spared_llama_servers": [p.pid for p in spared],
        "stale_state_file": stale_state,
        "leftover_supervisors": [p.pid for p in leftover_sup],
        "leftover_llama_servers": [p.pid for p in leftover_srv],
        "gpu_compute_apps": gpu_apps(),
        "endpoint": f"http://{host}:{port}",
        "state_file": str(state_file),
    }

    if leftover_sup or leftover_srv:
        return ServiceResult(
            False, "stop_incomplete",
            f"could not stop everything: supervisors {[p.pid for p in leftover_sup]}, "
            f"llama-servers {[p.pid for p in leftover_srv]}",
            details,
        )
    if not supervisors and not targets:
        msg = "z0intelligence was not running"
        if stale_state:
            msg += " (removed a stale state file)"
        return ServiceResult(True, "already_stopped", msg, details)
    return ServiceResult(
        True, "stopped",
        f"stopped z0intelligence (supervisors {terminated or 'none'}, llama-servers "
        f"{[p.pid for p in targets] or 'none'}); GPU released",
        details,
    )


def unload_service(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    timeout: float = 30.0,
    gpu_apps=gpu_compute_apps,
) -> ServiceResult:
    """Give the GPU back but leave the supervisor listening. Cheap to resume."""
    before = probe_health(host, port)
    if before is None:
        return ServiceResult(False, "not_running", f"no healthy supervisor at http://{host}:{port}", {})
    payload = http_json(f"http://{host}:{port}/z0int/release", method="POST", body={}, timeout=timeout)
    if payload is None:
        return ServiceResult(False, "unload_failed", f"release request to http://{host}:{port} failed", {})
    detail: dict[str, Any] = {"evicted": payload.get("evicted"), "resident": payload.get("resident"),
                              "gpu_compute_apps": gpu_apps()}
    if payload.get("evicted"):
        return ServiceResult(True, "unloaded", "released the resident model; supervisor still listening", detail)
    detail["note"] = "nothing was resident; the supervisor is still listening"
    return ServiceResult(True, "nothing_resident", "no model was resident; supervisor still listening", detail)


def _tail(path: Path, *, lines: int = 15) -> list[str]:
    try:
        return path.read_text(errors="replace").splitlines()[-lines:]
    except OSError:
        return []


__all__ = [
    "SCHEMA",
    "STATE_SCHEMA",
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "ProcInfo",
    "ServiceResult",
    "ServiceState",
    "build_supervisor_argv",
    "classify_llama_targets",
    "clear_state",
    "default_log_file",
    "default_state_dir",
    "default_state_file",
    "discover_llama_servers",
    "discover_supervisors",
    "gpu_compute_apps",
    "is_alive",
    "is_init_like",
    "is_supervisor_argv",
    "is_z0int_llama_argv",
    "iter_procs",
    "parse_llama_argv",
    "probe_health",
    "read_proc",
    "read_state",
    "reap_finished",
    "service_status",
    "start_service",
    "stop_service",
    "unload_service",
    "write_state",
]
