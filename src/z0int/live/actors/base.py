"""Provider-neutral conversation actors used for offline proof.

``FakeConversationActor`` is the deterministic actor the replay tests drive. It
performs no I/O, holds no credentials and makes no provider call, so the whole
architecture can be proven before any microphone, network or model is involved.

It also carries the Stage Manager fast path, because that must stay local and must
not start an LLM turn: window/stage utterances are matched deterministically here
and never reach the delegation path.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from ..contracts import (
    SILENT,
    ActorEventType,
    TaskRef,
)
from ..task_ledger import TaskLedger

#: Deterministic Stage Manager grammar. Deliberately small and literal: these are
#: the utterances that must never cost a model turn.
STAGE_INTENTS: tuple[tuple[str, str, dict[str, Any]], ...] = (
    ("next_window", r"\bnext (window|pane)\b", {}),
    ("previous_window", r"\b(previous|prev|last) (window|pane)\b", {}),
    ("focus_terminal", r"\bfocus (the )?(terminal|shell|console)\b", {"target": "terminal"}),
    ("focus_browser", r"\bfocus (the )?browser\b", {"target": "browser"}),
    ("restore_stage", r"\b(restore|reset) (the )?stage\b", {}),
    ("quadrant_split", r"\bquadrant split\b", {}),
)


@dataclass
class StageAction:
    intent: str
    params: dict[str, Any] = field(default_factory=dict)
    utterance: str = ""


def match_stage_intent(text: str) -> StageAction | None:
    """Return the deterministic Stage Manager action for an utterance, or None.

    Pure function: no model, no network, no state. Anything that does not match
    exactly falls through to the normal delegation path.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    low = text.lower()
    for intent, pattern, params in STAGE_INTENTS:
        if re.search(pattern, low):
            return StageAction(intent=intent, params=dict(params), utterance=text.strip())
    return None


class FakeConversationActor:
    """A ConversationActor that records everything and calls no provider.

    Wires together: transcript -> (stage fast path | delegation) -> ledger ->
    AgentControl -> events -> silent state / speakable commentary.
    """

    def __init__(self, control: Any, ledger: TaskLedger | None = None,
                 *, harness: str = "fake") -> None:
        self.control = control
        self.ledger = ledger or TaskLedger()
        self.harness = harness
        self.connected = False
        self.closed = False
        #: ordered record of everything the actor did, for assertions
        self.trace: list[tuple[str, dict[str, Any]]] = []
        #: what would actually be spoken / shown
        self.silent_states: list[tuple[TaskRef, dict[str, Any]]] = []
        self.speakable: list[tuple[TaskRef, str]] = []
        self.stage_actions: list[StageAction] = []
        self.transcripts: list[str] = []
        self.audio_chunks = 0
        self.cancelled: list[TaskRef] = []
        #: instrumentation: monotonic timestamps per pipeline stage
        self.timings: dict[str, float] = {}

    # ------------------------------------------------------------ lifecycle
    def connect(self) -> None:
        self.connected = True
        self.trace.append((ActorEventType.CONNECT.value, {}))

    def close(self) -> None:
        self.closed = True
        self.connected = False
        self.trace.append((ActorEventType.CLOSE.value, {}))

    def on_audio(self, audio: bytes) -> None:
        self.audio_chunks += 1
        self.trace.append((ActorEventType.AUDIO_INPUT.value, {"bytes": len(audio)}))

    # ------------------------------------------------------------- inbound
    def on_transcript(self, text: str, *, final: bool = True) -> None:
        """A final utterance either hits the deterministic fast path or delegates.

        This method never blocks on background work: it returns as soon as the
        task is opened and handed to the harness.
        """
        self.trace.append((ActorEventType.TRANSCRIPT.value, {"text": text, "final": final}))
        if not final:
            return
        self.transcripts.append(text)
        stage = match_stage_intent(text)
        if stage is not None:
            # deterministic, local, and explicitly NOT a model turn
            self.stage_actions.append(stage)
            self.trace.append(("stage_action", {"intent": stage.intent}))
            return
        self.delegate({"trace_id": f"{self.harness}:{len(self.transcripts)}",
                       "text": text})

    # ------------------------------------------------------------ delegation
    def delegate(self, request: dict[str, Any], *, announce: str = SILENT) -> TaskRef:
        self.timings.setdefault("transcript_received", time.monotonic())
        ref, replayed = self.ledger.open(request, harness=self.harness, announce=announce)
        self.timings.setdefault("delegation_emitted", time.monotonic())
        self.trace.append((ActorEventType.DELEGATION.value,
                           {"task_id": ref.task_id, "replayed": replayed}))
        if replayed:
            # idempotent: no second spawn, no second side effect
            return ref
        spawned = self.control.spawn(request, task=ref)
        self.timings.setdefault("worker_admitted", time.monotonic())
        started, _ = self.ledger.start(spawned, worker_id=spawned.worker_id or "worker")
        self.timings.setdefault("worker_started", time.monotonic())
        return started

    # ------------------------------------------------------------- outbound
    def inject_silent(self, task: TaskRef, state: dict[str, Any]) -> None:
        self.silent_states.append((task, state))
        self.trace.append((ActorEventType.SILENT_STATE.value, {"task_id": task.task_id}))

    def inject_speakable(self, task: TaskRef, text: str) -> None:
        self.speakable.append((task, text))
        self.timings.setdefault("commentary_injected", time.monotonic())
        self.trace.append((ActorEventType.SPEAKABLE.value,
                           {"task_id": task.task_id, "text": text}))

    def cancel(self, task: TaskRef) -> None:
        self.cancelled.append(task)
        self.control.cancel(task)
        self.ledger.cancel(task, reason="operator cancel")

    # ------------------------------------------------------------- delivery
    def pump(self, task: TaskRef, *, text: str | None = None) -> dict[str, Any] | None:
        """Deliver an announceable result to the conversation, if any.

        Silent tasks update background state only. Speakable results are spoken.
        Stale or unverified results return None and are never announced.
        """
        payload = self.ledger.announce(task, text=text)
        if payload is None:
            return None
        if payload["kind"] == SILENT:
            self.inject_silent(payload["task"], payload["state"] or {})
        else:
            self.inject_speakable(payload["task"], payload["text"])
        return payload
