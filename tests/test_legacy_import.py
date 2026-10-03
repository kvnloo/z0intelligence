"""Legacy importers: OMP v1 spine, OMP cognition-shadow and DSH jev receipts -> cohort legacy (synthetic only)."""
import builtins
import json
from pathlib import Path

import pytest

from z0int import harness_capture as hc
from z0int import harness_id
from z0int import loop_export as le

try:
    from z0int import legacy_import as li
except ImportError:  # red phase: the importer module does not exist yet; each test then fails on its own
    li = None

PROMPT = 'please refactor the payments module in /home/someone/secret-repo'


def write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as fh:
        fh.write(''.join(json.dumps(r) + '\n' for r in rows))


def rows_of(path):
    return hc._read_jsonl(path)


# ----------------------------------------------------------------------------- 13. OMP v1 spine
def receipt(trace, session='omp-s1', **kw):
    return {'schema': 'z0int.decision_receipt.v1', 'trace_id': trace, 'session_id': session, 'capability_id': 'route.code',
            'provider': 'local_mb', 'model': 'mb-1', 'prediction': PROMPT, 'route': 'local', 'execution': 'shadow',
            'ts': 1_780_000_000.0, 'extra': {'prompt': PROMPT, 'cwd': '/home/someone/secret-repo'}, **kw}


def join(trace, outcome, tier, session='omp-s1'):
    return {'schema': 'z0int.outcome_join.v1', 'ts': 1_780_000_100.0, 'trace_id': trace, 'outcome': outcome,
            'outcome_tier': tier, 'receipt': receipt(trace, session)}


@pytest.fixture
def spine(tmp_path):
    receipts = tmp_path / 'legacy' / 'receipts'
    write(receipts / 'decisions.jsonl', [
        receipt('tr-gold'), receipt('tr-neg'), receipt('tr-ambient'), receipt('tr-exec'), receipt('tr-none'),
        dict(receipt('tr-gold'), outcome={'test_pass': True, 'verification_source': 'ci'}, outcome_tier='gold'),
    ])
    write(receipts / 'outcomes.jsonl', [
        join('tr-gold', {'test_pass': True, 'verification_source': 'ci'}, 'gold'),
        join('tr-neg', {'user_correction': True, 'source': 'hermes'}, 'negative'),
        # a stale bridge stamped gold on an ambient close: effective_tier says execution, never a success label
        join('tr-ambient', {'success': True, 'tool_ok': True, 'test_pass': True, 'source': 'bridge_turn_end'}, 'gold'),
        join('tr-exec', {'execution_completed': True, 'source': 'omp_turn_end'}, 'execution'),
    ])
    return receipts


def test_omp_v1_spine_imports_gold_and_negative_tiers_as_the_only_labels(tmp_path, spine):
    home = tmp_path / 'z0'
    man = li.import_omp_v1(spine / 'decisions.jsonl', spine / 'outcomes.jsonl', root=home)
    rows = {r['turn_key']: r for r in rows_of(hc.state_dir('omp', home) / 'imported_turns.jsonl')}
    key = lambda t: harness_id.turn_key('omp', 'omp-s1', t)  # noqa: E731
    assert set(rows) == {key('tr-gold'), key('tr-neg'), key('tr-ambient'), key('tr-exec')}  # no outcome: not a row
    assert all(r['schema'] == 'z0int.omp.imported_turn.v0' and r['cohort'] == 'legacy' and r['harness'] == 'omp'
               for r in rows.values())
    assert rows[key('tr-gold')]['label'] == {'tier': 'gold', 'y_success': 1, 'state': 'verified_success'}
    assert rows[key('tr-neg')]['label'] == {'tier': 'negative', 'y_success': 0, 'state': 'verified_failure'}
    assert rows[key('tr-ambient')]['label'] == {'tier': 'execution', 'y_success': None, 'state': 'unverified'}
    assert rows[key('tr-exec')]['label']['y_success'] is None
    assert man['counts']['no_outcome'] == 1 and man['rows_added'] == 4
    raw = (hc.state_dir('omp', home) / 'imported_turns.jsonl').read_text()
    assert 'payments' not in raw and 'secret-repo' not in raw and 'omp-s1' not in raw
    le.assert_private(rows.values())


def test_omp_v1_rerun_adds_zero_rows_and_keeps_the_manifest(tmp_path, spine):
    home = tmp_path / 'z0'
    first = li.import_omp_v1(spine / 'decisions.jsonl', spine / 'outcomes.jsonl', root=home)
    second = li.import_omp_v1(spine / 'decisions.jsonl', spine / 'outcomes.jsonl', root=home)
    assert second['rows_added'] == 0 and second['manifest_sha256'] == first['manifest_sha256']
    write(spine / 'outcomes.jsonl', [join('tr-none', {'reverted': True, 'source': 'ci'}, 'negative')])
    third = li.import_omp_v1(spine / 'decisions.jsonl', spine / 'outcomes.jsonl', root=home)
    assert third['rows_added'] == 1 and third['manifest_sha256'] != first['manifest_sha256']


