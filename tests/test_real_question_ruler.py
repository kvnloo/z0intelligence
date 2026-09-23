"""The real-question ruler must be self-verifying.

If the frozen fixture drifts from its manifest hash, or an item's cited evidence
stops containing its own answer, every number measured against it is void. These
tests are the guard on that.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

FIX = ROOT / "benchmarks" / "fixtures" / "resolve-real-v1"
DB = "/mnt/zer0models/sft-svlm/data/agentsview/sessions.db"


pytestmark = pytest.mark.skipif(
    not (FIX / "dev.jsonl").exists(),
    reason="real-question rows are gitignored (private turns, public repo); regenerate with scripts/freeze_real_questions.py",
)


def _rows(split: str) -> list[dict]:
    p = FIX / f"{split}.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def test_manifest_hash_matches_dev_content() -> None:
    manifest = json.loads((FIX / "manifest.json").read_text())
    blob = "".join(json.dumps(r, sort_keys=True) + "\n" for r in _rows("dev"))
    assert hashlib.sha256(blob.encode()).hexdigest() == manifest["dataset_sha256"]


def test_ruler_is_honest_about_being_single_lineage() -> None:
    manifest = json.loads((FIX / "manifest.json").read_text())
    assert any("ONE session" in x for x in manifest["known_limitations"])
    assert len({r["session_id"] for r in _rows("dev")}) == 1


def test_splits_are_disjoint_by_session() -> None:
    seen: dict[str, str] = {}
    for split in ("train", "dev", "sealed"):
        for r in _rows(split):
            sid = r["session_id"]
            assert seen.get(sid, split) == split, f"{sid} leaks across splits"
            seen[sid] = split


def test_no_question_leaks_its_own_answer() -> None:
    for r in _rows("dev") + _rows("sealed"):
        q = r["question"].lower()
        v = r["expected_value"].lower()
        assert v not in q
        base = v.rsplit("/", 1)[-1]
        if len(base) > 5:
            assert base not in q


def test_ground_truth_never_appears_among_its_own_rivals() -> None:
    for r in _rows("dev") + _rows("sealed"):
        assert r["expected_value"] not in r["rival_values"]


def test_survivors_were_accepted_by_two_independent_critics() -> None:
    for r in _rows("dev") + _rows("sealed"):
        assert r["ground_truth_source"] == "independent_critic_2_of_2"
        assert r["quality_gate"] == "real-v1"


@pytest.mark.skipif(not Path(DB).exists(), reason="session archive not mounted")
def test_every_cited_evidence_pointer_still_supports_its_answer() -> None:
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    for r in _rows("dev") + _rows("sealed"):
        kind, _, raw = r["evidence_pointer"].partition("#")
        if kind == "messages":
            row = conn.execute("select content from messages where id=?", (int(raw),)).fetchone()
            blob = (row[0] if row else "") or ""
        else:
            row = conn.execute(
                "select file_path, input_json, substr(result_content,1,20000) from tool_calls where id=?",
                (int(raw),),
            ).fetchone()
            blob = "\n".join(x or "" for x in row) if row else ""
        assert blob, f"{r['id']}: evidence pointer {r['evidence_pointer']} does not resolve"
        assert r["expected_value"] in blob, f"{r['id']}: citation no longer supports the answer"


def test_value_shape_gate_rejects_the_known_junk() -> None:
    from gate_real_questions import value_shape_ok

    # a host is not a path answer, and a bare fragment is not a path
    assert value_shape_ok("PATH", "/127.0.0.1") is not None
    assert value_shape_ok("PATH", "/github.com") is not None
    assert value_shape_ok("PATH", "/.zeros") is not None
    assert value_shape_ok("PATH", "/dev.html") is None  # a real file name
    assert value_shape_ok("PATH", "/workspace/zer0/products/zerOS/README.md") is None
    assert value_shape_ok("PATH", "docs/ops/CLOUD-TELEPORT.md") is None
    # a date is not a revision
    assert value_shape_ok("REVISION", "20260712") is not None
    assert value_shape_ok("REVISION", "9d98e3e") is None
    # shape rules cannot catch semantic junk ("zer0models" is a storage
    # directory that happens to contain a digit and passes the shape test);
    # only the independent critic caught that one.
    assert value_shape_ok("MODEL", "/models/qwen3-coder") is not None
    assert value_shape_ok("MODEL", "qwen3-coder-30b") is None
    assert value_shape_ok("URL", "http://localhost:5173/dev.html") is None


def test_action_requests_are_not_lookups() -> None:
    from gate_real_questions import question_is_information_request, slot_object_ok

    assert question_is_information_request("continue working on your goal", "action") == "Q_ACTION_REQUEST"
    assert question_is_information_request("let's switch over to the latest branch", "action") == "Q_ACTION_REQUEST"
    # ...unless the turn names the value it wants
    assert question_is_information_request("can you spin up the dev server and get the link?", "action") is None
    assert not slot_object_ok("can you check for worktrees with node modules too?", "PATH")
    assert slot_object_ok("where is the oracle loop located?", "PATH")
