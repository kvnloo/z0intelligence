"""OMP / OMO capture through the z0-owned bridge (oh-my-pi#109, z0int#62).

The resident bridge worker (``z0int.bridge.runtime``) emits the C1 record family for every OMP/OMO user turn:
``turn_open`` writes ``z0int.omp.opportunity_record.v0`` through the detached capture core and ``turn_close``
writes ``z0int.omp.turn_outcome.v0`` joined on the canonical turn key. Also covered here: no prompt text in
``bridge.jsonl``, the cognition-shadow hygiene (no rows for a dead backend, a structured ``actual_tool`` and no
tool input in anything persisted), protocol parity with the canonical a9cbbed tree that the other OMP
extensions keep running, and the pinned-checkout install script.

No model server, no network, no live state: every test runs against its own ``Z0INT_HOME``.
"""

from __future__ import annotations

import json
import os
import shutil
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

import pytest

from z0int import harness_capture as hc
from z0int.bridge import runtime as rtmod
from z0int.bridge.protocol import BRIDGE_PROTOCOL
from z0int.bridge.runtime import BridgeRuntime
from z0int.cognition.adapters.local_slm import ToolDecision
from z0int.cognition.shadow import run_shadow
from z0int.harness_id import turn_key, turn_key_from_alias

ROOT = Path(__file__).resolve().parents[1]
BRIDGE_TS = ROOT / "omp-extensions" / "z0int-bridge" / "index.ts"
CANONICAL = Path(os.environ.get("Z0INT_CANONICAL_REPO", "/home/kvn/tmp/z0int-canonical"))
CANONICAL_SHA = "a9cbbed"
PROMPT = "please refactor the parser in SECRET_PROMPT_MARKER_42 module"
PREFLIGHT = {
    "capability_id": "coding.next_action",
    "route": "model",
    "label": "VERIFY",
    "p": 0.4,
    "baseline_input_tokens": 100,
    "baseline_output_tokens": 50,
    "estimated_frontier_tokens_avoided": 0,
}


@contextmanager
def z0home(path: Path, **extra: str | None):
    keys = {"Z0INT_HOME": str(path), **extra}
    prev = {k: os.environ.get(k) for k in keys}
    for k, v in keys.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        yield path
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@pytest.fixture
def home(tmp_path):
    with z0home(tmp_path / "z0", Z0INT_CAPTURE=None, Z0INT_CAPTURE_PRIVACY=None), mock.patch.object(
        rtmod, "preflight", lambda prompt: dict(PREFLIGHT)
    ), mock.patch.object(rtmod, "kerdoios_plan", lambda *a, **k: None), mock.patch.object(
        rtmod, "kerdoios_record", lambda **k: None
    ):
        yield tmp_path / "z0"


def inline_spawn(argv, job):
    """The detached child's job, run in-process (argv is hc.child_argv(harness))."""
    hc.record_opportunity(argv[argv.index("--harness") + 1], job["payload"], job["ctx"])


def rows(home: Path, harness: str, name: str) -> list[dict]:
    path = home / "state" / harness / name
    return [json.loads(line) for line in path.read_text().splitlines()] if path.is_file() else []


def git_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
    subprocess.run(["git", "init", "-q", str(path)], check=True, env=env)
    (path / "README.md").write_text("synthetic fixture\n")
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True, env=env)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "init"], check=True, env=env)
    return path


def open_close(rt: BridgeRuntime, trace: str, session: str, **open_kw):
    opened = rt.turn_open(trace_id=trace, session_id=session, prompt=PROMPT, omp_pid=4242, writer_generation=1,
                          **open_kw)
    closed = rt.turn_close(trace_id=trace, session_id=session, omp_pid=4242, measured=12, input_tokens=4,
                           output_tokens=8, execution_completed=True, verified_success=None,
                           harness=open_kw.get("harness", "omp"), writer_generation=1)
    return opened, closed


