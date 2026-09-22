"""Lifecycle safety for the z0intelligence service.

The service holds the whole GPU through a child that runs in its own session.
Two failure modes are dangerous, and both are pinned here:

* signalling a process we do not own, because a pidfile said so (pid reuse), and
* reporting "stopped" while an orphaned llama-server still holds the card.

So the tests check *which pids receive which signals*, not just that a function
returned ok.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from z0int.cognition import service as svc

REPO = Path(__file__).resolve().parents[1]
GGUF_DIR = "/mnt/zer0models/zer0-models/gguf"


# --- identity predicates ------------------------------------------------


def test_supervisor_argv_accepts_the_real_invocations():
    assert svc.is_supervisor_argv(["/v/bin/python", "-m", "z0int", "cognition", "serve"])
    assert svc.is_supervisor_argv(["/v/bin/python", "-m", "z0int", "cognition", "serve", "--port", "11500"])
    assert svc.is_supervisor_argv(["/usr/local/bin/z0int", "cognition", "serve"])
    assert svc.is_supervisor_argv(
        ["/v/bin/python", "-u", "-m", "z0int", "cognition", "serve", "--context", "4096"]
    )


def test_supervisor_argv_rejects_lookalikes():
    """Near misses must not be claimed: this predicate gates every signal."""
    assert not svc.is_supervisor_argv(["/v/bin/python", "-m", "z0int", "cognition", "decide"])
    assert not svc.is_supervisor_argv(["/v/bin/python", "-m", "z0int", "backends", "serve"])
    assert not svc.is_supervisor_argv(["/v/bin/python", "cognition", "serve"])  # no z0int anywhere
    assert not svc.is_supervisor_argv(["/v/bin/python", "-m", "other", "cognition", "serve"])
    assert not svc.is_supervisor_argv(["sleep", "100"])
    assert not svc.is_supervisor_argv([])
    # 'serve' before 'cognition' is not our shape
    assert not svc.is_supervisor_argv(["/v/bin/python", "-m", "z0int", "serve", "cognition"])


def test_supervisor_argv_port_filtering():
    argv = ["/v/bin/python", "-m", "z0int", "cognition", "serve", "--port", "12000"]
    assert svc.is_supervisor_argv(argv, port=12000)
    assert not svc.is_supervisor_argv(argv, port=11500)
    # no --port means the documented default
    bare = ["/v/bin/python", "-m", "z0int", "cognition", "serve"]
    assert svc.is_supervisor_argv(bare, port=svc.DEFAULT_PORT)
    assert not svc.is_supervisor_argv(bare, port=12000)


def test_llama_identity_requires_one_of_our_ggufs():
    ours = ["/b/llama-server", "-m", f"{GGUF_DIR}/qwen3.5-9b/Qwen_Qwen3.5-9B-Q4_K_M.gguf", "--port", "18100"]
    assert svc.is_z0int_llama_argv(ours, gguf_dir=GGUF_DIR)
    # same binary, somebody else's model directory -> not ours
    theirs = ["/b/llama-server", "-m", "/home/someone/models/llama-3.gguf"]
    assert not svc.is_z0int_llama_argv(theirs, gguf_dir=GGUF_DIR)
    # our model, but not a llama-server process
    assert not svc.is_z0int_llama_argv(["/b/other-proxy", "-m", f"{GGUF_DIR}/x.gguf"], gguf_dir=GGUF_DIR)
    # no model flag at all
    assert not svc.is_z0int_llama_argv(["/b/llama-server", "--port", "18100"], gguf_dir=GGUF_DIR)
    # path traversal must not escape the model root
    assert not svc.is_z0int_llama_argv(
        ["/b/llama-server", "-m", f"{GGUF_DIR}/../secrets/x.gguf"], gguf_dir=GGUF_DIR
    )


def test_parse_llama_argv_handles_both_flag_spellings():
    assert svc.parse_llama_argv(["/b/llama-server", "--model", "/m/x.gguf"]) == ("llama-server", "/m/x.gguf")
    assert svc.parse_llama_argv(["/b/llama-server", "-m", "/m/x.gguf"]) == ("llama-server", "/m/x.gguf")
    assert svc.parse_llama_argv(["/b/llama-server"]) == ("llama-server", None)


# --- orphan classification ---------------------------------------------


def proc(pid, ppid, argv):
    return svc.ProcInfo(pid=pid, ppid=ppid, argv=tuple(argv))


LLAMA_ARGV = ["/b/llama-server", "-m", f"{GGUF_DIR}/qwen3.5-9b/x.gguf"]


def test_orphans_are_targets_and_live_parents_are_spared():
    orphan = proc(10, 1, LLAMA_ARGV)          # reparented to init
    child = proc(11, 500, LLAMA_ARGV)          # child of a supervisor we stopped
    strangers_child = proc(12, os.getpid(), LLAMA_ARGV)  # live unrelated parent

    targets, spared = svc.classify_llama_targets([orphan, child, strangers_child], stopped_pids=[500])
    assert {p.pid for p in targets} == {10, 11}
    assert {p.pid for p in spared} == {12}


def test_init_like_detects_a_systemd_instance():
    assert svc.is_init_like(proc(1, 0, ["/sbin/init"]))
    assert svc.is_init_like(proc(1064, 1, ["/usr/lib/systemd/systemd", "--user"]))
    assert not svc.is_init_like(proc(9, 1, ["/bin/bash"]))
    assert not svc.is_init_like(proc(9, 1, ["/b/llama-server", "-m", "/x.gguf"]))


def test_llama_server_reparented_to_systemd_user_is_a_target(tmp_path):
    """The subtle GPU leak: systemd --user is a subreaper, so ppid is not 1.

    Where a user systemd instance runs, killing a supervisor with SIGKILL
    reparents the GPU-holding child to it.  Treating only ppid==1 as "orphaned"
    would spare that child and leave the card busy while reporting success.
    """
    root = make_proc_root(tmp_path, {
        1064: (1, ["/usr/lib/systemd/systemd", "--user"]),
        900: (1064, LLAMA_ARGV),
    })
    targets, spared = svc.classify_llama_targets(
        [proc(900, 1064, LLAMA_ARGV)], proc_root=root,
    )
    assert [p.pid for p in targets] == [900]
    assert spared == []


def test_llama_server_with_a_live_shell_parent_is_still_spared(tmp_path):
    """A deliberate foreground run by the user must not be swept."""
    root = make_proc_root(tmp_path, {
        950: (1, ["/bin/bash"]),
        901: (950, LLAMA_ARGV),
    })
    targets, spared = svc.classify_llama_targets(
        [proc(901, 950, LLAMA_ARGV)], proc_root=root,
    )
    assert targets == []
    assert [p.pid for p in spared] == [901]


def test_a_dead_parent_makes_a_server_a_target():
    dead_parent = proc(20, 999999, LLAMA_ARGV)
    targets, spared = svc.classify_llama_targets([dead_parent])
    assert [p.pid for p in targets] == [20]
    assert spared == []


# --- fake /proc ---------------------------------------------------------


def make_proc_root(tmp: Path, entries: dict[int, tuple[int, list[str]]]) -> Path:
    root = tmp / "proc"
    root.mkdir(parents=True, exist_ok=True)
    for pid, (ppid, argv) in entries.items():
        d = root / str(pid)
        d.mkdir(parents=True, exist_ok=True)
        (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
        (d / "stat").write_text(f"{pid} (name with) spaces) S {ppid} 0 0\n")
    return root


def test_discover_supervisors_reads_proc_not_just_the_pidfile(tmp_path):
    root = make_proc_root(tmp_path, {
        100: (1, ["/v/bin/python", "-m", "z0int", "cognition", "serve", "--port", "11500"]),
        101: (1, ["/v/bin/python", "-m", "z0int", "cognition", "serve", "--port", "12000"]),
        102: (1, ["/v/bin/python", "-m", "z0int", "cognition", "decide"]),
        103: (1, ["/b/llama-server", "-m", f"{GGUF_DIR}/x.gguf"]),
    })
    found = svc.discover_supervisors(port=11500, proc_root=root, state_file=tmp_path / "nope.json")
    assert [p.pid for p in found] == [100]
    assert found[0].ppid == 1  # parenthesised comm did not break stat parsing


def test_discover_llama_servers_matches_only_our_models(tmp_path):
    root = make_proc_root(tmp_path, {
        200: (1, ["/b/llama-server", "-m", f"{GGUF_DIR}/qwen3.5-9b/x.gguf"]),
        201: (1, ["/b/llama-server", "-m", "/home/other/llama.gguf"]),
        202: (1, ["/v/bin/python", "-m", "z0int", "cognition", "serve"]),
    })
    found = svc.discover_llama_servers(gguf_dir=GGUF_DIR, proc_root=root)
    assert [p.pid for p in found] == [200]


# --- state file ---------------------------------------------------------


def test_state_round_trip_and_clear(tmp_path):
    path = tmp_path / "state.json"
    assert svc.read_state(path) is None
    svc.write_state(svc.ServiceState(pid=7, host="h", port=9, started_at=1.5, argv=("a", "b")), path)
    back = svc.read_state(path)
    assert back is not None and back.pid == 7 and back.argv == ("a", "b")
    assert svc.clear_state(path) is True
    assert svc.read_state(path) is None
    assert svc.clear_state(path) is False


def test_corrupt_state_file_reads_as_absent(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not json")
    assert svc.read_state(path) is None


# --- stop: which pids get signalled ------------------------------------


def test_stop_signals_the_supervisor_and_sweeps_its_orphan(tmp_path):
    root = make_proc_root(tmp_path, {
        300: (1, ["/v/bin/python", "-m", "z0int", "cognition", "serve", "--port", "11500"]),
        301: (1, ["/b/llama-server", "-m", f"{GGUF_DIR}/qwen3.5-9b/x.gguf"]),
    })
    sent: list[tuple[int, int]] = []
    svc.stop_service(
        port=11500, gguf_dir=GGUF_DIR, proc_root=root,
        state_file=tmp_path / "state.json",
        alive=lambda pid: False,               # both die on SIGTERM
        signal_fn=lambda pid, sig: sent.append((pid, sig)),
        gpu_apps=lambda: [],
    )
    assert (300, signal.SIGTERM) in sent, "supervisor must be stopped"
    assert (301, signal.SIGTERM) in sent, "orphaned llama-server must be swept"
    assert not any(sig == signal.SIGKILL for _, sig in sent)


def test_stop_never_signals_a_pid_that_is_not_our_supervisor(tmp_path):
    """The pid-reuse case: the pidfile names a live process that is not ours."""
    root = make_proc_root(tmp_path, {
        400: (1, ["sleep", "1000"]),  # alive, but nothing to do with z0int
    })
    state = tmp_path / "state.json"
    svc.write_state(svc.ServiceState(pid=400, host="127.0.0.1", port=11500, started_at=0.0), state)
    sent: list[tuple[int, int]] = []
    result = svc.stop_service(
        port=11500, gguf_dir=GGUF_DIR, proc_root=root, state_file=state,
        alive=lambda pid: True, signal_fn=lambda pid, sig: sent.append((pid, sig)),
        gpu_apps=lambda: [],
    )
    assert sent == [], f"signalled an unrelated process: {sent}"
    assert result.details["stale_state_file"] is True
    assert result.details["stopped_supervisors"] == []
    assert result.ok is True and result.action == "already_stopped"


def test_stop_escalates_to_sigkill_only_when_sigterm_is_ignored(tmp_path):
    root = make_proc_root(tmp_path, {
        500: (1, ["/v/bin/python", "-m", "z0int", "cognition", "serve", "--port", "11500"]),
    })
    sent: list[tuple[int, int]] = []
    svc.stop_service(
        port=11500, gguf_dir=GGUF_DIR, proc_root=root, state_file=tmp_path / "s.json",
        grace=0.0,
        alive=lambda pid: True,  # never dies
        signal_fn=lambda pid, sig: sent.append((pid, sig)),
        gpu_apps=lambda: [],
    )
    assert (500, signal.SIGTERM) in sent and (500, signal.SIGKILL) in sent


def test_stop_reports_incomplete_rather_than_claiming_success(tmp_path):
    """If something survives, say so -- do not claim the GPU was released."""
    root = make_proc_root(tmp_path, {
        600: (1, ["/v/bin/python", "-m", "z0int", "cognition", "serve", "--port", "11500"]),
    })
    result = svc.stop_service(
        port=11500, gguf_dir=GGUF_DIR, proc_root=root, state_file=tmp_path / "s.json",
        grace=0.0, alive=lambda pid: True, signal_fn=lambda pid, sig: None,
        gpu_apps=lambda: [],
    )
    assert result.ok is False and result.action == "stop_incomplete"
    assert result.details["leftover_supervisors"] == [600]


def test_stop_on_a_quiet_machine_is_a_no_op(tmp_path):
    root = make_proc_root(tmp_path, {})
    result = svc.stop_service(
        port=11500, gguf_dir=GGUF_DIR, proc_root=root, state_file=tmp_path / "s.json",
        alive=lambda pid: False, signal_fn=lambda pid, sig: None, gpu_apps=lambda: [],
    )
    assert result.ok is True and result.action == "already_stopped"


def test_stop_spares_a_llama_server_with_a_live_unrelated_parent(tmp_path):
    root = make_proc_root(tmp_path, {
        651: (1, ["/bin/bash"]),  # someone's interactive shell
        700: (651, ["/b/llama-server", "-m", f"{GGUF_DIR}/qwen3.5-9b/x.gguf"]),
    })
    sent: list[tuple[int, int]] = []
    result = svc.stop_service(
        port=11500, gguf_dir=GGUF_DIR, proc_root=root, state_file=tmp_path / "s.json",
        alive=lambda pid: True, signal_fn=lambda pid, sig: sent.append((pid, sig)),
        gpu_apps=lambda: [],
    )
    assert sent == []
    assert result.details["spared_llama_servers"] == [700]


# --- status -------------------------------------------------------------


def test_status_flags_an_orphan_even_when_the_supervisor_is_gone(tmp_path):
    root = make_proc_root(tmp_path, {
        800: (1, ["/b/llama-server", "-m", f"{GGUF_DIR}/qwen3.5-9b/x.gguf"]),
    })
    result = svc.service_status(
        port=11500, gguf_dir=GGUF_DIR, proc_root=root,
        state_file=tmp_path / "s.json", gpu_apps=lambda: [{"pid": 800, "used_memory": "5824 MiB"}],
    )
    assert result.details["running"] is False
    assert result.details["orphan_llama_servers"] == [800]
    assert "still hold the GPU" in result.message


# --- CLI dispatch -------------------------------------------------------


@pytest.mark.parametrize("subcommand", ["start", "stop", "restart", "status", "unload"])
def test_every_service_subcommand_dispatches(subcommand, monkeypatch, tmp_path):
    """`z0int service restart` once crashed because the subparser lacked an
    option the dispatch read directly.  Dispatch every verb against stubs."""
    from z0int import cli

    calls: list[str] = []

    def fake_result(action):
        return svc.ServiceResult(True, action, f"stub {action}", {})

    monkeypatch.setattr(svc, "start_service",
                        lambda **kw: (calls.append("start"), fake_result("started"))[1])
    monkeypatch.setattr(svc, "stop_service",
                        lambda **kw: (calls.append("stop"), fake_result("stopped"))[1])
    monkeypatch.setattr(svc, "service_status",
                        lambda **kw: (calls.append("status"), fake_result("status"))[1])
    monkeypatch.setattr(svc, "unload_service",
                        lambda **kw: (calls.append("unload"), fake_result("unloaded"))[1])
    monkeypatch.setattr(svc, "default_state_file", lambda: tmp_path / "s.json")
    monkeypatch.setattr(svc, "default_log_file", lambda: tmp_path / "l.log")

    rc = cli.main(["service", subcommand])
    assert rc == 0, f"service {subcommand} returned {rc}"
    assert calls, f"service {subcommand} dispatched nothing"


def test_restart_dispatches_stop_before_start(monkeypatch, tmp_path):
    from z0int import cli

    order: list[str] = []
    monkeypatch.setattr(svc, "stop_service",
                        lambda **kw: (order.append("stop"), svc.ServiceResult(True, "stopped", "s", {}))[1])
    monkeypatch.setattr(svc, "start_service",
                        lambda **kw: (order.append("start"), svc.ServiceResult(True, "started", "s", {}))[1])
    monkeypatch.setattr(svc, "default_state_file", lambda: tmp_path / "s.json")
    monkeypatch.setattr(svc, "default_log_file", lambda: tmp_path / "l.log")

    assert cli.main(["service", "restart"]) == 0
    assert order == ["stop", "start"]


def test_service_options_reach_the_service_layer(monkeypatch, tmp_path):
    """Flags on the command line must arrive as arguments, not be dropped."""
    from z0int import cli

    seen: dict = {}
    monkeypatch.setattr(svc, "start_service",
                        lambda **kw: (seen.update(kw), svc.ServiceResult(True, "started", "s", {}))[1])
    monkeypatch.setattr(svc, "default_state_file", lambda: tmp_path / "s.json")
    monkeypatch.setattr(svc, "default_log_file", lambda: tmp_path / "l.log")

    assert cli.main(["service", "start", "--port", "12345", "--context", "8192",
                     "--idle-unload", "30", "--no-wait"]) == 0
    assert seen["port"] == 12345
    assert seen["context"] == 8192
    assert seen["idle_unload"] == 30
    assert seen["wait_health"] is False


# --- real processes -----------------------------------------------------


FAKE_SUPERVISOR = [
    sys.executable, "-c", "import time; time.sleep(120)",
    "z0int", "cognition", "serve", "--port", "11599",
]


@pytest.fixture
def fake_supervisor(tmp_path):
    """A real child process whose /proc/cmdline looks like our supervisor."""
    state = tmp_path / "state.json"
    log = tmp_path / "svc.log"
    result = svc.start_service(
        host="127.0.0.1", port=11599, state_file=state, log_file=log,
        wait_health=False, command=FAKE_SUPERVISOR,
    )
    assert result.ok and result.action == "started"
    pid = result.details["pid"]
    try:
        yield pid, state, log
    finally:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        svc.reap_finished()


def test_real_start_records_state_and_discovers_its_own_process(fake_supervisor):
    pid, state, _ = fake_supervisor
    assert pid > 0 and svc.is_alive(pid)
    recorded = svc.read_state(state)
    assert recorded is not None and recorded.pid == pid and recorded.port == 11599
    found = svc.discover_supervisors(port=11599, state_file=state)
    assert [p.pid for p in found] == [pid]


def test_real_stop_actually_kills_the_process_and_clears_state(fake_supervisor):
    pid, state, _ = fake_supervisor
    result = svc.stop_service(
        port=11599, gguf_dir=GGUF_DIR, state_file=state, grace=10.0, gpu_apps=lambda: [],
    )
    assert result.ok is True, result.message
    assert result.details["stopped_supervisors"] == [pid]
    deadline = time.time() + 5
    while svc.is_alive(pid) and time.time() < deadline:
        time.sleep(0.05)
    assert not svc.is_alive(pid), "process survived stop_service"
    assert svc.read_state(state) is None, "state file must not outlive the service"


def test_real_start_is_idempotent(fake_supervisor):
    pid, state, log = fake_supervisor
    again = svc.start_service(
        host="127.0.0.1", port=11599, state_file=state, log_file=log,
        wait_health=False, command=FAKE_SUPERVISOR,
    )
    assert again.ok and again.action == "already_running"
    assert again.details["supervisors"] == [pid]


def test_real_stop_then_start_reuses_the_port(fake_supervisor):
    pid, state, log = fake_supervisor
    svc.stop_service(port=11599, gguf_dir=GGUF_DIR, state_file=state, grace=10.0, gpu_apps=lambda: [])
    restarted = svc.start_service(
        host="127.0.0.1", port=11599, state_file=state, log_file=log,
        wait_health=False, command=FAKE_SUPERVISOR,
    )
    assert restarted.ok and restarted.action == "started"
    new_pid = restarted.details["pid"]
    try:
        assert new_pid != pid
        assert svc.is_alive(new_pid)
    finally:
        try:
            os.kill(new_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def test_start_detects_a_command_that_dies_immediately(tmp_path):
    result = svc.start_service(
        host="127.0.0.1", port=11599,
        state_file=tmp_path / "s.json", log_file=tmp_path / "l.log",
        wait_health=True, health_timeout=10.0,
        command=[sys.executable, "-c", "import sys; sys.exit(3)", "z0int", "cognition", "serve"],
    )
    assert result.ok is False and result.action == "start_failed"
    assert result.details["exit_code"] == 3
    assert svc.read_state(tmp_path / "s.json") is None


def test_start_timeout_rolls_back_and_does_not_leave_a_process(tmp_path):
    """A supervisor that never becomes healthy must not be left listening."""
    result = svc.start_service(
        host="127.0.0.1", port="11598" and 11598,
        state_file=tmp_path / "s.json", log_file=tmp_path / "l.log",
        wait_health=True, health_timeout=1.5,
        command=[sys.executable, "-c", "import time; time.sleep(120)",
                 "z0int", "cognition", "serve", "--port", "11598"],
    )
    assert result.ok is False and result.action == "start_timeout"
    pid = result.details["pid"]
    deadline = time.time() + 5
    while svc.is_alive(pid) and time.time() < deadline:
        time.sleep(0.05)
    assert not svc.is_alive(pid), "failed start left a supervisor running"
    assert svc.read_state(tmp_path / "s.json") is None