# ----------------------------------------------------------------------------- 14. OMP cognition-shadow
def shadow_receipt(i, served):
    row = {'label': 'nemotron_orchestrator_8b', 'backend': 'local_slm' if served else None, 'model': 'nemotron-8b',
           'selected_action': 'read' if served else None, 'abstained': not served, 'invalid_call': False,
           'latency_ms': 12.5 if served else 0.0}
    if served:
        row.update(candidate_action_count=3, parse_error=None, confidence=0.7)
    else:
        row.update(parse_error='transport_error: <urlopen error [Errno 111] Connection refused>',
                   error='transport_error: <urlopen error [Errno 111] Connection refused>')
    return {'schema': 'z0int.cognition.shadow.receipt.v1', 'ts': 1_780_000_000.0 + i, 'trace_id': f'cs-{i}',
            'session_id': 'omp-s1', 'state': f'OMP tool_call: read\ninput: {{"path": "/home/someone/secret-{i}.txt"}}',
            'graph_digest': 'g', 'candidate_action_count': 3, 'legal_ids': ['read', 'grep', 'bash'],
            'eliminated': [], 'deterministic_solution': None, 'escalation': {}, 'authority': ['read'],
            'budget_units': 8, 'spent_units': 1, 'shadow': [row], 'selected_action': None, 'executed_action': None}


def test_cognition_shadow_keeps_served_answers_only(tmp_path):
    src = tmp_path / 'legacy' / 'shadow' / 'cognition-shadow.jsonl'
    write(src, [shadow_receipt(i, served=i in (17, 4242)) for i in range(6902)])
    home = tmp_path / 'z0'
    man = li.import_cognition_shadow(src, root=home)
    rows = rows_of(hc.state_dir('omp', home) / 'shadow_decisions.jsonl')
    assert len(rows) == 2 and man['rows_added'] == 2
    assert man['counts']['backend_unavailable'] == 6900
    r = rows[0]
    assert r['schema'] == 'z0int.omp.shadow_decision.v0' and r['cohort'] == 'legacy' and r['y'] is None
    assert r['actual_tool'] == {'name': 'read', 'risk_class': 'read'}
    assert r['decision']['selected_action'] == 'read' and r['challenger']['id'] == 'nemotron_orchestrator_8b'
    raw = (hc.state_dir('omp', home) / 'shadow_decisions.jsonl').read_text()
    assert 'secret' not in raw and 'input' not in raw and 'omp-s1' not in raw
    le.assert_private(rows)


# ----------------------------------------------------------------------------- 15. DSH jev receipts
def dsh_rows():
    lineage = {'session_id': 'dsh-root-1', 'parent_session_id': None, 'root_session_id': 'dsh-root-1',
               'trace_id': 'tr-dsh-1', 'root_resolution': 'self_root', 'agent_id': 'session-dsh-root-1',
               'origin': None, 'delegation_depth': 0, 'role': 'root', 'attempt': 0, 'operation_id': 'op-1'}
    return [
        {'ts': '2026-09-29T10:00:00.000Z', 'harness': 'dsh', 'type': 'plugin_apply', 'root': '/x', 'receipts': '/y'},
        {'ts': '2026-09-29T10:00:01.000Z', 'harness': 'dsh', 'type': 'user_text_ok', 'chars': 120},
        {'ts': '2026-09-29T10:00:02.000Z', 'harness': 'dsh', 'type': 'jev_decision', 'agentId': 'session-dsh-root-1',
         'turn': 1, 'policy': 'jev-v3', 'tier': 'hard', 'difficulty': 0.8, 'specialty': 'code', 'confidence': 0.66,
         'costly_mistake': False, 'starting_provider': 'deepseek', 'starting_model': 'deepseek-chat',
         'selected_provider': 'deepseek', 'selected_model': 'deepseek-reasoner', 'selected_effort': 'high',
         'canonical_candidate': 'deepseek:deepseek-reasoner', 'route_changed': True, 'effort_changed': True,
         'model_routing': 'applied', 'fallback_reason': None, 'jev_latency_ms': 40, 'adapter_latency_ms': 55},
        {'ts': '2026-09-29T10:00:02.100Z', 'harness': 'dsh', 'type': 'model_request', 'agentId': 'session-dsh-root-1',
         'parentId': None, 'turn': 1, 'step': 1, 'purpose': 'root', 'provider': 'deepseek',
         'model': 'deepseek-reasoner', 'reasoningEffort': 'high', 'routed': True, **lineage},
        {'ts': '2026-09-29T10:00:03.000Z', 'harness': 'dsh', 'type': 'model_request', 'agentId': 'session-dsh-root-1',
         'turn': 1, 'step': 2, 'purpose': 'continuation', 'routed': False, **lineage},
        {'ts': '2026-09-29T10:01:00.000Z', 'harness': 'dsh', 'type': 'jev_decision', 'agentId': 'session-dsh-root-1',
         'turn': 2, 'fallback_reason': 'adapter_exception:TypeError: cannot read /home/someone/secret',
         'route_changed': False, 'effort_changed': False},
        {'ts': '2026-09-29T10:01:00.100Z', 'harness': 'dsh', 'type': 'model_request', 'agentId': 'session-dsh-root-1',
         'turn': 2, 'step': 1, 'purpose': 'root', 'routed': True, **dict(lineage, operation_id='op-2')},
    ]


