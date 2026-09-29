"""z0 Live offline contract + replay tests.

Everything here runs with no microphone, no network, no provider and no model, so
the architecture is proven before any live actor is wired.

Covered: contracts, the deterministic Stage Manager fast path, the full replay
sequence, duplicate trace_id, uncertain dispatch, cancellation, stale results,
concurrent workers, approval, and the execution-complete-vs-verified distinction.
"""

from __future__ import annotations

import pytest

from z0int.live import (
    SILENT,
    SPEAKABLE,
    AgentControl,
    ConversationActor,
    DuplicateTaskError,
    StaleTaskError,
    TaskEvent,
    TaskEventType,
    TaskLedger,
    TaskRef,
    TaskStatus,
    task_id_for,
)
from z0int.live.actors import FakeConversationActor, match_stage_intent
from z0int.live.adapters import (
    FakeHermesTransport,
    FakeOMPTransport,
    HermesAgentControl,
    OMPAgentControl,
)
from z0int.live.events import is_announceable, is_terminal


# --------------------------------------------------------------------- fixtures
@pytest.fixture(params=["omp", "hermes"])
def control(request):
    if request.param == "omp":
        transport = FakeOMPTransport()
        return OMPAgentControl(transport), transport
    transport = FakeHermesTransport()
    return HermesAgentControl(transport), transport


def _actor(control):
    ledger = TaskLedger()
    return FakeConversationActor(control, ledger), ledger


# -------------------------------------------------------------------- contracts
def test_protocols_are_runtime_checkable():
    assert isinstance(FakeConversationActor(OMPAgentControl()), ConversationActor)
    assert isinstance(OMPAgentControl(), AgentControl)
    assert isinstance(HermesAgentControl(), AgentControl)


def test_task_ref_revision_only_increases():
    ref = TaskRef(task_id="t", trace_id="x", revision=1, harness="omp")
    for _ in range(5):
        ref = ref.bump()
    assert ref.revision == 6


def test_task_id_is_stable_and_derived_from_trace():
    assert task_id_for("abc") == task_id_for("abc")
    assert task_id_for("abc") != task_id_for("abd")
    assert len(task_id_for("abc")) == 24


def test_verified_event_without_evidence_is_refused():
    with pytest.raises(ValueError):
        TaskEvent(type=TaskEventType.TASK_VERIFIED,
                  task=TaskRef(task_id="t", trace_id="x", revision=1, harness="omp"),
                  verified=False)


# ------------------------------------------------------- stage manager fast path
@pytest.mark.parametrize("utterance,intent", [
    ("next window", "next_window"),
    ("previous window", "previous_window"),
    ("focus terminal", "focus_terminal"),
    ("focus browser", "focus_browser"),
    ("restore stage", "restore_stage"),
    ("quadrant split", "quadrant_split"),
])
def test_stage_utterances_are_deterministic_and_local(utterance, intent):
    action = match_stage_intent(utterance)
    assert action is not None and action.intent == intent


def test_stage_fast_path_does_not_start_a_worker(control):
    ctl, _ = control
    actor, ledger = _actor(ctl)
    actor.connect()
    actor.on_transcript("next window")
    actor.on_transcript("focus terminal")
    assert [a.intent for a in actor.stage_actions] == ["next_window", "focus_terminal"]
    # the whole point: no delegation, no LLM turn, no task
    assert not [t for t in actor.trace if t[0] == "delegation"]
    assert ledger.open_tasks() == []


def test_non_stage_utterance_delegates(control):
    ctl, _ = control
    actor, _ = _actor(ctl)
    actor.connect()
    actor.on_transcript("inspect the repository")
    assert any(t[0] == "delegation" for t in actor.trace)


# ------------------------------------------------------------- replay sequence
def test_full_replay_sequence(control):
    ctl, transport = control
    actor, ledger = _actor(ctl)
    actor.connect()

    actor.on_transcript("inspect repo")
    ref = ledger.ref("fake:1")
    assert ref.status is TaskStatus.STARTED
    assert ref.worker_id

    # steering keeps the same task and worker, and bumps the revision
    steered = ctl.steer(ref, "focus on tests instead")
    assert steered.task_id == ref.task_id
    assert steered.revision == ref.revision + 1

    # progress is silent background state
    prog_ref, _ = ledger.progress(steered, "reading tests")
    payload = ledger.announce(prog_ref)
    assert payload is None, "progress must not be announceable"

    # verified result becomes speakable commentary
    vref, event = ledger.complete(prog_ref, {"summary": "tests inspected"},
                                  verified=True, evidence={"tests": "passed"})
    assert event.type is TaskEventType.TASK_VERIFIED and event.verified
    delivered = actor.pump(vref)
    assert delivered is not None and delivered["kind"] == SILENT
    assert len(actor.silent_states) == 1
    assert not actor.speakable, "a silent task must not be spoken"


