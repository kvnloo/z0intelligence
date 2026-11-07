"""T0 profile probe: the z0int fallback for kvnloo/z0 ``detect/profile.py`` (spec §3.2).

T0 only: OS, arch, RAM, disk, GPU class and VRAM, toolchains present, harnesses
present, and whether the user's instruction files exist (exists, size, mtime — the
bodies are never opened). It reads no settings, transcripts, credentials, or
environment values, and records no host name, user name, or path.

When kvnloo/z0 host onboarding wrote ``$Z0_HOME/onboard/profile.json``, the CLI may
use it instead, projected down to the consented tier by :func:`apply_tier`.
"""
from __future__ import annotations

import os
import platform
import shutil
from pathlib import Path
from typing import Any, Callable

SCHEMA = "z0.contribute.profile.v1"
TIERS = ("T0", "T1", "T2", "T3")

# Paths under $HOME this layer must never open, list or stat for content
# (spec §3.2 "never-touch"; enforced by tests/test_contribute_privacy.py).
NEVER_TOUCH = (
    ".claude/.credentials.json",
    ".claude/history.jsonl",
    ".claude/projects",
    ".claude/file-history",
    ".claude/paste-cache",
    ".claude/mcp-oauth-locks",
    ".codex/attachments",
    ".codex/dictation-history",
    ".codex/mcp-oauth-locks",
    ".codex/auth.json",
    ".codex/sessions",
    ".codex/history.jsonl",
    ".codex/config.toml",
    ".claude/settings.json",
    ".config/gh/hosts.yml",
    ".git-credentials",
    ".netrc",
    ".ssh",
)

# Harness label -> executable names. Labels only; nothing is run.
HARNESS_BINARIES = {
    "claude": ("claude",),
    "codex": ("codex",),
    "omp": ("omp",),
    "hermes": ("hermes",),
    "opencode": ("opencode",),
}
# Toolchain tag (closed vocab) -> executables that show it is present.
TOOLCHAIN_BINARIES = {
    "node": ("node",),
    "bun": ("bun",),
    "cargo": ("cargo",),
    "go": ("go",),
    "cuda": ("nvcc",),
    "llama-cpp": ("llama-server", "llama-cli"),
}
# Instruction files: existence/size/mtime only, keyed by harness label (no paths).
INSTRUCTION_FILES = {"claude": ".claude/CLAUDE.md", "codex": ".codex/AGENTS.md"}


def _os_tag() -> str:
    return {"Linux": "linux", "Darwin": "macos", "Windows": "windows"}.get(platform.system(), "unknown")


def _arch_tag() -> str:
    m = platform.machine().lower()
    if m in ("x86_64", "amd64"):
        return "x86_64"
    if m in ("arm64", "aarch64"):
        return "arm64"
    return "unknown"


def _ram_gib() -> float | None:
    try:
        return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024**3, 1)
    except (ValueError, OSError, AttributeError):
        return None


def _disk_free_gib(where: Path) -> float | None:
    try:
        return round(shutil.disk_usage(where).free / 1024**3, 1)
    except OSError:
        return None


def _gpu(which: Callable[[str], str | None]) -> dict[str, Any]:
    if which("nvidia-smi"):
        try:
            from ..models_mgmt import detect_vram_gb

            vram = detect_vram_gb()
        except Exception:  # noqa: BLE001 - _guard: a probe failure is not fatal
            vram = None
        if vram:
            return {"class": "nvidia", "vram_gib": int(round(vram))}
    if _os_tag() == "macos" and _arch_tag() == "arm64":
        return {"class": "apple", "vram_gib": 0}
    return {"class": "none", "vram_gib": 0}


def probe_t0(*, home: Path | None = None, workdir: Path | None = None,
             which: Callable[[str], str | None] = shutil.which) -> dict[str, Any]:
    """T0 facts. Every part is guarded: a failing probe yields 'unknown', never a crash."""
    home = Path(home) if home is not None else Path.home()
    toolchains = ["python"]
    for tag, bins in TOOLCHAIN_BINARIES.items():
        if any(which(b) for b in bins):
            toolchains.append(tag)
    gpu = _gpu(which)
    if gpu["class"] == "nvidia" and "cuda" not in toolchains:
        toolchains.append("cuda")
    harnesses = sorted(h for h, bins in HARNESS_BINARIES.items() if any(which(b) for b in bins))
    instruction = {}
    for label, rel in INSTRUCTION_FILES.items():
        try:
            st = (home / rel).stat()
            instruction[label] = {"exists": True, "size": int(st.st_size), "mtime": int(st.st_mtime)}
        except OSError:
            instruction[label] = {"exists": False, "size": None, "mtime": None}
    return {
        "schema": SCHEMA,
        "tier": "T0",
        "source": "z0int.profile_min",
        "hw": {
            "os": _os_tag(),
            "arch": _arch_tag(),
            "ram_gib": _ram_gib(),
            "disk_free_gib": _disk_free_gib(workdir or Path.cwd()),
            "gpu": gpu,
        },
        "network": None,
        "toolchains": sorted(set(toolchains)),
        "harnesses": harnesses,
        "instruction_files": instruction,
    }


_T0_KEYS = ("schema", "tier", "source", "hw", "network", "toolchains", "harnesses", "instruction_files")
_T2_KEYS = ("languages", "recency", "experience")


def apply_tier(profile: dict[str, Any], tier: str, *, prefs_interests: list[str] | None = None) -> dict[str, Any]:
    """Project a profile down to the consented tier (G1/G3). Unknown keys are dropped.

    T0/T1 keep only the T0 facts (z0int has no T1 detector of its own). T2 adds the
    language histogram, recency and experience bucket. Interests come from the
    user's own prefs at every tier; profile-derived (T3) interests only at T3.
    """
    if tier not in TIERS:
        raise ValueError(f"unknown tier {tier!r}")
    out = {k: profile[k] for k in _T0_KEYS if k in profile}
    out["schema"] = SCHEMA
    out["tier"] = tier
    level = TIERS.index(tier)
    if level >= 2:
        out.update({k: profile[k] for k in _T2_KEYS if k in profile})
    interests = list(prefs_interests or [])
    if level >= 3:
        interests += [i for i in profile.get("interests") or [] if i not in interests]
    if interests:
        out["interests"] = sorted(set(interests))
    return out
