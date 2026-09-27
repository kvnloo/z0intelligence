"""Shared capability model: providers are data, never branches (z0intelligence#20).

The point of these tests is that Groq and Cerebras are ordinary candidates. If a
provider ever needed a special case, one of these would fail.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from z0int.cognition.actions import ActionCandidate, ActionGraph, compile_actions
from z0int.cognition.adapters.local_slm import ToolDecision
from z0int.cognition.candidates import (
    QUALITY_CLASSES,
    CapabilityProfile,
    CapabilityRequirement,
    CandidateModel,
    CandidateRungBackend,
    build_inventory,
    candidates_from_local_manifest,
    candidates_from_pi_ai_catalog,
    filter_candidates,
    load_pi_ai_catalog,
    rank_candidates,
    requirement_for_legal_set,
)
from z0int.cognition.manifest import load_local_cognition

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "z0int" / "cognition"


def _pi_ai_entry(model_id, provider, **kw):
    entry = {
        "id": model_id,
        "provider": provider,
        "api": "openai-completions",
        "reasoning": False,
        "input": ["text"],
        "contextWindow": 131072,
        "maxTokens": 32768,
        "cost": {"input": 0.1, "output": 0.2},
    }
    entry.update(kw)
    return {"openai-completions": {model_id: entry}}


#: A pi-ai-shaped catalog with the two free-tier providers from DSH's catalog.
CATALOG = {
    "openai-completions": {
        "llama-3.3-70b-versatile": {
            "id": "llama-3.3-70b-versatile",
            "provider": "groq",
            "api": "openai-completions",
            "reasoning": False,
            "input": ["text"],
            "contextWindow": 131072,
            "maxTokens": 32768,
            "cost": {"input": 0.59, "output": 0.79},
        },
        "gpt-oss-120b": {
            "id": "gpt-oss-120b",
            "provider": "cerebras",
            "api": "openai-completions",
            "reasoning": True,
            "input": ["text", "image"],
            "contextWindow": 131072,
            "maxTokens": 40960,
            "cost": {"input": 0.35, "output": 0.75},
        },
    }
}

HINTS = {
    "groq/llama-3.3-70b-versatile": {
        "cost_class": "free",
        "quality_class": "standard",
        "max_risk_class": "write",
        "serves_tiers": ["orchestrator_slm", "general_slm"],
    },
    "cerebras/gpt-oss-120b": {
        "cost_class": "free",
        "quality_class": "strong",
        "max_risk_class": "write",
        "serves_tiers": ["orchestrator_slm", "general_slm"],
    },
}


# --- the shared capability model ----------------------------------------


def test_pi_ai_catalog_projects_groq_and_cerebras_as_plain_candidates():
    rows = {c.candidate_id: c for c in candidates_from_pi_ai_catalog(CATALOG, hints=HINTS)}
    assert set(rows) == {"groq/llama-3.3-70b-versatile", "cerebras/gpt-oss-120b"}
    groq = rows["groq/llama-3.3-70b-versatile"]
    cerebras = rows["cerebras/gpt-oss-120b"]
    # Capabilities come from the catalog rows, not from the provider name.
    assert groq.capabilities.tool_calling is True
    assert groq.capabilities.context_window == 131072
    assert groq.capabilities.reasoning is False
    assert groq.capabilities.modalities == ("text",)
    assert cerebras.capabilities.reasoning is True
    assert cerebras.capabilities.modalities == ("text", "image")
    assert groq.cost_class == "free" and cerebras.cost_class == "free"
    assert groq.source == "pi_ai_catalog"


def test_a_single_string_tier_hint_is_not_split_into_characters():
    """A hint may be a bare string; it must still mean one tier."""
    raw = json.loads(json.dumps(CATALOG))
    raw["openai-completions"]["gpt-oss-120b"]["z0int"] = {"serves_tiers": "orchestrator_slm"}
    rows = candidates_from_pi_ai_catalog(raw)
    cerebras = next(c for c in rows if c.candidate_id == "cerebras/gpt-oss-120b")
    assert cerebras.serves_tiers == ("orchestrator_slm",)


def test_a_third_provider_needs_no_code_change():
    """A provider nobody wrote code for flows through the same adapter."""
    catalog = dict(CATALOG)
    catalog["openai-completions"] = dict(catalog["openai-completions"])
    catalog["openai-completions"]["made-up-9b"] = {
        "id": "made-up-9b",
        "provider": "examplecloud",
        "api": "openai-completions",
        "reasoning": True,
        "input": ["text"],
        "contextWindow": 65536,
    }
    rows = candidates_from_pi_ai_catalog(catalog, hints=HINTS)
    assert "examplecloud/made-up-9b" in {c.candidate_id for c in rows}


def test_swapping_provider_names_does_not_change_selection_behaviour():
    """Identity is the only thing that changes when the provider name changes."""
    base = candidates_from_pi_ai_catalog(CATALOG, hints=HINTS)
    swapped_raw = json.loads(json.dumps(CATALOG))
    swapped_raw["openai-completions"]["llama-3.3-70b-versatile"]["provider"] = "cerebras"
    swapped_raw["openai-completions"]["gpt-oss-120b"]["provider"] = "groq"
    swapped = candidates_from_pi_ai_catalog(
        swapped_raw,
        hints={
            "cerebras/llama-3.3-70b-versatile": HINTS["groq/llama-3.3-70b-versatile"],
            "groq/gpt-oss-120b": HINTS["cerebras/gpt-oss-120b"],
        },
    )
    base_shape = sorted(
        (c.model_id, c.quality_class, c.max_risk_class, c.cost_class, c.serves_tiers)
        for c in base
    )
    swapped_shape = sorted(
        (c.model_id, c.quality_class, c.max_risk_class, c.cost_class, c.serves_tiers)
        for c in swapped
    )
    assert base_shape == swapped_shape


def _string_constants_outside_docstrings(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            docstrings.add(id(node.value))
    found: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            found.add(node.value)
    return found


@pytest.mark.parametrize("module", ["candidates.py", "surface.py"])
def test_selection_code_has_no_provider_specific_branch(module):
    literals = _string_constants_outside_docstrings(PACKAGE / module)
    for provider in ("groq", "cerebras", "openai", "anthropic"):
        assert provider not in literals, f"{module} branches on provider {provider!r}"


# --- hard filters -------------------------------------------------------


def _candidate(candidate_id="local/x", **kw):
    kw.setdefault("provider", candidate_id.split("/")[0])
    kw.setdefault("model_id", candidate_id.split("/")[-1])
    return CandidateModel(candidate_id=candidate_id, **kw)


def test_capability_filter_rejects_a_candidate_missing_tools():
    eligible, rejected = filter_candidates(
        [_candidate(capabilities=CapabilityProfile(tool_calling=False))],
        required=CapabilityRequirement(tool_calling=True),
        quality_class_required="bounded",
    )
    assert eligible == ()
    assert rejected[0].reason == "capability:tool_calling"


def test_quality_filter_rejects_below_the_required_class():
    eligible, rejected = filter_candidates(
        [_candidate(quality_class="tiny")], quality_class_required="strong"
    )
    assert eligible == ()
    assert rejected[0].reason == "quality:tiny<strong"


def test_risk_filter_rejects_a_candidate_with_a_lower_ceiling():
    eligible, rejected = filter_candidates(
        [_candidate(max_risk_class="read")], risk_class="publish"
    )
    assert eligible == ()
    assert rejected[0].reason == "risk:read<publish"


def test_cost_ceiling_fails_closed_before_paid_spill():
    paid = _candidate("remote/paid", cost_class="metered")
    free = _candidate("remote/free", cost_class="free")
    eligible, rejected = filter_candidates(
        [paid, free], max_cost_class="free"
    )
    assert [c.candidate_id for c in eligible] == ["remote/free"]
    assert rejected[0].candidate_id == "remote/paid"
    assert rejected[0].reason == "cost:metered>free"


def test_context_window_requirement_is_honoured():
    small = _candidate("local/small", capabilities=CapabilityProfile(context_window=4096))
    big = _candidate("local/big", capabilities=CapabilityProfile(context_window=131072))
    eligible, _ = filter_candidates(
        [small, big], required=CapabilityRequirement(min_context_window=32768)
    )
    assert [c.candidate_id for c in eligible] == ["local/big"]


def test_ranking_is_deterministic_and_prefers_local_free_before_paid():
    local = _candidate("local/a", quality_class="standard", cost_class="local")
    free = _candidate("remote/free", quality_class="standard", cost_class="free")
    paid = _candidate("remote/paid", quality_class="standard", cost_class="metered")
    first = rank_candidates([paid, free, local])
    second = rank_candidates([local, paid, free])
    assert [s.candidate_id for s in first] == [s.candidate_id for s in second]
    assert [s.candidate_id for s in first] == ["local/a", "remote/free", "remote/paid"]


# --- the local manifest projects into the same model --------------------


def test_local_manifest_projects_into_candidates():
    rows = {c.candidate_id: c for c in candidates_from_local_manifest(load_local_cognition())}
    assert "local/qwen3.5_4b" in rows
    assert rows["local/qwen3.5_4b"].source == "local_manifest"
    assert rows["local/qwen3.5_4b"].measured is True
    assert rows["local/qwen3.5_4b"].cost_class == "local"


def test_jev_scorer_and_slm_router_are_distinct_candidates():
    """z0intelligence#20: JEV/OpenJev is not the SLM router."""
    rows = {c.candidate_id: c for c in candidates_from_local_manifest(load_local_cognition())}
    nanojev = rows["local/nanojev_06b"]
    qwen = rows["local/qwen3.5_4b"]
    assert nanojev.serves("bounded_jev")
    assert not nanojev.serves("orchestrator_slm")
    assert qwen.serves("orchestrator_slm")
    assert nanojev.quality_class != qwen.quality_class


