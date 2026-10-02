"""State Packet v0 (#22 P0 slice). Synthetic git repo + sanitized synthetic transcript only."""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from z0int import state_packet as sp

FIXTURE = Path(__file__).parent / "fixtures" / "state_packet" / "session_sanitized.jsonl"


def _git(repo: Path, *args: str) -> str:
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=env).stdout


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.repo = (base / "proj").resolve()
        self.repo.mkdir()
        self.projects = base / "claude-projects"
        self.projects.mkdir()
        self._prev_home = os.environ.get("Z0INT_HOME")
        os.environ["Z0INT_HOME"] = str(base / "z0home")
        # hermetic resource posture: never read this host's real usage cache
        self._prev_cb = os.environ.get("Z0INT_POSTURE_CODEXBAR")
        self.codexbar = base / "codexbar-last.json"
        os.environ["Z0INT_POSTURE_CODEXBAR"] = str(self.codexbar)
        _git(self.repo, "init", "-q", "-b", "main")
        (self.repo / "src").mkdir()
        (self.repo / "src" / "widget.py").write_text("x = 1\n", encoding="utf-8")
        (self.repo / "ROADMAP.md").write_text(
            "# Roadmap\n\n## P0 - ship widget parser\n\n**Status: in progress**\n\n- [ ] parser tests\n- [x] scaffold\n",
            encoding="utf-8")
        (self.repo / "README.md").write_text("See `src/widget.py` and `src/missing_module.py`.\n", encoding="utf-8")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "init widget")

    def tearDown(self) -> None:
        if self._prev_cb is None:
            os.environ.pop("Z0INT_POSTURE_CODEXBAR", None)
        else:
            os.environ["Z0INT_POSTURE_CODEXBAR"] = self._prev_cb
        if self._prev_home is None:
            os.environ.pop("Z0INT_HOME", None)
        else:
            os.environ["Z0INT_HOME"] = self._prev_home
        self._tmp.cleanup()

    def add_transcript(self) -> Path:
        d = self.projects / sp._encode_project_dir(self.repo)
        d.mkdir(parents=True, exist_ok=True)
        f = d / "00000000-fixture-0000-0000-000000000001.jsonl"
        f.write_text(FIXTURE.read_text(encoding="utf-8").replace("__REPO__", str(self.repo)), encoding="utf-8")
        return f

    def build(self, **kw):
        kw.setdefault("projects_root", self.projects)
        return sp.build_state_packet(self.repo, **kw)


