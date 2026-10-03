from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

from z0int.gbrain import (
    GBrainClient,
    GBrainProtocolError,
    attach_shadow_candidate,
    build_shadow_candidate,
    response_to_context_packet,
)


class GBrainBridgeTests(unittest.TestCase):
    def test_root_cli_exposes_shadow_only_gbrain_commands(self) -> None:
        from z0int.cli import build_parser

        parser = build_parser()
        pack = parser.parse_args(["context", "gbrain-pack", "--entity", "z0intelligence"])
        self.assertEqual(pack.context_cmd, "gbrain-pack")
        self.assertEqual(pack.entity, ["z0intelligence"])
        wake = parser.parse_args(["context", "gbrain-delta", "--session-id", "wake-1"])
        self.assertEqual(wake.context_cmd, "gbrain-delta")
        self.assertEqual(wake.session_id, "wake-1")

    def test_cli_transport_is_read_only_and_world_only(self) -> None:
        calls = []

        def runner(argv, **kwargs):
            calls.append((argv, kwargs))
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"protocol_version": 1, "cards": [], "open_threads": [], "facts": []}),
                stderr="",
            )

        client = GBrainClient(binary="/bin/gbrain", runner=runner)
        out = client.context_pack(["z0intelligence"], budget_tokens=700, session_id="s-1")
        self.assertEqual(out["protocol_version"], 1)
        argv = calls[0][0]
        self.assertEqual(argv[:5], ["/bin/gbrain", "call", "--source", "default", "context_pack"])
        payload = json.loads(argv[5])
        self.assertEqual(payload["entities"], "z0intelligence")
        self.assertEqual(payload["budget_tokens"], 700)
        self.assertEqual(payload["session_id"], "s-1")

        with self.assertRaises(ValueError):
            client.call("remember", {"fact": "nope"})
        with self.assertRaises(ValueError):
            client.call("recall", {"query": "secret"})
        with self.assertRaises(ValueError):
            client.call("context_pack", {"entities": "x", "include_private": True})

        client.delta(since="2026-10-03T00:00:00Z", since_slug="projects/z0intelligence")
        delta_payload = json.loads(calls[-1][0][5])
        self.assertEqual(delta_payload["since_slug"], "projects/z0intelligence")
        with self.assertRaises(ValueError):
            client.delta(since_slug="projects/z0intelligence")

    def test_protocol_errors_fail_closed(self) -> None:
        def runner(argv, **kwargs):
            return SimpleNamespace(returncode=0, stdout='{"protocol_version":2}', stderr="")

        with self.assertRaises(GBrainProtocolError):
            GBrainClient(binary="gbrain", runner=runner).context_pack("x")

    def test_context_pack_projects_to_existing_context_packet(self) -> None:
        response = {
            "protocol_version": 1,
            "cards": [
                {
                    "entity": {"slug": "projects/z0intelligence", "title": "z0intelligence", "type": "project"},
                    "summary": "decision router",
                }
            ],
            "open_threads": [
                {"loop_id": "loop-7", "kind": "commitment", "status": "open", "counterparty": "maintainer"}
            ],
            "facts": [{"fact_id": "f-1", "fact": "shadow integration pending"}],
            "budget_tokens": 1200,
            "budget_used": 190,
        }
        packet = response_to_context_packet(response, verb="context_pack")
        self.assertEqual(packet.measurements["verb"], "context_pack")
        self.assertEqual(len(packet.evidence), 3)
        self.assertTrue(all(e.trust_class == "derived_memory" for e in packet.evidence))
        self.assertTrue(packet.aodl_projection)
        self.assertNotIn("authority", packet.aodl_projection)

    def test_delta_becomes_shadow_candidate_not_authority(self) -> None:
        response = {
            "protocol_version": 1,
            "threads": [{"loop_id": "loop-9", "kind": "commitment", "status": "open"}],
            "pages": [],
            "facts": [],
            "has_more": False,
        }
        candidate = build_shadow_candidate(response, verb="delta")
        self.assertTrue(candidate["shadow_only"])
        self.assertFalse(candidate["traffic_eligible"])
        self.assertEqual(candidate["attention"]["action"], "PREPARE")
        self.assertEqual(candidate["authority"]["granted"], [])
        self.assertTrue(candidate["candidate_id"].startswith("gb:"))
        linked = attach_shadow_candidate(
            {"trace_id": "t-1", "extra": {"experiment_id": "exp-1"}, "success": True},
            candidate,
        )
        self.assertEqual(linked["success"], True)
        self.assertEqual(linked["extra"]["experiment_id"], "exp-1")
        self.assertEqual(linked["extra"]["gbrain_candidate_id"], candidate["candidate_id"])
        self.assertEqual(linked["extra"]["gbrain_shadow_action"], "PREPARE")

        empty = build_shadow_candidate(
            {"protocol_version": 1, "threads": [], "pages": [], "facts": []},
            verb="delta",
        )
        self.assertEqual(empty["attention"]["action"], "IGNORE")


if __name__ == "__main__":
    unittest.main()