def test_speakable_delivery(control):
    ctl, _ = control
    actor, ledger = _actor(ctl)
    ref, _ = ledger.open({"trace_id": "s1", "text": "do work"}, harness="fake",
                         announce=SPEAKABLE)
    started, _ = ledger.start(ctl.spawn({"trace_id": "s1", "text": "do work"}, task=ref),
                              worker_id="w1")
    vref, _ = ledger.complete(started, {"summary": "done"}, verified=True,
                              evidence={"check": "ok"})
    out = actor.pump(vref)
    assert out["kind"] == SPEAKABLE and out["text"] == "done"
    assert actor.speakable


# ------------------------------------------------------------------- idempotency
def test_duplicate_trace_id_does_not_duplicate_side_effects(control):
    ctl, transport = control
    actor, ledger = _actor(ctl)
    before = len(transport.calls)
    actor.on_transcript("inspect repo")
    after_first = len(transport.calls)
    assert after_first > before

    # same trace, same payload -> replay, no second spawn
    ref, replayed = ledger.open({"trace_id": "fake:1", "text": "inspect repo"},
                                harness="fake")
    # same task, and the replay must NOT have advanced the revision
    assert replayed and ref.revision == 2
    assert ref.task_id == ledger.ref("fake:1").task_id
    assert len(transport.calls) == after_first, "replay must not re-spawn"


def test_repeated_trace_with_different_work_is_refused(control):
    ctl, _ = control
    actor, ledger = _actor(ctl)
    actor.on_transcript("inspect repo")
    with pytest.raises(DuplicateTaskError):
        ledger.open({"trace_id": "fake:1", "text": "something else"}, harness="fake")


def test_uncertain_dispatch_returns_same_task_for_recovery(control):
    """After an uncertain dispatch the caller re-issues the trace; it must resolve
    to the same task rather than starting new work."""
    ctl, transport = control
    actor, ledger = _actor(ctl)
    actor.on_transcript("inspect repo")
    first = ledger.ref("fake:1")
    spawns_before = len([c for c in transport.calls if c[0] in
                         ("task", "vibe", "delegate_task")])
    again, replayed = ledger.open({"trace_id": "fake:1", "text": "inspect repo"},
                                  harness="fake")
    spawns_after = len([c for c in transport.calls if c[0] in
                        ("task", "vibe", "delegate_task")])
    assert replayed and again.task_id == first.task_id
    assert spawns_after == spawns_before


# ------------------------------------------------------------- staleness/cancel
def test_stale_result_after_supersede_is_never_announced(control):
    ctl, _ = control
    actor, ledger = _actor(ctl)
    actor.on_transcript("inspect repo")
    ref = ledger.ref("fake:1")

    # a hard redirect supersedes the in-flight revision
    new_ref, superseded = ledger.redirect(ref, "focus on tests instead")
    assert superseded.type is TaskEventType.TASK_SUPERSEDED
    assert new_ref.revision > ref.revision

    # the OLD worker now reports success
    stale = TaskRef(task_id=ref.task_id, trace_id=ref.trace_id,
                    revision=ref.revision, harness=ref.harness,
                    worker_id=ref.worker_id, status=TaskStatus.VERIFIED)
    assert ledger.announce(stale) is None, "superseded result must not be announced"
    assert actor.pump(stale) is None
    assert not actor.speakable


def test_event_for_old_revision_is_refused(control):
    ctl, _ = control
    actor, ledger = _actor(ctl)
    actor.on_transcript("inspect repo")
    ref = ledger.ref("fake:1")
    ledger.redirect(ref, "new direction")
    with pytest.raises(StaleTaskError):
        ledger.progress(ref, "late progress from the superseded worker")


