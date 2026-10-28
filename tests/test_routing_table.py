"""Offline acceptance tests for the read-only capability routing table."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys

import pytest

from z0int.intelligence import REGISTRY
from z0int.routing_table import build_table, render_markdown, summary


TEXT_WORKERS = {
    "summarization",
    "research",
    "code_review",
    "coding_implementation",
    "cheap_bounded_worker",
}


def _entry(
    *,
    function: str = "research",
    provider: str = "local",
    model: str = "qwen2.5:3b",
    local: bool = True,
    eligible: bool = True,
) -> dict:
    """Use a real committed file as reproducible synthetic evidence."""
    evidence_file = REGISTRY.parent.parent / "README.md"
    return {
        "function": function,
        "provider": provider,
        "model": model,
        "route": "LOCAL_MODEL" if local else "REMOTE_MODEL",
        "local": local,
        "eligible": eligible,
        "eval_source": {
            "path": "README.md",
            "sha256": hashlib.sha256(evidence_file.read_bytes()).hexdigest(),
        },
        "quality_metric": {"name": "synthetic_fixture", "n": 1},
    }


def test_committed_text_workers_have_no_routable_rows():
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    rows = build_table(registry)
    totals = summary(rows)

    assert len(rows) == len(registry["entries"])
    assert TEXT_WORKERS <= totals.keys()
    for function in TEXT_WORKERS:
        group = [row for row in rows if row["function"] == function]
        assert group, function
        assert all(row["eligible"] is False for row in group)
        assert all(row["blocker"] == "NOT_ELIGIBLE" for row in group)
        assert totals[function]["rows"] == len(group)
        assert totals[function]["eligible"] == 0
        assert totals[function]["static_local_candidates"] == 0
        assert "qwen2.5:3b" in totals[function]["local_slm_candidates"]


@pytest.mark.parametrize(
    ("local", "expected_blocker", "routable"),
    [
        (True, None, 1),
        (False, "REMOTE_NEEDS_AUTH", 0),
    ],
)
def test_evidenced_eligible_candidate_respects_locality(local, expected_blocker, routable):
    entry = _entry(local=local, provider="local" if local else "remote")
    before = json.dumps(entry, sort_keys=True)
    rows = build_table({"entries": [entry]})

    assert json.dumps(entry, sort_keys=True) == before
    assert len(rows) == 1
    assert rows[0]["eligible"] is True
    assert rows[0]["evidenced"] is True
    assert rows[0]["blocker"] == expected_blocker
    assert summary(rows)["research"] == {
        "rows": 1,
        "eligible": 1,
        "evidenced": 1,
        "static_local_candidates": routable,
        "local_slm_candidates": ["qwen2.5:3b"] if local else [],
    }


def test_missing_evidence_precedes_remote_authorization():
    entry = _entry(local=False, provider="remote")
    entry["eval_source"]["sha256"] = "0" * 64
    rows = build_table({"entries": [entry]})

    assert rows[0]["eligible"] is True
    assert rows[0]["evidenced"] is False
    assert rows[0]["blocker"] == "NO_EVIDENCE"
    assert summary(rows)["research"]["static_local_candidates"] == 0


def test_ineligibility_precedes_evidence_and_remote_authorization():
    entry = _entry(local=False, provider="remote", eligible=False)
    entry["eval_source"] = None
    entry["quality_metric"] = None
    rows = build_table({"entries": [entry]})

    assert rows[0]["evidenced"] is False
    assert rows[0]["blocker"] == "NOT_ELIGIBLE"
    assert summary(rows)["research"]["eligible"] == 0


def test_provisional_measurement_remains_visible_but_not_evidenced():
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    row = next(row for row in build_table(registry) if row["function"] == "verification_needed")
    assert row["eligible"] is False
    assert row["evidenced"] is False
    assert row["quality_metric"]["n"] == 2
    assert "PROVISIONAL" in row["measurement_confidence"]
    assert row["eval_source"]["sha256"]


def test_summary_deduplicates_and_sorts_local_slm_candidates():
    registry = {
        "entries": [
            _entry(model="z-model"),
            _entry(model="a-model"),
            _entry(model="z-model"),
            _entry(provider="remote", model="remote-model", local=False),
        ]
    }
    result = summary(build_table(registry))["research"]

    assert result["rows"] == 4
    assert result["eligible"] == result["evidenced"] == 4
    assert result["static_local_candidates"] == 3
    assert result["local_slm_candidates"] == ["a-model", "z-model"]


def test_markdown_is_byte_stable_and_local_first():
    registry = {
        "entries": [
            _entry(function="research", provider="a-remote", model="remote", local=False),
            _entry(function="summarization", provider="local", model="last"),
            _entry(function="research", provider="z-local", model="a|b\nc"),
        ]
    }
    rows = build_table(registry)
    first = render_markdown(rows)

    assert first == render_markdown(rows)
    assert first == render_markdown(list(reversed(rows)))
    lines = first.splitlines()
    assert lines[2].startswith("| research | z-local |")
    assert lines[3].startswith("| research | a-remote |")
    assert lines[4].startswith("| summarization |")
    assert "a\\|b c" in first
    assert first.endswith("\n")


def test_cli_json_parses_with_custom_manifest(tmp_path):
    manifest = tmp_path / "registry.json"
    manifest.write_text(json.dumps({"entries": [_entry()]}), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-m", "z0int.routing_table", "--manifest", str(manifest), "--json"],
        cwd=REGISTRY.parent.parent,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert payload["rows"][0]["blocker"] is None
    assert payload["summary"]["research"]["static_local_candidates"] == 1


def test_cli_defaults_to_committed_registry():
    result = subprocess.run(
        [sys.executable, "-m", "z0int.routing_table", "--json"],
        cwd=REGISTRY.parent.parent,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert len(payload["rows"]) > 0
    for function in TEXT_WORKERS:
        assert payload["summary"][function]["static_local_candidates"] == 0
