"""Deterministic alignment engine (spec §3.3, §3.6). No LLM, no I/O.

Facts in (priorities doc, vocab, profile, prefs, history, optional live claim and
issue state), ranking out. The same inputs always give the same output, whatever
the task order in the file. Hard filters run first; the score only orders the
survivors. The order is ``(score desc, tier asc, rank asc, id asc)``.

Owner decision O-2: domain tags may also come from the closed ``affinity`` table
in ``.z0/vocab.yaml`` (for example cargo -> rust -> systems, latency). This is a
fixed lookup over detected facts, not free-text inference.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

ENGINE_VERSION = "1"
FEEDBACK_TASK = "t-onboarding-feedback"
TIER_WEIGHT = {"P0": 1.0, "P1": 0.75, "P2": 0.5, "P3": 0.3}
TIER_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
LANGUAGE_SHARE_MIN = 0.10
ALT_KIND_WINDOW = 0.25
FORBIDDEN_LLM_PROVIDERS = ("grok", "xai", "cursor")  # kernel law P1: never LLM providers
BLOCKING_LABELS = ("blocked", "parked", "hcl:backlog")
FALLBACK_LABELS = ("good first issue", "claimable")

# Closed reason codes (spec §3.3). Anything else is rejected by reasons_consistent().
_REASON_RE = re.compile(
    r"^(tier:P[0-3]|skill_match:[a-z0-9-]+|interest:[a-z0-9-]+|first_task_size:[SML]|size:[SML]"
    r"|hw_unique:[a-z0-9-]+|missing_toolchain:[a-z0-9-]+|fallback:(labels|feedback))$"
)


def check_llm_choice(choice: str) -> str:
    """Refuse grok/cursor in any spelling. The deterministic engine itself uses no LLM."""
    low = (choice or "").lower()
    if any(p in low for p in FORBIDDEN_LLM_PROVIDERS):
        raise ValueError(f"LLM provider {choice!r} is never allowed (grok and cursor are not LLM providers)")
    return choice


# ---------------------------------------------------------------- facts

def host_class(profile: dict[str, Any]) -> str:
    gpu = (profile.get("hw") or {}).get("gpu") or {}
    cls, vram = gpu.get("class") or "none", gpu.get("vram_gib") or 0
    if cls == "none" or vram <= 0:
        return "cpu"
    return f"{cls}-{int(round(vram))}g"


def build_have(profile: dict[str, Any], vocab: dict[str, Any], prefs: dict[str, Any] | None) -> set[str]:
    """Closed tag set the contributor brings: languages, toolchains, domains."""
    prefs = prefs or {}
    toolchains = set(profile.get("toolchains") or []) & set(vocab.get("toolchains") or [])
    langs_vocab = set(vocab.get("languages") or [])
    hist = profile.get("languages") or {}
    if hist:  # T2: consented histogram
        langs = {k for k, v in hist.items() if k in langs_vocab and v >= LANGUAGE_SHARE_MIN}
    else:  # T0/T1: languages come from toolchains only
        tl = vocab.get("toolchain_languages") or {}
        langs = {lang for tc in toolchains for lang in tl.get(tc, [])} & langs_vocab
    domains_vocab = set(vocab.get("domains") or [])
    affinity = vocab.get("affinity") or {}
    domains = {d for key in langs | toolchains for d in affinity.get(key, [])}
    domains |= set(profile.get("interests") or []) | set(prefs.get("interests") or [])
    return langs | toolchains | (domains & domains_vocab)


def interests_of(profile: dict[str, Any], prefs: dict[str, Any] | None) -> set[str]:
    return set((prefs or {}).get("interests") or []) | set(profile.get("interests") or [])


# ---------------------------------------------------------------- terms

def impact(tier: str, rank: int, n_tier: int) -> float:
    return TIER_WEIGHT[tier] * (1 - (rank - 1) / max(n_tier, 1))


def ramp(difficulty: str, done: int) -> float:
    if done <= 0:
        return {"S": 1.0, "M": 0.6, "L": 0.0}[difficulty]
    if done < 3:
        return {"S": 0.6, "M": 1.0, "L": 0.4}[difficulty]
    return {"S": 0.4, "M": 0.8, "L": 1.0}[difficulty]


def _claim_limit(claim: str) -> int | None:
    if claim == "exclusive":
        return 1
    m = re.fullmatch(r"parallel\((\d+)\)", claim or "")
    return int(m.group(1)) if m else None


def _gate_ok(task: dict[str, Any], status_by_id: dict[str, str], issue_states: dict[int, str] | None,
             conditions: set[str]) -> bool:
    gate = task.get("gate") or {}
    if not gate:
        return True
    if "task" in gate:
        return status_by_id.get(gate["task"]) == "done"
    if "issue" in gate:
        return str((issue_states or {}).get(int(gate["issue"]), "")).upper() == "CLOSED"
    if "condition" in gate:
        return gate["condition"] in conditions
    return False


def _hard_filter(task: dict[str, Any], ctx: dict[str, Any]) -> str | None:
    """First failing hard filter (an excluded_counts key), or None when the task survives."""
    prof, prefs = ctx["profile"], ctx["prefs"]
    hw_need = task["hardware"]
    hw = prof.get("hw") or {}
    if not task["newcomer"] or task["needs_owner_approval"]:
        return "owner_approval"
    if task["status"] != "open":
        return "status"
    if not _gate_ok(task, ctx["status_by_id"], ctx["issue_states"], ctx["conditions"]):
        return "gated"
    limit = _claim_limit(task["claim"])
    if limit is not None and ctx["live_claims"] is not None and ctx["live_claims"].get(task["id"], 0) >= limit:
        return "claimed"
    vram_need = hw_need["gpu_vram_gib"]
    vram_have = ((hw.get("gpu") or {}).get("vram_gib") or 0)
    if vram_need > 0 and (vram_have < vram_need or prefs.get("gpu_use") == "never"):
        return "hardware"
    ram = hw.get("ram_gib")
    disk = hw.get("disk_free_gib")
    if (ram is not None and ram < hw_need["ram_gib"]) or (disk is not None and disk < hw_need["disk_gib"]):
        return "hardware"
    if hw.get("os", "unknown") not in ("unknown", *hw_need["os"]):
        return "hardware"
    if hw.get("arch", "unknown") not in ("unknown", *hw_need["arch"]):
        return "hardware"
    if task["runtime"] == "z0" and ctx["onboard_mode"] == "repo_only":
        return "runtime"
    if task["network"] in ("hf_download", "other") and (
        prof.get("network") is False or (task["network"] == "hf_download" and prefs.get("hf_download") == "never")
    ):
        return "network"
    avoid = set(prefs.get("avoid") or [])
    if task["id"] in avoid or avoid & set(task["skills"]):
        return "avoided"
    if ctx["done"] == 0 and (
        task["difficulty"] == "L" or task["expected_hours"] > prefs.get("max_first_task_hours", 4)
    ):
        return "first_task_size"
    missing = set(task.get("toolchains") or []) - set(prof.get("toolchains") or [])
    if missing - ctx["installable"]:
        return "toolchain"
    return None


def _score(task: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    w = ctx["weights"]
    skills = list(task["skills"])
    matched = sorted(set(skills) & ctx["have"])
    liked = sorted(set(skills) & ctx["interests"])
    hw_cls = ctx["host_class"]
    uniq = int(task["claim"].startswith("parallel") and (task["id"], hw_cls) not in ctx["receipts"])
    missing = sorted(set(task.get("toolchains") or []) - set(ctx["profile"].get("toolchains") or []))
    terms = {
        "impact": impact(task["tier"], task["rank"], ctx["n_tier"][task["tier"]]),
        "fit": len(matched) / len(skills),
        "interest": 1 if liked else 0,
        "ramp": ramp(task["difficulty"], ctx["done"]),
        "uniq_hw": uniq,
        "penalty": ctx["penalty_per_missing"] * len(missing),
    }
    score = sum(w[k] * terms[k] for k in ("impact", "fit", "interest", "ramp", "uniq_hw")) - terms["penalty"]
    reasons = [f"tier:{task['tier']}"]
    reasons += [f"skill_match:{s}" for s in matched]
    reasons += [f"interest:{s}" for s in liked]
    reasons.append(f"first_task_size:{task['difficulty']}" if ctx["done"] == 0 else f"size:{task['difficulty']}")
    if uniq:
        reasons.append(f"hw_unique:{hw_cls}")
    reasons += [f"missing_toolchain:{m}" for m in missing]
    return {
        "task": task["id"], "issue": task["issue"], "title": task["title"], "kind": task["kind"],
        "tier": task["tier"], "rank": task["rank"], "difficulty": task["difficulty"],
        "impact_note": task["impact_note"], "score": score, "terms": terms, "reasons": reasons,
    }


def _card(row: dict[str, Any], *, reasons: list[str] | None = None) -> dict[str, Any]:
    tid = row["task"]
    return {
        "task": tid, "issue": row.get("issue"), "title": row.get("title", ""), "kind": row.get("kind", ""),
        "difficulty": row.get("difficulty", "S"), "impact_note": row.get("impact_note", ""),
        "score": round(float(row.get("score", 0.0)), 4),
        "reasons": list(reasons if reasons is not None else row.get("reasons", [])),
        "next": [f"z0int contribute claim {tid}", f"z0int contribute start {tid}"],
    }


def _feedback_card(task: dict[str, Any] | None) -> dict[str, Any]:
    row = {
        "task": FEEDBACK_TASK, "issue": None, "kind": "docs", "difficulty": "S", "score": 0.0,
        "title": (task or {}).get("title", "onboarding friction report"),
        "impact_note": (task or {}).get("impact_note", "tells the owner where onboarding hurts"),
    }
    return _card(row, reasons=["fallback:feedback"])


def _pick_three(ranked: list[dict[str, Any]], feedback_task: dict[str, Any] | None) -> tuple[dict, list[dict]]:
    if not ranked:
        return _feedback_card(feedback_task), []
    top = ranked[0]
    chosen = [top]
    rest = ranked[1:]
    alt1 = next((r for r in rest if r["kind"] != top["kind"] and top["score"] - r["score"] <= ALT_KIND_WINDOW), None)
    if alt1 is None and rest:
        alt1 = rest[0]
    if alt1 is not None:
        chosen.append(alt1)
    alt2 = next((r for r in ranked if r not in chosen), None)
    if alt2 is not None:
        chosen.append(alt2)
    cards = [_card(r) for r in chosen]
    if len(cards) < 3 and all(c["task"] != FEEDBACK_TASK for c in cards):
        cards.append(_feedback_card(feedback_task))
    return cards[0], cards[1:]


def align(
    doc: dict[str, Any],
    vocab: dict[str, Any],
    profile: dict[str, Any],
    prefs: dict[str, Any] | None = None,
    *,
    done_count: int = 0,
    onboard_mode: str = "full",
    live_claims: dict[str, int] | None = None,
    issue_states: dict[int, str] | None = None,
    satisfied_conditions: Iterable[str] = (),
    receipts_index: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    """Rank the priority file for one contributor. Pure function.

    ``live_claims``/``issue_states`` are ``None`` when GitHub could not be read; the
    claim filter is then skipped (warning "claim state unknown") and issue gates stay
    closed. ``receipts_index`` rows are ``{task, host_class}`` from .z0/receipts-index.yaml.
    """
    prefs = dict(prefs or {})
    tasks = sorted(doc["tasks"], key=lambda t: t["id"])  # input order never matters
    n_tier: dict[str, int] = {k: 0 for k in TIER_WEIGHT}
    for t in tasks:
        n_tier[t["tier"]] += 1
    al = doc["alignment"]
    ctx = {
        "profile": profile, "prefs": prefs, "done": int(done_count), "onboard_mode": onboard_mode,
        "live_claims": live_claims, "issue_states": issue_states, "conditions": set(satisfied_conditions),
        "status_by_id": {t["id"]: t["status"] for t in tasks},
        "installable": set(vocab.get("installable_toolchains") or []),
        "have": build_have(profile, vocab, prefs), "interests": interests_of(profile, prefs),
        "host_class": host_class(profile), "n_tier": n_tier, "weights": al["weights"],
        "penalty_per_missing": al["setup_penalty_per_missing"],
        "receipts": {(r.get("task"), r.get("host_class")) for r in receipts_index},
    }
    excluded: dict[str, int] = {}
    ranked: list[dict[str, Any]] = []
    for t in tasks:
        why = _hard_filter(t, ctx)
        if why:
            excluded[why] = excluded.get(why, 0) + 1
            continue
        ranked.append(_score(t, ctx))
    ranked.sort(key=lambda r: (-r["score"], TIER_ORDER[r["tier"]], r["rank"], r["task"]))
    feedback_task = next((t for t in tasks if t["id"] == FEEDBACK_TASK), None)
    pick, alts = _pick_three(ranked, feedback_task)
    warnings = []
    if live_claims is None:
        warnings.append("claim state unknown")
    if issue_states is None:
        warnings.append("issue state unknown; issue gates stay closed")
    return {
        "source": "priorities" if ranked else "fallback:feedback",
        "engine_version": ENGINE_VERSION,
        "ranked": ranked,
        "pick": pick,
        "alternatives": alts,
        "excluded_counts": dict(sorted(excluded.items())),
        "warnings": warnings,
        "tie_candidates": [r["task"] for r in ranked if ranked[0]["score"] - r["score"] <= al["tie_epsilon"]][:5],
    }


def fallback_from_labels(issues: list[dict[str, Any]], weights: dict[str, float] | None = None) -> dict[str, Any]:
    """Fallback 1: no valid priority file -> open issues labelled good first issue / claimable."""
    w = weights or {"ramp": 0.15}
    ranked = []
    for it in sorted(issues, key=lambda i: int(i["number"])):
        labels = set(it.get("labels") or [])
        if not labels & set(FALLBACK_LABELS) or labels & set(BLOCKING_LABELS):
            continue
        diff = "S" if "good first issue" in labels else "M"
        ranked.append({
            "task": f"gh-{int(it['number'])}", "issue": int(it["number"]), "title": str(it.get("title", ""))[:200],
            "kind": "issue", "tier": "P3", "rank": int(it["number"]), "difficulty": diff, "impact_note": "",
            "score": w["ramp"] * ramp(diff, 0), "terms": {"fit": 0}, "reasons": ["fallback:labels"],
        })
    ranked.sort(key=lambda r: (-r["score"], r["issue"]))
    pick, alts = _pick_three(ranked, None)
    return {
        "source": "fallback:labels" if ranked else "fallback:feedback", "engine_version": ENGINE_VERSION,
        "ranked": ranked, "pick": pick, "alternatives": alts, "excluded_counts": {},
        "warnings": ["no valid .z0/priorities.yaml; using GitHub labels"], "tie_candidates": [],
    }


def reasons_consistent(reasons: list[str], *, have: set[str], host_class: str, vocab: dict[str, Any]) -> bool:
    """Every code is closed and agrees with the facts (used to verify any tie-break answer)."""
    tags = set(vocab.get("languages", [])) | set(vocab.get("domains", [])) | set(vocab.get("toolchains", []))
    for code in reasons:
        if not _REASON_RE.match(code):
            return False
        kind, _, val = code.partition(":")
        if kind == "skill_match" and val not in have:
            return False
        if kind == "interest" and val not in tags:
            return False
        if kind == "hw_unique" and val != host_class:
            return False
        if kind == "missing_toolchain" and val not in set(vocab.get("toolchains", [])):
            return False
    return True


def to_suggestion(result: dict[str, Any], *, sha: str | None, priorities_sha256: str | None,
                  profile_tier: str, llm: str = "none", llm_reason: str | None = None) -> dict[str, Any]:
    engine = {"version": ENGINE_VERSION, "llm": check_llm_choice(llm), "answers_ref": None}
    if llm_reason:
        engine["llm_reason"] = llm_reason
    return {
        "schema": "z0.contribute.suggestion.v1",
        "sha": sha,
        "priorities_sha256": priorities_sha256,
        "profile_tier": profile_tier,
        "source": result["source"],
        "engine": engine,
        "pick": result["pick"],
        "alternatives": result["alternatives"],
        "excluded_counts": result["excluded_counts"],
        "warnings": result["warnings"],
    }
