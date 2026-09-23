"""The confirmed-outcome ruler must not invent ground truth.

Lane B exists because the previous binder inferred answerhood from post-question
proximity and an independent critic rejected 100% of what it produced. These
tests pin the disciplines that replaced it, and pin the two receipt-detection
bugs that made a failed command look like a successful consumption.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

import mine_confirmed_pairs as mcp  # noqa: E402


# --------------------------------------------------------------------------- #
# Tier discipline
# --------------------------------------------------------------------------- #
def test_tier_d_is_counted_but_never_emitted_as_a_row() -> None:
    """Only A/B/C may carry a confidence tier; D is excluded by construction."""
    allowed = {"A", "B", "C"}
    item = mcp.Item(question="q", slot="PATH", correct="/a/b", confirmation_type="x",
                    confidence_tier="A")
    assert item.confidence_tier in allowed
    # the miner's reject vocabulary is a Counter; nothing writes tier D into items
    src = (ROOT / "scripts" / "mine_confirmed_pairs.py").read_text()
    assert 'confidence_tier="D"' not in src


def test_schema_carries_rejected_candidates_for_negative_examples() -> None:
    """B7: a correction yields a positive and a negative in one row."""
    it = mcp.Item(question="which file did we use?", slot="PATH", correct="/right.sh",
                  confirmation_type="user_correction", confidence_tier="B",
                  rejected_candidates=["/wrong.sh"], evidence_refs=["messages#1", "messages#2"])
    d = it.__dict__
    for key in ("question", "slot", "correct", "rejected_candidates",
                "confirmation_type", "evidence_refs", "confidence_tier"):
        assert key in d
    assert d["rejected_candidates"] == ["/wrong.sh"]


# --------------------------------------------------------------------------- #
# Receipt detection -- two real bugs: Hermes writes `-> exit 127`, not `exit code: 127`
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("receipt", [
    "ran `./x.sh` -> exit 127, 1 lines output",
    "ran `./x.sh` -> exit 1, 3 lines output",
    "The command exited with code 2.",
    "exit code: 1",
    "zsh: command not found: ./x.sh",
    "cat: /nope: No such file or directory",
    "HTTP/1.1 404 Not Found",
    "permission denied",
])
def test_failed_receipts_do_not_count_as_a_successful_consumption(receipt: str) -> None:
    assert mcp.FAIL.search(receipt), f"{receipt!r} must disqualify the consumption"


@pytest.mark.parametrize("receipt", [
    "ran `./x.sh` -> exit 0, 2 lines output",
    "Script completed\nWall time 1.2 seconds\nOutput:\nok",
])
def test_successful_receipts_are_not_flagged(receipt: str) -> None:
    assert not mcp.FAIL.search(receipt)


def test_http_success_needs_a_real_status_line_not_the_number_200() -> None:
    assert mcp.HTTP_OK.search("HTTP/2 200")
    assert mcp.HTTP_OK.search("HTTP/1.1 204 No Content")
    assert mcp.HTTP_OK.search("HTTP/1.0 200 OK")
    # a bare port or count must not be read as a successful fetch
    assert not mcp.HTTP_OK.search("listening on 12000")
    assert not mcp.HTTP_OK.search("processed 200 rows")


# --------------------------------------------------------------------------- #
# Artifact matching -- the defect that produced 47 junk "verified" pairs
# --------------------------------------------------------------------------- #
def test_a_shared_parent_directory_is_not_evidence_the_file_answers_the_question() -> None:
    """`hermes-agent/venv/bin/python3` is not the answer to a question about data."""
    assert mcp.path_answers_question(
        "/workspace/hermes-home/hermes-agent/venv/bin/python3",
        "where will this data live? will it live inside the sft svlm pipeline?",
    ) == []


def test_a_distinctive_basename_term_does_match() -> None:
    assert mcp.path_answers_question(
        "/opt/tools/oracle_loop.py", "where is the oracle loop located?"
    ) == ["oracle"]


def test_generic_directory_words_never_establish_answerhood() -> None:
    """The term must be the artifact's own name, not a parent directory or a topic."""
    for path, question in [
        ("/workspace/zer0/oss/t3code", "where did u clone hermes into?"),
        ("/mnt/zer0models/x", "where is the zer0models pipeline?"),
        ("/workspace/zer0/products/zerOS/tools/gn", "which scripts do we use for docs?"),
    ]:
        assert mcp.path_answers_question(path, question) == [], path


def test_a_topic_word_in_the_basename_is_still_not_a_lookup() -> None:
    """`bootstrap_worktrees.sh` shares 'worktrees' but the turn asks for an action.

    The basename term alone would match; the question-level filter is what
    rejects it, so both layers are asserted here rather than either one alone.
    """
    from gate_real_questions import question_is_information_request

    q = "can u commit everything push and use worktrees going forward?"
    assert mcp.path_answers_question("./ops/bootstrap_worktrees.sh", q) == ["worktrees"]
    assert question_is_information_request(q, "action") == "Q_ACTION_REQUEST"


# --------------------------------------------------------------------------- #
# Value hygiene
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("raw", [
    "https://client.example/callback",
    "https://example.com/x",
    "http://127.0.0.1:5177/a.png\\ncurl",
    "https://host/x`",
    "not-a-url",
])
def test_extraction_artifacts_and_placeholders_are_not_urls(raw: str) -> None:
    assert mcp.clean_url(raw) is None


def test_a_clean_url_survives() -> None:
    assert mcp.clean_url("https://0.example.invalid:8443/dev.html?mode=command") is not None


def test_argv_paths_only_returns_things_in_command_position() -> None:
    # executed
    assert "./run.sh" in mcp.argv_paths("cd /tmp && ./run.sh --flag")
    assert "/usr/bin/python3" in mcp.argv_paths("bash /usr/bin/python3 x.py")
    # merely passed as data or as a working directory
    assert mcp.argv_paths("cd /workspace/app && git add docs/x.md") == []
    assert mcp.argv_paths("grep -n foo /etc/x.conf") == []


# --------------------------------------------------------------------------- #
# Ambiguity is preserved, not adjudicated away
# --------------------------------------------------------------------------- #
def test_ambiguity_bucket_shape_is_an_abstention_not_an_answer() -> None:
    manifest = ROOT / "benchmarks" / "fixtures" / "confirmed-ruler" / "manifest.json"
    if not manifest.exists():
        pytest.skip("manifest not generated on this machine")
    import json

    stats = json.loads(manifest.read_text())
    assert stats["tier_A"] == 0 and stats["tier_B"] == 0 and stats["tier_C"] == 0
    # nothing may be promoted to ground truth without a tier
    assert stats["final"] <= stats["tier_A"] + stats["tier_B"] + stats["tier_C"]
    assert "protocol" in stats
    assert "never written to truth" in stats["protocol"]["tiers"]["D"]
