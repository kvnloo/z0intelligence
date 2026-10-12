"""Offline learner: turn fast-path episodes into catalog changes.

Ground truth comes from three places, cheapest first:
1. Fallbacks: when Jev passed, Carly handled the request. Her transcript shows
   which Tern action she actually ran, so that action is the label.
2. Applied fast-path actions the user kept are positive labels.
3. Applied actions the user undid within seconds are negative labels.

Jev cannot invent options, so new actions only come from (1) and only from
Tern's own action universe. No model call happens here.
"""

from __future__ import annotations

import glob
import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from . import store

DAY = 86400.0
MATCH_WINDOW_S = 600.0
EARLY_SKEW_S = 5.0
# Learned actions run with no model turn and no confirmation, so anything that
# can lose work stays with Carly no matter how often she runs it.
UNSAFE_ACTION = re.compile(
    r"close|kill|quit|exit|delete|remove|reset|clear|restart|reload|sign_?out|log_?out|uninstall|discard|force|stop|signal|send|commit|push",
    re.I,
)

_STR = r"""\(\s*['"]([A-Za-z0-9_:.\-]+)['"]"""
_LUA_ACTION = re.compile(r"cx\s*:\s*(?:command|action)\s*" + _STR + r"|cx\.actions\s*:\s*run\s*" + _STR)
_LUA_FOCUS = re.compile(r"cx\.layout\s*:\s*focus\s*\(")
_LUA_UNSUPPORTED = {
    "session_switch": re.compile(r"cx\.sessions\s*:\s*switch\s*\("),
    "open": re.compile(r"cx\s*:\s*open\s*\("),
    "arrange": re.compile(r"cx\.layout\s*:\s*(?:arrange|move|park|unpark)\s*\("),
}


def normalize(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split())


def transcript_roots() -> list[str]:
    override = os.environ.get("Z0INT_CARLY_SESSIONS")
    if override:
        return [override]
    return [str(Path.home() / ".cache" / "tern" / "carly" / "*" / "sessions")]


def _parse_ts(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value) / (1000.0 if value > 1e11 else 1.0)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def labels_from_calls(calls: Iterable[dict[str, Any]]) -> list[str]:
    """Navigation labels in one Carly turn, in call order.

    ``tern:<id>`` is an executable Tern action, ``focus`` a target switch, and
    ``unsupported:<kind>`` a navigation the fast path cannot execute yet.
    """
    labels: list[str] = []
    for call in calls:
        name = call.get("name")
        args = call.get("arguments") or {}
        if name == "run_command" and isinstance(args.get("command"), str):
            labels.append("tern:" + args["command"])
        elif name == "actions" and isinstance(args.get("action"), str) and args["action"]:
            labels.append("tern:" + args["action"])
        elif name == "session" and args.get("operation") == "switch":
            labels.append("unsupported:session_switch")
        elif name == "arrange":
            labels.append("unsupported:arrange")
        elif name == "lua" and isinstance(args.get("code"), str):
            code = args["code"]
            for match in _LUA_ACTION.finditer(code):
                labels.append("tern:" + (match.group(1) or match.group(2)))
            if _LUA_FOCUS.search(code):
                labels.append("focus")
            for kind, pattern in _LUA_UNSUPPORTED.items():
                if pattern.search(code):
                    labels.append("unsupported:" + kind)
    return labels


def carly_turns() -> list[tuple[float, str, list[dict[str, Any]]]]:
    """(timestamp, user text, tool calls until the next user message) per Carly turn.

    Typed requests are ``user`` messages. Voice requests reach Carly's backend as
    ``custom_message`` rows (``live-delegation``) written by the live voice model.
    """
    turns: list[tuple[float, str, list[dict[str, Any]]]] = []
    for pattern in transcript_roots():
        for file in glob.glob(os.path.join(pattern, "*.jsonl")):
            current: tuple[float, str, list[dict[str, Any]]] | None = None
            for row in store.read_jsonl(Path(file)):
                text: str | None = None
                message = row.get("message") or {}
                content = message.get("content")
                if row.get("type") == "custom_message" and row.get("customType") == "live-delegation":
                    raw = row.get("content")
                    text = raw if isinstance(raw, str) else " ".join(
                        c.get("text", "") for c in raw or [] if isinstance(c, dict) and c.get("type") == "text")
                elif row.get("type") != "message" or not isinstance(content, list):
                    continue
                elif message.get("role") == "user":
                    text = " ".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")
                if text is not None:
                    ts = _parse_ts(row.get("timestamp")) or _parse_ts(message.get("timestamp"))
                    current = None if ts is None else (ts, text, [])
                    if current is not None:
                        turns.append(current)
                elif message.get("role") == "assistant" and current is not None:
                    current[2].extend(c for c in content if isinstance(c, dict) and c.get("type") == "toolCall")
    return turns


