#!/usr/bin/env python3
"""Build + validate the frozen synthetic corpus -> corpus.json (+ corpus.sha256).

Run once; the committed corpus.json is the frozen artifact the PREREG pins by sha256.
Re-running must reproduce the identical bytes (all generators are seeded).
"""
import hashlib
import json
import re
import statistics
import zlib
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "score"))

import convs_ab, convs_cde, convs_fgh  # noqa: E402
from normalize import norm  # noqa: E402

SUBSETS = {
    # round-0 compatibility screen (all candidates, 1 repeat)
    "S": ["A01_lantern_pnpm", "B01_db_choice", "C01_comms_style", "D01_host_move", "E01_trivia",
          "F01_env_paste", "G01_atlas_rewrite", "H02_third_party"],
    # paid arms (grok-4.7 low, luna xhigh), L1+dedup
    "P": ["A01_lantern_pnpm", "A06_k8s_deploy", "B01_db_choice", "B05_queue", "C01_comms_style",
          "D01_host_move", "E03_regex", "F01_env_paste", "G01_atlas_rewrite", "H02_third_party"],
    # conversations on which L2 (scene) + L3 (persona) are also run
    "L23": ["A01_lantern_pnpm", "A06_k8s_deploy", "B01_db_choice", "B03_launch_plan", "C01_comms_style",
            "C02_food_health", "C04_editor_tools", "D03_pref_change", "G01_atlas_rewrite", "G02_fitness",
            "G03_job_change", "G06_notes_app"],
    # paid arms: L2/L3 only on these, only after all paid L1 cells, only if budget remains
    "P_L23": ["A01_lantern_pnpm", "B01_db_choice", "C01_comms_style", "G01_atlas_rewrite"],
}


def validate(convs):
    ids = [c["id"] for c in convs]
    assert len(ids) == len(set(ids)), "duplicate conversation ids"
    all_secrets = [s for c in convs for s in c["secrets"]]
    for c in convs:
        gids = {g["gid"] for g in c["gold"]}
        for g in c["gold"]:
            for a in g["anchors"]:
                re.compile(a)
            assert all(re.search(a, norm(g["statement"])) for a in g["anchors"]), (c["id"], g["gid"], "statement fails own anchors")
        for o in c["optional"]:
            assert all(re.search(a, norm(o["statement"])) for a in o["anchors"]), (c["id"], o["statement"])
        for t in c["traps"]:
            fired = all(re.search(a, norm(t["statement"])) for a in t["anchors"]) and not any(
                re.search(u, norm(t["statement"])) for u in t["unless"])
            assert fired, (c["id"], t["tid"], "trap statement does not fire")
        for s in c["supersessions"]:
            assert s["current"] in gids, (c["id"], s)
        for d in c["dedup"]:
            assert d["gid"] in gids, (c["id"], d)
        text = "\n".join(m[r]["content"] for s in c["sessions"] for m in s["rounds"] for r in ("user", "assistant"))
        for sec in all_secrets:
            if sec in text:
                assert sec in c["secrets"], (c["id"], "foreign secret present")
        for sec in c["secrets"]:
            assert sec in text, (c["id"], "declared secret not in text")
    for k, v in SUBSETS.items():
        for cid in v:
            assert cid in ids, (k, cid)


def stats(convs):
    u = [len(m["user"]["content"]) for c in convs for s in c["sessions"] for m in s["rounds"]]
    a = [len(m["assistant"]["content"]) for c in convs for s in c["sessions"] for m in s["rounds"]]
    q = lambda xs, p: sorted(xs)[min(len(xs) - 1, int(p * len(xs)))]
    return {
        "conversations": len(convs),
        "sessions": sum(len(c["sessions"]) for c in convs),
        "rounds": len(u),
        "gold_required": sum(len(c["gold"]) for c in convs),
        "gold_empty_conversations": sum(1 for c in convs if not c["gold"]),
        "traps": sum(len(c["traps"]) for c in convs),
        "supersessions": sum(len(c["supersessions"]) for c in convs),
        "dedup_checks": sum(len(c["dedup"]) for c in convs),
        "secrets": sum(len(c["secrets"]) for c in convs),
        "user_chars": {"p50": q(u, .5), "p90": q(u, .9), "max": max(u), "mean": round(statistics.mean(u))},
        "assistant_chars": {"p50": q(a, .5), "p90": q(a, .9), "max": max(a)},
        "categories": sorted({cat for c in convs for cat in c["categories"]}),
    }


def pad_lengths(convs):
    """Production user turns are long (chiefstaff p50 ~1.3k chars, p90 ~7k): the owner writes long
    briefs and pastes context. Deterministically append a neutral pasted-notes block (PROSE filler,
    fact-free) to ~50% of substantive user turns in non-noise conversations."""
    from dsl import PROSE
    for c in convs:
        if "noise" in c["categories"]:
            continue
        for si, s in enumerate(c["sessions"]):
            for m in s["rounds"]:
                u = m["user"]["content"]
                key = zlib.crc32(f"{c['id']}:{si}:{m['round']}".encode())
                if 40 <= len(u) < 600 and key % 2 == 0:
                    m["user"]["content"] = u + "\n\n(context notes I pasted from my planning doc, FYI)\n" + PROSE(key % 100000, 6 + key % 17)


def main():
    convs = convs_ab.CONVS + convs_cde.CONVS + convs_fgh.CONVS
    pad_lengths(convs)
    validate(convs)
    corpus = {
        "schema": "tdai-model-select-corpus/v1",
        "synthetic": True,
        "note": "Fully synthetic, fictional persona; secrets are random seeded strings. Safe to send to cloud models.",
        "subsets": SUBSETS,
        "stats": stats(convs),
        "conversations": convs,
    }
    out = HERE / "corpus.json"
    data = json.dumps(corpus, ensure_ascii=False, indent=1, sort_keys=True).encode()
    out.write_bytes(data)
    h = hashlib.sha256(data).hexdigest()
    (HERE / "corpus.sha256").write_text(f"{h}  corpus.json\n")
    print(json.dumps(corpus["stats"], indent=1))
    print("sha256", h)


if __name__ == "__main__":
    main()
