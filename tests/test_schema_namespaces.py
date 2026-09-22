"""Schema namespaces must belong to the repo that owns the artifact.

Naming convention across the stack is `<owner>.<thing>.v<major>`. z0intelligence
emitted three of its own research artifacts under the **z0evals** namespace:

    z0evals.steal-sweep.v1
    z0evals.exploratory-beta.tournament.v1
    z0evals.exploratory-beta.provider-tournament.v1

z0evals owns frozen evaluation and publication. Those artifacts are this repo's
own exploratory output, so the id claimed a namespace — and implied an owner —
that did not define it. That is an ownership inversion, not a naming preference:
a consumer reading `z0evals.*` would reasonably look to z0evals for the schema.

They are now `z0int.*`. Nothing anywhere referenced the old ids (checked in
z0evals, evolution-lab and z0), and the only artifacts carrying them live under
`results/beta-sprint-20260922/` — **not** in the frozen Phase 1B run, which is
deliberately left untouched. Existing artifacts keep the old id; measurement is
not rewritten.

This test is the guard. It fails on a new namespace rather than allowing one
silently, because "which repo owns this schema" is a decision, not a default.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCHEMA_LITERAL = re.compile(r'"schema"\s*:\s*"([A-Za-z0-9._-]+)"')

# Namespaces this repo may legitimately emit into.
#
#   z0int  this repo's own artifacts
#   os     a SHARED namespace. `os.next_context.v0` is registered in the z0
#          registry as owned by `flow`, while this repo carries the os-context
#          machinery. Which one owns it is an open question recorded in the
#          sprint audit -- deliberately listed here rather than silently allowed,
#          so it is visible and a third namespace cannot slip in.
ALLOWED_NAMESPACES = {"z0int", "os"}

# Explicitly forbidden: the inversion that was fixed.
FORBIDDEN_NAMESPACES = {"z0evals", "tokenomics", "kerdoios", "aodl", "evolution-lab"}


def _emitted_schema_ids() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for root in (REPO / "src", REPO / "scripts"):
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            for match in SCHEMA_LITERAL.finditer(path.read_text(encoding="utf-8")):
                found.setdefault(match.group(1), set()).add(str(path.relative_to(REPO)))
    return found


class SchemaNamespaceTests(unittest.TestCase):
    def test_no_schema_is_emitted_under_another_repos_namespace(self) -> None:
        violations: list[str] = []
        for schema_id, files in _emitted_schema_ids().items():
            namespace = schema_id.split(".", 1)[0]
            if namespace in FORBIDDEN_NAMESPACES:
                violations.append(f"{schema_id} (in {', '.join(sorted(files))})")
        self.assertEqual(
            violations,
            [],
            "these schema ids claim a namespace owned by another repo:\n  "
            + "\n  ".join(violations),
        )

    def test_namespaces_are_restricted_to_the_documented_set(self) -> None:
        namespaces = {sid.split(".", 1)[0] for sid in _emitted_schema_ids()}
        unexpected = namespaces - ALLOWED_NAMESPACES
        self.assertEqual(
            unexpected,
            set(),
            "new schema namespace(s) appeared: "
            f"{sorted(unexpected)}. Decide which repo owns them, add to the z0 "
            "registry if it is a cross-repo contract, then extend "
            "ALLOWED_NAMESPACES here.",
        )

    def test_the_specific_inversion_does_not_come_back(self) -> None:
        self.assertNotIn(
            "z0evals.steal-sweep.v1",
            _emitted_schema_ids(),
            "steal-sweep is z0intelligence's own artifact, not a z0evals one",
        )

    def test_the_replacement_ids_are_present(self) -> None:
        emitted = _emitted_schema_ids()
        for expected in (
            "z0int.steal-sweep.v1",
            "z0int.exploratory-beta.tournament.v1",
            "z0int.exploratory-beta.provider-tournament.v1",
        ):
            self.assertIn(expected, emitted, f"{expected} is no longer emitted")


if __name__ == "__main__":
    unittest.main()