# --------------------------------------------------------------------------- 1. opportunity via the detached core
def test_turn_open_with_cwd_writes_an_omp_opportunity_through_the_detached_core(home, tmp_path):
    repo = git_repo(tmp_path / "task-repo")
    rt = BridgeRuntime(generation=1, instance_id="t", build_id="b")  # the real detached spawn
    opened = rt.turn_open(trace_id="trace-open-1", session_id="omp-sess-1", prompt=PROMPT, omp_pid=4242,
                          writer_generation=1, cwd=str(repo), model="stub/model-a")
    assert opened["ok"] is True
    rt.close_capture()
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and not rows(home, "omp", "opportunities.jsonl"):
        time.sleep(0.2)
    (opp,) = rows(home, "omp", "opportunities.jsonl")
    assert opp["schema"] == "z0int.omp.opportunity_record.v0"
    assert opp["harness"] == "omp"
    assert opp["turn_key"] == turn_key("omp", "omp-sess-1", "trace-open-1")
    assert opp["turn_key"] == turn_key_from_alias("omp.bridge_trace", "trace-open-1", session_id="omp-sess-1")
    assert opp["cohort"] == "interactive"
    assert opp["model_id"] == "stub/model-a"
    assert opp["repo"] == str(repo.resolve())  # the task cwd, never the worker's process cwd
    assert opp["opportunity"]["intent"]["request"] is None  # content_free by default
    assert PROMPT not in json.dumps(opp)


def test_bridge_reply_latency_stays_within_its_bound_over_200_turns(home, tmp_path):
    """The capture work (opportunity build, outcome rows) is off the reply path: a slow capture child must
    not move the turn_open/turn_close reply. Bound: the capture-off p95 of the same run + 5 ms."""
    seen = []

    def slow_spawn(argv, job):
        time.sleep(0.002)
        seen.append(job["ctx"]["turn_key"])

    def run(rt, tag):
        lat = []
        for i in range(200):
            t0 = time.perf_counter()
            open_close(rt, f"{tag}-{i}", f"{tag}-sess", cwd=str(tmp_path))
            lat.append((time.perf_counter() - t0) * 1000.0)
        return lat

    # Each run gets a fresh home: the bridge's own ledgers grow per turn, so a shared home would bias the 2nd run.
    with z0home(tmp_path / "off", Z0INT_CAPTURE="0"):
        off = run(BridgeRuntime(generation=1, instance_id="off", build_id="b", capture_spawn=slow_spawn), "off")
    on_home = tmp_path / "on"
    with z0home(on_home):
        rt = BridgeRuntime(generation=1, instance_id="on", build_id="b", capture_spawn=slow_spawn)
        on = run(rt, "on")
        rt.close_capture()
    p95 = lambda xs: statistics.quantiles(xs, n=20)[18]  # noqa: E731
    assert p95(on) <= p95(off) + 5.0, (p95(on), p95(off))
    assert len(seen) == 200 and hc.drop_count("omp", on_home) == 0
    assert len(rows(on_home, "omp", "outcomes.jsonl")) == 200


# --------------------------------------------------------------------------- 2. outcome joined by turn_key
def test_turn_close_writes_an_outcome_joined_by_turn_key_and_never_claims_verification(home, tmp_path):
    rt = BridgeRuntime(generation=1, instance_id="t", build_id="b", capture_spawn=inline_spawn)
    open_close(rt, "trace-join", "omp-sess-2", cwd=str(tmp_path))
    rt.turn_open(trace_id="trace-aborted", session_id="omp-sess-2", prompt="second", omp_pid=4242,
                 writer_generation=1, cwd=str(tmp_path))
    rt.turn_close(trace_id="trace-aborted", session_id="omp-sess-2", omp_pid=4242, execution_completed=False,
                  verified_success=True, writer_generation=1)  # an operator claim is not a verification
    rt.close_capture()
    opps = {r["trace_id"]: r for r in rows(home, "omp", "opportunities.jsonl")}
    outs = {r["trace_id"]: r for r in rows(home, "omp", "outcomes.jsonl")}
    assert set(opps) == set(outs) == {"trace-join", "trace-aborted"}
    for trace in opps:
        assert outs[trace]["schema"] == "z0int.omp.turn_outcome.v0"
        assert outs[trace]["turn_key"] == opps[trace]["turn_key"]
        assert outs[trace]["work_item_id"] == opps[trace]["work_item_id"]
        assert outs[trace]["verified_success"] is None  # null until C2 verifies
    assert outs["trace-join"]["execution_completed"] is True and outs["trace-join"]["ended"] == "completed"
    assert outs["trace-aborted"]["execution_completed"] is False and outs["trace-aborted"]["ended"] != "completed"
    assert opps["trace-join"]["work_item_id"] != opps["trace-aborted"]["work_item_id"]
    kinds = {r["kind"] for r in rows(home, "omp", "failures.jsonl")}
    assert "missing_verifier" in kinds  # OMP has no verifier at capture time: said explicitly


