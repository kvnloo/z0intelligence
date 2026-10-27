"""Real git/worktree and process-loss tests; no model or provider required."""
import json
import os
import re
import shutil
import signal
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

# Resumes; a user commit lands on the task branch before one git step (argv[3]), and the process is
# killed at one point (argv[2]): around a git step, straight after the commit just made was read back
# ("read_back"), or straight after the first checkpoint written once git commit ran ("recorded").
KILL_OR_RACE = SOCKET_GUARD + '''
import os, subprocess, sys
import z0int.task_loop as tl
real_git, real_save, seen = tl._run_git, tl.save_checkpoint, set()
USER = ["git", "-c", "user.email=t@local", "-c", "user.name=t"]
def run_git(args, **kwargs):
    verb = "commit" if "commit" in args else args[0]
    if sys.argv[3] == "before_" + verb and "raced" not in seen:
        seen.add("raced")
        with open(os.path.join(kwargs["cwd"], "raced.txt"), "w") as handle: handle.write("user\\n")
        subprocess.run(["git", "add", "--", "raced.txt"], cwd=kwargs["cwd"], check=True)
        subprocess.run([*USER, "commit", "-q", "--no-verify", "-m", "racing", "--", "raced.txt"],
                       cwd=kwargs["cwd"], check=True)
    if sys.argv[2] == "before_" + verb: os._exit(90)
    result = real_git(args, **kwargs)
    if verb == "commit": seen.add("committed")
    if sys.argv[2] == "after_" + verb: os._exit(90)
    if sys.argv[2] == "read_back" and verb == "cat-file" and "committed" in seen: os._exit(90)
    return result
def save(cp):
    path = real_save(cp)
    if sys.argv[2] == "recorded" and "committed" in seen: os._exit(90)
    return path
tl._run_git, tl.save_checkpoint = run_git, save
tl.resume_task(sys.argv[1])
'''

# Resumes and is killed with SIGKILL straight after one git step of the blob derivation.
KILL_9_WHILE_DERIVING = SOCKET_GUARD + '''
import os, signal, subprocess, sys
import z0int.task_loop as tl
real_run = subprocess.run
def run(args, *rest, **kwargs):
    result = real_run(args, *rest, **kwargs)
    if "GIT_INDEX_FILE" in (kwargs.get("env") or {}) and sys.argv[2] in args:
        os.kill(os.getpid(), signal.SIGKILL)
    return result
subprocess.run = run
tl.resume_task(sys.argv[1])
'''

RESUME = '''
import sys
from z0int.task_loop import resume_task
resume_task(sys.argv[1])
'''

