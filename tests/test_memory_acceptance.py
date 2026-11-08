"""Memory seam diagnostics must not claim native model visibility or answer quality."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from memory_fixture import SECRETS, SESSIONS, build_av_db
from z0int.memory import acceptance, seam

ROOT = Path(__file__).resolve().parents[1]
PINNED = ROOT / 'tests' / 'fixtures' / 'z0evals-fb14919'
COHORT = json.loads((PINNED / 'cohort.json').read_text())
CWD = '/w/z0'  # the eval's task cwd: the seeded cohort is project z0


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv('Z0INT_HOME', str(tmp_path / 'z0'))
    monkeypatch.setenv('AGENTSVIEW_DATA_DIR', str(tmp_path / 'av'))
    monkeypatch.setenv('PYTHONPATH', os.pathsep.join(p for p in (str(ROOT / 'src'), os.environ.get('PYTHONPATH', ''))
                                                     if p))
    monkeypatch.delenv('Z0INT_MEMORY_INJECT', raising=False)
    acceptance.seed_cohort(COHORT, av_db=tmp_path / 'av' / 'sessions.db', ledger_root=tmp_path / 'z0' / 'memory')
    return tmp_path


def test_supported_retrieval_remains_separate_from_model_visibility(env):
    rows = {r['question_id']: r for r in acceptance.run('hermes', COHORT, revision='0123abcd', cwd=CWD)}
    for qid in ('exact-identifier', 'cross-harness', 'minimal-context', 'contradiction'):
        row = rows[qid]
        assert row['retrieval_ok'] and row['context_supports_expectation'] and row['surface_verified']
        assert row['evidence_refs'] and all(ref['source_id'] for ref in row['evidence_refs'])
    assert rows['missing-evidence']['retrieval_abstained'] is True
    assert all(row['model_request_observed'] is False and row['model_answer_observed'] is False
               for row in rows.values())


def test_unattested_cohort_recency_does_not_receive_supersession_credit(env):
    rows = acceptance.run('hermes', COHORT, revision='0123abcd', cwd=CWD)
    row = next(item for item in rows if item['question_id'] == 'supersession')
    assert row['retrieval_abstained'] is True
    assert row['context_supports_expectation'] is False
    assert row['surface_verified'] is False
    assert row['superseded_evidence_absent'] is False


@pytest.mark.parametrize(('harness', 'push_supported'), [('grok', None), ('omo', False)])
def test_unsupported_push_cannot_be_misread_as_provider_visibility(env, harness, push_supported):
    rows = acceptance.run(harness, COHORT, revision='0123abcd', cwd=CWD, push_supported=push_supported)
    assert all(row['model_request_observed'] is False and row['model_answer_observed'] is False
               for row in rows)
    assert all('B=UNSUPPORTED' in ' '.join(row['notes']) for row in rows)


# ----------------------------------------------------------------------------- Grok: no rules file by default
def rules(*args, home):
    return subprocess.run([sys.executable, '-m', 'z0int.memory.cli', 'rules', '--harness', 'grok', *args],
                          capture_output=True, text=True, timeout=60, env=dict(os.environ, HOME=str(home)))


def test_grok_rules_need_project_scope_and_owner_approval_and_write_only_scrubbed_project_content(tmp_path, monkeypatch):
    monkeypatch.setenv('Z0INT_HOME', str(tmp_path / 'z0'))
    monkeypatch.setenv('AGENTSVIEW_DATA_DIR', str(tmp_path / 'av'))
    monkeypatch.setenv('PYTHONPATH', os.pathsep.join(p for p in (str(ROOT / 'src'), os.environ.get('PYTHONPATH', ''))
                                                     if p))
    build_av_db(tmp_path / 'av' / 'sessions.db', sessions=SESSIONS)
    home, project = tmp_path / 'home', tmp_path / 'z0'
    home.mkdir()
    project.mkdir()
    q = ['--project-dir', str(project), '--query', 'quokka deploy notes tool output']
    assert rules(*q, home=home).returncode != 0
    assert rules('--scope', 'project', *q, home=home).returncode != 0
    assert rules('--owner-approved', *q, home=home).returncode != 0
    assert not list(tmp_path.rglob('z0-memory.md'))
    ok = rules('--scope', 'project', '--owner-approved', *q, home=home)
    assert ok.returncode == 0, ok.stderr
    written = list(tmp_path.rglob('z0-memory.md'))
    assert written == [project / '.grok' / 'rules' / 'z0-memory.md']
    text = written[0].read_text()
    assert 'agentsview:c1#' in text and 'quokka sibling project plan' not in text  # project z0 only
    assert not any(v in text for v in SECRETS.values())
    assert not (home / '.grok').exists()


def test_a_grok_turn_never_writes_a_rules_file(env, monkeypatch):
    monkeypatch.setenv('Z0INT_MEMORY_INJECT', 'on')
    seam.turn('grok', turn_key='g1', query='quokka gateway', mode='on', endpoint='http://127.0.0.1:9')
    assert not list(env.rglob('.grok'))


# ----------------------------------------------------------------------------- round 2: seeding never touches live data
def test_seeding_refuses_an_existing_db_and_needs_an_explicit_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv('Z0INT_HOME', str(tmp_path / 'z0'))
    live = tmp_path / 'live' / 'sessions.db'
    build_av_db(live)
    before = live.read_bytes()
    with pytest.raises(FileExistsError):
        acceptance.seed_cohort(COHORT, av_db=live, ledger_root=tmp_path / 'ledger')
    assert live.read_bytes() == before and not (tmp_path / 'ledger').exists()
    with pytest.raises(TypeError):
        acceptance.seed_cohort(COHORT, av_db=tmp_path / 'fresh' / 'sessions.db')  # no default ledger (Z0INT_HOME)
    assert not (tmp_path / 'z0').exists() and not (tmp_path / 'fresh' / 'sessions.db').exists()
    proc = subprocess.run([sys.executable, '-m', 'z0int.memory.cli', 'eval', '--harness', 'dsh', '--cohort',
                           str(PINNED / 'cohort.json'), '--revision', 'x', '--out', str(tmp_path / 'rows.jsonl'),
                           '--seed-av-db', str(live), '--seed-ledger', str(tmp_path / 'ledger2')],
                          capture_output=True, text=True, timeout=120,
                          env=dict(os.environ, PYTHONPATH=os.pathsep.join([str(ROOT / 'src'), os.environ.get('PYTHONPATH', '')])))
    assert proc.returncode != 0 and live.read_bytes() == before and not (tmp_path / 'ledger2').exists()
    assert not (tmp_path / 'rows.jsonl').exists()


def test_retrieved_support_is_not_an_observed_model_answer(env):
    rows = acceptance.run('hermes', COHORT, revision='0123abcd', cwd=CWD)
    supported = next(row for row in rows if row['question_id'] == 'cross-harness')
    assert supported['retrieval_ok']
    assert supported.get('answer_supported') is not True
    assert supported.get('verified') is not True
    assert supported['schema'] != 'z0eval.unified_memory_receipt.v0'