def test_dsh_jev_receipts_become_legacy_shadow_decisions_never_gold(tmp_path):
    src = tmp_path / 'legacy' / 'dsh' / 'jev' / 'receipts.jsonl'
    write(src, dsh_rows())
    home = tmp_path / 'z0'
    man = li.import_dsh_jev(src, root=home)
    rows = rows_of(hc.state_dir('dsh', home) / 'shadow_decisions.jsonl')
    assert len(rows) == 2 and man['rows_added'] == 2
    assert all(r['schema'] == 'z0int.dsh.shadow_decision.v0' and r['cohort'] == 'legacy' and r['y'] is None
               and r['label'] is None for r in rows)
    first, second = sorted(rows, key=lambda r: r['decision'].get('tier') is None)
    assert first['turn_key'] == harness_id.turn_key('dsh', 'dsh-root-1', '1')
    assert first['decision']['tier'] == 'hard' and first['decision']['route_changed'] is True
    assert second['decision']['fallback'] == 'adapter_exception'
    assert man['counts']['model_request_only'] == 1
    raw = (hc.state_dir('dsh', home) / 'shadow_decisions.jsonl').read_text()
    assert 'secret' not in raw and 'dsh-root-1' not in raw
    le.assert_private(rows)


def test_dsh_importer_never_reads_hermes_home(tmp_path, monkeypatch):
    src = tmp_path / 'legacy' / 'dsh' / 'jev' / 'receipts.jsonl'
    write(src, dsh_rows())
    opened = []
    real_open, real_path_open = builtins.open, Path.open

    def spy(file, *a, **k):
        opened.append(str(file))
        return real_open(file, *a, **k)

    def spy_path(self, *a, **k):
        opened.append(str(self))
        return real_path_open(self, *a, **k)
    monkeypatch.setattr(builtins, 'open', spy)
    monkeypatch.setattr(Path, 'open', spy_path)
    monkeypatch.setenv('HERMES_HOME', '/workspace/hermes-home')
    li.import_dsh_jev(src, root=tmp_path / 'z0')
    assert opened and not any(p.startswith('/workspace/hermes-home') for p in opened)
    with pytest.raises(ValueError, match='hermes-home'):
        li.import_dsh_jev(Path('/workspace/hermes-home/jev/receipts.jsonl'), root=tmp_path / 'z0')


# ----------------------------------------------------------------------------- 16/17. privacy + idempotency
def test_every_importer_is_idempotent_by_watermark(tmp_path, spine):
    shadow = tmp_path / 'legacy' / 'cs.jsonl'
    write(shadow, [shadow_receipt(i, served=i % 2 == 0) for i in range(6)])
    jev = tmp_path / 'legacy' / 'jev.jsonl'
    write(jev, dsh_rows())
    home = tmp_path / 'z0'
    runs = [lambda: li.import_omp_v1(spine / 'decisions.jsonl', spine / 'outcomes.jsonl', root=home),
            lambda: li.import_cognition_shadow(shadow, root=home),
            lambda: li.import_dsh_jev(jev, root=home)]
    for run in runs:
        first, second = run(), run()
        assert first['rows_added'] > 0 and second['rows_added'] == 0
        assert first['manifest_sha256'] == second['manifest_sha256'] and second['run']['read_lines'] == 0
    write(shadow, [shadow_receipt(99, served=True)])
    assert li.import_cognition_shadow(shadow, root=home)['rows_added'] == 1  # appended lines are picked up


def test_new_tables_refuse_text_keys(tmp_path, spine):
    home = tmp_path / 'z0'
    li.import_omp_v1(spine / 'decisions.jsonl', spine / 'outcomes.jsonl', root=home)
    index = le.export_tables(tmp_path / 'out', root=home, host='h')
    assert 'omp/legacy' in index['tables'] and index['tables']['omp/legacy']['rows'] == 4
    path = hc.state_dir('omp', home) / 'imported_turns.jsonl'
    rows = rows_of(path)
    rows[0]['prompt'] = PROMPT
    path.write_text(''.join(json.dumps(r) + '\n' for r in rows))
    with pytest.raises(ValueError, match='private'):
        le.export_tables(tmp_path / 'out2', root=home, host='h')
    with pytest.raises(ValueError, match='private'):
        le.assert_private([{'turn_key': 'k', 'decision': {'text': 'x'}}])


def test_legacy_import_cli(tmp_path, spine, capsys):
    rc = li._main(['omp-v1', '--decisions', str(spine / 'decisions.jsonl'), '--outcomes', str(spine / 'outcomes.jsonl'),
                   '--root', str(tmp_path / 'z0')])
    assert rc == 0 and json.loads(capsys.readouterr().out)['rows_added'] == 4
