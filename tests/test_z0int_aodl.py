from __future__ import annotations

import re
import unittest
from dataclasses import replace

from z0int.aodl import (
    AODL_NODE_KINDS,
    AodlBindingConfig,
    AodlBudgets,
    AodlSpend,
    append_event,
    assert_basic_aodl_invariants,
    binding_for_implementation_stage,
    check_budget,
    compile_aodl,
    outcome_event,
    route_event,
    runtime_contract,
    with_observed_graph,
)
from z0int.cascade import CascadePolicy, StagePolicy
from z0int.routines import LiteralPredicate, RoutineCandidate, RoutineRule


class AodlBindingTests(unittest.TestCase):
    def cascade(self) -> CascadePolicy:
        return CascadePolicy(
            capability_id="coding.needs_verification",
            stages=(
                StagePolicy("mb", 0.95),
                StagePolicy("local_slm", 0.90),
                StagePolicy("frontier", 0.0, terminal=True),
            ),
            final_stage="frontier",
            min_success_rate=0.99,
            max_success_regression=0.01,
            objective="premium_tokens_per_verified_success",
            status="promoted",
        )

    def routine(self) -> RoutineCandidate:
        return RoutineCandidate(
            capability_id="coding.needs_verification",
            rule=RoutineRule(
                (
                    LiteralPredicate("prev_family", "eq", "EDIT"),
                    LiteralPredicate("tool_ok", "eq", True),
                )
            ),
            output="VERIFY",
            source_specialist="mb-gen2",
            source_generation=2,
            evidence_level="L2_outcome",
            precision_floor=0.98,
            min_sessions=5,
            status="promoted",
        )

    def test_intent_contract_is_stable_and_strategy_lives_in_plan(self) -> None:
        doc = compile_aodl(
            capability_id="coding.needs_verification",
            cascade=self.cascade(),
            routines=[self.routine()],
            config=AodlBindingConfig(
                harness_id="omp",
                budgets=AodlBudgets(
                    tokens=4000,
                    premium_tokens=1000,
                    latency_ms=1500,
                    joules=25.0,
                    attention=1,
                ),
            ),
        )
        assert_basic_aodl_invariants(doc)
        kinds = {n["kind"] for n in doc["intentGraph"]["nodes"]}
        self.assertTrue(kinds <= AODL_NODE_KINDS)
        self.assertEqual(kinds, {"task", "executor", "verifier", "stateStore"})
        self.assertNotIn("service", kinds)
        self.assertNotIn("model", kinds)
        executor = next(n for n in doc["intentGraph"]["nodes"] if n["kind"] == "executor")
        self.assertEqual(executor["harness"], "omp")
        self.assertEqual(doc["policies"]["protocol"], "intent-contract")
        self.assertNotIn("route", doc["policies"])
        order = doc["plan"]["route"]["order"]
        self.assertEqual(order[0], "routine-service")
        self.assertEqual(order[-1], "stage-frontier")
        self.assertEqual(doc["constraints"]["budgets"]["premium_tokens"], 1000)
        self.assertEqual(doc["constraints"]["budgets"]["attention"], 1)
        self.assertEqual(doc["constraints"]["acceptance"]["capabilityId"], "coding.needs_verification")

    def test_private_implementation_binding_lives_only_in_compiled_plan(self) -> None:
        doc = compile_aodl(
            capability_id="coding.needs_verification",
            cascade=self.cascade(),
            routines=[self.routine()],
            config=AodlBindingConfig(
                stage_bindings={
                    "mb": {"binding": "artifact", "uri": "z0int://specialists/needs-verification/mb"},
                    "local_slm": {"binding": "resource", "selector": {"class": "local_semantic"}},
                    "frontier": {"binding": "resource", "selector": {"class": "frontier"}},
                },
                stage_roles={"mb": "specialist", "local_slm": "local-semantic", "frontier": "frontier"},
            ),
        )
        plan = doc["plan"]
        self.assertEqual(plan["compiler"], "z0int.aodl.v2")
        self.assertEqual(plan["profile"], "intent-contract")
        self.assertEqual(plan["bindings"]["stage-specialist"]["uri"], "z0int://specialists/needs-verification/mb")
        self.assertEqual(plan["bindings"]["stage-specialist"]["implementationStage"], "mb")
        self.assertEqual(plan["route"]["thresholds"]["stage-specialist"], 0.95)
        self.assertEqual(plan["bindings"]["routine-registry"]["artifactType"], "z0int.routine_registry.v1")
        rendered_intent = str(doc["intentGraph"])
        self.assertNotIn("prev_family", rendered_intent)
        self.assertNotIn("Qwen", rendered_intent)
        self.assertNotIn("routine-service", rendered_intent)
        self.assertNotIn("stage-specialist", rendered_intent)

    def test_intent_source_hash_does_not_change_when_implementation_changes(self) -> None:
        a = compile_aodl(
            capability_id="coding.needs_verification",
            cascade=self.cascade(),
            routines=[self.routine()],
        )
        changed = replace(
            self.cascade(),
            stages=(
                StagePolicy("mb", 0.97),
                StagePolicy("local_slm", 0.91),
                StagePolicy("frontier", 0.0, terminal=True),
            ),
        )
        b = compile_aodl(
            capability_id="coding.needs_verification",
            cascade=changed,
            routines=[],
        )
        self.assertEqual(a["provenance"]["sourceHash"], b["provenance"]["sourceHash"])
        self.assertNotEqual(a["plan"]["planHash"], b["plan"]["planHash"])
        self.assertRegex(a["provenance"]["sourceHash"], re.compile(r"^[a-f0-9]{64}$"))

    def test_observed_route_and_outcome_are_events_and_observed_graph(self) -> None:
        doc = compile_aodl(
            capability_id="coding.needs_verification",
            cascade=self.cascade(),
            routines=[self.routine()],
        )
        doc = with_observed_graph(doc, stage="routine-service", lifecycle="running")
        kinds = {n["kind"] for n in doc["observedGraph"]["nodes"]}
        self.assertEqual(kinds, {"executor", "service", "verifier"})
        observed_executor = next(n for n in doc["observedGraph"]["nodes"] if n["kind"] == "executor")
        self.assertEqual(observed_executor["harness"], "omp")

        sh = doc["provenance"]["sourceHash"]
        routed = route_event(
            trace_id="trace-1",
            source_hash_hex=sh,
            revision=doc["revision"],
            stage="routine-service",
            capability_id="coding.needs_verification",
            confidence=1.0,
            routine_id=self.routine().routine_id,
        )
        outcome = outcome_event(
            trace_id="trace-1",
            source_hash_hex=sh,
            revision=doc["revision"],
            success=True,
            verifier="pytest",
            premium_tokens=0,
            total_tokens=0,
            latency_ms=0.4,
            causal_parents=[routed["eventId"]],
        )
        observed = append_event(append_event(doc, routed), outcome)
        self.assertEqual([e["type"] for e in observed["eventLog"]], ["route", "stateUpdate"])
        self.assertEqual(observed["eventLog"][1]["causalParents"], [routed["eventId"]])
        self.assertTrue(observed["eventLog"][1]["payload"]["success"])

    def test_no_routine_keeps_compiled_slot_disabled_without_changing_intent(self) -> None:
        with_routine = compile_aodl(
            capability_id="coding.needs_verification",
            cascade=self.cascade(),
            routines=[self.routine()],
        )
        without = compile_aodl(
            capability_id="coding.needs_verification",
            cascade=self.cascade(),
            routines=[],
        )
        self.assertEqual(with_routine["intentGraph"], without["intentGraph"])
        self.assertEqual(with_routine["provenance"]["sourceHash"], without["provenance"]["sourceHash"])
        self.assertEqual(without["plan"]["route"]["order"][0], "routine-service")
        self.assertFalse(without["plan"]["bindings"]["routine-service"]["enabled"])

    def test_gbrain_memory_is_stable_intent_and_binding_is_plan_detail(self) -> None:
        base = compile_aodl(
            capability_id="coding.needs_verification",
            cascade=self.cascade(),
            routines=[self.routine()],
            config=AodlBindingConfig(
                memory_id="personal-memory",
                memory_schema="MemoryContext",
                memory_binding={
                    "runtime": "gbrain",
                    "protocol": "MEMORY_VERBS_v1",
                    "readVerbs": ["entity", "recall", "context_pack", "delta"],
                    "worldOnlyDefault": True,
                },
            ),
        )
        nodes = {n["id"]: n for n in base["intentGraph"]["nodes"]}
        self.assertEqual(nodes["personal-memory"]["kind"], "memory")
        self.assertEqual(nodes["personal-memory"]["authorityCeiling"], ["recall"])
        memory_edge = next(e for e in base["intentGraph"]["edges"] if e["id"] == "e-memory-runtime")
        self.assertEqual(memory_edge["relation"], "data")
        self.assertEqual(memory_edge["authority"]["grant"], [])
        self.assertEqual(base["plan"]["bindings"]["personal-memory"]["runtime"], "gbrain")

        changed_binding = compile_aodl(
            capability_id="coding.needs_verification",
            cascade=self.cascade(),
            routines=[self.routine()],
            config=AodlBindingConfig(
                memory_id="personal-memory",
                memory_schema="MemoryContext",
                memory_binding={"runtime": "gbrain", "protocol": "MEMORY_VERBS_v1", "source": "team"},
            ),
        )
        self.assertEqual(base["provenance"]["sourceHash"], changed_binding["provenance"]["sourceHash"])
        self.assertNotEqual(base["plan"]["planHash"], changed_binding["plan"]["planHash"])

    def test_runtime_consumes_the_same_aodl_contract(self) -> None:
        doc = compile_aodl(
            capability_id="coding.needs_verification",
            cascade=self.cascade(),
            routines=[self.routine()],
            config=AodlBindingConfig(
                harness_id="codex",
                budgets=AodlBudgets(tokens=3000, premium_tokens=700),
                stage_roles={"mb": "specialist", "local_slm": "local-semantic", "frontier": "frontier"},
            ),
        )
        contract = runtime_contract(doc)
        self.assertEqual(contract.capability_id, "coding.needs_verification")
        self.assertEqual(contract.harness_id, "codex")
        self.assertEqual(contract.route_order[0], "routine-service")
        self.assertEqual(contract.route_order[-1], "stage-frontier")
        self.assertEqual(contract.budgets["premium_tokens"], 700)
        self.assertEqual(contract.bindings["stage-specialist"]["implementationStage"], "mb")

    def test_gamma_budget_is_runtime_enforced_including_attention(self) -> None:
        doc = compile_aodl(
            capability_id="coding.needs_verification",
            cascade=self.cascade(),
            routines=[self.routine()],
            config=AodlBindingConfig(
                budgets=AodlBudgets(tokens=500, premium_tokens=100, latency_ms=1000, attention=1),
                stage_roles={"mb": "specialist", "local_slm": "local-semantic", "frontier": "frontier"},
            ),
        )
        contract = runtime_contract(doc)
        ok = check_budget(
            contract,
            observed=AodlSpend(tokens=100, premium_tokens=0, latency_ms=100),
            proposed=AodlSpend(tokens=120, premium_tokens=0, latency_ms=20),
        )
        self.assertTrue(ok.allowed)
        blocked = check_budget(
            contract,
            observed=AodlSpend(tokens=450, premium_tokens=90, latency_ms=900, attention=1),
            proposed=AodlSpend(tokens=100, premium_tokens=20, latency_ms=150, attention=1),
        )
        self.assertFalse(blocked.allowed)
        self.assertEqual(set(blocked.exceeded), {"tokens", "premium_tokens", "latency_ms", "attention"})
        slot = binding_for_implementation_stage(contract, "mb")
        self.assertIsNotNone(slot)
        assert slot is not None
        self.assertEqual(slot[0], "stage-specialist")

    def test_hot_plane_refuses_uncredited_cascade_unless_explicit_shadow(self) -> None:
        candidate = replace(self.cascade(), status="candidate")
        with self.assertRaises(ValueError):
            compile_aodl(
                capability_id="coding.needs_verification",
                cascade=candidate,
            )
        shadow = compile_aodl(
            capability_id="coding.needs_verification",
            cascade=candidate,
            config=AodlBindingConfig(allow_uncredited_shadow=True),
        )
        self.assertEqual(shadow["plan"]["deployment"]["mode"], "shadow")
        self.assertFalse(shadow["plan"]["deployment"]["trafficEligible"])
        self.assertEqual(shadow["plan"]["deployment"]["creditStatus"], "candidate")

    def test_unknown_or_control_room_harness_fails_closed(self) -> None:
        with self.assertRaises(ValueError):
            AodlBindingConfig(harness_id="o8")
        with self.assertRaises(ValueError):
            AodlBindingConfig(harness_id="langchain")


if __name__ == "__main__":
    unittest.main()
