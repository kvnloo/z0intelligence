"""Real git/worktree and process-loss tests; no model or provider required."""
import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def test_content_changed_by_a_clean_filter_is_not_committed(self):
        self.kill_before_add()
        self.git('config', 'filter.shout.clean', 'tr a-z A-Z')
        info = Path(self.git('rev-parse', '--git-common-dir').strip()) / 'info'
        info.mkdir(exist_ok=True)
        (info / 'attributes').write_text('app.py filter=shout\n')
        self.assert_nothing_committed_and_not_verified()
        self.assertEqual(self.git('diff', '--cached', '--name-only').strip(), '')

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

    # --- git's own stored form, unnormalised names, pathspec environment, hooks, index modes ---

    def make_task(self, name='app.py', content=b'STATUS = "BROKEN"\nprint(STATUS)\n', config=(), attrs=None,
                  files=None, setup=None, replace='STATUS = "READY"', mode=None):
        """A task in a fresh repository whose config/attributes exist from the first commit."""
        self.made = getattr(self, 'made', 0) + 1
        repo = self.root / f'made-{self.made}'
        repo.mkdir()
        run = lambda *args: subprocess.run(['git', *args], cwd=repo, check=True, text=True, capture_output=True)
        run('init')
        for key, value in config:
            run('config', key, value)
        if attrs:
            (repo / '.gitattributes').write_text(attrs)
        for other, data in (files or {}).items():
            (repo / other).write_bytes(data)
        if setup:
            setup(repo, run)
        else:
            (repo / name).parent.mkdir(parents=True, exist_ok=True)
            (repo / name).write_bytes(content)
            if mode:
                (repo / name).chmod(mode)
            run('--literal-pathspecs', 'add', '-A')
        run('-c', 'user.email=t@local', '-c', 'user.name=t', 'commit', '-m', 'fixture')
        spec = PatchSpec(relative_path=name, find='STATUS = "BROKEN"', replace=replace, description='made task')
        cp = authorize_task(base_repo=repo, patch=spec, task_id=f'made-{self.made}')
        cp.status = 'resolved'
        save_checkpoint(cp)
        return step_worktree(cp, worktrees_root=self.root / 'wts')

    def wt_git(self, cp, *args):
        return subprocess.run(['git', *args], cwd=cp.worktree_path, check=True, text=True,
                              capture_output=True).stdout

    def assert_verified_with_one_commit_of(self, cp, *paths, status=''):
        done = resume_task(cp.task_id)
        self.assertTrue(done.verified_success, done.last_error)
        self.assertEqual(resume_task(cp.task_id).to_dict(), done.to_dict())
        self.assertEqual(self.wt_git(cp, 'rev-list', '--count', f'{cp.base_ref}..refs/heads/{cp.branch}').strip(), '1')
        self.assertEqual(self.wt_git(cp, 'show', '--name-only', '--format=', '-z', 'HEAD').split('\0')[:-1],
                         sorted(paths))
        self.assertEqual(self.wt_git(cp, 'status', '--porcelain'), status)
        return done

    def test_a_repository_whose_git_stores_another_form_of_the_file_still_verifies(self):
        rot13 = "tr 'A-Za-z' 'N-ZA-Mn-za-m'"
        keyword = b'# $Id$\nSTATUS = "BROKEN"\nprint(STATUS)\n'
        cases = {
            'core.autocrlf=true': dict(config=[('core.autocrlf', 'true')]),
            '* text=auto eol=crlf': dict(attrs='* text=auto eol=crlf\n'),
            '*.py text eol=crlf': dict(attrs='*.py text eol=crlf\n'),
            'text=auto and core.eol=crlf': dict(attrs='* text=auto\n', config=[('core.eol', 'crlf')]),
            'ident keyword': dict(attrs='*.py ident\n', content=keyword),
            'ident keyword with eol=crlf': dict(attrs='*.py ident text eol=crlf\n', content=keyword),
            'clean and smudge filter pair': dict(attrs='*.py filter=rot\n', config=[
                ('filter.rot.clean', rot13), ('filter.rot.smudge', rot13)]),
            'autocrlf with an LF inside the replacement': dict(
                config=[('core.autocrlf', 'true')], replace='STATUS = "READY"\nEXTRA = 1'),
        }
        for label, kwargs in cases.items():
            with self.subTest(case=label):
                cp = self.make_task(**kwargs)
                self.assert_verified_with_one_commit_of(cp, 'app.py')
                stored = subprocess.run(['git', 'cat-file', 'blob', 'HEAD:app.py'], cwd=cp.worktree_path,
                                        check=True, capture_output=True).stdout
                on_disk = (Path(cp.worktree_path) / 'app.py').read_bytes()
                self.assertNotEqual(stored, on_disk, 'the case must store a form that differs from the file')

    def test_a_stored_form_that_does_not_check_out_as_the_patch_is_withheld(self):
        # Differences beyond line ends and the $Id$ keyword are not git's own form of the patch.
        for label, clean in (('adds a line', "sh -c 'cat; echo stamped'"), ('rewrites a word', "sed s/READY/STEADY/")):
            with self.subTest(case=label):
                cp = self.make_task(attrs='*.py filter=lossy\n', config=[('filter.lossy.clean', clean)])
                with patch.dict(os.environ, {'GIT_LITERAL_PATHSPECS': '1'}):
                    done = resume_task(cp.task_id)
                self.assertIsNone(done.verified_success)
                self.assertIn('what git would store', done.last_error)
                self.assertEqual(self.wt_git(cp, 'rev-list', '--count', f'{cp.base_ref}..HEAD').strip(), '0')
                self.assertEqual(self.wt_git(cp, 'diff', '--cached', '--name-only'), '')  # unstaged again

    def test_a_blob_holding_the_exact_bytes_needs_no_checkout_comparison(self):
        # A smudge-only filter: the stored blob is the file byte for byte, though checking it
        # out again would not reproduce it.
        cp = self.make_task(attrs='*.py filter=stamp\n', config=[('filter.stamp.smudge', "sh -c 'cat; echo stamped'")])
        self.assertIn('stamped', (Path(cp.worktree_path) / 'app.py').read_text())
        self.assert_verified_with_one_commit_of(cp, 'app.py')

    def test_an_emptied_target_that_git_does_not_track_is_still_committed(self):
        # The after-image of an empty file hashes like a blob that could not be read at all.
        def only_a_readme(repo, run):
            (repo / 'README').write_text('fixture\n')
            run('add', 'README')
        cp = self.make_task(name='new.py', setup=only_a_readme, replace='')
        (Path(cp.worktree_path) / 'new.py').write_bytes(b'STATUS = "BROKEN"')
        self.assert_verified_with_one_commit_of(cp, 'new.py')
        self.assertEqual(self.wt_git(cp, 'show', 'HEAD:new.py'), '')

    def test_a_hook_that_commits_other_content_for_the_target_is_withheld(self):
        cp = self.make_task()
        self.install_hook(cp, 'oid=$(echo \'STATUS = "READY" # hook\' | git hash-object -w --stdin)\n'
                              'git update-index --cacheinfo 100644,$oid,app.py\n')
        done = resume_task(cp.task_id)
        self.assertIsNone(done.verified_success)
        self.assertIn('does not carry the recorded patch', done.last_error)
        self.assertIn('# hook', self.wt_git(cp, 'show', 'HEAD:app.py'))

    def test_unnormalised_target_paths_name_the_same_file(self):
        for name in ('sub/./a.py', 'sub//a.py', './sub/a.py'):
            with self.subTest(name=name):
                self.assert_verified_with_one_commit_of(self.make_task(name=name), 'sub/a.py')

    def test_literal_pathspecs_in_the_environment_do_not_block_the_commit(self):
        with patch.dict(os.environ, {'GIT_LITERAL_PATHSPECS': '1'}):
            self.assert_verified_with_one_commit_of(self.make_task(), 'app.py')
            cp = self.make_task(name='a*.py', files={'ab.py': b'SIBLING = 1\n'})
            (Path(cp.worktree_path) / 'ab.py').write_text('SIBLING = 2\n')
            self.assert_verified_with_one_commit_of(cp, 'a*.py', status=' M ab.py\n')

    def test_a_tracked_link_checked_out_as_a_file_is_committed_as_before(self):
        def link_as_file(repo, run):
            run('config', 'core.symlinks', 'false')
            (repo / 'app.py').write_bytes(b'STATUS = "BROKEN"')
            oid = subprocess.run(['git', 'hash-object', '-w', '--stdin'], cwd=repo, check=True, text=True,
                                 capture_output=True, input='STATUS = "BROKEN"').stdout.strip()
            run('update-index', '--add', '--cacheinfo', f'120000,{oid},app.py')
        cp = self.make_task(setup=link_as_file)
        self.assertFalse(os.path.islink(Path(cp.worktree_path) / 'app.py'))
        self.assert_verified_with_one_commit_of(cp, 'app.py')
        self.assertTrue(self.wt_git(cp, 'ls-tree', 'HEAD', '--', 'app.py').startswith('120000 '))
        self.assertEqual(self.wt_git(cp, 'show', 'HEAD:app.py'), 'STATUS = "READY"')

    def test_an_executable_target_is_committed_and_stays_executable(self):
        cp = self.make_task(mode=0o755)
        self.assert_verified_with_one_commit_of(cp, 'app.py')
        self.assertTrue(self.wt_git(cp, 'ls-tree', 'HEAD', '--', 'app.py').startswith('100755 '))

    def test_a_link_on_disk_is_not_committed_whatever_the_target_is_called(self):
        # 'a!.py' sorts before 'a*.py' and is a regular file: a pattern lookup would read its mode.
        # '100644 a.py' as a link lists as '120000 <oid> 0\t100644 a.py'.
        for name, files in (('a*.py', {'a!.py': b'SIBLING = 1\n'}), ('100644 a.py', None)):
            with self.subTest(name=name):
                cp = self.make_task(name=name, content=b'STATUS = "BROKEN"', files=files)
                self.kill_before_add(cp.task_id)
                target = Path(cp.worktree_path) / name
                after = target.read_bytes()
                target.unlink()
                (target.parent / os.fsdecode(after)).write_bytes(after)
                os.symlink(after, target)
                done = resume_task(cp.task_id)
                self.assertIsNot(done.verified_success, True)
                self.assertEqual(self.wt_git(cp, 'rev-list', '--count', f'{cp.base_ref}..HEAD').strip(), '0')

    def install_hook(self, cp, body):
        hooks = Path(cp.worktree_path) / self.wt_git(cp, 'rev-parse', '--git-common-dir').strip() / 'hooks'
        hooks.mkdir(exist_ok=True)
        (hooks / 'pre-commit').write_text('#!/bin/sh\n' + body)
        (hooks / 'pre-commit').chmod(0o755)

    def test_a_hook_that_stages_another_file_leaves_the_index_at_the_commit(self):
        cp = self.make_task(files={'notes.txt': b'n\n'})
        self.install_hook(cp, 'echo hooked > hooked.txt\necho more >> notes.txt\ngit add hooked.txt notes.txt\n')
        # The hook's files are in the commit either way; the index must not show them as reverted.
        self.assert_verified_with_one_commit_of(cp, 'app.py', 'hooked.txt', 'notes.txt')
        self.assertEqual(self.wt_git(cp, 'diff', '--cached', '--name-only'), '')

    def test_index_repair_after_a_hook_leaves_other_staged_work_alone(self):
        cp = self.make_task(files={'notes.txt': b'n\n', 'hx.txt': b'm\n'})
        wt = Path(cp.worktree_path)
        (wt / 'hx.txt').write_text('staged by the user; matches h*.txt as a pattern\n')
        (wt / 'notes.txt').write_text('user version\n')
        self.wt_git(cp, 'add', '--', 'hx.txt', 'notes.txt')
        (wt / 'notes.txt').write_text('n\n')
        self.install_hook(cp, 'echo hooked > "h*.txt"\necho more >> notes.txt\n'
                              'git --literal-pathspecs add "h*.txt" notes.txt\n')
        with patch.dict(os.environ, {'GIT_LITERAL_PATHSPECS': '1'}):
            self.assert_verified_with_one_commit_of(
                cp, 'app.py', 'h*.txt', 'notes.txt', status='M  hx.txt\nMM notes.txt\n')
        self.assertEqual(self.wt_git(cp, 'show', ':0:notes.txt'), 'user version\n')

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


if __name__ == '__main__':
    unittest.main()
