"""Linear decision backend: ridge and logistic over hashed state features.

The cheapest transferred baseline in the stack was a ridge fit on chronological
real traffic. That result had no backend, so it could not be served, could not be
compared against a model, and could not participate in placement. This makes it
a first-class ``DecisionBackend`` on the same contract as every other one.

Design constraints, each deliberate:

* **Dependency-light.** numpy only. No torch, no weights download, no GPU. A
  backend that needs a GPU cannot be the thing used to prove that a host process
  and a service agree.
* **Deterministic across processes.** Features are hashed with ``blake2b``, not
  ``hash()``. Python's string hash is salted per process, so a ``hash()``-based
  feature map would give different features in the host and in a worker process
  and would fail an otherwise correct parity check for the wrong reason.
* **Calibrated enough to abstain.** Every answer carries ``confidence`` so a
  caller can sweep a threshold and trade coverage against accuracy, instead of
  being forced to answer.
* **Honest about provenance.** ``revision`` is ``untrained`` until weights are
  fitted or loaded, and the untrained model returns a uniform distribution
  rather than pretending to a preference.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np

from .base import (
    BackendCapabilities,
    BackendHealth,
    DecisionAnswer,
    DecisionRequest,
    DecisionQuestion,
    DecisionResult,
)

SCHEMA = "z0int.linear_backend.v1"
DEFAULT_DIMS = 256
ABSTAIN = "abstain"


# ------------------------------------------------------------------ features


def _tokens(state: Any, question_id: str) -> Iterable[str]:
    """Stable token stream for any JSON-compatible state."""
    if question_id:
        yield f"q={question_id}"

    def walk(prefix: str, value: Any) -> Iterable[str]:
        if isinstance(value, dict):
            for key in sorted(value, key=str):
                child = f"{prefix}.{key}" if prefix else str(key)
                yield from walk(child, value[key])
        elif isinstance(value, (list, tuple)):
            for i, item in enumerate(value):
                yield from walk(f"{prefix}[{i}]", item)
        else:
            yield f"{prefix}={value}"

    if isinstance(state, str):
        for word in state.split():
            yield f"text={word.lower()}"
    else:
        yield from walk("", state)


def _bucket(token: str, dims: int) -> int:
    # blake2b, not hash(): Python's str hash is salted per process, so a
    # hash()-based feature map would differ between a host and a worker process.
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % dims


def features(state: Any, question_id: str, dims: int) -> np.ndarray:
    vector = np.zeros(dims, dtype=np.float64)
    for token in _tokens(state, question_id):
        vector[_bucket(token, dims)] += 1.0
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm > 0.0 else vector


def candidates(question: DecisionQuestion) -> list[str]:
    """The label space of a question, in a stable order."""
    if question.type == "boolean":
        return ["false", "true"]
    if question.type == "choice":
        return [opt.id for opt in question.options]
    return list(question.levels)


# ------------------------------------------------------------------- model


class LinearDecisionBackend:
    """One-vs-rest linear scores over hashed features, softmaxed per question."""

    ID = "linear"

    def __init__(
        self,
        *,
        kind: str = "ridge",
        dims: int = DEFAULT_DIMS,
        ridge_lambda: float = 1.0,
        confidence_threshold: float = 0.0,
        weights: dict[str, np.ndarray] | None = None,
        bias: dict[str, float] | None = None,
        revision: str | None = None,
    ) -> None:
        if kind not in ("ridge", "logistic"):
            raise ValueError(f"unsupported linear kind: {kind!r}")
        self.kind = kind
        self.dims = int(dims)
        self.ridge_lambda = float(ridge_lambda)
        self.confidence_threshold = float(confidence_threshold)
        self.weights: dict[str, np.ndarray] = dict(weights or {})
        self.bias: dict[str, float] = dict(bias or {})
        self.revision = revision or ("fitted" if self.weights else "untrained")

    # ---- metadata ---------------------------------------------------
    @property
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            id=self.ID,
            local=True,
            supports_boolean=True,
            supports_choice=True,
            supports_score=True,
            max_choice_options=255,
            max_score_levels=10,
            kind="linear",
            description=(
                f"{self.kind} linear baseline over {self.dims} hashed state features. "
                "Cheap, CPU-resident, abstains on low confidence."
            ),
            trainable=True,
            returns_distribution=True,
        )

    def digest(self) -> str:
        """Stable identity of the fitted model, for comparison and receipts."""
        payload = json.dumps(
            {
                "schema": SCHEMA,
                "kind": self.kind,
                "dims": self.dims,
                "weights": {k: [round(float(x), 12) for x in v] for k, v in sorted(self.weights.items())},
                "bias": {k: round(float(v), 12) for k, v in sorted(self.bias.items())},
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def health(self, *, load: bool = False) -> BackendHealth:
        return BackendHealth(
            id=self.ID,
            configured=True,
            ready=True,
            loaded=bool(self.weights),
            detail="numpy only; no weights required to construct",
            model=f"linear-{self.kind}",
            checkpoint=None,
            diagnostics={"dims": self.dims, "revision": self.revision, "digest": self.digest()},
        )

    # ---- fitting ----------------------------------------------------
    def fit(self, examples: Sequence[dict[str, Any]]) -> "LinearDecisionBackend":
        """Fit on rows of ``{"state", "question_id", "answer"}``.

        One-vs-rest: for each label seen anywhere, fit a linear score for
        "this label" against "any other label".
        """
        if not examples:
            raise ValueError("fit requires at least one example")
        labels = sorted({str(e["answer"]) for e in examples})
        design = np.vstack(
            [features(e["state"], str(e["question_id"]), self.dims) for e in examples]
        )
        for label in labels:
            target = np.array(
                [1.0 if str(e["answer"]) == label else -1.0 for e in examples], dtype=np.float64
            )
            if self.kind == "ridge":
                weight, intercept = self._fit_ridge(design, target)
            else:
                weight, intercept = self._fit_logistic(design, target)
            self.weights[label] = weight
            self.bias[label] = intercept
        self.revision = f"fitted:{len(examples)}"
        return self

    def _fit_ridge(self, design: np.ndarray, target: np.ndarray) -> tuple[np.ndarray, float]:
        gram = design.T @ design + self.ridge_lambda * np.eye(self.dims)
        weight = np.linalg.solve(gram, design.T @ target)
        intercept = float(np.mean(target) - float(np.mean(design @ weight)))
        return weight, intercept

    def _fit_logistic(
        self, design: np.ndarray, target: np.ndarray, *, epochs: int = 200, lr: float = 0.5
    ) -> tuple[np.ndarray, float]:
        weight = np.zeros(self.dims, dtype=np.float64)
        intercept = 0.0
        labels01 = (target > 0).astype(np.float64)
        n = len(labels01)
        for _ in range(epochs):
            logits = design @ weight + intercept
            pred = 1.0 / (1.0 + np.exp(-np.clip(logits, -30, 30)))
            error = pred - labels01
            weight -= lr * ((design.T @ error) / n + self.ridge_lambda * weight / n)
            intercept -= lr * float(np.mean(error))
        return weight, intercept

    # ---- persistence ------------------------------------------------
    def save(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(
                {
                    "schema": SCHEMA,
                    "kind": self.kind,
                    "dims": self.dims,
                    "ridge_lambda": self.ridge_lambda,
                    "confidence_threshold": self.confidence_threshold,
                    "revision": self.revision,
                    "digest": self.digest(),
                    "weights": {k: [float(x) for x in v] for k, v in self.weights.items()},
                    "bias": {k: float(v) for k, v in self.bias.items()},
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        return target

    @classmethod
    def load(cls, path: str | Path) -> "LinearDecisionBackend":
        payload = json.loads(Path(path).read_text())
        if payload.get("schema") != SCHEMA:
            raise ValueError(f"not a {SCHEMA} document: {payload.get('schema')!r}")
        backend = cls(
            kind=str(payload["kind"]),
            dims=int(payload["dims"]),
            ridge_lambda=float(payload.get("ridge_lambda", 1.0)),
            confidence_threshold=float(payload.get("confidence_threshold", 0.0)),
            weights={k: np.array(v, dtype=np.float64) for k, v in (payload.get("weights") or {}).items()},
            bias={k: float(v) for k, v in (payload.get("bias") or {}).items()},
            revision=payload.get("revision"),
        )
        recorded = payload.get("digest")
        if recorded and recorded != backend.digest():
            raise ValueError("weights digest does not match the loaded model")
        return backend

    # ---- inference ---------------------------------------------------
    def _scores(self, state: Any, question: DecisionQuestion, labels: list[str]) -> dict[str, float]:
        x = features(state, question.id, self.dims)
        out: dict[str, float] = {}
        for label in labels:
            weight = self.weights.get(label)
            out[label] = (float(weight @ x) + self.bias.get(label, 0.0)) if weight is not None else 0.0
        return out

    @staticmethod
    def _softmax(scores: dict[str, float]) -> dict[str, float]:
        if not scores:
            return {}
        top = max(scores.values())
        exps = {k: math.exp(v - top) for k, v in scores.items()}
        total = math.fsum(exps.values())
        if total <= 0.0:
            uniform = 1.0 / len(scores)
            return {k: uniform for k in scores}
        return {k: v / total for k, v in exps.items()}

    def _answer_for(self, state: Any, question: DecisionQuestion) -> DecisionAnswer:
        labels = candidates(question)
        if not labels:
            raise ValueError(f"question {question.id!r} has no label space")
        probabilities = self._softmax(self._scores(state, question, labels))
        # Ties must resolve deterministically and identically in every process.
        best = max(labels, key=lambda lab: (probabilities[lab], lab))
        confidence = probabilities[best]
        value: bool | str | float = best
        if question.type == "boolean":
            value = best == "true"
        elif question.type == "score":
            value = best
        if (
            self.confidence_threshold > 0.0
            and confidence < self.confidence_threshold
            and ABSTAIN in labels
        ):
            value = ABSTAIN
        return DecisionAnswer(
            question_id=question.id,
            type=question.type,
            probabilities=probabilities,
            value=value,
            confidence=confidence,
        )

    def evaluate(self, request: DecisionRequest) -> DecisionResult:
        started = time.perf_counter()
        answers = tuple(self._answer_for(request.state, q) for q in request.questions)
        elapsed = (time.perf_counter() - started) * 1000.0
        return DecisionResult(
            backend=self.ID,
            model=f"linear-{self.kind}",
            revision=self.revision,
            answers=answers,
            latency_ms=elapsed,
            # Deterministic: no timing, no timestamps. Parity compares results,
            # and anything process-local here would make them incomparable.
            diagnostics={"dims": self.dims, "digest": self.digest(), "kind": self.kind},
        )