def test_outcome_model_id_matches_the_opportunity_model_id(home, tmp_path):
    """The shim sends ctx.model as provider/id at open; the close frame splits provider and model."""
    rt = BridgeRuntime(generation=1, instance_id="t", build_id="b", capture_spawn=inline_spawn)
    rt.turn_open(trace_id="trace-model", session_id="s-model", prompt=PROMPT, omp_pid=1, writer_generation=1,
                 cwd=str(tmp_path), model="sandbox/bridge-fake")
    rt.turn_close(trace_id="trace-model", session_id="s-model", omp_pid=1, provider="sandbox", model="bridge-fake",
                  writer_generation=1)
    rt.close_capture()
    (opp,) = rows(home, "omp", "opportunities.jsonl")
    (out,) = rows(home, "omp", "outcomes.jsonl")
    assert opp["model_id"] == out["model_id"] == "sandbox/bridge-fake"


def test_omo_turns_land_in_their_own_record_family(home, tmp_path):
    rt = BridgeRuntime(generation=1, instance_id="t", build_id="b", capture_spawn=inline_spawn)
    open_close(rt, "trace-omo", "omo-sess", cwd=str(tmp_path), harness="omo")
    rt.close_capture()
    (opp,) = rows(home, "omo", "opportunities.jsonl")
    (out,) = rows(home, "omo", "outcomes.jsonl")
    assert opp["schema"] == "z0int.omo.opportunity_record.v0" and out["schema"] == "z0int.omo.turn_outcome.v0"
    assert opp["turn_key"] == out["turn_key"] == turn_key("omo", "omo-sess", "trace-omo")
    assert not rows(home, "omp", "opportunities.jsonl")


# --------------------------------------------------------------------------- 3. subagent cohort
def test_a_subagent_turn_is_agent_cohort_at_capture(home, tmp_path):
    rt = BridgeRuntime(generation=1, instance_id="t", build_id="b", capture_spawn=inline_spawn)
    open_close(rt, "trace-main", "main-sess", cwd=str(tmp_path), agent_kind="main")
    open_close(rt, "trace-sub", "sub-sess", cwd=str(tmp_path), agent_kind="sub", parent_id="Main")
    open_close(rt, "trace-sub2", "sub-sess-2", cwd=str(tmp_path), parent_id="0-Task")  # parent id alone
    rt.close_capture()
    opps = {r["trace_id"]: r for r in rows(home, "omp", "opportunities.jsonl")}
    outs = {r["trace_id"]: r for r in rows(home, "omp", "outcomes.jsonl")}
    assert opps["trace-main"]["cohort"] == outs["trace-main"]["cohort"] == "interactive"
    for trace in ("trace-sub", "trace-sub2"):
        assert opps[trace]["cohort"] == "agent" and outs[trace]["cohort"] == "agent"


# --------------------------------------------------------------------------- 4. missing cwd
def test_missing_cwd_gives_unknown_facts_and_the_gate_never_acts(home):
    rt = BridgeRuntime(generation=1, instance_id="t", build_id="b", capture_spawn=inline_spawn)
    rt.turn_open(trace_id="trace-nocwd", session_id="s-nocwd", prompt=PROMPT, omp_pid=1, writer_generation=1)
    rt.close_capture()
    (opp,) = rows(home, "omp", "opportunities.jsonl")
    assert opp["gate"] in ("OBSERVE", "ASK")
    assert not opp["opportunity"]["state"]["claims"]
    assert any(u["key"] == "git" and u["blocking"] for u in opp["opportunity"]["state"]["unknowns"])
    assert not any(a["legal"] for a in opp["opportunity"]["action_space"] if a["kind"] == "ACT")
    assert opp["repo"] is None


