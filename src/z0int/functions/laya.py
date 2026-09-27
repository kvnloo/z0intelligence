"""Local Laya implementation of ``verify.evidence_sufficiency``.

``convaiinnovations/laya``: ModernBERT-large + option-marker decision head, 421M,
asked the same ``noul`` question as Jev. Local, no metering, ~28 ms.

Loading is a *preference chain*, never a second implementation of the same thing:

1. ``z0int.backends.laya.LayaBackend`` if that module exists in the tree (the
   in-flight backend). Using it means one loader, one set of weights in VRAM.
2. otherwise the checkpoint's own shipped runtime (``rl_agent_api.RLAgent`` from
   the snapshot), which is what the validated numbers were produced with.

Weights are loaded once per process and reused across calls.
"""

from __future__ import annotations

import glob
import os
import sys
import threading
import time
from typing import Any

from .contract import (
    FUNCTION_ID,
    PROPOSITION,
    BackendCapabilities,
    VerifierUnavailable,
)

HF_REPO = "convaiinnovations/laya"
VALIDATION = {
    "controlled_packets": {
        "n": 3, "phrasings_passed": "3/3",
        "supported": 0.6890, "ambiguous": 0.5408, "unsupported": 0.2681,
        "spread": 0.4209,
    },
    "phrasing_sensitivity_mean_abs_delta": 0.0629,
    "runtime_task_family_fact_checking": {"accuracy": 0.883, "ece": 0.054},
}

_LOCK = threading.Lock()
_LOADED: dict[str, Any] = {}


def snapshot_dir() -> str | None:
    """The local checkpoint. Honours LAYA_CHECKPOINT, else the HF cache."""
    explicit = os.environ.get("LAYA_CHECKPOINT")
    if explicit and os.path.isdir(explicit):
        return explicit
    env = os.environ.get("HF_HOME")
    roots = [
        os.path.join(env, "hub") if env else None,
        os.path.expanduser("~/.cache/huggingface/hub"),
    ]
    for root in roots:
        if not root:
            continue
        hits = glob.glob(os.path.join(root, "models--convaiinnovations--laya", "snapshots", "*"))
        for hit in sorted(hits):
            if os.path.isfile(os.path.join(hit, "model.safetensors")):
                return hit
    return None


def _codex_backend_class():
    """The in-flight backend, if this tree has it. Preferred over a second loader."""
    try:
        from z0int.backends.laya import LayaBackend  # type: ignore

        return LayaBackend
    except Exception:
        return None


def _load_directly(snapshot: str):
    """The checkpoint's own shipped runtime (rl_agent_api.RLAgent)."""
    if snapshot not in sys.path:
        sys.path.insert(0, snapshot)
    from rl_agent_api import RLAgent  # type: ignore

    return RLAgent(snapshot)


class LayaVerifier:
    """Fast-path implementation. Local, unmetered, no network."""

    backend = "laya"
    role = "fast_path"

    def __init__(self, *, max_new_tokens: int = 0) -> None:
        self._handle: Any = None
        self._kind: str | None = None

    # -- loading ----------------------------------------------------------
    def _ensure_loaded(self):
        """Load once per process, not once per call.

        A fresh ``verify()`` builds a fresh verifier, so the handle must be cached
        at module level: reloading 421M of weights per function call would cost
        ~12 s and thrash VRAM.
        """
        with _LOCK:
            if "handle" in _LOADED:
                self._handle = _LOADED["handle"]
                self._kind = _LOADED["kind"]
                return self._handle
            if self._handle is not None:
                return self._handle
            snapshot = snapshot_dir()
            if not snapshot:
                raise VerifierUnavailable(
                    self.backend,
                    f"{HF_REPO} checkpoint not found (set LAYA_CHECKPOINT or warm the HF cache)")
            cls = _codex_backend_class()
            if cls is not None:
                try:
                    self._handle = cls.for_manifest_id("laya_421m") if hasattr(cls, "for_manifest_id") else cls()
                    self._kind = "z0int.backends.laya.LayaBackend"
                except Exception:
                    self._handle = None  # fall through to the shipped runtime
            if self._handle is None:
                self._handle = _load_directly(snapshot)
                self._kind = "rl_agent_api.RLAgent"
            _LOADED["handle"] = self._handle
            _LOADED["kind"] = self._kind
            return self._handle

    # -- contract ---------------------------------------------------------
    def capabilities(self, *, available: bool | None = None) -> BackendCapabilities:
        snapshot = snapshot_dir()
        return BackendCapabilities(
            function_id=FUNCTION_ID,
            backend=self.backend,
            local=True,
            role=self.role,
            model="laya_421m",
            revision=os.path.basename(snapshot) if snapshot else None,
            available=(snapshot is not None) if available is None else available,
            credential_source=None,
            validated_on=("3 controlled packets", "3 equivalent phrasings"),
            validation=VALIDATION,
            typical_latency_ms=28.0,
            cost_per_call_usd=0.0,
            detail=f"{HF_REPO} via {self._kind or 'shipped runtime'}; native noul probability",
        )

    def health(self) -> tuple[bool, str]:
        snapshot = snapshot_dir()
        if not snapshot:
            return False, f"{HF_REPO} checkpoint not present"
        return True, f"checkpoint at {snapshot}; live readiness requires a call"

    def verify(self, state: str, *, proposition: str = PROPOSITION
               ) -> tuple[float, dict[str, Any]]:
        handle = self._ensure_loaded()
        if self._kind == "rl_agent_api.RLAgent":
            out = handle.system_one(state, {"sufficient": {"type": "noul", "instructions": proposition}})
            answer = out["answers"]["sufficient"]
            return float(answer["noul"]), {
                "model": "laya_421m",
                "revision": os.path.basename(snapshot_dir() or ""),
                "loader": self._kind,
                "usage": out.get("usage") or {},
                "question_type": "noul",
            }
        # z0int.backends.laya.LayaBackend: DecisionBackend protocol
        from z0int.backends.base import DecisionQuestion, DecisionRequest  # type: ignore

        question = DecisionQuestion(id="sufficient", type="boolean", instructions=proposition)
        result = handle.evaluate(DecisionRequest(state=state, questions=(question,), request_id="laya"))
        answer = result.answers[0]
        p_true = float(answer.probabilities["true"])
        return p_true, {
            "model": getattr(result, "model", "laya_421m"),
            "revision": getattr(result, "revision", None),
            "loader": self._kind,
            "usage": {},
            "question_type": "boolean",
        }
