"""Deterministic fixtures for the GStack context-value shadow adapter."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from z0int.gstack_context import normalize_context_bill

HERE = Path(__file__).resolve().parent
CASES = HERE / "cases.json"


def run_cases(path: Path = CASES) -> dict[str, Any]:
    suite = json.loads(path.read_text(encoding="utf-8"))
    rows = []
    passed = 0
    for case in suite["cases"]:
        result = normalize_context_bill(case["bill"])
        candidates = result["candidates"]
        order = [row["skill"] for row in candidates]
        ok = order == case["expected_order"]
        by_name = {row["skill"]: row for row in candidates}

        for name, expected in (case.get("expected_density") or {}).items():
            actual = by_name[name]["value_density"]
            ok = ok and ((expected is None and actual is None) or actual == expected)

        expected_cost_state = case.get("expected_cost_state")
        if expected_cost_state:
            ok = ok and candidates[0]["cost"]["state"] == expected_cost_state

        expected_reason = case.get("expected_reason")
        if expected_reason:
            ok = ok and expected_reason in candidates[0]["reason_codes"]

        ok = ok and all(
            row["shadow_only"]
            and not row["traffic_eligible"]
            and row["authority"]["granted"] == []
            and row["outcome"]["verified"] is False
            for row in candidates
        )
        rows.append({"id": case["id"], "ok": ok, "order": order})
        passed += int(ok)

    return {
        "schema": "z0int.gstack_context_value_eval_result.v1",
        "passed": passed,
        "total": len(rows),
        "rows": rows,
    }


def main() -> int:
    result = run_cases()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["passed"] == result["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
