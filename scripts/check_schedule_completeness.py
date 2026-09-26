#!/usr/bin/env python3
"""Schedule completeness for a measured study.

A measured result is only eligible for capability/trust projection when its
schedule is demonstrably complete.  Completion is *computed* from the raw
records against the declared schedule.  It is never inferred from filenames,
process status, prose, or a study's own progress counters -- the 2026-09-22
rep-0 incident was exactly that mistake, and `post_run` counters are written
incrementally, so a finished-looking metadata block can describe a run that
later continued or never finished.

Declared schedule comes from run_metadata.json:
    matrix            {model: [cap, ...]}   -> cells
    fixture.n_states  int                   -> states per repetition
    settings.repetitions int                -> repetitions per cell

Usage:
    python scripts/check_schedule_completeness.py --study results/token-cap-20260922
    python scripts/check_schedule_completeness.py --study DIR --write
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

SCHEMA = "z0int.schedule_completeness.v1"

# A study may legitimately declare a different stopping rule.  Absent one, the
# default applies and cannot be waived by a partial run being "good enough".
DEFAULT_STOPPING_RULE = "states x repetitions per declared cell; no early stop declared"


@dataclass
class CellObservation:
    cell: str
    reps_observed: set[int] = field(default_factory=set)
    states_by_rep: dict[int, set[str]] = field(default_factory=dict)

    def add(self, rep: int, state_id: str) -> None:
        self.reps_observed.add(rep)
        self.states_by_rep.setdefault(rep, set()).add(state_id)


def declared_schedule(meta: Mapping[str, Any]) -> tuple[dict[str, tuple[int, ...]], int, int]:
    """(cells -> caps, states_expected, reps_expected) as declared by the study."""
    matrix = meta.get("matrix")
    if not isinstance(matrix, Mapping) or not matrix:
        raise ValueError("run_metadata.json has no usable 'matrix'; cannot declare cells")
    cells: dict[str, tuple[int, ...]] = {}
    for model, caps in matrix.items():
        if not isinstance(caps, Iterable) or isinstance(caps, (str, bytes)):
            raise ValueError(f"matrix[{model!r}] is not a list of caps")
        caps_t = tuple(int(c) for c in caps)
        if not caps_t:
            raise ValueError(f"matrix[{model!r}] declares no caps")
        cells[str(model)] = caps_t

    fixture = meta.get("fixture") or {}
    states = fixture.get("n_states")
    if not isinstance(states, int) or states <= 0:
        raise ValueError("run_metadata.json fixture.n_states missing; cannot declare states")

    settings = meta.get("settings") or {}
    reps = settings.get("repetitions")
    if not isinstance(reps, int) or reps <= 0:
        raise ValueError("run_metadata.json settings.repetitions missing; cannot declare reps")

    return cells, states, reps


def observe(records: Iterable[Mapping[str, Any]], *, include_warmups: bool = False) -> dict[str, CellObservation]:
    """Per-cell observed coverage from *measured* records.

    Warmups are excluded by default: they are real calls but not campaign
    coverage, and counting them would let a warmup stand in for a missing state.
    """
    out: dict[str, CellObservation] = {}
    for r in records:
        if not include_warmups and r.get("kind") != "measured":
            continue
        if r.get("invalid_cell"):
            continue
        cell = r.get("cell")
        rep = r.get("repetition")
        state = r.get("state_id")
        if cell is None or rep is None or state is None:
            raise ValueError(f"record missing cell/repetition/state_id: {sorted(r)[:8]}")
        obs = out.setdefault(str(cell), CellObservation(cell=str(cell)))
        obs.add(int(rep), str(state))
    return out


def completeness(
    meta: Mapping[str, Any],
    records: Iterable[Mapping[str, Any]],
    *,
    schedule_id: str | None = None,
) -> dict[str, Any]:
    """Computed completeness record for one study."""
    cells, states_expected, reps_expected = declared_schedule(meta)
    observed = observe(records)

    declared_cell_ids = {f"{model}@{cap}" for model, caps in cells.items() for cap in caps}
    reps_expected_set = set(range(reps_expected))

    per_cell: list[dict[str, Any]] = []
    cells_completed = 0
    for cell in sorted(declared_cell_ids):
        obs = observed.get(cell, CellObservation(cell=cell))
        reps_done = sorted(r for r in reps_expected_set if len(obs.states_by_rep.get(r, ())) == states_expected)
        reps_partial = sorted(
            r for r in obs.reps_observed if len(obs.states_by_rep.get(r, ())) < states_expected
        )
        states_completed = len(set().union(*obs.states_by_rep.values())) if obs.states_by_rep else 0
        ok = len(reps_done) == reps_expected and not reps_partial
        # A cell is complete only if every expected repetition is fully covered.
        # Full coverage of a subset of repetitions is still an incomplete cell.
        if ok:
            cells_completed += 1
        per_cell.append(
            {
                "cell": cell,
                "complete": ok,
                "reps_completed": len(reps_done),
                "reps_partial": sorted(reps_partial),
                "states_completed": states_completed,
                "n_measured": sum(len(v) for v in obs.states_by_rep.values()),
            }
        )

    undeclared = sorted(set(observed) - declared_cell_ids)
    reps_completed = max((c["reps_completed"] for c in per_cell), default=0)
    states_completed = max((c["states_completed"] for c in per_cell), default=0)
    total_n = sum(c["n_measured"] for c in per_cell)

    record = {
        "schema": SCHEMA,
        "schedule_id": schedule_id or str(meta.get("run_id") or "unknown"),
        "states_expected": states_expected,
        "reps_expected": reps_expected,
        "cells_expected": len(declared_cell_ids),
        "states_completed": states_completed,
        "reps_completed": reps_completed,
        "reps_completed_note": "maximum over cells, because repetitions need not finish in lockstep; per_cell is authoritative",
        "cells_completed": cells_completed,
        "n_measured": total_n,
        "n_expected": len(declared_cell_ids) * states_expected * reps_expected,
        "complete": bool(declared_cell_ids) and cells_completed == len(declared_cell_ids),
        "stopping_rule": DEFAULT_STOPPING_RULE,
        "undeclared_cells_observed": undeclared,
        "per_cell": per_cell,
    }

    claimed = _claimed_counters(meta)
    if claimed is not None:
        record["declared_progress_counters"] = claimed
        record["declared_counters_agree_with_records"] = claimed == total_n
    return record


def declared_states(meta: Mapping[str, Any], observed: Mapping[str, CellObservation]) -> tuple[list[str], str]:
    """The declared state universe, and where it came from.

    Preferring the fixture matters: a state that no cell ever reached is exactly
    the gap we need to name, and it is invisible in the observations.
    """
    fixture = meta.get("fixture") or {}
    path = fixture.get("path")
    if isinstance(path, str) and Path(path).is_file():
        ids: list[str] = []
        seen: set[str] = set()
        for line in Path(path).read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            sid = rec.get("fixture_id")
            if sid is None:
                raise ValueError(f"fixture record has no fixture_id: {sorted(rec)[:8]}")
            if sid not in seen:
                seen.add(sid)
                ids.append(str(sid))
        if ids:
            return sorted(ids), f"fixture:{path}"

    union: set[str] = set()
    for obs in observed.values():
        for states in obs.states_by_rep.values():
            union |= states
    return sorted(union), "observed-union (fixture path missing or unreadable)"


def missing_work(
    meta: Mapping[str, Any],
    records: Iterable[Mapping[str, Any]],
    *,
    schedule_id: str | None = None,
) -> dict[str, Any]:
    """Exactly which (cell, repetition, state) triples remain, for an exact resume."""
    cells, states_expected, reps_expected = declared_schedule(meta)
    observed = observe(records)
    universe, source = declared_states(meta, observed)

    declared_cell_ids = {f"{model}@{cap}" for model, caps in cells.items() for cap in caps}
    out: list[dict[str, Any]] = []
    for cell in sorted(declared_cell_ids):
        obs = observed.get(cell, CellObservation(cell=cell))
        for rep in range(reps_expected):
            got = obs.states_by_rep.get(rep, set())
            for state in universe:
                if state not in got:
                    out.append({"cell": cell, "repetition": rep, "state_id": state})

    return {
        "schema": SCHEMA + ".missing",
        "schedule_id": schedule_id or str(meta.get("run_id") or "unknown"),
        "state_universe_size": len(universe),
        "state_universe_source": source,
        "universe_matches_declaration": len(universe) == states_expected,
        "n_missing": len(out),
        "missing": out,
    }


def _claimed_counters(meta: Mapping[str, Any]) -> int | None:
    """n_measured as the study's own metadata block asserts it, if present."""
    post = meta.get("post_run")
    if isinstance(post, Mapping) and isinstance(post.get("n_measured_used"), int):
        return int(post["n_measured_used"])
    return None


