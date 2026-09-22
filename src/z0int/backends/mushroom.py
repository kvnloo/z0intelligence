"""Mushroom-body recovery specialist as a real DecisionBackend.

This is the integration item: the mushroom stops being an Evolution Lab artifact and
becomes something the registry can hand a decision to, behind the same contract as
NanoJev.

**What it can and cannot be pointed at.** The checkpoint is a `local_plasticity`
student fitted on `hermes_recovery`. Its input is an 8-step x 15-feature Hermes frame
tensor, expanded by the frozen encoder into 151 PN features. It is therefore a
*recovery* specialist and nothing else. It cannot be handed the bounded-choice corpus
without inventing a new frame encoding, and comparing a policy fitted on the original
features against a fresh encoding would not be like-for-like. The capability metadata
says so rather than leaving the wrong impression.

The forward path (PN expansion -> k-winner KC codes -> MBON argmax -> delayed-cue
protocol) mirrors `evolution_lab.models` and `evolution_lab.task`. It is re-derived
here because this package must not depend on the lab, and
`tests/test_mushroom_parity.py` pins it against the lab's own path when the lab is
importable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .base import (
    BackendCapabilities,
    BackendHealth,
    DecisionOption,
    DecisionRequest,
)

RECOVERY_ACTIONS = ("retry", "restart_sandbox", "escalate", "noop", "page_human")

# Resolved through the canonical lab resolver rather than a hardcoded checkout.
# Three Evolution Lab checkouts exist on this machine at three revisions; naming
# one here silently bound this production backend to that revision. The artifact
# itself is byte-identical across all three, so the resolution is safe, but the
# binding is now explicit and configurable.
from .. import paths as _paths

DEFAULT_BUNDLE = _paths.evolution_lab_artifact("data/p0/recovery_student.npz") or (
    _paths.LEGACY_EVOLUTION_LAB_ROOT / "data/p0/recovery_student.npz"
)
DEFAULT_META = _paths.evolution_lab_artifact("data/p0/recovery_student.json") or (
    _paths.LEGACY_EVOLUTION_LAB_ROOT / "data/p0/recovery_student.json"
)

N_FEATURES = 15
HISTORY = 8


def _pn_features(frames: Any) -> Any:
    """Flattened history + last step + max-over-time + a cue bit.

    Matches `evolution_lab.models._pn_features`. The cue bit is read from feature 9,
    which is where the task puts it.
    """
    import numpy as np

    f = np.asarray(frames, dtype=np.float64)
    if f.ndim == 2:
        f = f[None, ...]
    flat = f.reshape(f.shape[0], -1)
    last = f[:, -1, :]
    pooled = f.max(axis=1)
    cue = f[:, :, 9].max(axis=1)[:, None]
    return np.concatenate([flat, last, pooled, cue], axis=1)


def _kc_codes(x: Any, w_pn_kc: Any, k_winners: int) -> Any:
    """k-winner Kenyon-cell codes. PN->KC is frozen wiring, not a learned encoder."""
    import numpy as np

    drive = np.maximum(x @ w_pn_kc, 0.0)
    n, n_kc = drive.shape
    k = max(1, min(int(k_winners), n_kc))
    idx = np.argpartition(drive, -k, axis=1)[:, -k:]
    mask = np.zeros_like(drive)
    np.put_along_axis(mask, idx, 1.0, axis=1)
    return drive * mask


def _delayed_cue_protocol(frames: Any, preds: Any) -> Any:
    """Force `escalate` on the two delayed-cue bookends.

    Matches `evolution_lab.models._apply_delayed_cue_protocol`: t=0 cue, t=T-1 recall.
    """
    import numpy as np

    f = np.asarray(frames, dtype=np.float64)
    out = []
    for i in range(f.shape[0]):
        last = f[i, -1]
        cue_seen = float(f[i, :, 9].max()) > 0.5
        if float(last[9]) > 0.5 and float(last[7]) <= 0.5 and float(last[0]) > 0.5:
            out.append(RECOVERY_ACTIONS.index("escalate"))
        elif cue_seen and float(last[3]) > 0.99:
            out.append(RECOVERY_ACTIONS.index("escalate"))
        else:
            out.append(int(preds[i]))
    return np.asarray(out, dtype=np.int64)


@dataclass
class MushroomBackend:
    """Recovery-only specialist. Refuses anything that is not an 8x15 frame tensor."""

    ID = "mushroom"
    bundle: Path = DEFAULT_BUNDLE
    meta: Path = DEFAULT_META

    def __post_init__(self) -> None:
        self.bundle = Path(self.bundle)
        self.meta = Path(self.meta)

    @classmethod
    def from_manifest(cls, manifest_id: str) -> "MushroomBackend":
        raise ValueError(
            "mushroom has a single fitted bundle (recovery_student.npz); "
            "it has no manifest of selectable models"
        )

    @property
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            id=self.ID,
            kind="local_plasticity_specialist",
            description=(
                "Mushroom-body recovery specialist (Kenyon cells -> MBON, 640 params). "
                "Recovery only: consumes an 8x15 Hermes frame tensor."
            ),
            local=True,
            trainable=False,
            supports_choice=True,
            supports_boolean=False,
            supports_score=False,
            max_choice_options=len(RECOVERY_ACTIONS),
            max_score_levels=0,
            returns_distribution=False,
            autoregressive_decode=False,
        )

    def health(self, *, load: bool = False) -> BackendHealth:
        have = self.bundle.is_file() and self.meta.is_file()
        detail = "bundle present" if have else f"missing {self.bundle}"
        if load and have:
            try:
                self._load()
                detail = "bundle loaded"
            except Exception as exc:  # pragma: no cover
                return BackendHealth(id=self.ID, configured=False, ready=False,
                                     detail=f"load failed: {exc}")
        return BackendHealth(id=self.ID, configured=have, ready=have, detail=detail)

    def _load(self) -> tuple[Any, Any, int, dict]:
        import numpy as np

        b = np.load(self.bundle, allow_pickle=True)
        meta = json.loads(self.meta.read_text(encoding="utf-8"))
        k = int(np.asarray(b["k_winners"]).reshape(-1)[0])
        return b["W_pn_kc"], b["W_kc_mbon"], k, meta

    def evaluate(self, request: DecisionRequest) -> Any:
        import time

        from .base import DecisionAnswer, DecisionResult

        t0 = time.perf_counter()
        state = dict(request.state or {})
        frames = state.get("frames")
        if frames is None:
            raise ValueError(
                "mushroom needs state.frames (history x 15). It is a recovery specialist "
                "and cannot answer a bounded-choice question over legal actions."
            )
        w_pn_kc, w_kc_mbon, k, meta = self._load()
        x = _pn_features(frames)
        h = _kc_codes(x, w_pn_kc, k)
        raw = (h @ w_kc_mbon).argmax(axis=1)
        pred = _delayed_cue_protocol(frames, raw)

        answers = []
        for q in request.questions:
            if q.type != "choice":
                raise ValueError("mushroom answers choice questions over recovery actions only")
            idx = int(pred[0])
            answers.append(
                DecisionAnswer(
                    question_id=q.id,
                    type="choice",
                    value=RECOVERY_ACTIONS[idx],
                    confidence=None,
                    probabilities={},
                )
            )
        dt = (time.perf_counter() - t0) * 1000.0
        return DecisionResult(
            backend=self.ID,
            model=str(meta.get("genome_id") or "mushroom-body"),
            revision=str(meta.get("version") or ""),
            answers=tuple(answers),
            latency_ms=dt,
            diagnostics={
                "forward_ms": dt,
                "network_model_calls": 0,
                "n_params": meta.get("n_params"),
                "encoder": meta.get("encoder"),
                "domain": "hermes_recovery",
                "returns_distribution": False,
                "probability_status": (
                    "argmax only; this student has no calibrated distribution, so it "
                    "cannot be swept by confidence threshold"
                ),
            },
        )


__all__ = ["MushroomBackend", "RECOVERY_ACTIONS"]
