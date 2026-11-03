"""O18 privacy-tier routing: twin table, host-fact classifiers and the Hermes adapter."""
from __future__ import annotations

import importlib.util
import itertools
import json
import sys
from pathlib import Path

import pytest

from z0int.fsm import ops as P
from z0int.fsm import privacy_route as R

ROOT = Path(__file__).resolve().parents[1]
B2 = (False, True)
ALL_UP = {lane: True for lane in P.MLane}


def test_table_rows_match_owner_policy():
    allowed = {
        P.Tier.PUBLIC: set(P.MLane),
        P.Tier.PERSONAL: {P.MLane.LOCAL, P.MLane.PAID, P.MLane.FREE_OK},
        P.Tier.UNKNOWN: {P.MLane.LOCAL}, P.Tier.SENSITIVE: {P.MLane.LOCAL}, P.Tier.THIRD: {P.MLane.LOCAL},
        P.Tier.SECRET: set(),
    }
    for t, lane in itertools.product(P.Tier, P.MLane):
        assert P.tier_allows(t, lane) == (lane in allowed[t]), (t, lane)


@pytest.mark.parametrize("t,k,loc,paid,fok,fpub", list(itertools.product(P.Tier, P.Task, B2, B2, B2, B2)))
def test_route_sound_everywhere(t, k, loc, paid, fok, fpub):
    kind, lane = P.route(t, k, loc, paid, fok, fpub)
    if t is P.Tier.SECRET:
        assert kind == "refuse"
    if kind == "use":
        assert P.tier_allows(t, lane)
        assert {P.MLane.LOCAL: loc, P.MLane.PAID: paid, P.MLane.FREE_OK: fok, P.MLane.FREE_PUB: fpub}[lane]
        if k is P.Task.AGENTIC:
            assert lane not in (P.MLane.FREE_OK, P.MLane.FREE_PUB)
            if t in (P.Tier.PUBLIC, P.Tier.PERSONAL):
                assert lane is P.MLane.PAID


def test_decide_maps_lanes_to_router_models():
    assert R.decide(P.Tier.PERSONAL, P.Task.AGENTIC, ALL_UP)["model"] == "vllm-sr/private"
    assert R.decide(P.Tier.SENSITIVE, P.Task.AGENTIC, ALL_UP)["model"] == "vllm-sr/local"
    assert R.decide(P.Tier.PUBLIC, P.Task.SIMPLE, ALL_UP)["model"] == "vllm-sr/free-private"
    assert R.decide(P.Tier.SECRET, P.Task.SIMPLE, ALL_UP)["model"] == R.REFUSE_MODEL
    down = {**ALL_UP, P.MLane.PAID: False}
    assert R.decide(P.Tier.PERSONAL, P.Task.AGENTIC, down)["model"] == R.QUEUE_MODEL


def test_lanes_up_none_router_is_all_down():
    assert not any(R.lanes_up(None).values())
    assert R.lanes_up({"vllm-sr/local"})[P.MLane.LOCAL] is True


@pytest.mark.parametrize("path,tier", [
    ("/mnt/zer0models/store/sealed/tax.pdf", P.Tier.SECRET),
    ("/mnt/zer0models/sft-svlm/data/raw/google/Takeout.zip", P.Tier.SECRET),
    ("/mnt/zer0models/sft-svlm/data/raw/telegram/result.json", P.Tier.SECRET),
    ("~/.ssh/id_ed25519", P.Tier.SECRET),
    ("/workspace/zer0/repo/.env", P.Tier.SECRET),
    ("~/.hermes/profiles/gym-trainer/auth.json", P.Tier.SECRET),
    ("/proc/123/environ", P.Tier.SECRET),
    ("/mnt/zer0models/sft-svlm/data/privacy/manifest.jsonl", P.Tier.THIRD),
    ("/mnt/zer0models/sft-svlm/data/raw/chatgpt/conversations.json", P.Tier.THIRD),
    ("/mnt/zer0models/sft-svlm/data/datasets/sft.jsonl", P.Tier.SENSITIVE),
    ("/mnt/zer0models/store/llm/memory/x.md", P.Tier.PERSONAL),
    ("/workspace/zer0/oss/evolutionary-training-hermes-provider-20261010/README.md", P.Tier.PERSONAL),
    ("/usr/lib/python3.14/os.py", P.Tier.PUBLIC),
    ("/srv/somewhere/else", P.Tier.UNKNOWN),
])
def test_path_tier(path, tier):
    assert R.path_tier(path) is tier


def test_symlink_into_sealed_is_secret(tmp_path):
    sealed = tmp_path / "s"
    sealed.mkdir()
    link = tmp_path / "innocent"
    link.symlink_to("/mnt/zer0models/store/sealed")
    assert R.path_tier(str(link / "tax.pdf")) is P.Tier.SECRET


