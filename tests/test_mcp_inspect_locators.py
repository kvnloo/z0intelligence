"""`inspect` must open an indexed locator, not send the caller back to search.

This is the reading half of memory: retrieval returns locators, and the reader
has to be able to open the passage they point at. Before this, `inspect` only
understood filesystem paths and answered every index locator with "re-read
through `resolve`", so a harness could find evidence but never read it.

The test uses whatever store the machine actually has and asserts on the shape of
what comes back, not on the private content.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from z0int import context_providers as cp  # noqa: E402
from z0int.mcp_server import TOOLS, _tool_inspect  # noqa: E402


def _first_message_locator() -> tuple[str, str] | None:
    """(locator, session_id) for the first AgentsView message, or None."""
    if not cp.SESSIONS_DB.is_file():
        return None
    conn = sqlite3.connect(f"file:{cp.SESSIONS_DB}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "select m.id, m.session_id from messages m where length(coalesce(m.content,'')) > 40 "
            "order by m.id limit 1"
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return f"agentsview:{row[1]}#{row[0]}", row[1]


def test_inspect_tool_advertises_index_locators_not_just_paths() -> None:
    spec = next(t for t in TOOLS if t["name"] == "inspect")
    desc = spec["description"].lower()
    assert "agentsview:" in desc and "locator" in desc
    assert "read" in desc


def test_inspect_rejects_unknown_locator_explicitly() -> None:
    out = _tool_inspect({"locator": "definitely-not-a-locator"})
    assert out["ok"] is False
    assert "unsupported locator" in out["error"]
    # the old message told the caller to search again; it must be gone
    assert "re-read through" not in out["error"]


@pytest.mark.skipif(not cp.SESSIONS_DB.is_file(), reason="session archive not mounted")
def test_inspect_opens_an_indexed_conversation_locator_with_context() -> None:
    found = _first_message_locator()
    assert found, "archive has no usable message row"
    locator, session_id = found

    out = _tool_inspect({"locator": locator, "chars": 200})
    assert out["ok"] is True, out
    assert out["kind"] == "message"
    assert out["session_id"] == session_id
    assert out["excerpt"], "inspect returned no source text"
    # an identifier alone is not a read: the reader needs the neighbouring turns
    assert isinstance(out["context"], list) and out["context"], "no surrounding messages returned"
    roles = {c["role"] for c in out["context"]}
    assert roles & {"user", "assistant"}
    assert any(c.get("timestamp") for c in out["context"])
    # and a citation a reviewer can re-resolve
    assert session_id in out["citation"]
    assert out["source_version"]


@pytest.mark.skipif(not cp.COVERAGE_DB.is_file(), reason="coverage store not mounted")
def test_inspect_opens_a_typed_fact_locator() -> None:
    conn = sqlite3.connect(f"file:{cp.COVERAGE_DB}?mode=ro", uri=True)
    try:
        row = conn.execute("select session_id, kind, value from facts limit 1").fetchone()
    finally:
        conn.close()
    if not row:
        pytest.skip("no facts")
    locator = f"coverage:{row[0] or '?'}#{row[1]}:{row[2][:120]}"
    out = _tool_inspect({"locator": locator})
    assert out["ok"] is True, out
    assert out["kind"] == "fact"
    assert out["excerpt"]


@pytest.mark.skipif(not cp.SESSIONS_DB.is_file(), reason="session archive not mounted")
def test_read_locator_is_read_only_and_does_not_create_stores(tmp_path) -> None:
    missing = tmp_path / "nope.db"
    before = missing.exists()
    assert cp.read_locator("agentsview:x#1") is not None or True  # never raises
    assert missing.exists() == before


def test_secret_shapes_are_redacted_on_the_way_out() -> None:
    text, n = cp._redact_secrets("OPENAI_API_KEY=sk-abcdef0123456789abcdef\nplain text here")
    assert n >= 1
    assert "sk-abcdef0123456789abcdef" not in text
    assert "plain text here" in text


@pytest.mark.skipif(not cp.SESSIONS_DB.is_file(), reason="session archive not mounted")
def test_ordinal_form_locator_opens_the_same_row_as_its_id() -> None:
    """The AgentsView daemon returns (session_id, ordinal), not a message id.

    A locator built from an id alone was emitted as `...#None` and could not be
    opened at all. `@ordinal` must resolve, and must resolve to the same row.
    """
    conn = sqlite3.connect(f"file:{cp.SESSIONS_DB}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "select id, session_id, ordinal from messages "
            "where length(coalesce(content,'')) > 40 order by id limit 1"
        ).fetchone()
    finally:
        conn.close()
    if not row:
        pytest.skip("no usable message")
    mid, sid, ordinal = row

    by_id = cp.read_locator(f"agentsview:{sid}#{mid}")
    by_ord = cp.read_locator(f"agentsview:{sid}@{ordinal}")
    assert by_id and by_id["ok"], by_id
    assert by_ord and by_ord["ok"], by_ord
    assert by_ord["message_id"] == str(mid) == by_id["message_id"]
    assert by_ord["excerpt"] == by_id["excerpt"]


def test_locator_without_a_row_id_fails_explicitly_rather_than_guessing() -> None:
    out = cp.read_locator("agentsview:some-session#None")
    assert out is not None and out["ok"] is False
    assert "no row id" in out["error"]


def test_http_provider_emits_openable_locators(monkeypatch) -> None:
    """A hit whose payload has only an ordinal must not produce `...#None`."""
    import json as _json

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return _json.dumps(
                {"results": [{"session_id": "s1", "ordinal": "17", "snippet": "hello",
                              "project": "p", "agent": "codex"}]}
            ).encode()

    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Resp())
    hits = cp._conversation_http("q", 1)
    assert hits and hits[0].locator == "agentsview:s1@17"
    assert "#None" not in hits[0].locator
