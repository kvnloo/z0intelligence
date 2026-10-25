"""Synthetic O11/O12/O13 audit contracts. Do not run on groot."""
import copy
import datetime as dt
import hashlib
import json
import tempfile
from pathlib import Path

import pytest

from z0int.fsm import guard as G
from z0int.fsm import policy_guard as P

NOW = dt.datetime(2026, 10, 24, 12, tzinfo=dt.timezone.utc)
ARM = {'kind': 'slm', 'model': 'sha256:' + 'f' * 64, 'quant': 'Q4_0',
       'engine': 'b1-748d422', 'backend': 'cpu', 'device': 'test-worker'}
IDENTITY = {'model_id': 'synthetic-alias', 'engine_build': 'synthetic-build',
            'model_path': '/synthetic/models/test.gguf', 'model_sha256': 'f' * 64}


def policy(stage='live', *, credited=True):
    body = {'schema': 'z0.decision_policy.v1', 'compiled_at': NOW.isoformat(),
            'findings_head': 'synthetic-finding', 'compiler': 'test@r1',
            'families': {'omp.tool': {'stage': stage, 'arm': ARM, 'threshold': 0.95,
                         'rule_id': 'F-omp.tool-1', 'expires_at': '2026-10-25T12:00:00Z',
                         'abstain_to': 'frontier', 'canary_share': 0.1, 'risk_class': 'read',
                         'evidence': ['synthetic-finding'], 'demoted_reason': None}},
            'serving': [{**ARM, 'kind': 'engine', 'url': 'http://127.0.0.1:9999/v1',
                         'expected_model_id': IDENTITY['model_id'],
                         **{key: value for key, value in IDENTITY.items() if key != 'model_id'}}],
            'changes': []}
    body['version'] = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':'),
                                               ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    return capacity(body) if credited else body


def receipt(doc, **changes):
    return {'ts': NOW.isoformat(), 'family': 'omp.tool', 'policy_version': doc['version'],
            'policy_sha256': doc['version'], 'rule_id': 'F-omp.tool-1',
            'stage': next(iter(doc['families'].values()))['stage'], 'route': 'general_slm',
            'selected_arm': copy.deepcopy(ARM), 'serving_healthy': True,
            'option_count': 2, 'model_id': ARM['model'], 'quant': ARM['quant'], 'risk_class': 'read',
            'observed_identity': {key: value for key, value in IDENTITY.items() if key != 'model_sha256'},
            'props_model_id': IDENTITY['model_id'], 'expected_identity': copy.deepcopy(IDENTITY), **changes}


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


@pytest.mark.parametrize('field', [key for key in ARM if key != 'kind'])
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


def test_finding_role_is_not_physical_deployment_identity(tmp_path):
    doc = policy()
    row = receipt(doc)
    row['selected_arm']['kind'] = 'engine'
    report, _ = audit(tmp_path, doc, [row])
    assert not any(report['counts'].values())


