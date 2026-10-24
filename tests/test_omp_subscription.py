"""Offline contract tests. Every HTTP request uses an in-memory SSE fixture."""
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from z0int import omp_subscription as adapter


MESSAGES = [{'role': 'system', 'content': 'Answer briefly.'}, {'role': 'user', 'content': 'Say hello.'}]
# Synthetic credentials only. Never read the operator's credential store.
LOGIN = {'access': 'fake-access-token', 'accountId': 'fake-account', 'expires': 9999999999999}


class Stream(io.BytesIO):
    def __init__(self, events, headers=None):
        wire = ''.join('data: ' + json.dumps(event) + '\r\n\r\n' for event in events)
        super().__init__(wire.encode())
        self.headers = {'content-type': 'text/event-stream; charset=utf-8', **(headers or {})}


def terminal(**overrides):
    response = {'id': 'resp_test', 'model': 'gpt-6-luna', 'status': 'completed',
                'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'Hello.'}]}],
                'usage': {'input_tokens': 12, 'output_tokens': 4, 'input_tokens_details': {'cached_tokens': 3}}}
    response.update(overrides)
    return {'type': 'response.completed', 'response': response}


def anthropic_events(stop='end_turn', usage=None):
    return [
        {'type': 'message_start', 'message': {'id': 'msg_test', 'model': 'claude-haiku-5-5',
            'usage': {'input_tokens': 8, 'output_tokens': 0, 'cache_read_input_tokens': 3,
                      'cache_creation_input_tokens': 2}}},
        {'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'thinking', 'thinking': ''}},
        {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'thinking_delta', 'thinking': 'hidden'}},
        {'type': 'content_block_start', 'index': 1, 'content_block': {'type': 'text', 'text': ''}},
        {'type': 'content_block_delta', 'index': 1, 'delta': {'type': 'text_delta', 'text': 'Hello.'}},
        {'type': 'message_delta', 'delta': {'stop_reason': stop}, 'usage': usage or {'output_tokens': 5}},
        {'type': 'message_stop'},
    ]


class CredentialTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.env = patch.dict(os.environ, {'OMP_AGENT_DIR': self.directory.name})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.path = Path(self.directory.name) / 'agent.db'
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE auth_credentials (id INTEGER PRIMARY KEY, provider TEXT, '
                       'credential_type TEXT, disabled_cause TEXT, updated_at INTEGER, data TEXT)')

    def add(self, provider='openai-codex', data=None, updated=1, disabled=None, kind='oauth'):
        with sqlite3.connect(self.path) as db:
            db.execute('INSERT INTO auth_credentials(provider,credential_type,disabled_cause,updated_at,data) '
                       'VALUES (?,?,?,?,?)', (provider, kind, disabled, updated, json.dumps(LOGIN if data is None else data)))

    def test_latest_enabled_oauth_and_tie_break_are_read_only(self):
        self.add(data={**LOGIN, 'access': 'old'})
        self.add(data={**LOGIN, 'access': 'new'}, updated=2)
        self.add(data={**LOGIN, 'access': 'same-time-newer-id'}, updated=2)
        self.add(data={**LOGIN, 'access': 'disabled'}, updated=3, disabled='revoked')
        self.add(data={**LOGIN, 'access': 'api-key'}, updated=4, kind='api_key')
        before = self.path.read_bytes()
        connect = sqlite3.connect
        with patch.object(adapter.sqlite3, 'connect', wraps=connect) as wrapped:
            self.assertTrue(adapter.credential_available('openai-codex'))
            self.assertEqual(adapter._login('openai-codex')['access'], 'same-time-newer-id')
        self.assertIn('?mode=ro', wrapped.call_args.args[0])
        self.assertTrue(wrapped.call_args.kwargs['uri'])
        self.assertEqual(self.path.read_bytes(), before)

    def test_missing_store_is_not_created(self):
        missing = Path(self.directory.name) / 'missing'
        with patch.dict(os.environ, {'OMP_AGENT_DIR': str(missing)}):
            self.assertFalse(adapter.credential_available('anthropic'))
        self.assertFalse(missing.exists())

    def test_missing_disabled_or_wrong_type_credentials_fail_closed(self):
        self.assertFalse(adapter.credential_available('openai-codex'))
        self.add(disabled='revoked')
        self.add(kind='api_key')
        self.assertFalse(adapter.credential_available('openai-codex'))
        self.assertFalse(adapter.credential_available('opencode-zen'))

    def test_invalid_newest_credential_never_falls_back_to_old_account(self):
        self.add()
        self.add(data={**LOGIN, 'expires': 1}, updated=2)
        self.assertFalse(adapter.credential_available('openai-codex'))

    def test_malformed_expiry_and_missing_account_are_rejected(self):
        for data in ({}, {**LOGIN, 'expires': True}, {**LOGIN, 'expires': float('nan')},
                     {**LOGIN, 'expires': float('inf')}, {**LOGIN, 'expires': '9999999999999'},
                     {**LOGIN, 'expires': time.time() * 1000 - 1000}, {**LOGIN, 'accountId': ''},
                     {**LOGIN, 'access': ''}):
            with self.subTest(data_keys=list(data)):
                self.add(data=data, updated=2)
                self.assertFalse(adapter.credential_available('openai-codex'))

    def test_anthropic_requires_subscription_token_prefix(self):
        self.add(provider='anthropic')
        self.assertFalse(adapter.credential_available('anthropic'))
        self.add(provider='anthropic', data={**LOGIN, 'access': 'sk-ant-oat-fake'}, updated=2)
        self.assertTrue(adapter.credential_available('anthropic'))

    def test_login_does_not_return_refresh_fields(self):
        self.add(data={**LOGIN, 'refresh': 'fake-refresh'})
        self.assertEqual(set(adapter._login('openai-codex')), {'access', 'accountId', 'expires'})