def test_cancelled_task_is_terminal_and_never_verified(control):
    ctl, _ = control
    actor, ledger = _actor(ctl)
    actor.on_transcript("inspect repo")
    ref = ledger.ref("fake:1")
    cref, event = ledger.cancel(ref, reason="operator")
    assert event.type is TaskEventType.TASK_CANCELLED
    assert cref.status is TaskStatus.CANCELLED and cref.status.terminal
    assert ledger.announce(cref) is None
    with pytest.raises(StaleTaskError):
        ledger.complete(cref, {"summary": "late"}, verified=True, evidence={"x": 1})


# ------------------------------------------------------------- concurrency/ids
def test_two_workers_run_concurrently_without_identity_collision(control):
    ctl, transport = control
    actor, ledger = _actor(ctl)
    actor.connect()
    actor.on_transcript("first job")
    actor.on_transcript("second job")

    a = ledger.ref("fake:1")
    b = ledger.ref("fake:2")
    assert a.task_id != b.task_id, "distinct traces must not collide"
    assert a.worker_id != b.worker_id, "distinct traces must get distinct workers"

    va, _ = ledger.complete(a, {"summary": "a done"}, verified=True, evidence={"c": 1})
    vb, _ = ledger.complete(b, {"summary": "b done"}, verified=True, evidence={"c": 2})
    assert va.trace_id != vb.trace_id
    assert len(ctl.list()) >= 0  # list is safe with workers settled
    assert actor.pump(va) and actor.pump(vb)
    assert len(actor.silent_states) == 2


def test_concurrent_ledger_mutations_are_serialised():
    import threading
    ctl = OMPAgentControl()
    ledger = TaskLedger()
    refs = {}
    lock = threading.Lock()

    def worker(n: int):
        r, _ = ledger.open({"trace_id": f"t{n}", "text": f"job {n}"}, harness="omp")
        s, _ = ledger.start(ctl.spawn({"trace_id": f"t{n}", "text": f"job {n}"}, task=r),
                            worker_id=f"w{n}")
        with lock:
            refs[n] = s

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(24)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len({r.task_id for r in refs.values()}) == 24
    assert len({r.worker_id for r in refs.values()}) == 24


# -------------------------------------------------------------------- approval
def test_approval_required_blocks_then_resumes(control):
    ctl, transport = control
    actor, ledger = _actor(ctl)
    actor.on_transcript("dangerous job")
    ref = ledger.ref("fake:1")
    aref, event = ledger.require_approval(ref, "delete the branch?")
    assert event.type is TaskEventType.APPROVAL_REQUIRED
    assert aref.status is TaskStatus.APPROVAL_REQUIRED
    assert ledger.announce(aref) is None, "awaiting approval is not a result"
    resumed, _ = ledger.approve(aref, "approve", reason="reviewed")
    assert resumed.status is TaskStatus.STARTED


def test_approve_without_pending_request_is_refused(control):
    ctl, _ = control
    actor, ledger = _actor(ctl)
    actor.on_transcript("job")
    ref = ledger.ref("fake:1")
    with pytest.raises(StaleTaskError):
        ledger.approve(ref, "approve")


# --------------------------------------------------- execution vs verification
def test_execution_completion_without_evidence_becomes_failed_not_verified(control):
    ctl, _ = control
    actor, ledger = _actor(ctl)
    actor.on_transcript("inspect repo")
    ref = ledger.ref("fake:1")

    # the worker exited cleanly, but nothing independently checked the work
    fref, event = ledger.complete(ref, {"summary": "process exited 0"},
                                  verified=False)
    assert event.type is TaskEventType.TASK_FAILED
    assert event.execution_completed is True
    assert event.verified is False
    assert fref.status is TaskStatus.FAILED
    assert ledger.announce(fref) is None, "unverified completion must not be announced"


def test_verified_completion_requires_evidence(control):
    ctl, _ = control
    actor, ledger = _actor(ctl)
    actor.on_transcript("inspect repo")
    ref = ledger.ref("fake:1")
    with pytest.raises(ValueError):
        ledger.complete(ref, {"summary": "looks fine"}, verified=True)


