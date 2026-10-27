"""Secret scrub on every memory output (owner 09-22: AgentsView indexed credential-bearing tool output)."""
from __future__ import annotations

import json

import pytest

from memory_fixture import SECRET_MESSAGE, SECRETS, add_message, add_secret_finding, build_av_db, message_id
from z0int import intelligence_mcp
from z0int.memory import surface as ms
from z0int.memory import scrub
from z0int.memory.event_log import EventLog
from z0int.memory_contract import BitemporalClaim, MemoryScope

Z0 = MemoryScope(user='local', project='z0')


def assert_clean(blob: str) -> None:
    for name, value in SECRETS.items():
        assert value not in blob, f'{name} leaked'
    assert SECRETS['bws'].split(':')[-1] not in blob and SECRETS['bws'].split('.')[2].split(':')[0] not in blob


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv('Z0INT_HOME', str(tmp_path / 'z0'))
    monkeypatch.setenv('AGENTSVIEW_DATA_DIR', str(tmp_path / 'av'))
    build_av_db(tmp_path / 'av' / 'sessions.db')
    return tmp_path


def test_scrub_text_removes_each_secret_shape_and_counts_it():
    text, n = scrub.scrub_text(SECRET_MESSAGE)
    assert_clean(text)
    assert n >= 5 and 'quokka deploy notes end' in text


def test_scrub_obj_walks_nested_values():
    obj, n = scrub.scrub_obj({'a': [SECRETS['aws'], {'b': 'Bearer ' + SECRETS['bearer']}], 'k': 3})
    assert_clean(json.dumps(obj))
    assert n == 2 and obj['k'] == 3


def test_no_secret_reaches_search_brief_mcp_receipts_or_eventlog(env):
    res = ms.search('quokka deploy notes', ms.ScopePolicy(scope=Z0), layers=('lexical',), config={})
    assert res['evidence'] and res['scrubbed'] >= 5
    assert_clean(json.dumps(res))
    brief = ms.memory_brief('quokka deploy notes', ms.ScopePolicy(scope=Z0), config={})
    assert brief['scrubbed'] >= 5
    assert_clean(json.dumps(brief))
    assert_clean(json.dumps(brief['receipt']))
    for tool in ('memory_search', 'orient', 'verify', 'unknowns'):
        resp = intelligence_mcp.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                                        'params': {'name': tool, 'arguments': {'query': 'quokka deploy notes'}}},
                                       profile='memory')
        assert_clean(json.dumps(resp))
    locator = next(e['locator'] for e in res['evidence'] if e['session_id'] == 'c1')
    resp = intelligence_mcp.handle({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
                                    'params': {'name': 'inspect', 'arguments': {'locator': locator}}}, profile='memory')
    assert resp['result']['isError'] is False and 'quokka deploy notes' in json.dumps(resp)
    assert_clean(json.dumps(resp))
    ms.record_claim(BitemporalClaim(claim_id='leak', scope=Z0, subject='deploy', predicate='key',
                                    value=f"use {SECRETS['api_key']}", status='observed',
                                    observed_at='2026-10-01T00:00:00Z', recorded_at='2026-10-01T00:00:00Z'))
    events = (env / 'z0' / 'memory' / 'events.jsonl').read_text()
    assert 'deploy' in events
    assert_clean(events)


# Credential shapes found in env dumps, .env files and JSON configs (assembled at runtime; all synthetic).
_V = 'Fk3' + 'q9ZxR7vLm2Tb8NcW4yHd'
ENV_DUMP_SHAPES = {
    'xai_prefixed_env': 'XAI_API_KEY=' + 'xai-' + _V,
    'groq_prefixed_env': 'GROQ_API_KEY=' + 'gsk_' + _V,
    'opaque_prefixed_env': 'OPENROUTER_API_KEY=' + _V,
    'hf_token_env': 'HF_TOKEN=' + 'hf_' + _V,
    'json_api_key': '{"api_key": "' + _V + '"}',
    'json_access_token': '{"access_token": "' + _V + '"}',
    'json_client_secret': "{'client_secret': '" + _V + "'}",
    'aws_secret_env': 'AWS_SECRET_ACCESS_KEY=' + _V,
    'github_pat': 'cloned with ' + 'github_pat_' + '11ABCDEFG0' + _V,
    'bare_xai': 'key is ' + 'xai-' + _V,
    'bare_gsk': 'key is ' + 'gsk_' + _V,
}


