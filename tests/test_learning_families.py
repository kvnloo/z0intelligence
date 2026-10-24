"""Behavioral fixtures use native OMP field shapes, not private trace contents."""
import json
import sqlite3
import time

import pytest

from z0int.cognition.learning_families import (
    BASELINE, FAMILIES, UNKNOWN, derive_jobs, extract_labels, family_metrics,
    job_id, predict_future, project_prompt, run_jobs, wilson_interval,
)


def connection(*, result_timestamps=True):
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    optional = ",outcome_ts REAL" if result_timestamps else ""
    db.execute("CREATE TABLE calls(session TEXT,call TEXT,tool TEXT,features TEXT,"
               "outcome TEXT,ts REAL,arguments TEXT" + optional + ",PRIMARY KEY(session,call))")
    return db


def add(db, call, *, session="session", ts=10, tool="read", arguments=None,
        outcome=None, outcome_ts=None, features=None):
    names = ["session", "call", "tool", "features", "outcome", "ts", "arguments"]
    values = [session, call, tool, json.dumps(features or ["find", "read"]),
              json.dumps(outcome) if outcome is not None else None, ts,
              json.dumps(arguments if arguments is not None else {"path": "skill://sway-only-desktop-automation"})]
    if "outcome_ts" in {row[1] for row in db.execute("PRAGMA table_info(calls)")}:
        names.append("outcome_ts")
        values.append(outcome_ts)
    db.execute("INSERT INTO calls(" + ",".join(names) + ") VALUES(" + ",".join("?" for _ in names) + ")", values)


def first_choice(prompt, choices, strategy):
    assert json.loads(prompt)["choices"] == choices
    assert strategy["family"] in FAMILIES
    return {"selected_action": choices[0], "confidence": .9}


@pytest.mark.parametrize("tool,args,expected", [
    ("read", {"path": "skill://sway-only-desktop-automation"}, "sway-only-desktop-automation"),
    ("functions.read", {"path": "skill://no-mistakes/references/gates.md:3-8"}, "no-mistakes"),
    ("skill_view", {"name": "requesting-code-review"}, "requesting-code-review"),
    ("skill_load", {"skill_name": "review-bugbot"}, "review-bugbot"),
    ("skill_manage", {"skill": "kevin-phones", "action": "view"}, "kevin-phones"),
])
def test_exact_skill_argument_extraction(tool, args, expected):
    assert extract_labels(tool, args)["omp.skill_choice"] == expected
    assert extract_labels(tool, json.dumps(args))["omp.skill_choice"] == expected


@pytest.mark.parametrize("tool,args", [
    ("bash", {"command": "cat skill://not-a-read"}),
    ("read", {"i": "reading skill://not-the-path", "path": "regular.txt"}),
    ("skill_view", {"name": "one", "skill": "two"}),
    ("skill_view", {"name": ["one"]}),
    ("read", {"path": "skill://"}),
    ("read", {"path": "skill://ambiguous?query"}),
])
def test_no_skill_gold_from_mentions_or_ambiguity(tool, args):
    assert "omp.skill_choice" not in extract_labels(tool, args)


def test_explicit_model_is_exact_not_an_inferred_route():
    assert extract_labels("kanban_create", {"model": "provider/model@rev"}) == {
        "omp.model_choice": "provider/model@rev"}
    for args in ({"command": "--model guessed"}, {"model": ["one"]}, {"model": " x "}, {"model_id": "not-model"}):
        assert "omp.model_choice" not in extract_labels("bash", args)


@pytest.mark.parametrize("path,kind", [
    ("skill://ctx", "skill_uri"), ("memory://root/note", "internal_uri"),
    ("https://example.org/file", "web_url"), ("/project/src/a.py:1-3", "absolute_path"),
    ("src/a.py", "relative_path"),
])
def test_exact_path_kind(path, kind):
    assert extract_labels("read", {"path": path})["omp.path_kind"] == kind


@pytest.mark.parametrize("outcome", [
    {}, None, "failed with exit code 1", {"text": "success"}, {"exit_code": 0},
    {"tool_error": None}, {"tool_error": 0}, {"isError": "false"},
    {"tool_error": False, "isError": True},
])
def test_ambiguous_outcome_is_not_gold(outcome):
    assert "omp.wrapper_result" not in extract_labels("bash", {}, outcome)


