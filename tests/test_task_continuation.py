"""Real git/worktree and process-loss tests; no model or provider required."""
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import z0int.task_loop as task_loop
from z0int.continuation import InvalidCheckpoint, StaleCheckpoint
from z0int.task_loop import (
    PatchSpec, TaskCheckpoint, authorize_task, checkpoint_path, list_checkpoints, load_checkpoint,
    make_fixture_repo, record_task_checkpoint, resume_task, run_until, save_checkpoint,
    step_apply_patch, step_worktree, task_continuation,
)

# Fails any non-loopback connection, here and (via exec) in every child process.
SOCKET_GUARD = '''
import socket as _socket
_guarded_connect = _socket.socket.connect
def _loopback_only(sock, address, *args):
    host = address[0] if isinstance(address, tuple) else None
    if sock.family in (_socket.AF_INET, _socket.AF_INET6) and not (
            host == "localhost" or host == "::1" or str(host).startswith("127.")):
        raise AssertionError(f"non-loopback connection refused by test guard: {address!r}")
    return _guarded_connect(sock, address, *args)
_socket.socket.connect = _loopback_only
'''

# Kills the process at one named point around the git steps that follow the patch write.
KILL_AT_GIT_STEP = SOCKET_GUARD + '''
import os, sys
import z0int.task_loop as tl
real_git = tl._run_git
def run_git(args, **kwargs):
    verb = "commit" if "commit" in args else args[0]
    if sys.argv[2] == "before_" + verb: os._exit(90)
    result = real_git(args, **kwargs)
    if sys.argv[2] == "after_" + verb: os._exit(90)
    return result
tl._run_git = run_git
tl.resume_task(sys.argv[1])
'''

# Waits for a shared start signal, then resumes; holds the patch write open so
# that every unserialized resumer reaches it too. Each write of the target is logged.
RACING_RESUMER = SOCKET_GUARD + '''
import os, sys, time
from pathlib import Path
from z0int.task_loop import resume_task
race = Path(sys.argv[2])
original = Path.write_bytes
def logged_slow_write(self, data):
    if self.name == "app.py":
        fd = os.open(race / "writes.log", os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        os.write(fd, f"{os.getpid()}\\n".encode()); os.close(fd)
        time.sleep(0.5)
    return original(self, data)
Path.write_bytes = logged_slow_write
(race / f"ready.{os.getpid()}").touch()
deadline = time.monotonic() + 30
while not (race / "go").exists():
    if time.monotonic() > deadline: sys.exit(3)
    time.sleep(0.001)
cp = resume_task(sys.argv[1])
assert cp.verified_success, cp.last_error
'''


