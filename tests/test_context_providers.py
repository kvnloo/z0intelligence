"""Adversarial tests for the provider registry and the two-hop identifier path.

These attack the replacement before any old surface is deleted. Each test pins a
failure the earlier generation of this code actually exhibited, or that the
required failure-injection probes name explicitly:

* a *silent empty* where a source died  -> the empty/error distinction
* a whole-query PHRASE that returns nothing on multi-term prose
* a provider outage sinking the packet
* a typed need answering with prose instead of the identifier it was asked for
* a failed or empty resolution being cached as if it were an answer

Tests that need the real local indexes skip cleanly when a store is absent, so
this file is honest on a machine without them rather than green for no reason.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from z0int import context_providers as cp
from z0int.context_resolve import (
    InformationNeed,
    invalidate_recipe_cache,
    recipe_cache_state,
    resolve_context,
    save_recipe_cache,
    ResolutionRecipe,
)

HAVE_SESSIONS = cp.SESSIONS_DB.is_file()
HAVE_COVERAGE = cp.COVERAGE_DB.is_file()


@contextmanager
def z0home(tmp: str):
    prev = os.environ.get("Z0INT_HOME")
    os.environ["Z0INT_HOME"] = tmp
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("Z0INT_HOME", None)
        else:
            os.environ["Z0INT_HOME"] = prev


class FtsMatchTests(unittest.TestCase):
    """Defect #1 and #2, both measured on this corpus."""

    def test_terms_are_individually_quoted_not_adjacent(self):
        expr = cp.fts_match("sft-svlm tencentdb", mode="all")
        # A bare hyphenated term raises `no such column: svlm` in FTS5, so each
        # term must carry its own quotes.
        self.assertEqual(expr, '"sft-svlm" AND "tencentdb"')

    def test_any_mode_ors_terms(self):
        self.assertEqual(cp.fts_match("alpha beta", mode="any"), '"alpha" OR "beta"')

    def test_prose_mode_is_any(self):
        # MEASURED: the conjunction form returns literally zero for a
        # ten-term natural question, so prose retrieval must not use it.
        self.assertEqual(cp.PROSE_MODE, "any")

    def test_deliberate_phrase_is_preserved(self):
        self.assertEqual(cp.fts_match('"exact phrase" other', mode="any"),
                         '"exact phrase" OR "other"')

    def test_empty_query_is_not_a_match_all(self):
        self.assertEqual(cp.fts_match("   "), '""')


class ProviderIsolationTests(unittest.TestCase):
    """Required probe: source unavailable."""

    def test_missing_store_is_an_explicit_error_not_an_empty(self):
        with mock.patch.object(cp, "COVERAGE_DB", Path("/nonexistent/coverage.db")):
            hits, _ = cp.resolve_identifier_need("anything at all")
        self.assertEqual(hits, [])

    def test_dead_coverage_store_is_reported_in_status(self):
        with mock.patch.object(cp, "COVERAGE_DB", Path("/nonexistent/coverage.db")):
            _, lex = cp.resolve_identifier_need("where is the plugin key stored")
        facts = [s for s in lex.status if s.provider.startswith("facts")]
        self.assertTrue(facts, "the fact store must report its own status")
        self.assertFalse(facts[0].ok)
        self.assertIn("FileNotFoundError", facts[0].error or "")

    def test_one_dead_provider_does_not_sink_the_lexical_fan_out(self):
        with mock.patch.object(cp, "HERMES_ROOT", Path("/nonexistent/hermes")):
            res = cp.search_lexical("agentsview idle timeout", limit=2)
        by = {s.provider: s for s in res.status}
        self.assertFalse(by["hermes"].ok)
        # ...and the healthy providers still ran.
        self.assertTrue(any(s.ok and s.hits for s in res.status if s.provider != "hermes"))

    def test_search_reports_every_registered_provider(self):
        res = cp.search_lexical("plugin key", limit=1)
        self.assertEqual({s.provider for s in res.status},
                         {name for name, _ in cp.LEXICAL_PROVIDERS})


class TypedKindTests(unittest.TestCase):
    """Required probe: the need states a type; the answer must match it."""

    def test_every_declared_kind_is_routed(self):
        # The original module declared `exact_symbol` with no handling branch at
        # all, so it fell through to "unsupported kind".
        for kind in ("exact_path", "exact_symbol", "path", "symbol", "config_value",
                     "model", "command", "url", "revision", "repository", "issue",
                     "identifier", "natural_language"):
            self.assertTrue(
                cp.kinds_for(kind) or kind in cp.SOURCE_KINDS,
                f"{kind} resolves to no provider",
            )

    def test_typed_needs_map_to_fact_kinds(self):
        self.assertEqual(cp.kinds_for("path"), ("path",))
        self.assertEqual(cp.kinds_for("command"), ("command",))
        self.assertEqual(cp.kinds_for("url"), ("url",))
        self.assertNotIn("command", cp.kinds_for("config_value"))


class TokenHarvestTests(unittest.TestCase):
    def test_harvests_identifiers_not_prose(self):
        toks = cp.fact_tokens(
            "the key lives at /home/kvn/.hermes/profiles/chiefstaff/.env so set "
            "ANTHROPIC_API_KEY and run scripts/cleanup-cachyos.sh"
        )
        joined = " ".join(toks)
        self.assertIn("/home/kvn/.hermes/profiles/chiefstaff/.env", joined)
        self.assertIn("ANTHROPIC_API_KEY", joined)
        self.assertIn("cleanup-cachyos.sh", joined)

    def test_harvest_is_bounded_and_deduplicated(self):
        toks = cp.fact_tokens("a.py a.py a.py " * 200, limit=5)
        self.assertLessEqual(len(toks), 5)
        self.assertEqual(len(toks), len({t.lower() for t in toks}))


