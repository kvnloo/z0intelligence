"""Public-contract fixtures, not claims of a running OMP/Tern desktop."""
import json
import unittest
from dataclasses import replace

from z0int.continuation import ContinuationCheckpoint, ContinuationContext, PendingOperation, InvalidCheckpoint, StaleCheckpoint
from z0int.continuation_adapters import omp_boundary, tern_init, tern_save


def context():
    return ContinuationContext('test', '1', 'code:1', 'policy:1', 'intent:1',
                               'repo:fixture', 'trace:1', 'work:1', 'attempt:1')


class AdapterTests(unittest.TestCase):
    def capture(self, snapshot=None, event=None, **kwargs):
        state = {'sessionId': 's1', 'sessionFile': '/fixture/s1.jsonl',
                 'isStreaming': False, 'isCompacting': False,
                 'hasPendingAsyncWork': False, 'isSettled': True, 'queuedMessageCount': 0}
        if snapshot is not None:
            state = snapshot
        return omp_boundary(context=replace(context(), runtime='omp'), session_id='s1',
            boundary_ref='event:12', source_revisions={'session': 'revision:12'},
            snapshot=state, event=event or {'type': 'session_settled'}, **kwargs)

    def test_quiet_is_not_verified_task_completion(self):
        cp = self.capture()
        self.assertEqual(cp.body['phase'], 'settled')
        self.assertFalse(cp.body['terminal'])
        self.assertIsNone(cp.body['execution_completed'])
        self.assertIsNone(cp.body['verified_success'])

    def test_terminal_agent_end_alone_is_not_settled(self):
        cp = self.capture(event={'type': 'agent_end', 'isTerminal': True, 'yielded': True})
        self.assertEqual(cp.body['phase'], 'yielded')

    def test_yield_waits_for_owner_async_work(self):
        cp = self.capture(event={'type': 'agent_end', 'yielded': True, 'awaitingAsyncWork': True})
        self.assertEqual(cp.body['phase'], 'awaiting_async')
        self.assertEqual(cp.body['pending'][0]['status'], 'unknown')
        self.assertIsNone(cp.body['pending'][0]['durable_ref'])

    def test_missing_old_server_fields_are_not_invented(self):
        cp = self.capture(snapshot={'sessionId': 's1'})
        self.assertEqual(cp.body['phase'], 'unsettled')
        self.assertIsNone(cp.body['state']['observation']['isSettled'])
        self.assertEqual(cp.body['pending'][0]['status'], 'unknown')

    def test_pending_jobs_override_quiet_snapshot(self):
        cp = self.capture(pending=(PendingOperation('job', 'read', 'started'),))
        self.assertNotEqual(cp.body['phase'], 'settled')

    def test_prompt_completion_is_not_verification(self):
        cp = self.capture(event={'type': 'prompt_result', 'status': 'completed', 'sessionSettled': True})
        self.assertEqual(cp.body['phase'], 'settled')
        self.assertIsNone(cp.body['verified_success'])

    def test_wrong_session_or_type_refused(self):
        for state in ({'sessionId': 'other'}, {'sessionId': 's1', 'isSettled': 'true'},
                      {'sessionId': 's1', 'queuedMessageCount': True}):
            with self.assertRaises(InvalidCheckpoint):
                self.capture(snapshot=state)
        with self.assertRaises(InvalidCheckpoint):
            self.capture(event={'type': 'message_update'})

    def test_no_private_prompt_or_proc_id_laundering(self):
        cp = self.capture(snapshot={'sessionId': 's1', 'hasPendingAsyncWork': True,
            'systemPrompt': ['secret'], 'procId': 'proc://1',
            'goal': {'enabled': False, 'goal': {'id': 'g1', 'status': 'paused', 'objective': 'private'}}})
        self.assertNotIn('secret', cp.payload)
        self.assertNotIn('private', cp.payload)
        self.assertNotIn('proc://1', cp.payload)
        self.assertFalse(cp.body['state']['goal']['enabled'])

    def test_replayed_boundary_has_same_key(self):
        self.assertEqual(self.capture().checkpoint_id, self.capture().checkpoint_id)

    def test_tern_roundtrip_reconstructs_wait_from_named_state(self):
        ctx = replace(context(), runtime='tern')
        cp = ContinuationCheckpoint.build(continuation_id='pane:1', context=ctx,
            phase='waiting', resume_entrypoint='demo.wait_for_result',
            state={'request': 'read:1', 'nested': {'array': [], 'object': {}, 'null': None}},
            source_revisions={'plugin': 'v1'},
            pending=(PendingOperation('read:1', 'read', 'started', 'readback', durable_ref='result:1'),))
        # What survives save/reload: JSON only. The old callbacks are discarded.
        saved = json.loads(json.dumps(tern_save(cp)))
        subscriptions = {'old': lambda _: self.fail('old callback must not survive')}
        subscriptions.clear()
        plan = tern_init(saved, context=ctx, source_revisions={'plugin': 'v1'},
                         allowed_entrypoints={'demo.wait_for_result'})
        self.assertEqual(plan.disposition, 'reconcile')
        request = plan.checkpoint.body['pending'][0]['durable_ref']
        observed = []
        # This is fixture HOST code, not the adapter: bind a fresh readback callback.
        subscriptions[request] = lambda receipt: observed.append(receipt)
        subscriptions['result:1']({'result_ref': 'read:1:receipt'})
        self.assertEqual(observed, [{'result_ref': 'read:1:receipt'}])
        self.assertEqual(tern_init(saved, context=ctx, source_revisions={'plugin': 'v1'},
                          allowed_entrypoints={'demo.wait_for_result'}), plan)
        self.assertEqual(plan.checkpoint.body['state']['nested'], {'array': [], 'object': {}, 'null': None})

    def test_tern_wrong_scope_or_code_never_rebinds(self):
        ctx = replace(context(), runtime='tern')
        cp = ContinuationCheckpoint.build(continuation_id='pane:1', context=ctx,
                    phase='waiting', resume_entrypoint='wait', state={})
        for field in ('scope', 'code_revision', 'policy_revision'):
            with self.assertRaises(StaleCheckpoint):
                tern_init(tern_save(cp), context=replace(ctx, **{field: 'different'}),
                          source_revisions={}, allowed_entrypoints={'wait'})
        with self.assertRaises(InvalidCheckpoint):
            tern_save(self.capture())


if __name__ == '__main__':
    unittest.main()
