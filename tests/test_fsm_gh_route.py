"""O17: GitHub access gate (classifier, decision table, shim protocol, tape)."""
import json
from pathlib import Path

import pytest

from z0int.fsm import gh_route as R
from z0int.fsm import ops as P
from z0int.fsm.ops_replay import KINDS as R_KINDS

TAPE = Path(__file__).parent / "fixtures/ops_tapes/o17-gh-access.json"


def test_table_rows():
    assert P.gh_read_allows(False, True) is False
    assert P.gh_read_allows(True, True) is True
    assert P.gh_read_allows(False, False) is True
    assert P.gh_read_allows(True, False) is True
    assert P.gh_write_allows(True) is False
    assert P.gh_write_allows(False) is True


@pytest.mark.parametrize("argv,op,routable", [
    (["api", "repos/o/r"], "read", True),
    (["api", "repos/o/r/issues", "--paginate", "-q", ".[].number"], "read", True),
    (["api", "-X", "GET", "search/issues", "-f", "q=x"], "read", True),
    (["api", "repos/o/r/issues/1/comments", "-f", "body=x"], "write", False),
    (["api", "--method=PATCH", "repos/o/r/issues/1"], "write", False),
    (["api", "-i", "repos/o/r"], "read", False),
    (["api", "repos/o/r/contents/x", "--input", "f.json"], "write", False),
    (["api", "graphql", "-f", "query=mutation { x }"], "write", False),
    (["pr", "comment", "1", "-b", "x"], "write", False),
    (["pr", "view", "1"], "other", False),
    (["auth", "token"], "other", False),
])
def test_classify(argv, op, routable):
    c = R.classify(argv)
    assert (c["op"], c["routable"]) == (op, routable)


def test_target_repo_forms(tmp_path):
    assert R.target_repo(["pr", "comment", "1", "-R", "A/B"], str(tmp_path)) == "a/b"
    assert R.target_repo(["pr", "comment", "https://github.com/o/r/pull/2"], str(tmp_path)) == "o/r"
    assert R.target_repo(["api", "repos/o/r/issues/1/comments"], str(tmp_path)) == "o/r"


def test_env_cannot_clear_poller(monkeypatch):
    monkeypatch.setattr(R, "timer_started", lambda u: True)
    assert R.is_poller(1, {"GH_CALLER": "interactive"})[0] is True
    monkeypatch.setattr(R, "timer_started", lambda u: False)
    assert R.is_poller(1, {"GH_CALLER": "poller"})[0] is True


def test_shim_protocol(monkeypatch, tmp_path):
    bo = tmp_path / "BACKOFF.json"
    monkeypatch.setattr(R, "BACKOFF", bo)
    monkeypatch.setattr(R, "CONFIG", tmp_path)
    monkeypatch.delenv("Z0_GUARD_O17", raising=False)
    monkeypatch.setattr(R, "timer_started", lambda u: True)
    assert R.shim(["api", "notifications"], 1, str(tmp_path), {})[0] == R.EXIT_ROUTE
    assert R.shim(["api", "-i", "notifications"], 1, str(tmp_path), {})[0] == R.EXIT_REFUSED
    monkeypatch.setattr(R, "timer_started", lambda u: False)
    assert R.shim(["api", "notifications"], 1, str(tmp_path), {})[0] == 0
    bo.write_text(json.dumps({"o/r": {"since": "x", "items": []}}))
    assert R.shim(["pr", "comment", "1", "-R", "o/r"], 1, str(tmp_path), {})[0] == R.EXIT_REFUSED
    assert R.shim(["pr", "comment", "1", "-R", "o/other"], 1, str(tmp_path), {})[0] == 0
    monkeypatch.setenv("Z0_GUARD_O17", "report")
    assert R.shim(["pr", "comment", "1", "-R", "o/r"], 1, str(tmp_path), {})[0] == 0


def test_tape():
    rows, summary = R_KINDS["gh-access"](json.loads(TAPE.read_text()))
    assert summary["ok"], rows
