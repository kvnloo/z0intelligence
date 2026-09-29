"""AgentControl v1 → OMP adapter.

Maps onto **current OMP RPC** rather than the donor branch's older surface. The
donor (oh-my-pi PR #77) supplied Handsfree, the Stage Manager and the local intent
fast path; its RPC/session plumbing is largely superseded by current main's RPC /
Agent Hub / task / vibe primitives, so only the still-useful Stage Manager intent
matcher is carried across (see ``actors/base.py::match_stage_intent``), not the
runtime.

Verb mapping
    spawn    -> task or persistent vibe worker
    steer    -> rpc steer / vibe_send
    redirect -> abort_and_prompt   (true hard redirect only)
    status   -> rpc job/subagent state
    wait     -> rpc settle/progress events
    result   -> typed task output / agent:// artifact
    cancel   -> worker kill/abort
    approve  -> rpc approval resolve
    list     -> get_subagents

The transport is injected. ``FakeOMPTransport`` implements the same verbs in
memory, so contract tests run with no RPC server, no credentials and no model.
"""

from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, field
from typing import Any

from ..contracts import TaskEvent, TaskEventType, TaskRef, TaskStatus
from ..task_ledger import TaskLedger


@dataclass
class _Worker:
    worker_id: str
    prompt: str
    status: str = "running"
    result: dict[str, Any] | None = None
    events: list[TaskEvent] = field(default_factory=list)
    steers: list[str] = field(default_factory=list)
    aborted: bool = False


class FakeOMPTransport:
    """In-memory stand-in for current OMP RPC. No network, no provider, no model."""

    def __init__(self) -> None:
        self._ids = itertools.count(1)
        self.workers: dict[str, _Worker] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.settle_cursor: dict[str, int] = {}

    # verbs ---------------------------------------------------------------
    def task(self, prompt: str) -> str:
        wid = f"omp-worker-{next(self._ids)}"
        self.workers[wid] = _Worker(worker_id=wid, prompt=prompt)
        self.calls.append(("task", {"worker_id": wid}))
        return wid

    def vibe(self, prompt: str) -> str:
        wid = f"omp-vibe-{next(self._ids)}"
        self.workers[wid] = _Worker(worker_id=wid, prompt=prompt)
        self.calls.append(("vibe", {"worker_id": wid}))
        return wid

    def steer(self, worker_id: str, instruction: str) -> None:
        self.workers[worker_id].steers.append(instruction)
        self.calls.append(("steer", {"worker_id": worker_id}))

    def vibe_send(self, worker_id: str, instruction: str) -> None:
        self.workers[worker_id].steers.append(instruction)
        self.calls.append(("vibe_send", {"worker_id": worker_id}))

    def abort_and_prompt(self, worker_id: str, instruction: str) -> None:
        w = self.workers[worker_id]
        w.aborted = True
        w.prompt = instruction
        w.status = "running"
        self.calls.append(("abort_and_prompt", {"worker_id": worker_id}))

    def job_state(self, worker_id: str) -> dict[str, Any]:
        w = self.workers[worker_id]
        return {"worker_id": worker_id, "status": w.status, "aborted": w.aborted,
                "steers": len(w.steers)}

    def get_subagents(self) -> list[str]:
        self.calls.append(("get_subagents", {}))
        return [wid for wid, w in self.workers.items() if w.status == "running"]

    def settle(self, worker_id: str) -> list[dict[str, Any]]:
        """Progress/settle events after the cursor."""
        w = self.workers[worker_id]
        start = self.settle_cursor.get(worker_id, 0)
        self.settle_cursor[worker_id] = len(w.events)
        return [{"type": e.type.value, "payload": e.payload} for e in w.events[start:]]

    def kill(self, worker_id: str) -> None:
        self.workers[worker_id].status = "cancelled"
        self.calls.append(("kill", {"worker_id": worker_id}))

    def resolve_approval(self, worker_id: str, decision: str) -> None:
        self.workers[worker_id].status = "running"
        self.calls.append(("resolve_approval", {"worker_id": worker_id, "decision": decision}))

    # test helpers --------------------------------------------------------
    def emit(self, worker_id: str, kind: TaskEventType, payload: dict[str, Any]) -> None:
        w = self.workers[worker_id]
        w.events.append(TaskEvent(type=kind, task=TaskRef(
            task_id="", trace_id="", revision=0, harness="omp",
            worker_id=worker_id), payload=payload))

    def finish(self, worker_id: str, result: dict[str, Any]) -> None:
        w = self.workers[worker_id]
        w.status = "done"
        w.result = result


class OMPAgentControl:
    """AgentControl v1 implemented against an OMP transport."""

    name = "omp"

    def __init__(self, transport: Any | None = None, ledger: TaskLedger | None = None,
                 *, persistent: bool = False) -> None:
        self.transport = transport or FakeOMPTransport()
        self.ledger = ledger or TaskLedger()
        self.persistent = persistent
        self._refs: dict[str, TaskRef] = {}

    # ------------------------------------------------------------ AgentControl
    def spawn(self, request: dict[str, Any], *, task: TaskRef) -> TaskRef:
        prompt = str(request.get("text") or request.get("task") or "")
        wid = (self.transport.vibe(prompt) if self.persistent or request.get("persistent")
               else self.transport.task(prompt))
        ref = TaskRef(task_id=task.task_id, trace_id=task.trace_id,
                      revision=task.revision, harness="omp", worker_id=wid,
                      status=TaskStatus.PENDING)
        self._refs[wid] = ref
        return ref

    def steer(self, task: TaskRef, instruction: str) -> TaskRef:
        if self.persistent:
            self.transport.vibe_send(task.worker_id, instruction)
        else:
            self.transport.steer(task.worker_id, instruction)
        ref = task.bump(status=TaskStatus.PROGRESS)
        self._refs[task.worker_id] = ref
        return ref

    def redirect(self, task: TaskRef, instruction: str) -> TaskRef:
        # abort_and_prompt only for a true hard redirect
        self.transport.abort_and_prompt(task.worker_id, instruction)
        ref = task.bump(status=TaskStatus.STARTED)
        self._refs[task.worker_id] = ref
        return ref

    def status(self, task: TaskRef) -> dict[str, Any]:
        return self.transport.job_state(task.worker_id)

    def wait(self, task: TaskRef, *, timeout_s: float | None = None) -> list[TaskEvent]:
        """Drain progress/settle events. Bounded: returns when the worker settles,
        when no new events are pending, or at the deadline — never spins."""
        deadline = None if timeout_s is None else time.monotonic() + timeout_s
        out: list[TaskEvent] = []
        while True:
            raw = self.transport.settle(task.worker_id)
            for item in raw:
                out.append(TaskEvent(type=TaskEventType(item["type"]), task=task,
                                     payload=item.get("payload") or {}))
            if self.transport.job_state(task.worker_id)["status"] in ("done", "cancelled"):
                return out
            if deadline is not None and time.monotonic() > deadline:
                return out
            if not raw:
                return out

    def result(self, task: TaskRef) -> dict[str, Any] | None:
        w = self.transport.workers.get(task.worker_id)
        return None if w is None else w.result

    def cancel(self, task: TaskRef) -> TaskRef:
        self.transport.kill(task.worker_id)
        return task.bump(status=TaskStatus.CANCELLED)

    def approve(self, task: TaskRef, decision: str, *, reason: str = "") -> TaskRef:
        self.transport.resolve_approval(task.worker_id, decision)
        return task.bump(status=TaskStatus.STARTED)

    def list(self) -> list[TaskRef]:
        return [self._refs[w] for w in self.transport.get_subagents()
                if w in self._refs]