# --------------------------------------------------------------------------- 5. bridge.jsonl
def test_bridge_jsonl_rows_contain_no_prompt_text(home, tmp_path):
    rt = BridgeRuntime(generation=1, instance_id="t", build_id="b", capture_spawn=inline_spawn)
    open_close(rt, "trace-priv", "s-priv", cwd=str(tmp_path))
    rt.close_capture()
    stream = (home / "stream" / "bridge.jsonl").read_text()
    (row,) = [json.loads(line) for line in stream.splitlines()]
    assert "prompt" not in row
    assert "SECRET_PROMPT_MARKER_42" not in stream
    for path in home.rglob("*"):
        if path.is_file():
            assert "SECRET_PROMPT_MARKER_42" not in path.read_text(errors="replace"), path


# --------------------------------------------------------------------------- 6. cognition-shadow hygiene
TOOL_INPUT_MARKER = "SECRET_TOOL_INPUT_MARKER_7"


def shadow_payload(**over):
    payload = {
        "op": "cognition_shadow",
        "trace_id": "shadow-trace",
        "session_id": "omp-sess",
        "state": f'OMP tool_call: bash\ninput: {{"command": "cat {TOOL_INPUT_MARKER}"}}',
        "actions": [
            {"action_id": "bash", "kind": "tool", "tool": "bash", "risk_class": "write"},
            {"action_id": "read", "kind": "tool", "tool": "read", "risk_class": "read"},
        ],
        "granted_capabilities": ["bash", "read"],
        "authority": ["read", "write"],
        "facts": {"tool_name": "bash", "tool_call_id": "call-1"},
        "actual_tool": {"name": "bash", "risk_class": "write", "input": {"command": TOOL_INPUT_MARKER}},
        "risk_class": "write",
        "shadows": ["functiongemma_270m"],
    }
    payload.update(over)
    return payload


class _Backend:
    def __init__(self, action=None, raises=None):
        self.action, self.raises = action, raises

    def decide(self, request):
        if self.raises is not None:
            raise self.raises
        return ToolDecision(backend="fake", model="fake-model", revision="r", selected_action=self.action,
                            arguments={}, confidence=0.8, distribution=None, latency_ms=2.0, abstained=False,
                            candidate_action_count=request.legal.candidate_count)


class _Registry:
    def __init__(self, backend):
        self.backend = backend

    def served(self):
        return ("functiongemma_270m",)

    def backend_for(self, model_id):
        return self.backend


def test_dead_backend_writes_no_shadow_row_and_counts_backend_unavailable(home):
    # The real transport against a closed loopback port: urllib raises URLError (connection refused).
    (home / "config").mkdir(parents=True)
    (home / "config" / "serving.json").write_text(json.dumps({"endpoints": [
        {"model_id": "functiongemma_270m", "base_url": "http://127.0.0.1:11558/v1", "runtime": "llama.cpp"}]}))
    first = run_shadow(shadow_payload(), timeout_s=5)
    # And a backend that raises URLError itself.
    second = run_shadow(shadow_payload(), registry=_Registry(_Backend(raises=urllib.error.URLError("refused"))))
    for result in (first, second):
        assert result["ok"] is True and result["selected_action"] is None and result["executed_action"] is None
        assert result["receipts_path"] is None
        assert result["backend_unavailable"] == 1
    assert not (home / "shadow" / "cognition-shadow.jsonl").exists()
    counters = json.loads((home / "shadow" / "cognition-shadow-counters.json").read_text())
    assert counters["backend_unavailable"] == 2


def test_served_backend_writes_a_row_with_a_structured_actual_tool_and_no_tool_input(home):
    result = run_shadow(shadow_payload(), registry=_Registry(_Backend(action="read")))
    assert result["ok"] is True and result["backend_unavailable"] == 0
    text = Path(result["receipts_path"]).read_text()
    (row,) = [json.loads(line) for line in text.splitlines()]
    assert row["actual_tool"] == {"name": "bash", "risk_class": "write"}
    assert [r["selected_action"] for r in row["shadow"]] == ["read"]
    assert TOOL_INPUT_MARKER not in text and "input:" not in text
    for path in home.rglob("*"):
        if path.is_file():
            assert TOOL_INPUT_MARKER not in path.read_text(errors="replace"), path


