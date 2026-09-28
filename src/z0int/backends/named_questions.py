"""Shared z0int ⇄ named-question decision transport.

Several local decision backends (``laya``, ``decider``, ``julia``) speak the same
named-question wire shape: a mapping of caller question IDs to
``{type, instructions, criteria}``, answered with
``{type, probabilities, choice|score|noul, max_probability?}``.

That mapping was written out twice, verbatim, in ``backends/laya.py`` and
``backends/decider.py``. It lives here once so a third backend cannot drift from
the first two, and so the one place that knows the caller-ID contract is the one
place that gets changed.

Nothing here touches torch, transformers, model weights, or the network: it is
pure request/response translation over ``backends.base`` value types.
"""

from __future__ import annotations

import math
from typing import Any

from .base import DecisionAnswer, DecisionQuestion

DEFAULT_FALSE_CRITERION = "no, the statement does not hold"
DEFAULT_TRUE_CRITERION = "yes, the statement holds"


def normalize_probs(probs: dict[str, Any], *, source: str) -> dict[str, float]:
    """Coerce a model's probability mapping to finite, non-negative, summing to 1."""
    cleaned: dict[str, float] = {}
    for key, val in probs.items():
        fval = float(val)
        if not math.isfinite(fval) or fval < 0 or fval > 1:
            raise ValueError(f"invalid probability for {key!r}: {val!r}")
        cleaned[str(key)] = fval
    total = sum(cleaned.values())
    if total <= 0:
        raise ValueError(f"empty probability mass from {source}")
    return {k: v / total for k, v in cleaned.items()}


def question_to_named(q: DecisionQuestion) -> dict[str, Any]:
    """Render a z0int question in the shared named-question transport shape.

    ``boolean`` maps onto the wire type ``noul``; ``choice`` criteria are keyed by
    the caller's option IDs so they survive the round trip; ``score`` criteria are
    the ordered level descriptions.
    """
    if q.type == "boolean":
        return {
            "type": "noul",
            "instructions": q.instructions,
            "criteria": {
                "false": q.false_criterion or DEFAULT_FALSE_CRITERION,
                "true": q.true_criterion or DEFAULT_TRUE_CRITERION,
            },
        }
    if q.type == "choice":
        return {
            "type": "choice",
            "instructions": q.instructions,
            "criteria": {o.id: o.description for o in q.options},
        }
    return {
        "type": "score",
        "instructions": q.instructions,
        "criteria": list(q.levels),
    }


def answer_from_named(q: DecisionQuestion, raw: dict[str, Any], *, source: str) -> DecisionAnswer:
    """Translate one named-question answer back into the z0int decision result."""
    qtype = raw.get("type")
    conf = raw.get("confidence")
    confidence = float(conf) if conf is not None else None

    if qtype == "noul" or q.type == "boolean":
        p_true = float(raw.get("noul", 0.5))
        probs = normalize_probs({"false": 1.0 - p_true, "true": p_true}, source=source)
        return DecisionAnswer(
            question_id=q.id,
            type="boolean",
            probabilities=probs,
            value=p_true >= 0.5,
            confidence=confidence,
        )

    if qtype == "choice" or q.type == "choice":
        probs_raw = raw.get("probabilities") or {}
        probs = normalize_probs({str(k): float(v) for k, v in probs_raw.items()}, source=source)
        choice = str(raw.get("choice") or max(probs, key=probs.get))
        return DecisionAnswer(
            question_id=q.id,
            type="choice",
            probabilities=probs,
            value=choice,
            confidence=confidence,
        )

    probs_raw = raw.get("probabilities") or {}
    probs = normalize_probs({str(k): float(v) for k, v in probs_raw.items()}, source=source)
    score_val = raw.get("score")
    if score_val is None and probs:
        score_val = sum(int(k) * v for k, v in probs.items())
    return DecisionAnswer(
        question_id=q.id,
        type="score",
        probabilities=probs,
        value=float(score_val if score_val is not None else 0.0),
        confidence=confidence,
    )