DERIVE = '''
import sys
from pathlib import Path
import z0int.task_loop as tl
print(tl._git_derived_oid(Path(sys.argv[1]), sys.argv[2], sys.argv[3], Path(sys.argv[4])))
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
             replace='STATUS = "READY"', configure_after_commit=False, rel='app.py', directory=None,
             tracked=None, after_commit=None):
        repo = self.root / (directory or name)
        repo.mkdir(parents=True)
        self.git(repo, 'init')

        def configure():
            for key, value in config:
                self.git(repo, 'config', key, value)
            if attributes:
                (repo / '.gitattributes').write_text(attributes)

        if not configure_after_commit:
            configure()
        (repo / (tracked or rel)).parent.mkdir(parents=True, exist_ok=True)
        (repo / (tracked or rel)).write_bytes(content)
        self.git(repo, 'add', '-A')
        self.git(repo, '-c', 'user.email=t@local', '-c', 'user.name=t', 'commit', '-m', 'fixture')
        if configure_after_commit:
            configure()
        if after_commit:
            after_commit(repo)
        cp = authorize_task(base_repo=repo, task_id=name, patch=PatchSpec(
            relative_path=rel, find=find, replace=replace, description='git-derived blob'))
        cp.status = 'resolved'
        save_checkpoint(cp)
        cp = step_worktree(cp, worktrees_root=self.root / 'wts')
        self.wt = Path(cp.worktree_path)
        self.target = self.wt / (tracked or rel)
        return cp

    def normalising_task(self, name, **kwargs):
        config, attributes = NORMALISING[name]
        return self.task(name, config=config, attributes=attributes, **kwargs)

    def kill(self, cp, *points):
        for point in points:
            result = subprocess.run([sys.executable, '-c', KILL_AT_GIT_STEP, cp.task_id, point],
                                    text=True, capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 90, f'kill point {point} not reached: {result.stderr}')

    def derive_root(self, cp):
        return checkpoint_path(cp.task_id).parent / cp.task_id / 'derive'

    def derive(self, cp, rel='app.py', branch=None):
        return task_loop._git_derived_oid(self.wt, branch or cp.branch, rel, self.derive_root(cp))

    def scratch_left(self, cp):
        return sorted(Path(tempfile.gettempdir()).glob('z0int-derive-*')) + sorted(
            self.derive_root(cp).glob('z0int-derive-*'))

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
        derived = self.derive(cp, rel)
        self.assertEqual(self.git(self.wt, 'diff', '--cached', '--name-only'), b'', 'deriving staged something')
        self.assertNotEqual(subprocess.run(['git', 'cat-file', '-e', derived], cwd=self.wt,
                                           capture_output=True).returncode, 0, 'deriving wrote an object')
        self.assertEqual(self.scratch_left(cp), [])
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
                derived = self.derive(cp, rel)
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
        self.assertIsNone(self.derive(cp))
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
        with patch.object(task_loop, '_git_derived_oid', lambda *args: None):
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

        def rewritten_first(*args):
            self.target.write_bytes(self.target.read_bytes() + b'EXTRA = 1\r\n')  # predicate still holds
            return real(*args)

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


    # --- the repository path: no character of it is a separator ---

    AWKWARD_DIRECTORIES = {
        'colon': 're:po', 'colon-in-parent': 'a:b/repo', 'space': 'sp ace', 'double-quote': 'q"uo"te',
        'backslash': 'back\\slash', 'newline': 'new\nline', 'non-ascii': 'ünï-日本', 'leading-dash': '-dash',
        'leading-hash': '#hash', 'tab': 'ta\tb', 'all-of-them': '-a:b "c"\\d\ne ü#',
    }

    def base_repository_path_verifies(self, name):
        cp = self.task(name, config=(('core.autocrlf', 'true'),), directory=self.AWKWARD_DIRECTORIES[name])
        self.assertIn(self.AWKWARD_DIRECTORIES[name], cp.base_repo)
        index = Path(self.git(self.wt, 'rev-parse', '--path-format=absolute', '--git-path', 'index').strip().decode())
        self.kill(cp, 'before_add')
        before = index.read_bytes()
        loose = self.git(self.wt, 'count-objects', '-v')
        derived = self.derive(cp)
        self.assertEqual(index.read_bytes(), before, 'deriving touched the worktree index')
        self.assertEqual(self.git(self.wt, 'count-objects', '-v'), loose, 'deriving wrote to the object store')
        self.assert_derived_oid_is_what_git_add_stores(cp)
        self.assertEqual(self.git(self.wt, 'rev-parse', f'{cp.branch}:app.py').strip().decode(), derived)

    def test_base_repository_path_with_a_colon_verifies(self):
        self.base_repository_path_verifies('colon')

    def test_base_repository_path_with_a_colon_in_a_parent_directory_verifies(self):
        self.base_repository_path_verifies('colon-in-parent')

    def test_base_repository_path_with_a_space_verifies(self):
        self.base_repository_path_verifies('space')

    def test_base_repository_path_with_a_double_quote_verifies(self):
        self.base_repository_path_verifies('double-quote')

    def test_base_repository_path_with_a_backslash_verifies(self):
        self.base_repository_path_verifies('backslash')

    def test_base_repository_path_with_a_newline_verifies(self):
        self.base_repository_path_verifies('newline')

    def test_base_repository_path_with_non_ascii_characters_verifies(self):
        self.base_repository_path_verifies('non-ascii')

    def test_base_repository_path_with_a_leading_dash_verifies(self):
        self.base_repository_path_verifies('leading-dash')

    def test_base_repository_path_with_a_leading_hash_verifies(self):
        self.base_repository_path_verifies('leading-hash')

    def test_base_repository_path_with_a_tab_verifies(self):
        self.base_repository_path_verifies('tab')

    def test_base_repository_path_with_every_awkward_character_at_once_verifies(self):
        self.base_repository_path_verifies('all-of-them')

    def test_colon_in_the_path_survives_a_kill_at_every_git_step(self):
        for points in (('before_add',), ('after_add',), ('after_commit',)):
            with self.subTest(points=points):
                cp = self.task('colon', config=(('core.autocrlf', 'true'),), directory='a:b/re:po')
                self.kill(cp, *points)
                self.assert_verified_with_one_clean_commit(cp)
                shutil.rmtree(self.root)
                self.root.mkdir()

    def test_scratch_directory_path_with_awkward_characters_verifies(self):
        # The scratch area is made under the task's own state directory.
        home = self.root / 'ho:me "dir"\\\nü\r'
        with patch.dict(os.environ, {'Z0INT_HOME': str(home)}):
            cp = self.task('plain', config=(('core.autocrlf', 'true'),))
            self.assertTrue(self.derive_root(cp).is_relative_to(home))
            self.assert_verified_with_one_clean_commit(cp)
            self.assertEqual(sorted(path.name for path in self.derive_root(cp).iterdir()), ['lock'])

    def test_alternates_entry_is_c_quoted_so_no_byte_of_the_path_is_a_separator(self):
        self.assertEqual(task_loop._c_quoted('/plain/objects'), b'"/plain/objects"')
        self.assertEqual(task_loop._c_quoted('/a:b c/#d'), b'"/a:b c/#d"')
        self.assertEqual(task_loop._c_quoted('/q"uo\\te'), b'"/q\\"uo\\\\te"')
        self.assertEqual(task_loop._c_quoted('/new\nline\ttab'), b'"/new\\012line\\011tab"')
        self.assertEqual(task_loop._c_quoted('/\u00fc'), b'"/\\303\\274"')
        self.assertEqual(task_loop._c_quoted(os.fsdecode(b'/\xff')), b'"/\\377"')
        for path in self.AWKWARD_DIRECTORIES.values():
            self.assertNotIn(b'\n', task_loop._c_quoted(path))

    def test_inherited_alternate_object_directories_still_reach_the_scratch_index(self):
        # Every object of the repository is reachable only through the inherited variable,
        # whose one entry is itself quoted because its path holds the list separator.
        alternate = self.root / 'in:herited objects'

        def move_objects_out(repo):
            shutil.move(repo / '.git' / 'objects', alternate)
            (repo / '.git' / 'objects' / 'info').mkdir(parents=True)
            (repo / '.git' / 'objects' / 'pack').mkdir()
            self.assertNotEqual(subprocess.run(['git', 'rev-parse', '--verify', 'HEAD^{tree}'], cwd=repo,
                                               capture_output=True).returncode, 0)
            inherited = patch.dict(os.environ, {'GIT_ALTERNATE_OBJECT_DIRECTORIES': f'"{alternate}"'})
            inherited.start(); self.addCleanup(inherited.stop)

        cp = self.task('inherited', config=(('core.autocrlf', 'true'),), after_commit=move_objects_out)
        self.assert_derived_oid_is_what_git_add_stores(cp)
        self.assertEqual(os.environ['GIT_ALTERNATE_OBJECT_DIRECTORIES'], f'"{alternate}"')

    # --- the target path: compared in git's own spelling of it ---

    SPELLINGS = (('./app.py', 'app.py'), ('./sub/a.py', 'sub/a.py'), ('sub//a.py', 'sub/a.py'),
                 ('sub/./a.py', 'sub/a.py'), ('.//sub/.//a.py', 'sub/a.py'))

    def spelled_task(self, index, rel, canonical, **kwargs):
        return self.task(f'spelled-{index}', rel=rel, tracked=canonical, **kwargs)

    def spelling_verifies(self, index):
        rel, canonical = self.SPELLINGS[index]
        cp = self.spelled_task(index, rel, canonical)
        self.assertEqual(cp.patch['relative_path'], rel)
        done = self.assert_verified_with_one_clean_commit(cp, canonical)
        self.assertEqual(done.patch['relative_path'], rel)
        self.assertEqual(self.blob(cp, canonical), ORDINARY.replace(b'BROKEN', b'READY'))

    def test_dot_slash_target_path_verifies_with_one_commit(self):
        self.spelling_verifies(0)

    def test_dot_slash_nested_target_path_verifies_with_one_commit(self):
        self.spelling_verifies(1)

    def test_doubled_slash_target_path_verifies_with_one_commit(self):
        self.spelling_verifies(2)

    def test_dot_component_target_path_verifies_with_one_commit(self):
        self.spelling_verifies(3)

    def test_mixed_redundant_components_target_path_verifies_with_one_commit(self):
        self.spelling_verifies(4)

    def test_non_canonical_target_path_survives_a_kill_at_every_git_step(self):
        for index, (rel, canonical) in enumerate(self.SPELLINGS[:4]):
            for points in (('before_add',), ('after_add',), ('after_commit',)):
                with self.subTest(rel=rel, points=points):
                    cp = self.spelled_task(index, rel, canonical, config=(('core.autocrlf', 'true'),))
                    self.kill(cp, *points)
                    self.assert_verified_with_one_clean_commit(cp, canonical)
                    shutil.rmtree(self.root)
                    self.root.mkdir()

    def test_canonical_path_is_the_name_git_prints(self):
        for index, (rel, canonical) in enumerate(self.SPELLINGS):
            with self.subTest(rel=rel):
                self.spelled_task(index, rel, canonical)
                index_file = Path(self.git(self.wt, 'rev-parse', '--path-format=absolute', '--git-path',
                                           'index').strip().decode())
                before = index_file.read_bytes()
                printed = self.git(self.wt, 'ls-files', '--full-name', '--', rel).decode().splitlines()
                self.assertEqual(printed, [canonical])
                self.assertEqual(task_loop._canonical_path(self.wt, rel), canonical)
                self.assertEqual(task_loop._canonical_path(self.wt, canonical), canonical)
                self.assertEqual(self.git(self.wt, 'ls-tree', '--name-only', '--full-tree', '-r', 'HEAD').decode()
                                 .splitlines(), [canonical])
                self.assertEqual(index_file.read_bytes(), before)
        # untracked, removed from the index, and odd names are spelled by git too
        (self.wt / 'new dir').mkdir()
        (self.wt / 'new dir' / 'a*.py').write_text('X = 1\n')
        self.assertEqual(task_loop._canonical_path(self.wt, './new dir//a*.py'), 'new dir/a*.py')
        (self.wt / 'ab.py').write_text('X = 1\n')
        (self.wt / 'a*.py').write_text('X = 1\n')
        self.assertEqual(task_loop._canonical_path(self.wt, 'a*.py'), 'a*.py')
        self.git(self.wt, 'rm', '-q', '--cached', '--', 'sub/a.py')
        self.assertEqual(task_loop._canonical_path(self.wt, 'sub/./a.py'), 'sub/a.py')
        # no canonical form: nothing there, a directory, a file named as a directory
        self.assertIsNone(task_loop._canonical_path(self.wt, 'missing.py'))
        self.assertIsNone(task_loop._canonical_path(self.wt, 'sub'))
        self.assertIsNone(task_loop._canonical_path(self.wt, 'sub/'))
        self.assertIsNone(task_loop._canonical_path(self.wt, 'sub/a.py/'))
        self.assertIsNone(task_loop._canonical_path(self.wt, 'sub/a.py/.'))

    def test_target_path_that_escapes_the_worktree_is_still_refused(self):
        outside = self.root / 'outside.py'
        for index, rel in enumerate(('../outside.py', 'sub/../../outside.py', str(outside))):
            with self.subTest(rel=rel):
                cp = self.task(f'escape-{index}', rel='sub/a.py')
                outside.write_bytes(ORDINARY)
                cp.patch['relative_path'] = rel
                save_checkpoint(cp)
                with self.assertRaisesRegex(InvalidCheckpoint, 'patch target escapes the isolated worktree'):
                    resume_task(cp.task_id)
                self.assertEqual(outside.read_bytes(), ORDINARY)
                cp.pending_patch = {'status': 'completed', 'before': '0' * 64, 'after': '1' * 64}
                self.assertEqual(task_loop._commit_patch(cp),
                                 'patch not committed: commit failed: patch target escapes the isolated worktree')
                self.assertEqual(self.commits(cp), 0)
                self.assertEqual(self.git(self.wt, 'status', '--porcelain'), b'')

    def test_trailing_slash_on_the_target_path_is_withheld_with_nothing_committed(self):
        # git matches a name with a trailing slash against directories only: nothing is staged.
        cp = self.task('trailing', rel='sub/a.py/', tracked='sub/a.py')
        for _ in range(2):
            self.assert_withheld_with_nothing_committed(cp, 'patch not committed: ')
        self.assertEqual(self.git(self.wt, 'status', '--porcelain'), b' M sub/a.py\n')
        self.assertEqual(self.git(self.wt, 'diff', '--cached', '--name-only'), b'')

    def test_target_path_git_has_no_single_name_for_is_refused_before_git_derives_anything(self):
        cp = self.task('trailing', rel='sub/a.py/', tracked='sub/a.py')

        def never(*args):
            raise AssertionError('git was asked to derive a blob for a name it matches no file for')

        with patch.object(task_loop, '_git_derived_oid', never):
            self.assert_withheld_with_nothing_committed(cp, 'git would not store the target as a regular file')

    def test_line_ending_only_patch_under_a_non_canonical_path_leaves_a_clean_status(self):
        # Nothing to commit, but the index entry is still refreshed under git's name for the file.
        cp = self.task('spelled-eol', config=(('core.autocrlf', 'true'),), content=b'KEY = 1\r\n', find='\r\n',
                       replace='\n', rel='./app.py', tracked='app.py')
        self.assert_nothing_to_commit_matches_the_baseline(cp)
        self.assertEqual(self.target.read_bytes(), b'KEY = 1\n')

    # --- a commit found necessary stays necessary ---

    def attributes_file(self):
        common = self.git(self.wt, 'rev-parse', '--path-format=absolute', '--git-common-dir').strip().decode()
        (Path(common) / 'info').mkdir(exist_ok=True)
        return Path(common) / 'info' / 'attributes'

    def add_clean_filter(self, command):
        self.git(self.wt, 'config', 'filter.late.clean', command)
        self.attributes_file().write_text('*.py filter=late\n')

    def filter_appears_before(self, verb, command):
        real = task_loop._run_git

        def run_git(args, **kwargs):
            if args[0] == verb or (verb == 'commit' and 'commit' in args):
                self.add_clean_filter(command)
            return real(args, **kwargs)

        return patch.object(task_loop, '_run_git', run_git)

    def assert_withheld_until_the_reverting_filter_is_gone(self, cp):
        for _ in range(3):
            done = self.assert_withheld_with_nothing_committed(cp, 'a commit of the recorded patch was needed')
            self.assertIsNone(done.measurements.get('verifier_ref'))
        self.attributes_file().unlink()
        self.assert_verified_with_one_clean_commit(cp)
        self.assertEqual(self.blob(cp), ORDINARY.replace(b'BROKEN', b'READY'))

    def test_filter_that_undoes_the_patch_between_add_and_commit_is_not_verified_without_a_commit(self):
        cp = self.task('plain')
        with self.filter_appears_before('commit', 'sed s/READY/BROKEN/'):
            first = resume_task(cp.task_id)
        self.assertIsNone(first.verified_success)
        self.assertEqual(self.commits(cp), 0)
        self.assert_withheld_until_the_reverting_filter_is_gone(cp)

    def test_filter_that_undoes_the_patch_after_a_kill_following_the_add_is_not_verified_without_a_commit(self):
        cp = self.task('plain')
        self.kill(cp, 'after_add')
        self.assertEqual(self.git(self.wt, 'diff', '--cached', '--name-only'), b'app.py\n')
        self.add_clean_filter('sed s/READY/BROKEN/')
        self.assert_withheld_until_the_reverting_filter_is_gone(cp)

    def test_filter_that_undoes_the_patch_under_autocrlf_is_not_verified_without_a_commit(self):
        cp = self.normalising_task('autocrlf-true')
        with self.filter_appears_before('commit', 'sed s/READY/BROKEN/'):
            resume_task(cp.task_id)
        for _ in range(2):
            self.assert_withheld_with_nothing_committed(cp, 'a commit of the recorded patch was needed')

    def test_filter_that_undoes_the_patch_before_any_commit_was_needed_has_nothing_to_commit(self):
        # Present before the commit step first runs: exactly as if it had always been configured.
        cp = self.task('plain')
        self.kill(cp, 'before_--literal-pathspecs')  # patch written; the commit step asked git nothing yet
        self.add_clean_filter('sed s/READY/BROKEN/')
        self.assert_nothing_to_commit_matches_the_baseline(cp)

    def test_filter_that_changes_the_blob_between_add_and_commit_ends_in_one_commit_and_is_withheld(self):
        # git commit --only reads the file again, so the commit stores what the new filter makes of it and
        # not the blob this task staged and recorded. Nothing durable tells that from a hook that rewrote
        # the commit, so it is treated the same: one commit, never another, and not verified on the
        # strength of what git derives afterwards.
        cp = self.task('plain')
        with self.filter_appears_before('commit', 'tr a-z A-Z'):
            first = resume_task(cp.task_id, until='patched')
        tip = self.rev(f'refs/heads/{cp.branch}')
        patched = ORDINARY.replace(b'BROKEN', b'READY')
        self.assertEqual(self.blob(cp), patched.upper())
        record = first.measurements['patch_committed']
        self.assertEqual(record, {'commit': tip, 'blob': first.measurements['patch_commit_intended']['blob'],
                                  'stored': self.rev(f'{tip}:app.py')})
        self.assertEqual(self.git(self.wt, 'cat-file', 'blob', record['blob']), patched)
        self.assertEqual(self.derive(cp), record['stored'], 'git must derive what the commit stores now')
        self.assert_withheld_on_the_one_changed_commit(cp, tip, first.last_error)
        # The task branch carrying the recorded blob is all that is asked for.
        self.attributes_file().unlink()
        os.utime(self.target, (1, 1))
        self.git(self.wt, '-c', 'user.email=t@local', '-c', 'user.name=t', 'commit', '-m', 'by hand', '--', 'app.py')
        done = resume_task(cp.task_id)
        self.assertTrue(done.verified_success, done.last_error)
        self.assertEqual(self.commits(cp), 2)
        self.assertEqual(self.blob(cp), patched)
        self.assertEqual(done.measurements['patch_committed'], record)

    # --- conditions no earlier test distinguished ---

    def test_target_swapped_for_a_symlink_while_git_derives_the_blob_is_never_staged(self):
        cp = self.normalising_task('autocrlf-true')
        real, real_git, asked = task_loop._git_derived_oid, task_loop._run_git, []

        def swapped_after_deriving(*args):
            oid = real(*args)
            self.target.rename(self.wt / 'real.py')  # same bytes when read through the link
            self.target.symlink_to('real.py')
            return oid

        def recording(args, **kwargs):
            asked.append(list(args))
            return real_git(args, **kwargs)

        with patch.object(task_loop, '_git_derived_oid', swapped_after_deriving), \
                patch.object(task_loop, '_run_git', recording):
            done = resume_task(cp.task_id, until='patched')
        self.assertIn('the worktree content does not match the recorded patch', done.last_error)
        self.assertEqual([args for args in asked if 'add' in args or 'reset' in args or 'commit' in args], [])
        self.assertEqual(self.commits(cp), 0)
        self.assertEqual(self.git(self.wt, 'diff', '--cached', '--name-only'), b'')
        self.assert_withheld_with_nothing_committed(cp, 'symlink')

    def test_nothing_to_commit_leaves_a_different_staged_version_of_the_target_alone(self):
        cp = self.normalising_task('autocrlf-true')
        resume_task(cp.task_id, until='patched')
        self.assertEqual(self.commits(cp), 1)
        patched = self.target.read_bytes()
        self.target.write_bytes(b'USER VERSION\r\n')
        self.git(self.wt, 'add', '--', 'app.py')
        staged_by_the_user = self.staged_oid()
        self.target.write_bytes(patched)
        done = resume_task(cp.task_id)
        self.assertTrue(done.verified_success, done.last_error)
        self.assertEqual(self.commits(cp), 1)
        self.assertEqual(self.staged_oid(), staged_by_the_user)
        self.assertEqual(self.git(self.wt, 'cat-file', 'blob', staged_by_the_user), b'USER VERSION\n')

    def test_staged_blob_that_is_not_the_derived_one_is_unstaged_again(self):
        cp = self.task('plain')
        with self.filter_appears_before('add', 'tr a-z A-Z'):  # after deriving, before the real add
            done = resume_task(cp.task_id, until='patched')
        self.assertIn('what git staged for the target is not what it derives', done.last_error)
        self.assertEqual(self.commits(cp), 0)
        self.assertEqual(self.git(self.wt, 'diff', '--cached', '--name-only'), b'')
        self.assertEqual(self.staged_oid(), self.git(self.wt, 'rev-parse', f'{cp.branch}:app.py').strip().decode())
        self.assert_verified_with_one_clean_commit(cp)
        self.assertEqual(self.blob(cp), ORDINARY.replace(b'BROKEN', b'READY').upper())

    def test_scratch_index_that_cannot_be_read_from_the_task_branch_derives_nothing(self):
        cp = self.task('crlf-blob-then-autocrlf', config=(('core.autocrlf', 'true'),),
                       content=ORDINARY.replace(b'\n', b'\r\n'), configure_after_commit=True)
        self.kill(cp, 'before_add')
        with self.assertRaises(subprocess.CalledProcessError) as raised:
            self.derive(cp, branch='no-such-branch')
        self.assertIn('read-tree', raised.exception.cmd)

    def test_target_git_add_refuses_in_the_scratch_index_is_withheld(self):
        # A required clean filter that fails: git add stores nothing. The scratch index
        # still holds the task branch's entry, which must not be taken for the derived blob.
        cp = self.task('plain')
        self.git(self.wt, 'config', 'filter.late.clean', "sh -c 'cat >/dev/null; exit 1'")
        self.git(self.wt, 'config', 'filter.late.required', 'true')
        self.attributes_file().write_text('*.py filter=late\n')
        for _ in range(2):
            done = self.assert_withheld_with_nothing_committed(cp, 'commit failed')
            self.assertIn('clean filter', done.last_error)
        self.assertEqual(self.git(self.wt, 'diff', '--cached', '--name-only'), b'')
        with self.assertRaises(subprocess.CalledProcessError) as raised:
            self.derive(cp)
        self.assertIn('add', raised.exception.cmd)
        self.attributes_file().unlink()
        self.assert_verified_with_one_clean_commit(cp)

    # --- once the patch is committed, it is never committed again ---

    ID_TEXT = b'# $Id: old $\nSTATUS = "BROKEN"\n'
    # name -> (file content, what makes git derive another blob from the same patched file)
    LATE_NORMALISERS = {
        'filter-that-rewrites': (ORDINARY, lambda self: self.add_clean_filter('tr a-z A-Z')),
        'filter-that-undoes-the-patch': (ORDINARY, lambda self: self.add_clean_filter('sed s/READY/BROKEN/')),
        'ident': (ID_TEXT, lambda self: self.attributes_file().write_text('*.py ident\n')),
    }

    def rev(self, name):
        return self.git(self.wt, 'rev-parse', '--verify', name).strip().decode()

    def assert_stays_verified_on_the_one_patch_commit(self, cp, stored, commits=1, commit=None):
        tip, blob = self.rev(f'refs/heads/{cp.branch}'), self.rev(f'refs/heads/{cp.branch}:app.py')
        derived_now = self.git(self.wt, 'hash-object', '--path=app.py', '--', 'app.py').strip().decode()
        self.assertNotEqual(derived_now, blob, 'git must derive another blob now, or nothing is tested')
        self.assertEqual(self.commits(cp), commits)
        for _ in range(3):
            done = resume_task(cp.task_id)
            self.assertTrue(done.verified_success, done.last_error)
            self.assertEqual(done.status, 'verified')
            self.assertIsNone(done.last_error)
            self.assertEqual(self.commits(cp), commits, 'the patch was committed a second time')
            self.assertEqual(self.rev(f'refs/heads/{cp.branch}'), tip)
            self.assertEqual(self.blob(cp), stored)
            self.assertEqual(done.measurements['patch_committed'], {'commit': commit or tip, 'blob': blob})
        return done

    def late_normaliser_after_the_commit(self, name, reach_the_commit):
        content, appear = self.LATE_NORMALISERS[name]
        cp = self.task(name, content=content)
        reach_the_commit(cp)
        appear(self)
        self.assert_stays_verified_on_the_one_patch_commit(cp, content.replace(b'BROKEN', b'READY'))

    def test_clean_filter_added_after_a_kill_following_the_commit_adds_no_second_commit(self):
        self.late_normaliser_after_the_commit('filter-that-rewrites', lambda cp: self.kill(cp, 'after_commit'))

    def test_filter_undoing_the_patch_after_a_kill_following_the_commit_adds_no_second_commit(self):
        self.late_normaliser_after_the_commit('filter-that-undoes-the-patch', lambda cp: self.kill(cp, 'after_commit'))

    def test_ident_attribute_added_after_a_kill_following_the_commit_adds_no_second_commit(self):
        self.late_normaliser_after_the_commit('ident', lambda cp: self.kill(cp, 'after_commit'))

    def test_clean_filter_added_after_the_patch_step_committed_adds_no_second_commit(self):
        self.late_normaliser_after_the_commit('filter-that-rewrites', lambda cp: resume_task(cp.task_id, until='patched'))

    def test_filter_undoing_the_patch_after_the_patch_step_committed_adds_no_second_commit(self):
        self.late_normaliser_after_the_commit('filter-that-undoes-the-patch',
                                              lambda cp: resume_task(cp.task_id, until='patched'))

    def test_ident_attribute_added_after_the_patch_step_committed_adds_no_second_commit(self):
        self.late_normaliser_after_the_commit('ident', lambda cp: resume_task(cp.task_id, until='patched'))

    def test_patch_commit_is_recorded_with_its_commit_id_and_blob_id(self):
        cp = self.task('plain')
        done = resume_task(cp.task_id, until='patched')
        record = {'commit': self.rev(f'refs/heads/{cp.branch}'), 'blob': self.rev(f'refs/heads/{cp.branch}:app.py')}
        self.assertEqual(done.measurements['patch_committed'], record)
        self.assertEqual(json.loads(checkpoint_path(cp.task_id).read_text())['measurements']['patch_committed'], record)
        self.assertEqual(self.git(self.wt, 'cat-file', 'blob', record['blob']), ORDINARY.replace(b'BROKEN', b'READY'))
        self.assertEqual(self.rev(record['commit'] + '~1'), cp.base_ref)
        done = self.assert_verified_with_one_clean_commit(cp)
        self.assertEqual(done.measurements['patch_committed'], record)

    def test_patch_commit_is_found_after_a_kill_and_recorded_before_anything_is_derived(self):
        cp = self.task('plain')
        self.kill(cp, 'after_commit')
        self.assertNotIn('patch_committed', load_checkpoint(cp.task_id).measurements)
        tip = self.rev(f'refs/heads/{cp.branch}')

        def never(*args):
            raise AssertionError('a blob was derived although the patch commit exists')

        with patch.object(task_loop, '_git_derived_oid', never):
            done = resume_task(cp.task_id)
        self.assertTrue(done.verified_success, done.last_error)
        self.assertEqual(done.measurements['patch_committed'],
                         {'commit': tip, 'blob': self.rev(f'refs/heads/{cp.branch}:app.py')})
        self.assertEqual(self.commits(cp), 1)

    def test_patch_commit_record_is_durable_as_soon_as_the_commit_is_found(self):
        cp = self.task('plain')
        self.kill(cp, 'after_commit')
        cp = load_checkpoint(cp.task_id)
        record = task_loop._patch_commit_record(cp, self.wt, cp.branch, 'app.py')
        self.assertEqual(record, {'commit': self.rev(f'refs/heads/{cp.branch}'),
                                  'blob': self.rev(f'refs/heads/{cp.branch}:app.py')})
        self.assertEqual(load_checkpoint(cp.task_id).measurements['patch_committed'], record)

    def test_patch_committed_by_hand_above_an_unrelated_commit_is_the_one_recorded(self):
        # Each commit is looked at for what IT carries: the unrelated one below does not carry the patch.
        cp = self.task('plain')
        self.kill(cp, 'after_add')
        (self.wt / 'other.txt').write_text('unrelated\n')
        self.git(self.wt, 'add', '--', 'other.txt')
        identity = ('-c', 'user.email=t@local', '-c', 'user.name=t')
        self.git(self.wt, *identity, 'commit', '-m', 'unrelated', '--only', '--', 'other.txt')
        self.git(self.wt, *identity, 'commit', '-m', 'by hand')
        self.add_clean_filter('tr a-z A-Z')
        self.assert_stays_verified_on_the_one_patch_commit(cp, ORDINARY.replace(b'BROKEN', b'READY'), commits=2)

    def test_patch_commit_below_a_later_commit_is_the_one_recorded(self):
        cp = self.task('plain')
        self.kill(cp, 'after_commit')
        first = self.rev(f'refs/heads/{cp.branch}')
        (self.wt / 'other.txt').write_text('later work\n')
        self.git(self.wt, 'add', '--', 'other.txt')
        self.git(self.wt, '-c', 'user.email=t@local', '-c', 'user.name=t', 'commit', '-m', 'later work')
        self.add_clean_filter('tr a-z A-Z')
        self.assert_stays_verified_on_the_one_patch_commit(cp, ORDINARY.replace(b'BROKEN', b'READY'), commits=2,
                                                           commit=first)

    def test_patch_committed_by_hand_after_a_kill_following_the_add_is_not_committed_again(self):
        cp = self.task('plain')
        self.kill(cp, 'after_add')
        self.git(self.wt, '-c', 'user.email=t@local', '-c', 'user.name=t', 'commit', '-m', 'by hand')
        self.add_clean_filter('tr a-z A-Z')
        self.assert_stays_verified_on_the_one_patch_commit(cp, ORDINARY.replace(b'BROKEN', b'READY'))

    def test_intended_commit_that_never_happened_is_not_taken_for_a_patch_commit(self):
        # Killed after the intent was written and the file staged: no commit exists, so one is made.
        cp = self.task('plain')
        self.kill(cp, 'after_add')
        waiting = load_checkpoint(cp.task_id).measurements
        self.assertEqual(waiting['patch_commit_intended'],
                         {'blob': self.staged_oid(), 'parent': cp.base_ref,
                          'token': waiting['patch_commit_intended']['token']})
        self.assertNotIn('patch_committed', waiting)
        (self.wt / 'other.txt').write_text('unrelated\n')
        self.git(self.wt, 'add', '--', 'other.txt')
        self.git(self.wt, '-c', 'user.email=t@local', '-c', 'user.name=t', 'commit', '-m', 'unrelated',
                 '--only', '--', 'other.txt')
        done = resume_task(cp.task_id)
        self.assertTrue(done.verified_success, done.last_error)
        self.assertEqual(self.commits(cp), 2)
        self.assertEqual(done.measurements['patch_committed']['commit'], self.rev(f'refs/heads/{cp.branch}'))
        self.assertEqual(self.blob(cp), ORDINARY.replace(b'BROKEN', b'READY'))

    def test_blob_derived_anew_before_any_commit_is_the_one_committed_and_recorded(self):
        # Staged, killed, then a filter appears: nothing was committed yet, so git's answer today counts.
        cp = self.task('plain')
        self.kill(cp, 'after_add')
        staged = self.staged_oid()
        self.assertEqual(load_checkpoint(cp.task_id).measurements['patch_commit_intended']['blob'], staged)
        self.add_clean_filter('tr a-z A-Z')
        os.utime(self.target)  # git add reads a file again only when its stat data changed
        done = self.assert_verified_with_one_clean_commit(cp)
        self.assertEqual(self.blob(cp), ORDINARY.replace(b'BROKEN', b'READY').upper())
        record = {'commit': self.rev(f'refs/heads/{cp.branch}'), 'blob': self.rev(f'refs/heads/{cp.branch}:app.py')}
        self.assertNotEqual(record['blob'], staged)
        self.assertEqual(done.measurements['patch_committed'], record)
        self.assertEqual(done.measurements['patch_commit_intended'],
                         {'blob': record['blob'], 'parent': cp.base_ref, 'token': self.token_of(done)})

    def test_git_failure_reported_as_bytes_is_shown_as_text(self):
        cp = self.task('plain')
        self.kill(cp, 'before_add')

        def failing(wt):
            raise subprocess.CalledProcessError(128, ['git', 'count-objects'], stderr=b'fatal: br\xffken\n')

        with patch.object(task_loop, '_object_directories', failing):
            done = self.assert_withheld_with_nothing_committed(cp, 'commit failed')
        self.assertEqual(done.last_error, 'patch not committed: commit failed: fatal: br\ufffdken\n')
        self.assert_verified_with_one_clean_commit(cp)

    def assert_withheld_because_the_branch_lost_the_patch(self, cp, commits):
        index = Path(self.git(self.wt, 'rev-parse', '--path-format=absolute', '--git-path', 'index').strip().decode())
        before, tip = index.read_bytes(), self.rev(f'refs/heads/{cp.branch}')

        def never(*args):
            raise AssertionError('a blob was derived although the patch was committed before')

        for _ in range(3):
            with patch.object(task_loop, '_git_derived_oid', never):
                done = resume_task(cp.task_id)
            self.assertIsNone(done.verified_success)
            self.assertNotIn(done.status, ('verified', 'failed'))
            self.assertTrue(done.last_error.startswith('patch not committed: not committing it again'), done.last_error)
            self.assertIn('no longer carries', done.last_error)
            self.assertIsNone(done.measurements.get('verifier_ref'))
            self.assertEqual(self.commits(cp), commits)
            self.assertEqual(self.rev(f'refs/heads/{cp.branch}'), tip)
            self.assertEqual(index.read_bytes(), before, 'something was staged')

    def test_patch_commit_reset_off_the_task_branch_is_withheld_and_not_committed_again(self):
        cp = self.task('plain')
        record = resume_task(cp.task_id, until='patched').measurements['patch_committed']
        patched = self.target.read_bytes()
        self.git(self.wt, 'reset', '-q', '--hard', cp.base_ref)
        self.target.write_bytes(patched)
        self.assert_withheld_because_the_branch_lost_the_patch(cp, commits=0)
        # The task branch carrying that blob again is all that is asked for.
        self.git(self.wt, 'reset', '-q', '--hard', record['commit'])
        done = self.assert_verified_with_one_clean_commit(cp)
        self.assertEqual(done.measurements['patch_committed'], record)

    def test_target_changed_by_a_later_commit_is_withheld_and_not_committed_again(self):
        cp = self.task('plain')
        resume_task(cp.task_id, until='patched')
        patched = self.target.read_bytes()
        self.target.write_bytes(patched + b'LATER = 1\n')
        self.git(self.wt, '-c', 'user.email=t@local', '-c', 'user.name=t', 'commit', '-m', 'later', '--', 'app.py')
        self.target.write_bytes(patched)
        self.assert_withheld_because_the_branch_lost_the_patch(cp, commits=2)

    def test_patch_commit_lost_after_a_kill_following_the_commit_is_withheld(self):
        # The record is made from the intent on the first resume; the branch then loses the commit.
        cp = self.task('plain')
        self.kill(cp, 'after_commit')
        self.assertTrue(resume_task(cp.task_id, until='patched').measurements['patch_committed'])
        patched = self.target.read_bytes()
        self.git(self.wt, 'reset', '-q', '--hard', cp.base_ref)
        self.target.write_bytes(patched)
        self.assert_withheld_because_the_branch_lost_the_patch(cp, commits=0)

    def test_unreadable_patch_commit_record_is_withheld_and_nothing_is_committed(self):
        cp = self.task('plain')
        self.kill(cp, 'before_add')
        cp = load_checkpoint(cp.task_id)
        cp.measurements['patch_committed'] = 'yes'
        save_checkpoint(cp)
        self.assert_withheld_because_the_branch_lost_the_patch(cp, commits=0)

    def test_patch_commit_record_of_any_other_shape_is_withheld_and_nothing_is_committed(self):
        for index, record in enumerate(('stored', ['stored'], 7)):
            with self.subTest(index=index):
                cp = self.task('plain')
                self.kill(cp, 'before_add')
                cp = load_checkpoint(cp.task_id)
                cp.measurements['patch_committed'] = record
                save_checkpoint(cp)
                self.assert_withheld_because_the_branch_lost_the_patch(cp, commits=0)
                shutil.rmtree(self.root)
                self.root.mkdir()

    def test_malformed_intent_is_not_followed(self):
        malformed = (
            lambda written: 'x',
            lambda written: {},
            lambda written: {'blob': written['blob'], 'token': written['token']},
            lambda written: {'parent': written['parent'], 'token': written['token']},
            lambda written: {**written, 'blob': 7},
            lambda written: {**written, 'parent': None},
            lambda written: {**written, 'parent': '--all'},
            # a revision expression that names the right parent is still not a full object id
            lambda written: {**written, 'parent': 'HEAD~1'},
            # no token, or one that is not what this module writes: the attempt cannot be told from any other
            lambda written: {'blob': written['blob'], 'parent': written['parent']},
            lambda written: {**written, 'token': 7},
            lambda written: {**written, 'token': written['token'] + '\n'},
            lambda written: {**written, 'token': written['token'][:-1]},
            lambda written: {**written, 'token': written['token'].upper() + 'A'},
            lambda written: {**written, 'token': written['token'][:-1] + 'g'},
        )
        for index, make in enumerate(malformed):
            with self.subTest(index=index):
                cp = self.task('plain')
                self.kill(cp, 'after_commit')
                cp = load_checkpoint(cp.task_id)
                written = cp.measurements['patch_commit_intended']
                self.assertEqual(written, {'blob': self.rev(f'{cp.branch}:app.py'), 'parent': cp.base_ref,
                                           'token': self.token_of(cp)})
                self.assertEqual(self.rev('HEAD~1'), written['parent'])
                cp.measurements['patch_commit_intended'] = make(written)
                save_checkpoint(cp)
                self.assertIsNone(task_loop._patch_commit_record(cp, self.wt, cp.branch, 'app.py'))
                self.assertNotIn('patch_committed', load_checkpoint(cp.task_id).measurements)
                self.assert_verified_with_one_clean_commit(cp)
                shutil.rmtree(self.root)
                self.root.mkdir()

    # --- the task's own commit is known by what it is, not by the blob a hook left in it ---

    PATCHED = ORDINARY.replace(b'BROKEN', b'READY')
    BY_HAND = ('-c', 'user.email=t@local', '-c', 'user.name=t')
    REWRITES_THE_STAGED_TARGET = ('oid=$(printf "hooked\\n" | git hash-object -w --stdin)\n'
                                  'git update-index --cacheinfo 100644,$oid,app.py\n')
    REVERTS_THE_STAGED_TARGET = 'git update-index --cacheinfo 100644,$(git rev-parse HEAD:app.py),app.py\n'
    REWORDS_THE_MESSAGE = 'echo "PROJ-1 reworded by a hook" > "$1"\n'
    ADDS_A_TRAILER = 'printf "\\nChange-Id: I0123\\n" >> "$1"\n'
    COMMITS_AGAIN_ON_TOP = ('export GIT_AUTHOR_NAME=h GIT_AUTHOR_EMAIL=h@local GIT_COMMITTER_NAME=h '
                            'GIT_COMMITTER_EMAIL=h@local\n'
                            'c=$(git commit-tree -p HEAD -m "$(git log -1 --format=%s)" "HEAD^{tree}") && '
                            'git update-ref HEAD $c\n')

    def hook(self, name, script):
        common = self.git(self.wt, 'rev-parse', '--path-format=absolute', '--git-common-dir').strip().decode()
        path = Path(common) / 'hooks' / name
        path.parent.mkdir(exist_ok=True)
        path.write_text('#!/bin/sh\n' + script)
        path.chmod(0o755)
        return path

    def subjects(self, cp):
        return self.git(self.wt, 'log', '--format=%s', f'{cp.base_ref}..{cp.branch}').decode().splitlines()

    def empty_commits(self, cp):
        listed = self.git(self.wt, 'rev-list', f'{cp.base_ref}..{cp.branch}').decode().split()
        return [c for c in listed if self.rev(c + '^{tree}') == self.rev(c + '~1^{tree}')]

    def assert_withheld_on_the_one_changed_commit(self, cp, commit, error=None, commits=1):
        """Three resumes: nothing is committed, nothing is verified, and the reason does not move."""
        tip = self.rev(f'refs/heads/{cp.branch}')
        for _ in range(3):
            done = resume_task(cp.task_id)
            self.assertIsNone(done.verified_success)
            self.assertNotIn(done.status, ('verified', 'failed'))
            self.assertIsNone(done.measurements.get('verifier_ref'))
            error = error or done.last_error
            self.assertEqual(done.last_error, error)
            self.assertTrue(error.startswith('patch not committed: not committing it again'), error)
            self.assertIn('hook or filter changed what was committed', error)
            self.assertIn(commit, error)
            self.assertEqual(self.commits(cp), commits, 'the task committed again')
            self.assertEqual(self.rev(f'refs/heads/{cp.branch}'), tip)
            self.assertEqual(done.measurements['patch_committed']['commit'], commit)
        return done

    def test_hook_that_rewrites_the_staged_target_ends_in_one_commit_and_is_withheld(self):
        cp = self.task('plain')
        self.hook('pre-commit', self.REWRITES_THE_STAGED_TARGET)
        first = resume_task(cp.task_id, until='patched')  # the reason is the same from the commit on
        tip = self.rev(f'refs/heads/{cp.branch}')
        self.assertEqual(self.subjects(cp), ['z0int task plain: bounded patch'])
        self.assertEqual(self.blob(cp), b'hooked\n')
        record = first.measurements['patch_committed']
        self.assertEqual(record, {'commit': tip, 'blob': first.measurements['patch_commit_intended']['blob'],
                                  'stored': self.rev(f'{tip}:app.py')})
        self.assertEqual(self.git(self.wt, 'cat-file', 'blob', record['blob']), self.PATCHED)
        self.assertIsNone(first.verified_success)
        done = self.assert_withheld_on_the_one_changed_commit(cp, tip, first.last_error)
        self.assertIn(record['stored'], done.last_error)
        self.assertIn(record['blob'], done.last_error)
        self.assertEqual(done.measurements['patch_committed'], record)
        self.assertEqual(self.empty_commits(cp), [])

    def test_hook_removed_after_it_rewrote_the_commit_adds_no_commit_and_stays_withheld(self):
        cp = self.task('plain')
        hook = self.hook('pre-commit', self.REWRITES_THE_STAGED_TARGET)
        first = resume_task(cp.task_id)
        tip = self.rev(f'refs/heads/{cp.branch}')
        hook.unlink()
        self.assert_withheld_on_the_one_changed_commit(cp, tip, first.last_error)
        self.assertEqual(self.empty_commits(cp), [])
        # The task never commits again. The branch carrying the recorded patch is all that is asked for.
        self.git(self.wt, *self.BY_HAND, 'commit', '-m', 'by hand', '--', 'app.py')
        done = resume_task(cp.task_id)
        self.assertTrue(done.verified_success, done.last_error)
        self.assertIsNone(done.last_error)
        self.assertEqual(self.subjects(cp), ['by hand', 'z0int task plain: bounded patch'])
        self.assertEqual(self.blob(cp), self.PATCHED)
        # Killed before that was verified, and git derives another blob by then: the recorded blob is there.
        cp = load_checkpoint(cp.task_id)
        cp.status, cp.verified_success = 'patched', None
        save_checkpoint(cp)
        self.add_clean_filter('tr a-z A-Z')
        os.utime(self.target, (1, 1))
        done = resume_task(cp.task_id)
        self.assertTrue(done.verified_success, done.last_error)
        self.assertEqual(self.subjects(cp), ['by hand', 'z0int task plain: bounded patch'])

    def test_commit_a_hook_rewrote_is_found_after_a_kill_and_never_repeated(self):
        cp = self.task('plain')
        self.hook('pre-commit', self.REWRITES_THE_STAGED_TARGET)
        self.kill(cp, 'after_commit')
        self.assertNotIn('patch_committed', load_checkpoint(cp.task_id).measurements)
        tip = self.rev(f'refs/heads/{cp.branch}')
        self.assertEqual(self.commits(cp), 1)
        done = self.assert_withheld_on_the_one_changed_commit(cp, tip)
        self.assertEqual(done.measurements['patch_committed']['stored'], self.rev(f'{tip}:app.py'))
        self.assertEqual(self.empty_commits(cp), [])

    def test_commit_whose_message_and_content_hooks_changed_is_still_the_one_commit(self):
        cp = self.task('plain')
        self.hook('pre-commit', self.REWRITES_THE_STAGED_TARGET)
        self.hook('commit-msg', self.REWORDS_THE_MESSAGE)
        with patch.dict(os.environ, {'GIT_COMMITTER_NAME': 'someone', 'GIT_COMMITTER_EMAIL': 's@local'}):
            first = resume_task(cp.task_id)
        self.assertIn(b'committer someone <s@local>', self.git(self.wt, 'cat-file', '-p', cp.branch))
        self.assertEqual(self.subjects(cp), ['PROJ-1 reworded by a hook'])
        self.assert_withheld_on_the_one_changed_commit(cp, self.rev(f'refs/heads/{cp.branch}'), first.last_error)

    def test_reworded_and_rewritten_commit_is_found_after_a_kill(self):
        cp = self.task('plain')
        self.hook('pre-commit', self.REWRITES_THE_STAGED_TARGET)
        self.hook('commit-msg', self.REWORDS_THE_MESSAGE)
        self.kill(cp, 'after_commit')
        self.assertEqual(self.subjects(cp), ['PROJ-1 reworded by a hook'])
        self.assertIn(b'\ncommitter z0int-task-loop <z0int@local> ', self.git(self.wt, 'cat-file', '-p', cp.branch))
        self.assert_withheld_on_the_one_changed_commit(cp, self.rev(f'refs/heads/{cp.branch}'))

    def test_rewritten_commit_with_a_trailer_made_as_somebody_else_is_found_after_a_kill(self):
        cp = self.task('plain')
        self.hook('pre-commit', self.REWRITES_THE_STAGED_TARGET)
        self.hook('commit-msg', self.ADDS_A_TRAILER)
        with patch.dict(os.environ, {'GIT_COMMITTER_NAME': 'someone', 'GIT_COMMITTER_EMAIL': 's@local'}):
            self.kill(cp, 'after_commit')
        tip = self.rev(f'refs/heads/{cp.branch}')
        self.assertIn(b'committer someone <s@local>', self.git(self.wt, 'cat-file', '-p', tip))
        self.assertIn(b'bounded patch\n\nChange-Id: I0123\n', self.git(self.wt, 'cat-file', '-p', tip))
        self.assert_withheld_on_the_one_changed_commit(cp, tip)

    def test_commits_that_only_resemble_the_task_commit_are_not_taken_for_it(self):
        # Neither the task's message nor the task's committer on the recorded parent makes a commit the task's.
        cp = self.task('plain')
        self.kill(cp, 'after_add')
        self.git(self.wt, 'reset', '-q', '--', 'app.py')
        as_the_task = ('-c', 'user.email=z0int@local', '-c', 'user.name=z0int-task-loop')
        (self.wt / 'other.txt').write_text('one\n')
        self.git(self.wt, 'add', '--', 'other.txt')
        # directly on the recorded parent, committed (and authored) as the task commits, under the task's very message
        self.git(self.wt, *as_the_task, 'commit', '-m', 'z0int task plain: bounded patch', '--', 'other.txt')
        self.assertEqual(self.author('HEAD'), 'z0int-task-loop <z0int@local>')
        self.assertEqual(self.rev('HEAD~1'), cp.base_ref)
        (self.wt / 'other.txt').write_text('two\n')
        self.git(self.wt, *self.BY_HAND, 'commit', '-m', 'z0int task plain: bounded patch\n\nChange-Id: I1\n', '--',
                 'other.txt')
        cp = load_checkpoint(cp.task_id)
        self.assertEqual(cp.measurements['patch_commit_intended']['parent'], cp.base_ref)
        self.assertIsNone(task_loop._patch_commit_record(cp, self.wt, cp.branch, 'app.py'))
        self.assertNotIn('patch_committed', load_checkpoint(cp.task_id).measurements)
        done = resume_task(cp.task_id)
        self.assertTrue(done.verified_success, done.last_error)
        self.assertEqual(self.subjects(cp), ['z0int task plain: bounded patch'] * 3)
        tip = self.rev(f'refs/heads/{cp.branch}')
        self.assertEqual(done.measurements['patch_committed'], {'commit': tip, 'blob': self.rev(f'{cp.branch}:app.py')})
        self.assertEqual(self.author(tip), f'z0int-task-loop <z0int+{self.token_of(done)}@local>')
        self.assertEqual(self.blob(cp), self.PATCHED)

    REPLACES_THE_COMMIT_AS_SOMEBODY_ELSE = (
        'export GIT_AUTHOR_NAME=h GIT_AUTHOR_EMAIL=h@local GIT_COMMITTER_NAME=h GIT_COMMITTER_EMAIL=h@local\n'
        'c=$(git commit-tree -p HEAD~1 -m "$(git log -1 --format=%B)" "HEAD^{tree}") && git update-ref HEAD $c\n')

    def test_commit_a_hook_replaced_under_another_author_is_the_one_read_back_and_never_repeated(self):
        # A post-commit hook replaced the commit, token and all: what stands on the recorded parent
        # straight after git commit succeeded is the task's commit all the same.
        cp = self.task('plain')
        self.hook('pre-commit', self.REWRITES_THE_STAGED_TARGET)
        self.hook('commit-msg', self.REWORDS_THE_MESSAGE)
        self.hook('post-commit', self.REPLACES_THE_COMMIT_AS_SOMEBODY_ELSE)
        first = resume_task(cp.task_id, until='patched')
        tip = self.rev(f'refs/heads/{cp.branch}')
        self.assertEqual(self.author(tip), 'h <h@local>')
        self.assertEqual(self.subjects(cp), ['PROJ-1 reworded by a hook'])
        self.assertEqual(self.blob(cp), b'hooked\n')
        self.assertEqual(self.rev(f'{tip}~1'), cp.base_ref)
        self.assertEqual(first.measurements['patch_committed']['commit'], tip)
        self.assert_withheld_on_the_one_changed_commit(cp, tip, first.last_error)
        self.assertEqual(self.empty_commits(cp), [])

    def test_commit_a_hook_stacked_on_the_task_commit_is_not_recorded_in_its_place(self):
        cp = self.task('plain')
        self.hook('post-commit', self.COMMITS_AGAIN_ON_TOP)
        done = resume_task(cp.task_id)
        self.assertEqual(self.subjects(cp), ['z0int task plain: bounded patch'] * 2)
        self.assertTrue(done.verified_success, done.last_error)
        self.assertEqual(done.measurements['patch_committed'],
                         {'commit': self.rev(f'refs/heads/{cp.branch}~1'), 'blob': self.rev(f'{cp.branch}:app.py')})
        self.assertEqual(resume_task(cp.task_id).to_dict(), done.to_dict())
        self.assertEqual(self.commits(cp), 2)

    def test_branch_carrying_the_recorded_blob_again_is_verified_whatever_git_derives_by_then(self):
        # The recorded blob is what git stored under the filter of the day; the hook left something else.
        cp = self.task('plain')
        self.add_clean_filter('tr a-z A-Z')
        hook = self.hook('pre-commit', self.REWRITES_THE_STAGED_TARGET)
        first = resume_task(cp.task_id)
        tip = self.rev(f'refs/heads/{cp.branch}')
        self.assert_withheld_on_the_one_changed_commit(cp, tip, first.last_error)
        hook.unlink()
        self.git(self.wt, *self.BY_HAND, 'commit', '-m', 'by hand', '--', 'app.py')
        self.assertEqual(self.blob(cp), self.PATCHED.upper())
        self.git(self.wt, 'config', 'filter.late.clean', 'cat')  # git derives the plain file from now on
        os.utime(self.target, (1, 1))
        self.assertNotEqual(self.derive(cp), self.rev(f'{cp.branch}:app.py'), 'git must derive another blob now')
        for _ in range(2):
            done = resume_task(cp.task_id)
            self.assertTrue(done.verified_success, done.last_error)
            self.assertEqual(self.subjects(cp), ['by hand', 'z0int task plain: bounded patch'])
            self.assertEqual(self.blob(cp), self.PATCHED.upper())
            self.assertEqual(done.measurements['patch_committed']['commit'], tip)

    def test_first_parent_and_author_are_read_from_the_commit_object(self):
        cp = self.task('plain')
        base = self.rev('HEAD')
        self.git(self.wt, 'checkout', '-q', '-b', 'side')
        self.git(self.wt, *self.BY_HAND, 'commit', '-q', '--allow-empty', '-m', 'side')
        self.git(self.wt, 'checkout', '-q', cp.branch)
        self.git(self.wt, *self.BY_HAND, 'commit', '-q', '--allow-empty', '--author', 'm n <o@p>', '-m', 'mine')
        mine = self.rev('HEAD')
        self.git(self.wt, *self.BY_HAND, 'merge', '-q', '--no-ff', '-m', 'two\n\nparents\n', 'side')
        self.assertEqual(task_loop._commit_facts(self.wt, self.rev('HEAD')), (mine, b't <t@local>'))
        self.assertEqual(task_loop._commit_facts(self.wt, mine), (base, b'm n <o@p>'))
        self.assertEqual(task_loop._commit_facts(self.wt, '0' * 40), (None, b''))
        # the author is the header line, not the committer and not a line of the message that starts like one
        self.git(self.wt, *self.BY_HAND, 'commit', '-q', '--allow-empty', '--author', 'a b <c@d>', '-m',
                 'subject\n\nauthor x <y@z> 1 +0000\nparent ' + '1' * 40 + '\n')
        self.assertEqual(task_loop._commit_facts(self.wt, self.rev('HEAD')), (self.rev('HEAD~1'), b'a b <c@d>'))

    def test_target_the_task_branch_does_not_have_is_committed_as_a_new_file(self):
        cp = self.task('plain', rel='new.py', tracked='app.py')
        (self.wt / 'new.py').write_bytes(ORDINARY)
        self.target = self.wt / 'new.py'
        done = self.assert_verified_with_one_clean_commit(cp, rel='new.py')
        self.assertEqual(self.blob(cp, 'new.py'), self.PATCHED)
        self.assertEqual(done.measurements['patch_committed'],
                         {'commit': self.rev(f'refs/heads/{cp.branch}'), 'blob': self.rev(f'{cp.branch}:new.py')})

    def test_hook_that_reverts_the_staged_target_never_gets_a_second_task_commit(self):
        # Git runs the hook after it has decided there is something to commit; the task then never commits again.
        cp = self.task('plain')
        self.hook('pre-commit', self.REVERTS_THE_STAGED_TARGET)
        first = resume_task(cp.task_id)
        tip = self.rev(f'refs/heads/{cp.branch}')
        self.assertEqual(self.commits(cp), 1)
        self.assertEqual(self.blob(cp), ORDINARY)
        self.assert_withheld_on_the_one_changed_commit(cp, tip, first.last_error)
        # A filter under which git derives the entry the branch already had is no evidence of a commit.
        self.add_clean_filter('sed s/READY/BROKEN/')
        self.assert_withheld_on_the_one_changed_commit(cp, tip, first.last_error)

    def test_git_itself_refuses_an_empty_commit_when_the_branch_gained_the_blob_meanwhile(self):
        cp = self.task('plain')
        real, raced = task_loop._run_git, []

        def committed_by_hand_first(args, **kwargs):
            if args[0] == 'add' and not raced:
                raced.append(self.git(self.wt, 'add', '--', 'app.py'))
                self.git(self.wt, *self.BY_HAND, 'commit', '-m', 'by hand')
            return real(args, **kwargs)

        with patch.object(task_loop, '_run_git', committed_by_hand_first):
            first = resume_task(cp.task_id, until='patched')
        self.assertTrue(raced)
        self.assertIn('patch not committed: commit failed', first.last_error)
        self.assertEqual(self.subjects(cp), ['by hand'])
        done = self.assert_verified_with_one_clean_commit(cp)
        self.assertEqual(self.subjects(cp), ['by hand'])
        self.assertEqual(self.empty_commits(cp), [])
        self.assertEqual(done.measurements['patch_committed']['commit'], self.rev(f'refs/heads/{cp.branch}'))

    # --- the task's own commit is found again by the token it was made with ---

    SOMEONE = {'GIT_COMMITTER_NAME': 'someone', 'GIT_COMMITTER_EMAIL': 's@local'}
    SOMEONE_WROTE_IT_TOO = {**SOMEONE, 'GIT_AUTHOR_NAME': 'someone', 'GIT_AUTHOR_EMAIL': 's@local'}
    RESTAGES_THE_TARGET = 'git add -- app.py\n'
    CONSTANT_FILTER = "sh -c 'cat >/dev/null; echo hooked'"  # git derives b'hooked\n' from any file

    def token_of(self, cp):
        token = load_checkpoint(cp.task_id).measurements['patch_commit_intended']['token']
        self.assertRegex(token, r'\A[0-9a-f]{32}\Z')
        return token

    def author(self, commit):
        return self.git(self.wt, 'log', '-1', '--format=%an <%ae>', commit).decode().strip()

    def task_commits(self, cp):
        """Every commit on the task branch that was authored as the task."""
        listed = self.git(self.wt, 'log', '--format=%H %an', f'{cp.base_ref}..{cp.branch}').decode().splitlines()
        return [line.split(' ', 1)[0] for line in listed if line.split(' ', 1)[1] == 'z0int-task-loop']

    def user_commit_lands_before(self, verb):
        real, landed = task_loop._run_git, []

        def run_git(args, **kwargs):
            if (args[0] == verb or (verb == 'commit' and 'commit' in args)) and not landed:
                (self.wt / 'raced.txt').write_text('user\n')
                self.git(self.wt, 'add', '--', 'raced.txt')
                landed.append(self.git(self.wt, *self.BY_HAND, 'commit', '--no-verify', '-m', 'racing', '--',
                                       'raced.txt'))
            return real(args, **kwargs)

        return patch.object(task_loop, '_run_git', run_git)

    def test_task_commit_is_authored_with_the_token_recorded_before_anything_was_staged(self):
        for name, env in (('plain', {}), ('author-in-the-environment', self.SOMEONE_WROTE_IT_TOO)):
            with self.subTest(name=name), patch.dict(os.environ, env):
                cp = self.task('plain')
                self.kill(cp, 'before_add')
                token = self.token_of(cp)
                self.assertEqual(self.commits(cp), 0)
                done = self.assert_verified_with_one_clean_commit(cp)
                self.assertEqual(self.token_of(done), token)
                self.assertEqual(self.author(cp.branch), f'z0int-task-loop <z0int+{token}@local>')
                self.assertEqual(task_loop._task_author(token), self.author(cp.branch))
                tip = self.rev(f'refs/heads/{cp.branch}')
                self.assertTrue(task_loop._is_task_commit(self.wt, tip, token))
                self.assertEqual(task_loop._commit_facts(self.wt, tip)[1], self.author(cp.branch).encode())
                shutil.rmtree(self.root)
                self.root.mkdir()

    def test_attempt_on_another_parent_gets_another_token(self):
        cp = self.task('plain')
        self.kill(cp, 'after_add')
        first = self.token_of(cp)
        (self.wt / 'other.txt').write_text('unrelated\n')
        self.git(self.wt, 'add', '--', 'other.txt')
        self.git(self.wt, *self.BY_HAND, 'commit', '-m', 'unrelated', '--only', '--', 'other.txt')
        self.kill(cp, 'before_commit')
        second = self.token_of(cp)
        self.assertNotEqual(second, first)
        self.kill(cp, 'before_commit')  # the same attempt again: the same token
        self.assertEqual(self.token_of(cp), second)
        done = resume_task(cp.task_id)
        self.assertTrue(done.verified_success, done.last_error)
        self.assertEqual(self.author(cp.branch), f'z0int-task-loop <z0int+{second}@local>')
        self.assertEqual(len(self.task_commits(cp)), 1)

    def test_unusable_intent_is_replaced_before_anything_is_committed(self):
        unusable = (
            lambda written: 'x',
            lambda written: {'blob': written['blob'], 'parent': written['parent']},
            lambda written: {**written, 'token': 7},
            lambda written: {**written, 'token': written['token'] + '\n'},
            lambda written: {**written, 'token': 'not-a-token'},
            lambda written: {**written, 'token': written['token'][:-1] + 'g'},
        )
        for index, make in enumerate(unusable):
            with self.subTest(index=index):
                cp = self.task('plain')
                self.kill(cp, 'after_add')
                cp = load_checkpoint(cp.task_id)
                written = cp.measurements['patch_commit_intended']
                cp.measurements['patch_commit_intended'] = make(written)
                save_checkpoint(cp)
                done = self.assert_verified_with_one_clean_commit(cp)
                self.assertIs(type(done.measurements['patch_commit_intended']['token']), str)
                token = self.token_of(done)
                self.assertNotEqual(token, written['token'])
                self.assertEqual(done.measurements['patch_commit_intended'],
                                 {'blob': written['blob'], 'parent': written['parent'], 'token': token})
                self.assertEqual(self.author(cp.branch), f'z0int-task-loop <z0int+{token}@local>')
                shutil.rmtree(self.root)
                self.root.mkdir()

    def test_intent_is_written_again_when_the_entry_a_commit_was_needed_over_is_another(self):
        cp = self.task('plain')
        self.kill(cp, 'after_add')
        cp = load_checkpoint(cp.task_id)
        written, over = cp.measurements['patch_commit_intended'], cp.measurements['patch_commit_needed_over']
        self.assertEqual(over, self.rev(f'{cp.branch}:app.py'))
        cp.measurements['patch_commit_needed_over'] = ''
        save_checkpoint(cp)
        self.kill(cp, 'before_commit')
        again = load_checkpoint(cp.task_id).measurements
        self.assertEqual(again['patch_commit_needed_over'], over)
        self.assertNotEqual(again['patch_commit_intended']['token'], written['token'])
        self.assert_verified_with_one_clean_commit(cp)

    def test_commit_authored_with_another_token_is_not_taken_for_the_task_commit(self):
        cp = self.task('plain')
        self.kill(cp, 'after_add')
        token = self.token_of(cp)
        self.git(self.wt, 'reset', '-q', '--', 'app.py')
        other = 'f' * 32 if token != 'f' * 32 else 'e' * 32
        for number, (author, message) in enumerate((
                (f'z0int-task-loop <z0int+{other}@local>', 'another attempt'),
                ('z0int-task-loop <z0int@local>', f'z0int+{token}@local'),
                (f'z0int-task-loop <z0int+{token}@local.example>', 'longer'),
                (f'somebody <z0int+{token}@local>', 'another name'))):
            (self.wt / 'other.txt').write_text(f'{number}\n')
            self.git(self.wt, 'add', '--', 'other.txt')
            self.git(self.wt, *self.BY_HAND, 'commit', '--author', author, '-m', message, '--only', '--', 'other.txt')
            self.assertFalse(task_loop._is_task_commit(self.wt, self.rev('HEAD'), token))
        cp = load_checkpoint(cp.task_id)
        self.assertIsNone(task_loop._patch_commit_record(cp, self.wt, cp.branch, 'app.py'))
        done = resume_task(cp.task_id)
        self.assertTrue(done.verified_success, done.last_error)
        self.assertEqual(self.commits(cp), 5)
        self.assertEqual(done.measurements['patch_committed']['commit'], self.rev(f'refs/heads/{cp.branch}'))

    def test_reworded_and_rewritten_commit_made_as_somebody_else_is_found_after_a_kill_by_its_token(self):
        # Hooks changed the message and the content, and the environment names the committer (and the author):
        # only the token says whose commit it is.
        for name, env in (('committer', self.SOMEONE), ('committer-and-author', self.SOMEONE_WROTE_IT_TOO)):
            for point in ('after_commit', 'read_back'):
                with self.subTest(name=name, point=point), patch.dict(os.environ, env):
                    cp = self.task('plain')
                    self.hook('pre-commit', self.REWRITES_THE_STAGED_TARGET)
                    self.hook('commit-msg', self.REWORDS_THE_MESSAGE)
                    result = subprocess.run([sys.executable, '-c', KILL_OR_RACE, cp.task_id, point, '-'],
                                            text=True, capture_output=True, timeout=60)
                    self.assertEqual(result.returncode, 90, result.stderr)
                    self.assertNotIn('patch_committed', load_checkpoint(cp.task_id).measurements)
                    tip = self.rev(f'refs/heads/{cp.branch}')
                    raw = self.git(self.wt, 'cat-file', '-p', tip)
                    self.assertIn(b'\ncommitter someone <s@local> ', raw)
                    self.assertEqual(self.subjects(cp), ['PROJ-1 reworded by a hook'])
                    self.assert_withheld_on_the_one_changed_commit(cp, tip)
                    self.assertEqual(self.empty_commits(cp), [])
                    self.assertIn(f'\nauthor z0int-task-loop <z0int+{self.token_of(cp)}@local> '.encode(), raw)
                    shutil.rmtree(self.root)
                    self.root.mkdir()

    def test_commit_made_on_a_user_commit_that_landed_meanwhile_is_still_the_one_task_commit(self):
        # The recorded parent is no longer the commit's parent, and hooks changed message and content.
        for verb in ('add', 'commit'):
            for point in (None, 'after_commit'):
                with self.subTest(verb=verb, point=point):
                    cp = self.task('plain')
                    self.hook('pre-commit', self.REWRITES_THE_STAGED_TARGET)
                    self.hook('commit-msg', self.REWORDS_THE_MESSAGE)
                    first = None
                    if point:
                        result = subprocess.run(
                            [sys.executable, '-c', KILL_OR_RACE, cp.task_id, point, 'before_' + verb],
                            text=True, capture_output=True, timeout=60)
                        self.assertEqual(result.returncode, 90, result.stderr)
                    else:
                        with self.user_commit_lands_before(verb):
                            first = resume_task(cp.task_id).last_error
                    self.assertEqual(self.subjects(cp), ['PROJ-1 reworded by a hook', 'racing'])
                    tip = self.rev(f'refs/heads/{cp.branch}')
                    self.assertEqual(load_checkpoint(cp.task_id).measurements['patch_commit_intended']['parent'],
                                     cp.base_ref)
                    self.assertEqual(self.rev(f'{tip}~2'), cp.base_ref)
                    self.assert_withheld_on_the_one_changed_commit(cp, tip, first, commits=2)
                    self.assertEqual(self.task_commits(cp), [tip])
                    self.assertEqual(self.empty_commits(cp), [])
                    shutil.rmtree(self.root)
                    self.root.mkdir()

    def test_filter_under_which_git_derives_what_a_hook_committed_does_not_verify_the_changed_commit(self):
        def never(*args):
            raise AssertionError('a blob was derived although the task commit exists')

        for point in (None, 'after_commit'):
            with self.subTest(point=point):
                cp = self.task('plain')
                hook = self.hook('pre-commit', self.REWRITES_THE_STAGED_TARGET)
                if point:
                    self.kill(cp, point)
                first = resume_task(cp.task_id)
                tip = self.rev(f'refs/heads/{cp.branch}')
                hook.unlink()
                self.add_clean_filter(self.CONSTANT_FILTER)
                os.utime(self.target, (1, 1))
                self.assertEqual(self.derive(cp), self.rev(f'{tip}:app.py'), 'git must derive what the hook stored')
                self.assertEqual(self.blob(cp), b'hooked\n')
                with patch.object(task_loop, '_git_derived_oid', never):
                    done = self.assert_withheld_on_the_one_changed_commit(cp, tip, first.last_error)
                self.assertNotEqual(self.rev(f'{tip}:app.py'), done.measurements['patch_committed']['blob'])
                self.assertEqual(self.empty_commits(cp), [])
                shutil.rmtree(self.root)
                self.root.mkdir()

    def assert_one_task_commit(self, pre, msg, someone, point, race):
        """Whatever the hooks, the kill and the user commit: at most one task commit, at every stage."""
        cp = self.task('plain')
        for name, script in (('pre-commit', pre), ('commit-msg', msg)):
            if script:
                self.hook(name, script)
        patched, reverts = ORDINARY.replace(b'BROKEN', b'READY'), pre == self.REVERTS_THE_STAGED_TARGET
        changes = reverts or pre == self.REWRITES_THE_STAGED_TARGET

        def stage():
            mine, empty = self.task_commits(cp), self.empty_commits(cp)
            self.assertLessEqual(len(mine), 1, 'the task committed twice')
            self.assertEqual(empty, mine if reverts else [], 'an empty commit that git did not force')
            self.assertLessEqual(self.commits(cp), 1 + bool(race))
            return mine

        with patch.dict(os.environ, self.SOMEONE if someone else {}):
            result = subprocess.run([sys.executable, '-c', KILL_OR_RACE, cp.task_id, point or '-', race or '-'],
                                    text=True, capture_output=True, timeout=60)
            self.assertEqual(result.returncode, 90 if point else 0, result.stderr)
            stage()
            errors = []
            for _ in range(3):
                done = resume_task(cp.task_id)
                mine = stage()
                errors.append(done.last_error)
                self.assertEqual(done.verified_success is True, self.blob(cp) == patched and not changes,
                                 done.last_error)
        self.assertEqual(len(mine), 1)
        self.assertEqual(self.commits(cp), 1 + bool(race))
        self.assertEqual(done.measurements['patch_committed']['commit'], mine[0])
        self.assertEqual(self.author(mine[0]), f'z0int-task-loop <z0int+{self.token_of(done)}@local>')
        self.assertEqual(len(set(errors)), 1, errors)
        if changes:
            self.assertIn('hook or filter changed what was committed', errors[0])
            self.assertNotEqual(done.status, 'verified')
            self.assertEqual(self.blob(cp), ORDINARY if reverts else b'hooked\n')
        else:
            self.assertIsNone(errors[0])
            self.assertEqual(done.status, 'verified')

    # --- a patch committed by hand before the task ever asked git what it would store ---

    def committed_by_hand_before_anything_was_derived(self):
        cp = self.task('plain')
        self.kill(cp, 'before_symbolic-ref')  # straight after the write: nothing derived, no intent
        self.assertEqual(self.target.read_bytes(), self.PATCHED)
        self.assertNotIn('patch_commit_intended', load_checkpoint(cp.task_id).measurements)
        self.git(self.wt, *self.BY_HAND, 'commit', '-m', 'by hand', '--', 'app.py')
        return cp

    def test_patch_committed_by_hand_before_anything_was_derived_is_not_committed_again(self):
        for name, touch in (('filter', False), ('filter-and-new-stat-data', True)):
            with self.subTest(name=name):
                cp = self.committed_by_hand_before_anything_was_derived()
                self.add_clean_filter('tr a-z A-Z')
                if touch:
                    os.utime(self.target, (1, 1))
                tip = self.rev(f'refs/heads/{cp.branch}')
                record = {'commit': tip, 'blob': self.rev(f'{tip}:app.py')}
                for _ in range(3):
                    done = resume_task(cp.task_id)
                    self.assertTrue(done.verified_success, done.last_error)
                    self.assertIsNone(done.last_error)
                    self.assertEqual(self.subjects(cp), ['by hand'])
                    self.assertEqual(self.blob(cp), self.PATCHED)
                    self.assertEqual(done.measurements['patch_committed'], record)
                self.assertEqual(self.staged_oid(), record['blob'])
                shutil.rmtree(self.root)
                self.root.mkdir()

    def test_patch_found_committed_by_hand_is_durable_at_once(self):
        cp = self.committed_by_hand_before_anything_was_derived()
        self.add_clean_filter('tr a-z A-Z')
        cp = load_checkpoint(cp.task_id)
        self.assertIsNone(task_loop._commit_patch(cp))
        tip = self.rev(f'refs/heads/{cp.branch}')
        self.assertEqual(load_checkpoint(cp.task_id).measurements['patch_committed'],
                         {'commit': tip, 'blob': self.rev(f'{tip}:app.py')})
        self.assertEqual(self.subjects(cp), ['by hand'])

    def test_branch_holding_the_after_image_under_another_index_entry_is_not_taken_as_clean(self):
        # Branch and worktree agree, the index does not: the path is not clean, so git's answer today counts.
        cp = self.committed_by_hand_before_anything_was_derived()
        other = subprocess.run(['git', 'hash-object', '-w', '--stdin'], cwd=self.wt, input=b'staged by hand\n',
                               capture_output=True, check=True).stdout.strip().decode()
        self.git(self.wt, 'update-index', '--cacheinfo', f'100644,{other},app.py')
        self.add_clean_filter('tr a-z A-Z')
        done = resume_task(cp.task_id)
        self.assertTrue(done.verified_success, done.last_error)
        self.assertEqual(self.subjects(cp), ['z0int task plain: bounded patch', 'by hand'])
        self.assertEqual(self.blob(cp), self.PATCHED.upper())

    # --- carriage returns: every git output that carries a path is read as bytes ---

    CARRIAGE_RETURNS = ('c\rr', 'c\r\nr', 'cr\r')

    def assert_verified_with_one_commit_of(self, cp, canonical):
        done = resume_task(cp.task_id)
        self.assertTrue(done.verified_success, done.last_error)
        self.assertEqual(done.status, 'verified')
        self.assertEqual(self.commits(cp), 1)
        self.assertEqual(self.git(self.wt, 'diff-tree', '--no-commit-id', '--name-only', '-r', '-z', cp.branch),
                         canonical.encode() + b'\0')
        self.assertEqual(self.git(self.wt, 'status', '--porcelain', '-z'), b'')
        self.assertEqual(resume_task(cp.task_id).to_dict(), done.to_dict())
        self.assertEqual(self.commits(cp), 1)

    def carriage_return_in_the_repository_path_verifies(self, directory):
        for config in ((), (('core.autocrlf', 'true'),)):
            for points in ((), ('before_add',), ('after_add',), ('after_commit',)):
                with self.subTest(directory=directory, config=config, points=points):
                    cp = self.task('cr', config=config, directory=directory)
                    self.assertIn(directory, cp.base_repo)
                    self.kill(cp, *points)
                    self.assert_verified_with_one_commit_of(cp, 'app.py')
                    self.assertEqual(self.scratch_left(cp), [])
                    shutil.rmtree(self.root)
                    self.root.mkdir()

    def test_repository_path_with_a_carriage_return_verifies(self):
        self.carriage_return_in_the_repository_path_verifies('re\rpo')

    def test_repository_path_with_a_crlf_verifies(self):
        self.carriage_return_in_the_repository_path_verifies('re\r\npo')

    def test_repository_path_with_a_trailing_carriage_return_verifies(self):
        self.carriage_return_in_the_repository_path_verifies('repo\r')

    def test_repository_under_a_parent_directory_with_a_carriage_return_verifies(self):
        for parent in self.CARRIAGE_RETURNS:
            self.carriage_return_in_the_repository_path_verifies(parent + '/repo')

    def carriage_return_in_the_target_path_verifies(self, rel):
        for config in ((), (('core.autocrlf', 'true'),)):
            for points in ((), ('before_add',), ('after_add',), ('after_commit',)):
                with self.subTest(rel=rel, config=config, points=points):
                    cp = self.task('cr', config=config, rel=rel)
                    self.assertEqual(cp.patch['relative_path'], rel)
                    self.kill(cp, *points)
                    self.assert_verified_with_one_commit_of(cp, rel)
                    self.assertEqual(self.git(self.wt, 'cat-file', 'blob', self.rev(f'{cp.branch}^{{tree}}:{rel}')),
                                     ORDINARY.replace(b'BROKEN', b'READY'))
                    shutil.rmtree(self.root)
                    self.root.mkdir()

    def test_target_name_with_a_carriage_return_verifies(self):
        self.carriage_return_in_the_target_path_verifies('c\rr.py')

    def test_target_name_with_a_crlf_verifies(self):
        self.carriage_return_in_the_target_path_verifies('c\r\nr.py')

    def test_target_name_with_a_trailing_carriage_return_verifies(self):
        self.carriage_return_in_the_target_path_verifies('app.py\r')

    def test_target_under_a_directory_with_a_carriage_return_verifies(self):
        for parent in self.CARRIAGE_RETURNS:
            self.carriage_return_in_the_target_path_verifies(parent + '/a.py')

    def test_git_listings_keep_carriage_returns_in_names(self):
        names = ['c\rr.py', 'c\r\nr.py', 'app.py\r', 'd\r/a.py', 'new\nline.py']
        cp = self.task('cr-names', rel=names[0])
        for name in names[1:]:
            (self.wt / name).parent.mkdir(exist_ok=True)
            (self.wt / name).write_bytes(ORDINARY)
        self.git(self.wt, 'add', '-A')
        self.git(self.wt, '-c', 'user.email=t@local', '-c', 'user.name=t', 'commit', '-m', 'names')
        for name in names:
            with self.subTest(name=name):
                oid = self.rev(f'{cp.branch}^{{tree}}:{name}')
                self.assertEqual(task_loop._canonical_path(self.wt, name), name)
                self.assertEqual(task_loop._canonical_path(self.wt, './' + name), name)
                self.assertEqual(task_loop._branch_blob_oid(self.wt, cp.branch, name), oid)
                self.assertEqual(task_loop._staged_blob_oid(self.wt, name), oid)
        # a name that differs only in its line ending is another file
        self.assertIsNone(task_loop._branch_blob_oid(self.wt, cp.branch, 'c\nr.py'))
        self.assertIsNone(task_loop._staged_blob_oid(self.wt, 'c\nr.py'))
        self.assertIsNone(task_loop._canonical_path(self.wt, 'c\nr.py'))

    # --- scratch areas: one place per task, and none is left behind ---

    def scratch_anywhere(self, cp, tmpdir):
        return sorted(tmpdir.rglob('z0int-derive-*')) + sorted(self.derive_root(cp).glob('z0int-derive-*'))

    def test_kill_9_while_deriving_leaves_no_scratch_directory_behind(self):
        for point in ('read-tree', 'add'):
            with self.subTest(point=point):
                tmpdir = self.root / 'tmp'
                tmpdir.mkdir()
                env = dict(os.environ, TMPDIR=str(tmpdir))
                cp = self.task('killed', config=(('core.autocrlf', 'true'),))
                for kills in (1, 2, 3):
                    killed = subprocess.run([sys.executable, '-c', KILL_9_WHILE_DERIVING, cp.task_id, point],
                                            capture_output=True, timeout=30, env=env)
                    self.assertEqual(killed.returncode, -signal.SIGKILL, killed.stderr)
                    self.assertEqual(list(tmpdir.iterdir()), [], 'left in TMPDIR, where nothing ever removes it')
                    left = self.scratch_anywhere(cp, tmpdir)
                    self.assertEqual(len(left), 1, f'after {kills} kills: {left}')  # the killed one, not a pile
                    self.assertEqual(left[0].parent, self.derive_root(cp))
                resumed = subprocess.run([sys.executable, '-c', SOCKET_GUARD + RESUME, cp.task_id],
                                         capture_output=True, timeout=30, env=env)
                self.assertEqual(resumed.returncode, 0, resumed.stderr)
                self.assertEqual(self.scratch_anywhere(cp, tmpdir), [])
                self.assertEqual(sorted(path.name for path in self.derive_root(cp).iterdir()), ['lock'])
                self.assertTrue(load_checkpoint(cp.task_id).verified_success)
                self.assertEqual(self.commits(cp), 1)
                self.assertEqual(self.git(self.wt, 'status', '--porcelain'), b'')
                shutil.rmtree(self.root)
                self.root.mkdir()

    def test_scratch_directory_of_a_running_derivation_is_never_removed(self):
        import fcntl
        cp = self.task('plain', config=(('core.autocrlf', 'true'),))
        self.kill(cp, 'before_add')
        expected = self.derive(cp)
        root = self.derive_root(cp)
        in_use = root / 'z0int-derive-in-use'
        (in_use / 'objects').mkdir(parents=True)
        with open(root / 'lock', 'a+') as held:  # what a running derivation holds
            fcntl.flock(held, fcntl.LOCK_EX)
            other = subprocess.Popen([sys.executable, '-c', SOCKET_GUARD + DERIVE, str(self.wt), cp.branch, 'app.py',
                                      str(root)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.addCleanup(other.kill)
            with self.assertRaises(subprocess.TimeoutExpired):
                other.wait(timeout=3)
            self.assertTrue((in_use / 'objects').is_dir(), 'a scratch directory in use was removed')
        out, err = other.communicate(timeout=30)
        self.assertEqual(other.returncode, 0, err)
        self.assertEqual(out.decode().strip(), expected)
        self.assertFalse(in_use.exists(), 'nobody holds it any more: it is stale and must go')
        self.assertEqual(sorted(path.name for path in root.iterdir()), ['lock'])

    def test_scratch_directory_is_removed_when_git_fails_in_it(self):
        cp = self.task('plain')
        self.kill(cp, 'before_add')
        with self.assertRaises(subprocess.CalledProcessError):
            self.derive(cp, branch='no-such-branch')
        self.assertEqual(self.scratch_left(cp), [])

    # --- alternates: the scratch object directory adds no level to the chain ---

    def shared_chain(self, levels, origin='origin'):
        """The task's repository becomes the last of ``levels`` nested ``git clone --shared``."""
        def nest(repo):
            previous = self.root / origin
            previous.parent.mkdir(parents=True, exist_ok=True)
            repo.rename(previous)
            for index in range(levels - 1):
                self.git(self.root, 'clone', '-q', '--shared', str(previous), str(self.root / f'clone-{index}'))
                previous = self.root / f'clone-{index}'
            self.git(self.root, 'clone', '-q', '--shared', str(previous), str(repo))
            listed = self.git(repo, 'count-objects', '-v').split(b'\n')
            self.assertEqual(len([line for line in listed if line.startswith(b'alternate: ')]), levels)
            self.assertEqual(list((repo / '.git' / 'objects').glob('[0-9a-f][0-9a-f]')), [])
        return nest

    def scratch_runs(self):
        """Record every git run in a scratch area: (alternates file, stderr)."""
        real, runs = subprocess.run, []

        def run(args, *rest, **kwargs):
            result = real(args, *rest, **kwargs)
            scratch = (kwargs.get('env') or {}).get('GIT_OBJECT_DIRECTORY')
            if scratch:
                runs.append((Path(scratch, 'info', 'alternates').read_bytes(), result.stderr))
            return result

        return patch.object(subprocess, 'run', run), runs

    def nested_shared_clones_verify(self, levels):
        cp = self.task(f'chain-{levels}', after_commit=self.shared_chain(levels))
        self.kill(cp, 'before_add')
        patcher, runs = self.scratch_runs()
        with patcher:
            self.assert_verified_with_one_clean_commit(cp)
        self.assertEqual(len(runs), 3)  # read-tree, add, ls-files: derived once, never again
        for alternates, stderr in runs:
            self.assertEqual(alternates.count(b'\n'), levels + 1, 'every object directory, each listed directly')
            self.assertNotIn('too deep', stderr if isinstance(stderr, str) else stderr.decode())
        self.assertEqual(self.blob(cp), ORDINARY.replace(b'BROKEN', b'READY'))

    def test_repository_four_shared_clones_deep_verifies(self):
        self.nested_shared_clones_verify(4)

    def test_repository_five_shared_clones_deep_verifies(self):
        self.nested_shared_clones_verify(5)

    def test_repository_six_shared_clones_deep_the_deepest_git_reads_verifies(self):
        self.nested_shared_clones_verify(6)

    def test_scratch_alternates_list_the_deepest_object_directory_first(self):
        cp = self.task('ordered', after_commit=self.shared_chain(3))
        self.kill(cp, 'before_add')
        patcher, runs = self.scratch_runs()
        with patcher:
            self.derive(cp)
        own = lambda path: b'"' + str(path / '.git' / 'objects').encode() + b'"\n'  # noqa: E731
        plain = lambda path: str(path / '.git' / 'objects').encode() + b'\n'  # noqa: E731
        self.assertEqual(runs[0][0], plain(self.root / 'origin') + plain(self.root / 'clone-0')
                         + plain(self.root / 'clone-1') + own(self.root / 'ordered'))

    def test_shared_clone_of_an_awkwardly_named_repository_verifies(self):
        # git writes these names unquoted into the clone's alternates file and reports them quoted.
        for index, origin in enumerate(('or:ig "in"', 'ori\rgin ', '#ori\\gin\tü', 'a\rb/c:d/origin')):
            for levels in (1, 2):
                with self.subTest(origin=origin, levels=levels):
                    cp = self.task(f'awkward-{index}', after_commit=self.shared_chain(levels, origin))
                    self.kill(cp, 'before_add')
                    self.assert_verified_with_one_clean_commit(cp)
                    shutil.rmtree(self.root)
                    self.root.mkdir()



def _one_task_commit_case(pre, msg, someone, point, race):
    def test(self):
        self.assert_one_task_commit(pre, msg, someone, point, race)
    return test


# Hooks that reword, add a trailer, re-stage, rewrite or revert; the committer named by the environment or
# not; a kill at every step from the add to the durable record; a user commit landing before the add or
# the commit. One test each.
_HOOKS = {
    'reword': (None, GitDerivedBlobTests.REWORDS_THE_MESSAGE),
    'restage_trailer': (GitDerivedBlobTests.RESTAGES_THE_TARGET, GitDerivedBlobTests.ADDS_A_TRAILER),
    'rewrite_reword': (GitDerivedBlobTests.REWRITES_THE_STAGED_TARGET, GitDerivedBlobTests.REWORDS_THE_MESSAGE),
    'rewrite_trailer': (GitDerivedBlobTests.REWRITES_THE_STAGED_TARGET, GitDerivedBlobTests.ADDS_A_TRAILER),
    'revert_reword': (GitDerivedBlobTests.REVERTS_THE_STAGED_TARGET, GitDerivedBlobTests.REWORDS_THE_MESSAGE),
}
_POINTS = (None, 'before_add', 'after_add', 'before_commit', 'after_commit', 'read_back', 'recorded')
for _hooks, (_pre, _msg) in _HOOKS.items():
    for _someone in (False, True):
        _cases = [(_point, None) for _point in _POINTS]
        if _someone:
            _cases += [(_point, _race) for _point in (None, 'after_commit')
                       for _race in ('before_add', 'before_commit')]
        for _point, _race in _cases:
            _name = '__'.join(('test_one_task_commit', _hooks, 'as_someone' if _someone else 'as_the_task',
                               'killed_' + _point if _point else 'not_killed',
                               'user_commit_' + _race if _race else 'no_user_commit'))
            setattr(GitDerivedBlobTests, _name, _one_task_commit_case(_pre, _msg, _someone, _point, _race))

if __name__ == '__main__':
    unittest.main()