@pytest.mark.parametrize("tool,args,tier", [
    ("read_file", {"path": "/mnt/zer0models/store/sealed/a"}, P.Tier.SECRET),
    ("terminal", {"command": "cat ~/.hermes/.env"}, P.Tier.SECRET),
    ("terminal", {"command": "bws secret get abc"}, P.Tier.SECRET),
    ("terminal", {"command": "printenv"}, P.Tier.SECRET),
    ("terminal", {"command": "wc -l /mnt/zer0models/sft-svlm/data/privacy/manifest.jsonl"}, P.Tier.THIRD),
    ("terminal", {"command": "git -C /workspace/zer0 status"}, P.Tier.PERSONAL),
    ("search_files", {"path": "/workspace/zer0", "pattern": "x"}, P.Tier.PERSONAL),
])
def test_tool_tier(tool, args, tier):
    assert R.tool_tier(tool, args) is tier


@pytest.mark.parametrize("text,tier", [
    ("plain text about routing", P.Tier.PUBLIC),
    ("key sk-ant-api03-" + "a" * 40, P.Tier.SECRET),
    ("-----BEGIN OPENSSH PRIVATE KEY-----", P.Tier.SECRET),
    ("my SSN is 123-45-6789", P.Tier.SECRET),
    ("card 4111 1111 1111 1111", P.Tier.SECRET),
    ("order 4111 1111 1111 1112", P.Tier.PUBLIC),
    ("password = hunter2hunter", P.Tier.SECRET),
])
def test_content_tier(text, tier):
    assert R.content_tier(text) is tier


def test_request_text_skips_tool_schemas():
    req = {"model": "m", "tools": [{"description": "sk-ant-api03-" + "b" * 40}],
           "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]}
    assert R.content_tier(R.request_text(req)) is P.Tier.PUBLIC


def test_cli_exit_codes(capsys):
    assert R.main(["decide", "--tier", "personal", "--task", "agentic"]) == 0
    assert R.main(["decide", "--tier", "personal", "--task", "agentic", "--down", "paid"]) == 4
    assert R.main(["decide", "--tier", "t4"]) == 3


# ---------------------------------------------------------------- Hermes adapter
def _load_adapter(tmp_path, monkeypatch, cfg: dict | None):
    home = tmp_path / "home"
    home.mkdir()
    if cfg is not None:
        (home / "privacy-route.json").write_text(json.dumps(cfg))
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setitem(sys.modules, "hermes_constants", None)  # force the HERMES_HOME fallback
    spec = importlib.util.spec_from_file_location(
        f"privacy_route_adapter_{id(tmp_path)}", ROOT / "adapters/hermes-privacy-route/__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, home


@pytest.fixture
def adapter(tmp_path, monkeypatch):
    mod, home = _load_adapter(tmp_path, monkeypatch, {"floor": "personal", "kernel": str(ROOT / "src")})
    monkeypatch.setattr(mod.R, "router_models", lambda *a, **k: set(R.LANE_MODEL.values()))
    return mod, home


def test_adapter_routes_personal_agentic_to_paid(adapter):
    mod, home = adapter
    out = mod.on_llm_request(request={"model": "vllm-sr/local", "tools": [{}], "messages": [{"role": "user", "content": "hi"}]},
                             session_id="s1")
    assert out["request"]["model"] == "vllm-sr/private"
    row = json.loads((home / "logs/privacy-route.jsonl").read_text().splitlines()[-1])
    assert row["decision"] == "use" and row["tier"] == "personal" and "hi" not in json.dumps(row)


def test_adapter_taint_moves_session_to_local(adapter):
    mod, _ = adapter
    assert mod.on_pre_tool_call("read_file", {"path": "/mnt/zer0models/sft-svlm/data/datasets/x"}, session_id="s2") is None
    out = mod.on_llm_request(request={"tools": [{}], "messages": []}, session_id="s2")
    assert out["request"]["model"] == "vllm-sr/local"
    other = mod.on_llm_request(request={"tools": [{}], "messages": []}, session_id="s3")
    assert other["request"]["model"] == "vllm-sr/private"


def test_adapter_blocks_secret_reads(adapter):
    mod, _ = adapter
    r = mod.on_pre_tool_call("read_file", {"path": "/mnt/zer0models/store/sealed/ssn.pdf"}, session_id="s4")
    assert r["action"] == "block"


def test_adapter_secret_in_messages_refuses(adapter):
    mod, _ = adapter
    out = mod.on_llm_request(request={"messages": [{"role": "user", "content": "ghp_" + "c" * 36}]}, session_id="s5")
    assert out["request"]["model"] == R.REFUSE_MODEL


def test_adapter_errors_refuse(adapter, monkeypatch):
    mod, _ = adapter
    monkeypatch.setattr(mod.R, "router_models", lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    out = mod.on_llm_request(request={"messages": []}, session_id="s6")
    assert out["request"]["model"] == "vllm-sr/o18-refused"


def test_adapter_without_kernel_fails_closed(tmp_path, monkeypatch):
    for name in [m for m in sys.modules if m == "z0int" or m.startswith("z0int.")]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.setattr(sys, "path", [p for p in sys.path if not Path(p, "z0int").is_dir()])
    mod, _ = _load_adapter(tmp_path, monkeypatch, {"kernel": str(tmp_path / "missing")})
    assert mod.R is None
    assert mod.on_llm_request(request={"messages": []})["request"]["model"] == "vllm-sr/o18-refused"
    assert mod.on_pre_tool_call("read_file", {"path": "/usr/x"})["action"] == "block"