@pytest.mark.parametrize('name', sorted(ENV_DUMP_SHAPES))
def test_env_dump_and_json_credential_shapes_are_scrubbed(name):
    text, n = scrub.scrub_text(f'tool output:\n{ENV_DUMP_SHAPES[name]}\nquokka tail')
    assert _V not in text, name
    assert n >= 1 and 'quokka tail' in text


def test_scrub_does_not_eat_ordinary_token_counts():
    text, n = scrub.scrub_text('max_tokens: 600, input_tokens=12345678, "tokens": 4000')
    assert n == 0 and text == 'max_tokens: 600, input_tokens=12345678, "tokens": 4000'


def test_env_dump_shapes_never_reach_mcp_search(env):
    add_message(env / 'av' / 'sessions.db', 'c1', 'quokka envdump ' + ' '.join(ENV_DUMP_SHAPES.values()))
    resp = intelligence_mcp.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                                    'params': {'name': 'memory_search', 'arguments': {'query': 'quokka envdump'}}},
                                   profile='memory')
    assert 'envdump' in json.dumps(resp) and _V not in json.dumps(resp)


def test_agentsview_secret_findings_spans_are_redacted_before_the_regex_backstop(env):
    """A span AgentsView's own scanner recorded is blanked even when no regex knows its shape (kernel
    ``context_providers._redact_secrets``); offsets are UTF-8 bytes into the message, matched on ordinal."""
    db = env / 'av' / 'sessions.db'
    opaque = 'zq' + 'Opaque7Credential9Value'  # no regex in the backstop matches this shape
    prefix = 'quokka café vault holds '  # non-ASCII before the span: offsets are bytes, not characters
    add_message(db, 'h1', prefix + opaque + ' end')
    ordinal = 2
    start = len(prefix.encode('utf-8'))
    add_secret_finding(db, 'h1', ordinal, start, start + len(opaque))
    add_secret_finding(db, 'h1', 0, 0, 7)  # a finding for another ordinal must not touch this message
    add_secret_finding(db, 'h1', ordinal, 0, 6, kind='tool_input')  # tool-call spans index other text
    res = ms.search('quokka vault', ms.ScopePolicy(scope=Z0), layers=('lexical',), config={})
    hit = next(e for e in res['evidence'] if e['session_id'] == 'h1' and e['ordinal'] == ordinal)
    assert opaque not in json.dumps(res) and 'quokka café vault holds' in hit['excerpt']
    assert res['scrubbed'] >= 1
    out = ms.inspect(f'agentsview:h1#{message_id(db, "h1", ordinal)}', context=2, av_db=db, chars=4000)
    assert opaque not in json.dumps(out)
    by_ordinal = {m['ordinal']: m['text'] for m in out['messages']}
    assert by_ordinal[ordinal] == prefix + '[redacted:secret] end'
    assert by_ordinal[0] == '[redacted:secret]we deploy the quokka gateway'  # its own finding applies to it


def test_inspect_scrubs_before_it_truncates_an_agentsview_message(env):
    db = env / 'av' / 'sessions.db'
    filler = 'quokka straddle ' + 'x' * 380 + ' '
    add_message(db, 'o1', filler + SECRETS['aws'] + ' tail')
    mid = message_id(db, 'o1', 1)
    out = ms.inspect(f'agentsview:o1#{mid}', chars=len(filler) + 12, context=0, av_db=db)
    assert out['ok'] is True
    assert SECRETS['aws'][:12] not in json.dumps(out)


def test_inspect_scrubs_before_it_truncates_an_eventlog_payload(env):
    EventLog().append('memory.note', {'text': 'quokka ' + 'y' * 380 + ' ' + SECRETS['aws']}, source='z0')
    raw = json.dumps({'text': 'quokka ' + 'y' * 380 + ' ' + SECRETS['aws']}, ensure_ascii=False)
    out = ms.inspect('eventlog:0', chars=raw.index(SECRETS['aws']) + 12)
    assert out['ok'] is True
    assert SECRETS['aws'][:12] not in json.dumps(out)
