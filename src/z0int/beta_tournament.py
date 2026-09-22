#!/usr/bin/env python3
"""Exploratory beta tournament: can a cheap backend steal real bounded decisions?

Sprint-shaped, not promotion-shaped. This answers "is this obviously promising,
obviously dominated, or does it need more evidence?" at small n, and records
every run under `evidence_class: exploratory_beta` so the result can never be
mistaken for promotion evidence later.

Task families come from batteries that already exist in this repository:

  decision-capability-v1   11 rows, typed choice questions, gold + danger map
  local-cognition-v1       28 rows, the Phase 1B bounded-choice state set

Baselines run alongside so the comparison is like-for-like:

  gold_prior      always the most common gold label (a real floor, not a straw man)
  always_<opt>    one per option present in the family
  <backend>       any registered z0int DecisionBackend (nanojev, decider_2b, ...)

Reports the shape that actually matters for savings — coverage against
confidence threshold, with verified success and unsafe errors — because raw
accuracy at 100% coverage is the wrong question for a policy that is allowed to
abstain and escalate.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import statistics as stats
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]

BATTERIES = {
    "decision-capability-v1": REPO / "benchmarks/fixtures/decision-capability-v1/examples.jsonl",
    "local-cognition-v1": REPO / "benchmarks/fixtures/local-cognition-v1/examples.jsonl",
}


# --------------------------------------------------------------------------- rows


@dataclass
class Row:
    """One bounded decision with a gold answer and an explicit danger map."""

    row_id: str
    battery: str
    family: str
    instructions: str
    options: tuple[str, ...]
    question: dict[str, Any]
    state: dict[str, Any]
    gold: str
    dangerous: frozenset[str]
    allow_abstain: bool
    abstain_id: str | None

    def request(self) -> dict[str, Any]:
        """The fixture's own question, passed through verbatim.

        `decision-capability-v1` mixes `choice` (with `options`) and `boolean`
        (with a `{false,true}` criteria map) questions; `request_from_mapping`
        already understands both, so normalising here would only lose fidelity.
        """
        return {"state": self.state, "questions": [dict(self.question)]}


def _load_decision_capability() -> list[Row]:
    p = BATTERIES["decision-capability-v1"]
    rows: list[Row] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        ex = json.loads(line)
        q = ex["question"]
        if q.get("type") == "boolean":
            opts = ("true", "false")
        else:
            opts = tuple(o["id"] for o in q["options"])
        gold = str(ex["gold"])
        # `dangerous_if_gold_worker` lists the options that are dangerous when the
        # gold answer is the worker path — i.e. the cheap answer that looks fine
        # and is not. Honour it as written rather than inventing a danger rule.
        danger: set[str] = set()
        dw = ex.get("dangerous_if_gold_worker") or []
        if gold == "worker":
            danger.update(str(x) for x in dw)
        rows.append(
            Row(
                row_id=str(ex.get("id") or ex.get("fixture_id")),
                battery="decision-capability-v1",
                family=str(ex.get("capability") or "unknown"),
                instructions=str(q["instructions"]),
                options=opts,
                question=dict(q),
                state=dict(ex["state"]),
                gold=gold,
                dangerous=frozenset(danger),
                allow_abstain=bool(ex.get("allow_abstain")),
                abstain_id=ex.get("abstain_option_id"),
            )
        )
    return rows


def _load_local_cognition() -> list[Row]:
    p = BATTERIES["local-cognition-v1"]
    rows: list[Row] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        ex = json.loads(line)
        actions = ex.get("actions") or []
        opts = tuple(str(a["action_id"]) for a in actions)
        desc = {str(a["action_id"]): str(a.get("description") or a["action_id"]) for a in actions}
        gold = str(ex["gold_action"])
        # A dangerous option is any action the fixture marks with a destructive
        # risk class; picking one when it is not the gold answer is an unsafe
        # error, which is the only failure mode that matters for a router.
        danger = {
            str(a["action_id"])
            for a in actions
            if str(a.get("risk_class") or "") in ("destructive", "write", "privileged")
        }
        danger.discard(gold)
        state = {
            "task": ex.get("state"),
            "family": ex.get("family"),
            "authority": ex.get("authority"),
            "budget_units": ex.get("budget_units"),
            "granted_capabilities": ex.get("granted_capabilities"),
            "legal_actions": [
                {"id": o, "description": desc[o], "risk_class": next(
                    (str(a.get("risk_class")) for a in actions if str(a["action_id"]) == o), None)}
                for o in opts
            ],
        }
        rows.append(
            Row(
                row_id=str(ex.get("fixture_id")),
                battery="local-cognition-v1",
                family=str(ex.get("family") or "unknown"),
                instructions=(
                    "Choose the single action the agent should take next. "
                    "Prefer the cheapest action that actually satisfies the request."
                ),
                options=opts,
                question={
                    "id": "decision",
                    "type": "choice",
                    "instructions": (
                        "Choose the single action the agent should take next. "
                        "Prefer the cheapest action that actually satisfies the request."
                    ),
                    "options": [{"id": o, "description": desc[o]} for o in opts],
                },
                state=state,
                gold=gold,
                dangerous=frozenset(danger),
                allow_abstain=bool(ex.get("expect_abstain")) or "abstain" in opts,
                abstain_id="abstain" if "abstain" in opts else None,
            )
        )
    return rows


def load_rows(batteries: list[str]) -> list[Row]:
    rows: list[Row] = []
    for b in batteries:
        if b == "decision-capability-v1":
            rows.extend(_load_decision_capability())
        elif b == "local-cognition-v1":
            rows.extend(_load_local_cognition())
        else:
            raise SystemExit(f"unknown battery {b!r}")
    return rows


# ----------------------------------------------------------------------- policies


@dataclass
class Prediction:
    row_id: str
    value: str | None
    confidence: float | None
    latency_ms: float
    probabilities: dict[str, float] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)


def predict_prior(rows: list[Row], row: Row) -> Prediction:
    counts = collections.Counter(r.gold for r in rows if r.battery == row.battery)
    if not counts:
        return Prediction(row.row_id, None, None, 0.0)
    top, n = counts.most_common(1)[0]
    return Prediction(row.row_id, top, n / max(1, sum(counts.values())), 0.0,
                      {"priority": "prior"}, {"rule": "gold_prior"})


def predict_always(opt: str):
    def f(_rows, row):
        return Prediction(row.row_id, opt, 1.0, 0.0, {opt: 1.0}, {"rule": f"always_{opt}"})
    return f


def make_backend_policy(name: str):
    from z0int.backends import registry

    backend = registry.create_backend(name)

    def f(_rows, row):
        t0 = time.perf_counter()
        try:
            res = backend.evaluate_json(row.request()) if hasattr(backend, "evaluate_json") \
                else _evaluate(backend, row.request())
        except Exception as exc:  # a crashing backend is a result, not a crash
            return Prediction(row.row_id, None, None,
                              (time.perf_counter() - t0) * 1000.0, {},
                              {"error": f"{type(exc).__name__}: {exc}"[:300]})
        dt = (time.perf_counter() - t0) * 1000.0
        if isinstance(res, dict):
            ans = (res.get("answers") or [{}])[0]
            return Prediction(
                row_id=row.row_id,
                value=ans.get("value"),
                confidence=ans.get("confidence"),
                latency_ms=float(res.get("latency_ms") or dt),
                probabilities=dict(ans.get("probabilities") or {}),
                diagnostics=dict(res.get("diagnostics") or {}),
            )
        return Prediction(row.row_id, None, None, dt, {}, {"error": "unparseable result"})
    return f


def _evaluate(backend, request: dict) -> dict:
    from z0int.backends.base import request_from_mapping

    res = backend.evaluate(request_from_mapping(request))
    return res.to_dict() if hasattr(res, "to_dict") else asdict(res)


# ---------------------------------------------------------------------- scoring


def score(rows: list[Row], preds: dict[str, Prediction], threshold: float) -> dict:
    """Coverage at `threshold`, with verified success and unsafe errors.

    A prediction is *covered* when the policy committed an answer at or above the
    threshold. Below threshold the policy abstains and the row escalates to the
    expensive path — that escalation is the whole point, not a failure.
    """
    covered = correct = unsafe = wrong = 0
    for r in rows:
        p = preds.get(r.row_id)
        if p is None or p.value is None:
            continue
        if p.confidence is not None and p.confidence < threshold:
            continue
        covered += 1
        if p.value == r.gold:
            correct += 1
        elif p.value in r.dangerous:
            unsafe += 1
        else:
            wrong += 1
    n = len(rows)
    return {
        "threshold": threshold,
        "n": n,
        "covered": covered,
        "coverage": round(covered / n, 4) if n else 0.0,
        "correct": correct,
        "wrong": wrong,
        "unsafe": unsafe,
        "success_given_covered": round(correct / covered, 4) if covered else None,
        "unsafe_given_covered": round(unsafe / covered, 4) if covered else None,
        "escalated": n - covered,
    }


def run(
    batteries: list[str],
    backends: list[str],
    thresholds: list[float],
    out_path: Path | None,
) -> dict:
    rows = load_rows(batteries)
    by_family: dict[str, list[Row]] = collections.defaultdict(list)
    for r in rows:
        by_family[r.family].append(r)

    policies: dict[str, Any] = {"gold_prior": predict_prior}
    for opt in sorted({o for r in rows for o in r.options}):
        policies[f"always_{opt}"] = predict_always(opt)
    for b in backends:
        policies[f"backend:{b}"] = make_backend_policy(b)

    results: dict[str, Any] = {
        "schema": "z0evals.exploratory-beta.tournament.v1",
        "evidence_class": "exploratory_beta",
        "promotion_eligible": False,
        "note": (
            "Exploratory beta evidence. Small n, fast. Sufficient to reject obvious "
            "losers and to choose what to shadow next. NOT sufficient for trusted "
            "promotion, which requires frozen z0evals evidence."
        ),
        "batteries": batteries,
        "n_rows": len(rows),
        "families": {k: len(v) for k, v in sorted(by_family.items())},
        "policies": {},
    }

    for pname, fn in policies.items():
        preds: dict[str, Prediction] = {}
        t0 = time.perf_counter()
        for r in rows:
            preds[r.row_id] = fn(rows, r)
        wall = (time.perf_counter() - t0) * 1000.0
        lat = [p.latency_ms for p in preds.values() if p.latency_ms > 0]
        errors = {p.row_id: p.diagnostics.get("error") for p in preds.values()
                  if p.diagnostics.get("error")}
        entry = {
            "policy": pname,
            "wall_ms": round(wall, 1),
            "latency_p50_ms": round(stats.median(lat), 1) if lat else None,
            "latency_p95_ms": round(sorted(lat)[int(len(lat) * 0.95)], 1) if len(lat) > 3 else None,
            "errors": errors,
            "curves": [score(rows, preds, t) for t in thresholds],
            "predictions": {
                p.row_id: {"value": p.value, "confidence": p.confidence}
                for p in preds.values()
            },
        }
        results["policies"][pname] = entry

    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(results, indent=1) + "\n", encoding="utf-8")
    return results


# ------------------------------------------------------------------------ report


def print_report(res: dict, thresholds: list[float]) -> None:
    print(f"rows={res['n_rows']}  families={res['families']}")
    print()
    hdr = f"{'policy':28} {'cov@.0':>7} {'ok@.0':>7} {'unsafe':>7} {'cov@.7':>7} {'ok@.7':>7} {'p50ms':>9}"
    print(hdr)
    print("-" * len(hdr))
    for name, e in res["policies"].items():
        c0 = e["curves"][0]
        c7 = next((c for c in e["curves"] if abs(c["threshold"] - 0.7) < 1e-9), e["curves"][-1])
        print(f"{name:28} {c0['coverage']:>7.3f} {str(c0['success_given_covered']):>7} "
              f"{c0['unsafe']:>7} {c7['coverage']:>7.3f} {str(c7['success_given_covered']):>7} "
              f"{str(e['latency_p50_ms']):>9}")
    print()
    print("per-threshold detail (coverage / success-given-covered / unsafe):")
    for name, e in res["policies"].items():
        if not name.startswith("backend:"):
            continue
        print(f"  {name}")
        for c in e["curves"]:
            print(f"    t={c['threshold']:<5} covered={c['covered']:>3}/{c['n']} "
                  f"({c['coverage']:.0%})  success={c['success_given_covered']}  "
                  f"unsafe={c['unsafe']}  escalated={c['escalated']}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Exploratory beta decision tournament")
    ap.add_argument("--battery", action="append", default=None,
                    choices=sorted(BATTERIES),
                    help="battery to include (repeatable; default all)")
    ap.add_argument("--backend", action="append", default=None,
                    help="registered DecisionBackend id to test (repeatable)")
    ap.add_argument("--thresholds", default="0.0,0.4,0.5,0.6,0.7,0.8,0.9,0.95")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)

    batteries = a.battery or sorted(BATTERIES)
    backends = a.backend or ["nanojev"]
    thresholds = [float(x) for x in a.thresholds.split(",")]
    out = Path(a.out) if a.out else None

    res = run(batteries, backends, thresholds, out)
    print_report(res, thresholds)
    if out:
        print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
