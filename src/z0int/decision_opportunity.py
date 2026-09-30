"""DecisionOpportunity v0 — the per-decision unit of the verified learning loop (z0int#53, z0#15).

Wraps a State Packet (evidence -> state) and adds what the packet does not carry:

  * the user's intent and its revision,
  * deterministic authority from AODL (never from model confidence),
  * a question-scoped legal action space: ACT / OBSERVE / ASK / ABSTAIN / ESCALATE,
  * a harness-independent semantic id, so the same evidence + intent is the same
    opportunity whether Claude Code, Hermes or OMP observed it.

Kept distinct on purpose:  evidence != belief/state != authority != action != outcome.
The packet stays the evidence/state owner; this module never re-derives claims.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

SCHEMA = "z0int.decision_opportunity.v0"
ACTIONS = ("ACT", "OBSERVE", "ASK", "ABSTAIN", "ESCALATE")
EFFECTS = ("read", "write", "privileged")
# Without an authored AODL contract the only standing authority is read-only.
DEFAULT_AUTHORITY = ("read",)


def _sha(obj: Any, n: int = 16) -> str:
    raw = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:n]


def _standing(harness_grants: Iterable[str] | None) -> dict[str, Any]:
    """A harness's standing authority, authored by the user's session choice (e.g. Claude Code outside plan
    mode may edit the working copy). Privileged authority is never standing: only AODL or a user answer."""
    if harness_grants is None:
        return {"source": "harness-default", "grants": list(DEFAULT_AUTHORITY), "fingerprint": None}
    grants = set(harness_grants) | set(DEFAULT_AUTHORITY)
    bad = sorted(g for g in grants if g not in EFFECTS or g == "privileged")
    if bad:
        raise ValueError(f"harness standing authority cannot include {bad}; privileged comes only from AODL or a user grant")
    grants = sorted(grants, key=EFFECTS.index)
    return {"source": "harness-standing", "grants": grants, "fingerprint": None}


def _authority(aodl_doc: Mapping[str, Any] | None, harness_grants: Iterable[str] | None = None) -> dict[str, Any]:
    """Authority is authored, versioned and fingerprinted; it is never inferred."""
    if not aodl_doc:
        return _standing(harness_grants)
    grants: set[str] = set()
    for graph in ("intentGraph",):
        for node in ((aodl_doc.get(graph) or {}).get("nodes") or []):
            grants.update(str(c) for c in (node.get("authorityCeiling") or []) if isinstance(c, str))
    fingerprint = None
    try:  # aodl-canon-1 (kvnloo/aodl#21) names exactly which intent revision authority came from
        from aodl_contract import semantic_fingerprint  # type: ignore

        fingerprint = semantic_fingerprint(aodl_doc)
    except Exception as exc:
        # An authored contract that cannot be validated + fingerprinted grants nothing: fail closed.
        return {"source": "harness-default", "grants": list(DEFAULT_AUTHORITY), "fingerprint": None,
                "rejected_aodl": type(exc).__name__}
    return {"source": "aodl", "grants": sorted(grants) or list(DEFAULT_AUTHORITY), "fingerprint": fingerprint}


# Request vocabulary -> packet fact families. Authored from the packet key namespace only
# (state_packet._RENDER_ORDER, conv.*, docs.*, gh.*), never from evaluation answer keys.
FACT_FAMILIES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "git.branch": (("git.branch",), ("branch", "checked out", "checkout")),
    "git.head": (("git.head",), ("head", "last commit", "latest commit", "current commit")),
    "git.dirty": (("git.dirty",), ("dirty", "uncommitted", "modified", "clean", "working tree", "unstaged")),
    "git.upstream": (("git.upstream",), ("upstream", "tracking", "tracks", "remote branch")),
    "git.branches_ahead": (("git.branches_ahead", "git.default_branch"), ("ahead", "unmerged", "unpushed", "furthest", "behind")),
    "git.worktrees": (("git.worktrees",), ("worktree",)),
    "git.stash": (("git.stash_count",), ("stash",)),
    "git.history": (("git.recent_commits",), ("recent commits", "history", "git log", "commit log")),
    "git.remote": (("git.remote_refs_age_hours", "git.remote_state"), ("fetch", "stale remote", "remote refs")),
    "conv": (("conv.",), ("session", "conversation", "transcript", "claude code", "edited", "worked on", "agent")),
    "docs.priority": (("docs.priority", "gh.priority"), ("priority", "p0", "roadmap", "critical path", "what next", "do next")),
    "gh": (("gh.",), ("pull request", " pr ", "pr #", "ci ", "ci status", "checks", "github issue", "issue #")),
}


def required_families(request: str) -> list[str]:
    text = f" {request.lower()} "
    return [fam for fam, (_, words) in FACT_FAMILIES.items() if any(w in text for w in words)]


def _matches(key: str, prefixes: Iterable[str]) -> bool:
    return any(key == p or (p.endswith(".") and key.startswith(p)) or key.startswith(p + "[") for p in prefixes)


def _scope(packet: Mapping[str, Any], request: str) -> dict[str, Any]:
    """Question-scoped state: only facts the request depends on may block it."""
    families = required_families(request)
    if not families:
        # A request that depends on no known fact family must not inherit unrelated repo-level
        # blockers (e.g. a docs priority conflict blocking a coding question); authority still gates.
        return {"mode": "unscoped", "families": [], "prefixes": [], "missing": []}
    prefixes = [p for f in families for p in FACT_FAMILIES[f][0]]
    claims = [c["key"] for c in packet.get("current_claims") or []]
    coverage = packet.get("coverage") or {}
    facet_of = {"git": "git", "conv": "claude_code", "docs": "docs", "gh": "github"}
    known = {u["key"] for u in (packet.get("blocking_unknowns") or []) + (packet.get("unknowns") or [])}
    extra = []
    for fam in families:
        fam_prefixes = FACT_FAMILIES[fam][0]
        if any(_matches(k, fam_prefixes) for k in claims) or any(_matches(k, fam_prefixes) for k in known):
            continue
        facet = facet_of.get(fam_prefixes[0].split(".")[0], "")
        status = "unknown" if coverage.get(facet) in ("full", "partial") else "source_unavailable"
        extra.append({"key": fam_prefixes[0].rstrip("."), "reason": f"required by the request; no {fam} claim in the state",
                      "source_status": status})
    return {"mode": "question", "families": families, "prefixes": prefixes, "missing": extra}


def _unknowns(packet: Mapping[str, Any]) -> list[dict[str, Any]]:
    blocking = {u["key"]: u for u in packet.get("blocking_unknowns") or []}
    rows = []
    for u in list(blocking.values()) + [u for u in packet.get("unknowns") or [] if u["key"] not in blocking]:
        rows.append({
            "key": u["key"],
            # unknown != false != absent: a missing source is not a negative fact
            "status": u.get("source_status") or "unknown",
            "blocking": u["key"] in blocking,
            "reason": u.get("reason", ""),
        })
    return rows


def _action_space(unknowns: list[dict[str, Any]], contradictions: list[dict[str, Any]],
                  effects: Iterable[str], authority: dict[str, Any],
                  observe: list[dict[str, Any]]) -> list[dict[str, Any]]:
    blocking = [u["key"] for u in unknowns if u["blocking"]]
    contested = [c.get("contests") or c["key"] for c in contradictions]
    missing_authority = [e for e in effects if e not in authority["grants"]]
    act_blockers = [f"unknown:{k}" for k in blocking] + [f"contradiction:{k}" for k in contested] + \
                   [f"authority:{e}" for e in missing_authority]
    rows = [{"kind": "ACT", "action": "proceed", "legal": not act_blockers, "blocked_by": act_blockers,
             "why": "all required facts present, uncontested, and within authored authority" if not act_blockers
             else "blocked until the listed conditions are resolved"}]
    for t in observe:
        rows.append({"kind": "OBSERVE", "action": t["action"], "legal": True, "blocked_by": [], "why": t.get("why", "")})
    ask_reasons = [f"unknown:{u['key']}" for u in unknowns if u["blocking"] and u["status"] != "no_match"] + \
                  [f"authority:{e}" for e in missing_authority] + [f"contradiction:{k}" for k in contested]
    rows.append({"kind": "ASK", "action": "ask_user", "legal": bool(ask_reasons), "blocked_by": [] if ask_reasons else ["nothing_to_ask"],
                 "why": "only the user can supply a missing fact, pick between conflicting sources, or grant authority",
                 "about": ask_reasons})
    rows.append({"kind": "ESCALATE", "action": "surface_conflict", "legal": bool(contested),
                 "blocked_by": [] if contested else ["no_conflict"], "why": "sources conflict; do not pick a winner silently"})
    rows.append({"kind": "ABSTAIN", "action": "state_unknown", "legal": True, "blocked_by": [],
                 "why": "always legal: saying 'unknown' never exceeds authority"})
    return rows


def build_decision_opportunity(repo: str | Path, request: str, *, effects: Iterable[str] = ("read",),
                               aodl_doc: Mapping[str, Any] | None = None, packet: Mapping[str, Any] | None = None,
                               harness: str | None = None, trace_id: str | None = None, attempt: int = 0,
                               projects_root: str | Path | None = None, scoped: bool = True,
                               harness_grants: Iterable[str] | None = None) -> dict[str, Any]:
    """Deterministic given pinned evidence: identical inputs give an identical ``semantic_id``."""
    effects = [e for e in effects]
    unknown_effects = [e for e in effects if e not in EFFECTS]
    if unknown_effects:
        raise ValueError(f"unknown effect class(es) {unknown_effects}; expected {EFFECTS}")
    if packet is None:
        from .state_packet import build_state_packet

        packet = build_state_packet(Path(repo), projects_root=projects_root)
    authority = _authority(aodl_doc, harness_grants)
    scope = _scope(packet, request) if scoped else {"mode": "repo", "families": []}
    unknowns = _unknowns(packet)
    contradictions = list(packet.get("contradictions") or [])
    if scope["mode"] in ("question", "unscoped"):
        pre = scope["prefixes"]
        for u in unknowns:  # an unknown blocks only if the question depends on its family
            u["blocking"] = _matches(u["key"], pre)
        unknowns += [{"key": m["key"], "status": m["source_status"], "blocking": True, "reason": m["reason"]}
                     for m in scope["missing"]]
        contradictions = [c for c in contradictions if _matches(c.get("contests") or c["key"], pre)]
    observe = [t for t in packet.get("allowed_transitions") or [] if t.get("kind") == "OBSERVE"]
    claims = [{"key": c["key"], "value": c["value"], "evidence": c.get("evidence", []), "status": c.get("status")}
              for c in packet.get("current_claims") or []]
    superseded = [{"key": s["key"], "old_value": s.get("old_value"), "current_value": s.get("current_value")}
                  for s in packet.get("superseded_claims") or []]
    intent = {"request": request, "revision": _sha({"request": request, "effects": sorted(effects),
                                                   "aodl": authority.get("fingerprint")}, 12),
              "effects": sorted(effects)}
    semantic = {
        "schema": SCHEMA, "intent": intent, "authority": authority,
        "state": {"claims": claims, "superseded": superseded, "contradictions": contradictions, "unknowns": unknowns},
        "invalidation": {"source_revisions": packet.get("source_revisions") or {}},
        "scope": scope,
        "action_space": _action_space(unknowns, contradictions, effects, authority, observe),
    }
    semantic_id = _sha(semantic, 20)
    return {
        **semantic,
        "semantic_id": semantic_id,
        # identity of *this* observation of the opportunity; never part of the semantics
        "trace": {"harness": harness, "trace_id": trace_id, "attempt": attempt,
                  "opportunity_id": _sha({"semantic_id": semantic_id, "trace_id": trace_id, "attempt": attempt}, 20)},
        "provenance": {"packet_id": packet.get("packet_id"), "packet_schema": packet.get("schema"),
                       "evidence": packet.get("evidence") or [], "built_at": packet.get("built_at")},
        "fallback": {"on_uncertain": "ASK", "on_conflict": "ESCALATE", "always": "ABSTAIN"},
        "expected_outcome": {"verifier": None},
    }


def deterministic_gate(opportunity: Mapping[str, Any]) -> str:
    """The #55 falsification baseline: no learning, no confidence — first legal action by fixed priority."""
    legal = {a["kind"] for a in opportunity["action_space"] if a["legal"]}
    for kind in ("ACT", "ESCALATE", "ASK", "OBSERVE", "ABSTAIN"):
        if kind in legal:
            return kind
    return "ABSTAIN"


