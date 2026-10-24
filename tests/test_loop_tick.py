"""`z0int loop tick` (C6 red tests 1-5, 9-15): the scheduled offline learning tick, on synthetic homes only.

Every test runs in an isolated world (HOME, Z0INT_HOME, CLAUDE_CONFIG_DIR, AGENTSVIEW_DATA_DIR under tmp_path) with a
scratch lock file; the learner is a fake ``evolution_lab.verified_loop`` package in its own git repo, so the pin check
and the subprocess are real but nothing is learned.
"""
import fcntl
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_loop_export import verified
from test_loop_tables import opp, put
from z0int import harness_capture as hc
from z0int import paths

REPO = Path(__file__).resolve().parents[1]
STEPS = ['verify', 'import', 'shadow', 'export', 'sufficiency', 'learn', 'promote']
GIT_ENV = {'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_AUTHOR_NAME': 'f', 'GIT_AUTHOR_EMAIL': 'f@x',
           'GIT_COMMITTER_NAME': 'f', 'GIT_COMMITTER_EMAIL': 'f@x'}

FAKE_LEARNER = '''"""Fake evolution_lab.verified_loop: records what it was given, decides FAKE_DECISION."""
import argparse, json, os
from pathlib import Path


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--table', required=True)
    ap.add_argument('--manifest')
    ap.add_argument('--out', required=True)
    ap.add_argument('--md')
    a = ap.parse_args(argv)
    rows = [json.loads(x) for x in Path(a.table).read_text().splitlines() if x.strip()]
    with open(os.environ['FAKE_LEARNER_LOG'], 'a') as fh:
        fh.write(json.dumps({'table': a.table, 'strata': sorted({(r['harness'], r['cohort']) for r in rows}),
                             'rows': len(rows)}) + '\\n')
    decision = os.environ.get('FAKE_DECISION', 'INSUFFICIENT_DATA')
    Path(a.out).write_text(json.dumps({'schema': 'evolution_lab.verified_loop.result.v0',
                                       'prereg': 'docs/prereg/verified-loop-v0.md', 'primary': {'decision': decision}}))
    if a.md:
        Path(a.md).write_text('# fake\\n')
    print('decision=' + decision)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
'''


def tick_mod():
    from z0int import loop_tick
    return loop_tick


@pytest.fixture
def world(tmp_path, monkeypatch):
    w = tmp_path / 'world'
    home = w / 'z0'
    for k, v in {'HOME': w / 'home', 'Z0INT_HOME': home, 'CLAUDE_CONFIG_DIR': w / 'claude',
                 'AGENTSVIEW_DATA_DIR': w / 'av', 'XDG_CACHE_HOME': w / 'cache'}.items():
        monkeypatch.setenv(k, str(v))
    monkeypatch.setenv('PYTHONDONTWRITEBYTECODE', '1')
    (w / 'home').mkdir(parents=True)
    paths.ensure_layout(home)  # a real home already has the standard tree
    lock = tmp_path / 'locks' / 'quiet-lane.lock'
    lock.parent.mkdir()
    lock.touch()
    return SimpleNamespace(root=w, home=home, lock=lock, tmp=tmp_path)


