from __future__ import annotations

import json
import unittest
from dataclasses import dataclass
from typing import Any

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


class CtxHistoryCapabilityTests(unittest.TestCase):
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
