import json
import os
from pathlib import Path

import pytest

from z0int import claude_code, claude_code_tokenomics as cct

T0 = 1790000000.0


def iso(offset):
    import datetime as dt
    return dt.datetime.fromtimestamp(T0 + offset, dt.timezone.utc).isoformat().replace('+00:00', 'Z')


def prompt(pid, t, text='do the thing'):
    return {'type': 'user', 'promptId': pid, 'timestamp': iso(t), 'cwd': '/w', 'sessionId': 'S',
            'message': {'role': 'user', 'content': text}}


def assistant(mid, t, tools=(), model='claude-opus-5-5', **usage):
    u = {'input_tokens': 0, 'cache_creation_input_tokens': 0, 'cache_read_input_tokens': 0, 'output_tokens': 0, **usage}
    content = [{'type': 'tool_use', 'id': tid, 'name': name, 'input': inp} for tid, name, inp in tools]
    return {'type': 'assistant', 'timestamp': iso(t), 'message': {'id': mid, 'model': model, 'usage': u, 'content': content}}


def result(tid, t, text, pid='p1'):
    return {'type': 'user', 'promptId': pid, 'timestamp': iso(t),
            'message': {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': tid, 'content': text}]}}


def packet(t):
    return {'type': 'attachment', 'timestamp': iso(t), 'attachment': {
        'type': 'hook_additional_context', 'hookEvent': 'SessionStart', 'content': ['<z0-state-packet repo=x>' + 'y' * 399]}}


OBS_TEXT = ('head\n[z0 ObservationPack] 300 middle lines (8000 chars total, 360 lines) archived verbatim as obs-abc. '
            'Nothing was discarded.\ntail')
WORKER = 'mcp__plugin_z0intelligence_z0intelligence__route_worker'


def write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(json.dumps(r) + '\n' for r in rows))


def session_rows():
    return [
        packet(0),
        prompt('p1', 1),
        assistant('m1', 2, tools=[('t1', 'Bash', {'command': 'pytest -q'})], input_tokens=10, cache_read_input_tokens=1000, output_tokens=5),
        assistant('m1', 2, tools=[('t1', 'Bash', {'command': 'pytest -q'})], input_tokens=10, cache_read_input_tokens=1000, output_tokens=50),
        result('t1', 3, OBS_TEXT),
        assistant('m2', 4, tools=[('t2', 'Bash', {'command': 'z0obs abc 1 40'})], input_tokens=3, cache_creation_input_tokens=200, output_tokens=7),
        result('t2', 5, 'x' * 400),
        assistant('m3', 6, input_tokens=1, cache_read_input_tokens=1200, output_tokens=9),
        prompt('p2', 10),
        assistant('m4', 11, tools=[('t3', WORKER, {'task': 'classify', 'context': 'c' * 796})], input_tokens=2, output_tokens=210),
        result('t3', 12, json.dumps({'ok': True, 'subagent_id': 'sub1', 'provider': 'groot', 'model': 'qwen3-8b',
                                     'input_tokens': 220, 'output_tokens': 40, 'output': 'z' * 160}), pid='p2'),
        assistant('m5', 13, input_tokens=1, cache_read_input_tokens=1300, output_tokens=4),
    ]


def schema_validator():
    jsonschema = pytest.importorskip('jsonschema')
    import tokenomics
    spec = Path(tokenomics.__file__).resolve().parents[4] / 'spec' / 'schemas' / 'event.schema.json'
    if not spec.is_file():
        pytest.skip('tokenomics spec not available (non-editable install)')
    return jsonschema.Draft202012Validator(json.loads(spec.read_text()))


def test_backfill_turns_are_canonical_and_honest(tmp_path):
    projects = tmp_path / 'projects'
    write(projects / 'proj' / 'S.jsonl', session_rows())
    os.utime(projects / 'proj' / 'S.jsonl', (T0 + 20, T0 + 20))
    events, meta = cct.backfill_events(T0 - 10, base=projects, root=tmp_path / 'home', now=T0 + 3600)
    turns = [e for e in events if e['name'] == 'claude_code.turn']
    assert len(turns) == 2
    first = turns[0]
    # streamed m1 counted once with its final usage
    assert first['extra']['anthropic_usage'] == {'input_tokens': 14, 'cache_creation_input_tokens': 200,
                                                 'cache_read_input_tokens': 2200, 'output_tokens': 66}
    assert first['usage'] == {'input_tokens': 2414, 'output_tokens': 66, 'cached_input_tokens': 2200,
                              'cache_write_input_tokens': 200, 'attribution': 'incremental', 'source': 'provider'}
    assert first['measurement_source']['measurement_state'] == 'complete'
    mech = first['extra']['mechanisms']
    assert mech['packet_injected'] and mech['packet_tokens_est'] == 106
    assert mech['profile'] == 'lean_inferred' and mech['profile_basis'] == 'transcript_no_skill_listing'
    assert mech['obspack_compactions'] == 1 and mech['obspack_recalls'] == 1
    obs = [e for e in events if e['name'] == 'claude_code.obspack'][0]
    withheld = 8000 - len(OBS_TEXT)
    assert obs['context']['spilled_bytes'] == withheld
    assert obs['extra']['first_exposure_tokens_avoided_est'] == -(-withheld // 4)
    assert obs['extra']['recall_tokens_reintroduced_est'] == 100
    assert obs['economics']['estimated_tokens_avoided'] == -(-withheld // 4) - 100
    # withheld tokens are carried by m2, m3, m4, m5 (4 later requests); recall by m3, m4, m5
    assert obs['extra']['carried_forward_tokens_avoided_est'] == -(-withheld // 4) * 4 - 100 * 3
    assert 'usage' not in obs  # estimates never masquerade as provider usage
    assert turns[1]['extra']['mechanisms']['worker_offloads'] == 1
    assert meta['transcript_worker_calls']['sub1']['args_chars'] > 800
    validator = schema_validator()
    for e in events:
        assert not list(validator.iter_errors(e)), e['name']


def test_no_private_text_in_events(tmp_path):
    projects = tmp_path / 'projects'
    rows = session_rows()
    rows[1] = prompt('p1', 1, text='SECRET-PROMPT-TEXT')
    write(projects / 'proj' / 'S.jsonl', rows)
    events, _ = cct.backfill_events(0, base=projects, root=tmp_path / 'home', now=T0 + 3600)
    blob = json.dumps(events)
    assert 'SECRET' not in blob and 'pytest -q' not in blob and 'classify' not in blob


def test_latest_turn_of_a_live_file_is_partial(tmp_path):
    projects = tmp_path / 'projects'
    write(projects / 'proj' / 'S.jsonl', session_rows())
    events, _ = cct.backfill_events(0, base=projects, root=tmp_path / 'home', now=T0 + 30)
    states = [e['measurement_source']['measurement_state'] for e in events if e['name'] == 'claude_code.turn']
    assert states == ['complete', 'partial']
    last = [e for e in events if e['name'] == 'claude_code.turn'][-1]
    assert last['measurement_source']['state_reason']


def test_skill_listing_means_not_lean_and_launch_env_wins(tmp_path):
    rows = session_rows()
    rows.insert(0, {'type': 'attachment', 'attachment': {'type': 'skill_listing', 'content': 'x'}})
    path = tmp_path / 'S.jsonl'
    write(path, rows)
    parsed = cct.parse_transcript(path)
    assert cct.session_mechanisms(parsed['session'], role='root')['profile'] == 'stock_or_custom'
    mech = cct.session_mechanisms(parsed['session'], role='root', env_profile='lean')
    assert mech['lean_profile'] and mech['profile_basis'] == 'launch_env'
    ledger = [{'dir': '/w', 'profile': 'lean', 'ts': T0 - 30}]
    assert cct.session_mechanisms(parsed['session'], role='root', launches=ledger)['profile_basis'] == 'launch_ledger_match_120s'


def receipt(**over):
    base = {'trace_id': 'r1', 'provider': 'groot', 'model': 'qwen3-8b', 'input_tokens': 220, 'output_tokens': 40,
            'baseline_input_tokens': 230, 'baseline_output_tokens': 40, 'ts': T0,
            'extra': {'harness': 'claude-code', 'status': 'completed', 'physical_call_attempted': True,
                      'subagent_id': 'sub1', 'free_tier_validated': True, 'validated_price_usd': 0,
                      'provider_reported_cost_usd': None, 'usage_source': 'provider_response',
                      'baseline': {'method': 'ceil_utf8_bytes_div_4_v1'}}}
    base['extra'].update(over.pop('extra', {}))
    base.update(over)
    return base


def test_displacement_counterfactuals_are_labelled_estimates():
    programmatic = cct.displacement_estimate(receipt())
    assert programmatic['counterfactual'] == 'frontier_api_call'
    assert programmatic['frontier_tokens_displaced_est'] == 270
    assert programmatic['measurement_state'] == 'estimated' and programmatic['estimate_method'] == 'ceil_utf8_bytes_div_4_v1'
    assert programmatic['cost_usd'] == 0.0 and programmatic['cost_basis'] == 'validated_free_tier_price'
    inline = cct.displacement_estimate(receipt(), {'args_chars': 820})
    # parent would have written 40 output tokens itself but spent 205 writing the tool call
    assert inline['counterfactual'] == 'frontier_agent_inline'
    assert inline['delegation_overhead_tokens_est'] == 205 and inline['frontier_tokens_displaced_est'] == -165
    failed = cct.displacement_estimate(receipt(extra={'status': 'failed'}))
    assert failed['frontier_tokens_displaced_est'] == 0 and failed['counterfactual'] is None
    paid = cct.displacement_estimate(receipt(extra={'free_tier_validated': False, 'provider_reported_cost_usd': 0.002}))
    assert paid['cost_usd'] == 0.002 and paid['cost_basis'] == 'provider_reported'


def test_worker_receipts_filters_lifecycle_rows(tmp_path):
    home = tmp_path / 'home'
    rec = home / 'receipts' / 'decisions.jsonl'
    rec.parent.mkdir(parents=True)
    rows = [receipt(), receipt(extra={'status': 'started', 'physical_call_attempted': False}),
            receipt(extra={'harness': 'codex'}), {'provider': 'groot', 'extra': {'status': 'acquired'}}]
    rec.write_text(''.join(json.dumps(r) + '\n' for r in rows))
    assert len(cct.worker_receipts(home)) == 1


def test_report_keeps_observed_attributed_estimated_apart(tmp_path):
    projects = tmp_path / 'projects'
    write(projects / 'proj' / 'S.jsonl', session_rows())
    events, meta = cct.backfill_events(0, base=projects, root=tmp_path / 'home', now=T0 + 3600)
    rep = cct.build_report(events, sessions=meta['sessions'], receipts=[receipt()],
                           transcript_calls=meta['transcript_worker_calls'], window={'range': '7d'})
    assert rep['observed']['api_requests'] == 5 and rep['observed']['sessions'] == 1
    assert rep['attribution']['packet_sessions'] == 1
    w = rep['estimated']['worker_offload']
    assert w['transcript_mcp_calls'] == 1 and w['by_parent_class'] == {'frontier_agent': 1}
    assert w['cost_usd_total'] == 0.0 and w['frontier_tokens_displaced_est'] == {'frontier_agent_inline': 40 - -(-len(json.dumps({'task': 'classify', 'context': 'c' * 796}, separators=(',', ':'))) // 4)}
    tok = cct.tokenomics_report(events, '7d', now=T0 + 3600)
    assert tok['schema'] == 'tokenomics.report.v1'
    assert tok['totals']['measured_tokens_avoided'] == 0  # nothing here is a paired measurement
    assert tok['totals']['estimated_tokens_avoided'] == rep['estimated']['obspack']['first_exposure_tokens_avoided_est'] - 100
    assert 'text' not in cct.format_text({**rep, 'tokenomics_report_v1': tok}).lower().split()


def test_stop_hook_emits_mechanisms_once_and_migrates_state(tmp_path, monkeypatch):
    monkeypatch.setenv(cct.PROFILE_ENV, 'lean')
    root = tmp_path / 'home'
    path = tmp_path / 'S.jsonl'
    rows = session_rows()
    write(path, rows[:8])
    # legacy id-only state: m1 already billed by the old hook
    state = root / 'state' / 'claude-code' / 'S.json'
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps({'message_ids': ['m1']}))
    hook = {'session_id': 'S', 'transcript_path': str(path), 'prompt_id': 'p1'}
    out = claude_code.on_stop(hook, root=root)
    assert out == [{'role': 'root', 'usage': {'input_tokens': 4, 'cache_creation_input_tokens': 200,
                                              'cache_read_input_tokens': 1200, 'output_tokens': 16}, 'messages': 2}]
    ev = [json.loads(l) for l in (root / 'tokenomics' / 'events.jsonl').read_text().splitlines()]
    # m1 was billed by the old hook; the ObservationPack result after it was never recorded, so it counts now
    assert [e['name'] for e in ev] == ['claude_code.turn', 'claude_code.obspack']
    assert ev[0]['extra']['mechanisms']['profile_basis'] == 'launch_env'
    write(path, rows)
    claude_code.on_stop({**hook, 'prompt_id': 'p2'}, root=root)
    ev = [json.loads(l) for l in (root / 'tokenomics' / 'events.jsonl').read_text().splitlines()]
    assert [e['name'] for e in ev] == ['claude_code.turn', 'claude_code.obspack', 'claude_code.turn']
    assert ev[2]['extra']['mechanisms']['worker_offloads'] == 1
    assert claude_code.on_stop(hook, root=root) == []
    saved = json.loads(state.read_text())
    assert saved['lines'] == {'S.jsonl': len(rows)} and 'm5' in saved['message_ids']


def test_cli_backfill_json(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv('Z0INT_HOME', str(tmp_path / 'home'))
    projects = tmp_path / 'projects'
    write(projects / 'proj' / 'S.jsonl', session_rows())
    out_events = tmp_path / 'ev.jsonl'
    os.utime(projects / 'proj' / 'S.jsonl', (T0 + 20, T0 + 20))
    monkeypatch.setattr(cct.time, 'time', lambda: T0 + 3600)
    assert cct.run(['--backfill', '--range', '7d', '--projects-dir', str(projects), '--json', '--events-out', str(out_events)]) == 0
    rep = json.loads(capsys.readouterr().out)
    assert rep['schema'] == 'z0int.claude_code.tokenomics_report.v0'
    assert rep['window']['source'] == 'backfill:transcripts'
    assert out_events.read_text().count('tokenomics.event.v0') == len(out_events.read_text().splitlines())
