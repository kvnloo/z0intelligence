"""Routing episode compiler v0 (z0int#143): synthetic fixtures only, every home under tmp_path."""
import hashlib
import json
from pathlib import Path

import pytest

from z0int import episode_eval as EV
from z0int import episode_split as SP
from z0int import episodes as E
from z0int import episodes_cli as CLI
from z0int import harness_capture as hc
from z0int import sft_adapter as SFT
from z0int.memory.event_log import EventLog

SECRET = 'PRIVATE-REQUEST-TEXT please summarize the quarterly numbers'


def put(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as fh:
        for r in rows:
            fh.write(json.dumps(r) + '\n')


def ts(day: str, hour: int = 12) -> float:
    from datetime import datetime, timezone
    return datetime.fromisoformat(f'{day}T{hour:02d}:00:00').replace(tzinfo=timezone.utc).timestamp()


def route_rows(sub: str, day: str, *, session='s-route', status='completed', provider='groot', caller='c1'):
    attempt = {'schema': 'z0int.decision_receipt.v1', 'trace_id': f'att-{sub}', 'session_id': session,
               'capability_id': 'codex.delegated_text', 'provider': provider, 'model': 'qwen3-8b', 'route': 'model',
               'execution': 'live', 'ts': ts(day), 'latency_ms': 900.0, 'input_tokens': 300, 'output_tokens': 50,
               'extra': {'subtask': SECRET, 'subagent_id': sub, 'task_trace_id': sub, 'attempt_index': 0,
                         'status': status, 'category': 'explicit', 'harness': 'claude-code', 'caller_trace_id': caller,
                         'route_source': 'codex_explicit_selection', 'policy_revision': 'rev-1',
                         'task_sha256': hashlib.sha256((SECRET + sub).encode()).hexdigest(),
                         'context_sha256': 'c' * 64, 'physical_call_attempted': True,
                         'output_sha256': 'o' * 64}}
    dispatch = {'schema': 'z0int.decision_receipt.v1', 'trace_id': f'dispatch-{sub}', 'session_id': session,
                'capability_id': 'intelligence.dispatch', 'execution': 'orchestration', 'ts': ts(day),
                'extra': {'harness': 'claude-code', 'caller_trace_id': caller, 'status': 'completed',
                          'function': 'effect_inference_tiebreak', 'request_sha256': 'r' * 64,
                          'result': {'subagent_id': sub, 'ok': True, 'output': SECRET, 'provider': provider,
                                     'route': {'source': 'codex_explicit_selection', 'category': 'explicit',
                                               'candidates': [{'provider': provider, 'model': 'qwen3-8b'}],
                                               'skipped': [{'provider': 'local', 'reason': 'x'}]}}}}
    return [attempt, dispatch]


def omp_row(trace: str, day: str, *, session='s-omp', prompt=SECRET, placed='cerebras/free-coder'):
    return {'schema': 'z0int.decision_receipt.v1', 'trace_id': trace, 'session_id': session,
            'capability_id': 'coding.generic', 'provider': 'kerdoios_plan', 'route': 'model', 'execution': 'live',
            'ts': ts(day), 'latency_ms': 1200, 'input_tokens': 1000, 'output_tokens': 200, 'prompt': prompt,
            'kerdoios_plan': {'estimated_cost': 0.0, 'placements': [{'offer_id': placed, 'provider': 'cerebras'}],
                              'rejections': [{'offer_id': 'frontier/paid', 'reason': 'cost'},
                                             {'offer_id': 'local/qwen-32b', 'reason': 'vram'}]},
            'preflight': {'label': 'generic', 'confidence': 0.7, 'decision': {'route': 'model'},
                          'work_requirement': {'coding': 0.8, 'reasoning': 0.4, 'tool_use': True}}}


def join_row(trace: str, outcome: dict, day: str):
    return {'schema': 'z0int.outcome_join.v1', 'trace_id': trace, 'outcome': outcome, 'ts': ts(day, 13),
            'receipt': {'trace_id': trace}}


def opportunity(session: str, trace: str, day: str, *, legal_act=True):
    space = [{'kind': 'ACT', 'action': 'proceed', 'legal': legal_act, 'blocked_by': []},
             {'kind': 'ASK', 'action': 'ask_user', 'legal': not legal_act, 'blocked_by': []},
             {'kind': 'ESCALATE', 'action': 'surface_conflict', 'legal': False, 'blocked_by': []},
             {'kind': 'ABSTAIN', 'action': 'state_unknown', 'legal': True, 'blocked_by': []}]
    return {'schema': hc.schema('claude-code', 'opportunity_record'), 'session_id': session,
            'gate': 'ACT' if legal_act else 'ASK',
            'opportunity': {'semantic_id': f'sem-{trace}', 'scope': {'mode': 'unscoped', 'families': []},
                            'state': {'claims': [], 'unknowns': [], 'contradictions': []},
                            'intent': {'effects': ['read'], 'revision': f'rev-{trace}'},
                            'authority': {'source': 'harness-default', 'grants': ['read']},
                            'action_space': space,
                            'trace': {'trace_id': trace, 'opportunity_id': f'opp-{trace}'},
                            'provenance': {'packet_id': 'pk', 'built_at': f'{day}T10:00:00Z'}}}


def turn(session, trace, day, *, asked=False, state='verified_success'):
    obs = {'schema': hc.schema('claude-code', 'turn_outcome'), 'session_id': session, 'trace_id': trace,
           'asked_user': asked, 'tool_calls': 3, 'assistant_messages': 2}
    ver = {'schema': hc.schema('claude-code', 'turn_outcome_verified'), 'session_id': session, 'trace_id': trace,
           'verification_state': state, 'label_class': 'test', 'label_confidence': 0.9,
           'signals': [{'oracle': 'test_run', 'polarity': 1}], 'verified_at': f'{day}T11:00:00Z',
           'turn': {'started_at': f'{day}T10:00:00Z', 'ended_at': f'{day}T10:02:00Z', 'tool_calls': 3},
           'verifier': {'version': 'v-test'}}
    return obs, ver


@pytest.fixture
def home(tmp_path):
    h = tmp_path / 'src-home'
    put(h / 'receipts/decisions.jsonl', route_rows('sub1', '2026-09-20') + route_rows('sub2', '2026-10-10',
                                                                                        session='s-r2'))
    put(h / 'receipts/decisions.jsonl', [omp_row('omp-1', '2026-09-21'), omp_row('omp-2', '2026-10-11',
                                                                                 session='s-omp2'),
                                         omp_row('omp-3', '2026-09-22', session='s-omp3')])
    put(h / 'receipts/outcomes.jsonl', [
        join_row('omp-1', {'test_pass': True, 'verification_source': 'ci', 'source': 'ci'}, '2026-09-21'),
        join_row('omp-2', {'user_correction': True, 'source': 'omp'}, '2026-10-11'),
        # an ambient close never mints gold
        join_row('omp-3', {'success': True, 'test_pass': True, 'source': 'omp_turn_end'}, '2026-09-22')])
    st = h / 'state/claude-code'
    for sess, trace, day, legal, asked, state in (('s-a', 't1', '2026-09-23', True, False, 'verified_success'),
                                                  ('s-b', 't2', '2026-10-12', False, True, 'verified_failure'),
                                                  ('s-c', 't3', '2026-10-03', True, False, 'unverified')):
        put(st / 'opportunities.jsonl', [opportunity(sess, trace, day, legal_act=legal)])
        o, v = turn(sess, trace, day, asked=asked, state=state)
        put(st / 'outcomes.jsonl', [o])
        put(st / 'outcomes_verified.jsonl', [v])
    from z0int.loop_export import row_turn_key
    put(st / 'shadow_decisions.jsonl', [{
        'schema': hc.schema('claude-code', 'shadow_decision'), 'decision_id': 'sd1', 'decision': 'ASK', 'y': 1,
        'label': None, 'challenger': {'id': 'teacher-x', 'kind': 'model', 'sha256': 'f' * 64},
        'turn_key': row_turn_key('claude-code', 's-a', 't1'), 'opportunity_id': 'opp-t1', 'group': 'g',
        'distribution': {'ASK': 0.7, 'ACT': 0.3}, 'status': 'ok', 'source': 'shadow_slot', 'day': None}])
    return h


def tree_digest(root: Path):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob('*')) if p.is_file()}


