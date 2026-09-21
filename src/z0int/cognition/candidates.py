"""Shared capability model for execution candidates (z0intelligence#20).

One candidate type describes every execution target the decision surface may
consider: a locally served SLM, a JEV/OpenJev bounded scorer, and a remote
free-tier provider such as Groq or Cerebras.

Provider identity is *data*, never a branch. Nothing in this module (or in
:mod:`z0int.cognition.surface`) contains ``if provider == ...``; a provider
enters the candidate set only by having catalog rows flattened into
:class:`CandidateModel` records. That is what makes Groq and Cerebras ordinary
candidates instead of hard-coded special cases: they are two more rows of the
same capability model, and a third provider needs no code change at all.

Ownership boundary (do not cross it here):

* the *catalog* (which models exist at a provider, price, availability) is owned
  by DSH's ``llm-pi-ai``; :func:`candidates_from_pi_ai_catalog` is a read-only
  projection of that catalog, not a second registry;
* live capacity, RPM/RPD/TPM/TPD, reset timers and placement are owned by
  Kerdoios; this module never accounts for any of it;
* what this module owns is the semantic projection: what a candidate can do,
  how trustworthy it must be for a risk class, and which rung of the ladder it
  is allowed to serve.

The module is pure stdlib, side-effect free and replayable. Candidate rows carry
no API keys and no credentials of any kind.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence
import json
import os

from .actions import RISK_CLASSES, risk_rank
from .escalation import TIERS
from .manifest import LocalCognitionManifest, ModelCapability

# --- quality / risk classes -------------------------------------------------

#: Ordered capability/trust classes. A request states the class it *requires*; a
#: candidate states the class it *offers*. Selection is a comparison, never a
#: provider name lookup.
QUALITY_CLASSES: tuple[str, ...] = ("tiny", "bounded", "standard", "strong", "frontier")
_QUALITY_RANK = {name: rank for rank, name in enumerate(QUALITY_CLASSES)}

#: Cheapest rung that can satisfy each quality class. Used to derive the
#: escalation floor, so a "frontier" requirement cannot be met by a bounded
#: scorer no matter how confident it claims to be.
QUALITY_TIER_FLOOR: Mapping[str, str] = {
    "tiny": "tiny_specialist",
    "bounded": "bounded_jev",
    "standard": "orchestrator_slm",
    "strong": "general_slm",
    "frontier": "remote_frontier",
}

#: Declarative role -> ladder mapping for local manifest models. Keyed by role
#: (what the model is for), never by provider or model name.
ROLE_TIERS: Mapping[str, tuple[str, ...]] = {
    "tiny_action_specialist": ("tiny_specialist",),
    "bounded_scorer": ("bounded_jev",),
    "semantic_orchestrator": ("orchestrator_slm",),
    "general_function_caller": ("orchestrator_slm", "general_slm"),
    "general_local_fallback": ("general_slm",),
    "remote_specialist_fallback": ("remote_frontier",),
}

ROLE_QUALITY: Mapping[str, str] = {
    "tiny_action_specialist": "tiny",
    "bounded_scorer": "bounded",
    "semantic_orchestrator": "standard",
    "general_function_caller": "standard",
    "general_local_fallback": "strong",
    "remote_specialist_fallback": "frontier",
}

#: Highest risk class each local role is trusted with. The tiny specialist and
#: the bounded scorer never see consequential decisions; the orchestrator and the
#: general fallback are the tiers the escalation policy itself sends high-risk
#: work to, so they carry the full ceiling. Remote candidates default to ``read``
#: in :func:`candidates_from_pi_ai_catalog` and must be annotated upward.
ROLE_MAX_RISK: Mapping[str, str] = {
    "tiny_action_specialist": "read",
    "bounded_scorer": "read",
    "general_function_caller": "write",
    "semantic_orchestrator": "publish",
    "general_local_fallback": "publish",
    "remote_specialist_fallback": "read",
}


def quality_rank(quality_class: str) -> int:
    try:
        return _QUALITY_RANK[quality_class]
    except KeyError as exc:
        raise ValueError(f"unsupported quality class: {quality_class!r}") from exc


def quality_at_least(quality_class: str, floor: str) -> str:
    """Return whichever quality class is the more demanding of the two."""
    return quality_class if quality_rank(quality_class) >= quality_rank(floor) else floor


def max_quality(*classes: str) -> str:
    ranked = sorted(classes, key=quality_rank)
    return ranked[-1]


def max_risk(*classes: str) -> str:
    ranked = sorted(classes, key=risk_rank)
    return ranked[-1]


# --- capability model -------------------------------------------------------


@dataclass(frozen=True)
class CapabilityProfile:
    """What one execution candidate can actually do.

    Only capabilities the *controller* needs to reason about live here. Runtime
    concerns (leases, queues, retries, permissions, quota) are deterministic
    runtime mechanisms and deliberately absent.
    """

    tool_calling: bool = False
    parallel_tool_calls: bool = False
    structured_output: bool = False
    reasoning: bool = False
    multi_turn: bool = False
    modalities: tuple[str, ...] = ("text",)
    context_window: int = 0
    supports_confidence: bool = False
    supports_abstention: bool = False

    def unmet(self, required: "CapabilityRequirement") -> tuple[str, ...]:
        """Return the list of requirements this profile does not satisfy."""
        missing: list[str] = []
        if required.tool_calling and not self.tool_calling:
            missing.append("tool_calling")
        if required.parallel_tool_calls and not self.parallel_tool_calls:
            missing.append("parallel_tool_calls")
        if required.structured_output and not self.structured_output:
            missing.append("structured_output")
        if required.reasoning and not self.reasoning:
            missing.append("reasoning")
        if required.multi_turn and not self.multi_turn:
            missing.append("multi_turn")
        if required.min_context_window and self.context_window < required.min_context_window:
            missing.append(f"context_window>={required.min_context_window}")
        for modality in required.modalities:
            if modality not in self.modalities:
                missing.append(f"modality:{modality}")
        return tuple(missing)

    def satisfies(self, required: "CapabilityRequirement") -> bool:
        return not self.unmet(required)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_calling": self.tool_calling,
            "parallel_tool_calls": self.parallel_tool_calls,
            "structured_output": self.structured_output,
            "reasoning": self.reasoning,
            "multi_turn": self.multi_turn,
            "modalities": list(self.modalities),
            "context_window": self.context_window,
            "supports_confidence": self.supports_confidence,
            "supports_abstention": self.supports_abstention,
        }


@dataclass(frozen=True)
class CapabilityRequirement:
    """The capabilities a request needs a candidate to have."""

    tool_calling: bool = False
    parallel_tool_calls: bool = False
    structured_output: bool = False
    reasoning: bool = False
    multi_turn: bool = False
    modalities: tuple[str, ...] = ("text",)
    min_context_window: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_calling": self.tool_calling,
            "parallel_tool_calls": self.parallel_tool_calls,
            "structured_output": self.structured_output,
            "reasoning": self.reasoning,
            "multi_turn": self.multi_turn,
            "modalities": list(self.modalities),
            "min_context_window": self.min_context_window,
        }


@dataclass(frozen=True)
class CandidateModel:
    """One thing the ladder could execute on, described by capability only."""

    candidate_id: str
    provider: str
    model_id: str
    capabilities: CapabilityProfile = field(default_factory=CapabilityProfile)
    quality_class: str = "standard"
    max_risk_class: str = "read"
    cost_class: str = "unknown"
    serves_tiers: tuple[str, ...] = ()
    role_tags: tuple[str, ...] = ()
    measured: bool = False
    source: str = "unknown"
    evidence_refs: tuple[str, ...] = ()
    annotations: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.candidate_id.strip():
            raise ValueError("candidate_id must be nonempty")
        if not self.provider.strip():
            raise ValueError(f"{self.candidate_id}: provider must be nonempty")
        quality_rank(self.quality_class)  # validates
        risk_rank(self.max_risk_class)  # validates
        unknown_tiers = [t for t in self.serves_tiers if t not in TIERS]
        if unknown_tiers:
            raise ValueError(f"{self.candidate_id}: unknown tiers {unknown_tiers}")

    @property
    def identity(self) -> str:
        """Stable ``provider/model`` identity used for receipts and hints."""
        return f"{self.provider}/{self.model_id}"

    def serves(self, tier: str) -> bool:
        return tier in self.serves_tiers

    def admits_risk(self, risk_class: str) -> bool:
        """True when this candidate is trusted at ``risk_class``."""
        return risk_rank(risk_class) <= risk_rank(self.max_risk_class)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "candidate_id": self.candidate_id,
            "provider": self.provider,
            "model_id": self.model_id,
            "quality_class": self.quality_class,
            "max_risk_class": self.max_risk_class,
            "cost_class": self.cost_class,
            "serves_tiers": list(self.serves_tiers),
            "capabilities": self.capabilities.to_dict(),
        }
        if self.role_tags:
            out["role_tags"] = list(self.role_tags)
        if self.measured:
            out["measured"] = True
        if self.source != "unknown":
            out["source"] = self.source
        if self.evidence_refs:
            out["evidence_refs"] = list(self.evidence_refs)
        return out


# --- hard filters (shared by the surface and the rung routers) --------------


@dataclass(frozen=True)
class Rejection:
    candidate_id: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"candidate_id": self.candidate_id, "reason": self.reason}


@dataclass(frozen=True)
class Suitability:
    candidate_id: str
    score: float
    quality_class: str
    serves_tiers: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "score": self.score,
            "quality_class": self.quality_class,
            "serves_tiers": list(self.serves_tiers),
        }


def filter_candidates(
    candidates: Iterable[CandidateModel],
    *,
    tier: str | None = None,
    required: CapabilityRequirement | None = None,
    quality_class_required: str = "bounded",
    risk_class: str = "read",
    max_cost_class: str | None = None,
    cost_class_order: Sequence[str] = ("local", "free", "unknown", "metered"),
) -> tuple[tuple[CandidateModel, ...], tuple[Rejection, ...]]:
    """Apply the hard, deterministic gates. Returns ``(eligible, rejected)``.

    Order matters and is fixed so a replay reproduces it exactly: tier, then
    capability, then quality, then risk, then cost. Every rejection carries the
    gate that produced it. A candidate admitted here is still only *allowed*;
    quota/capacity placement happens later and elsewhere (Kerdoios).
    """
    req = required or CapabilityRequirement()
    eligible: list[CandidateModel] = []
    rejected: list[Rejection] = []
    rank_limit = None
    if max_cost_class is not None:
        try:
            rank_limit = tuple(cost_class_order).index(max_cost_class)
        except ValueError as exc:
            raise ValueError(f"unknown max_cost_class {max_cost_class!r}") from exc
    for candidate in candidates:
        if tier is not None and not candidate.serves(tier):
            rejected.append(Rejection(candidate.candidate_id, f"tier_not_served:{tier}"))
            continue
        missing = candidate.capabilities.unmet(req)
        if missing:
            rejected.append(
                Rejection(candidate.candidate_id, "capability:" + ",".join(missing))
            )
            continue
        if quality_rank(candidate.quality_class) < quality_rank(quality_class_required):
            rejected.append(
                Rejection(
                    candidate.candidate_id,
                    f"quality:{candidate.quality_class}<{quality_class_required}",
                )
            )
            continue
        if not candidate.admits_risk(risk_class):
            rejected.append(
                Rejection(
                    candidate.candidate_id,
                    f"risk:{candidate.max_risk_class}<{risk_class}",
                )
            )
            continue
        if rank_limit is not None:
            try:
                candidate_rank = tuple(cost_class_order).index(candidate.cost_class)
            except ValueError:
                rejected.append(
                    Rejection(candidate.candidate_id, f"cost_class_unknown:{candidate.cost_class}")
                )
                continue
            if candidate_rank > rank_limit:
                rejected.append(
                    Rejection(
                        candidate.candidate_id,
                        f"cost:{candidate.cost_class}>{max_cost_class}",
                    )
                )
                continue
        eligible.append(candidate)
    return tuple(eligible), tuple(rejected)


def rank_candidates(
    eligible: Iterable[CandidateModel],
    *,
    required: CapabilityRequirement | None = None,
    latency_budget_ms: float | None = None,
    estimated_latency_ms: Mapping[str, float] | None = None,
    cost_class_order: Sequence[str] = ("local", "free", "unknown", "metered"),
) -> tuple[Suitability, ...]:
    """Order already-eligible candidates by *semantic suitability*.

    The score is deterministic and inspectable: quality headroom first, then the
    cheapness of the execution class, then observed latency headroom. It never
    consults a provider name, a vendor benchmark, or a quota counter.
    """
    req = required or CapabilityRequirement()
    latency = dict(estimated_latency_ms or {})
    scored: list[tuple[float, CandidateModel]] = []
    for candidate in eligible:
        headroom = quality_rank(candidate.quality_class) - quality_rank("bounded")
        score = 1.0 + 0.1 * headroom
        try:
            cost_rank = tuple(cost_class_order).index(candidate.cost_class)
        except ValueError:
            cost_rank = len(cost_class_order)
        score -= 0.02 * cost_rank
        if candidate.capabilities.context_window:
            need = max(1, req.min_context_window)
            score += min(0.05, candidate.capabilities.context_window / need / 200.0)
        estimate = latency.get(candidate.candidate_id)
        if estimate is not None and latency_budget_ms:
            remaining = float(latency_budget_ms) - float(estimate)
            if remaining <= 0:
                score -= 0.5
            else:
                score += min(0.05, remaining / float(latency_budget_ms) / 20.0)
        scored.append((score, candidate))
    scored.sort(key=lambda pair: (-pair[0], pair[1].candidate_id))
    return tuple(
        Suitability(
            candidate_id=candidate.candidate_id,
            score=round(score, 6),
            quality_class=candidate.quality_class,
            serves_tiers=candidate.serves_tiers,
        )
        for score, candidate in scored
    )


# --- sources ----------------------------------------------------------------

#: Source label for rows projected out of DSH's pi-ai catalog.
PI_AI_SOURCE = "pi_ai_catalog"
#: Source label for rows projected out of the local capability manifest.
LOCAL_MANIFEST_SOURCE = "local_manifest"

_DEFAULT_CATALOG_TIERS: tuple[str, ...] = ("remote_frontier",)


def candidates_from_local_manifest(
    manifest: LocalCognitionManifest,
) -> tuple[CandidateModel, ...]:
    """Project local manifest models into the shared capability model.

    The manifest stays the canonical *local evidence* record; this is a read-only
    view so the surface can compare a local SLM and a remote provider with one
    comparator. A model holding several roles is merged into one candidate that
    serves the union of those rungs.
    """
    merged: dict[str, dict[str, Any]] = {}
    for model in manifest.models.values():
        if model.promotion_state in ("rejected", "unavailable"):
            continue
        roles = tuple(model.role_tags)
        if not roles:
            continue
        tiers: list[str] = []
        qualities: list[str] = []
        risks: list[str] = []
        for role in roles:
            for tier in ROLE_TIERS.get(role, ()):
                if tier not in tiers:
                    tiers.append(tier)
            qualities.append(ROLE_QUALITY.get(role, "standard"))
            risks.append(ROLE_MAX_RISK.get(role, "read"))
        entry = merged.setdefault(
            model.model_id,
            {
                "provider": "local",
                "model_id": model.model_id,
                "capabilities": _capabilities_from_manifest(model),
                "quality_class": max_quality(*qualities),
                "max_risk_class": max_risk(*risks),
                "cost_class": "local",
                "serves_tiers": (),
                "role_tags": (),
                "measured": model.measured,
                "evidence_refs": list(model.local_benchmark_receipt_ids)
                + [m.receipt_id for m in model.measurements if m.receipt_id],
                "annotations": {"hf": model.hf, "license": model.license},
            },
        )
        entry["serves_tiers"] = tuple(
            dict.fromkeys(tuple(entry["serves_tiers"]) + tuple(tiers))
        )
        entry["role_tags"] = tuple(dict.fromkeys(tuple(entry["role_tags"]) + roles))
    return tuple(
        CandidateModel(
            candidate_id=f"local/{model_id}",
            source=LOCAL_MANIFEST_SOURCE,
            **payload,
        )
        for model_id, payload in sorted(merged.items())
    )


def _capabilities_from_manifest(model: ModelCapability) -> CapabilityProfile:
    caps = dict(model.capabilities or {})
    return CapabilityProfile(
        tool_calling=bool(caps.get("single_tool_call") or caps.get("parallel_tool_calls")),
        parallel_tool_calls=bool(caps.get("parallel_tool_calls")),
        structured_output=bool(model.structured_output_support),
        reasoning=bool(caps.get("multi_step_calls") or caps.get("model_as_tool_orchestration")),
        multi_turn=bool(caps.get("multi_turn_calls")),
        modalities=("text",),
        context_window=int(model.tested_context or 0),
        supports_confidence=bool(model.supports_confidence),
        supports_abstention=bool(model.supports_abstention),
    )


def _iter_pi_ai_entries(raw: Any, *, depth: int = 0) -> Iterator[Mapping[str, Any]]:
    """Flatten any pi-ai catalog nesting into model entries.

    pi-ai ships ``{"<api>": {"<model>": {...}}}`` per provider and composite
    files may nest by provider first. An entry is any mapping that carries both
    an ``id`` and a ``provider`` (or ``api``) — so the flattener is shape-driven
    and needs no provider list.
    """
    if depth > 4 or not isinstance(raw, Mapping):
        return
    if "id" in raw and ("provider" in raw or "api" in raw):
        yield raw
        return
    for value in raw.values():
        if isinstance(value, Mapping):
            yield from _iter_pi_ai_entries(value, depth=depth + 1)


def _as_str_tuple(value: Any) -> tuple[str, ...]:
    """Coerce a hint value into a tuple of strings, never into characters."""
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(str(item) for item in value)
    raise ValueError(f"expected a string or list of strings, got {type(value).__name__}")


def _hint_for(
    entry: Mapping[str, Any],
    hints: Mapping[str, Mapping[str, Any]] | None,
    *keys: str,
) -> Any:
    """Read an override from inline ``z0int`` annotations, then from ``hints``."""
    inline = entry.get("z0int")
    identity = f"{entry.get('provider')}/{entry.get('id')}"
    table = hints or {}
    for key in keys:
        if isinstance(inline, Mapping) and key in inline:
            return inline[key]
        scoped = table.get(identity) or table.get(str(entry.get("id")))
        if isinstance(scoped, Mapping) and key in scoped:
            return scoped[key]
    return None


def candidates_from_pi_ai_catalog(
    raw: Mapping[str, Any],
    *,
    hints: Mapping[str, Mapping[str, Any]] | None = None,
    serves_tiers: Sequence[str] = _DEFAULT_CATALOG_TIERS,
    default_quality_class: str = "standard",
    default_risk_class: str = "read",
    default_cost_class: str = "unknown",
) -> tuple[CandidateModel, ...]:
    """Project a pi-ai model catalog into the shared capability model.

    Groq and Cerebras are not mentioned anywhere in this function. They appear
    because their catalog rows are flattened like every other provider's. Per-row
    ``z0int`` annotations (or the caller's ``hints``) may raise a row's quality
    class, risk ceiling, cost class or served tiers; nothing is inferred from the
    provider's name.
    """
    out: list[CandidateModel] = []
    seen: set[str] = set()
    for entry in _iter_pi_ai_entries(raw):
        provider = str(entry.get("provider") or "").strip()
        model_id = str(entry.get("id") or "").strip()
        if not provider or not model_id:
            continue
        candidate_id = f"{provider}/{model_id}"
        if candidate_id in seen:
            continue
        seen.add(candidate_id)
        api = str(entry.get("api") or "")
        modalities = tuple(str(x) for x in (entry.get("input") or ("text",)))
        tool_calling = _hint_for(entry, hints, "tool_calling")
        if tool_calling is None:
            # Declarative, provider-neutral: any model served over the OpenAI
            # chat-completions API can be handed tool definitions.
            tool_calling = api == "openai-completions"
        structured_output = _hint_for(entry, hints, "structured_output")
        if structured_output is None:
            structured_output = bool(
                isinstance(entry.get("compat"), Mapping)
                and entry["compat"].get("supportsStructuredOutputs")
            )
        multi_turn = _hint_for(entry, hints, "multi_turn")
        if multi_turn is None:
            multi_turn = bool(tool_calling)
        quality = _hint_for(entry, hints, "quality_class") or default_quality_class
        risk = _hint_for(entry, hints, "max_risk_class") or default_risk_class
        cost = _hint_for(entry, hints, "cost_class") or default_cost_class
        tier_hint = _hint_for(entry, hints, "serves_tiers")
        tiers = _as_str_tuple(tier_hint) if tier_hint else tuple(serves_tiers)
        roles = _as_str_tuple(_hint_for(entry, hints, "role_tags"))
        capabilities = CapabilityProfile(
            tool_calling=bool(tool_calling),
            parallel_tool_calls=bool(_hint_for(entry, hints, "parallel_tool_calls") or False),
            structured_output=bool(structured_output),
            reasoning=bool(entry.get("reasoning", False)),
            multi_turn=bool(multi_turn),
            modalities=modalities or ("text",),
            context_window=int(entry.get("contextWindow") or 0),
            supports_confidence=bool(_hint_for(entry, hints, "supports_confidence") or False),
            supports_abstention=bool(_hint_for(entry, hints, "supports_abstention") or False),
        )
        out.append(
            CandidateModel(
                candidate_id=candidate_id,
                provider=provider,
                model_id=model_id,
                capabilities=capabilities,
                quality_class=str(quality),
                max_risk_class=str(risk),
                cost_class=str(cost),
                serves_tiers=tiers,
                role_tags=roles,
                measured=False,
                source=PI_AI_SOURCE,
                annotations={"base_url": entry.get("baseUrl"), "api": api},
            )
        )
    out.sort(key=lambda c: c.candidate_id)
    return tuple(out)


def load_pi_ai_catalog(path: str | Path | None = None) -> tuple[Mapping[str, Any], ...]:
    """Read pi-ai catalog JSON from disk. Never reads credentials or the network.

    ``path`` may be a file or a directory of ``*.json`` provider files. When
    omitted, ``Z0INT_PI_AI_CATALOG`` is consulted (``os.pathsep``-separated files
    or directories). A missing catalog is not an error: it means only local
    candidates exist.
    """
    roots: list[Path] = []
    if path is not None:
        roots.append(Path(path))
    else:
        raw_env = os.environ.get("Z0INT_PI_AI_CATALOG", "")
        roots.extend(Path(p) for p in raw_env.split(os.pathsep) if p)
    payloads: list[Mapping[str, Any]] = []
    for root in roots:
        try:
            if root.is_dir():
                files = sorted(root.glob("*.json"))
            elif root.is_file():
                files = [root]
            else:
                continue
            for file in files:
                loaded = json.loads(file.read_text(encoding="utf-8"))
                if isinstance(loaded, Mapping):
                    payloads.append(loaded)
        except (OSError, ValueError):
            # A malformed or unreadable catalog degrades to "no remote
            # candidates"; it must never break a local decision.
            continue
    return tuple(payloads)


def build_inventory(
    *,
    manifest: LocalCognitionManifest | None = None,
    catalog: Iterable[Mapping[str, Any]] = (),
    hints: Mapping[str, Mapping[str, Any]] | None = None,
    catalog_tiers: Sequence[str] = _DEFAULT_CATALOG_TIERS,
) -> "CandidateInventory":
    """Merge every candidate source into one deduplicated capability inventory."""
    rows: list[CandidateModel] = []
    if manifest is not None:
        rows.extend(candidates_from_local_manifest(manifest))
    for payload in catalog:
        rows.extend(
            candidates_from_pi_ai_catalog(
                payload, hints=hints, serves_tiers=catalog_tiers
            )
        )
    return CandidateInventory(tuple(rows))


@dataclass(frozen=True)
class CandidateInventory:
    """A read-only view over candidates from every source. Not a registry."""

    candidates: tuple[CandidateModel, ...] = ()

    def __len__(self) -> int:
        return len(self.candidates)

    def __iter__(self) -> Iterator[CandidateModel]:
        return iter(self.candidates)

    def get(self, candidate_id: str) -> CandidateModel:
        for candidate in self.candidates:
            if candidate.candidate_id == candidate_id:
                return candidate
        raise KeyError(candidate_id)

    def by_tier(self, tier: str) -> tuple[CandidateModel, ...]:
        return tuple(c for c in self.candidates if c.serves(tier))

    def by_provider(self, provider: str) -> tuple[CandidateModel, ...]:
        return tuple(c for c in self.candidates if c.provider == provider)

    def providers(self) -> tuple[str, ...]:
        return tuple(sorted({c.provider for c in self.candidates}))

    def merge(self, other: "CandidateInventory") -> "CandidateInventory":
        return CandidateInventory(self.candidates + other.candidates)

    def to_dict(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for candidate in self.candidates:
            counts[candidate.source] = counts.get(candidate.source, 0) + 1
        return {
            "schema": "z0int.cognition.candidates.v1",
            "count": len(self.candidates),
            "providers": list(self.providers()),
            "sources": counts,
            "candidates": [c.to_dict() for c in self.candidates],
        }


# --- the provider-neutral rung router ---------------------------------------


class CandidateRungBackend:
    """Execute one ladder rung on the best semantically-suitable candidate.

    This is the "local / remote SLM router" rung: it holds *both* a local SLM and
    remote free-tier candidates (Groq, Cerebras, ...) and picks among them with
    :func:`filter_candidates` + :func:`rank_candidates`. It is deliberately not
    a quota allocator — it hands back the ordered *allowed* set in its decision
    diagnostics and lets Kerdoios place the work.

    It satisfies :class:`~z0int.cognition.adapters.local_slm.ToolDecisionBackend`,
    so the cascade treats it like any other tier. If the first candidate fails or
    abstains, the next eligible one is tried; each attempt is counted as a retry
    and reported, but nothing is ever guessed.
    """

    def __init__(
        self,
        *,
        tier: str,
        candidates: Sequence[CandidateModel],
        backends: Mapping[str, Any],
        backend_id: str | None = None,
        quality_class_required: str = "bounded",
        max_cost_class: str | None = None,
        required: CapabilityRequirement | None = None,
    ) -> None:
        if tier not in TIERS:
            raise ValueError(f"unknown tier {tier!r}")
        self._tier = tier
        self._candidates = tuple(candidates)
        self._backends = dict(backends)
        self._backend_id = backend_id or f"candidates:{tier}"
        self._quality_class_required = quality_class_required
        self._max_cost_class = max_cost_class
        self._required = required

    @property
    def backend_id(self) -> str:
        return self._backend_id

    @property
    def tier(self) -> str:
        return self._tier

    def health(self, *, load: bool = False) -> dict[str, Any]:
        ready = [cid for cid in self._backends if cid in {c.candidate_id for c in self._candidates}]
        return {
            "ready": bool(ready),
            "backend": self._backend_id,
            "tier": self._tier,
            "candidates": list(ready),
        }

    def allowed_for(self, request: "ToolDecisionRequest") -> tuple[CandidateModel, ...]:
        """The eligible candidate set for this request, best-first."""
        required = self._required or requirement_for_legal_set(request.legal)
        eligible, _ = filter_candidates(
            self._candidates,
            tier=self._tier,
            required=required,
            quality_class_required=self._quality_class_required,
            risk_class=request.risk_class or "read",
            max_cost_class=self._max_cost_class,
        )
        ordered = rank_candidates(eligible, required=required)
        order = {s.candidate_id: i for i, s in enumerate(ordered)}
        return tuple(sorted(eligible, key=lambda c: order[c.candidate_id]))

    def decide(self, request: "ToolDecisionRequest") -> "ToolDecision":
        from .adapters.local_slm import ToolDecision, abstain  # avoid an import cycle

        required = self._required or requirement_for_legal_set(request.legal)
        eligible, rejected = filter_candidates(
            self._candidates,
            tier=self._tier,
            required=required,
            quality_class_required=self._quality_class_required,
            risk_class=request.risk_class or "read",
            max_cost_class=self._max_cost_class,
        )
        ranked = rank_candidates(eligible, required=required)
        order = {item.candidate_id: i for i, item in enumerate(ranked)}
        ordered_candidates = tuple(
            sorted(eligible, key=lambda c: order[c.candidate_id])
        )
        ordered = [item.candidate_id for item in ranked]
        diagnostics: dict[str, Any] = {
            "tier": self._tier,
            "eligible_candidate_ids": ordered,
            "eligible_candidates": [c.to_dict() for c in ordered_candidates],
            "rejected_candidates": [r.to_dict() for r in rejected],
            "allowed_candidates": ordered,
            "quality_class_required": self._quality_class_required,
            "risk_class": request.risk_class or "read",
        }
        resolved = [cid for cid in ordered if cid in self._backends]
        if not resolved:
            return abstain(
                backend=self._backend_id,
                model=None,
                revision=None,
                candidate_count=request.legal.candidate_count,
                latency_ms=0.0,
                reason="no_eligible_candidate_backend",
                extra=diagnostics,
            )
        retries = 0
        last: ToolDecision | None = None
        attempts: list[dict[str, Any]] = []
        by_id = {c.candidate_id: c for c in eligible}
        for candidate_id in resolved:
            backend = self._backends[candidate_id]
            try:
                decision = backend.decide(request)
            except Exception as exc:  # noqa: BLE001 - try the next candidate
                attempts.append({"candidate_id": candidate_id, "error": f"{type(exc).__name__}: {exc}"})
                retries += 1
                continue
            attempts.append(
                {
                    "candidate_id": candidate_id,
                    "selected_action": decision.selected_action,
                    "abstained": decision.abstained,
                    "latency_ms": decision.latency_ms,
                }
            )
            if not decision.abstained and decision.selected_action is not None:
                merged = dict(decision.diagnostics or {})
                merged.update(diagnostics)
                merged["chosen_candidate_id"] = candidate_id
                merged["candidate_attempts"] = attempts
                merged["retries"] = retries
                merged["cost_class"] = by_id[candidate_id].cost_class
                return ToolDecision(
                    backend=self._backend_id,
                    model=decision.model,
                    revision=decision.revision,
                    selected_action=decision.selected_action,
                    arguments=decision.arguments,
                    confidence=decision.confidence,
                    distribution=decision.distribution,
                    latency_ms=decision.latency_ms,
                    abstained=False,
                    ttft_ms=decision.ttft_ms,
                    decode_tok_s=decision.decode_tok_s,
                    prompt_tokens=decision.prompt_tokens,
                    completion_tokens=decision.completion_tokens,
                    cached_tokens=decision.cached_tokens,
                    candidate_action_count=decision.candidate_action_count,
                    invalid_call=decision.invalid_call,
                    irrelevant_call=decision.irrelevant_call,
                    unnecessary_call=decision.unnecessary_call,
                    parse_error=decision.parse_error,
                    reasoning_metadata=decision.reasoning_metadata,
                    diagnostics=merged,
                )
            retries += 1
            last = decision
        return abstain(
            backend=self._backend_id,
            model=None,
            revision=None,
            candidate_count=request.legal.candidate_count,
            latency_ms=last.latency_ms if last is not None else 0.0,
            reason="all_candidates_abstained",
            extra={**diagnostics, "candidate_attempts": attempts, "retries": retries},
        )


def requirement_for_legal_set(legal: Any) -> CapabilityRequirement:
    """Derive the capability requirement from the already-legal action set.

    Only the *compiled* set is inspected, so a capability requirement can never
    re-admit an action the compiler removed; it only narrows which candidates are
    allowed to choose among the survivors.
    """
    required_tools = False
    reasons = False
    multi_turn = False
    for action in getattr(legal, "legal", ()):  # LegalActionSet.legal
        if action.kind == "tool":
            required_tools = True
        caps = tuple(getattr(action, "required_capabilities", ()) or ())
        if "reasoning" in caps:
            reasons = True
        if "multi_turn" in caps or "multi_step" in caps:
            multi_turn = True
    min_context = 0
    for action in getattr(legal, "legal", ()):
        schema = getattr(action, "arguments_schema", None) or {}
        if not isinstance(schema, Mapping):
            continue
        try:
            min_context = max(min_context, int(schema.get("x-z0int-min-context", 0) or 0))
        except (TypeError, ValueError):
            continue
    return CapabilityRequirement(
        tool_calling=required_tools,
        reasoning=reasons,
        multi_turn=multi_turn,
        min_context_window=min_context,
    )


def inventory_from_environment(
    *, manifest: LocalCognitionManifest | None = None, catalog_path: str | Path | None = None
) -> CandidateInventory:
    """Build the inventory from the local manifest plus the pi-ai catalog on disk."""
    if manifest is None:
        from .manifest import load_local_cognition

        try:
            manifest = load_local_cognition()
        except (OSError, ValueError):
            manifest = None
    return build_inventory(manifest=manifest, catalog=load_pi_ai_catalog(catalog_path))


__all__ = [
    "CapabilityProfile",
    "CapabilityRequirement",
    "CandidateInventory",
    "CandidateModel",
    "CandidateRungBackend",
    "LOCAL_MANIFEST_SOURCE",
    "PI_AI_SOURCE",
    "QUALITY_CLASSES",
    "QUALITY_TIER_FLOOR",
    "ROLE_MAX_RISK",
    "ROLE_QUALITY",
    "ROLE_TIERS",
    "Rejection",
    "Suitability",
    "build_inventory",
    "candidates_from_local_manifest",
    "candidates_from_pi_ai_catalog",
    "filter_candidates",
    "inventory_from_environment",
    "load_pi_ai_catalog",
    "max_quality",
    "max_risk",
    "quality_at_least",
    "quality_rank",
    "rank_candidates",
    "requirement_for_legal_set",
]
