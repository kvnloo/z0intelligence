"""AgentControl v1 → Hermes adapter.

Maps onto Hermes's EXISTING semantics rather than inventing a parallel vocabulary.
The real surface is the Hermes Talk tool set (``kvnloo/hermes-talk``), reached
through its API boundary rather than by importing Hermes core — importing core
would create a dependency cycle and would mean editing Hermes to fit us, which is
a stop condition.

Verb mapping
    spawn    -> delegate_task        (HTTP: POST /v1/runs)
    steer    -> steer_agent
    redirect -> redirect_agent
    status   -> check_work
    cancel   -> stop_work
    approve  -> resolve_approval
    list     -> list_agents
    result   -> run result payload

Progress and completion are pulled through ``check_work`` on a bounded poll, and
that poll must run OUTSIDE the realtime audio loop. ``wait`` therefore takes an
explicit budget and always returns.
"""

from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, field
from typing import Any

from ..contracts import TaskEvent, TaskEventType, TaskRef, TaskStatus
from ..task_ledger import TaskLedger


@dataclass
class _Run:
    run_id: str
    prompt: str
    state: str = "running"
    progress: list[str] = field(default_factory=list)
    result: dict[str, Any] | None = None
    approval: dict[str, Any] | None = None


class FakeHermesTransport:
    """In-memory stand-in for the Hermes Talk tool surface. No network, no model."""

    def __init__(self) -> None:
        self._ids = itertools.count(1)
        self.runs: dict[str, _Run] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def delegate_task(self, prompt: str) -> str:
        rid = f"hermes-run-{next(self._ids)}"
        self.runs[rid] = _Run(run_id=rid, prompt=prompt)
        self.calls.append(("delegate_task", {"run_id": rid}))
        return rid

    def steer_agent(self, run_id: str, instruction: str) -> None:
        self.runs[run_id].progress.append(f"steer: {instruction}")
        self.calls.append(("steer_agent", {"run_id": run_id}))

    def redirect_agent(self, run_id: str, instruction: str) -> None:
        self.runs[run_id].prompt = instruction
        self.runs[run_id].progress.append(f"redirect: {instruction}")
        self.calls.append(("redirect_agent", {"run_id": run_id}))

    def check_work(self, run_id: str) -> dict[str, Any]:
        r = self.runs[run_id]
        return {"run_id": run_id, "state": r.state, "progress": list(r.progress),
                "approval": r.approval}

    def stop_work(self, run_id: str) -> None:
        self.runs[run_id].state = "stopped"
        self.calls.append(("stop_work", {"run_id": run_id}))

    def resolve_approval(self, run_id: str, decision: str) -> None:
        self.runs[run_id].approval = {"decision": decision}
        self.runs[run_id].state = "running"
        self.calls.append(("resolve_approval", {"run_id": run_id, "decision": decision}))

    def list_agents(self) -> list[str]:
        self.calls.append(("list_agents", {}))
        return [r for r, v in self.runs.items() if v.state == "running"]

    def run_result(self, run_id: str) -> dict[str, Any] | None:
        return self.runs[run_id].result

    # test helpers --------------------------------------------------------
    def add_progress(self, run_id: str, note: str) -> None:
        self.runs[run_id].progress.append(note)

    def require_approval(self, run_id: str, question: str) -> None:
        self.runs[run_id].approval = {"question": question}
        self.runs[run_id].state = "approval_required"

    def finish(self, run_id: str, result: dict[str, Any]) -> None:
        self.runs[run_id].state = "done"
        self.runs[run_id].result = result


class HermesAgentControl:
    """AgentControl v1 implemented against the Hermes Talk tool surface."""

    name = "hermes"

    def __init__(self, transport: Any | None = None, ledger: TaskLedger | None = None,
                 *, poll_s: float = 0.0) -> None:
        self.transport = transport or FakeHermesTransport()
        self.ledger = ledger or TaskLedger()
        self.poll_s = poll_s
        self._refs: dict[str, TaskRef] = {}

    # ------------------------------------------------------------ AgentControl
    def spawn(self, request: dict[str, Any], *, task: TaskRef) -> TaskRef:
        prompt = str(request.get("text") or request.get("task") or "")
        rid = self.transport.delegate_task(prompt)
        ref = TaskRef(task_id=task.task_id, trace_id=task.trace_id,
                      revision=task.revision, harness="hermes", worker_id=rid,
                      status=TaskStatus.PENDING)
        self._refs[rid] = ref
        return ref

    def steer(self, task: TaskRef, instruction: str) -> TaskRef:
        self.transport.steer_agent(task.worker_id, instruction)
        ref = task.bump(status=TaskStatus.PROGRESS)
        self._refs[task.worker_id] = ref
        return ref

    def redirect(self, task: TaskRef, instruction: str) -> TaskRef:
        self.transport.redirect_agent(task.worker_id, instruction)
        ref = task.bump(status=TaskStatus.STARTED)
        self._refs[task.worker_id] = ref
        return ref

    def status(self, task: TaskRef) -> dict[str, Any]:
        return self.transport.check_work(task.worker_id)

    def wait(self, task: TaskRef, *, timeout_s: float | None = None) -> list[TaskEvent]:
        """Bounded poll. Must be called outside the audio loop."""
        deadline = None if timeout_s is None else time.monotonic() + timeout_s
        seen = 0
        out: list[TaskEvent] = []
        while True:
            state = self.transport.check_work(task.worker_id)
            progress = state.get("progress") or []
            for note in progress[seen:]:
                out.append(TaskEvent(type=TaskEventType.TASK_PROGRESS, task=task,
                                     payload={"note": note}))
            seen = len(progress)
            if state.get("approval") and state.get("approval", {}).get("question"):
                out.append(TaskEvent(type=TaskEventType.APPROVAL_REQUIRED, task=task,
                                     payload=dict(state["approval"])))
                return out
            if state.get("state") in ("done", "stopped", "approval_required"):
                return out
            if deadline is not None and time.monotonic() > deadline:
                return out
            if not progress[seen:]:
                return out
            if self.poll_s:
                time.sleep(self.poll_s)

    def result(self, task: TaskRef) -> dict[str, Any] | None:
        return self.transport.run_result(task.worker_id)

    def cancel(self, task: TaskRef) -> TaskRef:
        self.transport.stop_work(task.worker_id)
        return task.bump(status=TaskStatus.CANCELLED)

    def approve(self, task: TaskRef, decision: str, *, reason: str = "") -> TaskRef:
        self.transport.resolve_approval(task.worker_id, decision)
        return task.bump(status=TaskStatus.STARTED)

    def list(self) -> list[TaskRef]:
        return [self._refs[r] for r in self.transport.list_agents() if r in self._refs]
