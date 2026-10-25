"""Unit tests for the offline collector. Do not access the real user corpus."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sqlite3
from contextlib import closing
import subprocess
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("look_first_generator_under_test", ROOT / "scripts/look-first/generate.py")
assert SPEC and SPEC.loader
GEN = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GEN)
COLLECT = GEN._collect


def manifest():
    return {"errors": [], "local": [], "github": [], "skills": []}


def envelope(items=(), complete=True):
    return {"endpoint": "fixture", "items": list(items), "pages": 1, "complete": complete, "error": None}


def github_fixture(cache, repo="fixture/repo", *, issues=(), pulls=(), comments=(), inline=(), reviews=None):
    root = cache / "github" / repo
    for name, items in (("issues", issues), ("pulls", pulls), ("issue-comments", comments), ("review-comments", inline)):
        COLLECT.dump_json(root / f"{name}.json", envelope(items))
    for number, items in (reviews or {}).items():
        COLLECT.dump_json(root / "reviews" / f"{number}.json", envelope(items))
    return root


def make_repo(root):
    root.mkdir()
    for args in (("init", "-b", "main"), ("config", "user.email", "fixture@example.invalid"), ("config", "user.name", "Fixture"), ("remote", "add", "origin", "https://github.com/fixture/repo.git")):
        GEN.git(root, *args)
    (root / "docs").mkdir()
    (root / "docs/capability.md").write_text("# Cached allocation\nUse the registered allocator.\n", encoding="utf-8")
    (root / "src").mkdir()
    (root / "src/allocator.py").write_text('"""Registered allocation method."""\ndef allocate(state):\n    """Select the cheapest eligible offer."""\n    return state\n', encoding="utf-8")
    GEN.git(root, "add", ".")
    GEN.git(root, "commit", "-m", "fixture")
    return root


class Pagination(unittest.TestCase):
    def test_pagination_exhausts_all_pages(self):
        first = [{"id": number} for number in range(100)]
        with mock.patch.object(COLLECT, "run", side_effect=[json.dumps(first), '[{"id":100}]']) as run:
            result = COLLECT.fetch_pages("repos/fixture/repo/issues?state=all", timeout=7)
        self.assertTrue(result["complete"])
        self.assertEqual(len(result["items"]), 101)
        self.assertEqual(result["pages"], 2)
        self.assertIn("page=2", run.call_args_list[1].args[0][-1])
        self.assertEqual(run.call_args.kwargs["timeout"], 7)

    def test_failed_page_is_explicitly_incomplete(self):
        with mock.patch.object(COLLECT, "run", side_effect=[json.dumps([{"id": i} for i in range(100)]), subprocess.TimeoutExpired("gh", 4)]):
            result = COLLECT.fetch_pages("repos/fixture/repo/issues", timeout=4)
        self.assertFalse(result["complete"])
        self.assertEqual(len(result["items"]), 100)
        self.assertIn("TimeoutExpired", result["error"])

    def test_nonarray_response_is_incomplete(self):
        with mock.patch.object(COLLECT, "run", return_value='{"message":"unexpected"}'):
            result = COLLECT.fetch_pages("repos/fixture/repo/issues")
        self.assertFalse(result["complete"])

    def test_every_pull_review_endpoint_is_collected(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            with mock.patch.object(COLLECT, "fetch_pages", side_effect=[envelope(), envelope([{"number": 3}, {"number": 2}]), envelope(), envelope(), envelope(), envelope()]) as fetch:
                result = COLLECT.collect_repo("fixture/repo", cache)
            self.assertTrue(result["complete"])
            self.assertEqual(len(fetch.call_args_list), 6)
            self.assertTrue((cache / "github/fixture/repo/reviews/2.json").is_file())
            self.assertIn("pulls/2/reviews", fetch.call_args_list[4].args[0])

    def test_subprocess_timeout_is_always_present(self):
        result = subprocess.CompletedProcess(["git"], 0, b"value", b"")
        with mock.patch.object(COLLECT.subprocess, "run", return_value=result) as run:
            self.assertEqual(COLLECT.run(["git"], timeout=9), "value")
        self.assertEqual(run.call_args.kwargs["timeout"], 9)


class RemoteRecords(unittest.TestCase):
    def test_canonical_urls_and_conservative_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            github_fixture(cache, issues=[{"number": 1, "title": "Closed idea", "state": "closed", "body": "Not implemented", "html_url": "https://github.com/fixture/repo/issues/1"}, {"number": 2, "pull_request": {}, "body": "duplicate"}], pulls=[{"number": 2, "title": "Merged method", "body": "Actual method\nsecond line", "merged_at": "2026-01-01", "html_url": "https://github.com/fixture/repo/pull/2"}], comments=[{"id": 4, "body": "Merged discussion", "issue_url": "https://api.github.com/repos/fixture/repo/issues/2", "html_url": "https://github.com/fixture/repo/pull/2#issuecomment-4"}], reviews={2: [{"id": 5, "body": "Review body", "html_url": "https://github.com/fixture/repo/pull/2#pullrequestreview-5"}]})
            report = manifest()
            rows = list(GEN.remote_records({"github": "fixture/repo"}, cache, report))
            self.assertTrue(report["github"][0]["complete"])
            self.assertFalse(report["errors"])
            self.assertTrue(all(row["wired"] == "unverified" for row in rows))
            self.assertEqual(next(row for row in rows if row["text"] == "Not implemented")["status"], "proposal")
            self.assertEqual(next(row for row in rows if row["text"] == "Review body")["status"], "merged")
            self.assertFalse(any(row["text"] == "duplicate" for row in rows))
            self.assertTrue(all("\n" not in row["text"] for row in rows))

    def test_missing_review_cache_prevents_complete_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            github_fixture(cache, pulls=[{"number": 8, "title": "Feature", "body": "Method", "html_url": "https://github.com/fixture/repo/pull/8"}])
            report = manifest()
            rows = list(GEN.remote_records({"github": "fixture/repo"}, cache, report))
            self.assertFalse(report["github"][0]["complete"])
            self.assertTrue(rows)
            self.assertTrue(any("reviews/8" in item["scope"] for item in report["errors"]))

    def test_stale_unenumerated_reviews_do_not_enter_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            github_fixture(cache, reviews={99: [{"id": 5, "body": "stale"}]})
            report = manifest()
            self.assertEqual(list(GEN.remote_records({"github": "fixture/repo"}, cache, report)), [])


class SkillsAndWiring(unittest.TestCase):
    def test_nested_symlink_and_archived_skills_are_enumerated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "clean/skills"
            nested = root / "research/allocator"
            archived = root / ".archive/old"
            external = Path(tmp) / "external"
            for path in (nested, archived, external):
                path.mkdir(parents=True)
                (path / "SKILL.md").write_text(f"---\nname: {path.name}\ndescription: Useful metadata\n---\n# Run allocation\nUse the registered formula.\n", encoding="utf-8")
            (root / "linked").symlink_to(external, target_is_directory=True)
            report = manifest()
            rows = list(GEN.collect_skills([root], [], report))
            self.assertEqual(report["skills"][0]["enumerated"], 3)
            self.assertEqual(report["skills"][0]["indexed"], 3)
            self.assertTrue(report["skills"][0]["complete"])
            self.assertTrue(any(item["archived"] for item in report["skills"][0]["skills"]))
            linked_rows = [row for row in rows if "external" in row["source"]]
            self.assertTrue(linked_rows)
            self.assertTrue(all(row["status"] == "live" and row["wired"].startswith("link:") for row in linked_rows))
            self.assertTrue(all(row["status"] == "branch-only" for row in rows if "/.archive/" in row["source"]))
            self.assertTrue(any("Useful metadata" in row["text"] for row in rows))
            self.assertTrue(all(Path(row["source"].rsplit(":", 1)[0]).is_absolute() for row in rows))

    def test_cycles_and_missing_roots_are_manifest_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "skills"
            root.mkdir()
            (root / "cycle").symlink_to(root, target_is_directory=True)
            report = manifest()
            list(GEN.collect_skills([root, Path(tmp) / "missing"], [], report))
            self.assertEqual(len(report["errors"]), 2)
            self.assertTrue(all(not item["complete"] for item in report["skills"]))

    def test_only_exact_installed_bytes_receive_live_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            source.mkdir()
            entry = source / "index.ts"
            entry.write_text("export const allocate = () => 1;\n", encoding="utf-8")
            links = root / "extensions"
            links.mkdir()
            (links / "allocator").symlink_to(source, target_is_directory=True)
            report = manifest()
            evidence = GEN.wiring_evidence({"link_roots": [links], "units": [], "wiring": []}, report)
            self.assertIn("link:", GEN.wired_for(entry, entry.read_text(), evidence))
            self.assertEqual(GEN.wired_for(entry, "different bytes", evidence), "unverified")
            unrelated = root / "other.py"
            unrelated.write_text("def allocate(): pass\n", encoding="utf-8")
            self.assertEqual(GEN.wired_for(unrelated, unrelated.read_text(), evidence), "unverified")

    def test_unrelated_config_line_does_not_prove_wiring(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "method.py"
            target.write_text("def method(): pass\n", encoding="utf-8")
            config = root / "config.toml"
            config.write_text("enabled=true\n", encoding="utf-8")
            report = manifest()
            evidence = GEN.wiring_evidence({"link_roots": [], "units": [], "wiring": [{"kind": "config", "path": config, "target": target, "contains": "enabled=true"}]}, report)
            self.assertEqual(evidence, [])
            self.assertTrue(report["errors"])


class IndexAndBranches(unittest.TestCase):
    def test_jsonl_and_sqlite_are_deterministic_and_searchable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            row = GEN.record("allocation", "/fixture/source.py:2", "branch-only", "unverified", "module", "Use registered allocator\nnever guess")
            GEN.write_index([row, row], root)
            first_json = (root / "capability-index.jsonl").read_bytes()
            first_db = (root / "capability-index.sqlite3").read_bytes()
            GEN.write_index([row], root)
            self.assertEqual(first_json, (root / "capability-index.jsonl").read_bytes())
            self.assertEqual(first_db, (root / "capability-index.sqlite3").read_bytes())
            with closing(sqlite3.connect(root / "capability-index.sqlite3")) as db:
                self.assertEqual(db.execute("SELECT count(*) FROM entries").fetchone()[0], 1)
                self.assertEqual(db.execute("SELECT method FROM entries_fts WHERE entries_fts MATCH 'allocator'").fetchone()[0], "allocation")
                self.assertEqual([row[1] for row in db.execute("PRAGMA table_info(entries)")], ["id", *GEN.FIELDS])
            self.assertEqual(len(first_json.splitlines()), 1)

    def test_all_local_branches_have_real_line_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = make_repo(root / "repo")
            GEN.git(repo, "checkout", "-b", "feature")
            (repo / "docs/held.md").write_text("# Held method\nDo not install automatically.\n", encoding="utf-8")
            GEN.git(repo, "add", ".")
            GEN.git(repo, "commit", "-m", "held")
            GEN.git(repo, "checkout", "main")
            report = manifest()
            rows = list(GEN.collect_local({"name": "repo", "paths": [repo]}, root / "cache", [], report))
            self.assertFalse(report["errors"])
            self.assertTrue(report["local"][0]["complete"])
            held = next(row for row in rows if "Do not install automatically." in row["text"])
            self.assertEqual(held["status"], "branch-only")
            self.assertTrue(Path(held["source"].rsplit(":", 1)[0]).is_file())
            self.assertTrue(any(row["status"] == "merged" and "registered allocator" in row["text"] for row in rows))
            self.assertEqual({item["ref"] for item in report["local"][0]["branches"]}, {"HEAD", "refs/heads/main", "refs/heads/feature"})

    def test_registry_scope_and_offline_generation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = make_repo(root / "repo")
            registry = root / "registry.json"
            COLLECT.dump_json(registry, {"projects": [{"id": "repo", "coordination_repo": "fixture/repo", "path": str(repo)}, {"id": "unrelated", "coordination_repo": "fixture/unrelated"}]})
            config = root / "roots.json"
            COLLECT.dump_json(config, {"scope": ["repo"], "registry_paths": [str(registry)], "search_roots": [], "repositories": [], "skill_roots": [], "link_roots": [], "units": []})
            cache = root / "cache"
            github_fixture(cache)
            with mock.patch.object(COLLECT, "collect_repo", side_effect=AssertionError("Offline run attempted network")):
                result = GEN.generate(config_path=config, output_dir=root / "out", cache_dir=cache, checkout=repo)
            self.assertTrue(result["complete"])
            self.assertFalse(result["installation"]["applied"])
            self.assertEqual(len(result["local"]), 1)
            self.assertEqual(result["registries"][0]["records"], 2)
            self.assertTrue((root / "out/capability-index.manifest.json").is_file())

    def test_missing_scope_is_not_reported_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = make_repo(root / "repo")
            config = root / "roots.json"
            COLLECT.dump_json(config, {"scope": ["missing"], "repositories": [], "skill_roots": [], "link_roots": [], "units": []})
            result = GEN.generate(config_path=config, output_dir=root / "out", cache_dir=root / "cache", checkout=repo)
            self.assertFalse(result["complete"])
            self.assertTrue(result["errors"])
            self.assertEqual(result["index"]["entries"], 0)


class HeldRegistryReuse(unittest.TestCase):
    def test_held_inventory_is_a_scoped_discovery_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = make_repo(root / "repo")
            registry = root / "held-inventory.json"
            COLLECT.dump_json(registry, [
                {"name": "repo", "id": "fixture/repo", "local_paths": [{"path": str(repo), "content_excluded": False}], "purpose_quotes": ["Private metadata must not enter the capability text"]},
                {"name": "unrelated", "id": "fixture/unrelated", "local_paths": [{"path": str(root / "unrelated")}]},
            ])
            config = root / "roots.json"
            COLLECT.dump_json(config, {"scope": ["repo"], "registry_paths": [str(registry)], "search_roots": [], "repositories": [], "skill_roots": [], "link_roots": [], "units": []})
            report = manifest()
            parsed = GEN.config_roots(config, repo, report)
            self.assertFalse(report["errors"])
            self.assertEqual(parsed["repositories"][0]["github"], "fixture/repo")
            self.assertEqual(parsed["repositories"][0]["paths"], [repo.resolve()])
            self.assertEqual(report["registries"][0]["scoped_records"], 1)
            self.assertNotIn("purpose_quotes", parsed["repositories"][0])

    def test_stack_manifest_is_selected_without_selecting_private_inventory(self):
        self.assertTrue(GEN.selected("stack.toml"))
        self.assertTrue(GEN.selected("install.py"))
        self.assertFalse(GEN.selected("repository-inventory.json"))
        self.assertFalse(GEN.selected("secrets/README.md"))


class CompleteEnumeration(unittest.TestCase):
    def test_remote_only_branch_is_collected_without_fetch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = make_repo(root / "repo")
            GEN.git(repo, "checkout", "-b", "temporary")
            (repo / "docs/remote-only.md").write_text("# Semantic skill selection\nSelect from the unified skill manifest.\n", encoding="utf-8")
            GEN.git(repo, "add", ".")
            GEN.git(repo, "commit", "-m", "remote-only fixture")
            commit = GEN.git(repo, "rev-parse", "HEAD").strip()
            GEN.git(repo, "update-ref", "refs/remotes/origin/exp/semantic-skill-select", commit)
            GEN.git(repo, "checkout", "main")
            # Remove only the temporary branch created by this test.
            GEN.git(repo, "update-ref", "-d", "refs/heads/temporary")
            report = manifest()
            rows = list(GEN.collect_local({"name": "repo", "paths": [repo]}, root / "cache", [], report))
            self.assertFalse(report["errors"])
            self.assertTrue(any("unified skill manifest" in row["text"] for row in rows))
            self.assertTrue(any(item["ref"] == "refs/remotes/origin/exp/semantic-skill-select" for item in report["local"][0]["branches"]))
            self.assertTrue(any(item["deduplicated_commit"] for item in report["local"][0]["branches"]))

    def test_dangling_skill_uses_historical_metadata_without_live_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skills = root / "clean/skills"
            skills.mkdir(parents=True)
            (skills / "allocator").symlink_to(root / "gone", target_is_directory=True)
            snapshot = root / "snapshot.json"
            COLLECT.dump_json(snapshot, {"skills": [{"name": "allocator", "path": str(skills / "allocator/SKILL.md"), "description": "Select registered offers."}]})
            report = manifest()
            metadata = GEN.skill_metadata([snapshot], report)
            rows = list(GEN.collect_skills([skills], [], report, metadata, root / "cache"))
            self.assertEqual(report["skills"][0]["enumerated"], 1)
            self.assertEqual(report["skills"][0]["indexed"], 1)
            self.assertFalse(report["skills"][0]["complete"])
            self.assertEqual(rows[0]["status"], "branch-only")
            self.assertTrue(rows[0]["wired"].startswith("dangling-link:"))
            self.assertIn("Select registered offers.", rows[0]["text"])
            self.assertTrue(Path(rows[0]["source"].rsplit(":", 1)[0]).is_file())

    def test_collector_cli_is_offline_without_refresh_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            COLLECT.dump_json(cache / "github/fixture/repo/manifest.json", {"repo": "fixture/repo", "complete": True})
            with mock.patch.object(COLLECT, "collect_repo", side_effect=AssertionError("Unexpected remote access")):
                with mock.patch("builtins.print"):
                    self.assertEqual(COLLECT.main(["--repo", "fixture/repo", "--cache-dir", str(cache)]), 0)


if __name__ == "__main__":
    unittest.main()