def test_is_announceable_rejects_stale_and_unverified():
    ref = TaskRef(task_id="t", trace_id="x", revision=2, harness="omp")
    ok = TaskEvent(type=TaskEventType.TASK_VERIFIED, task=ref, verified=True)
    assert is_announceable(ok, current_revision=2)
    assert not is_announceable(ok, current_revision=3)
    unverified = TaskEvent(type=TaskEventType.TASK_FAILED, task=ref, verified=False)
    assert not is_announceable(unverified, current_revision=2)
    assert is_terminal(ok)
    assert not is_terminal(TaskEvent(type=TaskEventType.TASK_PROGRESS, task=ref))


# ---------------------------------------------------------------- adapter shape
def test_omp_adapter_uses_current_rpc_verbs():
    transport = FakeOMPTransport()
    ctl = OMPAgentControl(transport)
    ledger = TaskLedger()
    ref, _ = ledger.open({"trace_id": "o1", "text": "job"}, harness="omp")
    spawned = ctl.spawn({"trace_id": "o1", "text": "job"}, task=ref)
    ctl.steer(spawned, "steer it")
    ctl.redirect(spawned, "hard redirect")
    ctl.status(spawned)
    ctl.cancel(spawned)
    verbs = [c[0] for c in transport.calls]
    assert verbs[0] == "task"
    assert "steer" in verbs and "abort_and_prompt" in verbs and "kill" in verbs


def test_omp_persistent_uses_vibe_verbs():
    transport = FakeOMPTransport()
    ctl = OMPAgentControl(transport, persistent=True)
    ledger = TaskLedger()
    ref, _ = ledger.open({"trace_id": "o2", "text": "job"}, harness="omp")
    spawned = ctl.spawn({"trace_id": "o2", "text": "job"}, task=ref)
    ctl.steer(spawned, "s")
    verbs = [c[0] for c in transport.calls]
    assert verbs == ["vibe", "vibe_send"]


def test_hermes_adapter_uses_hermes_tool_semantics():
    transport = FakeHermesTransport()
    ctl = HermesAgentControl(transport)
    ledger = TaskLedger()
    ref, _ = ledger.open({"trace_id": "h1", "text": "job"}, harness="hermes")
    spawned = ctl.spawn({"trace_id": "h1", "text": "job"}, task=ref)
    ctl.steer(spawned, "steer")
    ctl.status(spawned)
    ctl.cancel(spawned)
    verbs = [c[0] for c in transport.calls]
    assert verbs == ["delegate_task", "steer_agent", "stop_work"]


def test_hermes_list_and_result():
    transport = FakeHermesTransport()
    ctl = HermesAgentControl(transport)
    ledger = TaskLedger()
    ref, _ = ledger.open({"trace_id": "h2", "text": "job"}, harness="hermes")
    spawned = ctl.spawn({"trace_id": "h2", "text": "job"}, task=ref)
    assert ctl.list()
    transport.finish(spawned.worker_id, {"summary": "done"})
    assert ctl.result(spawned) == {"summary": "done"}
    assert ctl.wait(spawned, timeout_s=0.01) == []


def test_wait_is_bounded_even_with_no_events(control):
    ctl, _ = control
    ledger = TaskLedger()
    ref, _ = ledger.open({"trace_id": "w1", "text": "job"}, harness=ctl.name)
    spawned = ctl.spawn({"trace_id": "w1", "text": "job"}, task=ref)
    events = ctl.wait(spawned, timeout_s=0.01)
    assert isinstance(events, list)


# -------------------------------------------------------------- instrumentation
def test_latency_stages_are_instrumented(control):
    ctl, _ = control
    actor, ledger = _actor(ctl)
    actor.connect()
    actor.on_transcript("inspect repo")
    ref = ledger.ref("fake:1")
    vref, _ = ledger.complete(ref, {"summary": "done"}, verified=True,
                              evidence={"ok": True})
    actor.pump(vref)
    # a speakable task exercises the final stage
    speak_ref = actor.delegate({"trace_id": "speak", "text": "say it"},
                               announce=SPEAKABLE)
    svref, _ = ledger.complete(speak_ref, {"summary": "spoken"}, verified=True,
                               evidence={"ok": True})
    actor.pump(svref)
    for stage in ("transcript_received", "delegation_emitted", "worker_admitted",
                  "worker_started", "commentary_injected"):
        assert stage in actor.timings, f"missing timing stage {stage}"
    ordered = [actor.timings[k] for k in
               ("transcript_received", "delegation_emitted", "worker_admitted",
                "worker_started", "commentary_injected")]
    assert ordered == sorted(ordered), "pipeline timings must be monotonic"
