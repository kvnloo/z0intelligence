"""Real git/worktree and process-loss tests; no model or provider required."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from z0int.continuation import InvalidCheckpoint, StaleCheckpoint
from z0int.task_loop import (
    TaskCheckpoint, authorize_task, checkpoint_path, load_checkpoint, make_fixture_repo,
    record_task_checkpoint, resume_task, run_until, save_checkpoint, step_apply_patch,
    step_worktree, task_continuation,
)


@unittest.skipUnless(os.name == 'posix', 'local task owner uses POSIX flock')
class TaskContinuationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        env = patch.dict(os.environ, {'Z0INT_HOME': str(self.root / 'home')})
        env.start(); self.addCleanup(env.stop)
        self.repo, spec = make_fixture_repo(self.root / 'repo')
        self.cp = authorize_task(base_repo=self.repo, patch=spec)
        # Exercise the real local executor from its resolved boundary. Context/AODL
        # resolution is covered separately by the repository's original test_task_loop.
        self.cp.status = 'resolved'
        save_checkpoint(self.cp)
        self.cp = step_worktree(self.cp, worktrees_root=self.root / 'wts')
        self.target = Path(self.cp.worktree_path) / 'app.py'

    def child(self, code):
        return subprocess.run([sys.executable, '-c', code, self.cp.task_id],
                              text=True, capture_output=True, timeout=20)

    def test_flat_checkpoint_fields_and_generic_roundtrip(self):
        row = json.loads(checkpoint_path(self.cp.task_id).read_text())
        self.assertEqual(row['status'], 'worktree_ready')
        self.assertIn('_continuation', row)
        self.assertNotIn('state', row['_continuation']['metadata'])
        self.assertEqual(load_checkpoint(self.cp.task_id).to_dict(), self.cp.to_dict())
        self.assertEqual(task_continuation(self.cp).body['resume_entrypoint'], 'apply_patch')

    def test_real_patch_verification_and_repeated_resume(self):
        cp = resume_task(self.cp.task_id)
        self.assertTrue(cp.verified_success)
        self.assertIn('BROKEN', (self.repo / 'app.py').read_text())
        self.assertIn('READY', self.target.read_text())
        before = self.target.stat().st_mtime_ns
        self.assertEqual(resume_task(cp.task_id).to_dict(), cp.to_dict())
        self.assertEqual(self.target.stat().st_mtime_ns, before)
        self.assertTrue(task_continuation(cp).body['verified_success'])

    def test_crash_after_write_recovers_without_reapplying(self):
        result = self.child('''
import os, sys
from pathlib import Path
from z0int.task_loop import resume_task
original = Path.write_bytes
def crash(self, data):
    result = original(self, data)
    if self.name == "app.py": os._exit(87)
    return result
Path.write_bytes = crash
resume_task(sys.argv[1])
''')
        self.assertEqual(result.returncode, 87, result.stderr)
        self.assertEqual(load_checkpoint(self.cp.task_id).pending_patch['status'], 'started')
        before = self.target.stat().st_mtime_ns
        cp = resume_task(self.cp.task_id)
        self.assertTrue(cp.verified_success)
        self.assertEqual(cp.pending_patch['status'], 'completed')
        self.assertEqual(self.target.stat().st_mtime_ns, before)
        self.assertTrue(any('git commit not inferred' in n for n in cp.notes))

    def test_crash_before_write_uses_exact_before_image(self):
        result = self.child('''
import os, sys
from pathlib import Path
from z0int.task_loop import resume_task
original = Path.write_bytes
def crash(self, data):
    if self.name == "app.py": os._exit(88)
    return original(self, data)
Path.write_bytes = crash
resume_task(sys.argv[1])
''')
        self.assertEqual(result.returncode, 88, result.stderr)
        self.assertIn('BROKEN', self.target.read_text())
        self.assertTrue(resume_task(self.cp.task_id).verified_success)

    def test_ambiguous_after_crash_never_overwrites(self):
        before = self.target.read_bytes()
        import hashlib
        self.cp.pending_patch = {'status': 'started',
            'before': hashlib.sha256(before).hexdigest(), 'after': 'a' * 64}
        save_checkpoint(self.cp)
        self.target.write_text('EXTERNAL CHANGE\n')
        with self.assertRaisesRegex(InvalidCheckpoint, 'manual reconciliation'):
            resume_task(self.cp.task_id)
        self.assertEqual(self.target.read_text(), 'EXTERNAL CHANGE\n')
        self.assertEqual(load_checkpoint(self.cp.task_id).pending_patch['status'], 'unknown')

    def test_cancelled_task_cannot_resume_pending_patch(self):
        self.cp.status = 'abandoned'
        self.cp.pending_patch = {'status': 'started', 'before': 'a'*64, 'after': 'b'*64}
        save_checkpoint(self.cp)
        result = resume_task(self.cp.task_id)
        self.assertEqual(result.status, 'abandoned')
        self.assertIn('BROKEN', self.target.read_text())

    def test_stale_caller_is_rejected_before_effects(self):
        newer = load_checkpoint(self.cp.task_id)
        newer.notes.append('new decision')
        save_checkpoint(newer)
        with self.assertRaises(StaleCheckpoint):
            run_until(self.cp)
        self.assertIn('BROKEN', self.target.read_text())

    def test_two_process_owners_apply_only_once(self):
        code = 'from z0int.task_loop import resume_task; import sys; assert resume_task(sys.argv[1]).verified_success'
        args = [sys.executable, '-c', code, self.cp.task_id]
        a = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        b = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for proc in (a, b):
            out, err = proc.communicate(timeout=20)
            self.assertEqual(proc.returncode, 0, out + err)
        commits = subprocess.check_output(['git', 'rev-list', '--count',
                          self.cp.base_ref + '..HEAD'], cwd=self.cp.worktree_path, text=True)
        self.assertEqual(commits.strip(), '1')

    def test_source_revision_change_refuses_resume(self):
        source = self.root / 'aodl.json'
        source.write_text('{"policy":1}')
        self.cp.aodl_path = str(source)
        save_checkpoint(self.cp)
        source.write_text('{"policy":2}')
        with self.assertRaises(StaleCheckpoint):
            resume_task(self.cp.task_id)
        self.assertIn('BROKEN', self.target.read_text())

    def test_corruption_and_unsupported_schema_refused(self):
        path = checkpoint_path(self.cp.task_id)
        row = json.loads(path.read_text())
        row['status'] = 'patched'
        path.write_text(json.dumps(row))
        with self.assertRaises(InvalidCheckpoint):
            load_checkpoint(self.cp.task_id)
        path.write_text('{')
        with self.assertRaises(InvalidCheckpoint):
            resume_task(self.cp.task_id)
        row = self.cp.to_dict(); row['schema'] = 'future'
        path.write_text(json.dumps(row))
        with self.assertRaises(InvalidCheckpoint):
            load_checkpoint(self.cp.task_id)

    def test_legacy_v1_load_and_migrate(self):
        path = checkpoint_path(self.cp.task_id)
        row = self.cp.to_dict(); row.pop('pending_patch')
        path.write_text(json.dumps(row))
        cp = load_checkpoint(self.cp.task_id)
        save_checkpoint(cp)
        self.assertIn('_continuation', json.loads(path.read_text()))
        self.assertTrue(resume_task(cp.task_id).verified_success)

    def test_target_escape_refused(self):
        self.cp.patch['relative_path'] = '../outside.py'
        save_checkpoint(self.cp)
        with self.assertRaises(InvalidCheckpoint):
            step_apply_patch(self.cp)
        self.assertIn('BROKEN', self.target.read_text())
        self.target.unlink(); self.target.symlink_to(self.repo / 'app.py')
        self.cp.patch['relative_path'] = 'app.py'
        with self.assertRaises(InvalidCheckpoint):
            step_apply_patch(self.cp)

    def test_invalid_task_id_refused(self):
        for task_id in ('../escape', '/tmp/escape', '', 'a/b'):
            with self.assertRaises(InvalidCheckpoint):
                checkpoint_path(task_id)

    def test_actual_eventlog_admission(self):
        try:
            from z0int.memory.event_log import EventLog
        except ModuleNotFoundError:
            if os.environ.get('GITHUB_ACTIONS') == 'true':
                raise
            self.skipTest('partial local checkout; full checkout CI runs EventLog integration')
        log = EventLog(self.root / 'ledger', blob_threshold=1024*1024)
        cp = resume_task(self.cp.task_id)
        record_task_checkpoint(cp.task_id, log)
        record_task_checkpoint(cp.task_id, log)
        events = list(log.iter_events())
        self.assertEqual(len(events), 2)
        a = events[0].payload['extra']['continuation']
        b = events[1].payload['extra']['continuation']
        self.assertEqual(a['replay_key'], b['replay_key'])
        self.assertTrue(a['verified_success'])
        self.assertEqual(a['work_item_id'], cp.task_id)


if __name__ == '__main__':
    unittest.main()