def by_source(eps):
    out = {}
    for e in eps:
        out.setdefault(e['source'], []).append(e)
    return out


# ----------------------------------------------------------------------------- compile
def test_compile_keeps_every_143_field_and_no_text(home):
    eps, stats = E.compile_episodes(home, host='h')
    blob = json.dumps(eps)
    assert SECRET not in blob and 'PRIVATE' not in blob
    assert stats['lineage']['fatal'] == 0 and not stats['invalid']
    for e in eps:
        for k in ('episode_id', 'decided_at', 'source_refs', 'evidence', 'candidates', 'action', 'receipt',
                  'outcome', 'provenance', 'lineage', 'hashes'):
            assert k in e
        assert all(r['line_sha256'] and r['store'] for r in e['source_refs'])
        E.validate(e)


def test_worker_route_episode(home):
    eps = by_source(E.compile_episodes(home)[0])['receipts.route_worker']
    e = next(x for x in eps if x['day'] == '2026-09-20')
    assert e['candidates'] == {'set': ['PARENT_ONLY', 'groot', 'local'], 'source': 'plan_receipt', 'exact': True,
                               'legal': ['PARENT_ONLY', 'groot'], 'skipped': ['local']}
    assert e['action']['chosen'] == 'groot'
    assert e['receipt']['execution_completed'] is True and e['receipt']['input_tokens'] == 300
    assert e['outcome']['observed'] is False and e['outcome']['why_missing'] == 'execution_is_not_verification'
    assert e['evidence']['content_sha'] and 'kw.language' in e['evidence']['features']
    assert e['evidence']['features']['kw.language'] == 1  # "summarize" seen in memory, stored as a boolean
    assert 'worker_routing.plan_route' in e['policy_replays']


