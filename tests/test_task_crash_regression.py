"""Same test runs on pre-#130 code: the failure must be lost patch recovery."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from z0int.task_loop import (
    authorize_task, make_fixture_repo, resume_task, save_checkpoint, step_worktree,
)


class CrashRegressionTests(unittest.TestCase):
    def test_process_loss_after_patch_write(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'Z0INT_HOME': tmp + '/home'}):
            root = Path(tmp)
            repo, spec = make_fixture_repo(root / 'repo')
            cp = authorize_task(base_repo=repo, patch=spec)
            cp.status = 'resolved'
            save_checkpoint(cp)
            cp = step_worktree(cp, worktrees_root=root / 'wts')
            # Both old (write_text) and new (write_bytes) implementations crash at
            # the same semantic boundary: real file write finished, checkpoint not.
            code = '''
import os, sys
from pathlib import Path
from z0int.task_loop import resume_task
old_text, old_bytes = Path.write_text, Path.write_bytes
def text(self, *a, **kw):
    result = old_text(self, *a, **kw)
    if self.name == 'app.py': os._exit(89)
    return result
def binary(self, *a, **kw):
    result = old_bytes(self, *a, **kw)
    if self.name == 'app.py': os._exit(89)
    return result
Path.write_text, Path.write_bytes = text, binary
resume_task(sys.argv[1])
'''
            proc = subprocess.run([sys.executable, '-c', code, cp.task_id],
                                  capture_output=True, text=True, timeout=20)
            self.assertEqual(proc.returncode, 89, proc.stderr)
            self.assertIn('READY', (Path(cp.worktree_path) / 'app.py').read_text())
            self.assertTrue(resume_task(cp.task_id).verified_success)
            self.assertIn('BROKEN', (repo / 'app.py').read_text())


if __name__ == '__main__':
    unittest.main()
