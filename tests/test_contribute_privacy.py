"""CW4 privacy rules (spec §3.2, §3.7; acceptance G1/G3/G4/G5 for the z0int side).

G4/G5 run the T0 probe and the whole `suggest` step in a subprocess with a canary
HOME and a ``sys.addaudithook`` on ``open``/``os.listdir``/``os.scandir``. No
never-touch path may be opened or listed, no file under HOME outside ``~/.z0int``
may be opened at all (instruction files are only stat'ed), and no canary value may
reach profile.json, suggestion.json or any receipt.
"""
from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from z0int.contribute import profile_min as PM

ROOT = Path(__file__).resolve().parents[1]
CANARY = "CANARY-7f3a9e-DO-NOT-LEAK"

PROBE = r"""
import json, os, sys
from pathlib import Path
home = Path(os.environ["HOME"])
events = []
def hook(name, args):
    if name in ("open", "os.listdir", "os.scandir") and args and isinstance(args[0], (str, bytes, os.PathLike)):
        events.append([name, os.fsdecode(args[0])])
sys.addaudithook(hook)
from z0int.contribute import cli as K
from z0int.contribute.gh import GH
env = K.Env(root=Path(sys.argv[1]), gh=GH("kvnloo/z0intelligence", runner=lambda a: (1, "", "offline")),
            tty=False, harness="claude-code")
out = K.cmd_suggest(env, profile_mode=sys.argv[2])
json.dump({"events": events, "out": out}, open(sys.argv[3], "w"))
"""


def _fake_home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    for rel in PM.NEVER_TOUCH:
        p = home / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if rel.endswith((".json", ".jsonl", ".toml", ".yml", "-credentials", ".netrc")) or "." in Path(rel).name:
            p.write_text(f"secret = \"{CANARY}\"\n")
        else:
            p.mkdir(parents=True, exist_ok=True)
            (p / "item.json").write_text(json.dumps({"token": CANARY}))
    (home / ".codex" / "config.toml").write_text(
        f'model = "x"\n[mcp_servers.demo]\ncommand = "demo"\n[mcp_servers.demo.env]\nAPI_KEY = "{CANARY}"\n')
    (home / ".claude" / "CLAUDE.md").write_text(f"# my private rules {CANARY}\n")
    (home / ".codex" / "AGENTS.md").write_text(f"# {CANARY}\n")
    (home / ".gitconfig").write_text(f"[user]\n\tname = {CANARY}\n\temail = canary@example.invalid\n")
    return home


@pytest.mark.parametrize("mode", ["none", "minimal", "full"])
def test_g4_g5_never_touch_paths_and_no_canary_leak(tmp_path, mode):
    home = _fake_home(tmp_path)
    out_file = tmp_path / "out.json"
    env = dict(os.environ, HOME=str(home), Z0INT_HOME=str(home / ".z0int"), PYTHONPATH=str(ROOT / "src"))
    env.pop("Z0_HOME", None)
    p = subprocess.run([sys.executable, "-c", PROBE, str(ROOT), mode, str(out_file)], env=env,
                       capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stderr[-2000:]
    res = json.loads(out_file.read_text())
    home_s = str(home.resolve())
    z0int_s = str((home / ".z0int").resolve())
    touched = [(n, str(Path(path).resolve())) for n, path in res["events"]]
    assert any(t[1].startswith(z0int_s) for t in touched)      # the hook really sees opens
    bad = [t for t in touched if t[1].startswith(home_s + os.sep) and not t[1].startswith(z0int_s)]
    assert bad == [], bad                                       # only ~/.z0int under HOME is opened
    for rel in PM.NEVER_TOUCH:
        assert not any(t[1].startswith(str((home / rel).resolve())) for t in touched), rel
    for f in (home / ".z0int" / "contribute").rglob("*"):
        if f.is_file():
            assert CANARY not in f.read_text(errors="ignore"), f
            assert "example.invalid" not in f.read_text(errors="ignore"), f
    assert CANARY not in json.dumps(res["out"])
    prof = json.loads((home / ".z0int" / "contribute" / "profile.json").read_text())
    assert prof["tier"] == {"none": "T0", "minimal": "T1", "full": "T1"}[mode]   # full w/o TTY/grant -> T1
    assert prof["instruction_files"]["claude"]["exists"] is True                  # existence only
    for name in ("profile.json", "suggestion.json"):
        assert stat.S_IMODE((home / ".z0int" / "contribute" / name).stat().st_mode) == 0o600


def test_g1_g3_tier_projection_drops_unconsented_signals():
    richer = {
        "schema": "z0.contribute.profile.v1", "tier": "T3",
        "hw": {"os": "linux", "arch": "x86_64", "ram_gib": 8, "gpu": {"class": "none", "vram_gib": 0}},
        "toolchains": ["python"], "harnesses": [], "languages": {"python": 1.0}, "recency": "active_30d",
        "interests": ["benchmarking"], "repo_names": ["secret-project"], "starred": ["x"],
    }
    for tier in ("T0", "T1"):
        p = PM.apply_tier(richer, tier)
        assert not {"languages", "recency", "interests", "repo_names", "starred"} & set(p), tier
    t2 = PM.apply_tier(richer, "T2")
    assert t2["languages"] == {"python": 1.0} and "interests" not in t2 and "repo_names" not in t2
    t3 = PM.apply_tier(richer, "T3")
    assert t3["interests"] == ["benchmarking"] and "repo_names" not in t3
    assert PM.apply_tier(richer, "T0", prefs_interests=["docs"])["interests"] == ["docs"]  # the user's own prefs


def test_t0_probe_records_no_host_identifiers(tmp_path):
    prof = PM.probe_t0(home=tmp_path, workdir=tmp_path)
    text = json.dumps(prof)
    import getpass
    import platform

    for ident in {platform.node(), getpass.getuser(), str(Path.home()), str(tmp_path)} - {""}:
        assert ident not in text, ident


def test_no_host_specific_literals_in_contributor_layer_files():
    files = list((ROOT / "src" / "z0int" / "contribute").glob("*.py")) + list((ROOT / ".z0").glob("*.yaml"))
    files += [ROOT / ".github" / "scripts" / "claim-bot.py", ROOT / "skills" / "z0int-contribute" / "SKILL.md"]
    pat = re.compile(r"/home/[a-z]|/Users/[A-Za-z]|@gmail\.|C:\\\\Users")
    for f in files:
        assert not pat.search(f.read_text(encoding="utf-8")), f
