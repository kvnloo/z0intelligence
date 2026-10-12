"""Hot path: one factorized TypeSafe System One request per navigation ask.

Ported from the Handsfree Stage Manager (``omp_hud/jev_decide.py``) and made
data-driven: the action options come from the learned catalog, and every ask is
logged as an episode for the learner. The API key never leaves this process.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
import uuid
from typing import Any, Callable

from . import store

NONE_ID = "none"
NONE_TEXT = "Not a window-navigation request, or none of these fit. Hand it to the chat assistant."

Transport = Callable[[dict[str, Any], float], dict[str, Any] | None]


def api_key() -> str:
    key = (os.environ.get("TYPESAFE_API_KEY") or "").strip()
    if key:
        return key
    try:
        with open(os.path.expanduser("~/.omp/.env"), encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("TYPESAFE_API_KEY="):
                    return line.split("=", 1)[1].strip().strip("'\"")
    except OSError:
        pass
    return ""


def jev_transport(body: dict[str, Any], timeout_s: float) -> dict[str, Any] | None:
    """POST /v1/systemone; ``None`` on any failure (the caller hands off to Carly)."""
    key = api_key()
    if not key:
        return None
    base = (os.environ.get("TYPESAFE_BASE_URL") or "https://api.typesafe.ai").rstrip("/")
    request = urllib.request.Request(
        f"{base}/v1/systemone",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            payload = json.loads(response.read().decode())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def describe(action: dict[str, Any], policy: dict[str, Any]) -> str:
    """Option text: the description plus a few learned phrasings (Jev reads literally)."""
    text = str(action.get("description") or "").strip()
    exemplars = [e for e in action.get("exemplars") or [] if isinstance(e, str) and e.strip()]
    exemplars = exemplars[: int(policy["exemplars_per_action"])]
    if exemplars:
        text = f"{text} For example: " + "; ".join(f'"{e}"' for e in exemplars) + "."
    return text[: int(policy["description_chars"])]


def offered_actions(catalog: dict[str, Any], limit: int | None = None) -> list[str]:
    """Active actions, most-supported first, within the option budget."""
    policy = catalog["policy"]
    active = [(aid, spec) for aid, spec in catalog["actions"].items() if spec.get("status") == "active"]
    seeds = [aid for aid, spec in active if spec.get("source") == "seed"]
    learned = sorted(
        (aid for aid, spec in active if spec.get("source") != "seed"),
        key=lambda aid: (-(catalog["actions"][aid].get("support") or 0), aid),
    )
    budget = int(limit if limit is not None else policy["max_actions"])
    return (seeds + learned)[: max(len(seeds), budget)]


def build_request(
    utterance: str,
    candidates: list[dict[str, Any]],
    catalog: dict[str, Any],
    action_ids: list[str],
) -> dict[str, Any]:
    policy = catalog["policy"]
    actions = catalog["actions"]
    action_criteria = {aid: describe(actions[aid], policy) for aid in action_ids}
    action_criteria[NONE_ID] = NONE_TEXT
    questions: dict[str, Any] = {
        "action": {
            "type": "choice",
            "instructions": (
                "The user typed this request in a terminal app with tabs and blocks. "
                "Which window-navigation action does it ask for? Pick none unless the request "
                "clearly asks for one of these actions."
            ),
            "criteria": action_criteria,
        }
    }
    targets = {c["id"]: c["label"] for c in candidates[: int(policy["max_targets"])] if c.get("id") and c.get("label")}
    # The action question must know what is open: "go to server logs" is a focus
    # request only because a tab called "server logs" exists.
    state: dict[str, Any] = {"request": utterance[:2000], "open_tabs_and_blocks": list(targets.values())}
    if "focus" in action_ids and targets:
        targets[NONE_ID] = "No listed tab or block."
        questions["target"] = {
            "type": "choice",
            "instructions": "If the request names or describes one of these tabs or blocks, pick it. Otherwise none.",
            "criteria": targets,
        }
    return {
        "state": state,
        "model": os.environ.get("TYPESAFE_DEFAULT_MODEL") or "jev-latest",
        "questions": questions,
    }


def interpret(payload: dict[str, Any] | None, catalog: dict[str, Any], min_confidence: float) -> dict[str, Any]:
    answers = payload.get("answers") if isinstance(payload, dict) else None
    if not isinstance(answers, dict):
        return {"action": NONE_ID, "reason": "no_answer"}
    action_answer = answers.get("action") or {}
    action = str(action_answer.get("choice") or NONE_ID)
    probabilities = action_answer.get("probabilities") if isinstance(action_answer.get("probabilities"), dict) else {}
    confidence = float(probabilities.get(action) or action_answer.get("confidence") or 0.0)
    top = sorted(probabilities.items(), key=lambda kv: -float(kv[1] or 0))[:5]
    result: dict[str, Any] = {"action": action, "confidence": confidence, "top": [[k, round(float(v), 4)] for k, v in top]}
    if action == NONE_ID or action not in catalog["actions"]:
        result.update(action=NONE_ID, reason="jev_none")
        return result
    if confidence < min_confidence:
        result.update(proposed=action, action=NONE_ID, reason="low_confidence")
        return result
    exec_spec = catalog["actions"][action]["exec"]
    result["exec"] = exec_spec
    if exec_spec.get("type") == "focus":
        target = str((answers.get("target") or {}).get("choice") or "")
        if not target or target == NONE_ID:
            result.update(proposed=action, action=NONE_ID, reason="no_target")
            return result
        result["target"] = target
    return result


def decide(
    utterance: str,
    candidates: list[dict[str, Any]],
    *,
    transport: Transport = jev_transport,
    catalog: dict[str, Any] | None = None,
    log: bool = True,
) -> dict[str, Any]:
    started = time.perf_counter()
    catalog = catalog or store.load_catalog()
    policy = catalog["policy"]
    utterance = utterance.strip()
    episode = uuid.uuid4().hex[:16]
    if not utterance:
        return {"episode": episode, "action": NONE_ID, "reason": "empty"}
    action_ids = offered_actions(catalog)
    body = build_request(utterance, candidates, catalog, action_ids)
    payload = transport(body, float(policy["jev_timeout_s"]))
    result = interpret(payload, catalog, float(policy["min_confidence"]))
    result["episode"] = episode
    latency_ms = int((time.perf_counter() - started) * 1000)
    if log:
        store.append_jsonl(
            "episodes.jsonl",
            {
                "id": episode,
                "ts": store.now(),
                "backend": "jev",
                "utterance": utterance[:500],
                "offered": action_ids,
                "targets": len(body["questions"].get("target", {}).get("criteria", {})),
                "decision": {k: v for k, v in result.items() if k not in {"episode", "exec"}},
                "latency_ms": latency_ms,
            },
        )
    return result
