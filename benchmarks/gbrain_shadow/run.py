"""Deterministic GBrain shadow-policy fixture runner.

No network, no model calls. This is a contract/safety eval, not a quality claim.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from z0int.gbrain import build_shadow_candidate

HERE = Path(__file__).resolve().parent
CASES = HERE / "cases.json"


def run_cases(path: Path = CASES) -> dict[str, Any]:
    suite = json.loads(path.read_text(encoding="utf-8"))
    rows = []
    passed = 0
    for case in suite["cases"]:
        candidate = build_shadow_candidate(case["response"], verb=case["verb"])
        action = candidate["attention"]["action"]
        reasons = candidate["attention"]["reason_codes"]
        ok = (
            action == case["expected_action"]
            and case["expected_reason"] in reasons
            and candidate["shadow_only"] is True
            and candidate["traffic_eligible"] is False
            and candidate["authority"]["granted"] == []
            and action not in {"ACT", "SURFACE"}
        )
        rows.append(
            {
                "id": case["id"],
                "ok": ok,
                "action": action,
                "expected_action": case["expected_action"],
                "reason_codes": reasons,
            }
        )
        passed += int(ok)
    return {
        "schema": "z0int.gbrain_shadow_eval_result.v1",
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