class TransportTests(unittest.TestCase):
    def call(self, events, provider='openai-codex', model='gpt-6-luna', headers=None):
        opener = Mock()
        opener.open.return_value = Stream(events, headers)
        with patch.object(adapter, '_login', return_value=LOGIN), patch.object(adapter, 'build_opener', return_value=opener):
            result = adapter.execute(provider, model, MESSAGES, 64, timeout_s=20)
        return result, opener.open.call_args.args[0]

    def test_codex_native_request_and_receipt_shape(self):
        result, request = self.call([terminal()])
        self.assertEqual(request.full_url, 'https://chatgpt.com/backend-api/codex/responses')
        headers = {k.lower(): v for k, v in request.header_items()}
        self.assertEqual(headers['authorization'], 'Bearer fake-access-token')
        self.assertEqual(headers['chatgpt-account-id'], 'fake-account')
        self.assertEqual(headers['originator'], 'codex_cli_rs')
        self.assertNotIn('x-api-key', headers)
        body = json.loads(request.data)
        self.assertEqual(body['model'], 'gpt-6-luna')
        self.assertEqual(body['instructions'], MESSAGES[0]['content'])
        self.assertFalse(body['store'])
        self.assertTrue(body['stream'])
        self.assertEqual(body['input'][0]['content'][0]['type'], 'input_text')
        self.assertNotIn('max_output_tokens', body)
        self.assertFalse(result.output_limit_enforced)
        self.assertEqual((result.content, result.finish_reason), ('Hello.', 'stop'))
        self.assertEqual((result.input_tokens, result.output_tokens, result.cached_input_tokens), (12, 4, 3))
        self.assertEqual(result.raw, {'model': 'gpt-6-luna', 'id': 'resp_test', 'choices': [{'finish_reason': 'stop'}]})
        self.assertEqual(result.usage['completion_tokens'], 4)
        self.assertIsNone(result.cost)

    def test_codex_terminal_omitted_output_uses_item_done(self):
        item = {'type': 'message', 'content': [{'type': 'output_text', 'text': 'Hello.'}]}
        result, _ = self.call([{'type': 'response.output_item.done', 'output_index': 0, 'item': item}, terminal(output=[])])
        self.assertEqual(result.content, 'Hello.')

    def test_codex_delta_fallback_and_done_variant(self):
        event = terminal(output=[])
        event['type'] = 'response.done'
        result, _ = self.call([{'type': 'response.output_text.delta', 'delta': 'Hello.'}, event])
        self.assertEqual(result.content, 'Hello.')

    def test_incomplete_refusal_and_tool_outputs_are_not_success(self):
        cases = [
            ({'status': 'incomplete', 'incomplete_details': {'reason': 'max_output_tokens'}}, 'length'),
            ({'output': [{'type': 'message', 'content': [{'type': 'refusal', 'refusal': 'No'}]}]}, 'refusal'),
            ({'output': [{'type': 'function_call'}]}, 'tool_calls'),
        ]
        for overrides, finish in cases:
            with self.subTest(finish=finish):
                result, _ = self.call([terminal(**overrides)])
                self.assertEqual(result.finish_reason, finish)
                self.assertEqual(result.tool_call, finish == 'tool_calls')

    def test_missing_usage_model_id_are_not_fabricated(self):
        result, _ = self.call([terminal(usage={}, model=None, id=None)])
        self.assertIsNone(result.input_tokens)
        self.assertIsNone(result.output_tokens)
        self.assertIsNone(result.model)
        self.assertIsNone(result.id)
        self.assertIsNone(result.cost)

    def test_only_valid_reported_cost_is_preserved(self):
        for cost, expected in [(0, 0), (.02, .02), (-1, None), (True, None), ('0', None), (float('nan'), None)]:
            result, _ = self.call([terminal(usage={'cost': cost})])
            self.assertEqual(result.cost, expected)

    def test_anthropic_native_oauth_identity_and_usage(self):
        result, request = self.call(anthropic_events(), 'anthropic', 'claude-haiku-5-5')
        self.assertEqual(request.full_url, 'https://api.anthropic.com/v1/messages?beta=true')
        headers = {k.lower(): v for k, v in request.header_items()}
        self.assertIn('oauth-2025-04-20', headers['anthropic-beta'])
        self.assertEqual(headers['x-app'], 'cli')
        self.assertNotIn('x-api-key', headers)
        body = json.loads(request.data)
        self.assertEqual(body['system'][0]['text'], adapter._IDENTITY)
        self.assertEqual(body['max_tokens'], 64)
        self.assertEqual(body['thinking']['type'], 'adaptive')
        self.assertEqual(body['output_config']['effort'], 'low')
        self.assertTrue(result.output_limit_enforced)
        self.assertEqual((result.content, result.finish_reason), ('Hello.', 'stop'))
        self.assertEqual((result.input_tokens, result.output_tokens, result.cached_input_tokens), (13, 5, 3))
        self.assertEqual((result.id, result.model), ('msg_test', 'claude-haiku-5-5'))
        self.assertIsNone(result.cost)

    def test_anthropic_max_tokens_is_incomplete(self):
        result, _ = self.call(anthropic_events('max_tokens'), 'anthropic', 'claude-haiku-5-5')
        self.assertEqual(result.finish_reason, 'length')

    def test_anthropic_missing_usage_stays_unknown(self):
        events = anthropic_events()
        events[0]['message'].pop('usage')
        events[-2].pop('usage')
        result, _ = self.call(events, 'anthropic', 'claude-haiku-5-5')
        self.assertIsNone(result.input_tokens)
        self.assertIsNone(result.output_tokens)

    def test_quota_headers_are_filtered(self):
        result, _ = self.call([terminal()], headers={'x-ratelimit-remaining-requests': '2',
                'authorization': 'fake-provider-secret', 'set-cookie': 'fake-cookie'})
        self.assertEqual(result.quota_headers, {'x-ratelimit-remaining-requests': '2'})

    def test_invalid_input_is_rejected_before_credentials_or_network(self):
        with patch.object(adapter, '_login') as login, patch.object(adapter, 'build_opener') as transport:
            for provider, model, messages, budget in [
                ('openai-codex', 'gpt-6-sol', MESSAGES, 64), ('anthropic', 'haiku', MESSAGES, 64),
                ('opencode-zen', 'gpt-6-luna', MESSAGES, 64), ('openai-codex', 'gpt-6-luna', MESSAGES, True),
                ('openai-codex', 'gpt-6-luna', [], 64),
                ('openai-codex', 'gpt-6-luna', [{'role': 'tool', 'content': 'bad'}], 64),
                ('openai-codex', 'gpt-6-luna', list(reversed(MESSAGES)), 64),
            ]:
                with self.assertRaises(adapter.SubscriptionError):
                    adapter.execute(provider, model, messages, budget)
            login.assert_not_called()
            transport.assert_not_called()

    def test_no_refresh_retry_or_raw_provider_error(self):
        for error in [URLError('fake-access-token'), HTTPError('https://example.invalid', 403,
                     'fake-access-token', {'Retry-After': '3', 'authorization': 'fake-access-token'},
                     io.BytesIO(b'fake-access-token'))]:
            opener = Mock()
            opener.open.side_effect = error
            with patch.object(adapter, '_login', return_value=LOGIN), patch.object(adapter, 'build_opener', return_value=opener):
                with self.assertRaises(adapter.SubscriptionError) as raised:
                    adapter.execute('openai-codex', 'gpt-6-luna', MESSAGES, 64)
            self.assertNotIn('fake-access-token', str(raised.exception))
            self.assertIsNone(raised.exception.__cause__)
            self.assertEqual(opener.open.call_count, 1)
            if isinstance(error, HTTPError):
                self.assertEqual(raised.exception.code, 403)
                self.assertEqual(raised.exception.headers, {'retry-after': '3'})

    def test_error_events_and_truncated_stream_fail_closed(self):
        for events in [[{'type': 'error', 'message': 'fake-access-token'}], [],
                       [{'type': 'response.output_text.delta', 'delta': 'partial'}],
                       [{'type': 'response.failed', 'response': {'error': 'fake-access-token'}}]]:
            with self.assertRaises(adapter.SubscriptionError) as raised:
                self.call(events)
            self.assertNotIn('fake-access-token', str(raised.exception))
        with self.assertRaises(adapter.SubscriptionError):
            self.call(anthropic_events()[:-1], 'anthropic', 'claude-haiku-5-5')

    def test_redirects_never_forward_authorization(self):
        redirect = adapter._NoRedirect()
        self.assertIsNone(redirect.redirect_request(None, None, 307, '', {}, 'https://other.invalid'))

    def test_sse_multiline_json_comments_and_eof(self):
        response = io.BytesIO(b': comment\n\ndata: {"type":\ndata: "ping"}\n\ndata: {"type":"last"}')
        self.assertEqual(list(adapter._events(response, time.monotonic() + 10)), [{'type': 'ping'}, {'type': 'last'}])


if __name__ == '__main__':
    unittest.main()
