#!/usr/bin/env python3
"""Reject well-typed weakenings of the pure goal-continuity contract.

This checks the specification, not a live harness or host attestations.
Run from the repository root: python benchmarks/goal_continuity_law_mutations.py
"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from bend_gate_law_mutations import run_proof

KERNEL = Path(__file__).resolve().parents[1] / "bend" / "goal_continuity"
MUTATIONS = (
    ("missing_workflow_bypassed", "      LoadWorkflow{}", "      Execute{}"),
    ("unfinished_work_completed", "          Execute{}", "          Finish{}"),
    ("exhausted_plan_idles", "        case ExhaustedPlan{}:\n          Replan{}", "        case ExhaustedPlan{}:\n          Idle{}"),
    ("unregistered_wait", "            case Unregistered{}:\n              Replan{}", "            case Unregistered{}:\n              Wait{0n}"),
    ("user_stop_ignored", "      RespectStop{}", "      Replan{}"),
)


def main() -> int:
    baseline, output, _ = run_proof(KERNEL, True)
    print(json.dumps({"case": "baseline", "proofs_check": baseline}))
    if not baseline:
        print(output)
        return 1
    failures = 0
    for name, old, new in MUTATIONS:
        with tempfile.TemporaryDirectory(prefix="goal-continuity-mutation-") as directory:
            root = Path(directory) / "kernel"
            shutil.copytree(KERNEL, root, ignore=shutil.ignore_patterns("build"))
            source = root / "contract.bend"
            original = source.read_text()
            if original.count(old) != 1:
                raise RuntimeError(f"Mutation {name} must target exactly one branch")
            source.write_text(original.replace(old, new, 1))
            typed, type_output, _ = run_proof(root, False, entry="contract.bend")
            proved, proof_output, _ = run_proof(root, True)
            rejected = typed and not proved
            print(json.dumps({"case": name, "program_typechecks": typed, "weakening_rejected": rejected}))
            if not rejected:
                print(type_output)
                print(proof_output)
                failures += 1
    return int(failures != 0)


if __name__ == "__main__":
    raise SystemExit(main())