def may_project_capability(record: Mapping[str, Any]) -> tuple[bool, str]:
    """Gate for capability/trust projection.  Lossy in the safe direction."""
    if record.get("complete") is True:
        return True, "schedule complete"
    return False, (
        "schedule incomplete "
        f"({record.get('cells_completed')}/{record.get('cells_expected')} cells, "
        f"{record.get('n_measured')}/{record.get('n_expected')} measurements); "
        "capability number must be omitted, not annotated"
    )


def _load_study(study: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    meta = json.loads((study / "run_metadata.json").read_text())
    records = [json.loads(l) for l in (study / "raw.jsonl").read_text().splitlines() if l.strip()]
    return meta, records


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--study", required=True, type=Path)
    ap.add_argument("--write", action="store_true", help="write schedule-completeness.json into the study dir")
    args = ap.parse_args()

    meta, records = _load_study(args.study)
    rec = completeness(meta, records)
    ok, why = may_project_capability(rec)
    rec["capability_projection_allowed"] = ok
    rec["capability_projection_reason"] = why

    miss = missing_work(meta, records)
    rec["n_missing"] = miss["n_missing"]
    rec["missing_work_file"] = "resume-point.json"
    if not miss["universe_matches_declaration"]:
        rec["state_universe_warning"] = (
            f"declared {rec['states_expected']} states but fixture/observed universe has "
            f"{miss['state_universe_size']}"
        )

    if args.write:
        out = args.study / "schedule-completeness.json"
        out.write_text(json.dumps(rec, indent=2) + "\n")
        (args.study / "resume-point.json").write_text(json.dumps(miss, indent=2) + "\n")
        print(f"wrote {out}")
        print(f"wrote {args.study / 'resume-point.json'}")

    print(
        "{id}: {cc}/{ce} cells, {nc}/{ne} measurements, complete={c}".format(
            id=rec["schedule_id"], cc=rec["cells_completed"], ce=rec["cells_expected"],
            nc=rec["n_measured"], ne=rec["n_expected"], c=rec["complete"],
        )
    )
    if rec.get("declared_projection_counters_agree") is False or rec.get("declared_counters_agree_with_records") is False:
        print(f"  ! declared progress counters ({rec.get('declared_progress_counters')}) "
              f"disagree with records ({rec['n_measured']})")
    if rec.get("state_universe_warning"):
        print(f"  ! {rec['state_universe_warning']}")
    print(f"  capability projection: {'ALLOWED' if ok else 'BLOCKED'} - {why}")
    for c in rec["per_cell"]:
        print(f"    {'OK ' if c['complete'] else 'PART'} {c['cell']:<34} "
              f"reps={c['reps_completed']}/{rec['reps_expected']} states={c['states_completed']}/{rec['states_expected']} n={c['n_measured']}")
    print(f"  outstanding measurements to finish the declared schedule: {rec['n_missing']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