def test_omp_route_tiers_and_kerdoios_candidates(home):
    eps = {e['lineage']['turn_key']: e for e in by_source(E.compile_episodes(home)[0])['receipts.omp_v1']}
    from z0int.harness_id import turn_key
    gold, neg, ambient = (eps[turn_key('omp', s, t)] for s, t in
                          (('s-omp', 'omp-1'), ('s-omp2', 'omp-2'), ('s-omp3', 'omp-3')))
    assert gold['candidates']['set'] == ['cerebras/free-coder', 'frontier/paid', 'local/qwen-32b']
    assert gold['candidates']['exact'] and gold['action']['chosen'] == 'cerebras/free-coder'
    assert (gold['outcome']['y_success'], neg['outcome']['y_success']) == (1, 0)
    assert ambient['outcome']['observed'] is False and ambient['outcome']['tier'] == 'execution'
    assert E.is_labelled(gold) and not E.is_labelled(ambient)


def test_decision_opportunity_episode(home):
    eps = {e['day']: e for e in by_source(E.compile_episodes(home)[0])['state.claude-code.opportunity']}
    a, b, c = eps['2026-09-23'], eps['2026-10-12'], eps['2026-10-03']
    assert a['candidates']['set'] == ['ACT', 'ASK', 'ABSTAIN', 'ESCALATE'] and a['candidates']['legal'] == ['ACT',
                                                                                                          'ABSTAIN']
    assert a['action']['chosen'] == 'ACT' and a['outcome']['y_success'] == 1 and a['outcome']['independent']
    assert b['action']['chosen'] == 'ASK' and b['outcome']['y_success'] == 0
    assert c['outcome']['observed'] is False
    assert a['policy_replays']['deterministic_gate'] == {'choice': 'ACT', 'replayed': 'ACT', 'revision_match': True}
    assert a['receipt']['latency_ms'] == 120000.0
    assert a['evidence']['state_ref']['semantic_id'] == 'sem-t1'