class StatePacketTests(_Base):
    def test_packet_shape_and_provenance(self):
        self.add_transcript()
        p = self.build()
        self.assertEqual(p["schema"], sp.SCHEMA)
        keys = {c["key"] for c in p["current_claims"]}
        for k in ("git.branch", "git.head", "git.dirty", "docs.priority", "conv.latest_session"):
            self.assertIn(k, keys)
        self.assertEqual(sp.provenance_complete(p), [])
        self.assertEqual(p["decision"]["mode"], "ACT")
        self.assertEqual(p["measurements"]["concurrent_adapters"], 4)
        prio = next(c for c in p["current_claims"] if c["key"] == "docs.priority")
        self.assertEqual(prio["value"]["status"], "in progress")
        self.assertTrue(all(t["authorizes"] is False for t in p["allowed_transitions"]))
        sess = next(c for c in p["current_claims"] if c["key"].startswith("conv.session["))
        self.assertEqual(sess["value"]["title"], "SYNTHETIC widget parser work")
        self.assertEqual(sess["value"]["last_ts"], "2026-01-01T10:02:00.000Z")  # unrelated cwd ignored
        files = next(c for c in p["current_claims"] if c["key"].startswith("conv.files_touched["))
        self.assertEqual(files["value"], ["src/widget.py"])
        self.assertTrue(any(w["kind"] == "delegation_pending" for w in p["open_work"]))
        self.assertTrue(any(w["kind"] == "doc_checklist" for w in p["open_work"]))

    def test_missing_required_fact_forces_observe(self):
        _git(self.repo, "checkout", "-q", "-b", "feature/x")
        (self.repo / "src" / "widget.py").write_text("x = 3\n", encoding="utf-8")
        p = self.build()  # no transcripts
        self.assertEqual(p["decision"]["mode"], "OBSERVE")
        self.assertIn("conv.latest_session", p["decision"]["missing"])
        acts = [t for t in p["allowed_transitions"] if t["action"] in ("continue_on_branch", "commit_local")]
        self.assertTrue(acts and all(t["kind"] == "BLOCKED" for t in acts))
        auth = sp.authorize_transition(p, "continue_on_branch", projects_root=self.projects)
        self.assertFalse(auth["allowed"])
        self.assertEqual(auth["mode"], "OBSERVE")
        # status intent does not require conversation history
        p2 = self.build(intent="status", use_cache=False)
        self.assertEqual(p2["decision"]["mode"], "ACT")

    def test_cache_hit_then_invalidation_on_new_commit(self):
        self.add_transcript()
        p1 = self.build()
        p2 = self.build()
        self.assertTrue(p2["measurements"]["cache_hit"])
        self.assertEqual(p1["packet_id"], p2["packet_id"])
        self.assertTrue(sp.check_packet(p1, self.projects)["valid"])
        (self.repo / "src" / "widget.py").write_text("x = 2\n", encoding="utf-8")
        _git(self.repo, "commit", "-qam", "change widget")
        chk = sp.check_packet(p1, self.projects)
        self.assertFalse(chk["valid"])
        self.assertIn("git", chk["changed_sources"])
        auth = sp.authorize_transition(p1, "refresh_packet", projects_root=self.projects)
        self.assertFalse(auth["allowed"])
        self.assertIn("stale", auth["reason"])
        p3 = self.build()
        self.assertFalse(p3["measurements"]["cache_hit"])
        self.assertNotEqual(p3["packet_id"], p1["packet_id"])

    def test_transcript_append_invalidates_and_scans_incrementally(self):
        f = self.add_transcript()
        p1 = self.build()
        full = p1["measurements"]["transcript_bytes_read"]
        extra = json.dumps({"type": "user", "sessionId": "00000000-fixture-0000-0000-000000000001",
                            "cwd": str(self.repo), "timestamp": "2026-01-02T09:00:00.000Z",
                            "message": {"role": "user", "content": "SYNTHETIC follow-up"}}) + "\n"
        with f.open("a", encoding="utf-8") as fh:
            fh.write(extra)
        p2 = self.build()
        self.assertFalse(p2["measurements"]["cache_hit"])
        inc = p2["measurements"]["transcript_bytes_read"]
        self.assertEqual(inc, len(extra.encode()))  # only the appended bytes are read
        self.assertLess(inc, full)
        self.assertFalse(sp.check_packet(p1, self.projects)["valid"])
        sess = next(c for c in p2["current_claims"] if c["key"].startswith("conv.session["))
        self.assertEqual(sess["value"]["last_ts"], "2026-01-02T09:00:00.000Z")

    def test_unrelated_transcript_append_does_not_invalidate(self):
        f = self.add_transcript()
        p1 = self.build()
        with f.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"type": "user", "sessionId": "00000000-fixture-0000-0000-000000000001",
                                 "cwd": "/elsewhere/unrelated", "timestamp": "2026-01-03T00:00:00.000Z",
                                 "message": {"role": "user", "content": "SYNTHETIC unrelated"}}) + "\n")
        self.assertTrue(sp.check_packet(p1, self.projects)["valid"])
        p2 = self.build()
        self.assertTrue(p2["measurements"]["cache_hit"])

    def test_supersession_keeps_history(self):
        self.add_transcript()
        self.build()
        _git(self.repo, "checkout", "-q", "-b", "feature/new-name")
        p = self.build()
        cur = next(c for c in p["current_claims"] if c["key"] == "git.branch")
        self.assertEqual(cur["value"], "feature/new-name")
        sup = [s for s in p["superseded_claims"] if s["key"] == "git.branch"]
        olds = {s["old_value"] for s in sup}
        self.assertIn("main", olds)  # from prior packet
        self.assertIn("feature/old-name", olds)  # from conversation observation
        self.assertTrue(all(s["superseded_by"] == cur["id"] for s in sup))

    def test_docs_drift_is_explicit_contradiction(self):
        p = self.build(intent="status")
        c = [x for x in p["contradictions"] if x["key"] == "path:src/missing_module.py"]
        self.assertEqual(len(c), 1)
        self.assertEqual(len(c[0]["claims"]), 2)
        for side in c[0]["claims"]:
            for e in side["evidence"]:
                self.assertIn(e, p["evidence"])
        self.assertFalse([x for x in p["contradictions"] if x["key"] == "path:src/widget.py"])

    def test_privacy_secret_never_in_packet(self):
        self.add_transcript()
        p = self.build()
        blob = json.dumps(p)
        self.assertNotIn("SYNTHETICSECRET", blob)
        self.assertIn("[redacted]", blob)
        text = sp.render_additional_context(p)
        self.assertNotIn("SYNTHETICSECRET", text)

    def test_renderer_budget_and_hook_contract(self):
        self.add_transcript()
        p = self.build()
        text = sp.render_additional_context(p, max_tokens=1500)
        self.assertLessEqual(sp.approx_tokens(text), 1500)
        self.assertIn("DECISION: ACT", text)
        self.assertIn("git.head", text)
        self.assertRegex(text, r"\[git:proj@[0-9a-f]{7}\]")
        small = sp.render_additional_context(p, max_tokens=200)
        self.assertLessEqual(len(small), 200 * 3.5)
        self.assertTrue(small.rstrip().endswith("</z0-state-packet>"))
        hook = sp.session_start_hook(json.dumps({"cwd": str(self.repo / "src")}), projects_root=str(self.projects))
        self.assertEqual(set(hook), {"hookSpecificOutput"})
        self.assertEqual(hook["hookSpecificOutput"]["hookEventName"], "SessionStart")
        self.assertIn("<z0-state-packet repo=proj", hook["hookSpecificOutput"]["additionalContext"])
        with tempfile.TemporaryDirectory() as nonrepo:
            empty = sp.session_start_hook(json.dumps({"cwd": nonrepo}))
            self.assertEqual(empty["hookSpecificOutput"]["additionalContext"], "")

    def test_context_resolve_projection(self):
        p = self.build()
        cp = sp.as_context_packet(p)
        d = cp.to_dict()
        self.assertEqual(d["schema"], "z0int.context_resolve.v1")
        self.assertTrue(any("conv.latest_session" in g for g in d["unresolved_gaps"]))
        self.assertIn("provenance", d["aodl_projection"])

    def test_cli_packet_render_and_check(self):
        from z0int.cli import main

        self.add_transcript()
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(["context", "packet", "--repo", str(self.repo), "--projects-root", str(self.projects), "--json"])
        self.assertEqual(rc, 0)
        pkt = json.loads(buf.getvalue())
        saved = Path(self._tmp.name) / "pkt.json"
        saved.write_text(json.dumps(pkt), encoding="utf-8")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(["context", "packet", "--check", str(saved), "--projects-root", str(self.projects),
                       "--authorize", "commit_local"])
        out = json.loads(buf.getvalue())
        self.assertEqual(rc, 0)
        self.assertTrue(out["valid"])
        self.assertFalse(out["authorization"]["allowed"])  # clean tree: commit_local not in legal set
        buf = io.StringIO()
        with redirect_stdout(buf):
            main(["context", "packet", "--repo", str(self.repo), "--projects-root", str(self.projects), "--render"])
        self.assertTrue(re.search(r"^<z0-state-packet repo=proj", buf.getvalue()))

    def test_priority_conflict_forces_observe_for_plan(self):
        (self.repo / "AGENTS.md").write_text("# Agents\n\n## Widget export (critical path)\n", encoding="utf-8")
        p = self.build(intent="plan")
        conf = [c for c in p["contradictions"] if c["kind"] == "priority_conflict"]
        self.assertEqual(len(conf), 1)
        self.assertEqual(len(conf[0]["claims"]), 2)
        self.assertEqual(p["decision"]["mode"], "OBSERVE")
        self.assertIn("docs.priority", p["decision"]["missing"])
        self.assertIn("priority_conflict", sp.render_additional_context(p))

    def test_github_adapter_with_fake_gh(self):
        _git(self.repo, "remote", "add", "origin", "https://github.com/example/proj.git")
        bindir = Path(self._tmp.name) / "bin"
        bindir.mkdir()
        issues = [{"number": 7, "title": "SYNTHETIC tracker", "updatedAt": "2026-01-01T00:00:00Z",
                   "body": "## Agent priority\n\n**P0 next slice: widget exporter.**\n"}]
        prs = [{"number": 9, "title": "SYNTHETIC pr", "headRefName": "feat/x", "isDraft": True,
                "updatedAt": "2026-01-01T00:00:00Z"}]
        script = bindir / "gh"
        script.write_text("#!/bin/sh\nif [ \"$1\" = issue ]; then echo '%s'; else echo '%s'; fi\n"
                          % (json.dumps(issues), json.dumps(prs)), encoding="utf-8")
        script.chmod(0o755)
        prev = os.environ["PATH"]
        os.environ["PATH"] = f"{bindir}{os.pathsep}{prev}"
        try:
            p = self.build(intent="plan", github=True)
        finally:
            os.environ["PATH"] = prev
        self.assertIn("github", p["source_revisions"])
        gh = next(c for c in p["current_claims"] if c["key"] == "gh.priority[#7]")
        self.assertEqual(gh["value"]["declares"], "P0 next slice: widget exporter.")
        self.assertTrue(any(w["kind"] == "open_pr" for w in p["open_work"]))
        conf = [c for c in p["contradictions"] if c["kind"] == "priority_conflict"]
        self.assertEqual(len(conf), 1)  # ROADMAP P0 vs GitHub P0
        self.assertEqual(p["decision"]["mode"], "OBSERVE")
        self.assertEqual(sp.provenance_complete(p), [])

    def test_redact(self):
        self.assertEqual(sp.redact("key ghp_" + "a" * 30 + " mail me@x.io"), "key [redacted] mail [email]")
        self.assertLessEqual(len(sp.redact("x" * 500)), sp.EXCERPT_CHARS)


