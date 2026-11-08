"""Read-only capability routing table for the committed registry.

This reports static eligibility and evidence, not provider availability or
authorization to dispatch. It does not participate in routing decisions.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .automatic import evidenced


def build_table(registry: dict) -> list[dict]:
    """Report every manifest entry using the router's existing evidence gate."""
    rows = []
    for entry in registry["entries"]:
        eligible = entry.get("eligible") is True
        local = entry.get("local") is True
        has_evidence = evidenced(entry)
        if not eligible:
            blocker = "NOT_ELIGIBLE"
        elif not has_evidence:
            blocker = "NO_EVIDENCE"
        elif not local:
            blocker = "REMOTE_NEEDS_AUTH"
        else:
            blocker = None

        rows.append(
            {
                "function": entry["function"],
                "provider": entry["provider"],
                "model": entry["model"],
                "route": entry["route"],
                "local": local,
                "eligible": eligible,
                "evidenced": has_evidence,
                "measurement_confidence": entry.get("measurement_confidence"),
                "quality_metric": entry.get("quality_metric"),
                "eval_source": entry.get("eval_source"),
                "blocker": blocker,
            }
        )
    return rows


def summary(rows: list[dict]) -> dict:
    """Count static candidate states by function, including local SLM models."""
    totals = {}
    local_slms = {}
    for row in rows:
        function = row["function"]
        if function not in totals:
            totals[function] = {
                "rows": 0,
                "eligible": 0,
                "evidenced": 0,
                "static_local_candidates": 0,
                "local_slm_candidates": [],
            }
            local_slms[function] = set()

        group = totals[function]
        group["rows"] += 1
        group["eligible"] += int(row["eligible"])
        group["evidenced"] += int(row["evidenced"])
        group["static_local_candidates"] += int(row["blocker"] is None)
        if row["local"] and row["route"] == "LOCAL_MODEL":
            local_slms[function].add(row["model"])

    for function in totals:
        totals[function]["local_slm_candidates"] = sorted(local_slms[function])
    return {function: totals[function] for function in sorted(totals)}


def _markdown_cell(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value).replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def render_markdown(rows: list[dict]) -> str:
    """Render a stable, local-first table without changing the input rows."""
    columns = ("function", "provider", "model", "route", "local", "eligible", "evidenced", "measurement_confidence", "blocker")
    lines = [
        "| Function | Provider | Model | Route | Local | Eligible | Evidenced | Measurement confidence | Blocker |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    ordered = sorted(
        rows,
        key=lambda row: (
            row["function"],
            not row["local"],
            row["provider"],
            row["model"],
            row["route"],
            str(row["blocker"]),
            not row["eligible"],
            not row["evidenced"],
        ),
    )
    for row in ordered:
        lines.append("| " + " | ".join(_markdown_cell(row[column]) for column in columns) + " |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect static z0int capability routing eligibility.")
    parser.add_argument("--manifest", type=Path, help="Registry JSON path (default: z0int.intelligence.REGISTRY)")
    parser.add_argument("--json", action="store_true", help="Emit rows and per-function summary as JSON")
    args = parser.parse_args(argv)

    from .intelligence import REGISTRY

    manifest = args.manifest if args.manifest is not None else REGISTRY
    registry = json.loads(manifest.read_text(encoding="utf-8"))
    rows = build_table(registry)
    if args.json:
        print(json.dumps({"rows": rows, "summary": summary(rows)}, indent=2, sort_keys=True))
    else:
        print(render_markdown(rows), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
