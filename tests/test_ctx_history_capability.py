from __future__ import annotations

import json
import os
import unittest
from dataclasses import dataclass
from typing import Any
from unittest import mock

from z0int.capabilities.ctx_history import (
    CtxCommandError,
    CtxHistoryCapability,
    CtxProtocolError,
    CtxUnavailable,
)


@dataclass
class _Proc:
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""


class _Runner:
    def __init__(self, responses: list[Any]):
        self.responses = list(responses)
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(self, argv: list[str], **kwargs: Any) -> _Proc:
        self.calls.append((list(argv), dict(kwargs)))
        response = self.responses.pop(0)
        if isinstance(response, dict):
            return _Proc(stdout=json.dumps(response))
        return response


SEARCH = {
    "schema_version": 2,
    "payload_type": "search_results",
    "query": "why retry",
    "freshness": {"mode": "off", "status": "read_only"},
    "retrieval": {
        "requested_mode": "lexical",
        "effective_mode": "lexical",
        "generation_id": "gen-7",
    },
    "generated_at": "2026-10-03T20:00:00Z",
    "results": [
        {
            "ctx_event_id": "evt-1",
            "ctx_session_id": "ses-1",
            "provider": "codex",
            "snippet": "because the first attempt timed out",
            "rank": 1,
            "result_scope": "session",
        }
    ],
    "result_window": {"limit": 5, "returned": 1, "more_available": False},
}

EVENT = {
    "target": "event",
    "event": {
        "ctx_event_id": "evt-1",
        "ctx_session_id": "ses-1",
        "provider": "codex",
        "sequence": 4,
        "event_type": "message",
        "role": "assistant",
        "text": "because the first attempt timed out",
    },
    "events": [],
}


# Sanitized fixtures shaped like real ctx 2.2.7 output on local history
# (default session-scoped lexical search; `show event` event_window). IDs and
# text are synthetic; only the key layout mirrors the observed payloads.
REAL_SHAPED_SEARCH = {
    "schema_version": 2,
    "payload_type": "search_results",
    "query": "unified memory",
    "filters": {"content_scope": "all"},
    "freshness": {"mode": "off", "status": "existing_generation"},
    "retrieval": {
        "requested_mode": "lexical",
        "effective_mode": "lexical",
        "generation_id": "f" * 64,
        "semantic_status": "skipped",
        "semantic_weight": 0.0,
        "index": "core",
    },
    "generated_at": "2026-10-03T22:10:00Z",
    "diversification": {"status": "applied", "top_n": 2},
    "truncation": {"candidate_pool_truncated": True},
    "results": [
        {
            "item_id": "00000000-0000-8000-8000-0000000000a1",
            "ctx_session_id": "00000000-0000-8000-8000-0000000000a1",
            "session_id": "00000000-0000-8000-8000-0000000000a1",
            "ctx_event_id": "00000000-0000-8000-8000-0000000000e1",
            "event_id": "00000000-0000-8000-8000-0000000000e1",
            "event_seq": 65536,
            "provider": "claude",
            "provider_session_id": "synthetic-provider-session",
            "result_type": "session_result",
            "result_scope": "session",
            "agent_scope": "subagent",
            "session_relationship": "workflow_child",
            "rank": 1,
            "retrieval_score": 12.5,
            "snippet": "synthetic snippet about unified memory",
            "snippet_truncated": True,
            "visibility": "local",
            "citations": [{"kind": "event"}],
        },
        {
            "item_id": "00000000-0000-8000-8000-0000000000a2",
            "ctx_session_id": "00000000-0000-8000-8000-0000000000a2",
            "ctx_event_id": "00000000-0000-8000-8000-0000000000e2",
            "provider": "hermes",
            "result_type": "session_result",
            "result_scope": "session",
            "rank": 2,
            "snippet": "another synthetic snippet",
            "visibility": "local",
        },
    ],
    "result_window": {"limit": 2, "returned": 2, "more_available": True},
}

REAL_SHAPED_EVENT = {
    "schema_version": 1,
    "target": "event",
    "payload_type": "event_window",
    "format": "json",
    "ctx_event_id": "00000000-0000-8000-8000-0000000000e1",
    "ctx_session_id": "00000000-0000-8000-8000-0000000000a1",
    "event": {
        "item_id": "00000000-0000-8000-8000-0000000000e1",
        "ctx_event_id": "00000000-0000-8000-8000-0000000000e1",
        "ctx_session_id": "00000000-0000-8000-8000-0000000000a1",
        "record_type": "event",
        "provider": "claude",
        "provider_session_id": "synthetic-provider-session",
        "source_format": "claude_projects_jsonl_tree",
        "sequence": 65536,
        "event_type": "message",
        "role": "user",
        "occurred_at": "2026-10-01T04:57:52.192Z",
        "content": {"complete": True, "policy_status": "selected"},
        "text": "synthetic event text",
    },
    "events": [
        {
            "ctx_event_id": "00000000-0000-8000-8000-0000000000e1",
            "ctx_session_id": "00000000-0000-8000-8000-0000000000a1",
        }
    ],
    "copied_lineage": {"schema_version": 2, "returned": 0, "occurrences": []},
}


