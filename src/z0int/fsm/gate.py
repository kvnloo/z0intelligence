"""Opt-in pre-dispatch checks. No hooks and no worker execution."""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess



def identity(pid: int) -> str | None:
    try:
        # comm can contain spaces and parentheses; starttime is field 22.
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        if fields[0] == "Z":
            return None
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip() + ":" + fields[19]
    except FileNotFoundError:
        return None


def lease_path(worktree: str) -> tuple[Path, str]:
    def git(*args: str) -> str:
        return subprocess.check_output(["git", "-C", worktree, *args], text=True).strip()
    top = str(Path(git("rev-parse", "--show-toplevel")).resolve())
    common = Path(git("rev-parse", "--path-format=absolute", "--git-common-dir"))
    return common / "z0-leases" / (hashlib.sha256(top.encode()).hexdigest() + ".json"), top


def lease(action: str, worktree: str, holder: str, pid: int) -> tuple[dict, int]:
    path, top = lease_path(worktree)
    path.parent.mkdir(mode=0o700, exist_ok=True)
    # Serialize reclaim/release too: O_EXCL alone does not protect unlink races.
    with (path.parent / ".guard").open("a") as guard:
        fcntl.flock(guard, fcntl.LOCK_EX)
        try:
            current = json.loads(path.read_text())
            if not isinstance(current, dict):
                raise ValueError("invalid lease")
        except FileNotFoundError:
            current = None
        except (ValueError, OSError):
            return {"status": "hold", "reason": "unreadable lease; manual investigation required", "path": str(path)}, 3
        host = socket.gethostname()
        def dead(record: dict) -> bool:
            if record.get("host") != host or not isinstance(record.get("pid"), int) or not record.get("process_start"):
                return False
            return identity(record["pid"]) != record["process_start"]
        if action == "status":
            return {"status": "held" if current else "free", "lease": current, "path": str(path),
                    "dead": dead(current) if current else False}, 0
        if action == "release":
            if current is None:
                return {"status": "free", "path": str(path)}, 0
            if current.get("holder") != holder or current.get("pid") != pid or current.get("host") != host or current.get("process_start") != identity(pid):
                return {"status": "hold", "reason": "only the live holder can release", "lease": current}, 3
            path.unlink()
            return {"status": "released", "path": str(path)}, 0
        start = identity(pid)
        if start is None:
            return {"status": "hold", "reason": "acquiring pid is not alive"}, 3
        if current:
            if (current.get("holder"), current.get("pid"), current.get("process_start"), current.get("host")) == (holder, pid, start, host):
                return {"status": "held", "lease": current, "path": str(path)}, 0
            if not dead(current):
                return {"status": "hold", "reason": "lease has a live or unverifiable holder", "lease": current}, 3
            path.unlink()
        record = {"worktree": top, "holder": holder, "pid": pid, "host": host, "process_start": start,
                  "started_at": dt.datetime.now(dt.timezone.utc).isoformat()}
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(record, stream)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        return {"status": "acquired", "lease": record, "path": str(path)}, 0


def heavy(cmd: str) -> tuple[dict, int]:
    # Judge each local executable independently so mentioning CUDA or fleet in
    # an unrelated command cannot exempt a local cargo/npm build.
    from .supervise import Call, heavy_jobs_groot
    violations = [job.cmd for job in heavy_jobs_groot([Call("gate", 0, "terminal", {"command": cmd}, "")])
                  if job.exception == "none"]
    return {"status": "hold" if violations else "ok", "law": "W4",
            "reason": "route local heavy commands via fleet-build" if violations else "no unexcepted local heavy command",
            "commands": violations}, 3 if violations else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    leases = commands.add_parser("lease")
    leases.add_argument("action", choices=["acquire", "release", "status"])
    leases.add_argument("--worktree", required=True)
    leases.add_argument("--holder", required=True)
    leases.add_argument("--pid", type=int, default=os.getppid(), help="local persistent worker pid (default: parent)")
    placement = commands.add_parser("heavy")
    placement.add_argument("--cmd", required=True)
    args = parser.parse_args(argv)
    try:
        result, code = (heavy(args.cmd) if args.command == "heavy" else
                        lease(args.action, args.worktree, args.holder, args.pid))
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        result, code = {"status": "hold", "reason": str(exc)}, 3
    print(json.dumps(result))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