# --------------------------------------------------------------------------- 7. protocol parity with a9cbbed
def _bun(expr: str, z0: Path) -> object:
    script = f"const m = await import({json.dumps(BRIDGE_TS.as_posix())}); console.log(JSON.stringify(await ({expr})));"
    proc = subprocess.run(["bun", "-e", script], capture_output=True, text=True, timeout=60, check=True,
                          env={**os.environ, "Z0INT_HOME": str(z0)}, cwd=str(z0))
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def canonical_tree(tmp_path_factory):
    """The canonical a9cbbed tree, exported file by file with `git show` (never the dirty checkout)."""
    if not (CANONICAL / ".git").exists():
        pytest.skip(f"UNSUPPORTED here: canonical repo {CANONICAL} is absent; parity is unverified")
    out = tmp_path_factory.mktemp("a9cbbed")
    names = subprocess.run(["git", "-C", str(CANONICAL), "ls-tree", "-r", "--name-only", CANONICAL_SHA, "src/"],
                           capture_output=True, text=True, check=True).stdout.split()
    for name in names:
        if not name.endswith((".py", ".json")):
            continue
        blob = subprocess.run(["git", "-C", str(CANONICAL), "show", f"{CANONICAL_SHA}:{name}"],
                              capture_output=True, check=True).stdout
        (out / name).parent.mkdir(parents=True, exist_ok=True)
        (out / name).write_bytes(blob)
    return out


class Worker:
    def __init__(self, root: Path, z0: Path, scratch: Path):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("Z0INT_", "KERDOIOS_", "PYTHONPATH"))}
        env.update(PYTHONPATH=str(root / "src") + os.pathsep + os.environ.get("PYTHONPATH", ""), Z0INT_ROOT=str(root),
                   Z0INT_HOME=str(z0), KERDOIOS_ROOT=str(scratch / "no-kerdoios"),
                   KERDOIOS_PYTHON=str(scratch / "no-python"))
        self.proc = subprocess.Popen([sys.executable, "-u", "-m", "z0int.bridge.worker", "--generation", "1"],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     text=True, env=env, cwd=str(scratch))

    def rpc(self, frame: dict) -> dict:
        self.proc.stdin.write(json.dumps(frame) + "\n")
        self.proc.stdin.flush()
        return json.loads(self.proc.stdout.readline())

    def stop(self):
        self.rpc({"id": "bye", "op": "shutdown"})
        self.proc.wait(timeout=30)


def _check_responses(responses: dict) -> None:
    """What a z0int-bridge shim (either tree) requires of a worker reply."""
    for req_id, resp in responses.items():
        assert resp.get("id") == req_id, resp
        assert resp.get("ok") is True, resp
    hello = responses["hello"]
    assert hello["protocol"] == BRIDGE_PROTOCOL and isinstance(hello["generation"], int)
    assert isinstance(hello["instance_id"], str) and isinstance(hello["build_id"], str)


# The frames the a9cbbed shim sends (a9cbbed omp-extensions/z0int-bridge/index.ts: before_agent_start / turn_end).
OLD_FRAMES = [
    {"id": "hello", "op": "hello", "bridge_generation": 1},
    {"id": "check", "op": "self_check", "bridge_generation": 1},
    {"id": "open", "bridge_generation": 1, "op": "turn_open", "trace_id": "old-trace", "session_id": "old-sess",
     "omp_pid": 77, "payload": {"prompt": "old shim prompt", "session_id": "old-sess", "omp_pid": 77}},
    {"id": "close", "bridge_generation": 1, "op": "turn_close", "trace_id": "old-trace", "session_id": "old-sess",
     "omp_pid": 77, "payload": {"trace_id": "old-trace", "session_id": "old-sess", "omp_pid": 77, "measured": 3,
                                "input_tokens": 1, "output_tokens": 2, "execution_completed": True,
                                "source": "bridge_turn_end", "provider": None, "model": None}},
    {"id": "status", "op": "status", "bridge_generation": 1},
]