def test_synthetic_teacher_label_never_becomes_truth(home):
    eps = E.compile_episodes(home)[0]
    sh = [e for e in eps if e['provenance']['kind'] == 'synthetic']
    assert len(sh) == 1
    s = sh[0]
    assert s['outcome']['observed'] is False and s['outcome']['y_success'] is None
    assert s['provenance']['teacher_claim'] == 1 and not E.is_labelled(s)
    parent = next(e for e in eps if e['source'] == 'state.claude-code.opportunity' and e['day'] == '2026-09-23')
    assert s['lineage']['parent_episode_ids'] == [parent['episode_id']] and s['day'] == parent['day']
    forged = json.loads(json.dumps(s))
    forged['outcome'] = {'observed': True, 'y_success': 1, 'independent': True, 'state': 'verified_success'}
    with pytest.raises(E.EpisodeInvalid, match='synthetic'):
        E.validate(forged)
    assert E.lineage_check([forged])['fatal'] == 1


def test_validate_fails_closed(home):
    e = E.compile_episodes(home)[0][0]
    for mutate, msg in ((lambda x: x['evidence'].__setitem__('prompt', 'hi'), 'private'),
                        (lambda x: x['action'].__setitem__('chosen', 'nowhere'), 'candidate'),
                        (lambda x: x['outcome'].update(observed=True, y_success=1, independent=False), 'independent'),
                        (lambda x: x.__setitem__('source_refs', []), 'source ref')):
        bad = json.loads(json.dumps(e))
        mutate(bad)
        with pytest.raises(E.EpisodeInvalid, match=msg):
            E.validate(bad)


def test_dedup_exact_and_semantic(home):
    eps = E.compile_episodes(home)[0]
    e = next(x for x in eps if x['family'] == 'omp_route')
    twin = json.loads(json.dumps(e))
    twin['episode_id'] = 'other-host-copy'
    near = json.loads(json.dumps(e))
    near['episode_id'], near['decided_at'] = 'later-retry', '2026-12-01T00:00:00Z'
    near['hashes'] = E.episode_hashes(near)
    out, rep = E.dedup([e, twin, near])
    assert rep['exact_dropped'] == 1 and rep['semantic_marked'] == 1
    assert {x['episode_id'] for x in out} == {e['episode_id'], 'later-retry'}
    assert next(x for x in out if x['episode_id'] == 'later-retry')['dup_of'] == e['episode_id']


def test_eventlog_refs_and_independent_outcome(home):
    log = EventLog(home / 'memory')
    log.append('work.verification', {'trace_id': 'att-sub1', 'tests_passed': True, 'classification': 'x'},
               source='codex-independent-verifier')
    log.append('context.resolve', {'task_id': 'c1', 'schema': 'x'}, source='harness:omp')
    before = tree_digest(home)
    eps, stats = E.compile_episodes(home)
    assert tree_digest(home) == before  # the canonical ledger is only read
    e = next(x for x in eps if x['source'] == 'receipts.route_worker' and x['day'] == '2026-09-20')
    assert e['outcome']['source'] == 'eventlog.work.verification' and e['outcome']['y_success'] == 1
    assert {r['event_type'] for r in e['event_refs']} == {'work.verification', 'context.resolve'}
    assert stats['eventlog']['events'] == 2 and stats['eventlog']['outcomes_from_eventlog'] >= 1