def label_for_fallback(episode: dict[str, Any], turns: list[tuple[float, str, list[dict[str, Any]]]]) -> str | None:
    """The single navigation label of Carly's turn for this request, if unambiguous."""
    needle = normalize(episode.get("utterance") or "")
    ts = float(episode.get("ts") or 0)
    if not needle:
        return None
    for turn_ts, text, calls in turns:
        # Carly's own `plugins['carly-nav'].go(...)` call carries the exact words,
        # and it runs after her turn started, so it may precede the episode.
        via_export = any(
            call.get("name") == "lua" and "carly-nav" in str((call.get("arguments") or {}).get("code"))
            and needle in normalize(str((call.get("arguments") or {}).get("code")))
            for call in calls
        )
        typed_here = needle in normalize(text) and ts - EARLY_SKEW_S <= turn_ts <= ts + MATCH_WINDOW_S
        if not (typed_here or (via_export and abs(turn_ts - ts) <= MATCH_WINDOW_S)):
            continue
        labels = list(dict.fromkeys(labels_from_calls(calls)))
        if not labels:
            return "none"  # Carly answered without navigating: a true negative for Jev.
        return labels[0] if len(labels) == 1 else None
    return None


def action_id_for(label: str) -> str | None:
    if label == "focus":
        return "focus"
    if label.startswith("tern:"):
        return label[len("tern:"):]
    return None


def collect_labels(turns: list[tuple[float, str, list[dict[str, Any]]]] | None = None) -> list[dict[str, Any]]:
    episodes = {row["id"]: row for row in store.read_jsonl("episodes.jsonl") if row.get("id")}
    outcomes: dict[str, str] = {}
    for row in store.read_jsonl("outcomes.jsonl"):
        if row.get("episode") in episodes:
            # Later outcomes win: applied → undone, or fallback after a failed apply.
            outcomes[row["episode"]] = str(row.get("outcome"))
    turns = carly_turns() if turns is None else turns
    labels: list[dict[str, Any]] = []
    for episode_id, episode in episodes.items():
        outcome = outcomes.get(episode_id)
        decision = episode.get("decision") or {}
        proposed = decision.get("action") if decision.get("action") != "none" else decision.get("proposed")
        base = {"episode": episode_id, "ts": episode.get("ts"), "utterance": episode.get("utterance"),
                "backend": episode.get("backend"), "jev_action": proposed,
                "confidence": decision.get("confidence"), "offered": episode.get("offered") or []}
        if outcome == "applied":
            labels.append({**base, "label": decision.get("action"), "source": "kept", "correct": True})
        elif outcome == "undone":
            labels.append({**base, "label": None, "source": "undone", "wrong": decision.get("action"), "correct": False})
        elif outcome == "fallback":
            label = label_for_fallback(episode, turns)
            if label is None:
                continue
            labels.append({**base, "label": action_id_for(label) or label, "source": "carly",
                           "correct": proposed == action_id_for(label) if proposed else None})
    return labels


