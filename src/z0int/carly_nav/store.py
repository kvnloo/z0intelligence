"""Private state for the Carly navigation inlet, under ``~/.z0int/state/carly_nav``.

Files:
- ``catalog.json``: navigation actions Jev may choose, plus the tuned policy.
- ``episodes.jsonl``: one row per fast-path request (hot path appends).
- ``outcomes.jsonl``: what happened after a request (applied, fallback, undone).
- ``universe.json``: Tern's palette actions as last reported by the plugin.
- ``labels.jsonl``: learner-derived ground truth (rewritten by ``learn``).

Stdlib only: the hot path imports this on every Tern request.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Iterator

SCHEMA = "z0int.carly_nav.v1"

# A navigation action's executor. ``tern_action`` runs a Tern action id with no
# target; ``focus`` asks a second (target) question over the window's tabs/blocks.
SEED_ACTIONS: dict[str, dict[str, Any]] = {
    "next_tab": {
        "description": "Go to the next tab.",
        "exec": {"type": "tern_action", "name": "next_tab"},
    },
    "previous_tab": {
        "description": "Go to the previous tab.",
        "exec": {"type": "tern_action", "name": "previous_tab"},
    },
    "focus": {
        "description": "Switch to one specific tab or block the request names or describes.",
        "exec": {"type": "focus"},
    },
}

DEFAULT_POLICY: dict[str, Any] = {
    # Jev accepts up to 255 choice options and its guide favours the full list
    # plus a way out. The learner keeps the active set small by retiring stale
    # actions; ``tune`` lowers this only when replay evidence shows it helps.
    "max_actions": 64,
    "max_targets": 64,
    "min_confidence": 0.4,
    "exemplars_per_action": 3,
    "description_chars": 160,
    "promote_min_support": 3,
    "retire_after_days": 30,
    "jev_timeout_s": 0.8,
    "evidence": None,
}


def root() -> Path:
    override = os.environ.get("Z0INT_HOME")
    base = Path(override).expanduser() if override else Path.home() / ".z0int"
    return base / "state" / "carly_nav"


def path(name: str) -> Path:
    return root() / name


def now() -> float:
    return time.time()


def append_jsonl(name: str, row: dict[str, Any]) -> None:
    target = path(name)
    target.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(row, separators=(",", ":"), ensure_ascii=False) + "\n"
    # One write() per row keeps concurrent appenders from interleaving lines.
    fd = os.open(target, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, line.encode())
    finally:
        os.close(fd)


def read_jsonl(name: str | Path) -> Iterator[dict[str, Any]]:
    target = name if isinstance(name, Path) else path(name)
    try:
        handle = open(target, encoding="utf-8")
    except FileNotFoundError:
        return
    with handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                yield row


def write_json(name: str, value: Any) -> None:
    target = path(name)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    os.replace(tmp, target)


def read_json(name: str) -> Any:
    try:
        with open(path(name), encoding="utf-8") as handle:
            return json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def default_catalog() -> dict[str, Any]:
    actions = {
        action_id: {
            **spec,
            "status": "active",
            "source": "seed",
            "exemplars": [],
            "support": 0,
            "last_used": None,
        }
        for action_id, spec in SEED_ACTIONS.items()
    }
    return {"schema": SCHEMA, "actions": actions, "policy": dict(DEFAULT_POLICY), "updated": None}


def load_catalog() -> dict[str, Any]:
    catalog = read_json("catalog.json")
    if not isinstance(catalog, dict) or not isinstance(catalog.get("actions"), dict):
        return default_catalog()
    policy = dict(DEFAULT_POLICY)
    policy.update(catalog.get("policy") or {})
    catalog["policy"] = policy
    for action_id, spec in SEED_ACTIONS.items():
        catalog["actions"].setdefault(
            action_id,
            {**spec, "status": "active", "source": "seed", "exemplars": [], "support": 0, "last_used": None},
        )
    return catalog


def save_catalog(catalog: dict[str, Any]) -> None:
    catalog["schema"] = SCHEMA
    catalog["updated"] = now()
    write_json("catalog.json", catalog)
