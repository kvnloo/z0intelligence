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

    def refuse(name):  # the spy itself never lets an open reach the live home (or the link that points there)
        opened.append(name)
        if 'hermes-home' in name or name.endswith('innocent.jsonl'):
            raise PermissionError(f'test spy refused {name}')

    def spy(file, *a, **k):
        refuse(str(file))
        return real_open(file, *a, **k)

    def spy_path(self, *a, **k):
        refuse(str(self))
        return real_path_open(self, *a, **k)
    monkeypatch.setattr(builtins, 'open', spy)
    monkeypatch.setattr(Path, 'open', spy_path)
    monkeypatch.setenv('HERMES_HOME', '/workspace/hermes-home')
    li.import_dsh_jev(src, root=tmp_path / 'z0')
    assert opened and not any(p.startswith('/workspace/hermes-home') for p in opened)
    with pytest.raises(ValueError, match='hermes-home'):
        li.import_dsh_jev(Path('/workspace/hermes-home/jev/receipts.jsonl'), root=tmp_path / 'z0')
    link = tmp_path / 'innocent.jsonl'  # a link INTO hermes-home is refused too (the link is resolved, not opened)
    link.symlink_to('/workspace/hermes-home/jev/receipts.jsonl')
    opened.clear()
    with pytest.raises(ValueError, match='hermes-home'):
        li.import_dsh_jev(link, root=tmp_path / 'z0')
    assert not any('hermes-home' in p or p == str(link) for p in opened)


def jev_pair(agent, turn, ts, session):
    lineage = {'session_id': session, 'root_session_id': session, 'trace_id': f'tr-{session}', 'role': 'root'}
    return [{'ts': ts, 'harness': 'dsh', 'type': 'jev_decision', 'agentId': agent, 'turn': turn, 'policy': 'jev-v3',
             'tier': 'easy', 'route_changed': False, 'effort_changed': False},
            {'ts': ts, 'harness': 'dsh', 'type': 'model_request', 'agentId': agent, 'turn': turn, 'step': 1,
             'routed': True, **lineage}]


def test_dsh_jev_orphan_decision_never_stalls_the_import(tmp_path):
    """A jev_decision whose model_request never came (a crash) must not hold back every later agent's rows."""
    src = tmp_path / 'legacy' / 'jev.jsonl'
    home = tmp_path / 'z0'
    t = 1_780_000_000.0
    orphan = {'ts': '2026-05-28T20:26:40.000Z', 'harness': 'dsh', 'type': 'jev_decision', 'agentId': 'crashed',
              'turn': 1, 'policy': 'jev-v3', 'tier': 'hard'}
    unrouted = {'ts': '2026-05-28T20:26:41.000Z', 'harness': 'dsh', 'type': 'model_request', 'agentId': 'z',
                'turn': 1, 'routed': False, 'session_id': 'dsh-z'}
    write(src, [orphan, unrouted] + jev_pair('b', 1, '2026-05-28T20:26:42.000Z', 'dsh-b')
          + jev_pair('c', 1, '2026-05-28T20:26:43.000Z', 'dsh-c'))
    out = hc.state_dir('dsh', home) / 'shadow_decisions.jsonl'
    # while the orphan is fresh and at the tail it is held, but the other agents' rows are imported now
    first = li.import_dsh_jev(src, root=home, now=t + 60)
    assert first['rows_added'] == 2 and len(rows_of(out)) == 2
    assert first['counts'].get('no_lineage', 0) == 0 and first['counts']['model_request_only'] == 1
    again = li.import_dsh_jev(src, root=home, now=t + 60)  # re-reading from the held line counts nothing twice
    assert again['rows_added'] == 0 and again['manifest_sha256'] == first['manifest_sha256']
    # past the grace window its request is not coming: imported as no_lineage, the watermark moves past it
    late = li.import_dsh_jev(src, root=home, now=t + 3600)
    assert late['rows_added'] == 1 and late['counts']['no_lineage'] == 1 and late['totals']['model_request_only'] == 1
    rows = rows_of(out)
    assert len(rows) == 3 and len({r['decision_id'] for r in rows}) == 3
    done = li.import_dsh_jev(src, root=home, now=t + 7200)
    assert done['rows_added'] == 0 and done['run']['read_lines'] == 0 and done['manifest_sha256'] == late['manifest_sha256']


