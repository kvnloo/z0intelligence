"""Option-budget tuning by live replay against Jev.

Jev's guide favours offering the full option list (up to 255) plus a way out.
More options also cost latency and can blur close neighbours, so the budget is
measured, not assumed: replay labelled requests at several budgets and keep the
smallest budget whose accuracy stays within ``tolerance`` of the best.

This makes real API calls: ``budgets x rows``. It refuses to run without enough
labels, and it caps rows.
"""

from __future__ import annotations

import statistics
import time
from typing import Any

from . import decide as hot
from . import store


def replay_rows(limit: int) -> list[dict[str, Any]]:
    catalog = store.load_catalog()
    active = set(hot.offered_actions(catalog, limit=10_000))
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for row in sorted(store.read_jsonl("labels.jsonl"), key=lambda r: -(r.get("ts") or 0)):
        label = row.get("label")
        utterance = (row.get("utterance") or "").strip()
        if not utterance or (label != hot.NONE_ID and label not in active):
            continue
        key = utterance.lower()
        if key in seen:
            continue
        seen.add(key)
        rows.append({"utterance": utterance, "label": label})
        if len(rows) >= limit:
            break
    return rows


def tune(*, transport: hot.Transport = hot.jev_transport, max_rows: int = 40, min_rows: int = 20,
         tolerance: float = 0.01, apply: bool = True) -> dict[str, Any]:
    catalog = store.load_catalog()
    rows = replay_rows(max_rows)
    if len(rows) < min_rows:
        return {"status": "insufficient_labels", "rows": len(rows), "min_rows": min_rows}
    active = hot.offered_actions(catalog, limit=10_000)
    budgets = sorted({b for b in (len(active), 32, 16, 8) if b <= len(active)}, reverse=True)
    timeout = float(catalog["policy"]["jev_timeout_s"]) * 3  # tuning measures quality, not the hot-path deadline
    results = []
    for budget in budgets:
        offered = hot.offered_actions(catalog, limit=budget)
        correct = 0
        latencies = []
        failures = 0
        for row in rows:
            body = hot.build_request(row["utterance"], [], catalog, offered)
            started = time.perf_counter()
            payload = transport(body, timeout)
            latencies.append((time.perf_counter() - started) * 1000)
            if payload is None:
                failures += 1
                continue
            choice = ((payload.get("answers") or {}).get("action") or {}).get("choice")
            correct += int(choice == row["label"])
        results.append({
            "budget": len(offered),
            "accuracy": round(correct / len(rows), 4),
            "median_ms": round(statistics.median(latencies), 1),
            "failures": failures,
        })
    best = max(r["accuracy"] for r in results)
    chosen = min((r for r in results if r["accuracy"] >= best - tolerance), key=lambda r: r["budget"])
    evidence = {"ts": store.now(), "rows": len(rows), "results": results, "chosen": chosen["budget"]}
    if apply:
        catalog["policy"]["max_actions"] = chosen["budget"]
        catalog["policy"]["evidence"] = evidence
        store.save_catalog(catalog)
    store.append_jsonl("tuning.jsonl", evidence)
    return {"status": "ok", **evidence}