@pytest.mark.parametrize('actual_family,actual_scope,violates', [
    ('omp.tool', {'device': 'test-worker'}, False),
    ('omp.tool', {'device': 'another-worker'}, True),
    ('router.model', {'device': 'test-worker'}, True),
    ('omp.tool', None, False),
])
def test_scoped_rule_cannot_credit_another_scope_or_family(tmp_path, actual_family, actual_scope, violates):
    doc = policy('canary')
    entry = doc['families'].pop('omp.tool')
    entry.update(family='omp.tool', scope={'device': 'test-worker'})
    scope_sha = hashlib.sha256(json.dumps(entry['scope'], sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    key = 'omp.tool@' + scope_sha[:12]
    entry['rule_id'] = 'F-' + key + '-1'
    doc['families'][key] = entry
    del doc['version']
    doc['version'] = hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(',', ':'),
                                              ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    row = receipt(doc, stage='canary', family=actual_family, policy_family=key,
                  rule_id=entry['rule_id'], scope=actual_scope)
    if actual_scope is None:
        row.update(route='remote_frontier', stage='none', policy_stage='canary',
                   policy_reason='no_serving_for_scope', serving_healthy=None)
        del row['scope']
        del row['selected_arm']
    report, _ = audit(tmp_path, doc, [row])
    assert bool(report['counts']['o11_evidence_bound_route']) is violates
    assert bool(report['counts']['stale_policy']) is violates


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


def test_typed_typescript_routing_overrides_are_not_exempt(tmp_path):
    (tmp_path / 'worker.ts').write_text('const route: string = "general_slm";\n'
                                      'const credited_families: string[] = ["omp.tool"];\n')
    findings = P.static_findings(tmp_path)
    assert {finding['rule'] for finding in findings} == {'hardcoded-route', 'hardcoded-credited-families'}


def test_session_envelope_inherits_timestamp(tmp_path):
    doc = policy('canary')
    row = receipt(doc)
    del row['ts']
    report, _ = audit(tmp_path, doc, [{'timestamp': NOW.isoformat(), 'type': 'custom', 'data': row}])
    assert report['available']
    assert report['window']['receipts'] == 1
    assert not any(report['counts'].values())


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


@pytest.mark.parametrize('field', ['model_id', 'engine_build', 'model_path'])
def test_o11_binds_observed_endpoint_identity(tmp_path, field):
    doc = policy('canary')
    row = receipt(doc)
    row['observed_identity'][field] = 'wrong'
    report, _ = audit(tmp_path, doc, [row])
    assert report['counts']['o11_evidence_bound_route'] == 1


def test_o11_binds_props_alias_and_does_not_require_revision(tmp_path):
    doc = policy('canary')
    row = receipt(doc, response_revision=None)
    report, _ = audit(tmp_path, doc, [row])
    assert not any(report['counts'].values())
    row['props_model_id'] = 'another-model'
    report, _ = audit(tmp_path, doc, [row])
    assert report['counts']['o11_evidence_bound_route'] == 1


@pytest.mark.parametrize('field', P.PHYSICAL_KEYS)
def test_o11_rejects_partial_named_physical_arm(tmp_path, field):
    doc = copy.deepcopy(policy('canary'))
    del doc['families']['omp.tool']['arm'][field]
    del doc['version']
    doc['version'] = hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(',', ':'),
                                              ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    report, _ = audit(tmp_path, doc, [receipt(doc)])
    assert report['counts']['o11_evidence_bound_route'] == 1


@pytest.fixture
def real_tmp_path():
    # Fleet sets TMPDIR=/work/tmp. The policy rule deliberately means /tmp.
    with tempfile.TemporaryDirectory(prefix='policy-guard-', dir='/tmp') as value:
        yield Path(value)


def synthetic_home(home, *, rule_marker=True, receipt_marker=True):
    finding = {'schema': 'z0.finding.v1', 'prev': '0' * 64, 'ts': NOW.isoformat(),
               'source': {'kind': 'manual', 'path': '/synthetic/fixture.json',
                          'sha256': '0' * 64, 'run_id': 'fixture'},
               'subject': {'family': 'omp.tool', 'job': 'synthetic guard fixture'}, 'arm': ARM,
               'metric': {'name': 'acc_at_conf', 'value': 1.0, 'n': 100, 'ci_low': 0.96,
                          'ci_high': 1.0, 'ci_method': 'wilson95', 'higher_is_better': True},
               'baseline': {'arm': 'frontier', 'value': 1.0}, 'evidence_class': 'measured',
               'scope_note': 'Synthetic fixture, never production evidence',
               'expires_at': '2026-10-25T12:00:00Z', 'synthetic': True}
    finding['id'] = hashlib.sha256(json.dumps(finding, sort_keys=True, separators=(',', ':'),
                                             ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    doc = policy('canary')
    doc['findings_head'] = finding['id']
    doc['families']['omp.tool']['evidence'] = [finding['id']]
    if rule_marker:
        doc['families']['omp.tool']['synthetic'] = True
    del doc['version']
    doc['version'] = hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(',', ':'),
                                              ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    row = receipt(doc, route='frontier', trace_id='synthetic-trace', synthetic=receipt_marker)
    path = home / 'policy/decision-policy.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(doc))
    ledger = home / 'findings/findings.jsonl'
    ledger.parent.mkdir()
    ledger.write_text(json.dumps(finding) + '\n')
    receipts = home / 'receipts.jsonl'
    receipts.write_text(json.dumps(row) + '\n')
    return path, receipts, finding['id']


def test_synthetic_tmp_home_allowed_even_when_process_z0int_home_is_overridden(real_tmp_path, monkeypatch):
    home = real_tmp_path / 'isolated'
    path, receipts, _ = synthetic_home(home)
    monkeypatch.setenv('Z0INT_HOME', str(home))
    report, findings = P.audit(str(receipts), path, '24h', now=NOW)
    assert report['available']
    assert not any(report['counts'].values())
    assert not findings


@pytest.mark.parametrize('rule_marker', [False, True])
def test_synthetic_live_home_and_tmp_symlink_are_rejected(real_tmp_path, monkeypatch, rule_marker):
    owner = real_tmp_path / 'owner'
    live = owner / '.z0int'
    path, receipts, finding_id = synthetic_home(live, rule_marker=rule_marker, receipt_marker=False)
    monkeypatch.setattr(P.Path, 'home', staticmethod(lambda: owner))
    link = real_tmp_path / 'looks-temporary'
    link.symlink_to(live, target_is_directory=True)
    report, findings = P.audit(str(receipts), link / 'policy/decision-policy.json', '24h', now=NOW)
    synthetic = [finding for finding in findings if finding['rule'] == 'synthetic-evidence-outside-tmp']
    assert report['counts']['o11_evidence_bound_route'] == 3
    assert {finding['id'] for finding in synthetic} == {finding_id, 'F-omp.tool-1', 'synthetic-trace'}
    assert path.resolve() == (link / 'policy/decision-policy.json').resolve()


def test_explicit_live_home_is_protected_even_under_tmp(real_tmp_path, monkeypatch):
    path, receipts, _ = synthetic_home(real_tmp_path / 'live')
    monkeypatch.setenv('Z0INT_LIVE_HOME', str(path.parent.parent))
    report, _ = P.audit(str(receipts), path, '24h', now=NOW)
    assert report['counts']['o11_evidence_bound_route'] == 3


def test_tmpfoo_is_not_tmp_and_cli_returns_three(tmp_path, monkeypatch, capsys):
    doc = policy('canary')
    doc['families']['omp.tool']['synthetic'] = True
    del doc['version']
    doc['version'] = hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(',', ':'),
                                              ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    virtual = Path('/tmpfoo/policy-guard-fixture/policy/decision-policy.json')
    original = Path.read_text
    def fixture_read(path, *args, **kwargs):
        return json.dumps(doc) if path == virtual else original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', fixture_read)
    monkeypatch.setenv('Z0INT_HOME', str(tmp_path / 'output'))
    receipts = tmp_path / 'receipts.jsonl'
    current = dt.datetime.now(dt.timezone.utc).isoformat()
    receipts.write_text(json.dumps(receipt(doc, ts=current, route='frontier', synthetic=True,
                                            trace_id='tmpfoo-trace')) + '\n')
    assert G.main(['policy', '--policy', str(virtual), '--receipts', str(receipts), '--json']) == 3
    output = capsys.readouterr()
    findings = json.loads(output.err)['findings']
    assert {finding.get('id') for finding in findings} >= {'F-omp.tool-1', 'tmpfoo-trace'}
    assert not P._synthetic_context(virtual)[1]


def test_envelope_cannot_clear_synthetic_taint_in_child(tmp_path, monkeypatch):
    doc = policy('canary')
    row = receipt(doc, route='frontier', synthetic=False, trace_id='nested-synthetic')
    monkeypatch.setenv('Z0INT_LIVE_HOME', str(tmp_path.parent))
    report, findings = audit(tmp_path, doc, [{'synthetic': True, 'receipts': [row]}])
    assert report['counts']['o11_evidence_bound_route'] == 1
    assert any(finding.get('id') == 'nested-synthetic' for finding in findings)


def rehash(doc):
    doc.pop('version', None)
    doc['version'] = hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(',', ':'),
                                              ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    return doc


def capacity(doc, allowed_k=24, **changes):
    scope = {key: ARM[key] for key in P.PHYSICAL_KEYS}
    key = json.dumps(scope, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    doc['option_capacity'] = {key: {'read': {'allowed_k': allowed_k, 'finding_ids': ['capacity-cell'],
        'wilson_lower': .97, 'task': ['T1', 'T2'], 'bar_derived': .95,
        'inputs': {'measured': False, 'status': 'unmeasured', 'cost_call': 0,
                   'cost_frontier': 1, 'cost_error': 20, 'abstain_rate': 0,
                   'finding_ids': []},
        'expires_at': '2026-10-25T12:00:00Z', **changes}}}
    return rehash(doc)


@pytest.mark.parametrize('stage', ['shadow', 'canary', 'live'])
@pytest.mark.parametrize('k,expected', [(119, 1), (24, 1), (2, 1)])
def test_o13_all_request_stages_use_default_zero(tmp_path, stage, k, expected):
    doc = policy(stage, credited=False)
    report, findings = audit(tmp_path, doc, [receipt(doc, id='request-1', option_count=k)])
    assert report['counts']['o13_option_capacity'] == expected
    bad = [f for f in findings if f['rule'] == 'o13_option_capacity']
    assert [f['id'] for f in bad] == (['request-1'] if expected else [])
    if bad:
        assert bad[0]['allowed_k'] == 0
        assert bad[0]['option_count'] == k


def test_o13_todays_nested_shadow_receipt_shapes(tmp_path):
    rows = [json.loads(line) for line in
            (Path(__file__).parent / 'fixtures/policy-guard/o13-shadow.jsonl').read_text().splitlines()]
    report, findings = audit(tmp_path, policy(), rows)
    assert report['available']
    assert report['counts']['o13_option_capacity'] == 3
    assert [f['option_count'] for f in findings] == [119, 24, 2]
    assert all(f['id'].startswith(('synthetic-full-catalogue-119:', 'synthetic-projection-24:', 'synthetic-binary-2:'))
               for f in findings)
    clean, findings = audit(tmp_path, policy(), rows[-1:])
    assert clean['available']
    assert clean['counts']['o13_option_capacity'] == 1
    assert len(findings) == 1


@pytest.mark.parametrize('field', P.PHYSICAL_KEYS)
def test_o13_capacity_credit_cannot_generalize_any_scope_dimension(tmp_path, field):
    doc = capacity(policy('canary'))
    row = receipt(doc, option_count=24)
    row['selected_arm'][field] = 'different'
    report, findings = audit(tmp_path, doc, [row])
    assert report['counts']['o13_option_capacity'] == 1
    assert next(f for f in findings if f['rule'] == 'o13_option_capacity')['allowed_k'] == 0


@pytest.mark.parametrize('changes', [
    {}, {'allowed_k': 24.0}, {'allowed_k': True}, {'finding_ids': []},
    {'finding_ids': [None]}, {'task': []}, {'task': ['']}, {'wilson_lower': .94},
    {'wilson_lower': True}, {'bar_derived': None}, {'bar_derived': False},
    {'expires_at': '2026-10-24T11:59:59Z'}, {'expires_at': 'invalid'},
])
def test_o13_only_valid_unexpired_compiled_capacity_credits(tmp_path, changes):
    doc = capacity(policy('canary'), **changes)
    report, findings = audit(tmp_path, doc, [receipt(doc, option_count=24, risk_class='read')])
    assert report['counts']['o13_option_capacity'] == int(bool(changes))
    assert report['available']
    if not changes:
        assert not findings


@pytest.mark.parametrize('risk', [None, 'write', 'destructive', 'unknown', 'mutating_reversible'])
def test_o13_read_credit_never_transfers_risk(tmp_path, risk):
    doc = capacity(policy('canary'))
    report, _ = audit(tmp_path, doc, [receipt(doc, option_count=2, risk_class=risk)])
    assert report['counts']['o13_option_capacity'] == 1


@pytest.mark.parametrize('bar,lower,expected', [(-1, .01, 24), (1.1, 1, 0), (.95, .95, 0)])
def test_o13_strict_unclamped_cost_threshold(bar, lower, expected):
    inputs = {'cost_call': 0, 'cost_frontier': 1 - bar if bar < 1 else 1,
              'cost_error': 1, 'abstain_rate': 0,
              'status': 'unmeasured', 'measured': False, 'finding_ids': []}
    if bar > 1:
        inputs['cost_call'] = bar
    doc = capacity(policy('canary'), bar_derived=bar, wilson_lower=lower, inputs=inputs)
    scope = {key: ARM[key] for key in P.PHYSICAL_KEYS}
    assert P._allowed_k(doc, scope, NOW, 'read') == expected


@pytest.mark.parametrize('inputs', [
    {'blocked': True},
    {'measured': True, 'status': 'unmeasured'},
    {'cost_call': 0, 'cost_frontier': 1, 'cost_error': 0, 'abstain_rate': 0,
     'measured': False, 'status': 'unmeasured'},
])
def test_o13_invalid_cost_authority_fails_closed(inputs):
    doc = capacity(policy('canary'), inputs=inputs)
    scope = {key: ARM[key] for key in P.PHYSICAL_KEYS}
    assert P._allowed_k(doc, scope, NOW, 'read') == 0


@pytest.mark.parametrize('value', [None, True, '24', 2.5, -1])
def test_o13_invalid_request_count_is_not_a_pass(tmp_path, value):
    doc = policy('canary')
    report, _ = audit(tmp_path, doc, [receipt(doc, option_count=value)])
    assert report['counts']['o13_option_capacity'] == 1


def test_o13_ledger_cannot_grant_capacity(tmp_path):
    home = tmp_path / 'home'
    (home / 'policy').mkdir(parents=True)
    (home / 'findings').mkdir()
    (home / 'findings/findings.jsonl').write_text(json.dumps({
        'id': 'uncompiled', 'subject': {'family': 'choice.option_capacity'},
        'arm': ARM, 'metric': {'option_count': 119, 'value': 1, 'n': 10000}}) + '\n')
    doc = policy('canary')
    path = home / 'policy/current.json'
    path.write_text(json.dumps(doc))
    rec = home / 'receipts.jsonl'
    rec.write_text(json.dumps(receipt(doc, option_count=119)) + '\n')
    report, _ = P.audit(str(rec), path, '24h', now=NOW)
    assert report['counts']['o13_option_capacity'] == 1


@pytest.mark.parametrize('relative,body', [
    ('config/serving.json', {'schema': 'z0int.serving.v1',
                            'endpoints': [{'id': 'configured-arm', **ARM, 'option_count': 24}]}),
    ('config/loop.json', {'shadow_learning': {'arms': [
        {'id': 'configured-arm', 'scope': ARM, 'option_count': 24}]}}),
    ('config/z0int.json', {'shadow_learning': {'arms': [
        {'id': 'configured-arm', 'arm': ARM, 'option_count': 24}]}}),
    ('config/shadow.json', {'arms': [{'id': 'configured-arm', **ARM, 'candidate_k': 24}]}),
    ('shadow/config.json', {'arms': [{'id': 'configured-arm', **ARM, 'option_count': 119}]}),
    ('loop/shadow-learning-status.json', {'projection_config': {'candidate_k': 24}}),
])
def test_o13_configured_arms_are_audited_even_without_receipts(tmp_path, relative, body):
    home = tmp_path / 'home'
    (home / 'policy').mkdir(parents=True)
    target = home / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(body))
    path = home / 'policy/current.json'
    path.write_text(json.dumps(policy()))
    report, findings = P.audit(None, path, '24h', now=NOW)
    assert report['counts']['o13_option_capacity'] == 1
    assert findings[0]['id']
    assert findings[0]['path'] == str(target)
    assert findings[0]['allowed_k'] == 0


def test_o13_projection_config_pairs_each_serving_endpoint(tmp_path):
    home = tmp_path / 'home'
    (home / 'policy').mkdir(parents=True)
    (home / 'loop').mkdir()
    (home / 'config').mkdir()
    (home / 'loop/shadow-learning-status.json').write_text(
        json.dumps({'projection_config': {'candidate_k': 24}}))
    (home / 'config/serving.json').write_text(json.dumps({'schema': 'z0int.serving.v1',
        'endpoints': [{'model_id': 'small-a'}, {'model_id': 'small-b'}]}))
    path = home / 'policy/current.json'
    path.write_text(json.dumps(policy()))
    report, findings = P.audit(None, path, '24h', now=NOW)
    assert report['counts']['o13_option_capacity'] == 2
    assert {f['id'] for f in findings} == {'/projection_config@small-a', '/projection_config@small-b'}


@pytest.mark.parametrize('stage', ['shadow', 'canary', 'live'])
def test_o13_configured_policy_families_bind_compiled_capacity(tmp_path, stage):
    doc = policy(stage, credited=False)
    doc['families']['omp.tool']['option_count'] = 24
    rehash(doc)
    report, findings = audit(tmp_path, doc, [])
    assert report['counts']['o13_option_capacity'] == 1
    assert findings[0]['id'] == 'F-omp.tool-1'
    capacity(doc)
    report, findings = audit(tmp_path, doc, [])
    assert report['counts']['o13_option_capacity'] == 0
    assert not findings


@pytest.mark.parametrize('reason', ['option_bound_exceeded', 'option_capacity_exceeded'])
def test_o13_honest_refusal_is_not_o12_or_o13_violation(tmp_path, reason):
    doc = policy()
    row = receipt(doc, route='frontier', stage='none', policy_stage='live', policy_reason=reason,
                  slm_requested=False, option_count=0, attempted_option_count=119)
    report, findings = audit(tmp_path, doc, [row])
    assert report['available']
    assert not any(report['counts'].values())
    assert not findings


def test_o13_dishonest_no_request_flag_does_not_hide_positive_count(tmp_path):
    doc = policy()
    report, _ = audit(tmp_path, doc, [receipt(doc, slm_requested=False, option_count=119)])
    assert report['counts']['o13_option_capacity'] == 1


def test_o13_cli_returns_three_and_lists_offending_id(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv('Z0INT_HOME', str(tmp_path / 'output'))
    doc = policy('canary')
    doc['families']['omp.tool']['option_count'] = 119
    rehash(doc)
    path = tmp_path / 'policy.json'
    path.write_text(json.dumps(doc))
    assert G.main(['policy', '--policy', str(path), '--since', '365d', '--json']) == 3
    output = capsys.readouterr()
    assert json.loads(output.out)['counts']['o13_option_capacity'] == 1
    assert json.loads(output.err)['findings'][0]['id'] == 'F-omp.tool-1'
