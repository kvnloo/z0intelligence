"""Synthetic O11/O12 audit contracts. Do not run on groot."""
import copy
import datetime as dt
import hashlib
import json
from pathlib import Path

import pytest

from z0int.fsm import guard as G
from z0int.fsm import policy_guard as P

NOW = dt.datetime(2026, 10, 24, 12, tzinfo=dt.timezone.utc)
ARM = {'kind': 'slm', 'model': 'synthetic-0.6b@r1', 'quant': 'Q4_0',
       'engine': 'synthetic@r1', 'backend': 'cpu', 'device': 'test-worker'}


def policy(stage='live'):
    body = {'schema': 'z0.decision_policy.v1', 'compiled_at': NOW.isoformat(),
            'findings_head': 'synthetic-finding', 'compiler': 'test@r1',
            'families': {'omp.tool': {'stage': stage, 'arm': ARM, 'threshold': 0.95,
                         'rule_id': 'F-omp.tool-1', 'expires_at': '2026-10-25T12:00:00Z',
                         'abstain_to': 'frontier', 'canary_share': 0.1,
                         'evidence': ['synthetic-finding'], 'demoted_reason': None}},
            'serving': [], 'changes': []}
    body['version'] = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':'),
                                               ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    return body


def receipt(doc, **changes):
    return {'ts': NOW.isoformat(), 'family': 'omp.tool', 'policy_version': doc['version'],
            'policy_sha256': doc['version'], 'rule_id': 'F-omp.tool-1',
            'stage': doc['families']['omp.tool']['stage'], 'route': 'general_slm',
            'selected_arm': copy.deepcopy(ARM), 'serving_healthy': True, **changes}