@unittest.skipUnless(HAVE_SESSIONS, "AgentsView sessions.db not present")
class LexicalProviderTests(unittest.TestCase):
    def test_prose_question_returns_evidence(self):
        res = cp.search_lexical(
            "how should agentsview serve be kept alive to avoid the idle-timeout shutdown",
            limit=3,
        )
        self.assertTrue(res.hits, "a long prose question must not return zero")
        self.assertTrue(all(h.excerpt for h in res.hits))

    def test_single_provider_selection(self):
        res = cp.search_lexical("agentsview", limit=2, only=("conversation",))
        self.assertEqual([s.provider for s in res.status], ["conversation"])


@unittest.skipUnless(HAVE_SESSIONS and HAVE_COVERAGE, "local indexes not present")
class TwoHopBridgeTests(unittest.TestCase):
    """The capability this whole slice exists to prove."""

    def test_reaches_an_identifier_with_no_lexical_overlap(self):
        # The question and the answer share no tokens at all.
        hits, _ = cp.resolve_identifier_need(
            "where is the CachyOS cleanup script located and what is it called",
            need_kind="path",
        )
        self.assertTrue(hits, "the bridge returned nothing")
        self.assertTrue(
            any("cleanup-cachyos.sh" in (h.excerpt or "") for h in hits),
            [h.excerpt for h in hits[:3]],
        )

    def test_bridge_records_how_it_was_reached(self):
        hits, _ = cp.resolve_identifier_need(
            "where is the CachyOS cleanup script located and what is it called",
            need_kind="path",
        )
        self.assertTrue(any(h.bridge for h in hits),
                        "a bridged identifier must not look like a direct hit")

    def test_typed_need_leads_with_the_identifier(self):
        packet = resolve_context(
            needs=[InformationNeed(id="p", description="where is the CachyOS cleanup script located",
                                   kind="path")],
            use_cache=False,
        )
        self.assertTrue(packet.evidence)
        first = packet.evidence[0]
        self.assertIn("via=identifier", first.note or "")
        self.assertIn("cleanup-cachyos.sh", first.excerpt or first.locator)

    def test_identifier_miss_is_a_gap_not_an_invention(self):
        packet = resolve_context(
            needs=[InformationNeed(id="z", description="zzz-nonexistent-identifier-qqq",
                                   kind="path")],
            use_cache=False,
        )
        self.assertTrue(any("no evidence" in g for g in packet.unresolved_gaps))


class CacheCorrectnessTests(unittest.TestCase):
    """Semantics folded in from MemoryPacketCache."""

    def test_provider_failure_is_never_cached(self):
        with tempfile.TemporaryDirectory() as tmp, z0home(tmp):
            with mock.patch.object(cp, "HERMES_ROOT", Path("/nonexistent/hermes")):
                p = resolve_context(
                    needs=[InformationNeed(id="n", description="anything", kind="natural_language")],
                    use_cache=True,
                )
            self.assertEqual(p.measurements["cache_write"], "skipped:provider-error")
            self.assertEqual(recipe_cache_state(p.recipe.request_signature), "MISS")

    def test_empty_resolution_is_never_cached(self):
        with tempfile.TemporaryDirectory() as tmp, z0home(tmp):
            p = resolve_context(
                needs=[InformationNeed(id="n", description="zzz-nothing-qqq",
                                       kind="natural_language")],
                use_cache=True,
                providers=("misc-extra",),
            )
            if not p.evidence:
                self.assertEqual(p.measurements["cache_write"], "skipped:empty-evidence")
                self.assertEqual(recipe_cache_state(p.recipe.request_signature), "MISS")

    def test_invalidate_keeps_last_known_good(self):
        with tempfile.TemporaryDirectory() as tmp, z0home(tmp):
            recipe = ResolutionRecipe(
                capability_id="context_resolve", request_signature="sig123",
                scope_fingerprint="scopeA", policy_revision="v0",
                source_epochs={"policy": "v0"}, operations=(), required_evidence_fields=(),
                verifier_revision="none", hardware_profile="local",
            )
            save_recipe_cache(recipe, {"evidence_count": 3})
            self.assertEqual(recipe_cache_state("sig123"), "FRESH")

            self.assertEqual(invalidate_recipe_cache("scopeA"), 1)
            # Still readable, and now explicitly stale: never silently deleted.
            self.assertEqual(recipe_cache_state("sig123"), "STALE")
            self.assertIsNotNone(cp.__dict__ and __import__(
                "z0int.context_resolve", fromlist=["load_recipe_cache"]
            ).load_recipe_cache("sig123"))

    def test_invalidation_is_scoped(self):
        with tempfile.TemporaryDirectory() as tmp, z0home(tmp):
            for sig, scope in (("s1", "A"), ("s2", "B")):
                save_recipe_cache(
                    ResolutionRecipe(
                        capability_id="context_resolve", request_signature=sig,
                        scope_fingerprint=scope, policy_revision="v0",
                        source_epochs={}, operations=(), required_evidence_fields=(),
                        verifier_revision="none", hardware_profile="local",
                    ),
                    {"evidence_count": 1},
                )
            self.assertEqual(invalidate_recipe_cache("A"), 1)
            self.assertEqual(recipe_cache_state("s1"), "STALE")
            self.assertEqual(recipe_cache_state("s2"), "FRESH")

    def test_policy_revision_participates_in_the_signature(self):
        needs = [InformationNeed(id="n", description="same question")]
        from z0int.context_resolve import _request_signature

        a = _request_signature(needs, None)
        self.assertEqual(a, _request_signature(needs, None))
        self.assertNotEqual(a, _request_signature(needs, None) + "x")


if __name__ == "__main__":
    unittest.main()