# --- the provider-neutral rung router -----------------------------------


def _legal(*actions, authority=("read", "write")):
    graph = ActionGraph(
        actions=tuple(
            ActionCandidate(
                action_id=a,
                kind="tool",
                tool=a,
                family="fs",
                description=a,
            )
            for a in actions
        )
    )
    return compile_actions(
        graph=graph, granted_capabilities=(), authority=authority, budget_units=8, facts={}
    )


class _FakeBackend:
    def __init__(self, backend_id, action, *, raises=None, abstain=False):
        self._id = backend_id
        self._action = action
        self._raises = raises
        self._abstain = abstain

    @property
    def backend_id(self):
        return self._id

    def health(self, *, load=False):
        return {"ready": True}

    def decide(self, request):
        if self._raises is not None:
            raise self._raises
        return ToolDecision(
            backend=self._id,
            model=self._id,
            revision="rev",
            selected_action=None if self._abstain else self._action,
            arguments={},
            confidence=0.9,
            distribution=None,
            latency_ms=4.0,
            abstained=self._abstain,
            candidate_action_count=request.legal.candidate_count,
        )


def _router_request(actions=("a", "b"), risk_class="read"):
    from z0int.cognition.adapters.local_slm import ToolDecisionRequest

    return ToolDecisionRequest(state="s", legal=_legal(*actions), risk_class=risk_class)