def audit(tmp_path, doc, rows):
    (tmp_path / 'policy.json').write_text(json.dumps(doc))
    (tmp_path / 'receipts.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows))
    return P.audit(str(tmp_path / 'receipts*.jsonl'), tmp_path / 'policy.json', '24h', now=NOW)


@pytest.mark.parametrize('stage,count', [('none', 1), ('shadow', 1), ('canary', 0), ('live', 0)])
def test_o11_requires_canary_or_live(tmp_path, stage, count):
    doc = policy(stage)
    report, findings = audit(tmp_path, doc, [receipt(doc)])
    assert report['counts']['o11_evidence_bound_route'] == count
    assert report['available']
    assert bool(findings) is bool(count)


@pytest.mark.parametrize('field', list(ARM))
def test_o11_binds_every_arm_dimension(tmp_path, field):
    doc = policy()
    row = receipt(doc)
    row['selected_arm'][field] = 'another-arm'
    report, _ = audit(tmp_path, doc, [row])
    assert report['counts']['o11_evidence_bound_route'] == 1


def test_o11_model_string_is_not_full_arm_evidence(tmp_path):
    doc = policy()
    row = receipt(doc, model=ARM['model'])
    del row['selected_arm']
    report, _ = audit(tmp_path, doc, [row])
    assert report['counts']['o11_evidence_bound_route'] == 1


@pytest.mark.parametrize('healthy,abstain,expected', [
    (True, None, 1), (False, None, 0),
    (True, True, 1),
    (True, {'confidence': None, 'threshold': 0.95}, 1),
    (True, {'confidence': 0.95, 'threshold': 0.95}, 1),
    (True, {'confidence': 0.9, 'threshold': 0.5}, 1),
    (True, {'confidence': 0.9, 'threshold': 0.95}, 0),
])
def test_o12_requires_recorded_below_threshold_abstain(tmp_path, healthy, abstain, expected):
    doc = policy()
    report, _ = audit(tmp_path, doc, [receipt(doc, route='remote_frontier',
                                            serving_healthy=healthy, abstain=abstain)])
    assert report['counts']['o12_offload_obligation'] == expected
    assert report['available']


def test_unknown_endpoint_health_is_not_a_false_pass(tmp_path):
    doc = policy()
    report, _ = audit(tmp_path, doc, [receipt(doc, route='remote_frontier', serving_healthy=None)])
    assert report['counts']['o12_offload_obligation'] == 0
    assert not report['available']
    assert 'all 1 live frontier receipts' in report['unavailable_reason']


def test_offload_rate_reports_decisions_and_abstentions(tmp_path):
    doc = policy()
    report, _ = audit(tmp_path, doc, [receipt(doc), receipt(doc, route='frontier',
                                      abstain={'confidence': 0.8, 'threshold': 0.95})])
    assert report['offload_rate']['omp.tool'] == {
        'live_decisions': 2, 'offloaded': 1, 'abstained': 1, 'frontier': 1}


def test_unstamped_stale_and_window(tmp_path):
    doc = policy('canary')
    row = receipt(doc, route='frontier')
    del row['rule_id']
    report, _ = audit(tmp_path, doc, [row, receipt(doc, policy_version='older'),
                    receipt(doc, ts='2026-10-01T00:00:00Z')])
    assert report['window']['receipts'] == 2
    assert report['counts']['unstamped'] == 1
    assert report['counts']['stale_policy'] == 2


def test_multifamily_receipts_use_family_specific_stamps(tmp_path):
    doc = policy('canary')
    row = receipt(doc)
    row['policy_families'] = {'omp.tool': {'stage': 'canary', 'rule_id': 'F-omp.tool-1'}}
    report, _ = audit(tmp_path, doc, [row])
    assert not any(report['counts'].values())
    assert report['window']['receipts'] == 1


def test_missing_or_corrupt_policy_and_receipts_are_unavailable(tmp_path):
    report, _ = P.audit(str(tmp_path / '*.jsonl'), tmp_path / 'missing', '24h', now=NOW)
    assert not report['available']
    assert report['policy_version'] is None
    assert 'no stamped receipts' in report['unavailable_reason']
    doc = policy()
    doc['families']['omp.tool']['stage'] = 'canary'  # Hash no longer matches.
    report, _ = audit(tmp_path, doc, [receipt(doc)])
    assert not report['available']
    assert 'hash mismatch' in report['unavailable_reason']


def test_invalid_timestamp_cannot_silently_escape_window(tmp_path):
    doc = policy()
    report, _ = audit(tmp_path, doc, [receipt(doc, ts=None)])
    assert not report['available']
    assert 'timestamp' in report['unavailable_reason']


@pytest.mark.parametrize('name,source,count', [
    ('worker.py', 'credited_families = ["omp.tool"]\n', 1),
    ('worker.py', 'credited_families = []\n', 1),
    ('worker.py', 'config = {"credited_families": ("omp.tool",)}\n', 1),
    ('worker.py', 'route = "general_slm"\n', 1),
    ('worker.py', 'DEFAULT_ROUTE = "remote_frontier"\n', 1),
    ('worker.py', 'route = policy.route\ncredited_families = policy.credited_families\n', 0),
    ('worker.ts', 'const credited_families = ["omp.tool"];\nconst route = "slm";\n', 2),
    ('worker.yaml', 'route: general_slm\n', 1),
])
def test_static_literal_routes_and_lists(tmp_path, name, source, count):
    (tmp_path / name).write_text(source)
    findings = P.static_findings(tmp_path)
    assert len(findings) == count
    assert all(f['line'] > 0 for f in findings)


def test_only_exact_loader_files_exempt_and_tests_ignored(tmp_path):
    for name in ('src/z0int/policy_runtime.py', 'omp-extensions/local-cognition/index.ts',
                 'tests/fixture.py', 'src/z0int/policy/pretend_loader.py'):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('route = "frontier"\n')
    findings = P.static_findings(tmp_path)
    assert len(findings) == 1
    assert findings[0]['path'].endswith('pretend_loader.py')


def test_cli_persists_exact_report_schema_and_returns_unavailable(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv('Z0INT_HOME', str(tmp_path))
    assert G.main(['policy', '--policy', str(tmp_path / 'missing'), '--json']) == 4
    report = json.loads(capsys.readouterr().out)
    saved = json.loads((tmp_path / 'policy/guard-report.json').read_text())
    fixture = json.loads((Path(__file__).parent / 'fixtures/policy-guard/report.json').read_text())
    assert report == saved
    assert report.keys() == fixture.keys()
    assert report['counts'].keys() == fixture['counts'].keys()
    assert not report['available']


def test_cli_static_findings_return_three_even_without_receipts(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv('Z0INT_HOME', str(tmp_path / 'home'))
    (tmp_path / 'worker.py').write_text('route = "frontier"\n')
    assert G.main(['policy', '--static', str(tmp_path), '--json']) == 3
    output = capsys.readouterr()
    assert json.loads(output.err)['static_findings'] == 1
    assert not json.loads(output.out)['available']


def test_cli_invalid_duration_returns_usage_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv('Z0INT_HOME', str(tmp_path))
    assert G.main(['policy', '--since', 'yesterday', '--json']) == 2
    assert 'duration' in capsys.readouterr().err