@pytest.mark.skipif(shutil.which("bun") is None, reason="bun not installed: parity UNVERIFIED")
def test_protocol_parity_with_the_canonical_a9cbbed_worker(canonical_tree, tmp_path):
    z0 = tmp_path / "z0"
    z0.mkdir()
    ctx = {"sessionId": "new-sess", "cwd": str(tmp_path), "parentId": None, "agentKind": "main",
           "model": "stub/model-a"}
    new_open = _bun(f"m.turnOpenFrame({{traceId:'new-trace', sessionId:'new-sess', pid: 88}}, 'new prompt', "
                    f"{json.dumps(ctx)}, 'omp')", z0)
    new_close = _bun("m.turnCloseFrame({traceId:'new-trace', sessionId:'new-sess', pid: 88}, 'agent_end', "
                     "{measured: 5, input_tokens: 2, output_tokens: 3, execution_completed: true}, 'omp')", z0)
    assert new_open["op"] == "turn_open" and new_open["payload"]["cwd"] == str(tmp_path)
    assert new_open["payload"]["harness"] == "omp" and new_close["payload"]["harness"] == "omp"
    new_frames = [OLD_FRAMES[0], OLD_FRAMES[1], {"id": "open", "bridge_generation": 1, **new_open},
                  {"id": "close", "bridge_generation": 1, **new_close}, OLD_FRAMES[4]]

    for label, tree, frames in (("a9cbbed worker, new frames", canonical_tree, new_frames),
                                ("new worker, a9cbbed frames", ROOT, OLD_FRAMES)):
        home = tmp_path / label.replace(" ", "_").replace(",", "")
        worker = Worker(tree, home, tmp_path)
        try:
            responses = {f["id"]: worker.rpc(f) for f in frames}
        finally:
            worker.stop()
        _check_responses(responses)

    # The new worker captured the a9cbbed shim's turn: no cwd, so unknown facts, but joined rows.
    new_home = tmp_path / "new_worker_a9cbbed_frames"
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and not rows(new_home, "omp", "opportunities.jsonl"):
        time.sleep(0.2)
    (opp,) = rows(new_home, "omp", "opportunities.jsonl")
    (out,) = rows(new_home, "omp", "outcomes.jsonl")
    assert opp["turn_key"] == out["turn_key"] == turn_key("omp", "old-sess", "old-trace")
    assert opp["gate"] != "ACT"


def _read_current(tree: Path, z0: Path) -> dict:
    code = ("import json; from z0int.bridge import generation as g; "
            "print(json.dumps({'cur': g.read_current(), 'stale1': g.is_stale(1), 'stale9': g.is_stale(9)}))")
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True,
                          env={**os.environ, "PYTHONPATH": str(tree / "src"), "Z0INT_HOME": str(z0)})
    return json.loads(proc.stdout)


def _publish(tree: Path, z0: Path) -> None:
    code = "from z0int.bridge import generation as g; g.publish_current(generation=3, instance_id='i', build_id='b')"
    subprocess.run([sys.executable, "-c", code], check=True,
                   env={**os.environ, "PYTHONPATH": str(tree / "src"), "Z0INT_HOME": str(z0)})


@pytest.mark.skipif(shutil.which("bun") is None, reason="bun not installed: parity UNVERIFIED")
def test_bridge_current_json_is_readable_across_both_trees(canonical_tree, tmp_path):
    writers = {"new-python": lambda z0: _publish(ROOT, z0), "a9cbbed-python": lambda z0: _publish(canonical_tree, z0),
               "new-shim": lambda z0: _bun("(m.publishCurrent({generation: 3, instanceId: 'i', buildId: 'b'}), true)", z0)}
    for name, write in writers.items():
        z0 = tmp_path / name
        (z0 / "runtime").mkdir(parents=True)
        write(z0)
        for tree in (ROOT, canonical_tree):
            seen = _read_current(tree, z0)
            assert seen["cur"]["protocol"] == BRIDGE_PROTOCOL and seen["cur"]["generation"] == 3, (name, tree)
            assert seen["stale1"] is True and seen["stale9"] is False, (name, tree)


# --------------------------------------------------------------------------- 9. pinned install script
INSTALL = ROOT / "scripts" / "omp_bridge_install.py"
EXT_FILES = ("omp-extensions/z0int-bridge/index.ts", "omp-extensions/local-cognition/index.ts",
             "omp-extensions/z0int-intelligence/index.ts", "omp-extensions/openjev/index.ts",
             "src/z0int/bridge/worker.py")


