"""Install, uninstall, or inspect the z0int bridge OMP extension.

The extension is installed as ONE symlink in the OMP agent directory:

    <agent-dir>/extensions/z0int-bridge -> <repo>/omp-extensions/z0int-bridge

A symlink (not a copy) is required: the shim imports its sibling
``../z0int-intelligence`` and ``../../harness-adapters``, and locates
``src/z0int`` relative to its own real path. Nothing else in the agent
directory is touched, so uninstall is removing that one link.

    python scripts/omp_bridge_install.py status
    python scripts/omp_bridge_install.py install [--agent-dir DIR] [--root REPO]
    python scripts/omp_bridge_install.py uninstall [--agent-dir DIR]

The agent dir defaults to ``$PI_CODING_AGENT_DIR`` or ``~/.omp/agent``.
Restart any running OMP session after install/uninstall.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

LINK_NAME = "z0int-bridge"
REPO_ROOT = Path(__file__).resolve().parents[1]
REQUIRED = (
    "omp-extensions/z0int-bridge/index.ts",
    "omp-extensions/z0int-intelligence/index.ts",
    "omp-extensions/z0int-intelligence/schema.json",
    "harness-adapters/automatic-client.mjs",
    "src/z0int/bridge/worker.py",
)


def default_agent_dir() -> Path:
    env = os.environ.get("PI_CODING_AGENT_DIR")
    return Path(env).expanduser() if env else Path.home() / ".omp" / "agent"


def link_path(agent_dir: Path) -> Path:
    return agent_dir / "extensions" / LINK_NAME


def status(agent_dir: Path, root: Path = REPO_ROOT) -> dict:
    link = link_path(agent_dir)
    missing = [rel for rel in REQUIRED if not (root / rel).is_file()]
    out: dict = {
        "agent_dir": str(agent_dir),
        "link": str(link),
        "root": str(root),
        "root_complete": not missing,
        "missing": missing,
        "installed": False,
        "target": None,
        "ours": False,
    }
    if link.is_symlink():
        target = Path(os.readlink(link))
        if not target.is_absolute():
            target = (link.parent / target).resolve()
        out.update(installed=True, target=str(target), ours=target.name == LINK_NAME, dangling=not target.exists())
    elif link.exists():
        out.update(installed=True, target=None, ours=False, foreign="not_a_symlink")
    venv_py = root / ".venv" / "bin" / "python"
    out["python"] = os.environ.get("Z0INT_PYTHON") or (str(venv_py) if venv_py.exists() else "python3 (fallback)")
    return out


def install(agent_dir: Path, root: Path = REPO_ROOT, *, force: bool = False) -> dict:
    st = status(agent_dir, root)
    if not st["root_complete"]:
        raise SystemExit(f"refusing: {root} is missing {st['missing']}")
    link = link_path(agent_dir)
    target = (root / "omp-extensions" / LINK_NAME).resolve()
    if st["installed"]:
        if st.get("target") == str(target):
            return {**status(agent_dir, root), "action": "already_installed"}
        if not st["ours"] and not force:
            raise SystemExit(f"refusing: {link} exists and is not a z0int-bridge link (use --force)")
        if link.is_dir() and not link.is_symlink():
            raise SystemExit(f"refusing: {link} is a real directory; remove it by hand")
        link.unlink()
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target, target_is_directory=True)
    return {**status(agent_dir, root), "action": "installed"}


def uninstall(agent_dir: Path) -> dict:
    link = link_path(agent_dir)
    st = status(agent_dir)
    if not st["installed"]:
        return {**st, "action": "not_installed"}
    if not link.is_symlink() or not st["ours"]:
        raise SystemExit(f"refusing: {link} is not a z0int-bridge symlink; leaving it alone")
    link.unlink()
    return {**status(agent_dir), "action": "uninstalled"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["status", "install", "uninstall"])
    ap.add_argument("--agent-dir", type=Path, default=None)
    ap.add_argument("--root", type=Path, default=REPO_ROOT, help="z0intelligence checkout that provides the extension")
    ap.add_argument("--force", action="store_true", help="replace a foreign symlink at the link path")
    args = ap.parse_args(argv)
    agent_dir = (args.agent_dir or default_agent_dir()).expanduser()
    root = args.root.expanduser().resolve()
    if args.command == "status":
        out = status(agent_dir, root)
    elif args.command == "install":
        out = install(agent_dir, root, force=args.force)
    else:
        out = uninstall(agent_dir)
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
