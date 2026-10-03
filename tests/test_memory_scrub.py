"""Secret scrub on every memory output (owner 09-22: AgentsView indexed credential-bearing tool output)."""
from __future__ import annotations

import json

import pytest

from memory_fixture import SECRET_MESSAGE, SECRETS, build_av_db
from z0int import intelligence_mcp
from z0int.memory import surface as ms
from z0int.memory import scrub
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
