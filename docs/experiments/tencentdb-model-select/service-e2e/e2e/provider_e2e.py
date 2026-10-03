#!/usr/bin/env python3
"""End-to-end check of Hermes' memory_tencentdb provider against the running gateway.

Runs the provider code Hermes actually loads (~/.hermes/plugins/memory_tencentdb ->
TencentDB-Agent-Memory MemoryCore/hermes-plugin/memory/memory_tencentdb) with the
live hermes-agent's MemoryProvider base class, but in an isolated HOME/HERMES_HOME
and an isolated tenancy (team/agent/user = z0-e2e/tencentdb-e2e/synthetic-e2e), so
no live Hermes state or the default memory tenant is touched. Only synthetic text
is sent. Nothing secret is read or printed.

Usage (see ../README.md):
  env -i HOME=$E2E_HOME HERMES_HOME=$E2E_HOME/.hermes PATH=/usr/bin:/bin \
      PYTHONDONTWRITEBYTECODE=1 ~/.hermes/hermes-agent/venv/bin/python provider_e2e.py \
      [--calls 20] [--l1-wait 0] [--out results.json]
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import uuid
from pathlib import Path

HERMES_AGENT = Path("~/.hermes/hermes-agent")
PLUGINS_DIR = Path("~/.hermes/plugins")          # contains memory_tencentdb symlink
TENANT = dict(team_id="z0-e2e", agent_id="tencentdb-e2e", user_id="synthetic-e2e")


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    k = (len(xs) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def summarize(name: str, xs: list[float]) -> dict:
    return {"op": name, "n": len(xs), "p50_ms": round(pct(xs, 0.5), 1),
            "p95_ms": round(pct(xs, 0.95), 1), "max_ms": round(max(xs), 1),
            "mean_ms": round(statistics.mean(xs), 1)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--calls", type=int, default=20)
    ap.add_argument("--l1-wait", type=int, default=0,
                    help="seconds to poll L1 (atomic) search for LLM-extracted memories")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    home = os.environ.get("HOME", "")
    assert "/homes/tencentdb-e2e" in home, f"refusing to run outside the isolated HOME (HOME={home})"
    sys.path[:0] = [str(HERMES_AGENT), str(PLUGINS_DIR)]
    import memory_tencentdb as mt  # noqa: E402  (the installed Hermes plugin)

    report: dict = {"plugin_file": str(Path(mt.__file__).resolve()), "tenant": TENANT}
    provider = mt.MemoryTencentdbProvider()

    # 1) Availability exactly as Hermes decides it (no env overrides set).
    for var in ("MEMORY_TENCENTDB_GATEWAY_CMD", "MEMORY_TENCENTDB_GATEWAY_PORT"):
        assert var not in os.environ, f"{var} must be unset for the availability check"
    report["is_available"] = provider.is_available()
    if not report["is_available"]:
        print(json.dumps(report, indent=2)); return 1

    # Guard: if the gateway blips mid-test the provider's recovery path must not
    # spawn a second gateway from the plugin checkout; /bin/false fails harmlessly.
    os.environ["MEMORY_TENCENTDB_GATEWAY_CMD"] = "/bin/false"

    session = f"e2e-{uuid.uuid4().hex[:12]}"
    provider.initialize(session, **TENANT)
    t0 = time.time()
    while not provider._gateway_available and time.time() - t0 < 10:
        time.sleep(0.1)
    report["initialized"] = provider._gateway_available
    report["tool_schemas"] = [s["name"] for s in provider.get_tool_schemas()]

    # 2) Write a synthetic memory through the provider's real write path (sync_turn ->
    #    POST /v3/conversation/add on a background thread), then wait for it.
    canary = f"HERON{uuid.uuid4().hex[:10].upper()}"
    user_msg = (f"Synthetic end-to-end test only. My test codename is {canary} and my "
                f"favourite synthetic colour is ultraviolet-{canary[-4:]}. Please remember it.")
    asst_msg = f"Noted: your test codename is {canary}."
    t = time.perf_counter()
    provider.sync_turn(user_msg, asst_msg, session_id=session)
    for th in list(provider._active_syncs):
        th.join(timeout=30)
    report["write_ms_incl_thread_join"] = round((time.perf_counter() - t) * 1000, 1)

    # 3) Read it back. (a) the provider client's search call -- the exact HTTP request
    #    the memory_tencentdb_conversation_search tool makes; (b) the tool's formatted
    #    text. Upstream plugin bug: the tool parses data.items but the gateway (this
    #    image AND the plugin's own MemoryCore revision) returns data.messages, so (b)
    #    reports "No conversations found" even when (a) has hits. Recorded, not patched.
    found, attempts, t = False, 0, time.perf_counter()
    while not found and time.perf_counter() - t < 15:
        attempts += 1
        raw = provider._client.conversation_search(query=canary, limit=5, **TENANT)
        hits = (raw.get("data") or {}).get("messages") or []
        found = any(canary in (h.get("content") or "") for h in hits)
        if not found:
            time.sleep(0.5)
    tool_text = provider.handle_tool_call("memory_tencentdb_conversation_search",
                                          {"query": canary, "limit": 5})
    report["readback"] = {"found_via_provider_client": found, "attempts": attempts,
                          "ms_until_found": round((time.perf_counter() - t) * 1000, 1),
                          "hits": len(hits),
                          "tool_text_contains_canary": canary in tool_text,
                          "tool_text": tool_text[:120]}

    # 4) Tenancy isolation: the default tenant must NOT see the synthetic canary.
    default_client = provider._client
    iso = default_client.conversation_search(query=canary, limit=5)  # default/default/default
    report["default_tenant_sees_canary"] = canary in json.dumps(iso)

    # 5) Latency over N calls of each provider operation.
    lat: dict[str, list[float]] = {"prefetch(L1+L3+L2 parallel)": [],
                                   "conversation_search tool": [],
                                   "conversation_add (sync client call)": []}
    for i in range(args.calls):
        t = time.perf_counter(); provider.prefetch(f"what is my test codename {i}", session_id=session)
        lat["prefetch(L1+L3+L2 parallel)"].append((time.perf_counter() - t) * 1000)
        t = time.perf_counter(); provider.handle_tool_call(
            "memory_tencentdb_conversation_search", {"query": canary, "limit": 5})
        lat["conversation_search tool"].append((time.perf_counter() - t) * 1000)
        t = time.perf_counter(); provider._client.conversation_add(
            messages=[{"role": "user", "content": f"synthetic latency probe {i} for {canary}",
                       "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}],
            session_id=session, **TENANT)
        lat["conversation_add (sync client call)"].append((time.perf_counter() - t) * 1000)
    report["latency"] = [summarize(k, v) for k, v in lat.items()]
    report["breaker_failures"] = provider._consecutive_failures

    # 6) Optional: wait for the LLM pipeline (L1 extraction) to produce atomic memories.
    if args.l1_wait > 0:
        t, l1 = time.time(), ""
        while time.time() - t < args.l1_wait:
            l1 = provider.handle_tool_call("memory_tencentdb_memory_search",
                                           {"query": canary, "limit": 5})
            if canary in l1:
                break
            time.sleep(15)
        report["l1"] = {"found_canary": canary in l1, "waited_s": round(time.time() - t),
                        "sample": l1[:300]}

    provider.shutdown()
    report["canary"] = canary
    report["session"] = session
    text = json.dumps(report, indent=2, ensure_ascii=False)
    print(text)
    if args.out:
        Path(args.out).write_text(text + "\n")
    ok = report["initialized"] and found and not report["default_tenant_sees_canary"]
    if args.l1_wait > 0:
        ok = ok and report["l1"]["found_canary"]
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