def test_dsh_jev_orphan_that_is_no_longer_the_tail_is_imported(tmp_path, monkeypatch):
    monkeypatch.setattr(li, 'JEV_TAIL_LINES', 2)
    src = tmp_path / 'legacy' / 'jev.jsonl'
    home = tmp_path / 'z0'
    orphan = {'ts': '2026-05-28T20:26:40.000Z', 'harness': 'dsh', 'type': 'jev_decision', 'agentId': 'crashed',
              'turn': 1, 'tier': 'hard'}
    write(src, [orphan] + jev_pair('b', 1, '2026-05-28T20:26:42.000Z', 'dsh-b')
          + jev_pair('c', 1, '2026-05-28T20:26:43.000Z', 'dsh-c'))
    man = li.import_dsh_jev(src, root=home, now=1_780_000_000.0 + 1)  # fresh, but four lines follow it
    assert man['rows_added'] == 3 and man['counts']['no_lineage'] == 1


def test_scrub_correction_leaves_one_row_per_turn_with_the_corrected_label(tmp_path, spine):
    """receipt.scrub_contaminated_outcomes appends a downgraded outcome_join.v1: only the latest row is exported."""
    home = tmp_path / 'z0'
    li.import_omp_v1(spine / 'decisions.jsonl', spine / 'outcomes.jsonl', root=home)
    write(spine / 'outcomes.jsonl', [join('tr-gold', {'user_correction': True, 'source': 'scrub'}, 'negative')])
    assert li.import_omp_v1(spine / 'decisions.jsonl', spine / 'outcomes.jsonl', root=home)['rows_added'] == 1
    gold = harness_id.turn_key('omp', 'omp-s1', 'tr-gold')
    le.export_tables(tmp_path / 'tA', root=home, host='host-a')
    le.export_tables(tmp_path / 'tB', root=home, host='host-b')
    le.merge([tmp_path / 'tA', tmp_path / 'tB'], tmp_path / 'm')
    for d in ('tA', 'm'):
        rows = [json.loads(x) for x in (tmp_path / d / 'omp' / 'legacy.jsonl').read_text().splitlines()]
        assert len(rows) == len({r['turn_key'] for r in rows}) == 4
        assert [r['label']['y_success'] for r in rows if r['turn_key'] == gold] == [0]


def test_shadow_tables_keep_the_latest_row_per_decision(tmp_path):
    src = tmp_path / 'legacy' / 'cs.jsonl'
    write(src, [shadow_receipt(i, served=True) for i in range(2)])
    home = tmp_path / 'z0'
    li.import_cognition_shadow(src, root=home)
    path = hc.state_dir('omp', home) / 'shadow_decisions.jsonl'
    rows = rows_of(path)
    write(path, [dict(rows[0], latency_ms=99.0)])  # a re-import that changed this decision's content
    (table,) = le.shadow_tables(home).values()
    assert len(table.rows) == 2
    assert [r['latency_ms'] for r in table.rows if r['decision_id'] == rows[0]['decision_id']] == [99.0]


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


def test_legacy_table_manifest_names_its_label_source_not_an_agentsview_polarity(tmp_path, spine):
    """omp/legacy rows are labelled by the OMP v1 effective tier, not by AgentsView shell exits."""
    home = tmp_path / 'z0'
    li.import_omp_v1(spine / 'decisions.jsonl', spine / 'outcomes.jsonl', root=home)
    le.export_tables(tmp_path / 'out', root=home, host='h')
    man = json.loads((tmp_path / 'out' / 'omp' / 'legacy.manifest.json').read_text())
    assert 'test_label_polarity' not in man
    assert man['label_source'] == 'effective_tier'
