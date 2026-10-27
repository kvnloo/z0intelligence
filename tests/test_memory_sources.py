"""`z0int memory sources`: harness history must reach every memory index, discovered, not hand-listed."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from z0int.memory import sources

NOW = 1_800_000_000.0


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')


def av_db(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path)
    c.execute('create table sessions (id text primary key, project text, agent text, started_at text, '
              'file_path text, user_message_count integer)')
    for i, (agent, fp, age_h, project) in enumerate(rows):
        c.execute('insert into sessions values (?,?,?,?,?,?)', (f's{i}', project, agent, iso(NOW - age_h * 3600), fp, 3))
    c.execute('pragma user_version = 113')
    c.commit()
    c.close()


def touch(path, age_h):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{}\n')
    t = NOW - age_h * 3600
    os.utime(path, (t, t))
    return path


class FakeCtx:
    """Stands in for `ctx sources --all --format json` and `ctx sources add/remove`."""

    def __init__(self, roots):
        self.roots = list(roots)  # [{provider, path, status}]
        self.failures = []  # `ctx status` refresh.diagnostics.source_failures
        self.calls = []

    def run(self, cmd, **kw):
        self.calls.append(cmd)
        if cmd[1:3] == ['sources', 'add']:
            self.roots.append({'provider': cmd[cmd.index('--provider') + 1], 'path': cmd[cmd.index('--root') + 1],
                               'status': 'available'})
        return subprocess.CompletedProcess(cmd, 0, 'ok', '')


@pytest.fixture
def world(tmp_path, monkeypatch):
    omp = tmp_path / 'omp' / 'agent' / 'sessions'
    claude = tmp_path / 'claude'
    f_omp = touch(omp / '--proj--' / 'a.jsonl', 1)
    f_claude = touch(claude / 'projects' / '-proj' / 'b.jsonl', 2)
    touch(claude / 'statsig' / 'noise.json', 0)  # home noise must not count as session activity
    av = tmp_path / 'av'
    av_db(av / 'sessions.db', [('omp', str(f_omp), 1.5, 'zer0'), ('claude', str(f_claude), 2.5, 'quackles')])
    policy = tmp_path / 'policy.json'
    policy.write_text(json.dumps({'principals': {'operator': [{'source': 'agentsview', 'scope': 'zer0'}]}}))
    ctx_root = tmp_path / 'ctx'
    ctx_root.mkdir()
    monkeypatch.setenv('CTX_DATA_ROOT', str(ctx_root))
    monkeypatch.setattr(sources, 'HARNESSES', ('omp', 'claude-code'))
    return dict(av=av, omp=omp, claude=claude, policy=policy, ctx_root=ctx_root)


def run(world, fake, apply=False):
    original, original_f = sources.ctx_sources, sources.ctx_failures
    sources.ctx_sources = lambda *_a, **_k: list(fake.roots)
    sources.ctx_failures = lambda *_a, **_k: list(fake.failures)
    try:
        return sources.reconcile(av_dir=world['av'], ctx_bin='ctx', policy_path=world['policy'], apply=apply,
                                 now=NOW, runner=fake.run)
    finally:
        sources.ctx_sources, sources.ctx_failures = original, original_f


def test_an_active_harness_root_missing_from_ctx_fails_and_apply_registers_its_real_root(world):
    fake = FakeCtx([{'provider': 'claude', 'path': str(world['claude']), 'status': 'available'}])
    before = run(world, fake)
    assert not before['ok']
    assert any('not indexed by ctx (pi)' in p for p in before['problems'])
    after = run(world, fake, apply=True)
    assert after['ok'], after['unresolved']
    add = [c for c in fake.calls if c[1:3] == ['sources', 'add']]
    assert add == [['ctx', 'sources', 'add', '--provider', 'pi', '--root', os.path.realpath(world['omp']),
                    '--quiet', add[0][-1]]]
    assert add[0][-1].startswith('auto-omp-')


def test_claude_and_codex_roots_are_provider_homes_not_their_session_dirs():
    assert sources.CTX_RULES['claude'][1]('/h/.claude/projects/-p/x.jsonl') == '/h/.claude'
    assert sources.CTX_RULES['codex'][1]('/h/codex/sessions/2026/10/25/r.jsonl') == '/h/codex'
    assert sources.CTX_RULES['omp'][1]('/h/.omp/agent/sessions/--p--/x.jsonl') == '/h/.omp/agent/sessions'
    assert sources.CTX_RULES['opencode'][1]('/h/opencode.db#ses_1') == '/h/opencode.db'


def test_a_registered_root_ctx_reports_missing_is_a_wrong_shape_failure(world):
    fake = FakeCtx([{'provider': 'claude', 'path': str(world['claude']), 'status': 'available'},
                    {'provider': 'pi', 'path': str(world['omp']), 'status': 'available'},
                    {'provider': 'claude', 'path': str(world['claude'] / 'projects' / 'projects'),
                     'status': 'missing'}])
    out = run(world, fake)
    assert not out['ok']
    assert any('registered but missing' in p for p in out['problems'])


def test_agentsview_behind_disk_fails_and_home_noise_does_not_count(world):
    touch(world['omp'] / '--proj--' / 'new.jsonl', 0)  # 1.5 h after AgentsView's newest: fine
    fake = FakeCtx([{'provider': 'claude', 'path': str(world['claude']), 'status': 'available'},
                    {'provider': 'pi', 'path': str(world['omp']), 'status': 'available'}])
    assert run(world, fake)['ok']
    touch(world['omp'] / '--proj--' / 'a.jsonl', -8)  # disk 9.5 h ahead of the index
    out = run(world, fake)
    assert any('AgentsView 10 h behind disk' in p or 'AgentsView 9 h behind disk' in p for p in out['problems'])
    claude = next(r for r in out['roots'] if r['agent'] == 'claude')
    assert claude['av_lag_hours'] == 0.5  # projects/ file only; statsig/noise.json (age 0) would read 2.5


def test_ungranted_projects_are_reported_not_granted(world):
    fake = FakeCtx([{'provider': 'claude', 'path': str(world['claude']), 'status': 'available'},
                    {'provider': 'pi', 'path': str(world['omp']), 'status': 'available'}])
    out = run(world, fake, apply=True)
    assert [g['project'] for g in out['ungranted_projects']] == ['quackles']
    assert out['warnings'] and out['ok']
    assert json.loads(world['policy'].read_text())['principals']['operator'] == [
        {'source': 'agentsview', 'scope': 'zer0'}]


def test_a_required_harness_agentsview_never_saw_is_found_on_disk(world, tmp_path, monkeypatch):
    dsh = tmp_path / 'dsh' / 'sessions'
    touch(dsh / '--p--' / 's.jsonl', 1)
    monkeypatch.setattr(sources, 'HARNESSES', ('omp', 'claude-code', 'dsh'))
    monkeypatch.setitem(sources.DEFAULT_ROOTS, 'deepseek-harness', [str(dsh)])
    fake = FakeCtx([{'provider': 'claude', 'path': str(world['claude']), 'status': 'available'},
                    {'provider': 'pi', 'path': str(world['omp']), 'status': 'available'}])
    out = run(world, fake)
    assert any(p.startswith('deepseek-harness') and 'not in AgentsView' in p for p in out['problems'])


def test_a_provider_home_listed_as_its_expanded_children_counts_as_indexed(world):
    """ctx reports a Claude home root as <home>/projects and a codex home as sessions/, archived_sessions/ ..."""
    fake = FakeCtx([{'provider': 'claude', 'path': str(world['claude'] / 'projects'), 'status': 'available'},
                    {'provider': 'claude', 'path': str(world['claude'] / 'archived'), 'status': 'missing'},
                    {'provider': 'pi', 'path': str(world['omp']), 'status': 'available'}])
    out = run(world, fake, apply=True)
    assert out['ok'], out['unresolved']
    assert not [c for c in fake.calls if c[1:3] == ['sources', 'add']]


def test_hermes_lag_ignores_empty_and_automated_sessions(tmp_path):
    db = tmp_path / 'state.db'
    c = sqlite3.connect(db)
    c.execute('create table sessions (id text, source text, started_at real)')
    c.execute('create table messages (id integer primary key, session_id text)')
    c.executemany('insert into sessions values (?,?,?)', [('a', 'tui', 100.0), ('b', 'acp', 500.0), ('c', 'cron', 900.0)])
    c.executemany('insert into messages (session_id) values (?)', [('a',), ('c',)])
    c.commit()
    c.close()
    assert sources.newest_source('hermes', db) == 100.0  # b is empty, c is cron


def test_registered_omp_root_that_ctx_cannot_import_fails_and_apply_mirrors_only_transcripts(world):
    omp = world['omp']
    session = omp / '--proj--' / 'a.jsonl'
    session.write_text('{"type":"title","title":""}\n{"type":"session","id":"s1"}\n{"type":"message"}\n')
    t = NOW - 3600
    os.utime(session, (t, t))
    sub = omp / '--proj--' / 'a' / 'Worker.jsonl'
    sub.parent.mkdir(parents=True)
    sub.write_text(session.read_text())
    os.utime(sub, (t, t))
    artefact = omp / '--proj--' / 'a' / 'local' / 'findings.jsonl'
    artefact.parent.mkdir(parents=True)
    artefact.write_text('{"arm":{"backend":"cpu"}}\n')
    root = os.path.realpath(omp)
    (world['ctx_root'] / 'config.toml').write_text(f'[sources.roots.auto-omp]\nprovider = "pi"\npath = "{root}"\n')
    fake = FakeCtx([{'provider': 'claude', 'path': str(world['claude']), 'status': 'available'},
                    {'provider': 'pi', 'path': root, 'status': 'available'}])
    fake.failures = [{'provider': 'pi', 'selector': root,
                      'detail': 'invalid capture payload: Pi source has no valid session header'}]
    before = run(world, fake)
    assert not before['ok'] and any('ctx import fails' in p for p in before['problems'])
    after = run(world, fake, apply=True)
    assert after['ok'], after['unresolved']
    mirror = sources.pi_mirror_path(root)
    assert ['ctx', 'sources', 'add', '--provider', 'pi', '--root', str(mirror), '--quiet', '--replace',
            'auto-omp'] in fake.calls
    files = sorted(str(p.relative_to(mirror)) for p in mirror.rglob('*.jsonl'))
    # Worker.jsonl repeats session id s1 (OMP copies subagent transcripts into forks): ctx gets one copy.
    assert len(files) == 1 and files[0] in ('--proj--/a.jsonl', '--proj--/a/Worker.jsonl')
    sub.write_text(sub.read_text() + '{"type":"message","longer":true}\n')
    sources.sync_pi_mirror(root, mirror)
    assert sorted(str(p.relative_to(mirror)) for p in mirror.rglob('*.jsonl')) == ['--proj--/a/Worker.jsonl']
    sub.unlink()
    sources.sync_pi_mirror(root, mirror)
    assert (mirror / '--proj--/a.jsonl').stat().st_ino == session.stat().st_ino  # hard link: appends visible
    session.unlink()
    assert sources.sync_pi_mirror(root, mirror)['removed'] == 1
    assert not (mirror / '--proj--/a.jsonl').exists()
