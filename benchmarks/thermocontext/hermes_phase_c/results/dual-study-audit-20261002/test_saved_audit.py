"""Validate portable saved-artifact replay and rejection of corrupt records."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('saved_study_audit', HERE / 'audit_saved_studies.py')
auditor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(auditor)
ORIGINALS = [Path(os.environ.get(key, '/workspace/hermes-factory/reviews/' + default))
             for key, default in (('HERMES_AUDIT_V1', 'hermes-verified-cohort-01'),
                                  ('HERMES_AUDIT_V2', 'hermes-claim-contract-v2-01'))]


@unittest.skipUnless(all(path.is_dir() for path in ORIGINALS), 'set HERMES_AUDIT_V1 and HERMES_AUDIT_V2 archive paths')
class SavedAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.copies = [Path(self.tmp.name) / str(index) for index in range(2)]
        for original, clone in zip(ORIGINALS, self.copies):
            for source in original.rglob('*'):
                if not source.is_file():
                    continue
                relative = str(source.relative_to(original))
                if auditor.local_runtime(relative):
                    continue
                target = clone / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)

    def test_relocated_public_archive_never_reads_original_absolute_paths(self):
        original_open = Path.open
        def guarded_open(path, *args, **kwargs):
            resolved = path.resolve()
            if any(resolved.is_relative_to(root) for root in ORIGINALS):
                raise AssertionError('portable audit read original archive: ' + str(path))
            if str(resolved).startswith('/workspace/hermes-factory/experiments/'):
                raise AssertionError('portable audit read original runtime source: ' + str(path))
            return original_open(path, *args, **kwargs)
        with patch.object(Path, 'open', guarded_open):
            result = auditor.audit(*self.copies, HERE / 'source-snapshots', public_artifacts=True)
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['v1']['verified_successes'], 0)
        self.assertEqual(result['v2']['verified_successes'], 1)
        self.assertEqual(result['predecessor_immutability']['checked_files'], 112)
        self.assertEqual(len(result['predecessor_immutability']['omitted_local_runtime_files']), 86)
        self.assertFalse(result['predecessor_immutability']['all_198_locally_rechecked'])

    def test_missing_public_evidence_is_not_an_allowed_runtime_omission(self):
        (self.copies[0] / 'runs/full-complete/answer.json').unlink()
        with self.assertRaisesRegex(ValueError, 'predecessor artifact'):
            auditor.audit(*self.copies, HERE / 'source-snapshots', public_artifacts=True)

    def test_changed_native_bytes_are_rejected(self):
        path = self.copies[1] / 'runs/full-complete/native-request.bin'
        path.write_bytes(path.read_bytes() + b' ')
        with self.assertRaisesRegex(ValueError, 'physical raw wire'):
            auditor.audit(*self.copies, HERE / 'source-snapshots', public_artifacts=True)

    def test_changed_reported_cost_is_rejected(self):
        path = self.copies[1] / 'runs/minimal-complete/receipt.json'
        value = auditor.read(path)
        value['reported_cost'] = 0
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, 'reported cost'):
            auditor.audit(*self.copies, HERE / 'source-snapshots', public_artifacts=True)


if __name__ == '__main__':
    unittest.main()
