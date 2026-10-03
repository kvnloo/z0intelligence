"""C8: memory acceptance rows (z0evals#56 A-F) per harness, and the Grok project-rules command.

dsh/hermes/omo/omp rows are z0eval.unified_memory_receipt.v0 and must validate against receipt.schema.json at
z0evals fb14919 (vendored byte-identical under tests/fixtures/z0evals-fb14919, blob ids checked below).
claude-code/codex/grok are not in that study's harness enum, so their rows are z0int.memory_acceptance.v0
("z0 memory acceptance"), never the study schema, until the study is amended (C12).
"""
from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

from memory_fixture import SECRETS, SESSIONS, build_av_db
from z0int.memory import acceptance, seam

ROOT = Path(__file__).resolve().parents[1]
PINNED = ROOT / 'tests' / 'fixtures' / 'z0evals-fb14919'
# git blob ids of studies/unified-memory-v0/{receipt.schema,cohort}.json at kvnloo/z0evals fb14919
BLOBS = {'receipt.schema.json': 'b43eaf3eba88e31142d5f0f2af01b0729eb221ba',
         'cohort.json': '284c3e8cef695d0be7a90f782627f9fdaa65e3b4'}
SCHEMA = json.loads((PINNED / 'receipt.schema.json').read_text())
COHORT = json.loads((PINNED / 'cohort.json').read_text())


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv('Z0INT_HOME', str(tmp_path / 'z0'))
    monkeypatch.setenv('AGENTSVIEW_DATA_DIR', str(tmp_path / 'av'))
    monkeypatch.setenv('PYTHONPATH', os.pathsep.join(p for p in (str(ROOT / 'src'), os.environ.get('PYTHONPATH', ''))
                                                     if p))
    monkeypatch.delenv('Z0INT_MEMORY_INJECT', raising=False)
    acceptance.seed_cohort(COHORT, av_db=tmp_path / 'av' / 'sessions.db')
    return tmp_path


def blob_id(path):
    raw = path.read_bytes()
    return hashlib.sha1(b'blob %d\0' % len(raw) + raw).hexdigest()


def test_the_vendored_study_files_are_the_pinned_fb14919_blobs():
    for name, blob in BLOBS.items():
        assert blob_id(PINNED / name) == blob


def test_the_pinned_study_enum_is_exactly_dsh_hermes_omo_omp():
    assert tuple(SCHEMA['properties']['harness']['enum']) == acceptance.STUDY_HARNESSES == ('dsh', 'hermes', 'omo', 'omp')


@pytest.mark.parametrize('harness', ['dsh', 'hermes', 'omo', 'omp'])
def test_study_harness_rows_validate_against_the_pinned_receipt_schema(env, harness):
    rows = acceptance.run(harness, COHORT, revision='0123abcd')
    assert [r['question_id'] for r in rows] == [q['id'] for q in COHORT['questions']]
    for row in rows:
        assert row['schema'] == 'z0eval.unified_memory_receipt.v0'
        assert acceptance.validate(row, SCHEMA) == [], row


def test_cohort_cases_on_the_synthetic_fixture(env):
    rows = {r['question_id']: r for r in acceptance.run('hermes', COHORT, revision='0123abcd')}
    for qid in ('exact-identifier', 'cross-harness', 'minimal-context', 'supersession', 'contradiction'):
        r = rows[qid]
        assert r['retrieval_ok'] and r['injected'] and r['answer_supported'] and r['verified'], r  # A, B, C
        assert r['evidence_refs'] and all(ref['source_id'] for ref in r['evidence_refs'])
    assert rows['supersession']['superseded_answer'] is True  # F: the newer cap is current
    miss = rows['missing-evidence']
    assert miss['abstained'] is True and miss['answer_supported'] is True  # D: abstains, nothing forbidden
    assert all(r['duplicate_injection'] is False for r in rows.values())  # E: a replay never injects twice
    assert all('B=UNVERIFIED' in ' '.join(r['notes']) for r in rows.values())  # seam-level only, no model request


def test_an_observed_model_request_upgrades_b(env):
    observed = {q['id']: True for q in COHORT['questions']}
    rows = acceptance.run('omp', COHORT, revision='0123abcd', observed=observed)
    assert all('B=VERIFIED' in ' '.join(r['notes']) for r in rows if r['injected'])


@pytest.mark.parametrize('harness', ['claude-code', 'codex', 'grok'])
def test_z0_only_harnesses_emit_memory_acceptance_rows_and_never_the_study_label(env, harness):
    rows = acceptance.run(harness, COHORT, revision='0123abcd')
    assert {r['schema'] for r in rows} == {acceptance.ACCEPTANCE_SCHEMA} == {'z0int.memory_acceptance.v0'}
    assert all(r['label'] == 'z0 memory acceptance' and r['harness'] == harness for r in rows)
    with pytest.raises(ValueError, match='not in the z0evals fb14919 harness enum'):
        acceptance.run(harness, COHORT, revision='0123abcd', schema='z0eval.unified_memory_receipt.v0')
    study = dict(rows[0], schema='z0eval.unified_memory_receipt.v0')
    study.pop('label')
    assert any('harness' in e for e in acceptance.validate(study, SCHEMA))


def test_grok_case_b_is_unsupported_pull_only(env):
    rows = acceptance.run('grok', COHORT, revision='0123abcd', observed={q['id']: True for q in COHORT['questions']})
    assert all(r['injected'] is False and 'B=UNSUPPORTED' in ' '.join(r['notes']) for r in rows)


def test_omo_case_b_is_unsupported_when_the_senpi_api_lacks_a_context_hook(env):
    rows = acceptance.run('omo', COHORT, revision='0123abcd', push_supported=False)
    assert all(r['injected'] is False and 'B=UNSUPPORTED' in ' '.join(r['notes']) for r in rows)
    assert all(acceptance.validate(r, SCHEMA) == [] for r in rows)


def test_eval_cli_writes_validated_rows_under_the_given_out(env):
    out = env / 'rows.jsonl'
    proc = subprocess.run([sys.executable, '-m', 'z0int.memory.cli', 'eval', '--harness', 'dsh', '--cohort',
                           str(PINNED / 'cohort.json'), '--revision', '0123abcd', '--out', str(out)],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert len(rows) == 6 and all(acceptance.validate(r, SCHEMA) == [] for r in rows)


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
