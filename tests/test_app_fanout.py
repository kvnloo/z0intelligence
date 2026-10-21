"""Offline contract tests. Fixtures are synthetic, never described as handset dumps."""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from z0int.turbo import app_fanout as app
from z0int.turbo import app_fanout_queue as queue

FIXTURE = Path(__file__).parent / "fixtures/app_fanout/synthetic.json"
URL = "https://github.com/kvnloo/dash/issues/107"
META = {URL: {"title": "Dogfood fixture", "body": "Synthetic issue context", "acceptance": ["A visible fixture changes"],
              "owner_words": "Synthetic owner quote, not real Kevin feedback", "route": "/fixture"}}


class Clock:
    def __init__(self):
        self.now = 0
    def __call__(self):
        return self.now
    def sleep(self, seconds):
        self.now += seconds


class FanoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        account = patch.object(app, "account_root", return_value=self.root / "account")
        account.start()
        self.addCleanup(account.stop)
        self.run_dir = self.root / "smoke"

    def make(self, n=1, mode="chat"):
        return app.plan(self.run_dir, n, [URL], mode, META)

    def launch(self, **options):
        clock = Clock()
        return app.launch(self.run_dir, app.SimDevice(FIXTURE), dry_run=True, clock=clock,
                          sleep=clock.sleep, **options), clock

    def test_plan_exact_tags_and_quotes(self):
        p = self.make(11)
        self.assertEqual(len({b["tag"] for b in p["briefs"]}), 11)
        self.assertIn("make a draft PR to " + app.BASE, p["briefs"][0]["brief"])
        self.assertEqual(p["briefs"][0]["owner_words"], META[URL]["owner_words"])
        with self.assertRaises(ValueError):
            self.make()

    def test_metadata_required_no_invented_quotes(self):
        with self.assertRaises(ValueError):
            app.plan(self.run_dir, 1, [URL], metadata={URL: {"title": "No criteria"}})

    def test_malformed_issue_not_normalized(self):
        with self.assertRaises(ValueError):
            app.plan(self.run_dir, 1, [URL + "/"], metadata=META)

    def test_chat_state_machine_pacing_cap_restart(self):
        self.make(7)
        rows, clock = self.launch()
        self.assertEqual(len(rows), 5)
        self.assertEqual(clock.now, 80)
        self.assertTrue(all(r["state"] == "launched" for r in rows))
        rows2, _ = self.launch()
        self.assertEqual(rows2, rows)
        events = [json.loads(x) for x in (self.run_dir / "events.jsonl").read_text().splitlines()]
        self.assertEqual(sum(x["kind"] == "action" for x in events), 20)

    def test_codex_state_machine_requires_repo(self):
        self.make(2, "codex")
        rows, clock = self.launch()
        self.assertEqual(len(rows), 2)
        self.assertEqual(clock.now, 20)
        self.assertTrue(all(r["state"] == "launched" for r in rows))

    def test_noop_fails_closed(self):
        self.make()
        d = app.SimDevice(FIXTURE)
        d.act = lambda *a, **kw: None
        with self.assertRaises(RuntimeError):
            app.launch(self.run_dir, d, dry_run=True)
        self.assertEqual(app.load(self.run_dir / "launches.json")[0]["state"], "uncertain")

    def test_stop_before_any_device_action(self):
        self.make()
        (self.run_dir / "STOP").touch()
        d = app.SimDevice(FIXTURE)
        d.dump = lambda: self.fail("STOP must precede dump")
        with self.assertRaisesRegex(RuntimeError, "STOP"):
            app.launch(self.run_dir, d, dry_run=True)

    def test_stop_during_pacing(self):
        self.make(2)
        clock = Clock()
        def sleep(seconds):
            (self.run_dir / "STOP").touch()
            clock.sleep(seconds)
        with self.assertRaisesRegex(RuntimeError, "STOP"):
            app.launch(self.run_dir, app.SimDevice(FIXTURE), dry_run=True, clock=clock, sleep=sleep)
        self.assertEqual(len(app.load(self.run_dir / "launches.json")), 1)

    def test_rate_limit_and_ambiguity(self):
        frames = app.load(FIXTURE)
        with self.assertRaisesRegex(RuntimeError, "rate_limit"):
            app.select(frames["home"].replace("New chat", "You've reached your limit"), ("New chat",))
        with self.assertRaisesRegex(RuntimeError, "ambiguous"):
            app.select(frames["home"].replace('text="Codex"', 'text="New chat"'), ("New chat",))
        with self.assertRaisesRegex(RuntimeError, "hierarchy"):
            app.nodes("ERROR: could not get idle state.")

    def test_launch_limits_and_live_consent(self):
        self.make()
        for kwargs in ({"pace": 19}, {"concurrency": 21}, {"concurrency": 0}, {"pace": float('nan')}):
            with self.assertRaises(ValueError):
                app.launch(self.run_dir, None, **kwargs)
        with self.assertRaises(ValueError):
            app.launch(self.run_dir, None, device_name="s25", opt_in=True)

    def test_tag_matching_no_prefix_collision(self):
        p = self.make(11)
        b = p["briefs"][0]
        prs = [{"repo": "kvnloo/dash", "base": app.BASE, "title": p["briefs"][10]["tag"],
                "body": "", "head": "a" * 40, "url": "https://github.com/kvnloo/dash/pull/999"}]
        candidates = app.harvest(self.run_dir, pr_rows=prs, simulated=True)
        self.assertEqual([x["tag"] for x in candidates], [p["briefs"][10]["tag"]])

    def candidate(self):
        p = self.make()
        self.launch()
        diff = "diff --git a/feature.txt b/feature.txt\n--- a/feature.txt\n+++ b/feature.txt\n@@ -1 +1 @@\n-old\n+new\n"
        replies = {p["briefs"][0]["tag"]: "```diff\n" + diff + "```"}
        return app.harvest(self.run_dir, pr_rows=[], replies=replies, simulated=True)

    def repo(self):
        repo = self.root / "repo"
        repo.mkdir()
        queue.git(repo, "init", "-b", app.BASE)
        (repo / "feature.txt").write_text("old\n")
        queue.git(repo, "add", ".")
        queue.git(repo, "commit", "-m", "fixture")
        return repo

    def test_full_simulated_pipeline_to_now_testing(self):
        self.candidate()
        repo = self.repo()
        log = self.root / "metro.log"
        log.write_text("Historical SyntaxError ignored\n")
        clock = Clock()
        now = self.root / "now-testing.jsonl"
        def build(target):
            self.assertEqual((target / "feature.txt").read_text(), "new\n")
        rows = queue.queue(self.run_dir, repo, repo, metro_log=log, now_testing=now,
                           build=build, sleep=clock.sleep, simulated=True)
        self.assertEqual(rows[0]["state"], "now_testing")
        self.assertEqual((repo / "feature.txt").read_text(), "new\n")
        self.assertEqual(json.loads(now.read_text())["owner_words"], META[URL]["owner_words"])
        self.assertEqual(clock.now, 20)
        again = queue.queue(self.run_dir, repo, repo, metro_log=log, now_testing=now,
                            build=lambda _: self.fail("duplicate queue"), sleep=clock.sleep, simulated=True)
        self.assertEqual(rows, again)

    def test_queue_redbox_reverts_exact_candidate(self):
        self.candidate()
        repo = self.repo()
        original = queue.git(repo, "rev-parse", "HEAD^{tree}")
        log = self.root / "metro.log"
        log.write_text("")
        def sleep(seconds):
            with log.open("a") as out:
                out.write("TypeError: simulated Metro redbox\n")
        now = self.root / "now-testing.jsonl"
        rows = queue.queue(self.run_dir, repo, repo, metro_log=log, now_testing=now,
                           build=lambda _: None, sleep=sleep, simulated=True)
        self.assertEqual(rows[0]["state"], "reverted")
        self.assertEqual(queue.git(repo, "rev-parse", "HEAD^{tree}"), original)
        self.assertFalse(now.exists())

    def test_queue_build_failure_never_merges(self):
        self.candidate()
        repo = self.repo()
        original = queue.git(repo, "rev-parse", "HEAD")
        def fail(_):
            raise RuntimeError("export failed")
        rows = queue.queue(self.run_dir, repo, repo, build=fail, simulated=True)
        self.assertEqual(rows[0]["state"], "rejected")
        self.assertEqual(queue.git(repo, "rev-parse", "HEAD"), original)

    def test_simulation_cannot_enter_live_preview(self):
        self.candidate()
        repo = self.repo()
        with self.assertRaisesRegex(ValueError, "simulation"):
            queue.queue(self.run_dir, repo, repo)

    def test_shared_account_cap_across_runs_without_real_device(self):
        self.make()
        clock = Clock()
        with patch.object(app, "account_root", return_value=self.root / "account"):
            app.launch(self.run_dir, app.SimDevice(FIXTURE), opt_in=True, concurrency=1,
                       clock=clock, sleep=clock.sleep)
            another = self.root / "another"
            app.plan(another, 1, [URL], metadata=META)
            device = app.SimDevice(FIXTURE)
            device.dump = lambda: self.fail("shared cap must precede device dump")
            rows = app.launch(another, device, opt_in=True, concurrency=1,
                              clock=clock, sleep=clock.sleep)
            self.assertEqual(rows, [])
            self.assertEqual(len(app.load(self.root / "account/launches.json")), 1)

    def test_reply_dump_requires_unique_tag_and_final_state(self):
        device = app.SimDevice(FIXTURE)
        device.state = "sent"
        device.text = "No tag"
        with self.assertRaises(ValueError):
            app.replies_from_dump(device.dump())
        device.text = "[fanout:smoke:1] final reply"
        self.assertIn("[fanout:smoke:1]", app.replies_from_dump(device.dump()))
        with self.assertRaisesRegex(ValueError, "generating"):
            app.replies_from_dump(device.dump().replace("Task started", "Stop generating"))

    def test_paginated_branch_harvest(self):
        self.make()
        def gh(argv, **kwargs):
            if "/pulls?" in argv[-1]:
                return "[[]]"
            return json.dumps([[{"name": "fanout-smoke-1", "commit": {"sha": "a" * 40}}]])
        with patch.object(app, "command", side_effect=gh):
            candidates = app.harvest(self.run_dir)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["revision"], "a" * 40)

    def test_rate_limit_persists_stop(self):
        self.make()
        device = app.SimDevice(FIXTURE)
        device.frames["home"] = device.frames["home"].replace("New chat", "You've reached your limit")
        with self.assertRaisesRegex(RuntimeError, "rate_limit"):
            app.launch(self.run_dir, device, dry_run=True)
        self.assertTrue((self.run_dir / "STOP").exists())

    def test_cli_s25_gate_before_any_external_command(self):
        self.make()
        p = argparse.ArgumentParser()
        app.register(p.add_subparsers())
        args = p.parse_args(["app-fanout", "launch", "--run-dir", str(self.run_dir),
                             "--device", "s25", "--opt-in"])
        with patch.object(app, "command", side_effect=AssertionError("must not call adb")), \
             patch.object(app, "U2Device", side_effect=AssertionError("must not connect")):
            self.assertEqual(app.main(args), 2)

    def test_interrupted_queue_requires_reconciliation(self):
        self.candidate()
        repo = self.repo()
        app.save(self.run_dir / "queue.json", [{"tag": "[fanout:smoke:1]", "state": "checking"}])
        with self.assertRaisesRegex(RuntimeError, "reconcile"):
            queue.queue(self.run_dir, repo, repo, simulated=True)

    def test_cli_registration(self):
        from z0int.turbo import cli
        p = argparse.ArgumentParser()
        cli.register(p.add_subparsers(dest="cmd"))
        args = p.parse_args(["turbo", "app-fanout", "stop", "--run-dir", str(self.run_dir)])
        self.assertEqual(cli.main(args), 0)
        self.assertTrue((self.run_dir / "STOP").exists())


if __name__ == "__main__":
    unittest.main()
