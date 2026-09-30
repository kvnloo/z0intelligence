"""z0int adapter for the local NanoJev decision engine."""

from __future__ import annotations

import os
import time
from pathlib import Path

from .base import (
    BackendCapabilities,
    BackendHealth,
    DecisionAnswer,
    DecisionRequest,
    DecisionResult,
)
from .nanojev_runtime import NanoJevEngine, validate_checkpoint_dir


class NanoJevBackend:
    ID = "nanojev"

    def __init__(
        self,
        checkpoint_dir: Path,
        *,
        model_id: str = "nanojev_06b",
        precision: str = "bf16",
        device: str = "cuda:0",
    ):
        self.checkpoint_dir = checkpoint_dir.expanduser()
        self.model_id = model_id
        self.precision = precision
        self.device = device
        self._engine: NanoJevEngine | None = None

    @classmethod
    def from_config(cls) -> "NanoJevBackend":
        from z0int import paths

        raw = os.environ.get("Z0INT_NANOJEV_CHECKPOINT")
        checkpoint = (
            Path(raw).expanduser()
            if raw
            else paths.home() / "models" / "nanojev_06b"
        )
        from .device import default_device

        device = default_device("Z0INT_NANOJEV_DEVICE", cuda="cuda:0")
        precision = os.environ.get("Z0INT_NANOJEV_PRECISION") or ("fp32" if device.startswith("cpu") else "bf16")
        return cls(checkpoint, device=device, precision=precision)

    @property
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            id=self.ID,
            local=True,
            trainable=True,
            supports_boolean=True,
            supports_choice=True,
            supports_score=True,
            max_choice_options=255,
            max_score_levels=10,
            returns_distribution=True,
            autoregressive_decode=False,
            shared_prefix=False,
            supports_observed_outcome_training=True,
            supports_soft_distribution_training=True,
        )

    def health(self, *, load: bool = False) -> BackendHealth:
        missing = validate_checkpoint_dir(self.checkpoint_dir)
        if missing:
            return BackendHealth(
                id=self.ID,
                configured=True,
                ready=False,
                loaded=False,
                detail=f"checkpoint incomplete: missing {missing}",
                model=self.model_id,
                checkpoint=str(self.checkpoint_dir),
                diagnostics={"missing": missing},
            )
        if load:
            try:
                self._ensure_loaded()
            except Exception as exc:
                return BackendHealth(
                    id=self.ID,
                    configured=True,
                    ready=False,
                    loaded=False,
                    detail=f"load failed: {type(exc).__name__}: {exc}",
                    model=self.model_id,
                    checkpoint=str(self.checkpoint_dir),
                )
        return BackendHealth(
            id=self.ID,
            configured=True,
            ready=True,
            loaded=self._engine is not None,
            detail="checkpoint complete" + ("; model loaded" if self._engine else "; not loaded"),
            model=self.model_id,
            checkpoint=str(self.checkpoint_dir),
            diagnostics={"device": self.device, "precision": self.precision},
        )

    def _ensure_loaded(self) -> NanoJevEngine:
        if self._engine is None:
            self._engine = NanoJevEngine(
                self.checkpoint_dir,
                device_name=self.device,
                precision=self.precision,
            )
        return self._engine

    def evaluate(self, request: DecisionRequest) -> DecisionResult:
        engine = self._ensure_loaded()
        start = time.perf_counter()
        raw = engine.predict(request)
        answers: list[DecisionAnswer] = []
        for ex, probs in raw["answers"]:
            distribution = dict(zip(ex.candidate_ids, probs))
            best = max(range(len(probs)), key=probs.__getitem__)
            if ex.type == "boolean":
                value = bool(best)
            elif ex.type == "choice":
                value = ex.candidate_ids[best]
            else:
                value = sum(i * p for i, p in enumerate(probs))
            answers.append(
                DecisionAnswer(
                    question_id=ex.question_id,
                    type=ex.type,
                    probabilities=distribution,
                    value=value,
                    confidence=max(probs),
                )
            )
        return DecisionResult(
            backend=self.ID,
            model=self.model_id,
            revision=str(engine.config.get("resolved_model_revision") or "") or None,
            answers=tuple(answers),
            latency_ms=(time.perf_counter() - start) * 1000.0,
            diagnostics={
                "forward_ms": raw["forward_ms"],
                "questions": raw["questions"],
                "candidate_paths": raw["candidate_paths"],
                "autoregressive_decode_steps": 0,
                "network_model_calls": 0,
                "prefix_sharing": False,
                "truncated_questions": raw.get("truncated_questions", []),
                "probability_status": (
                    "complete normalized decision distribution; "
                    "calibration is checkpoint/task dependent"
                ),
            },
        )
