"""owner-canon-gate: twin parity with the Bend laws, and one case per law (block + allow)."""
import importlib.util
import itertools
import sys
from pathlib import Path

import pytest

P = Path(__file__).resolve().parents[1] / "plugins" / "owner-canon-gate"
spec = importlib.util.spec_from_file_location("owner_canon_gate", P / "__init__.py")
G = importlib.util.module_from_spec(spec)
sys.modules["owner_canon_gate"] = G
spec.loader.exec_module(G)
K = G.K


def test_twin_matches_laws_exhaustively():
    # LAWS.bend: each guard with trigger True{} implies the clearance; trigger False is always admitted
    for law, (_, pred) in K.RULES.items():
        assert pred(False, False) and pred(False, True) and pred(True, True) and not pred(True, False), law
    for bits in itertools.product([False, True], repeat=6):
        pairs = list(zip(bits[::2], bits[1::2]))
        assert K.admits(*bits) == all(K.guard(t, c) for t, c in pairs)
    assert K.admits(*([False] * 6))  # ordinary_call_admitted


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    G._last_limited.clear()
    return tmp_path


def blocked(tool, **args):
    r = G.on_pre_tool_call(tool_name=tool, args=args)
    return bool(r and r.get("action") == "block")





def test_gui_focus():
    assert blocked("terminal", command="xdotool key super+1")
    assert blocked("terminal", command="adb -s S shell input tap 10 10")
    assert not blocked("terminal", command="adb -s S shell input --display 7 tap 10 10")
    assert not blocked("terminal", command="xvfb-run -a xdotool key a")






def test_quota_backoff():
    args = {"command": "curl https://api.example.com/v1"}
    assert not blocked("terminal", **args)
    G.on_post_tool_call(tool_name="terminal", args=args, result="HTTP 429 Too Many Requests, Retry-After: 30")
    assert blocked("terminal", **args)
    G._last_limited[G._key("terminal", args)] = 0.5  # back-off elapsed
    assert not blocked("terminal", **args)


def test_done_claim_flag_then_enforce(home):
    assert not blocked("message_agent", target="ayo", message="Done, all fixed.")  # default: flag
    (home / "owner-canon-gate.json").write_text('{"mode": {"done_needs_receipt": "enforce"}}')
    assert blocked("message_agent", target="ayo", message="Done, all fixed.")
    assert not blocked("message_agent", target="ayo", message="Done: commit 6a7b5a8, receipt /a/b/c.json")


def test_ordinary_calls_pass():
    for cmd in ("ls -la", "git status", "python3 -m pytest -q", "rm file.txt", "hermes doctor"):
        assert not blocked("terminal", command=cmd), cmd
    assert not blocked("read_file", path="/etc/hosts")


def test_unlocked_candidate_rules_are_not_gated():
    # rules 16-60 are candidates (not adopted): deletes, public posts and GPT launches are not this gate's call
    for cmd in ("rm -rf /mnt/zer0models/models/foo", "gh pr create --repo NousResearch/hermes-agent --title t",
                "codex exec --model gpt-5.5 'x'"):
        assert not blocked("terminal", command=cmd), cmd
    assert set(K.RULES) == {"gui_never_steals_focus", "done_needs_receipt", "quota_backs_off"}

