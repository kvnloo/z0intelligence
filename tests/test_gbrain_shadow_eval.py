from __future__ import annotations

import importlib.util
from pathlib import Path


def _runner():
    path = Path(__file__).resolve().parents[1] / "benchmarks" / "gbrain_shadow" / "run.py"
    spec = importlib.util.spec_from_file_location("gbrain_shadow_eval", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_gbrain_shadow_contract_fixture_suite() -> None:
    result = _runner().run_cases()
    assert result["passed"] == result["total"]
    assert result["total"] >= 8


def test_incomplete_memory_never_collapses_to_ignore() -> None:
    result = _runner().run_cases()
    by_id = {row["id"]: row for row in result["rows"]}
    assert by_id["budget-dropped-empty"]["action"] == "OBSERVE"
    assert by_id["degraded-empty-pack"]["action"] == "OBSERVE"
