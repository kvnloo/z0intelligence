"""Weight-space eval harness: the scorers and the edits reader reproduce the paper's released results.

Needs the pinned lm_understand-logs checkout in $Z0INT_HOME/cache/weight-space (benchmarks/weight_space/run.py
fetches it); skipped when it is absent.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "benchmarks" / "weight_space"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "slm"))

np = pytest.importorskip("numpy")
import edits  # noqa: E402
import run  # noqa: E402

LOGS = run.CACHE
needs_logs = pytest.mark.skipif(not (LOGS / "exp1_which_game").exists(), reason="lm_understand-logs not fetched")


@needs_logs
def test_read_scorer_reproduces_paper_7_of_7():
    for t in run.tasks(LOGS, ["exp1", "exp2"]):
        ans = (Path(t["prompt_path"]).parent / "answer.md").read_text()
        s = run.score_read(t, ans)
        assert s["correct"], (t["id"], s)
        assert s["confidence"] is not None


@needs_logs
@pytest.mark.parametrize("game", ["breakout", "seaquest", "asterix"])
def test_edits_reader_rebuilds_released_networks_exactly(game):
    d = LOGS / "exp5_write_from_scratch"
    w, rep = edits.apply((d / f"runs/{game}/reply/answer.md").read_text())
    ref = np.load(d / f"networks/{game}/hand.npz")
    assert rep["errors"] == [] and rep["applied"] == rep["statements"]
    for k, v in w.items():
        assert np.array_equal(ref[edits.NAMES[k]], v), k
    applied = json.loads((d / f"runs/{game}/apply.json").read_text())["edit_report"]
    assert rep["statements"] == applied["statements"]


def test_edits_reader_semantics():
    text = "x\n```edits\nW1[0:2, 0] = [1, 2]\nb1[:] = 0.5\nW3[0, 0:2] = [\n 3, 4]\nW1[0, 0] *= 10\nbad line\n```\n"
    w, rep = edits.apply(text)
    assert w["W1"][0, 0] == 10 and w["W1"][1, 0] == 2 and (w["b1"] == 0.5).all() and w["W3"][0, 1] == 4
    assert rep["applied"] == 4 and len(rep["errors"]) == 1


def test_classify_first_keyword_wins():
    assert run.classify("Frogger-like road crossing, not Pong", run.GAME_WORDS) == "freeway"
    assert run.classify("a filled disc", run.SHAPE_WORDS) == "circle"
    assert run.classify("Pac‑Man–style maze game", run.GAME_WORDS) == "pacman"
    assert run.classify(None, run.SHAPE_WORDS) is None


def test_last_boxed_and_grading():
    import common as C
    assert C.last_boxed("<think>\\boxed{1}</think> so \\boxed{\\frac{1}{2}}") == "\\frac{1}{2}"
    assert C.last_boxed("no box") is None
    pytest.importorskip("math_verify")
    assert C.is_correct("0.5", "\\frac{1}{2}")
    assert not C.is_correct("3", "4")


def test_model_identity_is_required():
    import editors
    good = editors.Reply("opus", "claude-opus-5-5", "claude-opus-5-5", "ok", {}, 1, 0, True, "fixture")
    assert good.valid
    good.model_observed = "another-model"
    assert not good.valid
    good.model_observed = good.model_requested
    good.text = ""
    assert not good.valid


def test_paired_bootstrap_reproducible():
    import session
    orig = [{"id": str(i), "sample": 0, "correct": False} for i in range(8)]
    edited = [dict(r, correct=i < 4) for i, r in enumerate(orig)]
    a = session._paired(orig, edited)
    assert a == session._paired(orig, edited)
    assert a["pairs"] == 8 and a["diff_edited_minus_original_pts"] == 50.0
    assert a["paired_bootstrap_95ci_pts"] == [12.5, 87.5]
    assert session._paired([], []) == {}