def with_authority_grant(opportunity: Mapping[str, Any], *, granted_by_user: str, effect: str) -> dict[str, Any]:
    """The ONLY way authority widens: an explicit human answer to an ASK. Model confidence has no entry point."""
    if not granted_by_user or not isinstance(granted_by_user, str):
        raise ValueError("authority can only be granted by an identified user answer")
    if effect not in EFFECTS:
        raise ValueError(f"unknown effect {effect!r}")
    authority = dict(opportunity["authority"])
    authority["grants"] = sorted(set(authority["grants"]) | {effect})
    authority["granted"] = list(authority.get("granted", [])) + [{"effect": effect, "by": granted_by_user}]
    state = opportunity["state"]
    semantic = {k: opportunity[k] for k in ("schema", "intent", "invalidation", "scope")}
    semantic.update({"authority": authority, "state": state,
                     "action_space": _action_space(state["unknowns"], state["contradictions"],
                                                   opportunity["intent"]["effects"], authority,
                                                   [a for a in opportunity["action_space"] if a["kind"] == "OBSERVE"])})
    return {**opportunity, **semantic, "semantic_id": _sha(semantic, 20)}


HARNESS_PREFIXES = ("<agent-message", "<task-notification", "<system-reminder")


def with_prompt_grants(opportunity: Mapping[str, Any], *, grants: Iterable[Mapping[str, Any]],
                       required: Iterable[Mapping[str, Any]], source: str) -> dict[str, Any]:
    """Scoped in-prompt grants (z0int#55 v1): the user's own prompt instructs a privileged action, so it
    authorises exactly that action on exactly that scope, and nothing else.

    Same shape as ``with_authority_grant``: authority widens only from an identified human source, never from
    model confidence. ``privileged`` is added for THIS opportunity only when every required privileged action
    is covered by a grant (``effect_inference.grant_covers``: same kind, target inside the phrase's scope;
    protected branches only when named). Otherwise authority is unchanged and the uncovered actions are
    recorded. Refused outright for non-user sources and harness/subagent messages; a grant whose phrase is not
    in the opportunity's own request is rejected (provenance)."""
    from .effect_inference import grant_covers

    request = str(opportunity["intent"]["request"] or "")
    if source != "user":
        raise ValueError(f"in-prompt grants come only from the user's own prompt, not {source!r}")
    if request.lstrip().startswith(HARNESS_PREFIXES):
        raise ValueError("harness/subagent messages carry no user authority")
    grants = [dict(g) for g in grants]
    required = [dict(a) for a in required]
    for g in grants:
        if g.get("source") != "prompt" or not g.get("phrase") or not isinstance(g.get("scope"), Mapping):
            raise ValueError("a prompt grant needs provenance {source: 'prompt', phrase, scope}")
        if str(g["phrase"]).lower() not in request.lower():
            raise ValueError(f"grant phrase {g['phrase']!r} is not in the user's request")
    uncovered = [a for a in required if not any(grant_covers(g, a) for g in grants)]
    authority = dict(opportunity["authority"])
    authority["prompt_grants"] = [{"source": "prompt", "phrase": g["phrase"], "scope": g["scope"]} for g in grants]
    authority["prompt_grant_uncovered"] = [{"kind": a.get("kind"), "phrase": a.get("phrase"), "target": a.get("target")}
                                           for a in uncovered]
    if required and not uncovered and "privileged" in opportunity["intent"]["effects"]:
        authority["grants"] = sorted(set(authority["grants"]) | {"privileged"}, key=EFFECTS.index)
        authority["granted"] = list(authority.get("granted", [])) + [
            {"effect": "privileged", "by": "prompt", "scope": [g["scope"] for g in grants],
             "phrases": [g["phrase"] for g in grants]}]
    state = opportunity["state"]
    semantic = {k: opportunity[k] for k in ("schema", "intent", "invalidation", "scope")}
    semantic.update({"authority": authority, "state": state,
                     "action_space": _action_space(state["unknowns"], state["contradictions"],
                                                   opportunity["intent"]["effects"], authority,
                                                   [a for a in opportunity["action_space"] if a["kind"] == "OBSERVE"])})
    return {**opportunity, **semantic, "semantic_id": _sha(semantic, 20)}
