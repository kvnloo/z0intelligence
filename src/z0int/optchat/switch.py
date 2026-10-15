"""The /optchat switch. Missing file means off."""
from __future__ import annotations

from pathlib import Path

from .log import chat_root


def flag_path() -> Path:
    """Next to the log: ~/.z0int/optchat/enabled, or the harness-local log's own flag."""
    return chat_root().parent / "enabled"


def enabled() -> bool:
    try:
        return flag_path().read_text(encoding="utf-8").strip() == "on"
    except OSError:
        return False


def set_enabled(on: bool) -> None:
    path = flag_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("on\n" if on else "off\n", encoding="utf-8")
