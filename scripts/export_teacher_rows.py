#!/usr/bin/env python3
"""Export Phase 1B observations as teacher-table source rows.

Evolution Lab owns the Q-Route teacher table; z0intelligence owns this
observation format.  The bridge is therefore one-directional and explicit: this
script converts receipts into the ``bounded`` raw row shape
``evolution_lab.q_route.teacher`` already parses, so the table can be rebuilt
from densified evidence without either repo learning the other's internals.

    python scripts/export_teacher_rows.py --run-id p1b-... --out <path>

Residency matters for what a teacher row *means*.  An arm's intrinsic cost is its
warm cost; a cold load or a model swap is a property of the placement policy, not
of the arm.  Folding a 13-second swap into an arm's latency would make the gate
compare placement accidents.  So cold and swap rows are excluded by default and
kept in ``observations.jsonl`` for the residency report; ``--residency all``
includes them for a sensitivity check.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

REPO = Path(__file__).resolve().parents[1]
RESULTS_ROOT = REPO / "results" / "phase1b"

EXPORT_SCHEMA = "z0int.phase1b.teacher_rows.v1"
WARM_CLASSES = ("warm_invocation", "already_resident")


def to_teacher_row(
    row: Mapping[str, Any], fixture: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """One observation receipt -> one ``bounded``-kind teacher source row.

    ``dangerous_actions`` comes from the fixture, not from the receipt, and it is
    load-bearing: the teacher table derives a row's ``compiler`` flag from
    whether the declared dangerous set survived into ``legal_ids``.  Omitting it
    makes an unfiltered arm look compiler-first and lets it win regions it should
    never own -- which is exactly what happened before this was fixed.
    """
    arm = str(row.get("arm_alias") or row.get("arm"))
    declared = [str(a) for a in ((fixture or {}).get("dangerous_actions") or [])]
    return {
        "fixture_id": row.get("state_id"),
        "backend": arm,
        "trajectory_correct": bool(row.get("correct")),
        # ``decision_ms`` is the compute the arm actually did; ``total_ms``
        # includes whatever residency it happened to hit.
        "latency_ms": float(row.get("decision_ms") or row.get("total_ms") or 0.0),
        "legal_ids": list(row.get("legal_actions") or []),
        "candidate_action_count": int(row.get("candidate_action_count") or 0),
        "dangerous_actions": declared,
        "dangerous_selected": bool(row.get("dangerous_selected")),
        "abstained": bool(row.get("abstained")),
        "invalid_call": bool(row.get("invalid_call")),
        "expect_abstain": bool((row.get("verification") or {}).get("expect_abstain")),
        "eliminated": list(row.get("eliminated") or []),
        # Provenance the teacher table does not read, kept for auditability.
        "_phase1b": {
            "run_id": row.get("run_id"),
            "trace_id": row.get("trace_id"),
            "repetition": row.get("repetition"),
            "arm": row.get("arm"),
            "cold_or_warm": row.get("cold_or_warm"),
            "distribution_source": row.get("distribution_source"),
        },
    }


def _fixture_index(fixtures_path: Path | str | None) -> dict[str, dict[str, Any]]:
    """state_id -> fixture row, so the declared dangerous set can be joined on."""
    index: dict[str, dict[str, Any]] = {}
    if not fixtures_path:
        return index
    path = Path(fixtures_path)
    if not path.is_file():
        return index
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("fixture_id"):
                index[str(row["fixture_id"])] = row
    return index


def export(
    observations_path: Path,
    out_path: Path,
    *,
    residency: str = "warm",
    arms: Iterable[str] | None = None,
    fixtures_path: Path | str | None = None,
) -> dict[str, Any]:
    fixtures = _fixture_index(fixtures_path)
    wanted = {a for a in (arms or []) if a}
    kept = 0
    skipped_residency = 0
    skipped_arm = 0
    exposed = 0
    arm_counts: Counter[str] = Counter()
    with observations_path.open(encoding="utf-8") as src, out_path.open(
        "w", encoding="utf-8"
    ) as dst:
        for line in src:
            line = line.strip()
            if not line:
                continue
            obs = json.loads(line)
            if wanted and str(obs.get("arm")) not in wanted and str(obs.get("arm_alias")) not in wanted:
                skipped_arm += 1
                continue
            klass = str(obs.get("cold_or_warm") or "")
            if residency == "warm" and klass not in WARM_CLASSES:
                skipped_residency += 1
                continue
            record = to_teacher_row(obs, fixtures.get(str(obs.get("state_id"))))
            if not record["fixture_id"] or not record["backend"]:
                continue
            dst.write(json.dumps(record, sort_keys=True, default=str) + "\n")
            arm_counts[record["backend"]] += 1
            if set(record["dangerous_actions"]) & set(record["legal_ids"]):
                exposed += 1
            kept += 1
    return {
        "schema": EXPORT_SCHEMA,
        "observations": str(observations_path),
        "out": str(out_path),
        "residency_policy": residency,
        "rows": kept,
        "skipped_residency": skipped_residency,
        "skipped_arm_filter": skipped_arm,
        "fixtures_joined": len(fixtures),
        "rows_with_dangerous_exposed": exposed,
        "arms": dict(sorted(arm_counts.items())),
    }


def main(argv: Iterable[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--run-dir", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--residency", choices=("warm", "all"), default="warm")
    ap.add_argument("--arm", action="append", default=[])
    ap.add_argument("--fixtures", default=None,
                    help="fixture file supplying each state's declared dangerous actions")
    args = ap.parse_args(list(argv) if argv is not None else None)

    run_dir = Path(args.run_dir) if args.run_dir else (RESULTS_ROOT / args.run_id)
    observations = run_dir / "observations.jsonl"
    if not observations.is_file():
        print(f"# no observations at {observations}", file=sys.stderr)
        return 2
    out = Path(args.out) if args.out else (run_dir / "teacher_rows.jsonl")
    summary = export(observations, out, residency=args.residency, arms=args.arm,
                     fixtures_path=args.fixtures)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
