"""Unit tests for the #116 real-history bakeoff harness (benchmarks/ctx_history/run.py).

No ctx, AgentsView, qmd, network or private history: the harness's grading, gating
and sanitizing logic is tested on synthetic rows only.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

_RUN = Path(__file__).resolve().parents[1] / "benchmarks" / "ctx_history" / "run.py"
_spec = importlib.util.spec_from_file_location("ctx_history_bakeoff", _RUN)
bake = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = bake
_spec.loader.exec_module(bake)  # type: ignore[union-attr]

SENTINEL = "PRIVATE-TRANSCRIPT-BODY-7f3a"


def _item(locator, text, *, source="claude", observed_at="2026-10-03T10:00:00Z"):
    return {"locator": locator, "text": text, "source_system": source, "observed_at": observed_at}


class GradeTests(unittest.TestCase):
    def test_answer_must_match_within_one_item_and_cites_first_rank(self):
        items = [
            _item("ctx:event:a", "burners came from nproc"),
            _item("ctx:event:b", "nproc=10 because OMP_NUM_THREADS=10"),
            _item("ctx:event:c", "OMP_NUM_THREADS capped nproc again"),
        ]
        got = bake.grade(items, {"all": ["nproc", "omp_num_threads"]})
        self.assertTrue(got["answered"])
        self.assertEqual(got["cited"], "ctx:event:b")
        self.assertEqual(got["rank"], 2)
        self.assertEqual(got["source_system"], "claude")

    def test_split_facts_across_items_do_not_count(self):
        items = [_item("x:1", "nproc only"), _item("x:2", "OMP_NUM_THREADS only")]
        self.assertFalse(bake.grade(items, {"all": ["nproc", "OMP_NUM_THREADS"]})["answered"])

    def test_any_group(self):
        items = [_item("x:1", "port 8420 on loopback")]
        self.assertTrue(bake.grade(items, {"any": ["8420", "8125"]})["answered"])
        self.assertFalse(bake.grade(items, {"any": ["8096"]})["answered"])

    def test_empty_items_is_not_an_answer(self):
        got = bake.grade([], {"any": ["x"]})
        self.assertEqual(got, {"answered": False, "cited": None, "rank": None, "source_system": None})

    def test_answer_spec_must_have_a_pattern(self):
        with self.assertRaises(ValueError):
            bake.grade([_item("x:1", "anything")], {})


class SanitizeTests(unittest.TestCase):
    def test_no_text_or_unknown_fields_survive(self):
        row = {
            "question_id": "Q01",
            "arm": "ctx_lexical",
            "status": "ok",
            "question": SENTINEL,
            "items": [_item("ctx:event:6c071871-133d-8a64-8a2b-a09dd2784989", SENTINEL)],
            "evidence_ids": [
                "ctx:event:6c071871-133d-8a64-8a2b-a09dd2784989",
                "agentsview:0b1d2c3e-1111-2222-3333-444455556666#455001",
                f"qmd://notes/{SENTINEL}.md",
            ],
            "cited_id": f"qmd://notes/{SENTINEL}.md",
            "error": f"CtxCommandError: ctx exited 2: {SENTINEL}",
            "latency_cold_ms": 12.5,
        }
        out = bake.sanitize(row)
        blob = json.dumps(out)
        self.assertNotIn(SENTINEL, blob)
        self.assertNotIn("items", out)
        self.assertNotIn("question", out)
        self.assertEqual(out["evidence_ids"][0], "ctx:event:6c071871-133d-8a64-8a2b-a09dd2784989")
        self.assertEqual(out["evidence_ids"][1], "agentsview:0b1d2c3e-1111-2222-3333-444455556666#455001")
        self.assertTrue(out["evidence_ids"][2].startswith("h:"))
        self.assertTrue(out["cited_id"].startswith("h:"))
        self.assertEqual(out["error"], "CtxCommandError")
        self.assertEqual(out["latency_cold_ms"], 12.5)

    def test_safe_id_rejects_lookalikes(self):
        self.assertTrue(bake.safe_id("ctx:event:../../etc/passwd").startswith("h:"))
        self.assertTrue(bake.safe_id("agentsview:abc def#1").startswith("h:"))
        self.assertEqual(bake.safe_id(None), None)


class HybridGateTests(unittest.TestCase):
    ready = {"semantic": {"enabled": True, "status": "ready", "executor": None}}

    def test_no_opt_in_never_runs(self):
        self.assertEqual(bake.hybrid_gate(False, self.ready), "not_run:semantic_opt_in_absent")

    def test_disabled_semantic_is_unavailable(self):
        status = {"semantic": {"enabled": False, "status": "disabled", "reason": "semantic_disabled"}}
        self.assertEqual(bake.hybrid_gate(True, status), "unavailable:semantic_disabled")

    def test_remote_executor_is_refused(self):
        status = {"semantic": {"enabled": True, "status": "ready", "executor": "https://embed.example/v1"}}
        self.assertEqual(bake.hybrid_gate(True, status), "unavailable:remote_executor_refused")

    def test_local_ready_semantic_runs(self):
        self.assertIsNone(bake.hybrid_gate(True, self.ready))
        builtin = {"semantic": {"enabled": True, "status": "ready", "executor": "builtin"}}
        self.assertIsNone(bake.hybrid_gate(True, builtin))


class HydrationTextTests(unittest.TestCase):
    def test_target_first_then_nearest_neighbours_within_cap(self):
        events = [
            {"ctx_event_id": "e1", "text": "AAAA"},
            {"ctx_event_id": "e2", "text": "BBBB"},
            {"ctx_event_id": "e3", "text": "TARGET"},
            {"ctx_event_id": "e4", "text": "CCCC"},
            {"ctx_event_id": "e5", "text": "DDDD"},
        ]
        text = bake.hydration_text(events, "e3", cap=15)
        self.assertTrue(text.startswith("TARGET"))
        self.assertLessEqual(len(text), 15)
        self.assertIn("BBBB", text)
        self.assertNotIn("AAAA", text)

    def test_missing_text_is_skipped(self):
        events = [{"ctx_event_id": "e1"}, {"ctx_event_id": "e2", "text": None}]
        self.assertEqual(bake.hydration_text(events, "e1", cap=100), "")


class CutoffTests(unittest.TestCase):
    def test_only_dated_items_at_or_after_cutoff_are_dropped(self):
        items = [
            _item("agentsview:s#1", "old", observed_at="2026-10-03T22:49:59Z"),
            _item("agentsview:s#2", "new", observed_at="2026-10-03T22:50:00.5Z"),
            _item("qmd:x", "undated", observed_at=""),
        ]
        kept, dropped = bake.apply_cutoff(items, "2026-10-03T22:50:00Z")
        self.assertEqual([i["locator"] for i in kept], ["agentsview:s#1", "qmd:x"])
        self.assertEqual(dropped, 1)

    def test_no_cutoff_keeps_everything(self):
        items = [_item("a", "t", observed_at="2099-01-01T00:00:00Z")]
        self.assertEqual(bake.apply_cutoff(items, None), (items, 0))


class CtxArmTests(unittest.TestCase):
    def _cap(self, calls):
        evidence = tuple(
            SimpleNamespace(
                locator=f"ctx:event:e{i}",
                excerpt=f"snippet {i}" + (" 8420" if i == 2 else ""),
                note=f"provider=claude; scope=session; retrieval=lexical; session=s{i}",
                observed_at="2026-10-03T22:00:00Z",
            )
            for i in (1, 2, 3)
        )

        class Cap:
            available = True

            def search(self, query, **kw):
                calls.append(("search", kw))
                return SimpleNamespace(
                    evidence=evidence, generation_id="gen1", effective_mode="lexical", returned=3
                )

            def show_event(self, event_id, window=0):
                calls.append(("show", event_id, window))
                return SimpleNamespace(
                    window_events=({"ctx_event_id": event_id, "text": f"window for {event_id}"},),
                    identity=SimpleNamespace(event_uid=f"evt_{event_id}"),
                )

        return Cap()

    def test_lexical_never_hydrates_and_hydration_bounds_top_k(self):
        calls: list = []
        q = {"id": "Q", "question": "which port", "answer": {"any": ["8420"]}}
        lex = bake.ctx_query(self._cap(calls), q, mode="ctx_lexical", hydrate_top=2, hydrate_cap=50)
        self.assertEqual([c[0] for c in calls], ["search"])
        self.assertEqual(calls[0][1]["backend"], "lexical")
        self.assertTrue(lex["answered"])
        self.assertEqual(lex["generation"], "gen1")

        calls.clear()
        hyd = bake.ctx_query(self._cap(calls), q, mode="ctx_exact_hydration", hydrate_top=2, hydrate_cap=50)
        self.assertEqual([c[0] for c in calls], ["search", "show", "show"])
        self.assertEqual(hyd["hydrated_uids"], ["evt_e1", "evt_e2"])
        self.assertGreater(hyd["excerpt_chars"], lex["excerpt_chars"])

    def test_hybrid_mode_passes_explicit_semantic_opt_in(self):
        calls: list = []
        q = {"id": "Q", "question": "which port", "answer": {"any": ["8420"]}}
        bake.ctx_query(self._cap(calls), q, mode="ctx_hybrid", hydrate_top=0, hydrate_cap=0)
        self.assertEqual(calls[0][1]["backend"], "hybrid")
        self.assertIs(calls[0][1]["allow_semantic"], True)


class CallerEnvTests(unittest.TestCase):
    def test_neutral_env_drops_agent_session_markers_only(self):
        env = {
            "CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "s", "CLAUDE_CODE_CHILD_SESSION": "c", "CLAUDE_PID": "1",
            "CODEX_THREAD_ID": "t", "HERMES_SESSION_ID": "h", "CLAUDE_CONFIG_DIR": "/cfg",
            "CTX_DATA_ROOT": "/root", "PATH": "/bin", "HOME": "/h",
        }
        out = bake.caller_env(env, "neutral")
        self.assertEqual(out, {"CLAUDE_CONFIG_DIR": "/cfg", "CTX_DATA_ROOT": "/root", "PATH": "/bin", "HOME": "/h"})
        self.assertEqual(bake.caller_env(env, "inherit"), env)


class SummaryTests(unittest.TestCase):
    def test_wins_and_losses_are_relative_to_control(self):
        rows = [
            {"question_id": "Q1", "arm": "control", "status": "ok", "answered": True},
            {"question_id": "Q1", "arm": "ctx_lexical", "status": "ok", "answered": False},
            {"question_id": "Q2", "arm": "control", "status": "ok", "answered": False},
            {"question_id": "Q2", "arm": "ctx_lexical", "status": "ok", "answered": True},
            {"question_id": "Q3", "arm": "control", "status": "ok", "answered": True},
            {"question_id": "Q3", "arm": "ctx_lexical", "status": "ok", "answered": True},
            {"question_id": "Q1", "arm": "ctx_hybrid", "status": "unavailable:semantic_disabled"},
        ]
        s = bake.summarize(rows)
        self.assertEqual(s["arms"]["ctx_lexical"]["vs_control"], {"wins": ["Q2"], "losses": ["Q1"], "ties": 1})
        self.assertEqual(s["arms"]["control"]["answered"], 2)
        self.assertEqual(s["arms"]["ctx_hybrid"]["status_counts"], {"unavailable:semantic_disabled": 1})


if __name__ == "__main__":
    unittest.main()
