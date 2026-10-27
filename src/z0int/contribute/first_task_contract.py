"""Closed contributor tie-break contract; stdlib only.

Kept equivalent to z0int.contribute.first_task_contract so neither public
package needs a runtime dependency on the other to verify an answer.
"""
from __future__ import annotations

import copy
import re

_TAG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
PROFILE_KEYS = {"skills", "interests", "contrib_done", "tier"}
TASK_KEYS = {"id", "title", "kind", "skills", "difficulty", "impact_note", "tier"}


def _tags(value):
    return (type(value) is list and all(type(v) is str and _TAG.fullmatch(v) for v in value))


def close_facts(source, candidates):
    """Reject unprojected/T3 fields rather than risk silently sending identifiers.

    Callers supply only closed skill/interest aggregates, never raw profiles.
    Candidate order is deterministic ranking order; the first id is the default.
    """
    if (not 1 <= len(candidates) <= 5 or len(set(candidates)) != len(candidates)
            or any(type(c) is not str or not c for c in candidates)):
        raise ValueError("invalid first_task_pick candidates")
    if type(source) is not dict or set(source) != {"profile", "tasks"}:
        raise ValueError("unsafe first_task_pick facts")
    profile, tasks = source["profile"], source["tasks"]
    if (type(profile) is not dict or set(profile) - PROFILE_KEYS
            or not _tags(profile.get("skills")) or not _tags(profile.get("interests"))
            or type(profile.get("contrib_done")) is not int or profile["contrib_done"] < 0
            or profile.get("tier", "T0") not in ("T0", "T1", "T2")):
        raise ValueError("unsafe first_task_pick profile")
    if type(tasks) is not list or len(tasks) != len(candidates):
        raise ValueError("invalid first_task_pick tasks")
    ids = []
    for task in tasks:
        if (type(task) is not dict or set(task) != TASK_KEYS
                or any(type(task.get(k)) is not str for k in TASK_KEYS - {"skills"})
                or not _tags(task["skills"]) or task["difficulty"] not in ("S", "M", "L")
                or task["tier"] not in ("P0", "P1", "P2", "P3")):
            raise ValueError("unsafe first_task_pick task")
        ids.append(task["id"])
    if len(set(ids)) != len(ids) or set(ids) != set(candidates):
        raise ValueError("invalid first_task_pick task ids")
    return copy.deepcopy(source)


def allowed_codes(facts, pick):
    task = next((t for t in facts["tasks"] if t["id"] == pick), None)
    if task is None:
        return set()
    profile = facts["profile"]
    prefix = "first_task_size" if profile["contrib_done"] == 0 else "size"
    codes = {f"tier:{task['tier']}", f"{prefix}:{task['difficulty']}"}
    codes.update("skill_match:" + tag for tag in set(task["skills"]) & set(profile["skills"]))
    codes.update("interest:" + tag for tag in set(task["skills"]) & set(profile["interests"]))
    return codes


def verify(facts, answer):
    if (type(answer) is not dict or set(answer) != {"pick", "reason_codes"}
            or type(answer["pick"]) is not str or type(answer["reason_codes"]) is not list
            or not answer["reason_codes"]
            or any(type(code) is not str for code in answer["reason_codes"])):
        return False
    codes = allowed_codes(facts, answer["pick"])
    return bool(codes) and all(code in codes for code in answer["reason_codes"])


def schema(facts, candidates):
    codes = sorted({code for pick in candidates for code in allowed_codes(facts, pick)})
    return {"type": "object", "properties": {"pick": {"type": "string", "enum": list(candidates)},
            "reason_codes": {"type": "array", "minItems": 1,
                             "items": {"type": "string", "enum": codes}}},
            "required": ["pick", "reason_codes"], "additionalProperties": False}