def _checkout(path: Path) -> Path:
    git_repo(path)
    for rel in EXT_FILES:
        (path / rel).parent.mkdir(parents=True, exist_ok=True)
        (path / rel).write_text("// fixture\n")
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True, env=env)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "ext"], check=True, env=env)
    return path


def _install(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(INSTALL), *args], capture_output=True, text=True)


def _links(ext: Path) -> dict[str, str]:
    return {p.name: os.readlink(p) for p in sorted(ext.iterdir()) if p.is_symlink()}


@pytest.fixture
def agent(tmp_path):
    old = _checkout(tmp_path / "canonical")
    pinned = _checkout(tmp_path / "pinned")
    ext = tmp_path / "agent" / "extensions"
    ext.mkdir(parents=True)
    for name in ("z0int-bridge", "local-cognition", "z0int-intelligence", "openjev"):
        (ext / name).symlink_to(old / "omp-extensions" / name, target_is_directory=True)
    return tmp_path / "agent", old, pinned


def test_install_dry_run_lists_only_the_bridge_and_local_cognition_retargets(agent):
    agent_dir, old, pinned = agent
    before = _links(agent_dir / "extensions")
    proc = _install("--target", str(pinned), "--agent-dir", str(agent_dir), "--dry-run")
    assert proc.returncode == 0, proc.stderr
    plan = json.loads(proc.stdout)
    retargets = {a["name"]: a for a in plan["actions"] if a["action"] == "retarget"}
    assert set(retargets) == {"z0int-bridge", "local-cognition"}
    assert retargets["z0int-bridge"]["to"] == str((pinned / "omp-extensions" / "z0int-bridge").resolve())
    assert retargets["z0int-bridge"]["from"] == str(old / "omp-extensions" / "z0int-bridge")
    assert _links(agent_dir / "extensions") == before  # nothing changed
    assert not list(agent_dir.glob("extensions.links.bak-*"))  # a dry run writes nothing


def test_install_writes_the_links_backup_first_and_is_idempotent(agent):
    agent_dir, old, pinned = agent
    before = _links(agent_dir / "extensions")
    proc = _install("--target", str(pinned), "--agent-dir", str(agent_dir))
    assert proc.returncode == 0, proc.stderr
    (backup,) = agent_dir.glob("extensions.links.bak-z0wiring-*")
    saved = json.loads(backup.read_text())
    assert {k: v["target"] for k, v in saved["links"].items()} == before  # the pre-change listing, every link
    after = _links(agent_dir / "extensions")
    assert after["z0int-bridge"] == str((pinned / "omp-extensions" / "z0int-bridge").resolve())
    assert after["local-cognition"] == str((pinned / "omp-extensions" / "local-cognition").resolve())
    assert after["z0int-intelligence"] == before["z0int-intelligence"]  # live routing left alone
    assert after["openjev"] == before["openjev"]
    stamp = backup.read_text()
    again = _install("--target", str(pinned), "--agent-dir", str(agent_dir))
    assert again.returncode == 0, again.stderr
    assert {a["action"] for a in json.loads(again.stdout)["actions"]} == {"unchanged"}
    assert backup.read_text() == stamp and _links(agent_dir / "extensions") == after


def test_install_refuses_a_dirty_target_checkout(agent):
    agent_dir, _, pinned = agent
    before = _links(agent_dir / "extensions")
    (pinned / "omp-extensions" / "z0int-bridge" / "index.ts").write_text("// local edit\n")
    for extra in ((), ("--dry-run",)):
        proc = _install("--target", str(pinned), "--agent-dir", str(agent_dir), *extra)
        assert proc.returncode != 0 and "dirty" in (proc.stderr + proc.stdout)
    assert _links(agent_dir / "extensions") == before
    assert not list(agent_dir.glob("extensions.links.bak-*"))


def test_install_touches_z0int_intelligence_only_with_include_routing(agent):
    agent_dir, _, pinned = agent
    proc = _install("--target", str(pinned), "--agent-dir", str(agent_dir), "--include-routing", "--dry-run")
    assert proc.returncode == 0, proc.stderr
    names = {a["name"] for a in json.loads(proc.stdout)["actions"] if a["action"] == "retarget"}
    assert names == {"z0int-bridge", "local-cognition", "z0int-intelligence"}
