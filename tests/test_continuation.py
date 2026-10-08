"""Offline contract tests: no provider, scheduler or model calls."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from z0int.continuation import (
    SCHEMA, MAX_BYTES, ContinuationCheckpoint as Checkpoint, ContinuationContext,
    InvalidCheckpoint, PendingOperation, StaleCheckpoint, canonical_json,
    parse_json, plan_restore, read_json, record_checkpoint, write_atomic,
)


def context():
    return ContinuationContext('test', '1', 'code:1', 'policy:1', 'intent:1',
                               'repo:fixture', 'trace:1', 'work:1', 'attempt:1')


def checkpoint(**kwargs):
    fields = dict(continuation_id='c1', context=context(), phase='waiting',
                  resume_entrypoint='fixture.resume', state={'count': 3},
                  source_revisions={'repo': 'abc'})
    fields.update(kwargs)
    return Checkpoint.build(**fields)


def restore(cp, **kwargs):
    fields = dict(context=context(), source_revisions={'repo': 'abc'},
                  allowed_entrypoints={'fixture.resume'})
    fields.update(kwargs)
    return plan_restore(cp, **fields)


class ContinuationTests(unittest.TestCase):
    def test_roundtrip_preserves_types_and_identity(self):
        cp = checkpoint(state={'n': None, 'list': [], 'object': {}, 'f': 1.0,
                               'unicode': 'Luau → λ', 'bool': False})
        out = Checkpoint.loads(cp.dumps())
        self.assertEqual(out, cp)
        self.assertEqual(type(out.body['state']['f']), float)
        self.assertEqual(type(out.body['state']['object']), dict)
        self.assertEqual(cp.checkpoint_id, out.checkpoint_id)

    def test_order_independent_and_detached(self):
        a = checkpoint(state={'a': 1, 'b': 2})
        b = checkpoint(state={'b': 2, 'a': 1})
        self.assertEqual(a, b)
        a.body['state']['a'] = 9
        self.assertEqual(a.body['state']['a'], 1)

    def test_rejects_tamper(self):
        row = checkpoint().to_dict()
        row['payload'] = row['payload'].replace('"count":3', '"count":4')
        with self.assertRaisesRegex(InvalidCheckpoint, 'checksum'):
            Checkpoint.from_dict(row)

    def test_rejects_unknown_envelope_and_payload_fields(self):
        cp = checkpoint()
        for row in ({**cp.to_dict(), 'schema': 'future'},
                    {**cp.to_dict(), 'extra': 1}):
            with self.subTest(row=row):
                with self.assertRaises(InvalidCheckpoint):
                    Checkpoint.from_dict(row)
        body = cp.body
        body['arbitrary'] = True
        with self.assertRaises(InvalidCheckpoint):
            Checkpoint(canonical_json(body))

    def test_json_is_strict(self):
        cyclic = []; cyclic.append(cyclic)
        for state in ({'v': float('nan')}, {'v': float('inf')}, {3: 'key'},
                      {'v': b'bytes'}, {'v': lambda: None}, {'v': (1, 2)},
                      {'v': 2**53}, {'v': cyclic}, {'v': '\ud800'}):
            with self.subTest(state_type=type(state)):
                with self.assertRaises(InvalidCheckpoint):
                    checkpoint(state=state)
        for raw in ('{"a":1,"a":2}', '{"n":NaN}', '{', '"'+'x'*MAX_BYTES+'"'):
            with self.assertRaises(InvalidCheckpoint):
                parse_json(raw)

    def test_rejects_nonsymbolic_entrypoint(self):
        for value in ('', '../run', 'import os', 'x()', 1):
            with self.assertRaises(InvalidCheckpoint):
                checkpoint(resume_entrypoint=value)

    def test_current_identity_must_match_every_field(self):
        cp = checkpoint()
        for field in context().__dataclass_fields__:
            with self.subTest(field=field):
                with self.assertRaises(StaleCheckpoint):
                    restore(cp, context=replace(context(), **{field: 'changed'}))
        with self.assertRaises(StaleCheckpoint):
            restore(cp, source_revisions={})
        with self.assertRaises(StaleCheckpoint):
            restore(cp, allowed_entrypoints={'other'})

    def test_started_is_unknown_not_retry(self):
        cp = checkpoint(pending=(PendingOperation('write:1', 'write', 'started',
                          'idempotent_retry', idempotency_key='same'),))
        plan = restore(cp)
        self.assertEqual(plan.disposition, 'reconcile')
        self.assertEqual(plan.checkpoint.body['pending'][0]['status'], 'unknown')
        self.assertEqual(plan.checkpoint.body['parent_checkpoint_id'], cp.checkpoint_id)
        self.assertEqual(cp.body['pending'][0]['status'], 'started')
        self.assertEqual(restore(cp), plan)
        self.assertEqual(restore(plan.checkpoint), plan)

    def test_terminal_stays_terminal_with_unknown_work(self):
        cp = checkpoint(terminal=True, pending=(PendingOperation('x', 'write', 'started'),))
        self.assertEqual(restore(cp).disposition, 'terminal')

    def test_operations_validate(self):
        for kw in ({'status': []}, {'status': 'complete'}, {'resume_policy': 'retry'},
                   {'status': 'completed'}, {'resume_policy': 'idempotent_retry'}):
            with self.assertRaises(InvalidCheckpoint):
                PendingOperation('x', 'write', **kw)
        op = PendingOperation('x', 'read')
        with self.assertRaises(InvalidCheckpoint):
            checkpoint(pending=(op, op))

    def test_execution_is_not_verification(self):
        self.assertIsNone(checkpoint(execution_completed=True).body['verified_success'])
        for kw in ({'verified_success': True},
                   {'verified_success': True, 'execution_completed': True},
                   {'execution_completed': 1}, {'terminal': 'false'}):
            with self.assertRaises(InvalidCheckpoint):
                checkpoint(**kw)
        cp = checkpoint(execution_completed=True, verified_success=True, verifier_ref='test:1')
        self.assertTrue(cp.body['verified_success'])

    def test_receipt_join_and_eventlog_interface(self):
        log = Mock()
        cp = checkpoint()
        record_checkpoint(log, cp, parent_event_ids=(4,))
        args, kw = log.append.call_args
        self.assertEqual(args[0], 'continuation.checkpoint')
        self.assertEqual(args[1]['extra']['continuation']['replay_key'], cp.checkpoint_id)
        self.assertEqual(kw['parent_event_ids'], (4,))
        self.assertEqual(kw['session_id'], 'c1')
        self.assertIsNone(args[1]['extra']['continuation']['verified_success'])

    def test_atomic_replace_failure_preserves_old_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'checkpoint.json'
            write_atomic(path, {'old': True})
            with patch('z0int.continuation.os.replace', side_effect=OSError('disk fault')):
                with self.assertRaises(OSError):
                    write_atomic(path, {'new': True})
            self.assertEqual(read_json(path), {'old': True})
            self.assertEqual(list(Path(tmp).iterdir()), [path])
            if os.name == 'posix':
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_maximum_size_written_is_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'limit.json'
            value = 'x' * (MAX_BYTES - 2)
            write_atomic(path, value)
            self.assertEqual(read_json(path), value)
            with self.assertRaises(InvalidCheckpoint):
                write_atomic(path, value + 'x')
            self.assertEqual(read_json(path), value)

    def test_fresh_process_roundtrip(self):
        cp = checkpoint()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'c.json'
            write_atomic(path, cp.to_dict())
            proc = subprocess.run([sys.executable, '-c',
                'from z0int.continuation import *; import sys; '
                'c=ContinuationCheckpoint.from_dict(read_json(sys.argv[1])); '
                'print(c.checkpoint_id)', str(path)], capture_output=True, text=True, check=True)
            self.assertEqual(proc.stdout.strip(), cp.checkpoint_id)


if __name__ == '__main__':
    unittest.main()