# ----------------------------------------------------------------------------- split
def test_split_temporal_lineage_contamination(home):
    eps = E.compile_episodes(home)[0]
    ids, man = SP.split(eps, eval_start='2026-10-08', eval_end='2026-10-12', dev_days=7)
    day = {e['episode_id']: e['day'] for e in eps}
    assert all(day[i] < '2026-10-01' for i in ids['train'])
    assert all('2026-10-01' <= day[i] < '2026-10-08' for i in ids['dev'])
    assert all('2026-10-08' <= day[i] < '2026-10-12' for i in ids['eval'])
    assert all(day[i] >= '2026-10-12' for i in ids['recent'])
    assert not any(man['residual_overlap'].values())
    assert man['counts']['eval']['synthetic'] == 0
    # a session spanning train and eval: its train part is quarantined, eval stays date-pure
    extra = json.loads(json.dumps(next(e for e in eps if e['source'] == 'receipts.omp_v1' and e['day'] == '2026-10-11')))
    extra.update(episode_id='early-part', decided_at='2026-09-25T00:00:00Z', day='2026-09-25')
    extra['evidence']['semantic_key'] = 'distinct'
    extra['hashes'] = E.episode_hashes(extra)
    ids2, man2 = SP.split(eps + [extra], eval_start='2026-10-08', eval_end='2026-10-12')
    assert 'early-part' not in ids2['train'] and man2['removed']['lineage_straddle:train->eval'] == 1
    # same request content in train and eval: train copy removed
    leak = json.loads(json.dumps(extra))
    leak.update(episode_id='leak')
    leak['lineage']['group'] = 'fresh-group'
    leak['evidence']['semantic_key'] = next(e for e in eps if e['episode_id'] in ids['eval'])['evidence']['semantic_key']
    leak['hashes'] = E.episode_hashes(leak)
    ids3, man3 = SP.split(eps + [leak], eval_start='2026-10-08', eval_end='2026-10-12')
    assert 'leak' not in ids3['train'] and any(k.startswith('content_contamination:train') for k in man3['removed'])


def test_synthetic_follows_parent_out_of_train(home):
    eps = E.compile_episodes(home)[0]
    syn = next(e for e in eps if e['provenance']['kind'] == 'synthetic')
    ids, _ = SP.split(eps, eval_start='2026-10-08', eval_end='2026-10-12')
    assert syn['episode_id'] in ids['train']
    ids, man = SP.split(eps, eval_start='2026-09-23', eval_end='2026-10-12', dev_days=1)
    assert syn['episode_id'] not in sum(ids.values(), []) and man['removed']['synthetic_parent_held_out'] == 1


def test_eval_lock_is_fixed(tmp_path):
    lock = tmp_path / 'locks' / 'eval_window.json'
    with pytest.raises(SP.LockConflict):
        SP.resolve_lock(lock, None, None)
    first = SP.resolve_lock(lock, '2026-10-08', '2026-10-15')
    assert SP.resolve_lock(lock, None, None) == first
    with pytest.raises(SP.LockConflict, match='locked'):
        SP.resolve_lock(lock, '2026-10-01', '2026-10-15')


# ----------------------------------------------------------------------------- utility + baselines
def test_baselines_and_utility(home):
    eps = E.compile_episodes(home)[0]
    ids, _ = SP.split(eps, eval_start='2026-10-08', eval_end='2026-10-12', dev_days=7)
    by = {e['episode_id']: e for e in eps}
    rep = EV.baseline_report({s: [by[i] for i in xs] for s, xs in ids.items()})
    assert set(rep['splits']['eval']) <= {'worker_route', 'omp_route', 'decision_opportunity'}
    for fams in rep['splits'].values():
        for v in fams.values():
            assert set(v['policies']) == {'historical', 'production', 'keyword', 'majority', 'cheapest'}
            assert v['decision_grade'] is False  # a handful of labels is never a verdict
    m = EV.Model([by[i] for i in ids['train']])
    do = next(e for e in eps if e['source'] == 'state.claude-code.opportunity' and e['day'] == '2026-10-12')
    assert EV.production(do, m) == 'ASK'
    assert EV.utility(do, 'ACT', m)['violation'] is True  # ACT was not legal: authority penalty
    hist = EV.utility(do, 'ASK', m)
    assert hist['matched'] and hist['observed_success'] and hist['success'] == 0.0
    assert EV.cheapest(do, m) in do['candidates']['legal']