def learn(now: float | None = None, turns=None) -> dict[str, Any]:
    now = store.now() if now is None else now
    catalog = store.load_catalog()
    policy = catalog["policy"]
    actions = catalog["actions"]
    universe = {a["id"]: a for a in (store.read_json("universe.json") or {}).get("actions", []) if a.get("id")}
    labels = collect_labels(turns)
    store.root().mkdir(parents=True, exist_ok=True)
    with open(store.path("labels.jsonl"), "w", encoding="utf-8") as handle:
        for row in labels:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    utterances: dict[str, set[str]] = defaultdict(set)
    misses: dict[str, list[str]] = defaultdict(list)
    last_used: dict[str, float] = {}
    unsupported: Counter[str] = Counter()
    for row in labels:
        label = row.get("label")
        if not label or label == "none":
            continue
        if label.startswith("unsupported:"):
            unsupported[label] += 1
            continue
        utterances[label].add(normalize(row["utterance"]))
        last_used[label] = max(last_used.get(label, 0.0), float(row.get("ts") or 0))
        # Jev saw this action and still passed: teach it the phrasing.
        if row["source"] == "carly" and label in row.get("offered", []) and row.get("jev_action") != label:
            misses[label].append(row["utterance"])

    changes: list[dict[str, Any]] = []
    blocked: dict[str, int] = {}
    horizon = now - float(policy["retire_after_days"]) * DAY
    # Promote: actions Carly keeps running that Jev was never offered.
    for action_id, seen in utterances.items():
        spec = actions.get(action_id)
        if spec is None:
            if action_id not in universe or UNSAFE_ACTION.search(action_id):
                blocked[action_id] = len(seen)
                continue
            spec = actions[action_id] = {
                "description": str(universe[action_id].get("label") or action_id).rstrip(".…") + ".",
                "exec": {"type": "tern_action", "name": action_id},
                "status": "candidate", "source": "learned", "exemplars": [], "support": 0, "last_used": None,
            }
            changes.append({"action": action_id, "change": "candidate"})
        spec["support"] = len(seen)
        spec["last_used"] = last_used.get(action_id)
        recent = float(spec["last_used"] or 0) >= horizon
        if spec["status"] in {"candidate", "retired"} and recent and len(seen) >= int(policy["promote_min_support"]):
            spec["status"] = "active"
            changes.append({"action": action_id, "change": "promoted", "support": len(seen)})

    # Exemplars: misses first (they fix real errors), then the shortest distinct phrasings.
    for action_id, spec in actions.items():
        pool = list(dict.fromkeys(misses.get(action_id, []) + sorted(utterances.get(action_id, ()), key=len)))
        chosen = [p for p in pool if p][: int(policy["exemplars_per_action"])]
        if chosen and chosen != spec.get("exemplars"):
            spec["exemplars"] = chosen
            changes.append({"action": action_id, "change": "exemplars"})

    # Retire: learned actions unused for too long, so the option list stays small.
    for action_id, spec in actions.items():
        if spec.get("source") == "seed" or spec.get("status") != "active":
            continue
        if float(spec.get("last_used") or 0) < horizon:
            spec["status"] = "retired"
            changes.append({"action": action_id, "change": "retired"})

    threshold = calibrate_threshold(labels, float(policy["min_confidence"]))
    if threshold != policy["min_confidence"]:
        changes.append({"policy": "min_confidence", "from": policy["min_confidence"], "to": threshold})
        policy["min_confidence"] = threshold

    store.save_catalog(catalog)
    return {
        "labels": len(labels),
        "by_source": dict(Counter(row["source"] for row in labels)),
        "unsupported": dict(unsupported),
        "blocked": blocked,
        "changes": changes,
        "active": sorted(a for a, s in actions.items() if s.get("status") == "active"),
        "candidates": {a: s.get("support") for a, s in actions.items() if s.get("status") == "candidate"},
    }


def calibrate_threshold(labels: list[dict[str, Any]], current: float, *, precision: float = 0.95,
                        min_samples: int = 20) -> float:
    """Lowest confidence cut whose kept Jev decisions reach ``precision``.

    Uses only Jev decisions with a known verdict. Too little evidence keeps the
    current value; the cut never drops below 0.3 or rises above 0.9.
    """
    scored = [(float(r["confidence"]), bool(r["correct"])) for r in labels
              if r.get("backend") == "jev" and r.get("confidence") is not None and r.get("correct") is not None]
    if len(scored) < min_samples:
        return current
    for cut in [x / 100 for x in range(30, 91, 5)]:
        kept = [ok for conf, ok in scored if conf >= cut]
        if len(kept) >= min_samples // 2 and sum(kept) / len(kept) >= precision:
            return cut
    return 0.9
