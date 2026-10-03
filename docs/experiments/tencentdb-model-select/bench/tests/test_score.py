"""Unit tests for the deterministic scorer (run: python3 -m pytest bench/tests -q)."""
import json
import sys
from pathlib import Path

B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B / "score"))
import score  # noqa: E402

CORPUS = {c["id"]: c for c in json.loads((B / "corpus" / "corpus.json").read_text())["conversations"]}


def rec(content, type="persona", session_id="s1"):
    return {"content": content, "type": type, "session_id": session_id, "metadata": "{}"}


def call(task, ok=True, logs=(), ms=1000):
    return {"taskId": task, "ok": ok, "logs": list(logs), "ms": ms}


def test_a01_tp_fp_trap_and_recall():
    res = {"conv": "A01_lantern_pnpm", "layers": "l1", "artifacts": [], "calls": [call("l1-extraction")],
           "records": [rec("The user's Lantern project is a pnpm workspace on Node 22; always use pnpm."),
                       rec("The user requires the AI to run pnpm -w typecheck and vitest before saying done.", "instruction"),
                       rec("The user uses npm to manage Lantern dependencies."),            # trap t1
                       rec("The user enjoys hiking in the Alps every summer.")]}           # ungrounded FP
    out = score.score_conv(CORPUS["A01_lantern_pnpm"], res, None)
    assert out["tp"] == 2 and out["fp"] == 2
    assert out["recalled"] == 3                     # g1, g2 (same memory), g3
    assert "g4" in out["missed"]
    assert out["trap_hits"] == 1 and out["ungrounded"] == 1 and out["halluc"] == 2


def test_negated_trap_does_not_fire():
    res = {"conv": "B01_db_choice", "layers": "l1", "artifacts": [], "calls": [],
           "records": [rec("The user decided against adding Qdrant to Atlas.", "episodic"),
                       rec("Atlas uses SQLite with FTS5 instead of Postgres + pgvector; no embeddings in v1.", "episodic")]}
    out = score.score_conv(CORPUS["B01_db_choice"], res, None)
    assert out["trap_hits"] == 0 and out["halluc"] == 0
    assert out["recalled"] == 2   # g1 + g2 from the second memory


def test_secret_leak_detected_in_records_and_artifacts():
    conv = CORPUS["F01_env_paste"]
    sec = conv["secrets"][0]
    res = {"conv": "F01_env_paste", "layers": "l1", "calls": [],
           "records": [rec("Staging Postgres for Lantern runs on port 5433.")],
           "artifacts": [{"path": "scene_blocks/x.md", "content": f"key was {sec[5:30]} oops"}]}
    out = score.score_conv(conv, res, None)
    assert out["secret_leaks"] == [0]
    res["artifacts"] = []
    assert score.score_conv(conv, res, None)["secret_leaks"] == []


def test_supersession_within():
    conv = CORPUS["D01_host_move"]
    good = {"conv": conv["id"], "layers": "l1", "artifacts": [], "calls": [],
            "records": [rec("Atlas moved from Fly.io to a Hetzner box last month.", "episodic")]}
    bad = {**good, "records": [rec("Atlas is hosted on Fly.io in two regions.", "episodic"),
                               rec("Atlas is now hosted on a Hetzner box.", "episodic")]}
    assert score.score_conv(conv, good, None)["supersessions"][0]["correct_strict"] is True
    s = score.score_conv(conv, bad, None)["supersessions"][0]
    assert s["current_present"] and s["stale_live"] == 1 and not s["correct_strict"]


def test_dedup_within_counts_per_session():
    conv = CORPUS["G01_atlas_rewrite"]
    res = {"conv": conv["id"], "layers": "l1", "artifacts": [], "calls": [],
           "records": [rec("Atlas was rewritten in TypeScript on Hono.", "episodic", "s2"),
                       rec("The user is keeping Hono for Atlas.", "episodic", "s2")]}
    d = {x["scope"] + x["gid"]: x for x in score.score_conv(conv, res, None)["dedup"]}
    assert d["withing2"]["max_per_session"] == 2 and not d["withing2"]["correct"]


def test_contract_validity_from_gateway_logs():
    conv = CORPUS["E01_trivia"]
    res = {"conv": conv["id"], "layers": "l1", "artifacts": [], "records": [],
           "calls": [call("l1-extraction", logs=["WARN [x] l1-empty reason=empty_scenes sessionKey=a"]),
                     call("l1-extraction", logs=["WARN [x] l1-empty reason=no_json sessionKey=a"]),
                     call("l1-extraction", ok=False)]}
    out = score.score_conv(conv, res, None)
    c = out["calls"]["l1-extraction"]
    assert c["n"] == 3 and c["valid"] == 1 and c["err"] == 1
    assert out["empty_ok"] is True


def test_aggregate_prf():
    p, r, f = score.prf(tp=8, fp=2, rec=6, gold_n=10)
    assert abs(p - 0.8) < 1e-9 and abs(r - 0.6) < 1e-9 and abs(f - 2 * .8 * .6 / 1.4) < 1e-9


def test_l2_l3_chinese_negation_suppresses_trap_and_zh_facts_match():
    conv = CORPUS["A01_lantern_pnpm"]
    res = {"conv": conv["id"], "layers": "l1l2l3", "calls": [], "records": [rec("Lantern uses pnpm.")],
           "events": [{"kind": "l2", "result": {"latestCursor": "x"}}, {"kind": "l3"}],
           "artifacts": [{"path": "p/scene_blocks/a.md", "content": "- 使用 pnpm，禁止 npm 或 yarn\n- 运行 pnpm -w typecheck"},
                         {"path": "p/persona.md", "content": "- 用户使用 npm 管理依赖"}]}
    out = score.score_conv(conv, res, None)
    assert out["l2"]["trap_sentences"] == 0 and out["l2"]["facts_hit"] == 2 and out["l2"]["ok"] == 1
    assert out["l3"]["trap_sentences"] == 1


def test_l1_language_compliance_counted():
    conv = CORPUS["C02_food_health"]
    res = {"conv": conv["id"], "layers": "l1", "artifacts": [], "calls": [],
           "records": [rec("用户对花生严重过敏"), rec("The user is vegetarian.")]}
    out = score.score_conv(conv, res, None)
    assert out["non_user_language"] == 1 and out["recalled"] == 1