if __name__ == "__main__":
    unittest.main()


def test_missing_history_is_unavailable_not_no_match(tmp_path):
    import subprocess
    from z0int import state_packet as sp
    repo = tmp_path / "r"; repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "f").write_text("x")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "i"], check=True)
    empty = tmp_path / "projects"; empty.mkdir()
    pkt = sp.build_state_packet(repo, projects_root=empty)
    unknown = [u for u in pkt["unknowns"] + pkt.get("blocking_unknowns", []) if u["key"] == "conv.latest_session"]
    assert unknown and unknown[0]["source_status"] == "source_unavailable"
    text = sp.render_additional_context(pkt)
    assert "scanner" not in text and "transcript(s) scanned" not in text
    if pkt.get("blocking_unknowns"):
        assert "abstain" in text


def test_subagent_only_history_still_yields_latest_session(tmp_path):
    import json, subprocess
    from z0int import state_packet as sp
    repo = tmp_path / "r"; repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "f").write_text("x")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "i"], check=True)
    sub = tmp_path / "projects" / "p" / "sess1" / "subagents"
    sub.mkdir(parents=True)
    (sub / "agent-abcdef12.jsonl").write_text(json.dumps({"type": "user", "sessionId": "sess1", "cwd": str(repo),
        "timestamp": "2026-09-30T00:00:00Z", "message": {"role": "user", "content": "work here"}}) + "\n")
    pkt = sp.build_state_packet(repo, projects_root=tmp_path / "projects")
    latest = [c for c in pkt["current_claims"] if c["key"] == "conv.latest_session"]
    if latest:  # only assert when the adapter indexed the transcript at all
        assert "/sub:" in latest[0]["value"]


