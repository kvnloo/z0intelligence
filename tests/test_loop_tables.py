"""Per-harness x cohort training tables over the record family, and `loop merge` across hosts (synthetic only)."""
import json

import pytest

from test_loop_export import SECRET, opp_record, verified
from z0int import harness_capture as hc
from z0int import loop_export as le


def put(home, harness, name, rows):
    path = hc.state_dir(harness, home) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as fh:
        fh.write(''.join(json.dumps(r) + '\n' for r in rows))


def opp(harness, session, trace, **kw):
    rec = opp_record(session, trace, **kw)
    rec.update(schema=hc.schema(harness, 'opportunity_record'), harness=harness, recorded_at='2026-09-30T10:00:00Z')
    rec['opportunity']['intent']['request'] = None
    return rec


def ver(harness, session, trace, state, cohort=None):
    row = dict(verified(session, trace, state), schema=hc.schema(harness, 'turn_outcome_verified'), harness=harness)
    if cohort:
        row['cohort'] = cohort
    return row


def cc_cohort(sid):
    return 'interactive'


# ----------------------------------------------------------------------------- 10. every harness, per cohort
@pytest.mark.parametrize('harness', hc.HARNESSES)
def test_export_accepts_every_harness_v0_record_family(tmp_path, harness):
    home = tmp_path / 'z0'
    put(home, harness, 'opportunities.jsonl', [opp(harness, 's1', 't1')])
    put(home, harness, 'outcomes_verified.jsonl', [ver(harness, 's1', 't1', 'verified_success', cohort='interactive')])
    index = le.export_tables(tmp_path / 'out', root=home, host='h1', cohort_fns={'claude-code': cc_cohort})
    (key,) = index['tables']
    assert key == f'{harness}/interactive'
    rows = [json.loads(x) for x in (tmp_path / 'out' / harness / 'interactive.jsonl').read_text().splitlines()]
    assert len(rows) == 1 and rows[0]['harness'] == harness and rows[0]['label']['y_success'] == 1
    assert index['records'][harness]['unsupported_schema'] == {}


def test_export_tables_split_by_harness_and_cohort_and_count_what_they_cannot_use(tmp_path):
    home = tmp_path / 'z0'
    put(home, 'codex', 'opportunities.jsonl', [opp('codex', 'th-1', 'a'), opp('codex', 'th-2', 'b'),
                                               {'schema': 'z0int.codex.opportunity_record.v1', 'session_id': 'th-3'}])
    put(home, 'codex', 'outcomes_verified.jsonl', [ver('codex', 'th-1', 'a', 'verified_failure', cohort='automated'),
                                                   ver('codex', 'th-2', 'b', 'verified_success', cohort='interactive')])
    put(home, 'codex', 'failures.jsonl', [{'schema': 'z0int.codex.failure.v0', 'kind': 'missing_verifier'},
                                          {'schema': 'z0int.codex.failure.v0', 'kind': 'unjoined'},
                                          {'schema': 'z0int.codex.failure.v0', 'kind': 'unjoined'}])
    put(home, 'codex', 'outcomes.jsonl', [{'schema': 'z0int.codex.mystery.v0'}])
    put(home, 'hermes', 'opportunities.jsonl', [opp('hermes', 'cron_1', 'c1')])
    put(home, 'hermes', 'outcomes_verified.jsonl', [ver('hermes', 'cron_1', 'c1', 'unverified', cohort='automated')])
    put(home, 'hermes', 'opportunities.jsonl', [opp('hermes', 'nobody', 'n1')])  # never classified -> unknown
    index = le.export_tables(tmp_path / 'out', root=home, host='h1')
    assert sorted(index['tables']) == ['codex/automated', 'codex/interactive', 'hermes/automated', 'hermes/unknown']
    for key, entry in index['tables'].items():
        harness, cohort = key.split('/')
        rows = [json.loads(x) for x in (tmp_path / 'out' / harness / f'{cohort}.jsonl').read_text().splitlines()]
        assert {(r['harness'], r['cohort']) for r in rows} == {(harness, cohort)} and len(rows) == entry['rows']
        man = json.loads((tmp_path / 'out' / harness / f'{cohort}.manifest.json').read_text())
        assert (man['harness'], man['cohort'], man['host']) == (harness, cohort, 'h1')
    assert index['records']['codex']['unsupported_schema'] == {'z0int.codex.mystery.v0': 1,
                                                               'z0int.codex.opportunity_record.v1': 1}
    assert index['records']['codex']['failures'] == {'missing_verifier': 1, 'unjoined': 2}
    blob = ''.join(p.read_text() for p in (tmp_path / 'out').rglob('*.json*'))
    for needle in ('payments', 'th-1', 'cron_1', SECRET):
        assert needle not in blob


def test_the_table_api_cannot_pool_harnesses_or_cohorts(tmp_path):
    home = tmp_path / 'z0'
    put(home, 'codex', 'opportunities.jsonl', [opp('codex', 'th-1', 'a'), opp('codex', 'th-2', 'b')])
    put(home, 'codex', 'outcomes_verified.jsonl', [ver('codex', 'th-1', 'a', 'verified_failure', cohort='automated'),
                                                   ver('codex', 'th-2', 'b', 'verified_success', cohort='interactive')])
    tables = le.build_tables(home)
    auto, inter = tables[('codex', 'automated')], tables[('codex', 'interactive')]
    with pytest.raises(ValueError):
        le.Table('codex', 'interactive', auto.rows + inter.rows)
    with pytest.raises(ValueError):
        le.Table('hermes', 'automated', auto.rows)
    with pytest.raises(ValueError):
        le.Table('codex', 'not-a-cohort', [])