def test_wrapper_flag_is_not_task_or_process_success():
    assert extract_labels("bash", {}, {"tool_error": False, "exit_code": 1}) == {
        "omp.wrapper_result": "wrapper_ok"}
    assert extract_labels("bash", {}, {"isError": True, "details": {"exitCode": 1}}) == {
        "omp.wrapper_result": "wrapper_error"}


def test_exact_session_and_full_pipe_call_join_are_independent():
    db = connection()
    call = "call_native|fc_full_identifier"
    add(db, call, session="header-session-a", outcome={"isError": True}, outcome_ts=12)
    add(db, call, session="header-session-b", outcome={"isError": False}, outcome_ts=13)
    result = derive_jobs(db, now=20)
    assert result["labels_added"] == 6
    rows = db.execute("SELECT session,call,label FROM family_jobs WHERE family='omp.wrapper_result' ORDER BY session").fetchall()
    assert [(r["session"], r["call"], r["label"]) for r in rows] == [
        ("header-session-a", call, "wrapper_error"), ("header-session-b", call, "wrapper_ok")]
    assert job_id("a|b", "c", "omp.skill_choice") != job_id("a", "b|c", "omp.skill_choice")


def test_current_or_future_gold_never_enters_projection_or_candidates():
    db = connection()
    add(db, "old", ts=1, arguments={"path": "skill://prior-skill"})
    add(db, "target", ts=10, arguments={"path": "skill://SECRET_TARGET_GOLD", "command": "DO_NOT_COPY"})
    add(db, "future", ts=20, arguments={"path": "skill://SECRET_FUTURE_GOLD"})
    derive_jobs(db, now=30)
    captured = []
    def inspect(prompt, choices, strategy):
        captured.append((prompt, choices, strategy))
        return first_choice(prompt, choices, strategy)
    run_jobs(db, inspect, now=30, max_jobs=20)
    identity = job_id("session", "target", "omp.skill_choice")
    row = db.execute("SELECT prompt,choices FROM family_evaluations WHERE id=? AND arm LIKE 'slm:%'", (identity,)).fetchone()
    assert "prior-skill" in row["prompt"]
    assert "SECRET_TARGET_GOLD" not in row["prompt"] + row["choices"]
    assert "SECRET_FUTURE_GOLD" not in row["prompt"] + row["choices"]
    assert "DO_NOT_COPY" not in row["prompt"]
    assert captured


def test_first_unseen_gold_does_not_repair_candidate_recall():
    db = connection()
    add(db, "only", arguments={"model": "never-observed-before"})
    derive_jobs(db, now=20)
    result = run_jobs(db, first_choice, now=20)
    score = result["families"]["omp.model_choice"]["arms"]["slm:qwen3-0.6b-q8"]["replayed_L1"]
    assert score["n"] == 1 and score["covered_n"] == 0 and score["correct"] == 0
    assert json.loads(db.execute("SELECT choices FROM family_evaluations WHERE arm LIKE 'slm:%'").fetchone()[0]) == [UNKNOWN]


def test_same_timestamp_parallel_gold_is_not_a_prior_example():
    db = connection()
    add(db, "a", ts=10, arguments={"model": "parallel-a"})
    add(db, "b", ts=10, arguments={"model": "parallel-b"})
    derive_jobs(db, now=20)
    run_jobs(db, first_choice, now=20)
    for row in db.execute("SELECT prompt FROM family_evaluations WHERE arm LIKE 'slm:%'"):
        assert "parallel-a" not in row["prompt"] and "parallel-b" not in row["prompt"]


def test_projection_bounds_remain_valid_json_with_unicode_and_large_labels():
    examples = [{"label": "skill-" + "界" * 100 + str(i), "features": ["x" * 500] * 40} for i in range(120)]
    prompt, choices = project_prompt("omp.skill_choice", ["工具" * 300] * 100,
                                      examples, prompt_bytes=384, candidate_k=3)
    body = json.loads(prompt)
    assert len(prompt.encode()) <= 384
    assert 1 <= len(choices) <= 3 and body["choices"] == choices
    assert len(body["previous_tools"]) <= 8
    with pytest.raises(ValueError):
        project_prompt("omp.skill_choice", [], [], prompt_bytes=100)