@pytest.fixture
def learner(tmp_path, monkeypatch, world):
    d = tmp_path / 'learner'
    (d / 'evolution_lab').mkdir(parents=True)
    (d / 'evolution_lab' / '__init__.py').write_text('')
    (d / 'evolution_lab' / 'verified_loop.py').write_text(FAKE_LEARNER)
    env = dict(os.environ, **GIT_ENV)
    for args in (['init', '-q'], ['add', '-A'], ['commit', '-qm', 'fake learner']):
        subprocess.run(['git', '-C', str(d), *args], check=True, env=env, capture_output=True)
    sha = subprocess.run(['git', '-C', str(d), 'rev-parse', 'HEAD'], check=True, capture_output=True,
                         text=True).stdout.strip()
    log = tmp_path / 'learner-calls.jsonl'
    monkeypatch.setenv('PYTHONPATH', str(d))  # the tick's learner subprocess only; this process is unchanged
    monkeypatch.setenv('FAKE_LEARNER_LOG', str(log))
    set_config(world.home, learner_sha=sha)
    calls = lambda: [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []  # noqa: E731
    return SimpleNamespace(dir=d, sha=sha, log=log, calls=calls)


def set_config(home, **kw):
    path = home / 'config' / 'loop.json'
    cfg = json.loads(path.read_text()) if path.exists() else {}
    path.write_text(json.dumps(dict(cfg, **kw)))


def turns(home, harness, specs, cohort='interactive'):
    """(session, trace, state, day): an opportunity, an ACT observation and a verified row per turn."""
    for session, trace, state, day in specs:
        rec = opp(harness, session, trace)
        rec['recorded_at'] = f'{day}T10:00:00Z'
        put(home, harness, 'opportunities.jsonl', [rec])
        put(home, harness, 'outcomes.jsonl', [{'schema': hc.schema(harness, 'turn_outcome'), 'session_id': session,
                                               'trace_id': trace, 'asked_user': False, 'tool_calls': 2}])
        if state:
            row = dict(verified(session, trace, state), schema=hc.schema(harness, 'turn_outcome_verified'),
                       harness=harness, cohort=cohort)
            row['turn'] = {'started_at': f'{day}T10:00:05Z'}
            put(home, harness, 'outcomes_verified.jsonl', [row])


def cc_opportunities(home, n=2):
    """Claude Code turns captured by the hooks (labels come from transcripts, which the test may or may not write)."""
    put(home, 'claude-code', 'opportunities.jsonl',
        [dict(opp('claude-code', 'cc-s1', f'p{i}'), schema='z0int.claude_code.opportunity_record.v0') for i in range(n)])


def omp_v1_legacy(home):
    """The OMP v1 spine at its default location ($Z0INT_HOME/receipts): two traces, gold + negative."""
    rec = lambda tr: {'schema': 'z0int.decision_receipt.v1', 'trace_id': tr, 'session_id': 'omp-legacy-1',  # noqa
                      'capability_id': 'route.code', 'provider': 'local_mb', 'route': 'local', 'ts': 1759300000}
    (home / 'receipts' / 'decisions.jsonl').write_text(''.join(json.dumps(rec(f'lt-{i}')) + '\n' for i in range(2)))
    joins = [({'test_pass': True, 'verification_source': 'ci'}, 'gold'), ({'user_correction': True}, 'negative')]
    (home / 'receipts' / 'outcomes.jsonl').write_text(''.join(
        json.dumps({'schema': 'z0int.outcome_join.v1', 'trace_id': f'lt-{i}', 'outcome': oc, 'outcome_tier': t,
                    'receipt': rec(f'lt-{i}')}) + '\n' for i, (oc, t) in enumerate(joins)))


def tick(world, **kw):
    kw.setdefault('lock', world.lock)
    kw.setdefault('lock_wait_s', 5.0)
    kw.setdefault('budget_s', 120.0)
    return tick_mod().run_tick(world.home, **kw)


def snapshot(root, skip=()):
    out = {}
    for p in sorted(root.rglob('*')):
        rel = p.relative_to(root)
        if any(rel.parts[:len(s)] == s for s in skip):
            continue
        out[str(rel)] = p.read_bytes() if p.is_file() else 'dir'
    return out


def line_counts(home):
    return {str(p.relative_to(home)): len(p.read_text().splitlines()) for p in sorted((home / 'state').rglob('*.jsonl'))}


def summary_file(home):
    (path,) = sorted((home / 'loop' / 'reports').glob('*/summary.json'))
    return json.loads(path.read_text())


def lock_is_free(lock):
    with open(lock) as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        fcntl.flock(fh, fcntl.LOCK_UN)
        return True


# ----------------------------------------------------------------------------- 1. steps in order, idempotent
def test_tick_runs_the_steps_in_order_writes_the_summary_and_a_second_tick_adds_nothing(world, learner):
    turns(world.home, 'hermes', [('hs-1', 't1', 'verified_success', '2026-09-29'),
                                 ('hs-2', 't2', 'verified_failure', '2026-09-30')])
    cc_opportunities(world.home)
    omp_v1_legacy(world.home)
    first = tick(world)
    assert [s['name'] for s in first['steps']] == STEPS
    assert [s['status'] for s in first['steps']] == ['done'] * len(STEPS)
    starts = [s['started_at'] for s in first['steps']]
    assert starts == sorted(starts)
    assert summary_file(world.home) == first
    assert first['rows_added']['shadow_decisions'] > 0 and first['rows_added']['imported'] == 2
    counts = line_counts(world.home)
    index = json.loads((world.home / 'loop' / 'tables' / 'manifest.json').read_text())
    manifests = {p.name: json.loads(p.read_text())['manifest_sha256']
                 for p in (world.home / 'loop' / 'tables').rglob('*.manifest.json')}
    second = tick(world)
    assert second['rows_added'] == dict.fromkeys(first['rows_added'], 0)
    assert line_counts(world.home) == counts
    assert json.loads((world.home / 'loop' / 'tables' / 'manifest.json').read_text())['manifest_sha256'] == \
        index['manifest_sha256'] == second['tables']['manifest_sha256']
    assert {p.name: json.loads(p.read_text())['manifest_sha256']
            for p in (world.home / 'loop' / 'tables').rglob('*.manifest.json')} == manifests


def test_cli_loop_tick_routes_to_the_tick(world, learner, capsys):
    from z0int import loop_export as le
    cc_opportunities(world.home)
    code = le._main(['tick', '--z0int-home', str(world.home), '--lock', str(world.lock), '--lock-wait-s', '5',
                     '--budget-s', '120'])
    assert code in (0, 3)
    assert summary_file(world.home)['steps'][0]['name'] == 'verify'


# ----------------------------------------------------------------------------- 2. never promotes
def test_a_shadow_candidate_writes_only_a_promotion_request(world, learner, monkeypatch):
    monkeypatch.setenv('FAKE_DECISION', 'SHADOW_CANDIDATE')
    turns(world.home, 'claude-code', [('cc-s1', 'p1', None, '2026-09-30')])
    skip = (('z0', 'state'), ('z0', 'loop'))
    before = snapshot(world.root, skip)
    rep = tick(world)
    assert snapshot(world.root, skip) == before
    (req,) = sorted((world.home / 'loop' / 'requests').iterdir())
    body = json.loads(req.read_text())
    assert body['schema'] == 'z0int.loop.promotion_request.v0'
    assert body['lifecycle']['stages'] == ['sanity', 'replay', 'shadow', 'promoted']
    assert body['lifecycle']['requested_stage'] == 'shadow' and body['approved'] is False
    assert body['stratum'] == {'harness': 'claude-code', 'cohort': 'unknown'}
    assert len(body['candidate_sha256']) == 64 and body['contract_fields_to_fill']
    assert [r['decision'] for r in rep['results'].values()] == ['SHADOW_CANDIDATE']
    assert rep['promotion_requests'] == [str(req.relative_to(world.home))]
    tick(world)  # the same candidate again: no second request
    assert len(list((world.home / 'loop' / 'requests').iterdir())) == 1


# ----------------------------------------------------------------------------- 3. no network
def test_tick_never_opens_a_network_connection(world, learner, monkeypatch):
    attempts = []

    def refuse(self, address, *a):
        attempts.append(address)
        raise ConnectionRefusedError('socket guard')
    monkeypatch.setattr(socket.socket, 'connect', refuse)
    monkeypatch.setattr(socket.socket, 'connect_ex', refuse)
    monkeypatch.setattr(socket, 'create_connection', lambda *a, **k: refuse(None, a[0]))
    turns(world.home, 'hermes', [('hs-1', 't1', 'verified_success', '2026-09-30')])
    cc_opportunities(world.home)
    rep = tick(world)
    assert attempts == [] and [s['status'] for s in rep['steps']] == ['done'] * len(STEPS)


# ----------------------------------------------------------------------------- 4. sufficiency + prereg
def test_a_thin_stratum_is_insufficient_with_a_projection_and_no_prereg_means_no_learner(world, learner):
    turns(world.home, 'hermes', [(f'hs-{i}', f't{i}', 'verified_failure' if i % 3 == 0 else 'verified_success',
                                  '2026-09-28' if i < 4 else '2026-09-30') for i in range(8)])
    cc_opportunities(world.home)
    rep = tick(world)
    st = rep['strata']['hermes/interactive']
    assert st['sufficiency']['decision'] == 'INSUFFICIENT_DATA'
    assert st['sufficiency']['observed'] == {'rows': 8, 'not_success': 3, 'groups': 8}
    checks = st['sufficiency']['checks']
    assert sorted(checks) == ['groups>=10', 'k==5_and_every_test_fold_has_both_classes', 'not_success>=30', 'rows>=300']
    assert not checks['rows>=300'] and not checks['not_success>=30'] and not checks['groups>=10']
    proj = st['sufficiency']['projection']
    assert proj['days_observed'] == 3 and proj['binding'] in ('rows>=300', 'not_success>=30', 'groups>=10')
    assert proj['days_to_sufficiency'] == pytest.approx(max((300 - 8) / (8 / 3), (30 - 3) / (3 / 3), (10 - 8) / (8 / 3)))
    assert st['learn'] == 'prereg_missing'
    assert all(c['strata'] != [['hermes', 'interactive']] for c in learner.calls())
    assert any(c['strata'][0][0] == 'claude-code' for c in learner.calls())  # the covered stratum did run


# ----------------------------------------------------------------------------- 5. never pooled, legacy never learned
def test_strata_are_learned_one_harness_x_cohort_at_a_time_and_legacy_never(world, learner):
    set_config(world.home, preregs={'claude-code': 'verified-loop-v0', 'omp': 'test-prereg'})
    turns(world.home, 'omp', [('om-1', 't1', 'verified_success', '2026-09-30')])
    turns(world.home, 'omp', [('om-2', 't2', 'verified_failure', '2026-09-30')], cohort='agent')
    omp_v1_legacy(world.home)
    cc_opportunities(world.home)
    rep = tick(world)
    calls = learner.calls()
    assert all(len(c['strata']) == 1 for c in calls)
    learned = sorted(tuple(c['strata'][0]) for c in calls)
    assert learned == [('claude-code', 'unknown'), ('omp', 'agent'), ('omp', 'interactive')]
    assert rep['strata']['omp/legacy']['learn'] == 'not_learnable'
    assert rep['strata']['omp/legacy']['rows'] == 2


# ----------------------------------------------------------------------------- 9. lock
def wait_held(lock, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        with open(lock) as fh:
            try:
                fcntl.flock(fh, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                return
        time.sleep(0.05)
    raise AssertionError('holder never took the lock')


@pytest.mark.skipif(not shutil.which('flock'), reason='util-linux flock not installed')
def test_an_exclusive_window_makes_the_tick_wait_then_skip_with_a_receipt(world, learner):
    from z0int import loop_export as le
    cc_opportunities(world.home)
    holder = subprocess.Popen(['flock', '-x', str(world.lock), 'sleep', '30'])
    try:
        wait_held(world.lock)
        t0 = time.monotonic()
        code = le._main(['tick', '--z0int-home', str(world.home), '--lock', str(world.lock), '--lock-wait-s', '1',
                         '--budget-s', '60'])
        waited = time.monotonic() - t0
    finally:
        holder.kill()
        holder.wait()
    assert code == 0 and waited >= 1.0
    rep = summary_file(world.home)
    assert rep['status'] == 'skipped_exclusive_window' and rep['steps'] == []
    assert not (hc.state_dir('claude-code', world.home) / 'shadow_decisions.jsonl').exists()


def test_a_free_lock_is_held_shared_for_the_whole_tick_and_released(world, learner, monkeypatch):
    from z0int import shadow_slot
    cc_opportunities(world.home)
    seen = {}
    real = shadow_slot.replay

    def probe(*a, **k):
        with open(world.lock) as fh:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                seen['exclusive'] = 'granted'
            except BlockingIOError:
                seen['exclusive'] = 'blocked'
        with open(world.lock) as fh:
            fcntl.flock(fh, fcntl.LOCK_SH | fcntl.LOCK_NB)  # raises if the tick held it exclusively
            seen['shared'] = 'granted'
        return real(*a, **k)
    monkeypatch.setattr(shadow_slot, 'replay', probe)
    rep = tick(world)
    assert seen == {'exclusive': 'blocked', 'shared': 'granted'}
    assert rep['lock']['mode'] == 'flock(2) LOCK_SH' and rep['status'] != 'skipped_exclusive_window'
    assert lock_is_free(world.lock)


def test_a_posix_lockf_holder_does_not_block_the_tick(world, learner):
    cc_opportunities(world.home)
    code = ('import fcntl, sys, time\nf = open(sys.argv[1], "r+")\nfcntl.lockf(f, fcntl.LOCK_EX)\n'
            'print("held", flush=True)\ntime.sleep(30)\n')
    holder = subprocess.Popen([sys.executable, '-c', code, str(world.lock)], stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == 'held'
        rep = tick(world, lock_wait_s=1.0)
    finally:
        holder.kill()
        holder.wait()
    assert rep['status'] != 'skipped_exclusive_window' and [s['name'] for s in rep['steps']] == STEPS


# ----------------------------------------------------------------------------- 10. budget
def test_an_exhausted_budget_stops_releases_the_lock_and_is_partial(world, learner, monkeypatch):
    from z0int import shadow_slot
    cc_opportunities(world.home)
    real = shadow_slot.replay

    def slow(*a, **k):
        time.sleep(2.5)
        return real(*a, **k)
    monkeypatch.setattr(shadow_slot, 'replay', slow)
    rep = tick(world, budget_s=2.0)
    assert rep['status'] == 'partial_measurement' and rep['exit'] != 0
    status = {s['name']: s['status'] for s in rep['steps']}
    assert status['shadow'] == 'partial'  # the slot itself stops at the deadline; the rest waits for the next tick
    assert [status[n] for n in STEPS[3:]] == ['skipped_budget'] * 4
    assert lock_is_free(world.lock)
    assert summary_file(world.home)['status'] == 'partial_measurement'


def test_the_verify_step_stops_between_harnesses_at_the_deadline(world, learner, monkeypatch):
    """verify is bounded too: a slow label source holds the lock for at most one harness past the budget."""
    from z0int import outcome_verifier as ov
    for h in ('codex', 'hermes', 'omp'):
        turns(world.home, h, [(f'{h}-s', 't1', None, '2026-09-30')])
    real, seen = ov.verify_harness, []

    def slow(h, **k):
        seen.append(h)
        time.sleep(1.5)
        return real(h, **k)
    monkeypatch.setattr(ov, 'verify_harness', slow)
    rep = tick(world, budget_s=1.0)
    status = {s['name']: s['status'] for s in rep['steps']}
    assert status['verify'] == 'partial' and len(seen) == 1
    assert [status[n] for n in STEPS[1:]] == ['skipped_budget'] * 6
    assert rep['status'] == 'partial_measurement' and rep['tokenomics']['lock_held_s'] < 1.0 + 1.5 + 1.0
    assert lock_is_free(world.lock)


def test_the_import_step_stops_between_legacy_sources_at_the_deadline(world, learner, monkeypatch):
    from z0int import legacy_import as li
    omp_v1_legacy(world.home)
    (world.home / 'shadow').mkdir(exist_ok=True)
    (world.home / 'shadow' / 'cognition-shadow.jsonl').write_text('')
    real, seen = li.import_omp_v1, []

    def slow(*a, **k):
        seen.append('omp-v1')
        time.sleep(1.5)
        return real(*a, **k)
    monkeypatch.setattr(li, 'import_omp_v1', slow)
    monkeypatch.setattr(li, 'import_cognition_shadow', lambda *a, **k: seen.append('cognition') or {})
    rep = tick(world, budget_s=1.0)
    status = {s['name']: s['status'] for s in rep['steps']}
    assert status['import'] == 'partial' and seen == ['omp-v1']
    assert rep['status'] == 'partial_measurement'


# ----------------------------------------------------------------------------- sufficiency thresholds: the learner's
def repin_learner(world, learner, consts):
    """Commit a learner version that declares its own sufficiency constants and pin the tick to it."""
    path = learner.dir / 'evolution_lab' / 'verified_loop.py'
    path.write_text(consts + '\n' + FAKE_LEARNER)
    env = dict(os.environ, **GIT_ENV)
    for args in (['add', '-A'], ['commit', '-qm', 'learner constants']):
        subprocess.run(['git', '-C', str(learner.dir), *args], check=True, env=env, capture_output=True)
    sha = subprocess.run(['git', '-C', str(learner.dir), 'rev-parse', 'HEAD'], check=True, capture_output=True,
                         text=True).stdout.strip()
    set_config(world.home, learner_sha=sha)


def test_sufficiency_uses_the_pinned_learners_thresholds_and_flags_a_mismatch(world, learner):
    repin_learner(world, learner, 'MIN_ROWS = 2\nMIN_NEG = 1\nMIN_GROUPS = 2\nK = 2')
    cc_opportunities(world.home)
    turns(world.home, 'hermes', [('hs-1', 't1', 'verified_success', '2026-09-30')])
    rep = tick(world)
    assert rep['learner']['status'] == 'ok'
    assert rep['learner']['thresholds'] == {'MIN_ROWS': 2, 'MIN_NEG': 1, 'MIN_GROUPS': 2, 'K': 2}
    assert rep['learner']['threshold_mismatch'] == {'MIN_ROWS': [300, 2], 'MIN_NEG': [30, 1], 'MIN_GROUPS': [10, 2],
                                                    'K': [5, 2]}
    for s in rep['strata'].values():
        assert s['sufficiency']['thresholds'] == rep['learner']['thresholds']
        assert s['sufficiency']['thresholds_source'] == 'learner'


def test_a_learner_with_the_preregistered_thresholds_reports_no_mismatch(world, learner):
    repin_learner(world, learner, 'MIN_ROWS = 300\nMIN_NEG = 30\nMIN_GROUPS = 10\nK = 5')
    cc_opportunities(world.home)
    rep = tick(world)
    assert rep['learner']['thresholds'] == {'MIN_ROWS': 300, 'MIN_NEG': 30, 'MIN_GROUPS': 10, 'K': 5}
    assert rep['learner']['threshold_mismatch'] == {}
    assert rep['strata']['claude-code/unknown']['sufficiency']['thresholds_source'] == 'learner'


def test_cli_help_lists_the_tick_under_loop():
    from z0int import cli
    sub = next(a for a in cli.build_parser()._actions if a.dest == 'cmd')
    (loop,) = [c for c in sub._choices_actions if c.dest == 'loop']
    assert 'tick' in loop.help and 'merge' in loop.help


# ----------------------------------------------------------------------------- 11. report
def test_report_counts_per_harness_discrimination_vs_base_rate_and_the_cost_event(world, learner):
    turns(world.home, 'hermes', [('hs-1', 't1', 'verified_success', '2026-09-30'),
                                 ('hs-2', 't2', 'verified_failure', '2026-09-30'),
                                 ('hs-3', 't3', 'verified_success', '2026-09-30'),
                                 ('hs-4', 't4', 'unverified', '2026-09-30')])
    rep = tick(world)
    h = rep['harnesses']['hermes']
    assert (h['captured'], h['verified'], h['labelled']) == (4, 4, 3)
    assert h['join_rate'] is None  # no AgentsView index in this world: nothing joined, nothing claimed
    assert h['drops'] == 0 and h['failures'] == {'reader_unavailable': 1}
    d = rep['strata']['hermes/interactive']['discrimination']
    assert d['predictor'] == 'deterministic_gate' and d['n'] == 3
    assert d['base_rate'] == pytest.approx(2 / 3)
    assert d['base_rate_brier'] == pytest.approx((2 / 3) * (1 / 3))
    assert d['brier'] == pytest.approx(1 / 3)  # the gate ACTs on all three: p=1 for each
    assert d['auroc'] == pytest.approx(0.5)
    ev = rep['tokenomics']
    assert ev['schema'] == 'z0int.loop_tick.cost.v0' and ev['wall_s'] >= 0 and ev['cost_usd'] == 0.0
    lines = (world.home / 'loop' / 'tokenomics' / 'events.jsonl').read_text().splitlines()
    assert json.loads(lines[-1]) == ev


# ----------------------------------------------------------------------------- 12. environment
def test_claude_code_without_a_label_source_is_degraded_never_success(world, learner, monkeypatch):
    monkeypatch.delenv('CLAUDE_CONFIG_DIR')
    cc_opportunities(world.home)
    rep = tick(world)
    assert rep['status'] == 'degraded' and 'label_source_empty' in rep['degraded_by']
    assert rep['harnesses']['claude-code']['failures'] == {'label_source_empty': 1}
    assert rep['exit'] != 0


def test_claude_code_with_its_transcripts_is_success(world, learner):
    cc_opportunities(world.home)
    proj = world.root / 'claude' / 'projects' / '-work'
    proj.mkdir(parents=True)
    (proj / 'cc-s1.jsonl').write_text(json.dumps({'type': 'summary'}) + '\n')
    rep = tick(world)
    assert rep['status'] == 'success' and rep['degraded_by'] == [] and rep['exit'] == 0


# ----------------------------------------------------------------------------- 13. learner pin
def test_learner_not_importable_is_learner_missing(world, learner, monkeypatch):
    monkeypatch.delenv('PYTHONPATH')
    cc_opportunities(world.home)
    rep = tick(world)
    assert rep['learner']['status'] == 'learner_missing' and rep['learner']['reason'] == 'not_importable'
    assert {s['learn'] for s in rep['strata'].values()} == {'learner_missing'}
    assert learner.calls() == [] and 'learner_missing' in rep['degraded_by']


def test_learner_sha_differing_from_the_pin_is_learner_missing(world, learner):
    set_config(world.home, learner_sha='deadbeef')
    cc_opportunities(world.home)
    rep = tick(world)
    assert rep['learner'] == {'status': 'learner_missing', 'reason': 'sha_mismatch', 'pinned': 'deadbeef',
                              'found': learner.sha}
    assert learner.calls() == []


def test_learner_at_the_pinned_sha_runs(world, learner):
    set_config(world.home, learner_sha=learner.sha[:7])
    cc_opportunities(world.home)
    rep = tick(world)
    assert rep['learner']['status'] == 'ok' and len(learner.calls()) == 1
    assert rep['strata']['claude-code/unknown']['learn'] == 'INSUFFICIENT_DATA'


# ----------------------------------------------------------------------------- 14. no learned artifacts in git
def test_an_out_root_inside_a_git_worktree_is_refused_before_anything_runs(world, learner):
    from z0int import loop_export as le
    repo = world.tmp / 'repo'
    repo.mkdir()
    subprocess.run(['git', '-C', str(repo), 'init', '-q'], check=True, env=dict(os.environ, **GIT_ENV))
    cc_opportunities(world.home)
    before = snapshot(world.root)
    code = le._main(['tick', '--z0int-home', str(world.home), '--lock', str(world.lock), '--out-root',
                     str(repo / 'nested' / 'out')])
    assert code == 2
    assert not (repo / 'nested').exists() and snapshot(world.root) == before


def test_no_tracked_weights_under_src_or_deploy():
    out = subprocess.run(['git', '-C', str(REPO), 'ls-files', '--', 'src', 'deploy'], capture_output=True, text=True,
                         check=True).stdout.split()
    assert out and not [p for p in out if p.endswith(('.pt', '.safetensors', '.pkl', '.joblib', '.npz'))]


# ----------------------------------------------------------------------------- 15. unit files
UNITS = REPO / 'deploy' / 'systemd'


def unit(name):
    """key -> [values] (systemd allows repeated keys such as Environment=)."""
    out = {}
    for line in (UNITS / name).read_text().splitlines():
        if '=' in line and not line.lstrip().startswith(('#', ';', '[')):
            k, v = line.split('=', 1)
            out.setdefault(k.strip(), []).append(v.strip())
    return out


def test_tick_unit_runs_the_in_process_lock_tick_at_idle_priority_with_its_environment():
    svc = unit('z0int-loop-tick.service')
    (start,) = svc['ExecStart']
    assert ' loop tick ' in start and '--lock /mnt/zer0models/cua-lane-tmp/locks/quiet-lane.lock' in start
    assert 'flock' not in start.split(' loop tick ')[0]  # no wrapper: the tick takes the lock itself
    assert '--lock-wait-s 1800' in start and '--budget-s 600' in start
    assert svc['Type'] == ['oneshot'] and svc['Nice'] == ['19'] and svc['IOSchedulingClass'] == ['idle']
    env = dict(e.split('=', 1) for e in svc['Environment'])
    assert sorted(env) == ['AGENTSVIEW_DATA_DIR', 'CLAUDE_CONFIG_DIR', 'Z0INT_HOME']
    assert not [k for k in svc if 'GPU' in k.upper() or 'CUDA' in k.upper()]
    timer = unit('z0int-loop-tick.timer')
    assert timer['OnCalendar'] == ['hourly'] and timer['Persistent'] == ['true']


def test_agentsview_sync_unit_is_a_bounded_shared_lock_oneshot_not_a_daemon():
    svc = unit('agentsview-sync.service')
    (start,) = svc['ExecStart']
    assert start.startswith('/usr/bin/flock -s -w 60 -E 0 /mnt/zer0models/cua-lane-tmp/locks/quiet-lane.lock ')
    assert start.endswith('agentsview sync') and 'serve' not in start and 'daemon' not in start
    assert svc['Type'] == ['oneshot'] and svc['Nice'] == ['19'] and svc['IOSchedulingClass'] == ['idle']
    env = dict(e.split('=', 1) for e in svc['Environment'])
    assert 'AGENTSVIEW_DATA_DIR' in env
    # v0.44: plain `agentsview sync` leaves a background serve daemon running unless this is set (ops README: Required)
    assert env.get('AGENTSVIEW_NO_DAEMON') == '1'
    assert svc['TimeoutStartSec'] == ['50min']  # the owner-approved ops unit's bound on one sync
    assert 'v74 -> v113' not in (UNITS / 'agentsview-sync.service').read_text()  # the resync is done (v0.44 live)
    timer = unit('agentsview-sync.timer')
    assert timer['OnCalendar'] and timer['RandomizedDelaySec'] == ['2min']


@pytest.mark.skipif(not shutil.which('systemd-analyze'), reason='systemd-analyze not installed')
def test_units_pass_systemd_analyze_verify_with_a_stub_exec(tmp_path):
    stub = tmp_path / 'stub'
    stub.write_text('#!/bin/sh\nexit 0\n')
    stub.chmod(0o755)
    names = ['z0int-loop-tick.service', 'z0int-loop-tick.timer', 'agentsview-sync.service', 'agentsview-sync.timer']
    for name in names:
        lines = []
        for line in (UNITS / name).read_text().splitlines():
            if line.startswith('ExecStart='):
                line = f'ExecStart={stub} ' + line.split('=', 1)[1]  # same arguments, an executable that exists
            lines.append(line)
        (tmp_path / name).write_text('\n'.join(lines) + '\n')
    (tmp_path / 'rt').mkdir()  # user-manager mode needs a runtime dir; a scratch one, never the session's
    p = subprocess.run(['systemd-analyze', '--user', 'verify', *(str(tmp_path / n) for n in names)],
                       capture_output=True, text=True, env=dict(os.environ, XDG_RUNTIME_DIR=str(tmp_path / 'rt')))
    ours = [x for x in (p.stderr + p.stdout).splitlines() if any(n.split('.')[0] in x for n in names)]
    assert p.returncode == 0 and ours == [], p.stderr + p.stdout  # warnings about our units fail; host units' do not
