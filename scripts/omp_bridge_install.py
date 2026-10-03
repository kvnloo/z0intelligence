"""Point the OMP agent's capture extensions at one pinned z0intelligence checkout (activation step A2).

    python scripts/omp_bridge_install.py --target /mnt/zer0models/z0-wt/pinned/z0intelligence [--dry-run]
        [--agent-dir DIR] [--include-routing] [--allow-no-routing] [--backup PATH]

Each extension stays ONE symlink ``<agent-dir>/extensions/<name> -> <target>/omp-extensions/<name>`` (a link,
never a copy: the shim imports its siblings and finds ``src/z0int`` through its real path). Salvaged from
feat/omp-bridge-ready@815c680 and narrowed to the wiring plan:

- only ``z0int-bridge`` and ``local-cognition`` move; ``z0int-intelligence`` (live routing) moves only with
  ``--include-routing``, and no other entry under ``extensions/`` is ever touched. The bridge registers capture
  only, so routing comes from the ``z0int-intelligence`` link alone: the plan reports it as ``kept``, and a
  missing link is refused (routing would vanish) unless the owner passes ``--allow-no-routing``;
- the target must be a clean git checkout that carries each extension it will point at (a dirty tree is
  refused, in a dry run too) and an entry that is not a symlink is refused, never replaced;
- before the first change the full listing of ``extensions/`` (name -> link target) is saved to
  ``<agent-dir>/extensions.links.bak-z0wiring-<date>``; an existing backup is kept, it is the rollback point;
- a second run with the same target changes nothing; ``--dry-run`` prints the plan and writes nothing.

Prints the plan as JSON. Rollback: ``ln -sfn <target from the backup> <agent-dir>/extensions/<name>``.
The agent dir defaults to ``$PI_CODING_AGENT_DIR`` or ``~/.omp/agent``. Restart OMP after a change.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

CAPTURE = ("z0int-bridge", "local-cognition")
ROUTING = ("z0int-intelligence",)
REQUIRED = ("src/z0int/bridge/worker.py",)


def default_agent_dir() -> Path:
    env = os.environ.get("PI_CODING_AGENT_DIR")
    return Path(env).expanduser() if env else Path.home() / ".omp" / "agent"


def default_backup(agent_dir: Path) -> Path:
    return agent_dir / f"extensions.links.bak-z0wiring-{time.strftime('%Y%m%d')}"


def refuse(message: str) -> None:
    raise SystemExit(f"refusing: {message}")


def check_target(target: Path, names: tuple[str, ...]) -> None:
    proc = subprocess.run(["git", "-C", str(target), "status", "--porcelain"], capture_output=True, text=True)
    if proc.returncode != 0:
        refuse(f"{target} is not a git checkout")
    if proc.stdout.strip():
        refuse(f"{target} is a dirty checkout; pin a clean tree:\n{proc.stdout}")
    missing = [rel for rel in (*REQUIRED, *(f"omp-extensions/{n}/index.ts" for n in names))
               if not (target / rel).is_file()]
    if missing:
        refuse(f"{target} is missing {missing}")


def listing(ext_dir: Path) -> dict:
    links, other = {}, {}
    for entry in sorted(ext_dir.iterdir()) if ext_dir.is_dir() else []:
        if entry.is_symlink():
            links[entry.name] = {"target": os.readlink(entry)}
        else:
            other[entry.name] = "dir" if entry.is_dir() else "file"
    return {"links": links, "other": other}


def plan(agent_dir: Path, target: Path, names: tuple[str, ...]) -> list[dict]:
    actions = []
    for name in names:
        link, want = agent_dir / "extensions" / name, (target / "omp-extensions" / name).resolve()
        if link.is_symlink():
            current = os.readlink(link)
            if (link.parent / current).resolve() == want:
                actions.append({"name": name, "action": "unchanged", "to": str(want)})
            else:
                actions.append({"name": name, "action": "retarget", "from": current, "to": str(want)})
        elif link.exists():
            refuse(f"{link} exists and is not a symlink; leaving it alone")
        else:
            actions.append({"name": name, "action": "create", "to": str(want)})
    return actions


def routing_entry(agent_dir: Path, allow_missing: bool) -> dict:
    """The routing link this run leaves alone: reported ``kept``; a missing one is refused unless allowed."""
    link = agent_dir / "extensions" / ROUTING[0]
    if link.is_symlink():
        return {"name": ROUTING[0], "action": "kept", "target": os.readlink(link)}
    if link.exists():
        return {"name": ROUTING[0], "action": "kept", "target": None}
    if not allow_missing:
        refuse(f"{link} does not exist: the bridge carries no routing, so live routing would vanish; "
               "restore the z0int-intelligence link, pass --include-routing, or --allow-no-routing")
    return {"name": ROUTING[0], "action": "absent"}


def apply(agent_dir: Path, actions: list[dict], backup: Path) -> bool:
    """Save the listing (once), then swap each link atomically. Returns whether a backup was written."""
    if not any(a["action"] in ("create", "retarget") for a in actions):
        return False
    ext = agent_dir / "extensions"
    ext.mkdir(parents=True, exist_ok=True)
    wrote = False
    if not backup.exists():
        backup.write_text(json.dumps({"schema": "z0int.omp_extension_links.v0", "agent_dir": str(agent_dir),
                                      "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                      **listing(ext)}, indent=2) + "\n")
        wrote = True
    for action in actions:
        if action["action"] in ("unchanged", "kept", "absent"):
            continue
        tmp = ext / f".{action['name']}.z0wiring-tmp"
        if tmp.is_symlink():
            tmp.unlink()
        tmp.symlink_to(action["to"], target_is_directory=True)
        os.replace(tmp, ext / action["name"])
    return wrote


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", type=Path, required=True, help="pinned z0intelligence checkout (clean)")
    ap.add_argument("--agent-dir", type=Path, default=None)
    ap.add_argument("--include-routing", action="store_true", help="also move z0int-intelligence (live routing)")
    ap.add_argument("--allow-no-routing", action="store_true",
                    help="proceed although no z0int-intelligence link exists (OMP then runs without routing)")
    ap.add_argument("--backup", type=Path, default=None, help="links backup path (default: dated, in agent dir)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    agent_dir = (args.agent_dir or default_agent_dir()).expanduser()
    target = args.target.expanduser().resolve()
    names = CAPTURE + (ROUTING if args.include_routing else ())
    check_target(target, names)
    actions = plan(agent_dir, target, names)
    if not args.include_routing:
        actions.append(routing_entry(agent_dir, args.allow_no_routing))
    backup = (args.backup or default_backup(agent_dir)).expanduser()
    wrote = False if args.dry_run else apply(agent_dir, actions, backup)
    print(json.dumps({"agent_dir": str(agent_dir), "target": str(target), "dry_run": args.dry_run,
                      "backup": str(backup), "backup_written": wrote, "actions": actions}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