def test_incremental_derivation_and_grading_do_not_repeat():
    db = connection()
    for i in range(5):
        add(db, str(i), ts=10+i, outcome={"tool_error": False}, outcome_ts=11+i)
    assert derive_jobs(db, now=30, max_calls=2)["calls_scanned"] == 2
    assert derive_jobs(db, now=30, max_calls=20)["calls_scanned"] == 3
    invocations = []
    def count(prompt, choices, strategy):
        invocations.append(strategy)
        return first_choice(prompt, choices, strategy)
    first = run_jobs(db, count, now=30, max_jobs=100)
    assert first["jobs_evaluated"] == 15
    before = family_metrics(db, now=30)
    assert derive_jobs(db, now=31)["calls_scanned"] == 0
    second = run_jobs(db, count, now=31, max_jobs=100)
    assert second["jobs_evaluated"] == 0 and len(invocations) == 15
    assert before == family_metrics(db, now=30)
    assert db.execute("SELECT COUNT(*) FROM family_evaluations").fetchone()[0] == 30


def test_first_bounded_tick_grades_each_available_family_with_fake_slm():
    db = connection()
    for i in range(3):
        add(db, str(i), ts=i+1, arguments={"path": "skill://ctx", "model": "exact-model"},
            outcome={"tool_error": False}, outcome_ts=i+1.5)
    derive_jobs(db, now=20)
    result = run_jobs(db, first_choice, now=20, max_jobs=4)
    assert result["jobs_evaluated"] == 4
    for family in FAMILIES:
        arms = result["families"][family]["arms"]
        assert arms["slm:qwen3-0.6b-q8"]["replayed_L1"]["n"] == 1
        assert arms[BASELINE]["replayed_L1"]["n"] == 1
    assert run_jobs(db, first_choice, now=20, max_jobs=4, deadline=time.monotonic()-1)["jobs_evaluated"] == 0


def test_pending_result_is_graded_after_exact_same_call_arrives():
    db = connection()
    add(db, "pending|full", ts=10)
    derive_jobs(db, now=11)
    result = run_jobs(db, first_choice, now=11, max_jobs=20)
    assert result["families"]["omp.wrapper_result"]["arms"] == {}
    assert result["families"]["omp.wrapper_result"]["pending"] == 1
    db.execute("UPDATE calls SET outcome=?,outcome_ts=? WHERE session=? AND call=?",
               (json.dumps({"tool_error": False}), 12, "session", "pending|full"))
    assert derive_jobs(db, now=13)["labels_added"] == 1
    assert run_jobs(db, first_choice, now=13)["jobs_evaluated"] == 0
    arms = family_metrics(db, now=13)["omp.wrapper_result"]["arms"]
    assert arms["slm:qwen3-0.6b-q8"]["live_shadow"]["n"] == 1
    assert arms["slm:qwen3-0.6b-q8"]["live_shadow"]["correct"] == 1


def test_old_result_or_missing_result_timestamp_cannot_become_live():
    for result_ts in (None, 9):
        db = connection()
        add(db, "pending", ts=5)
        derive_jobs(db, now=10)
        run_jobs(db, first_choice, now=10)
        db.execute("UPDATE calls SET outcome=?,outcome_ts=?", (json.dumps({"isError": False}), result_ts))
        derive_jobs(db, now=11)
        arms = family_metrics(db, now=11)["omp.wrapper_result"]["arms"]
        assert "live_shadow" not in arms["slm:qwen3-0.6b-q8"]
        assert arms["slm:qwen3-0.6b-q8"]["replayed_L1"]["n"] == 1


def test_optional_pre_call_hook_is_prospective_not_replayed():
    db = connection()
    add(db, "prior", ts=1, arguments={"path": "skill://ctx"})
    derive_jobs(db, now=2)
    assert predict_future(db, "session", "next|native", ["read"], first_choice,
                          now=10, families=("omp.skill_choice",)) == 1
    add(db, "next|native", ts=11, arguments={"path": "skill://ctx"})
    derive_jobs(db, now=12)
    score = family_metrics(db, now=12)["omp.skill_choice"]["arms"]["slm:qwen3-0.6b-q8"]["live_shadow"]
    assert score["n"] == score["correct"] == 1
    with pytest.raises(ValueError, match="unobserved"):
        predict_future(db, "session", "next|native", [], first_choice, now=13)


