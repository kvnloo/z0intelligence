#!/usr/bin/env python3
"""Durable, offline, synthetic end-to-end receipt. Never connects a device."""
import argparse
import json
from pathlib import Path

from z0int.turbo import app_fanout as app
from z0int.turbo import app_fanout_queue as queue


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    root = args.out.resolve()
    if root.exists():
        raise SystemExit("create-only output; choose a new path")
    root.mkdir(parents=True)
    url = "https://github.com/kvnloo/dash/issues/107"
    meta = {url: {"title": "Synthetic offline smoke", "body": "NOT a real device run",
                  "acceptance": ["The fixture file changes from old to new"],
                  "owner_words": "Synthetic quote; not Kevin's real issue words", "route": "/fixture"}}
    fixture = Path(__file__).resolve().parents[1] / "tests/fixtures/app_fanout/synthetic.json"
    outcomes = []
    for scenario in ("success", "redbox"):
        run = root / scenario
        plan = app.plan(run, 2, [url], "chat", meta)
        # Real 20-second pacing exercised here. No UI, network or external GitHub writes.
        rows = app.launch(run, app.SimDevice(fixture), dry_run=True)
        assert len(rows) == 2 and all(x["state"] == "launched" for x in rows)
        diff = "diff --git a/feature.txt b/feature.txt\n--- a/feature.txt\n+++ b/feature.txt\n@@ -1 +1 @@\n-old\n+new\n"
        tag = plan["briefs"][0]["tag"]
        # Captured-XML parser exercised with generated XML, explicitly synthetic.
        device = app.SimDevice(fixture)
        device.state = "sent"
        device.text = tag + "\n```diff\n" + diff + "```"
        xml = device.dump()
        (run / "synthetic-final.xml").write_text(xml)
        replies = app.replies_from_dump(xml)
        candidates = app.harvest(run, pr_rows=[], replies=replies, simulated=True)
        assert len(candidates) == 1
        repo = root / (scenario + "-repo")
        repo.mkdir()
        queue.git(repo, "init", "-b", app.BASE)
        (repo / "feature.txt").write_text("old\n")
        queue.git(repo, "add", ".")
        queue.git(repo, "commit", "-m", "Synthetic baseline")
        baseline = queue.git(repo, "rev-parse", "HEAD^{tree}")
        log = run / "fake-metro.log"
        log.write_text("")
        def tick(_):
            if scenario == "redbox":
                with log.open("a") as out:
                    out.write("TypeError: SYNTHETIC redbox\n")
        def build(target):
            assert (target / "feature.txt").read_text() == "new\n"
            app.save(run / "simulated-build.json", {"simulated": True, "check": "fixture bytes only; NOT an Android export"})
        result = queue.queue(run, repo, repo, metro_log=log, now_testing=root / "synthetic-now-testing.jsonl",
                             build=build, sleep=tick, simulated=True)
        expected = "now_testing" if scenario == "success" else "reverted"
        assert result[0]["state"] == expected, result
        if scenario == "redbox":
            assert queue.git(repo, "rev-parse", "HEAD^{tree}") == baseline
        outcomes.append({"scenario": scenario, "state": result[0]["state"], "launches": len(rows)})
    app.save(root / "summary.json", {"simulated": True, "real_device_actions": 0, "outcomes": outcomes,
                                    "scope": "Synthetic UI, real Git apply/merge/revert; stubbed build and Metro"})
    print(json.dumps(outcomes, indent=2))


if __name__ == "__main__":
    main()
