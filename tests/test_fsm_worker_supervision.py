"""Worker supervision statechart (loop 2): twin behaviour, Bend parity and the
read-only replay checker on synthetic sources."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
from argparse import Namespace
from pathlib import Path

import pytest

from z0int.fsm import supervise as S
from z0int.fsm import worker_supervision as WS
from z0int.fsm import ws_parity
from z0int.fsm.worker_supervision import Device, Ev, Exc, Host, Lease, Load, Phase, St, W, lstep, peff, pstep, step

LIM = 7200
needs_bend = pytest.mark.skipif(not Path(ws_parity.BEND).exists() and not shutil.which("bend"),
                                reason="bend not installed (Bend/Lean live on groot only)")


def run(es, w=W(1), lim=LIM):
    return WS.run(es, lim, St(Phase.DISPATCHED, w))


# ---------------------------------------------------------------- the twin

def test_happy_path_completes_and_integrates():
    s = run([Ev("Start", h=Host.MBP, t=0), Ev("Beat", t=10), Ev("Finish", t=20), Ev("Integrate")])
    assert s.phase is Phase.INTEGRATED


def test_heavy_on_groot_needs_exception():
    heavy = W(1, load=Load.HEAVY)
    assert run([Ev("Start", h=Host.GROOT, t=0)], heavy).phase is Phase.DISPATCHED   # refused
    assert run([Ev("Start", h=Host.MBP, t=0)], heavy).phase is Phase.RUNNING
    assert run([Ev("Start", h=Host.GROOT, t=0)], W(1, load=Load.HEAVY, exc=Exc.GPU)).phase is Phase.RUNNING


def test_silent_worker_stalls_then_interrupts_then_goes_overdue():
    s = run([Ev("Start", h=Host.MBP, t=0), Ev("Tick", t=LIM + 1)])
    assert s.phase is Phase.STALLED
    s = step(s, Ev("Tick", t=2 * LIM + 1), LIM)
    assert s.phase is Phase.INTERRUPTED and s.at == 2 * LIM + 1
    s = step(s, Ev("Tick", t=3 * LIM + 2), LIM)
    assert s.phase is Phase.OVERDUE and WS.needs_kevin(s)


def test_resume_needs_log_and_adopts_background():
    s = run([Ev("Start", h=Host.MBP, t=0), Ev("Spawn"), Ev("Interrupt", t=5)])
    assert step(s, Ev("Resume", by=2, log=False), LIM) == s
    r = step(s, Ev("Resume", by=2, log=True), LIM)
    assert r.phase is Phase.RESUMED and r.by == 2 and r.bg == 1


def test_abandon_and_integrate_need_reaped_processes():
    s = run([Ev("Start", h=Host.MBP, t=0), Ev("Spawn"), Ev("Interrupt", t=5)])
    assert step(s, Ev("Abandon"), LIM) == s
    assert step(step(s, Ev("Reap"), LIM), Ev("Abandon"), LIM).phase is Phase.ABANDONED
    c = run([Ev("Start", h=Host.MBP, t=0), Ev("Spawn"), Ev("Finish")])
    assert step(c, Ev("Integrate"), LIM) == c


def test_lease_and_phone():
    l = lstep(Lease(), "Acquire", 1)
    assert lstep(l, "Acquire", 2) == l and lstep(l, "Release", 2) == l
    assert lstep(l, "Reclaim", 2, dead=False) == l and lstep(l, "Reclaim", 2, dead=True) == Lease(2)
    p = Device()
    assert peff(p, "Install", 1) == ("NoEff",)
    p = pstep(p, "Lock", 1)
    assert peff(p, "Install", 1) == ("Installed", 1) and peff(p, "Install", 2) == ("NoEff",)
    assert peff(p, "Input", 1, 7) == ("NoEff",)          # nothing seen in the foreground yet
    p = pstep(p, "Peek", app=7)
    assert peff(p, "Input", 1, 8) == ("NoEff",)          # another app is in front
    assert peff(p, "Input", 1, 7) == ("Typed", 1, 7)
    assert peff(pstep(p, "Input", 1, 7), "Input", 1, 7) == ("NoEff",)  # needs a fresh look


# ---------------------------------------------------------------- Bend parity

@needs_bend
def test_python_matches_bend_on_seeded_tapes():
    res = ws_parity.run_parity(seed=7, n_tapes=400)
    assert res["equal"], res
    assert len(res["final_phases"]) == 9, res["final_phases"]  # every phase reached


@needs_bend
def test_parity_harness_can_fail():
    # test-only sabotage: Python lets a worker with live background processes
    # be abandoned. fsm.bend (production reference) is right; this path is knowingly wrong.
    def sabotaged(s, e, lim):
        if s.phase is Phase.INTERRUPTED and e.kind == "Abandon":
            return St(Phase.ABANDONED, s.w)
        return step(s, e, lim)

    assert not ws_parity.run_parity(seed=7, n_tapes=400, stepper=sabotaged)["worker_equal"]


# ---------------------------------------------------------------- replay checker

T0 = 1792500000.0  # 2026-10-20T05:20:00Z


def _db(path: Path, rows: list[tuple]) -> None:
    con = sqlite3.connect(path)
    con.executescript("""
      create table sessions (id text primary key, source text, title text, started_at real, ended_at real,
        end_reason text, parent_session_id text, last_activity_at real);
      create table messages (id integer primary key autoincrement, session_id text, role text, content text,
        tool_call_id text, tool_calls text, timestamp real);
      create table async_delegations (delegation_id text, state text, dispatched_at real, completed_at real,
        task_json text, result_json text);
    """)
    con.execute("insert into sessions values ('sub1','subagent','Subagent: Build the widget…',?,?,null,'p',?)",
                (T0, T0 + 600, T0 + 600))
    con.execute("insert into sessions values ('sub2','subagent','Subagent: Tidy the widget docs…',?,?,null,'p',?)",
                (T0 + 60, T0 + 500, T0 + 500))
    con.execute("insert into sessions values ('p','cli','parent',?,null,null,null,?)", (T0 - 100, T0 + 900))
    for i, (sess, ts, name, args, result, rts) in enumerate(rows):
        cid = f"c{i}"
        con.execute("insert into messages (session_id, role, tool_calls, timestamp) values (?,?,?,?)",
                    (sess, "assistant", json.dumps([{"id": cid, "function": {"name": name, "arguments": json.dumps(args)}}]), ts))
        con.execute("insert into messages (session_id, role, content, tool_call_id, timestamp) values (?,?,?,?,?)",
                    (sess, "tool", result, cid, rts))
    con.commit()
    con.close()


@pytest.fixture(autouse=True)
def _s25(monkeypatch):
    monkeypatch.setenv("Z0_FSM_S25_MATCH", r"10\.0\.0\.25")


def _fixture(tmp: Path) -> Namespace:
    live = tmp / "live" / "deleg_aaaa"
    live.mkdir(parents=True)
    started = S.dt.datetime.fromtimestamp(T0, S.HOST_TZ).strftime("%Y-%m-%d %H:%M:%S")
    (live / "task-0.log").write_text(f"goal: Build the widget\nstarted: {started}\n"
                                     "00:00:01 final    | end status=interrupted exit_reason=interrupted\n")
    os.utime(live / "task-0.log", (T0 + 600, T0 + 600))
    started2 = S.dt.datetime.fromtimestamp(T0 + 60, S.HOST_TZ).strftime("%Y-%m-%d %H:%M:%S")
    (live / "task-1.log").write_text(f"goal: Tidy the widget docs\nstarted: {started2}\n"
                                     "00:00:01 final    | end status=completed\n")
    os.utime(live / "task-1.log", (T0 + 500, T0 + 500))
    repo = tmp / "wt" / "widget"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q",
                    "--allow-empty", "-m", "init"], check=True)
    (repo / "a.py").write_text("x = 1\n")
    lock = "flock /home/kvn/.hermes/profiles/clean/cache/scratch/heavy.lock nice -n 10"
    rows = [
        # two heavy.lock builds that overlap: the second must have waited for the first
        ("sub1", T0 + 10, "terminal", {"command": f"{lock} ./gradlew assembleDebug"}, '{"exit_code": 0}', T0 + 310),
        ("sub2", T0 + 70, "terminal", {"command": f"{lock} cargo build --release"}, '{"exit_code": 0}', T0 + 400),
        # routed to the fleet: not a groot job
        ("sub1", T0 + 20, "terminal", {"command": "fleet-build -- ./gradlew assembleDebug"}, '{"exit_code": 0}', T0 + 30),
        # Bend's toolchain lives only on groot: standing exception
        ("sub1", T0 + 500, "terminal", {"command": f"{lock} hermes bend verify /x --receipt r.json"}, "{}", T0 + 501),
        # two sessions write the same worktree while sub1 holds it
        ("sub1", T0 + 100, "patch", {"path": str(repo / "a.py")}, "{}", T0 + 101),
        ("sub2", T0 + 200, "write_file", {"path": str(repo / "b.py")}, "{}", T0 + 201),
        ("sub1", T0 + 300, "patch", {"path": str(repo / "a.py")}, "{}", T0 + 301),
        # S25: install without the lock, input without a foreground check, then a checked input
        ("sub1", T0 + 400, "terminal", {"command": "adb -s 10.0.0.25:5555 install -r app.apk"}, "{}", T0 + 405),
        ("sub1", T0 + 410, "terminal", {"command": "adb -s 10.0.0.25:5555 shell input tap 1 2"}, "{}", T0 + 411),
        ("sub1", T0 + 420, "terminal", {"command": "adb -s 10.0.0.25:5555 shell dumpsys window | grep mCurrentFocus && "
                                                     "adb -s 10.0.0.25:5555 shell input tap 1 2"}, "{}", T0 + 421),
        ("sub1", T0 + 430, "terminal", {"command": "flock ~/.hermes/profiles/clean/cache/scratch/s25-noop-install.lock "
                                                     "adb -s 10.0.0.25:5555 install -r app.apk"}, "{}", T0 + 431),
        # the parent's notice that Hermes terminated two background processes of a child
        ("p", T0 + 700, "delegate_task", {}, "trace): /x/delegation/live/deleg_aaaa/task-0.log\\nChild left 2 background "
         "process(es) running that were TERMINATED with it (subagent process notices never reach you): "
         "proc_aaaaaaaaaaaa `sleep 9` (5s), proc_bbbbbbbbbbbb `sleep 9` (3s). Re-launch in this session.", T0 + 701),
    ]
    _db(tmp / "state.db", rows)
    runs = tmp / "runs"
    runs.mkdir()
    name = S.dt.datetime.fromtimestamp(T0 + 20, S.HOST_TZ).strftime("%Y%m%d-%H%M%S")
    (runs / f"{name}-ab12-mbp.log").write_text("# fleet job x node=mbp cmd=./gradlew assembleDebug\nok\n# exit 0\n")
    os.utime(runs / f"{name}-ab12-mbp.log", (T0 + 29, T0 + 29))
    (tmp / "procs.json").write_text("[]")
    return Namespace(delegation_dir=str(tmp / "live"), state_db=str(tmp / "state.db"), processes=str(tmp / "procs.json"),
                     roots=[str(tmp / "wt")], fleet_runs=str(runs), decisions=tmp / "none.jsonl",
                     loop1_report=None, limit_hours=2.0, now=S.iso(T0 + 4 * 3600), json=None)


def _codes(rep):
    return {f["code"] for f in rep["findings"]}


def test_replay_on_synthetic_sources(tmp_path):
    rep = S.build(_fixture(tmp_path))
    codes = _codes(rep)
    assert {"interrupted_past_limit", "heavy_on_groot", "worktree_two_owners", "install_without_lock",
            "input_without_foreground_check"} <= codes
    heavy = [f for f in rep["findings"] if f["code"] == "heavy_on_groot"]
    assert len(heavy) == 2 and not any("fleet-build" in f["evidence"]["cmd"] or "bend" in f["evidence"]["cmd"]
                                       for f in heavy)
    g = rep["groot_vs_fleet"]
    assert g["groot_heavy_excepted"]["toolchain"] == 1
    assert g["groot_all"]["jobs_that_waited"] == 1 and g["groot_all"]["wait_total_s"] == 310 - 70
    assert g["fleet_jobs"] == 1 and g["fleet_routed_calls"] == 1
    assert rep["phone"]["s25_installs"] == 2 and rep["phone"]["s25_installs_locked"] == 1
    assert rep["phone"]["s25_inputs_with_fg_check"] == 1
    assert rep["background"]["child_left_notices"] == 1 and rep["background"]["child_left_processes"] == 2
    nk = rep["needs_kevin"]
    assert nk["size"] == 1 and nk["items"][0]["worker"] == "deleg_aaaa/task-0"
    assert nk["items"][0]["recommended"] == "resume"   # its own a.py is uncommitted


def test_replay_is_deterministic_and_reads_only(tmp_path):
    args = _fixture(tmp_path)
    before = {p: p.stat().st_mtime for p in tmp_path.rglob("*") if p.is_file() and ".git" not in p.parts}
    a, b = S.build(args), S.build(args)
    assert json.dumps(a, default=str) == json.dumps(b, default=str)
    after = {p: p.stat().st_mtime for p in tmp_path.rglob("*") if p.is_file() and ".git" not in p.parts}
    assert before == after


def test_decision_line_abandons(tmp_path):
    args = _fixture(tmp_path)
    (tmp_path / "wt" / "widget" / "a.py").unlink()
    args.decisions.write_text(json.dumps({"worker": "deleg_aaaa/task-0", "action": "abandon", "by": "kevin"}) + "\n")
    rep = S.build(args)
    assert rep["needs_kevin"]["size"] == 0
    assert {w["id"]: w["phase"] for w in rep["workers"]}["deleg_aaaa/task-0"] == "Abandoned"


def test_dispatch_check(tmp_path):
    rep = S.build(_fixture(tmp_path))
    assert S.dispatch_check(rep, None, heavy=True, exception=None)
    assert not S.dispatch_check(rep, None, heavy=True, exception="gpu")


def test_committed_diagram_is_current():
    mmd = Path(__file__).resolve().parents[1] / "docs" / "fsm" / "worker-supervision.mmd"
    assert mmd.read_text() == WS.mermaid(), "regenerate: python -m z0int.fsm.supervise --mermaid docs/fsm/worker-supervision.mmd"
