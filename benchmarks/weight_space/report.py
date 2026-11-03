"""Aggregate weight-space runs into results/<run_id>/summary.{json,md}.

  python benchmarks/weight_space/report.py --run-id 20261102-grid [--extra 20261103-high]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
PAPER_ASTRA = {"exp5/breakout": 517.4, "exp5/seaquest": 53.1, "exp5/asterix": 53.2}
PPO = {"exp5/breakout": (15.8, 25.3), "exp5/seaquest": (5.6, 88.9), "exp5/asterix": (25.8, 55.2)}


def tokens(u: dict) -> tuple[int | None, int | None]:
    """(output tokens, reasoning tokens) from any route's usage record."""
    u = u or {}
    out = u.get("output_tokens") or u.get("completion_tokens")
    rs = ((u.get("output_tokens_details") or {}).get("thinking_tokens")
          or (u.get("output_tokens_details") or {}).get("reasoning_tokens") or u.get("thinking_tokens")
          or (u.get("completion_tokens_details") or {}).get("reasoning_tokens") or u.get("reasoning_output_tokens"))
    return out, rs


def load(root: Path, label: str) -> list[dict]:
    rows = []
    for p in sorted(root.glob("*/exp*/*/r*/score.json")):
        r = json.loads(p.read_text())
        call = json.loads((p.parent / "call.json").read_text())
        r["arm"] = f"{r['editor']}" if not label else f"{r['editor']}@{label}"
        r["effort"] = call.get("effort")
        r["wrote_weights"] = (r.get("edit_report") or {}).get("block") if r["task"].startswith("exp5") else None
        rows.append(r)
    return rows


def read_cell(r: dict) -> str:
    if r.get("correct"):
        s = "✅"
    elif not r.get("predicted"):
        s = "∅"
    else:
        s = "❌ " + str(r["predicted"])
    return s + (f" {r['confidence']:.0f}%" if r.get("confidence") is not None else "")


def write_cell(r: dict) -> str:
    if not r.get("wrote_weights"):
        return "∅ (no weights)"
    return f"{r['mean']:.1f}" if r.get("mean") is not None else "error"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--extra", default="", help="comma list of extra run ids to show as extra arms (label = run id)")
    a = ap.parse_args()
    root = HERE / "results" / a.run_id
    rows = load(root, "")
    for rid in filter(None, a.extra.split(",")):
        rows += load(HERE / "results" / rid, rid.split("-")[-1])
    arms = sorted({r["arm"] for r in rows}, key=lambda x: (x.split("@")[0], "@" in x, x))
    tasks = sorted({r["task"] for r in rows})
    by = {}
    for r in rows:
        by.setdefault((r["arm"], r["task"]), []).append(r)

    summ = {"run_id": a.run_id, "extra": a.extra, "arms": {}}
    for arm in arms:
        rd = [r for r in rows if r["arm"] == arm and not r["task"].startswith("exp5")]
        wr = {r["task"]: (r.get("mean") if r.get("wrote_weights") else None)
              for r in rows if r["arm"] == arm and r["task"].startswith("exp5")}
        summ["arms"][arm] = {"read_correct": sum(bool(r.get("correct")) for r in rd), "read_total": len(rd),
                             "write_mean_score": wr}
    (root / "summary.json").write_text(json.dumps(summ, indent=1))

    read_arms = [x for x in arms if any(not t.startswith("exp5") for (aa, t) in by if aa == x)]
    write_arms = [x for x in arms if any(t.startswith("exp5") for (aa, t) in by if aa == x)]
    md = [f"# Weight-space programming evals: `{a.run_id}`", "",
          "Tasks, prompts and networks verbatim from the paper's released logs (RikiyaT/lm_understand-logs @ 4d09ba6);",
          "one fresh, tool-free reply per cell; reasoning effort as in the paper (read: high, write: xhigh) unless the",
          "arm is labelled otherwise.", "",
          "## Read: name the game / shape from the raw weights (open answer)", "",
          "| task | " + " | ".join(read_arms) + " |", "|---|" + "---|" * len(read_arms)]
    for t in [t for t in tasks if not t.startswith("exp5")]:
        md.append(f"| {t} | " + " | ".join(" ".join(read_cell(r) for r in by.get((x, t), [])) or "—"
                                          for x in read_arms) + " |")
    md.append("| **total** | " + " | ".join(
        f"**{summ['arms'][x]['read_correct']}/{summ['arms'][x]['read_total']}**" for x in read_arms) + " |")
    md += ["", "✅ correct · ❌ named something else · ∅ no answer (output budget spent reasoning) · n% = stated confidence.",
           "", "## Write: all 20,486 weights of a MinAtar policy from scratch, one reply, no tools", "",
           "Mean score over 1,000 episodes of official MinAtar 1.0.15 (sticky actions 0.1, softmax sampling, 10k-step cap).",
           "", "| game | PPO 100M / 500M (paper) | Astra (paper) | " + " | ".join(write_arms) + " |",
           "|---|---|---|" + "---|" * len(write_arms)]
    for t in [t for t in tasks if t.startswith("exp5")]:
        p = PPO[t]
        md.append(f"| {t.split('/')[1]} | {p[0]} / {p[1]} | {PAPER_ASTRA[t]} | " + " | ".join(
            " / ".join(write_cell(r) for r in by.get((x, t), [])) or "—" for x in write_arms) + " |")
    md += ["", ("∅ (no weights) = the reply had no `edits` block: the model spent its whole output budget "
           "(128k tokens on the API routes) reasoning."), "",
           "## Cost per cell", "", "| arm | task | effort | out tok | reasoning tok | wall s | finish |",
           "|---|---|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda r: (r["arm"], r["task"], r["rep"])):
        o, rs = tokens(r.get("usage"))
        md.append(f"| {r['arm']} | {r['task']} | {r['effort']} | {o} | {rs} | {r['wall_s']} | {r.get('finish') or ''} |")
    (root / "summary.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
