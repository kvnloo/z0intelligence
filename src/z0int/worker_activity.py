"""Read-only worker activity derived from canonical decision receipts."""
from __future__ import annotations
import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

TERMINAL = {"completed", "failed", "incomplete", "completed_unmetered_or_unidentified"}
FIELDS = ("provider", "model", "latency_ms", "input_tokens", "output_tokens")


def project_activity(rows, parent_agent=None):
    """Deduplicate attempts; latest terminal wins, later starts cannot erase it."""
    workers = {}
    for row in rows:
        extra = row.get("extra") or {}
        sub = extra.get("subagent_id")
        parent = extra.get("parent_agent")
        trace = row.get("trace_id")
        status = extra.get("status")
        if not sub or not trace or status not in TERMINAL | {"started"}:
            continue
        if parent_agent is not None and parent != parent_agent:
            continue
        attempts = workers.setdefault((parent, sub), {})
        previous = attempts.get(trace)
        if previous and previous["status"] in TERMINAL and status == "started":
            continue
        attempts[trace] = {
            "subtask": extra.get("subtask", sub),
            "adopted": extra.get("parent_consumption") == "adopted" and status == "completed",
            "trace_id": trace, "attempt_index": extra.get("attempt_index", 0),
            "status": status, "response_model": extra.get("response_model"),
            "physical_call_attempted": extra.get("physical_call_attempted", False),
            **{key: row.get(key) for key in FIELDS},
            "estimated_frontier_tokens_avoided": (
                row.get("estimated_frontier_tokens_avoided") if status == "completed" else None),
        }
    result = []
    for (parent, sub), by_trace in workers.items():
        attempts = sorted(by_trace.values(), key=lambda a: (a["attempt_index"], a["trace_id"]))
        last = attempts[-1]
        result.append({
            "parent_agent": parent, "subagent_id": sub, "subtask": last["subtask"], "status": last["status"],
            "adopted_provider": next((a["provider"] for a in reversed(attempts) if a["adopted"]), None),
            "provider": last["provider"], "model": last["model"],
            "response_model": last["response_model"], "fallback": last["attempt_index"] > 0,
            "attempts": attempts,
            "known_input_tokens": sum(a["input_tokens"] or 0 for a in attempts),
            "known_output_tokens": sum(a["output_tokens"] or 0 for a in attempts),
            "unknown_usage_attempts": sum(a["input_tokens"] is None or a["output_tokens"] is None for a in attempts),
            "known_latency_ms": sum(a["latency_ms"] or 0 for a in attempts),
            "estimated_frontier_tokens_avoided": last["estimated_frontier_tokens_avoided"],
            "parent_consumption": "adopted" if any(a["adopted"] for a in attempts) else "not_recorded", "quality_verified": False,
        })
    return {"schema_version": "worker_activity.v1", "workers": result,
            "aggregate": {
                "attempted_providers": dict(Counter(a["provider"] for w in result for a in w["attempts"] if a["physical_call_attempted"])),
                "completed_providers": dict(Counter(a["provider"] for w in result for a in w["attempts"] if a["status"] == "completed")),
                "adopted_providers": dict(Counter(w["adopted_provider"] for w in result if w["adopted_provider"])),
                "known_input_tokens": sum(w["known_input_tokens"] for w in result),
                "known_output_tokens": sum(w["known_output_tokens"] for w in result),
                "unknown_usage_attempts": sum(w["unknown_usage_attempts"] for w in result)}}


def read_activity(path, parent_agent=None):
    # Ignore an unfinished trailing append; malformed committed lines are errors.
    data = Path(path).read_bytes()
    lines = data.splitlines(keepends=True)
    rows = [json.loads(line) for line in lines if line.strip() and line.endswith(b"\n")]
    return project_activity(rows, parent_agent)


def render_activity(snapshot):
    def safe(value):
        return "".join(c if c.isprintable() else " " for c in str(value))
    lines = ["z0intelligence · live worker activity", "", "Subtask · provider · model · status · latency · input/output tokens · fallback chain"]
    for w in snapshot["workers"]:
        chain = " → ".join(f"{a['provider']}:{a['status']}" for a in w["attempts"])
        adopted = w["adopted_provider"] or "not recorded"
        lines.append(safe(f"{w['subtask']} · {w['provider']} · {w['model']} · {w['status']} · {w['known_latency_ms']:.0f}ms known · {w['known_input_tokens']}/{w['known_output_tokens']} known · {chain} · adopted: {adopted}"))
    a = snapshot["aggregate"]
    lines += ["", safe(f"Provider calls: {a['attempted_providers']} | completed: {a['completed_providers']} | adopted: {a['adopted_providers']}"),
              f"Worker tokens: {a['known_input_tokens']} input / {a['known_output_tokens']} output; unknown attempts: {a['unknown_usage_attempts']}",
              "Completion is not adoption. Started may be stalled. No net savings claim."]
    return "\n".join(lines)


def acknowledge_adoption(trace_id, output, parent_agent, *, root=None):
    """Parent assertion of adoption, tied to the exact completed output hash."""
    import hashlib
    from .receipt import find_receipt, append_receipt
    row = find_receipt(trace_id, root=root)
    if not row or row.get("extra", {}).get("status") != "completed":
        raise ValueError("Adoption requires a completed canonical receipt")
    extra = row["extra"]
    if extra.get("parent_agent") != parent_agent or extra.get("output_sha256") != hashlib.sha256(output.encode()).hexdigest():
        raise ValueError("Parent or output does not match receipt")
    extra.update(parent_consumption="adopted", adopted_at=time.time())
    return append_receipt(row, root=root)


def main():
    from .receipt import receipts_path
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipts", type=Path)
    parser.add_argument("--parent-agent")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=float, default=2.0)
    args = parser.parse_args()
    if not 0.1 <= args.interval <= 60:
        parser.error("interval must be between 0.1 and 60 seconds")
    path = args.receipts or receipts_path()
    try:
        while True:
            try:
                snapshot = read_activity(path, args.parent_agent)
                output = render_activity(snapshot) if args.watch else json.dumps(snapshot, indent=2)
            except (OSError, ValueError) as exc:
                if not args.watch:
                    raise
                output = "Receipt read failed: " + type(exc).__name__ + "; retrying"
            if args.watch and sys.stdout.isatty():
                print("\033[2J\033[H", end="")
            print(output, flush=True)
            if not args.watch:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