class CtxHistoryCapabilityTests(unittest.TestCase):
    def test_real_shaped_session_search_and_event_hydration(self):
        runner = _Runner([REAL_SHAPED_SEARCH, REAL_SHAPED_EVENT])
        cap = CtxHistoryCapability(binary="ctx", runner=runner)

        out = cap.search("unified memory", limit=2)
        self.assertEqual(out.generation_id, "f" * 64)
        self.assertEqual(
            [e.locator for e in out.evidence],
            [
                "ctx:event:00000000-0000-8000-8000-0000000000e1",
                "ctx:event:00000000-0000-8000-8000-0000000000e2",
            ],
        )
        self.assertTrue(all(e.source_version == "ctx-core:" + "f" * 64 for e in out.evidence))
        self.assertTrue(out.more_available)

        hydrated = cap.show_event(out.evidence[0].locator.removeprefix("ctx:event:"), window=3)
        self.assertEqual(hydrated.identity.source_system, "ctx:claude")
        self.assertEqual(hydrated.identity.source_session, "00000000-0000-8000-8000-0000000000a1")
        self.assertIsNone(hydrated.identity.ledger_seq)
        self.assertEqual(len(hydrated.window_events), 1)

    def test_ctx_reads_disable_ctx_side_effect_writes(self):
        # Observed on real ctx 2.2.7: a plain `search --refresh off` upserts
        # <data-root>/usage.sqlite unless CTX_LOCAL_USAGE_ENABLED=false.
        runner = _Runner([REAL_SHAPED_SEARCH, REAL_SHAPED_EVENT])
        cap = CtxHistoryCapability(binary="ctx", runner=runner)
        with mock.patch.dict(
            os.environ,
            {"CTX_DATA_ROOT": "/history/ctx", "CTX_LOCAL_USAGE_ENABLED": "true"},
        ):
            cap.search("unified memory", limit=2)
            cap.show_event("00000000-0000-8000-8000-0000000000e1")

        for _argv, kwargs in runner.calls:
            env = kwargs.get("env")
            self.assertIsNotNone(env, "ctx must run with an explicit read-only env")
            self.assertEqual(env["CTX_LOCAL_USAGE_ENABLED"], "false")
            self.assertEqual(env["CTX_ANALYTICS_ENABLED"], "false")
            self.assertEqual(env["CTX_DATA_ROOT"], "/history/ctx")

    def test_missing_binary_is_explicit(self):
        cap = CtxHistoryCapability(binary="", runner=_Runner([]))
        with self.assertRaisesRegex(CtxUnavailable, "not found"):
            cap.search("why retry")

    def test_search_is_read_only_and_preserves_generation(self):
        runner = _Runner([SEARCH])
        cap = CtxHistoryCapability(binary="/usr/bin/ctx", runner=runner)
        out = cap.search("why retry", provider="codex")

        self.assertEqual(out.generation_id, "gen-7")
        self.assertEqual(out.effective_mode, "lexical")
        self.assertEqual(out.returned, 1)
        self.assertEqual(out.evidence[0].source_id, "ctx:event:evt-1")
        self.assertEqual(out.evidence[0].source_version, "ctx-core:gen-7")
        self.assertEqual(out.evidence[0].locator, "ctx:event:evt-1")
        self.assertEqual(out.evidence[0].trust_class, "conversation")

        argv = runner.calls[0][0]
        self.assertEqual(argv[0], "/usr/bin/ctx")
        self.assertEqual(argv[1], "search")
        self.assertEqual(argv[argv.index("--refresh") + 1], "off")
        self.assertNotIn("setup", argv)
        self.assertNotIn("import", argv)
        self.assertNotIn("index", argv)

    def test_search_rejects_wrong_schema_and_non_read_only_freshness(self):
        wrong_schema = dict(SEARCH)
        wrong_schema["schema_version"] = 3
        cap = CtxHistoryCapability(binary="ctx", runner=_Runner([wrong_schema]))
        with self.assertRaisesRegex(CtxProtocolError, "schema"):
            cap.search("x")

        wrong_freshness = dict(SEARCH)
        wrong_freshness["freshness"] = {"mode": "background", "status": "completed"}
        cap = CtxHistoryCapability(binary="ctx", runner=_Runner([wrong_freshness]))
        with self.assertRaisesRegex(CtxProtocolError, "refresh=off"):
            cap.search("x")

    def test_semantic_retrieval_requires_explicit_opt_in(self):
        cap = CtxHistoryCapability(binary="ctx", runner=_Runner([]))
        with self.assertRaisesRegex(ValueError, "allow_semantic=True"):
            cap.search("conceptual recall", backend="hybrid")

    def test_show_event_maps_stable_identity_and_detects_payload_change(self):
        changed = {
            **EVENT,
            "event": {
                **EVENT["event"],
                "text": "the source event changed",
            },
        }
        cap = CtxHistoryCapability(binary="ctx", runner=_Runner([EVENT, changed]))

        first = cap.show_event("evt-1")
        second = cap.show_event("evt-1")

        self.assertEqual(first.identity.event_uid, second.identity.event_uid)
        self.assertEqual(first.identity.source_system, "ctx:codex")
        self.assertEqual(first.identity.source_session, "ses-1")
        self.assertEqual(first.identity.source_event_id, "evt-1")
        self.assertTrue(first.identity.has_payload_conflict(second.identity))

    def test_command_failure_is_explicit(self):
        cap = CtxHistoryCapability(
            binary="ctx",
            runner=_Runner([_Proc(returncode=2, stderr="generation unavailable")]),
        )
        with self.assertRaisesRegex(CtxCommandError, "generation unavailable"):
            cap.search("x")


if __name__ == "__main__":
    unittest.main()
