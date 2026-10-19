"""Offline contract tests; fake models are never benchmark evidence."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from unittest.mock import patch

import pytest

from z0int.cli import main
from z0int.turbo import adapters, cli, runtime
from z0int.turbo.graph import compile_graph, validate_jobs


@pytest.fixture(autouse=True)
def private_home(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path/"home"))
    monkeypatch.setenv("GROQ_API_KEY", "test-not-a-secret")


def plan(lanes=("fast",)):
    return {"jobs": [{"id": "job"+str(i), "lane": lane, "prompt": "Use context", "depends_on": []}
                     for i, lane in enumerate(lanes)], "acceptance": ["independent evidence"]}


def result(provider="groq", text="candidate", known=True):
    return {"provider": provider, "model": "test", "text": text,
            "input_tokens": 10 if known else None, "output_tokens": 5 if known else None,
            "wall_seconds": .1, "billing": "test_only"}


def test_graph_compiles_canonical_contract():
    doc = compile_graph(plan(("fast", "code")), "turbo-1234567890abcdef", 1000, 30)
    assert doc["policies"]["fanIn"] == "all"
    assert len(doc["intentGraph"]["nodes"]) == 4
    assert doc["constraints"]["budgets"]["tokens"] == 1000


@pytest.mark.parametrize("change", ["cycle", "lane", "duplicate", "shell", "no_acceptance", "reserved"])
def test_bad_graph_rejected(change):
    p = plan(("fast", "fast"))
    if change == "cycle": p["jobs"][0]["depends_on"] = ["job1"]
    if change == "lane": p["jobs"][0]["lane"] = "shell"
    if change == "duplicate": p["jobs"][1]["id"] = "job0"
    if change == "shell": p["jobs"][0]["check"] = "rm -rf /"
    if change == "no_acceptance": p["acceptance"] = []
    if change == "reserved": p["jobs"][0]["id"] = "planner"
    with pytest.raises(ValueError): validate_jobs(p)


def test_mode_default_expiry_and_off(capsys):
    assert cli.mode("status")["enabled"] is False
    assert main(["mode", "turbo", "on", "--duration", "1s", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["scope"] == "duration"
    with patch.object(time, "time", return_value=time.time()+2):
        assert cli.mode("status")["enabled"] is False
    assert cli.mode("off")["enabled"] is False


@pytest.mark.parametrize("value", ["0", "-1m", "2h", "1;false", "abc"])
def test_invalid_duration(value):
    with pytest.raises(ValueError): runtime.duration(value)


def test_unknown_id_and_path_traversal():
    with pytest.raises(ValueError): runtime.status("../../password")
    with pytest.raises(ValueError): runtime.stop("turbo-1234567890abcdef")


def test_cancel_and_deadline():
    r = runtime.Runner("test")
    runtime.stop(r.run_id)
    with pytest.raises(adapters.Halt, match="cancelled"):
        adapters.check(r.deadline, r.stop)
    with pytest.raises(adapters.Halt, match="deadline"):
        adapters.check(time.monotonic()-1, r.folder/"absent")


def test_budget_is_before_dispatch():
    r = runtime.Runner("test", max_tokens=256, max_requests=1)
    called = []
    with pytest.raises(adapters.Halt, match="budget"):
        r.call(lambda: called.append(True), reserve=257)
    assert not called
    r.call(lambda: called.append(True), reserve=1)
    with pytest.raises(adapters.Halt): r.call(lambda: None)
    assert len(called) == 1


def test_429_host_cooldown():
    stop = runtime.root()/"absent"
    with pytest.raises(adapters.ProviderError):
        with runtime.Admission("groq", time.monotonic()+1, stop):
            raise adapters.ProviderError(429, 60)
    saved = json.loads((runtime.root()/"admit-groq.json").read_text())
    assert saved["last_status"] == 429 and saved["next_at"] > time.time()+50
    with pytest.raises(adapters.Halt, match="deadline"):
        with runtime.Admission("groq", time.monotonic()+.1, stop): pass


def test_same_provider_serialized():
    active = 0
    peak = 0
    lock = threading.Lock()
    def run():
        nonlocal active, peak
        with runtime.Admission("groq", time.monotonic()+3, runtime.root()/"absent"):
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(.03)
            with lock: active -= 1
            runtime.save(runtime.root()/"admit-groq.json", {"next_at": 0})
    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert peak == 1


def test_private_artifacts():
    r = runtime.Runner("test")
    assert (r.folder.stat().st_mode & 0o777) == 0o700
    for path in r.folder.iterdir():
        assert path.stat().st_mode & 0o077 == 0


def test_real_process_cancel_is_scoped(tmp_path):
    stop = tmp_path/"stop"
    timer = threading.Timer(.2, stop.touch)
    timer.start()
    try:
        with pytest.raises(adapters.Halt, match="cancelled"):
            adapters.process([sys.executable, "-c", "import time;time.sleep(20)"], tmp_path,
                             tmp_path/"log", time.monotonic()+3, stop)
    finally:
        timer.join()
    assert time.monotonic() > 0


def test_worktree_isolation(tmp_path):
    repo = tmp_path/"repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    (repo/"example").write_text("base")
    subprocess.run(["git", "-C", str(repo), "add", "example"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=test", "-c", "user.email=test@example.test", "commit", "-m", "base"], check=True, capture_output=True)
    other = runtime.worktree(repo, tmp_path/"other")
    (other/"example").write_text("candidate")
    assert (repo/"example").read_text() == "base"


def test_verifier_rejection_and_unknown_accounting():
    r = runtime.Runner("test", normal=True)
    def native(prompt, role, *args):
        return result("openai-codex", json.dumps({"verified": False, "reason": "missing evidence"})
                      if role == "verifier" else "uncertain", known=False)
    with patch.object(adapters, "native", native), patch.object(adapters, "load_keys"):
        receipt = r.execute()
    assert receipt["state"] == "failed"
    assert receipt["output_tok_s_wall"] is None
    assert receipt["cost_usd"] is None
    assert receipt["providers"]["openai-codex"]["output_tokens"] is None


def test_failed_owner_check_overrides_verifier(tmp_path):
    r = runtime.Runner("test", normal=True, checks=[[sys.executable, "-c", "raise SystemExit(1)"]])
    with patch.object(adapters, "native", return_value=result("openai-codex", '{"verified":true,"reason":"model says done"}')):
        receipt = r.execute()
    assert receipt["quality"]["verified"] is False
    assert receipt["quality"]["deterministic_checks"][0]["passed"] is False


def test_native_identity_and_accounting(tmp_path):
    raw = '\n'.join(json.dumps(x) for x in [
        {"type":"system","subtype":"init","model":"claude-opus-5-5"},
        {"type":"result","exit_code":0,"text":"{}","tokens":{"input":15,"output":12}}])
    with patch.object(adapters,"process",return_value=raw) as proc:
        got = adapters.native("plan", "planner", tmp_path, tmp_path, time.monotonic()+10, tmp_path/"stop")
        assert got["output_tokens"] == 12
        assert "--safe-mode" in proc.call_args.args[0]
    with patch.object(adapters,"process",return_value=raw.replace("claude-opus-5-5","other-model")):
        with pytest.raises(RuntimeError, match="identity"):
            adapters.native("plan", "planner", tmp_path, tmp_path, time.monotonic()+10,tmp_path/"stop")


def test_sse_and_secret_free_headers(tmp_path):
    class Response:
        headers = {"x-ratelimit-limit-tokens":"8000", "Authorization":"do-not-save"}
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def __iter__(self):
            return iter([b'data: {"choices":[{"delta":{"content":"ok"}}]}\n',
                         b'data: {"choices":[],"usage":{"prompt_tokens":2,"completion_tokens":1}}\n', b'data: [DONE]\n'])
    from types import SimpleNamespace
    with patch.object(adapters.urllib.request,"urlopen",return_value=Response()) as req:
        got=adapters.chat(SimpleNamespace(provider="groq",model="test"),"hello",32,time.monotonic()+2,tmp_path/"stop")
    assert got["text"] == "ok" and got["output_tokens"] == 1
    assert "Authorization" not in got["rate_headers"]
    assert req.call_args.args[0].headers["User-agent"] == "z0int-turbo/0.1"


def test_parallel_real_scheduler_with_fake_inference():
    from kerdoios.types import ResourceOffer, CapabilityProfile, Capacity, Economics, Telemetry
    offers=[ResourceOffer(id=p+"/test", provider=p, model="test", resource_type="llm", local=False,
                         capabilities=CapabilityProfile(), capacity=Capacity(), economics=Economics(), telemetry=Telemetry()) for p in ["groq","cerebras"]]
    def choose(offers, coding=False, excluded=(), measurements=None):
        return next((o for o in offers if o.provider not in excluded),None)
    def native(prompt, role, *args):
        return result("anthropic" if role=="planner" else "openai-codex",
                      json.dumps(plan(("fast","fast"))) if role=="planner" else '{"verified":true,"reason":"fake test only"}')
    def chat(offer,*args):
        time.sleep(.15)
        return result(offer.provider)
    r=runtime.Runner("offline test",max_tokens=10000)
    with patch("kerdoios.turbo.discover_turbo_offers",return_value=offers), patch("kerdoios.turbo.choose_turbo_offer",side_effect=choose), patch.object(adapters,"native",side_effect=native), patch.object(adapters,"chat",side_effect=chat):
        receipt=r.execute()
    assert receipt["state"] == "completed"
    assert receipt["peak_parallelism"] == 2
    assert {receipt["results"][j]["provider"] for j in ["job0","job1"]} == {"groq","cerebras"}


def test_checks_not_shell_and_cli_errors(capsys):
    assert main(["turbo","status","../secret","--json"]) == 2
    assert json.loads(capsys.readouterr().out)["verified"] is False
