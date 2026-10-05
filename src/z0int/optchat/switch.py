"""The /optchat switch. Missing file means off."""
from __future__ import annotations

import os
from pathlib import Path


def flag_path() -> Path:
    home = Path(os.environ.get("Z0INT_HOME", Path.home() / ".z0int"))
    return home / "optchat" / "enabled"


def enabled() -> bool:
    try:
        return flag_path().read_text(encoding="utf-8").strip() == "on"
    except OSError:
        return False


def set_enabled(on: bool) -> None:
    path = flag_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("on\n" if on else "off\n", encoding="utf-8")
