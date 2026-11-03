#!/usr/bin/env python3
"""O18 privacy-lane conformance: does each privacy virtual model's pool carry only backends its lane allows?

The routing decision is z0intelligence O18 (bend/ops_fsm, twin z0int.fsm.ops.route): the client
(the Hermes privacy-route plugin) picks a lane for the request's data tier and calls that lane's
virtual model. This check is the other half: the router config must not put a backend in a lane
that the lane's tiers may not reach (for example a train-on-prompts free model inside the
T1 paid lane, or any hosted backend inside the local lane).

    python3 deploy/vllm-sr/privacy_lanes.py [--config deploy/vllm-sr/config.yaml] [--json]

Exit 0 when every lane conforms, 1 otherwise. Backend classes come from base_url / backend name
(pol-1.2, sft-svlm/data/privacy/README.md): local host, approved paid subscription (Claude, ChatGPT, Grok),
approved free (Cerebras), and everything else (public: may train on or log prompts).
Not checked: whether the router's global fallback can leave a recipe's modelRefs (vllm-sr docs say
fallback stays within the decision's modelRefs; re-check on upgrades).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
# Must equal z0int.fsm.privacy_route.LANE_MODEL (the plugin's map).
LANES = {"vllm-sr/local": "local", "vllm-sr/private": "paid", "vllm-sr/free-private": "free_ok",
         "vllm-sr/turbo": "free_pub"}
# Backend classes each lane may contain. free_pub only serves T0, so any backend is acceptable there.
LANE_OK = {"local": {"local"}, "paid": {"paid_ok"}, "free_ok": {"free_ok"},
           "free_pub": {"local", "paid_ok", "free_ok", "public"}}


def backend_class(ref: dict) -> str:
    url = ref.get("base_url", "")
    name = ref.get("name", "")
    if url.startswith(("http://100.113.138.100:", "http://127.0.0.1:", "http://localhost:")):
        return "local"
    if name in ("subproxy-anthropic", "subproxy-codex", "subproxy-xai"):  # Grok approved for T1 2026-11-03
        return "paid_ok"
    if "api.cerebras.ai" in url:
        return "free_ok"
    return "public"


def check(cfg: dict) -> dict:
    models = {m["name"]: m for m in cfg["providers"]["models"]}
    recipes = {r["name"]: r for r in cfg.get("recipes", [])}
    entry = {n: e["recipe"] for e in cfg.get("entrypoints", []) for n in e["model_names"]}
    out, ok = {}, True
    for vm, lane in LANES.items():
        rec = recipes.get(entry.get(vm, ""))
        if vm == "vllm-sr/turbo" and entry.get(vm) == "default":
            refs = [r["model"] for d in cfg["routing"]["decisions"] for r in d["modelRefs"]]
        elif rec is None:
            out[vm] = {"lane": lane, "ok": False, "error": "no entrypoint/recipe"}
            ok = False
            continue
        else:
            refs = [r["model"] for d in rec["routing"]["decisions"] for r in d["modelRefs"]]
        classes = {m: sorted({backend_class(b) for b in models[m]["backend_refs"]}) for m in refs}
        bad = {m: c for m, c in classes.items() if not set(c) <= LANE_OK[lane]}
        out[vm] = {"lane": lane, "ok": not bad, "pool": classes, **({"violations": bad} if bad else {})}
        ok &= not bad
    return {"ok": ok, "lanes": out}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(HERE / "config.yaml"))
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    r = check(yaml.safe_load(Path(a.config).read_text()))
    if a.json:
        print(json.dumps(r, indent=2))
    else:
        for vm, row in r["lanes"].items():
            print(f"{'ok ' if row['ok'] else 'BAD'} {vm:22} {row['lane']:9} {row.get('violations') or row.get('error') or ''}")
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