def test_labels_per_day_uses_event_time_not_replay_ingestion_time():
    db = connection()
    now = 200000
    add(db, "old", ts=1, outcome={"isError": False}, outcome_ts=2)
    add(db, "today", ts=now-2, outcome={"isError": True}, outcome_ts=now-1)
    add(db, "unknown", ts=0, outcome={"isError": False})
    derive_jobs(db, now=now)
    metrics = family_metrics(db, now=now)
    for family in ("omp.skill_choice", "omp.path_kind", "omp.wrapper_result"):
        assert metrics[family]["labels"] == 3
        assert metrics[family]["labels_day"] == 1
        assert metrics[family]["unknown_time_labels"] == 1
    assert family_metrics(db, now=now+86401)["omp.skill_choice"]["labels_day"] == 0


def test_missing_outcome_timestamp_column_remains_honest():
    db = connection(result_timestamps=False)
    add(db, "old", outcome={"isError": False})
    derive_jobs(db, now=20)
    result = run_jobs(db, first_choice, now=20)
    wrapper = result["families"]["omp.wrapper_result"]
    assert wrapper["labels"] == wrapper["unknown_time_labels"] == 1
    assert wrapper["labels_day"] == 0
    assert wrapper["arms"]["slm:qwen3-0.6b-q8"]["replayed_L1"]["n"] == 1


def test_wilson_scores_use_covered_predictions_not_point_accuracy():
    assert wilson_interval(0, 0) == (0, 1)
    low, high = wilson_interval(5, 5)
    assert .5 < low < .8 and high == pytest.approx(1)
    assert wilson_interval(60, 60)[0] > .9
    db = connection()
    for i in range(60):
        add(db, str(i), ts=i+1, arguments={"path": "/exact/path"}, outcome={"isError": False}, outcome_ts=i+1.5)
    derive_jobs(db, now=100)
    run_jobs(db, first_choice, now=100, max_jobs=200)
    score = family_metrics(db, now=100)["omp.wrapper_result"]["arms"]["slm:qwen3-0.6b-q8"]["replayed_L1"]
    assert score["n"] == score["correct"] == 60
    assert .9 < score["ci_low"] < score["accuracy"] == 1
    assert score["ci_method"] == "wilson95" and score["synthetic"] is False


@pytest.mark.parametrize("answer", [None, {"action": "outside", "confidence": 1},
    {"action": "wrapper_ok", "confidence": float("nan")},
    {"action": "wrapper_ok", "confidence": True},
    {"action": "wrapper_ok", "confidence": 1, "parse_error": "private transport details"}])
def test_bad_slm_answers_are_immutable_visible_abstentions(answer):
    db = connection()
    add(db, "one", arguments={}, outcome={"isError": False}, outcome_ts=12)
    derive_jobs(db, now=20)
    run_jobs(db, lambda *args: answer, now=20)
    score = family_metrics(db, now=20)["omp.wrapper_result"]["arms"]["slm:qwen3-0.6b-q8"]["replayed_L1"]
    assert score["n"] == 1 and score["covered_n"] == score["correct"] == 0
    assert score["errors"] == 1
    assert run_jobs(db, first_choice, now=21)["jobs_evaluated"] == 0


def test_predictor_exception_is_recorded_without_private_message():
    db = connection()
    add(db, "one", arguments={}, outcome={"isError": False}, outcome_ts=12)
    derive_jobs(db, now=20)
    def failed(*args):
        raise OSError("private endpoint and credentials")
    run_jobs(db, failed, now=20)
    row = db.execute("SELECT error FROM family_evaluations WHERE arm LIKE 'slm:%'").fetchone()
    assert row["error"] == "predictor_exception"