def test_candidate_rung_routes_to_the_best_eligible_and_counts_retries():
    candidates = candidates_from_pi_ai_catalog(CATALOG, hints=HINTS)
    router = CandidateRungBackend(
        tier="orchestrator_slm",
        candidates=candidates,
        backends={
            # The preferred (higher-quality) Cerebras row is down; the router must
            # fall through to Groq and record that attempt as a retry.
            "cerebras/gpt-oss-120b": _FakeBackend("cerebras", "a", raises=RuntimeError("down")),
            "groq/llama-3.3-70b-versatile": _FakeBackend("groq", "b"),
        },
    )
    decision = router.decide(_router_request())
    assert decision.selected_action == "b"
    assert decision.diagnostics["chosen_candidate_id"] == "groq/llama-3.3-70b-versatile"
    assert decision.diagnostics["retries"] == 1
    assert decision.diagnostics["eligible_candidate_ids"] == [
        "cerebras/gpt-oss-120b",
        "groq/llama-3.3-70b-versatile",
    ]


def test_candidate_rung_abstains_when_no_eligible_backend_is_configured():
    candidates = candidates_from_pi_ai_catalog(CATALOG, hints=HINTS)
    router = CandidateRungBackend(tier="orchestrator_slm", candidates=candidates, backends={})
    decision = router.decide(_router_request())
    assert decision.abstained is True
    assert decision.selected_action is None
    assert decision.diagnostics["reason"] == "no_eligible_candidate_backend"
    assert decision.diagnostics["eligible_candidate_ids"]


def test_candidate_rung_rejects_candidates_above_the_risk_ceiling():
    """Remote candidates are read-only unless annotated upward."""
    candidates = candidates_from_pi_ai_catalog(  # serves the rung, but no risk hint
        CATALOG,
        hints={
            "groq/llama-3.3-70b-versatile": {"serves_tiers": ["orchestrator_slm"]},
            "cerebras/gpt-oss-120b": {"serves_tiers": ["orchestrator_slm"]},
        },
    )
    router = CandidateRungBackend(
        tier="orchestrator_slm",
        candidates=candidates,
        backends={
            "groq/llama-3.3-70b-versatile": _FakeBackend("groq", "a"),
            "cerebras/gpt-oss-120b": _FakeBackend("cerebras", "b"),
        },
    )
    decision = router.decide(_router_request(risk_class="publish"))
    assert decision.abstained is True
    assert all("risk:" in row["reason"] for row in decision.diagnostics["rejected_candidates"])


def test_capability_requirement_is_derived_from_the_already_legal_set():
    legal = _legal("a", "b")
    required = requirement_for_legal_set(legal)
    assert required.tool_calling is True
    # The compiler ran first; the requirement can only narrow the candidate set.
    assert set(legal.ids()) == {"a", "b"}


# --- catalog loading ----------------------------------------------------


def test_load_pi_ai_catalog_reads_a_directory(tmp_path):
    (tmp_path / "groq.json").write_text(json.dumps(CATALOG), encoding="utf-8")
    payloads = load_pi_ai_catalog(tmp_path)
    assert len(payloads) == 1
    rows = candidates_from_pi_ai_catalog(payloads[0], hints=HINTS)
    assert "groq/llama-3.3-70b-versatile" in {c.candidate_id for c in rows}


def test_load_pi_ai_catalog_missing_path_is_not_an_error(tmp_path):
    assert load_pi_ai_catalog(tmp_path / "nope.json") == ()


def test_build_inventory_merges_local_and_provider_candidates():
    inventory = build_inventory(manifest=load_local_cognition(), catalog=(CATALOG,), hints=HINTS)
    providers = set(inventory.providers())
    assert {"local", "groq", "cerebras"} <= providers
    assert inventory.by_provider("groq")
    dumped = inventory.to_dict()
    assert dumped["schema"] == "z0int.cognition.candidates.v1"
    assert dumped["count"] == len(inventory)
