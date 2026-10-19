import copy
import json
from pathlib import Path
import unittest

from z0int.voice_plan import validate_voice_plan


class VoicePlanTests(unittest.TestCase):
    def setUp(self):
        self.plan = json.loads(
            (
                Path(__file__).resolve().parents[1] / "manifests/z0live_voice.v1.json"
            ).read_text()
        )

    def test_reproducible_and_pinned(self):
        plan = validate_voice_plan(self.plan)
        self.assertEqual(validate_voice_plan(plan), plan)
        self.assertEqual(plan["roles"]["smol"]["model"], "claude-haiku-4-5")
        other = copy.deepcopy(self.plan)
        other["roles"]["smol"]["model"] = "gpt-6-luna"
        self.assertNotEqual(validate_voice_plan(other)["digest"], plan["digest"])
        self.assertEqual(plan["roles"]["smol"]["model"], "claude-haiku-4-5")

    def test_missing_role_malformed_identifier_digest_and_fallback(self):
        changes = [
            lambda p: p["roles"].pop("task"),
            lambda p: p["roles"]["smol"].update(model=" broken "),
            lambda p: p.update(digest="bad"),
            lambda p: p.update(fallbacks=["implicit-auto"]),
        ]
        for change in changes:
            plan = copy.deepcopy(self.plan)
            change(plan)
            with self.assertRaises(ValueError):
                validate_voice_plan(plan)