def setUpModule():
    real = socket.socket.connect
    exec(SOCKET_GUARD, {})
    unittest.addModuleCleanup(setattr, socket.socket, 'connect', real)


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

    # --- evidence for single-owner resume, commit completion, refusals, seal ---

    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.cp.worktree_path, text=True)

    def assert_patch_committed_once(self):
        self.assertEqual(self.git('status', '--porcelain'), '')
        self.assertEqual(self.git('rev-list', '--count', self.cp.base_ref + '..HEAD').strip(), '1')
        self.assertIn('READY', self.git('show', 'HEAD:app.py'))
        self.assertIn('BROKEN', (self.repo / 'app.py').read_text())

    def test_socket_guard_refuses_non_loopback(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            with self.assertRaisesRegex(AssertionError, 'non-loopback'):
                sock.connect(('192.0.2.1', 9))

    def test_racing_resumers_write_the_patch_once(self):
        import time
        race = self.root / 'race'
        race.mkdir()
        count = 4
        procs = [subprocess.Popen([sys.executable, '-c', RACING_RESUMER, self.cp.task_id, str(race)],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                 for _ in range(count)]
        try:
            deadline = time.monotonic() + 30
            while len(list(race.glob('ready.*'))) < count:
                self.assertLess(time.monotonic(), deadline, 'resumers did not all start')
                time.sleep(0.005)
            (race / 'go').touch()  # all resumers are imported and waiting: release together
            results = [(p.returncode, *p.communicate(timeout=60)) for p in procs]
            results = [(p.returncode, out, err) for p, (_, out, err) in zip(procs, results)]
        finally:
            for p in procs:
                p.kill()
        writers = (race / 'writes.log').read_text().split()
        self.assertEqual(len(writers), 1, f'{len(writers)} resumers wrote the same patch')
        for code, out, err in results:
            self.assertEqual(code, 0, out + err)
        self.assert_patch_committed_once()

    def kill_points_then_resume(self, *points):
        for point in points:
            result = subprocess.run([sys.executable, '-c', KILL_AT_GIT_STEP, self.cp.task_id, point],
                                    text=True, capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 90, f'kill point {point} not reached: {result.stderr}')
        cp = resume_task(self.cp.task_id)
        self.assertTrue(cp.verified_success, cp.last_error)
        self.assert_patch_committed_once()
        self.assertEqual(resume_task(cp.task_id).to_dict(), cp.to_dict())
        self.assert_patch_committed_once()

    def test_kill_after_write_before_git_add(self):
        self.kill_points_then_resume('before_add')

    def test_kill_after_git_add_before_commit(self):
        self.kill_points_then_resume('after_add')

    def test_kill_after_commit_before_checkpoint(self):
        self.kill_points_then_resume('after_commit')

    def test_kill_again_while_resume_completes_the_commit(self):
        self.kill_points_then_resume('before_add', 'after_add')

    def test_kill_again_after_resume_committed(self):
        self.kill_points_then_resume('before_add', 'after_commit')

    def kill_before_add(self, task_id=None):
        result = subprocess.run([sys.executable, '-c', KILL_AT_GIT_STEP, task_id or self.cp.task_id, 'before_add'],
                                text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 90, result.stderr)

    def assert_nothing_committed_and_not_verified(self):
        cp = resume_task(self.cp.task_id)
        self.assertIsNot(cp.verified_success, True)
        self.assertNotEqual(cp.status, 'verified')
        self.assertEqual(self.git('rev-list', '--count', self.cp.base_ref + '..HEAD').strip(), '0')
        self.assertIn('BROKEN', self.git('show', 'HEAD:app.py'))

    def test_target_swapped_for_a_symlink_is_not_committed(self):
        self.kill_before_add()
        real = self.target.with_name('real.py')
        self.target.rename(real)
        self.target.symlink_to('real.py')
        self.assert_nothing_committed_and_not_verified()
        self.assertIn('symlink', load_checkpoint(self.cp.task_id).last_error)  # refused before any git add

    def test_a_clean_filter_added_after_the_write_commits_what_git_derives(self):
        # A filter that appears between the write and the commit is indistinguishable from
        # one that was always there: the blob is whatever git itself derives, exactly once.
        self.kill_before_add()
        self.git('config', 'filter.shout.clean', 'tr a-z A-Z')
        info = Path(self.git('rev-parse', '--git-common-dir').strip()) / 'info'
        info.mkdir(exist_ok=True)
        (info / 'attributes').write_text('app.py filter=shout\n')
        cp = resume_task(self.cp.task_id)
        self.assertTrue(cp.verified_success, cp.last_error)
        self.assertEqual(self.git('rev-list', '--count', self.cp.base_ref + '..HEAD').strip(), '1')
        self.assertEqual(self.git('show', 'HEAD:app.py'), self.target.read_text().upper())
        self.assertEqual(self.git('status', '--porcelain'), '')
        self.assertEqual(self.git('show', '--name-only', '--format=', 'HEAD').split(), ['app.py'])

    def test_a_target_name_with_glob_characters_commits_only_that_file(self):
        repo = self.root / 'globrepo'
        repo.mkdir()
        run = lambda *args: subprocess.run(['git', *args], cwd=repo, check=True, text=True, capture_output=True)
        run('init')
        (repo / 'a*.py').write_text('STATUS = "BROKEN"\n')
        (repo / 'ab.py').write_text('SIBLING = 1\n')
        run('add', '-A')
        run('-c', 'user.email=t@local', '-c', 'user.name=t', 'commit', '-m', 'fixture')
        spec = PatchSpec(relative_path='a*.py', find='STATUS = "BROKEN"', replace='STATUS = "READY"',
                         description='glob-named target')
        cp = authorize_task(base_repo=repo, patch=spec, task_id='glob-task')
        cp.status = 'resolved'
        save_checkpoint(cp)
        cp = step_worktree(cp, worktrees_root=self.root / 'wts')
        self.kill_before_add(cp.task_id)
        wt = Path(cp.worktree_path)
        (wt / 'ab.py').write_text('SIBLING = 2\n')
        done = resume_task(cp.task_id)
        self.assertTrue(done.verified_success, done.last_error)
        changed = subprocess.run(['git', 'show', '--name-only', '--format=', 'HEAD'], cwd=wt, check=True,
                                 text=True, capture_output=True).stdout.split()
        self.assertEqual(changed, ['a*.py'])
        self.assertIn('ab.py', subprocess.run(['git', 'status', '--porcelain'], cwd=wt, check=True, text=True,
                                              capture_output=True).stdout)

    def run_named_target(self, name, sibling=None):
        repo = self.root / ('repo-' + str(abs(hash(name))))
        repo.mkdir()
        run = lambda *args: subprocess.run(['git', *args], cwd=repo, check=True, text=True, capture_output=True)
        run('init')
        (repo / name).write_text('STATUS = "BROKEN"\n')
        if sibling:
            (repo / sibling).write_text('STATUS = "READY"\n')
        run('add', '-A')
        run('-c', 'user.email=t@local', '-c', 'user.name=t', 'commit', '-m', 'fixture')
        spec = PatchSpec(relative_path=name, find='STATUS = "BROKEN"', replace='STATUS = "READY"',
                         description='oddly named target')
        cp = authorize_task(base_repo=repo, patch=spec, task_id='named-' + str(abs(hash(name))))
        cp.status = 'resolved'
        save_checkpoint(cp)
        cp = step_worktree(cp, worktrees_root=self.root / 'wts')
        return cp

    def test_a_target_name_that_looks_like_an_index_stage_still_verifies(self):
        for name in ('0:a.py', '2:a.py', '3:a.py'):
            with self.subTest(name=name):
                cp = self.run_named_target(name, sibling='a.py')
                done = resume_task(cp.task_id)
                self.assertTrue(done.verified_success, done.last_error)
                wt = Path(cp.worktree_path)
                changed = subprocess.run(['git', 'show', '--name-only', '--format=', 'HEAD'], cwd=wt, check=True,
                                         text=True, capture_output=True).stdout.split()
                self.assertEqual(changed, [name])

    def test_a_symlink_whose_link_text_equals_the_patch_is_not_committed(self):
        self.kill_before_add()
        after = self.target.read_bytes()
        self.target.unlink()
        # A file named by the after-image text and holding it, with the target linked to it:
        # both the link's text and the bytes read through it equal the recorded patch.
        (self.target.parent / os.fsdecode(after)).write_bytes(after)
        os.symlink(after, self.target)
        cp = resume_task(self.cp.task_id)
        self.assertIsNot(cp.verified_success, True)
        self.assertEqual(self.git('rev-list', '--count', self.cp.base_ref + '..HEAD').strip(), '0')

    def test_uncommittable_patch_is_not_reported_verified(self):
        hooks = Path(self.git('rev-parse', '--git-common-dir').strip()) / 'hooks'
        hooks.mkdir(exist_ok=True)
        hook = hooks / 'pre-commit'
        hook.write_text('#!/bin/sh\nexit 1\n')
        hook.chmod(0o755)
        cp = resume_task(self.cp.task_id)
        self.assertIsNot(cp.verified_success, True)
        self.assertNotEqual(cp.status, 'verified')
        self.assertIn('commit', cp.last_error)
        hook.unlink()  # once the commit can complete, the same task resumes to verified
        self.assertTrue(resume_task(self.cp.task_id).verified_success)
        self.assert_patch_committed_once()

    def test_refused_checkpoints_are_listed_with_reason(self):
        _, spec = make_fixture_repo(self.root / 'repo')
        corrupt = authorize_task(base_repo=self.repo, patch=spec, task_id='corrupt-task')
        checkpoint_path(corrupt.task_id).write_text('{"task_id": "corrupt-task", "sta')
        stale = authorize_task(base_repo=self.repo, patch=spec, task_id='stale-task')
        source = self.root / 'aodl.json'
        source.write_text('{"policy":1}')
        stale.aodl_path = str(source)
        save_checkpoint(stale)
        source.write_text('{"policy":2}')
        rows = {row['task_id']: row for row in list_checkpoints()}
        self.assertEqual(set(rows), {self.cp.task_id, 'corrupt-task', 'stale-task'})
        self.assertEqual(rows[self.cp.task_id]['status'], 'worktree_ready')
        self.assertNotIn('reason', rows[self.cp.task_id])
        self.assertEqual(rows['corrupt-task']['status'], 'refused')
        self.assertIn('InvalidCheckpoint', rows['corrupt-task']['reason'])
        self.assertEqual(rows['stale-task']['status'], 'refused')
        self.assertIn('StaleCheckpoint', rows['stale-task']['reason'])
        self.assertIsNone(rows['stale-task']['verified_success'])
        shown = subprocess.run([sys.executable, '-m', 'z0int.cli', 'task', 'status'],
                               text=True, capture_output=True, timeout=60)
        self.assertEqual(shown.returncode, 0, shown.stderr)
        listed = {row['task_id']: row for row in json.loads(shown.stdout)['tasks']}
        self.assertEqual(listed['corrupt-task']['status'], 'refused')
        self.assertEqual(listed['stale-task']['status'], 'refused')
        self.assertTrue(listed['stale-task']['reason'])

    def test_removing_the_seal_does_not_unlock_an_edited_patch(self):
        path = checkpoint_path(self.cp.task_id)
        row = json.loads(path.read_text())
        del row['_continuation']
        path.write_text(json.dumps(row))  # seal stripped, nothing else changed
        with self.assertRaisesRegex(InvalidCheckpoint, 'unsealed'):
            load_checkpoint(self.cp.task_id)
        row['patch']['replace'] = 'STATUS = "TAMPERED"'
        path.write_text(json.dumps(row))
        with self.assertRaisesRegex(InvalidCheckpoint, 'unsealed'):
            resume_task(self.cp.task_id)
        self.assertIn('BROKEN', self.target.read_text())
        self.assertEqual(self.git('rev-list', '--count', self.cp.base_ref + '..HEAD').strip(), '0')
        listed = {r['task_id']: r for r in list_checkpoints()}[self.cp.task_id]
        self.assertEqual(listed['status'], 'refused')
        self.assertIn('unsealed', listed['reason'])

    # --- after-image-bound commit, target-only commit, task-branch proof, OSError refusals ---

    def commits(self, ref='HEAD'):
        return int(self.git('rev-list', '--count', f'{self.cp.base_ref}..{ref}').strip())

    def assert_withheld(self, cp, phrase):
        self.assertIsNone(cp.verified_success)
        self.assertNotIn(cp.status, ('verified', 'failed'))
        self.assertIn(phrase, cp.last_error or '')
        self.assertIsNone(task_continuation(cp).body['verified_success'])

    def test_resume_never_commits_content_beyond_the_recorded_patch(self):
        cp = resume_task(self.cp.task_id, until='patched')
        self.assertEqual(cp.status, 'patched')
        self.assert_patch_committed_once()
        recorded = self.target.read_bytes()
        self.target.write_bytes(recorded + b'EXTRA = "not part of the patch"\n')  # predicate still holds
        cp = resume_task(self.cp.task_id)
        self.assert_withheld(cp, 'does not match the recorded patch')
        self.assertEqual(self.commits(), 1, 'a second "bounded patch" commit was made')
        self.assertNotIn('EXTRA', self.git('show', 'HEAD:app.py'))
        self.assertIn('EXTRA', self.target.read_text())  # the foreign edit is left alone
        self.target.write_bytes(recorded)  # still resumable once the worktree matches again
        self.assertTrue(resume_task(self.cp.task_id).verified_success)
        self.assert_patch_committed_once()

    def test_uncommitted_patch_with_extra_content_is_not_committed(self):
        hook = Path(self.git('rev-parse', '--git-common-dir').strip()) / 'hooks' / 'pre-commit'
        hook.parent.mkdir(exist_ok=True)
        hook.write_text('#!/bin/sh\nexit 1\n')
        hook.chmod(0o755)
        self.assert_withheld(resume_task(self.cp.task_id), 'commit')
        hook.unlink()
        recorded = self.target.read_bytes()
        self.target.write_bytes(recorded + b'EXTRA = 1\n')
        cp = resume_task(self.cp.task_id)
        self.assert_withheld(cp, 'does not match the recorded patch')
        self.assertEqual(self.commits(), 0)
        self.target.write_bytes(recorded)
        self.assertTrue(resume_task(self.cp.task_id).verified_success)
        self.assert_patch_committed_once()

    def test_legacy_checkpoint_without_after_image_is_not_committed_at_verify(self):
        self.target.write_text(self.target.read_text().replace('BROKEN', 'READY'))
        row = self.cp.to_dict()
        row.pop('pending_patch')  # pre-seal shape: no after-image, no seal
        row.update(status='patched', execution_completed=True)
        checkpoint_path(self.cp.task_id).write_text(json.dumps(row))
        cp = resume_task(self.cp.task_id)
        self.assertIsNone(cp.pending_patch)
        self.assertEqual(self.commits(), 0, 'verify committed bytes with no recorded after-image')
        self.assertEqual(self.git('status', '--porcelain'), ' M app.py\n')
        self.assertTrue(cp.verified_success)  # same outcome as before the commit check existed

    def test_other_staged_files_stay_out_of_the_patch_commit(self):
        other = Path(self.cp.worktree_path) / 'other.txt'
        other.write_text('staged by someone else\n')
        self.git('add', '--', 'other.txt')
        cp = resume_task(self.cp.task_id)
        self.assertTrue(cp.verified_success, cp.last_error)
        self.assertEqual(self.commits(), 1)
        self.assertEqual(self.git('show', '--name-only', '--format=', 'HEAD').split(), ['app.py'])
        self.assertEqual(self.git('status', '--porcelain'), 'A  other.txt\n')

    def test_detached_worktree_is_not_reported_verified(self):
        branch = 'refs/heads/' + self.cp.branch
        self.git('checkout', '--quiet', '--detach')
        cp = resume_task(self.cp.task_id)
        self.assert_withheld(cp, 'task branch')
        self.assertEqual(self.git('rev-parse', branch).strip(), self.cp.base_ref)
        self.assertEqual(self.git('rev-parse', 'HEAD').strip(), self.cp.base_ref)  # nothing committed
        self.assertNotEqual(subprocess.run(['git', 'symbolic-ref', '--quiet', 'HEAD'],
                                           cwd=self.cp.worktree_path).returncode, 0)  # nothing checked out
        self.git('checkout', '--quiet', self.cp.branch)  # the operator reattaches; edit carries over
        self.assertTrue(resume_task(self.cp.task_id).verified_success)
        self.assert_patch_committed_once()
        self.assertIn('READY', self.git('show', branch + ':app.py'))

    def test_worktree_on_another_branch_is_not_reported_verified(self):
        resume_task(self.cp.task_id, until='patched')
        self.assert_patch_committed_once()
        self.git('checkout', '--quiet', '-b', 'elsewhere', self.cp.base_ref)
        self.target.write_text(self.target.read_text().replace('BROKEN', 'READY'))  # after-image bytes
        cp = resume_task(self.cp.task_id)
        self.assert_withheld(cp, 'task branch')
        self.assertEqual(self.commits('elsewhere'), 0)
        self.assertEqual(self.commits(self.cp.branch), 1)
        self.assertEqual(self.git('symbolic-ref', 'HEAD').strip(), 'refs/heads/elsewhere')

    def test_detached_at_the_patch_commit_is_still_withheld(self):
        resume_task(self.cp.task_id, until='patched')
        self.git('checkout', '--quiet', '--detach')
        cp = resume_task(self.cp.task_id)
        self.assert_withheld(cp, 'task branch')
        self.assertEqual(self.commits(self.cp.branch), 1)

    def test_unreadable_checkpoint_entries_are_listed_as_refused(self):
        tasks = checkpoint_path(self.cp.task_id).parent
        (tasks / 'a-directory.json').mkdir()
        (tasks / 'dangling-link.json').symlink_to(tasks / 'no-such-target.json')
        unreadable = tasks / 'unreadable-file.json'
        unreadable.write_text('{}')
        unreadable.chmod(0)
        self.addCleanup(unreadable.chmod, 0o600)
        expected = {'a-directory': 'IsADirectoryError', 'dangling-link': 'FileNotFoundError'}
        if os.geteuid() != 0:  # root reads through mode 000
            expected['unreadable-file'] = 'PermissionError'
        rows = {row['task_id']: row for row in list_checkpoints()}
        self.assertEqual(set(rows), {self.cp.task_id, 'a-directory', 'dangling-link', 'unreadable-file'})
        self.assertEqual(rows[self.cp.task_id]['status'], 'worktree_ready')
        for name, error in expected.items():
            self.assertEqual(rows[name]['status'], 'refused')
            self.assertIn(error, rows[name]['reason'])
            self.assertIsNone(rows[name]['verified_success'])
            self.assertIsNone(rows[name]['execution_completed'])
        shown = subprocess.run([sys.executable, '-m', 'z0int.cli', 'task', 'status'],
                               text=True, capture_output=True, timeout=60)
        self.assertEqual(shown.returncode, 0, shown.stderr)
        listed = {row['task_id']: row for row in json.loads(shown.stdout)['tasks']}
        for name in expected:
            self.assertEqual(listed[name]['status'], 'refused')


# Repositories in which git normalises the target on the way in, so the stored blob
# is not the worktree bytes. name -> (git config, .gitattributes)
REDACT = (('filter.redact.clean', 'sed s/SECRET/REDACTED/'), ('filter.redact.smudge', 'sed s/REDACTED/SECRET/'))
NORMALISING = {
    'autocrlf-true': ((('core.autocrlf', 'true'),), None),
    'text-auto-eol-crlf': ((), '* text=auto eol=crlf\n'),
    'py-text-eol-crlf': ((), '*.py text eol=crlf\n'),
    'ident': ((), '*.py ident\n'),
    'clean-smudge-filter': (REDACT, '*.py filter=redact\n'),
}
ORDINARY = b'STATUS = "BROKEN"\n# $Id$\nKEY = "SECRET"\n'


@unittest.skipUnless(os.name == 'posix', 'local task owner uses POSIX flock')
class GitDerivedBlobTests(unittest.TestCase):
    """The blob that must be on the task branch is the one git derives from the after-image."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        env = patch.dict(os.environ, {'Z0INT_HOME': str(self.root / 'home')})
        env.start(); self.addCleanup(env.stop)

    def git(self, cwd, *args, check=True):
        return subprocess.run(['git', *args], cwd=cwd, check=check, capture_output=True).stdout

    def task(self, name, *, config=(), attributes=None, content=ORDINARY, find='STATUS = "BROKEN"',
             replace='STATUS = "READY"', configure_after_commit=False, rel='app.py'):
        repo = self.root / name
        repo.mkdir()
        self.git(repo, 'init')

        def configure():
            for key, value in config:
                self.git(repo, 'config', key, value)
            if attributes:
                (repo / '.gitattributes').write_text(attributes)

        if not configure_after_commit:
            configure()
        (repo / rel).write_bytes(content)
        self.git(repo, 'add', '-A')
        self.git(repo, '-c', 'user.email=t@local', '-c', 'user.name=t', 'commit', '-m', 'fixture')
        if configure_after_commit:
            configure()
        cp = authorize_task(base_repo=repo, task_id=name, patch=PatchSpec(
            relative_path=rel, find=find, replace=replace, description='git-derived blob'))
        cp.status = 'resolved'
        save_checkpoint(cp)
        cp = step_worktree(cp, worktrees_root=self.root / 'wts')
        self.wt = Path(cp.worktree_path)
        self.target = self.wt / rel
        return cp

    def normalising_task(self, name, **kwargs):
        config, attributes = NORMALISING[name]
        return self.task(name, config=config, attributes=attributes, **kwargs)

    def kill(self, cp, *points):
        for point in points:
            result = subprocess.run([sys.executable, '-c', KILL_AT_GIT_STEP, cp.task_id, point],
                                    text=True, capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 90, f'kill point {point} not reached: {result.stderr}')

    def commits(self, cp, ref=None):
        return int(self.git(self.wt, 'rev-list', '--count', f'{cp.base_ref}..{ref or cp.branch}').strip())

    def blob(self, cp, rel='app.py'):
        return self.git(self.wt, 'cat-file', 'blob', f'refs/heads/{cp.branch}:{rel}')

    def staged_oid(self, rel='app.py'):
        return self.git(self.wt, '--literal-pathspecs', 'ls-files', '--stage', '--', rel).split()[1].decode()

    def assert_verified_with_one_clean_commit(self, cp, rel='app.py'):
        done = resume_task(cp.task_id)
        self.assertTrue(done.verified_success, done.last_error)
        self.assertEqual(done.status, 'verified')
        self.assertIsNone(done.last_error)
        self.assertEqual(self.commits(cp), 1)
        self.assertEqual(self.git(self.wt, 'status', '--porcelain'), b'')
        self.assertEqual(self.git(self.wt, 'show', '--name-only', '--format=', cp.branch).split(), [rel.encode()])
        self.assertEqual(resume_task(cp.task_id).to_dict(), done.to_dict())
        self.assertEqual(self.commits(cp), 1)
        return done

    def assert_withheld_with_nothing_committed(self, cp, phrase):
        done = resume_task(cp.task_id)
        self.assertIsNone(done.verified_success)
        self.assertNotIn(done.status, ('verified', 'failed'))
        self.assertIn(phrase, done.last_error or '')
        self.assertEqual(self.commits(cp), 0)
        return done

    # --- an ordinary task verifies with exactly one commit under each normalising configuration ---

    def ordinary_task_verifies(self, name, stored):
        cp = self.normalising_task(name)
        self.assert_verified_with_one_clean_commit(cp)
        self.assertIn(b'STATUS = "READY"', self.target.read_bytes())
        self.assertEqual(self.blob(cp), stored)
        self.assertNotEqual(self.blob(cp), self.target.read_bytes(), 'this configuration must normalise the file')

    def test_ordinary_task_verifies_under_core_autocrlf_true(self):
        self.ordinary_task_verifies('autocrlf-true', b'STATUS = "READY"\n# $Id$\nKEY = "SECRET"\n')
        self.assertIn(b'\r\n', self.target.read_bytes())

    def test_ordinary_task_verifies_under_text_auto_eol_crlf(self):
        self.ordinary_task_verifies('text-auto-eol-crlf', b'STATUS = "READY"\n# $Id$\nKEY = "SECRET"\n')
        self.assertIn(b'\r\n', self.target.read_bytes())

    def test_ordinary_task_verifies_under_py_text_eol_crlf(self):
        self.ordinary_task_verifies('py-text-eol-crlf', b'STATUS = "READY"\n# $Id$\nKEY = "SECRET"\n')
        self.assertIn(b'\r\n', self.target.read_bytes())

    def test_ordinary_task_verifies_under_the_ident_attribute(self):
        self.ordinary_task_verifies('ident', b'STATUS = "READY"\n# $Id$\nKEY = "SECRET"\n')
        self.assertIn(b'$Id: ', self.target.read_bytes())

    def test_ordinary_task_verifies_under_a_clean_smudge_filter(self):
        self.ordinary_task_verifies('clean-smudge-filter', b'STATUS = "READY"\n# $Id$\nKEY = "REDACTED"\n')
        self.assertIn(b'SECRET', self.target.read_bytes())

    def test_ordinary_task_keeps_crlf_git_already_stores_once_autocrlf_is_enabled(self):
        # A blob that already holds CRLF is NOT converted by git add under core.autocrlf.
        # git hash-object does not load the index, so it would predict a different blob.
        crlf = ORDINARY.replace(b'\n', b'\r\n')
        cp = self.task('crlf-blob-then-autocrlf', config=(('core.autocrlf', 'true'),), content=crlf,
                       configure_after_commit=True)
        self.kill(cp, 'before_add')
        predicted = self.git(self.wt, 'hash-object', '--path=app.py', '--', 'app.py').strip().decode()
        self.assert_verified_with_one_clean_commit(cp)
        self.assertEqual(self.blob(cp), crlf.replace(b'BROKEN', b'READY'))
        self.assertNotEqual(self.git(self.wt, 'rev-parse', f'{cp.branch}:app.py').strip().decode(), predicted)

    # --- the derived id is exactly what git add stores, and deriving it changes nothing ---

    def assert_derived_oid_is_what_git_add_stores(self, cp, rel='app.py', hash_object_agrees=True):
        self.kill(cp, 'before_add')  # patch written, nothing staged
        derived = task_loop._git_derived_oid(self.wt, cp.branch, rel)
        self.assertEqual(self.git(self.wt, 'diff', '--cached', '--name-only'), b'', 'deriving staged something')
        self.assertNotEqual(subprocess.run(['git', 'cat-file', '-e', derived], cwd=self.wt,
                                           capture_output=True).returncode, 0, 'deriving wrote an object')
        self.assertEqual(list(Path(tempfile.gettempdir()).glob('z0int-derive-*')), [])
        hashed = self.git(self.wt, 'hash-object', f'--path={rel}', '--', rel).strip().decode()
        self.assertEqual(hashed == derived, hash_object_agrees)
        self.git(self.wt, 'add', '--', f':(literal){rel}')
        self.assertEqual(self.staged_oid(rel), derived)
        self.git(self.wt, 'reset', '-q')
        self.assert_verified_with_one_clean_commit(cp, rel)
        self.assertEqual(self.git(self.wt, 'rev-parse', f'{cp.branch}:{rel}').strip().decode(), derived)

    def test_derived_oid_is_what_git_add_stores_without_normalisation(self):
        self.assert_derived_oid_is_what_git_add_stores(self.task('plain'))

    def test_derived_oid_is_what_git_add_stores_under_core_autocrlf_true(self):
        self.assert_derived_oid_is_what_git_add_stores(self.normalising_task('autocrlf-true'))

    def test_derived_oid_is_what_git_add_stores_under_text_auto_eol_crlf(self):
        self.assert_derived_oid_is_what_git_add_stores(self.normalising_task('text-auto-eol-crlf'))

    def test_derived_oid_is_what_git_add_stores_under_py_text_eol_crlf(self):
        self.assert_derived_oid_is_what_git_add_stores(self.normalising_task('py-text-eol-crlf'))

    def test_derived_oid_is_what_git_add_stores_under_the_ident_attribute(self):
        self.assert_derived_oid_is_what_git_add_stores(self.normalising_task('ident'))

    def test_derived_oid_is_what_git_add_stores_under_a_clean_smudge_filter(self):
        self.assert_derived_oid_is_what_git_add_stores(self.normalising_task('clean-smudge-filter'))

    def test_derived_oid_is_what_git_add_stores_for_a_crlf_blob_under_autocrlf(self):
        cp = self.task('crlf-blob-then-autocrlf', config=(('core.autocrlf', 'true'),),
                       content=ORDINARY.replace(b'\n', b'\r\n'), configure_after_commit=True)
        self.assert_derived_oid_is_what_git_add_stores(cp, hash_object_agrees=False)

    def test_derived_oid_is_what_git_add_stores_for_glob_and_colon_names_under_autocrlf(self):
        for index, rel in enumerate(('a*.py', '2:a.py', ':x.py')):
            with self.subTest(rel=rel):
                cp = self.task(f'named-{index}', config=(('core.autocrlf', 'true'),), rel=rel)
                (self.wt / 'ab.py').write_text('SIBLING = 2\n')  # a*.py must not match it
                self.kill(cp, 'before_add')
                derived = task_loop._git_derived_oid(self.wt, cp.branch, rel)
                done = resume_task(cp.task_id)
                self.assertTrue(done.verified_success, done.last_error)
                self.assertEqual(self.commits(cp), 1)
                self.assertEqual(self.git(self.wt, 'rev-parse', f'{cp.branch}:./{rel}').strip().decode(), derived)
                self.assertEqual(self.git(self.wt, 'show', '--name-only', '--format=', cp.branch).split(),
                                 [rel.encode()])
                self.assertEqual(self.git(self.wt, 'status', '--porcelain').split(), [b'??', b'ab.py'])

    # --- a kill anywhere between the write and the commit still ends in exactly one commit ---

    def test_kill_between_write_and_commit_completes_under_every_normalising_configuration(self):
        for name in NORMALISING:
            for points in (('before_add',), ('after_add',), ('after_commit',), ('before_add', 'after_add')):
                with self.subTest(configuration=name, points=points):
                    cp = self.normalising_task(name)
                    self.kill(cp, *points)
                    self.assert_verified_with_one_clean_commit(cp)
                    shutil.rmtree(self.root)
                    self.root.mkdir()

    # --- a patch whose only change is line endings or $Id$ text ---

    def test_line_ending_only_patch_is_committed_when_git_stores_a_different_blob(self):
        for name, config, attributes, stored in (
                ('plain', (), None, b'KEY = "SECRET"\n'),
                ('ident', (), '*.py ident\n', b'KEY = "SECRET"\n'),
                ('clean-smudge-filter', REDACT, '*.py filter=redact\n', b'KEY = "REDACTED"\n')):
            with self.subTest(configuration=name):
                cp = self.task(name + '-eol', config=config, attributes=attributes,
                               content=b'KEY = "SECRET"\r\n', find='\r\n', replace='\n')
                self.assertIn(b'\r\n', self.blob(cp))
                self.assert_verified_with_one_clean_commit(cp)
                self.assertEqual(self.blob(cp), stored)

    def test_id_text_only_patch_is_committed_when_git_stores_a_different_blob(self):
        for name, config, attributes in (('plain', (), None), ('autocrlf-true', (('core.autocrlf', 'true'),), None),
                                         ('clean-smudge-filter', REDACT, '*.py filter=redact\n')):
            with self.subTest(configuration=name):
                cp = self.task(name + '-id', config=config, attributes=attributes,
                               content=b'# $Id: old $\nKEY = 1\n', find='$Id: old $', replace='$Id: patched $')
                self.assert_verified_with_one_clean_commit(cp)
                self.assertEqual(self.blob(cp), b'# $Id: patched $\nKEY = 1\n')

    def assert_nothing_to_commit_matches_the_baseline(self, cp):
        # The PR baseline ran git add, found nothing to commit, and verified by the content
        # predicate with a clean status. There is no commit to make: the branch already holds
        # the blob git derives from the patched file.
        before = self.git(self.wt, 'rev-parse', f'{cp.branch}:app.py')
        done = resume_task(cp.task_id)
        self.assertTrue(done.verified_success, done.last_error)
        self.assertEqual(self.commits(cp), 0)
        self.assertEqual(self.git(self.wt, 'rev-parse', f'{cp.branch}:app.py'), before)
        self.assertEqual(self.staged_oid(), before.strip().decode())
        self.assertEqual(self.git(self.wt, 'status', '--porcelain'), b'')

    def test_line_ending_only_patch_git_stores_identically_has_nothing_to_commit(self):
        for name in ('autocrlf-true', 'text-auto-eol-crlf', 'py-text-eol-crlf'):
            with self.subTest(configuration=name):
                cp = self.normalising_task(name, content=b'KEY = 1\r\n', find='\r\n', replace='\n')
                self.assertEqual(self.target.read_bytes(), b'KEY = 1\r\n')
                self.assert_nothing_to_commit_matches_the_baseline(cp)
                self.assertEqual(self.target.read_bytes(), b'KEY = 1\n')

    def test_id_text_only_patch_under_ident_has_nothing_to_commit(self):
        cp = self.normalising_task('ident', content=b'# $Id$\nKEY = 1\n', find='PLACEHOLDER', replace='$Id: patched $')
        expanded = re.search(rb'\$Id: [0-9a-f]+ \$', self.target.read_bytes()).group(0).decode()
        cp.patch['find'] = expanded
        save_checkpoint(cp)
        self.assert_nothing_to_commit_matches_the_baseline(cp)
        self.assertIn(b'$Id: patched $', self.target.read_bytes())

    # --- interference: what is refused, and what git itself decides ---

    def test_symlinked_target_is_refused_before_anything_is_staged(self):
        cp = self.normalising_task('autocrlf-true')
        self.kill(cp, 'before_add')
        self.target.rename(self.wt / 'real.py')
        self.target.symlink_to('real.py')
        index = Path(self.git(self.wt, 'rev-parse', '--path-format=absolute', '--git-path', 'index').strip().decode())
        before = index.read_bytes()
        self.assert_withheld_with_nothing_committed(cp, 'symlink')
        self.assertEqual(index.read_bytes(), before)

    def test_index_entries_that_are_not_regular_files_are_never_the_expected_blob(self):
        cp = self.task('plain')
        self.target.unlink()
        self.target.symlink_to('ab.py')
        self.git(self.wt, 'add', '--', 'app.py')
        self.assertTrue(self.git(self.wt, 'ls-files', '--stage', '--', 'app.py').startswith(b'120000 '))
        self.assertIsNone(task_loop._staged_blob_oid(self.wt, 'app.py'))
        self.assertIsNone(task_loop._git_derived_oid(self.wt, cp.branch, 'app.py'))
        self.git(self.wt, '-c', 'user.email=t@local', '-c', 'user.name=t', 'commit', '-m', 'link')
        self.assertTrue(self.git(self.wt, 'ls-tree', cp.branch, '--', 'app.py').startswith(b'120000 '))
        self.assertIsNone(task_loop._branch_blob_oid(self.wt, cp.branch, 'app.py'))
        self.assertIsNone(task_loop._branch_blob_oid(self.wt, cp.branch, 'missing.py'))
        self.assertIsNone(task_loop._staged_blob_oid(self.wt, 'missing.py'))
        # a directory name is not the file inside it
        (self.wt / 'pkg').mkdir()
        (self.wt / 'pkg' / 'mod.py').write_text('X = 1\n')
        self.git(self.wt, 'add', '--', 'pkg/mod.py')
        self.git(self.wt, '-c', 'user.email=t@local', '-c', 'user.name=t', 'commit', '-m', 'pkg')
        oid = self.git(self.wt, 'rev-parse', f'{cp.branch}:pkg/mod.py').strip().decode()
        self.assertEqual(task_loop._staged_blob_oid(self.wt, 'pkg/mod.py'), oid)
        self.assertEqual(task_loop._branch_blob_oid(self.wt, cp.branch, 'pkg/mod.py'), oid)
        self.assertIsNone(task_loop._staged_blob_oid(self.wt, 'pkg'))
        self.assertIsNone(task_loop._branch_blob_oid(self.wt, cp.branch, 'pkg'))
        self.assertIn(b'\tpkg/mod.py', self.git(self.wt, 'ls-tree', cp.branch, '--', 'pkg/'))
        self.assertIsNone(task_loop._branch_blob_oid(self.wt, cp.branch, 'pkg/'))
        # an unmerged entry (stage 2) is not the staged file
        subprocess.run(['git', 'update-index', '--index-info'], cwd=self.wt, check=True, text=True,
                       input=f'0 {"0" * 40}\tpkg/mod.py\n100644 {oid} 2\tpkg/mod.py\n')
        self.assertIn(b' 2\tpkg/mod.py', self.git(self.wt, 'ls-files', '--stage', '--', 'pkg/mod.py'))
        self.assertIsNone(task_loop._staged_blob_oid(self.wt, 'pkg/mod.py'))

    def test_a_target_git_would_not_store_as_a_regular_file_is_withheld(self):
        cp = self.normalising_task('autocrlf-true')
        with patch.object(task_loop, '_git_derived_oid', lambda wt, branch, rel: None):
            done = resume_task(cp.task_id)
        self.assertIsNone(done.verified_success)
        self.assertIn('regular file', done.last_error)
        self.assertEqual(self.commits(cp), 0)
        self.assertEqual(self.git(self.wt, 'diff', '--cached', '--name-only'), b'')

    def test_assume_unchanged_target_is_withheld_because_git_add_stages_nothing(self):
        # assume-unchanged: git add succeeds and stages nothing; skip-worktree: git add refuses.
        for flag, phrase in (('assume-unchanged', 'what git staged'), ('skip-worktree', 'commit failed')):
            with self.subTest(flag=flag):
                cp = self.task(flag, config=(('core.autocrlf', 'true'),))
                self.git(self.wt, 'update-index', f'--{flag}', '--', 'app.py')
                self.assert_withheld_with_nothing_committed(cp, phrase)
                self.git(self.wt, 'update-index', f'--no-{flag}', '--', 'app.py')
                self.assert_verified_with_one_clean_commit(cp)

    def test_a_users_own_staged_version_of_the_target_is_replaced_by_the_recorded_patch(self):
        cp = self.normalising_task('autocrlf-true')
        self.kill(cp, 'before_add')
        patched = self.target.read_bytes()
        self.target.write_bytes(b'USER VERSION\r\n')
        self.git(self.wt, 'add', '--', 'app.py')
        self.target.write_bytes(patched)
        self.assert_verified_with_one_clean_commit(cp)
        self.assertNotIn(b'USER', self.blob(cp))

    def test_staged_sibling_stays_staged_under_a_normalising_configuration(self):
        cp = self.normalising_task('clean-smudge-filter')
        (self.wt / 'other.py').write_text('KEY = "SECRET"\n')
        self.git(self.wt, 'add', '--', 'other.py')
        done = resume_task(cp.task_id)
        self.assertTrue(done.verified_success, done.last_error)
        self.assertEqual(self.commits(cp), 1)
        self.assertEqual(self.git(self.wt, 'show', '--name-only', '--format=', cp.branch).split(), [b'app.py'])
        self.assertEqual(self.git(self.wt, 'status', '--porcelain'), b'A  other.py\n')

    def test_detached_head_is_withheld_under_a_normalising_configuration(self):
        cp = self.normalising_task('py-text-eol-crlf')
        self.git(self.wt, 'checkout', '--quiet', '--detach')
        self.assert_withheld_with_nothing_committed(cp, 'task branch')
        self.assertEqual(self.commits(cp, 'HEAD'), 0)
        self.git(self.wt, 'checkout', '--quiet', cp.branch)
        self.assert_verified_with_one_clean_commit(cp)

    def test_content_that_is_not_the_after_image_is_refused_before_git_is_asked(self):
        cp = self.normalising_task('autocrlf-true')
        resume_task(cp.task_id, until='patched')
        self.assertEqual(self.commits(cp), 1)
        self.target.write_bytes(self.target.read_bytes() + b'EXTRA = 1\r\n')  # predicate still holds

        def never(*args):
            raise AssertionError('git was asked to derive a blob from content that is not the recorded patch')

        with patch.object(task_loop, '_git_derived_oid', never):
            done = resume_task(cp.task_id)
        self.assertIsNone(done.verified_success)
        self.assertIn('does not match the recorded patch', done.last_error)
        self.assertEqual(self.commits(cp), 1)

    def test_target_rewritten_while_git_derives_the_blob_is_not_committed(self):
        cp = self.normalising_task('autocrlf-true')
        real = task_loop._git_derived_oid

        def rewritten_first(wt, branch, rel):
            self.target.write_bytes(self.target.read_bytes() + b'EXTRA = 1\r\n')  # predicate still holds
            return real(wt, branch, rel)

        with patch.object(task_loop, '_git_derived_oid', rewritten_first):
            done = resume_task(cp.task_id)
        self.assertIsNone(done.verified_success)
        self.assertIn('does not match the recorded patch', done.last_error)
        self.assertEqual(self.commits(cp), 0)
        self.assertEqual(self.git(self.wt, 'diff', '--cached', '--name-only'), b'')

    def test_target_rewritten_between_git_add_and_commit_is_not_reported_verified(self):
        cp = self.normalising_task('autocrlf-true')
        real = task_loop._run_git

        def rewritten_before_commit(args, **kwargs):
            if 'commit' in args:
                self.target.write_bytes(self.target.read_bytes() + b'EXTRA = 1\r\n')
            return real(args, **kwargs)

        with patch.object(task_loop, '_run_git', rewritten_before_commit):
            done = resume_task(cp.task_id, until='patched')
        self.assertIn('does not carry the recorded patch', done.last_error)
        done = resume_task(cp.task_id)
        self.assertIsNone(done.verified_success)
        self.assertNotEqual(done.status, 'verified')


if __name__ == '__main__':
    unittest.main()
