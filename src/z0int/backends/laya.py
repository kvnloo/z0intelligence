"""Laya (~421M) DecisionBackend — convaiinnovations/laya via upstream `laya` runtime."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .base import (
    BackendCapabilities,
    BackendHealth,
    DecisionAnswer,
    DecisionQuestion,
    DecisionRequest,
    DecisionResult,
)
from .named_questions import answer_from_named, normalize_probs, question_to_named


@dataclass
class _Loaded:
    agent: Any
    model_dir: str
    load_ms: float


def _default_device() -> str:
    return os.environ.get("Z0INT_LAYA_DEVICE", "cpu")


def _resolve_model_dir(*, hf: str, revision: str) -> Path:
    from z0int.models_mgmt import model_cached

    from huggingface_hub import snapshot_download

    if model_cached(hf, revision):
        return Path(snapshot_download(repo_id=hf, revision=revision, local_files_only=True))
    return Path(snapshot_download(repo_id=hf, revision=revision))


def _laya_import_error() -> str | None:
    try:
        import laya  # noqa: F401
    except ImportError as exc:
        return f"laya package not installed: {exc}"
    return None


# The z0int ⇄ named-question mapping now lives in ``named_questions`` because
# ``julia`` and ``decider`` need the identical translation. These shims keep the
# original private names working for existing callers and tests, with the old
# one-argument signatures.
def _normalize_probs(probs: dict[str, float]) -> dict[str, float]:
    return normalize_probs(probs, source="laya")


def _question_to_laya(q: DecisionQuestion) -> dict[str, Any]:
    return question_to_named(q)


def _answer_from_laya(q: DecisionQuestion, raw: dict[str, Any]) -> DecisionAnswer:
    return answer_from_named(q, raw, source="laya")


class LayaBackend:
    ID = "laya"

    def __init__(
        self,
        *,
        model_id: str = "laya_421m",
        hf: str,
        revision: str,
        device: str | None = None,
        model_dir: Path | None = None,
    ):
        self.model_id = model_id
        self.hf = hf
        self.revision = revision
        self.device = device or _default_device()
        self._model_dir = model_dir
        self._loaded: _Loaded | None = None

    @classmethod
    def for_manifest_id(cls, manifest_id: str = "laya_421m") -> "LayaBackend":
        from z0int.models_mgmt import load_manifest

        meta = (load_manifest().get("models") or {}).get(manifest_id) or {}
        hf = str(meta.get("hf") or "")
        rev = str(meta.get("revision") or "")
        if not hf or not rev:
            raise ValueError(f"manifest {manifest_id!r} missing hf/revision")
        return cls(model_id=manifest_id, hf=hf, revision=rev)

    @property
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            id=self.ID,
            trainable=False,
            supports_boolean=True,
            supports_choice=True,
            supports_score=True,
            max_choice_options=16,
            max_score_levels=10,
            local=True,
        )

    def _model_path(self) -> Path:
        if self._model_dir is not None:
            return self._model_dir.expanduser()
        return _resolve_model_dir(hf=self.hf, revision=self.revision)

    def health(self, *, load: bool = False) -> BackendHealth:
        err = _laya_import_error()
        if err:
            return BackendHealth(
                id=self.ID,
                configured=True,
                ready=False,
                loaded=False,
                model=self.model_id,
                detail=err,
                diagnostics={"hf": self.hf, "manifest_id": self.model_id, "revision": self.revision},
            )
        try:
            path = self._model_path()
            present = path.is_dir() and (path / "model.safetensors").is_file()
        except Exception as exc:  # noqa: BLE001
            return BackendHealth(
                id=self.ID,
                configured=True,
                ready=False,
                loaded=False,
                model=self.model_id,
                detail=f"weights probe failed: {exc}",
                diagnostics={"hf": self.hf, "manifest_id": self.model_id, "revision": self.revision},
            )
        if load:
            try:
                self._ensure_loaded()
                loaded = True
                detail = f"weights loaded on {self.device}"
            except Exception as exc:  # noqa: BLE001
                loaded = False
                detail = f"load failed: {exc}"
        else:
            loaded = False
            detail = "weights present" if present else "weights not cached locally"
        return BackendHealth(
            id=self.ID,
            configured=True,
            ready=present,
            loaded=loaded,
            model=self.model_id,
            detail=detail,
            checkpoint=str(path) if present else None,
            diagnostics={
                "hf": self.hf,
                "manifest_id": self.model_id,
                "revision": self.revision,
                "device": self.device,
                "runtime": "laya",
                "license": "apache-2.0",
                "commercial_use": True,
            },
        )

    def _ensure_loaded(self) -> _Loaded:
        if self._loaded is not None:
            return self._loaded
        import_err = _laya_import_error()
        if import_err:
            raise RuntimeError(import_err)
        import laya

        path = self._model_path()
        if not path.is_dir():
            raise FileNotFoundError(f"laya checkpoint not found: {path}")
        t0 = time.perf_counter()
        agent = laya.load(str(path), device=self.device)
        load_ms = (time.perf_counter() - t0) * 1000.0
        self.device = str(agent.device)
        self._loaded = _Loaded(agent=agent, model_dir=str(path), load_ms=load_ms)
        return self._loaded

    def evaluate(self, request: DecisionRequest) -> DecisionResult:
        loaded = self._ensure_loaded()
        start = time.perf_counter()
        questions = {q.id: question_to_named(q) for q in request.questions}
        raw = loaded.agent.predict(request.state, questions)
        answers: list[DecisionAnswer] = []
        for q in request.questions:
            ans_raw = (raw.get("answers") or {}).get(q.id)
            if not isinstance(ans_raw, dict):
                raise ValueError(f"laya returned no answer for question {q.id!r}")
            answers.append(answer_from_named(q, ans_raw, source="laya"))
        usage = raw.get("usage") or {}
        return DecisionResult(
            backend=self.ID,
            model=self.model_id,
            revision=self.revision,
            answers=tuple(answers),
            latency_ms=(time.perf_counter() - start) * 1000.0,
            diagnostics={
                "input_tokens": usage.get("input_tokens"),
                "output_tokens": usage.get("output_tokens", 0),
                "device": self.device,
                "model_dir": loaded.model_dir,
                "load_ms": loaded.load_ms,
                "runtime": "laya",
                "readout": "non-autoregressive [MASK]-marker softmax over declared options",
                "probability_status": "RLCD-calibrated option probabilities via upstream laya runtime",
            },
        )