def test_late_source_correction_does_not_regrade_frozen_gold():
    db = connection()
    add(db, "one", arguments={}, outcome={"isError": False}, outcome_ts=12)
    derive_jobs(db, now=20)
    run_jobs(db, first_choice, now=20)
    db.execute("UPDATE calls SET outcome=?", (json.dumps({"isError": True}),))
    assert derive_jobs(db, now=21)["labels_added"] == 0
    assert db.execute("SELECT label FROM family_jobs").fetchone()[0] == "wrapper_ok"
    assert run_jobs(db, first_choice, now=21)["jobs_evaluated"] == 0


def test_schema_creation_does_not_commit_the_parent_transaction():
    db = connection()
    add(db, "one")
    assert db.in_transaction
    derive_jobs(db, now=20)
    assert db.in_transaction
    db.rollback()
    assert db.execute("SELECT COUNT(*) FROM calls").fetchone()[0] == 0


def test_strategy_changes_create_independent_immutable_trials():
    db = connection()
    for i in range(3):
        add(db, str(i), ts=i+1, arguments={"path": "skill://ctx"},
            outcome={"isError": False}, outcome_ts=i+1.5)
    derive_jobs(db, now=20)
    first = run_jobs(db, first_choice, now=20, max_jobs=100)
    assert first["jobs_evaluated"] == 9
    second = run_jobs(db, first_choice, now=21, max_jobs=100,
                      strategy_id="history-small", retrieval_k=1, history_width=1)
    assert second["jobs_evaluated"] == 9
    assert run_jobs(db, first_choice, now=22, max_jobs=100,
                    strategy_id="history-small", retrieval_k=1, history_width=1)["jobs_evaluated"] == 0
    arms = family_metrics(db, now=22)["omp.skill_choice"]["arms"]
    assert arms["slm:qwen3-0.6b-q8"]["replayed_L1"]["n"] == 3
    alternative = [value["replayed_L1"] for key, value in arms.items()
                   if key.startswith("slm:") and key != "slm:qwen3-0.6b-q8"]
    assert len(alternative) == 1 and alternative[0]["n"] == 3
    assert alternative[0]["strategy_id"] == "history-small"
    assert alternative[0]["strategy_config"]["retrieval_k"] == 1
    assert alternative[0]["base_arm"] == "slm:qwen3-0.6b-q8"
    # Reusing a human-readable ID with different settings still isolates scores.
    third = run_jobs(db, first_choice, now=23, max_jobs=100,
                     strategy_id="history-small", retrieval_k=2, history_width=1)
    assert third["jobs_evaluated"] == 9
    assert db.execute("SELECT COUNT(*) FROM family_evaluations").fetchone()[0] == 54


def test_per_family_strategy_controls_actual_history_projection():
    db = connection()
    add(db, "prior", ts=1, arguments={"path": "skill://ctx"}, features=["glob", "find", "read"])
    add(db, "target", ts=2, arguments={"path": "skill://ctx"}, features=["bash", "grep", "write"])
    derive_jobs(db, now=3)
    observed = []
    def capture(prompt, choices, strategy):
        observed.append((json.loads(prompt), strategy))
        return first_choice(prompt, choices, strategy)
    result = run_jobs(db, capture, now=3, max_jobs=20,
        families=("omp.skill_choice", "omp.path_kind"),
        strategies={"omp.skill_choice": {"strategy_id": "short-history",
                    "retrieval_k": 1, "history_width": 1, "candidate_k": 2}})
    assert result["jobs_evaluated"] == 4
    skill_prompts = [(body, strategy) for body, strategy in observed
                     if strategy["family"] == "omp.skill_choice"]
    assert len(skill_prompts) == 2
    assert all(len(body["previous_tools"]) == 1 for body, _ in skill_prompts)
    assert all(strategy["strategy_id"] == "short-history" for _, strategy in skill_prompts)
    assert result["families"]["omp.wrapper_result"]["arms"] == {}


@pytest.mark.parametrize("settings", [
    {"retrieval_k": 0}, {"retrieval_k": 513}, {"history_width": 0},
    {"history_width": 9}, {"candidate_k": 4}, {"prompt_bytes": 100},
])
def test_strategy_bounds_are_enforced_without_model_calls(settings):
    db = connection()
    add(db, "one")
    derive_jobs(db, now=20)
    with pytest.raises(ValueError):
        run_jobs(db, first_choice, now=20, **settings)