# ----------------------------------------------------------------------------- SFT adapter
def test_sft_examples_and_remote_gate(home, tmp_path):
    eps = E.compile_episodes(home)[0]
    ex = SFT.to_examples(eps)
    assert ex and all(x['provenance'] == 'observed' for x in ex)
    assert {x['target'] for x in ex} <= {'ACT', 'cerebras/free-coder'}  # verified successes only
    assert SECRET not in json.dumps(ex)
    out_home = tmp_path / 'out'
    p, body = SFT.plan('tinker', eps, home=out_home, dataset_receipt={}, split_manifest={})
    assert p['remote'] and not p['train_allowed'] and p['reason'] == 'no_owner_approval_file'
    sha = p['examples']['sha256']
    put(out_home / 'config' / 'sft_remote_approval.json', [{'owner_approved': True, 'allow_remote': True,
                                                            'dataset_sha256': sha}])
    p2, _ = SFT.plan('tinker', eps, home=out_home, dataset_receipt={}, split_manifest={})
    assert not p2['train_allowed'] and p2['reason'] == 'INSUFFICIENT_DATA'
    p3, _ = SFT.plan('stub', eps, home=out_home, dataset_receipt={}, split_manifest={})
    assert p3['reason'] == 'INSUFFICIENT_DATA' and not p3['remote']
    with pytest.raises(PermissionError):
        SFT.TinkerBackend().train(tmp_path / 'x', tmp_path)


# ----------------------------------------------------------------------------- CLI
def _worktree_check_below(base: Path):
    # hosts may carry a stray repo above the pytest temp root (e.g. /tmp/.git): only look up to tmp_path
    def check(path):
        p = Path(path).resolve()
        return any((q / '.git').exists() for q in (p, *p.parents) if q == base or base in q.parents)
    return check


def test_cli_end_to_end_read_only(home, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(CLI, 'in_git_worktree', _worktree_check_below(tmp_path))
    out = tmp_path / 'out-home'
    before = tree_digest(home)
    assert CLI._main(['--home', str(out), 'build', '--source-home', str(home), '--host', 'test']) == 0
    assert CLI._main(['--home', str(out), 'split', '--eval-start', '2026-10-08', '--eval-end', '2026-10-12']) == 0
    assert CLI._main(['--home', str(out), 'baseline']) == 0
    assert CLI._main(['--home', str(out), 'sft-plan', '--backend', 'tinker']) == 0
    assert CLI._main(['--home', str(out), 'split', '--eval-start', '2026-10-01', '--eval-end', '2026-10-12']) == 2
    assert tree_digest(home) == before  # sources untouched, nothing created in the source home
    root = out / 'episodes' / 'routing'
    bid = (root / 'LATEST_BUILD').read_text().strip()
    rec = json.loads((root / 'builds' / bid / 'receipt.json').read_text())
    assert rec['schema'] == E.RECEIPT_SCHEMA and rec['sources']['receipts/decisions.jsonl']['sha256']
    assert rec['summary']['episodes'] > 0 and rec['code']['compiler_version'] == E.COMPILER_VERSION
    body = (root / 'builds' / bid / 'episodes.jsonl').read_bytes()
    assert hashlib.sha256(body).hexdigest() == rec['episodes_sha256'] and SECRET.encode() not in body
    sid = (root / 'LATEST_SPLIT').read_text().strip()
    assert (root / 'splits' / sid / 'baseline.json').is_file()
    assert json.loads((root / 'locks' / 'eval_window.json').read_text())['eval_start'] == '2026-10-08'
    assert SECRET not in capsys.readouterr().out


def test_cli_refuses_output_inside_git_worktree(tmp_path, monkeypatch):
    repo = tmp_path / 'repo'
    (repo / '.git').mkdir(parents=True)
    assert CLI.in_git_worktree(repo / 'home' / 'episodes')  # the unpatched rule
    monkeypatch.setattr(CLI, 'in_git_worktree', _worktree_check_below(tmp_path))
    with pytest.raises(SystemExit, match='git worktree'):
        CLI._main(['--home', str(repo / 'home'), 'build', '--source-home', str(tmp_path)])