# ----------------------------------------------------------------------------- 11. first_opp without built_at
def test_first_opportunity_falls_back_to_recorded_at_when_no_packet_was_built(tmp_path):
    s = tmp_path / 'state'
    s.mkdir()
    rec = opp_record('s1', 't1')
    rec['opportunity']['provenance']['built_at'] = None  # a non-repo turn: empty state packet
    rec['recorded_at'] = '2026-09-30T09:00:00Z'
    (s / 'opportunities.jsonl').write_text(json.dumps(rec) + '\n')
    rows = [{'session_id': 's1', 'trace_id': 't1', 'verification_state': 'unverified',
             'turn': {'started_at': '2026-09-30T10:00:00Z', 'harness_message': False}}]
    out = le.sweep_counts(rows, s, since_spec='7d', now=0)
    assert out['first_opportunity_at'] == '2026-09-30T09:00:00Z'
    assert out['total']['turns_after_first_opportunity'] == 1


# ----------------------------------------------------------------------------- 12. loop merge across hosts
def two_hosts(tmp_path):
    a, b = tmp_path / 'host-a', tmp_path / 'host-b'
    put(a, 'codex', 'opportunities.jsonl', [opp('codex', 'th-1', 'x'), opp('codex', 'th-1', 'y')])
    put(a, 'codex', 'outcomes_verified.jsonl', [ver('codex', 'th-1', 'x', 'verified_success', cohort='interactive'),
                                                ver('codex', 'th-1', 'y', 'unverified', cohort='interactive')])
    put(b, 'codex', 'opportunities.jsonl', [opp('codex', 'th-1', 'y'), opp('codex', 'th-9', 'z')])
    put(b, 'codex', 'outcomes_verified.jsonl', [ver('codex', 'th-1', 'y', 'verified_failure', cohort='interactive'),
                                                ver('codex', 'th-9', 'z', 'verified_success', cohort='interactive')])
    le.export_tables(tmp_path / 'tA', root=a, host='host-a')
    le.export_tables(tmp_path / 'tB', root=b, host='host-b')
    return tmp_path / 'tA', tmp_path / 'tB'


def test_loop_merge_keeps_host_provenance_and_is_reproducible(tmp_path):
    ta, tb = two_hosts(tmp_path)
    first = le.merge([ta, tb], tmp_path / 'm1')
    rows = [json.loads(x) for x in (tmp_path / 'm1' / 'codex' / 'interactive.jsonl').read_text().splitlines()]
    hosts = sorted(tuple(r['hosts']) for r in rows)
    assert hosts == [('host-a',), ('host-a', 'host-b'), ('host-b',)]
    both = next(r for r in rows if r['hosts'] == ['host-a', 'host-b'])
    assert both['label']['y_success'] == 0  # the resolved label wins over the host that had none yet
    assert first['tables']['codex/interactive']['rows'] == 3 and first['hosts'] == ['host-a', 'host-b']
    again = le.merge([tb, ta], tmp_path / 'm2')
    assert again['manifest_sha256'] == first['manifest_sha256']
    assert (tmp_path / 'm1' / 'codex' / 'interactive.jsonl').read_bytes() == \
        (tmp_path / 'm2' / 'codex' / 'interactive.jsonl').read_bytes()


def test_loop_merge_refuses_to_pool_harnesses_cohorts_or_schema_versions(tmp_path):
    with pytest.raises(ValueError, match='cohort'):
        le.merge_tables([le.Table('codex', 'interactive', []), le.Table('codex', 'agent', [])])
    with pytest.raises(ValueError, match='harness'):
        le.merge_tables([le.Table('codex', 'interactive', []), le.Table('grok', 'interactive', [])])
    ta, tb = two_hosts(tmp_path)
    man = tb / 'codex' / 'interactive.manifest.json'
    m = json.loads(man.read_text())
    m['table_version'] = '9.9.9'
    man.write_text(json.dumps(m))
    with pytest.raises(ValueError, match='schema'):
        le.merge([ta, tb], tmp_path / 'm')


def test_merged_tables_refuse_text_keys(tmp_path):
    ta, tb = two_hosts(tmp_path)
    path = tb / 'codex' / 'interactive.jsonl'
    rows = [json.loads(x) for x in path.read_text().splitlines()]
    rows[0]['observed'] = {'prompt': 'leaked text'}
    path.write_text(''.join(json.dumps(r) + '\n' for r in rows))
    with pytest.raises(ValueError, match='private'):
        le.merge([ta, tb], tmp_path / 'm')
    assert not (tmp_path / 'm' / 'codex' / 'interactive.jsonl').exists()


def test_loop_cli_export_and_merge(tmp_path, capsys):
    ta, tb = two_hosts(tmp_path)
    assert le._main(['merge', '--in', str(ta), '--in', str(tb), '--out', str(tmp_path / 'mc')]) == 0
    assert 'codex/interactive' in capsys.readouterr().out
    assert le._main(['export', '--root', str(tmp_path / 'host-a'), '--host', 'host-a',
                     '--out-dir', str(tmp_path / 'ec')]) == 0
    assert (tmp_path / 'ec' / 'codex' / 'interactive.jsonl').is_file()
