"""Stage-change diffs with finding and source references."""
from __future__ import annotations


def policy_diff(before: dict | None, after: dict, findings: list[dict]) -> dict:
    old = (before or {}).get("families", {})
    new = after.get("families", {})
    indexed = {row["id"]: row for row in findings}
    changes, referenced = [], set()
    for key in sorted(set(old) | set(new)):
        previous, current = old.get(key, {}), new.get(key, {})
        start, end = previous.get("stage", "none"), current.get("stage", "none")
        if start == end:
            continue
        evidence = sorted(set(current.get("evidence", [])))
        referenced.update(evidence)
        changes.append({"rule_id": current.get("rule_id", previous.get("rule_id", key)),
                        "family": current.get("family", previous.get("family", key)),
                        "scope": current.get("scope", previous.get("scope", {})),
                        "from": start, "to": end, "because": evidence,
                        "reason": current.get("demoted_reason"),
                        "serving_missing": current.get("serving_missing", False)})
    return {"from_version": (before or {}).get("version"), "to_version": after["version"],
            "changes": changes,
            "synthetic": after.get("synthetic", False),
            "serving_missing": sorted(row["rule_id"] for row in new.values() if row.get("serving_missing")),
            "evidence": [{"id": finding_id, "path": indexed[finding_id]["source"]["path"],
                          "url": indexed[finding_id]["source"].get("url")}
                         for finding_id in sorted(referenced) if finding_id in indexed]}


def markdown_diff(before: dict | None, after: dict, findings: list[dict]) -> str:
    diff = policy_diff(before, after, findings)
    evidence = {row["id"]: row for row in diff["evidence"]}
    lines = ["# Decision policy changelog", "",
             f"From: `{diff['from_version'] or 'none'}`", f"To: `{diff['to_version']}`", ""]
    if diff["synthetic"]:
        lines.extend(["**Synthetic policy. This is not a real experiment promotion.**", ""])
    for change in diff["changes"]:
        lines.append(f"- `{change['rule_id']}`: **{change['from']} → {change['to']}**")
        if change["reason"]:
            lines.append(f"  - Reason: {change['reason']}")
        for finding_id in change["because"]:
            source = evidence.get(finding_id)
            if source:
                from pathlib import Path
                lines.append(f"  - Finding `{finding_id}`: [{source['path']}]({Path(source['path']).as_uri()})")
            else:
                lines.append(f"  - Finding `{finding_id}`: source not present in this ledger")
    if not diff["changes"]:
        lines.append("No stage changes.")
    if diff["serving_missing"]:
        lines.extend(["", "## Missing serving endpoints", ""])
        lines.extend(f"- `{rule_id}`: no measured endpoint matches the full rule scope."
                     for rule_id in diff["serving_missing"])
    return "\n".join(lines) + "\n"