def test_result_during_slm_rpc_is_not_live_shadow(monkeypatch):
    db = connection()
    add(db, "pending", ts=1)
    derive_jobs(db, now=10)
    clock = [100.0]
    monkeypatch.setattr("z0int.cognition.learning_families.time.monotonic", lambda: clock[0])
    def delayed(prompt, choices, strategy):
        clock[0] += 5
        return first_choice(prompt, choices, strategy)
    run_jobs(db, delayed, now=10, families=("omp.wrapper_result",))
    db.execute("UPDATE calls SET outcome=?,outcome_ts=?", (json.dumps({"isError": False}), 12))
    derive_jobs(db, now=16)
    arms = family_metrics(db, now=16)["omp.wrapper_result"]["arms"]
    assert arms["slm:qwen3-0.6b-q8"]["replayed_L1"]["n"] == 1
    assert "live_shadow" not in arms["slm:qwen3-0.6b-q8"]


def test_abstentions_reduce_coverage_not_covered_accuracy_or_wilson():
    db = connection()
    for i in range(4):
        add(db, str(i), ts=i+1, arguments={}, outcome={"isError": False}, outcome_ts=i+1.5)
    derive_jobs(db, now=10)
    attempts = [0]
    def selective(prompt, choices, strategy):
        attempts[0] += 1
        assert len(choices) <= strategy["model_cap"] == 3
        return first_choice(prompt, choices, strategy) if attempts[0] <= 2 else {"selected_action": None}
    run_jobs(db, selective, now=10, families=("omp.wrapper_result",))
    score = family_metrics(db, now=10)["omp.wrapper_result"]["arms"]["slm:qwen3-0.6b-q8"]["replayed_L1"]
    assert score["n"] == score["total"] == 4
    assert score["covered_n"] == score["correct"] == 2
    assert score["accuracy"] == 1 and score["coverage"] == .5
    assert (score["ci_low"], score["ci_high"]) == wilson_interval(2, 2)


def test_three_option_default_and_explicit_five_option_model_cap():
    examples = [{"label": "skill-" + str(i), "features": []} for i in range(20)]
    _, choices = project_prompt("omp.skill_choice", [], examples)
    assert len(choices) == 3
    assert choices == ["skill-0", "skill-1", "skill-10"]
    with pytest.raises(ValueError):
        project_prompt("omp.skill_choice", [], examples, candidate_k=5)
    _, choices = project_prompt("omp.skill_choice", [], examples, candidate_k=5, model_cap=5)
    assert len(choices) == 5
    with pytest.raises(ValueError):
        project_prompt("omp.skill_choice", [], examples, candidate_k=6, model_cap=5)


def test_zero_coverage_is_explicitly_blocked():
    db = connection()
    add(db, "only", arguments={"model": "not-in-prior-vocabulary"})
    derive_jobs(db, now=20)
    run_jobs(db, first_choice, now=20)
    score = family_metrics(db, now=20)["omp.model_choice"]["arms"]["slm:qwen3-0.6b-q8"]["replayed_L1"]
    assert score["status"] == "blocked"
    assert score["n"] == score["total"] == 1 and score["covered_n"] == 0
    assert score["ci_low"] == 0 and score["ci_high"] == 1


def test_recent_session_strategy_retrieves_only_preceding_selected_sessions():
    from z0int.cognition.learning_families import _prior
    db = connection()
    add(db, 'old', session='old', ts=1, arguments={'path': 'skill://old-skill'})
    add(db, 'recent', session='recent', ts=2, arguments={'path': 'skill://recent-skill'})
    add(db, 'target', session='target', ts=3, arguments={'path': 'skill://gold-not-input'})
    add(db, 'future', session='future', ts=4, arguments={'path': 'skill://future-skill'})
    derive_jobs(db, now=10)
    row = db.execute("SELECT * FROM family_jobs WHERE session='target' AND family='omp.skill_choice'").fetchone()
    scoped = _prior(db, row, limit=64, recent_sessions_n=1)
    assert [example['label'] for example in scoped] == ['recent-skill']
    all_prior = _prior(db, row, limit=64)
    assert [example['label'] for example in all_prior] == ['old-skill', 'recent-skill']
    db.close()