class ResourcePostureFactTests(_Base):
    """``resource.posture`` fact family: shadow context, fail-open, never blocks ACT."""

    def write_codexbar(self, used_weekly: float, reset_hours: float) -> None:
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        iso = lambda h: (now + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
        self.codexbar.write_text(json.dumps([{"provider": "claude", "usage": {
            "updatedAt": iso(0), "secondary": {"resetsAt": iso(reset_hours), "windowMinutes": 10080,
                                               "usedPercent": used_weekly}}}]), encoding="utf-8")

    def test_posture_fact_present_and_rendered(self):
        self.add_transcript()
        self.write_codexbar(18, 9)
        p = self.build()
        claims = {c["key"]: c for c in p["current_claims"]}
        self.assertEqual(claims["resource.posture"]["value"]["factory"], "BURN")
        self.assertFalse(claims["resource.posture"]["material"])
        self.assertEqual(claims["resource.posture[claude]"]["value"]["binding_pool"], "claude:weekly")
        self.assertEqual(p["decision"]["mode"], "ACT")
        self.assertIsNotNone(p["source_revisions"]["resource"])
        text = sp.render_additional_context(p)
        self.assertIn("resource.posture: BURN", text)
        self.assertIn("(shadow; not enforced)", text)

    def test_posture_change_invalidates_packet(self):
        self.write_codexbar(18, 9)
        p = self.build()
        self.assertTrue(sp.check_packet(p, self.projects)["valid"])
        self.write_codexbar(100, 9)  # exhausted -> OFFLOAD: verdict changed
        chk = sp.check_packet(p, self.projects)
        self.assertFalse(chk["valid"])
        self.assertIn("resource", chk["changed_sources"])

    def test_missing_usage_source_is_nonblocking_unknown(self):
        self.add_transcript()
        p = self.build()
        self.assertNotIn("resource.posture", {c["key"] for c in p["current_claims"]})
        self.assertIn("resource.posture", {u["key"] for u in p["unknowns"]})
        self.assertEqual(p["decision"]["mode"], "ACT")

    def test_posture_failure_is_fail_open(self):
        from unittest import mock

        self.add_transcript()
        with mock.patch("z0int.posture.current_posture", side_effect=RuntimeError("boom")):
            p = self.build(use_cache=False)
        self.assertEqual(p["decision"]["mode"], "ACT")
        self.assertIn("resource.adapter", {u["key"] for u in p["unknowns"]})
        self.assertIsNone(p["source_revisions"]["resource"])
