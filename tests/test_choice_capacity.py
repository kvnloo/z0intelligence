from datetime import datetime, timedelta, timezone
import json

import pytest

from z0int.cognition.choice_capacity import effective_cap
from z0int.cognition.shadow_binary import score_choices
from z0int.policy.findings import canonical, digest


IDENTITY = {'model': 'sha256:' + 'a' * 64, 'quant': 'Q8_0',
            'engine': 'llama.cpp@build', 'backend': 'cuda', 'device': 'groot'}
RISKS = ('read', 'mutating_reversible', 'destructive')


def entry(now, allowed=5):
    expiry = (now + timedelta(days=1)).isoformat()
    cost_ids = [str(i) * 64 for i in range(4)]
    return {'allowed_k': allowed, 'finding_ids': cost_ids + ([str(i) * 64 for i in range(4, 8)] if allowed else []),
            'wilson_lower': .97 if allowed else None, 'bar_derived': .955,
            'inputs': {'measured': True, 'status': 'measured', 'cost_unit': 'latency_ms_lambda0',
                       'cost_call': .1, 'cost_frontier': 1., 'cost_error': 20.,
                       'abstain_rate': 0., 'finding_ids': cost_ids, 'expires_at': expiry},
            'task': ['T1', 'T2', 'T3', 'T4'], 'expires_at': expiry}


def write_policy(home, *, synthetic=False, scope=None, entries=None):
    now = datetime.now(timezone.utc)
    body = {'schema': 'z0.decision_policy.v1', 'compiled_at': now.isoformat(),
            'synthetic': synthetic, 'option_capacity': {canonical(scope or IDENTITY).decode():
                entries if entries is not None else {risk: entry(now) for risk in RISKS}}}
    body['version'] = digest(body)
    target = home / 'policy' / 'decision-policy.json'
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(body))
    return target


def test_capacity_defaults_and_exact_all_risk_envelope(tmp_path):
    assert effective_cap(.6, IDENTITY, tmp_path) == 3
    assert effective_cap(1.7, IDENTITY, tmp_path) == 5
    now = datetime.now(timezone.utc)
    write_policy(tmp_path, entries={risk: entry(now, allowed) for risk, allowed in zip(RISKS, (8, 5, 6))})
    assert effective_cap(.6, IDENTITY, tmp_path) == 5
    assert effective_cap(.6, {**IDENTITY, 'backend': 'cpu'}, tmp_path) == 3
    assert effective_cap(.6, {**IDENTITY, 'model': 'friendly-alias'}, tmp_path) == 3


@pytest.mark.parametrize('attack', ['synthetic', 'missing_risk', 'unmeasured', 'expired', 'bad_cost_expiry',
                                   'bad_bar', 'bad_lower', 'missing_task', 'missing_evidence', 'modified'])
def test_invalid_envelope_keeps_exploratory_fallback(tmp_path, attack):
    now = datetime.now(timezone.utc)
    entries = {risk: entry(now) for risk in RISKS}
    row = entries['read']
    if attack == 'missing_risk':
        del entries['destructive']
    elif attack == 'unmeasured':
        row['inputs']['measured'] = False
    elif attack == 'expired':
        row['expires_at'] = (now - timedelta(days=1)).isoformat()
    elif attack == 'bad_cost_expiry':
        row['inputs']['expires_at'] = (now - timedelta(days=1)).isoformat()
    elif attack == 'bad_bar':
        row['bar_derived'] = .5
    elif attack == 'bad_lower':
        row['wilson_lower'] = .9
    elif attack == 'missing_task':
        row['task'] = ['T1']
    elif attack == 'missing_evidence':
        row['finding_ids'] = row['inputs']['finding_ids']
    path = write_policy(tmp_path, synthetic=attack == 'synthetic', entries=entries)
    if attack == 'modified':
        body = json.loads(path.read_text())
        body['option_capacity'][canonical(IDENTITY).decode()]['read']['allowed_k'] = 100
        path.write_text(json.dumps(body))
    assert effective_cap(.6, IDENTITY, tmp_path) == 3


def test_zero_measured_envelope_abstains_without_rpc(tmp_path):
    now = datetime.now(timezone.utc)
    write_policy(tmp_path, entries={risk: entry(now, 0 if risk == 'destructive' else 5) for risk in RISKS})
    assert effective_cap(.6, IDENTITY, tmp_path) == 0
    calls = []
    result = score_choices({}, [{'id': 'a', 'description': 'a'}],
                           lambda *a, **kw: calls.append((a, kw)),
                           policy_identity=IDENTITY, policy_home=tmp_path)
    assert result['abstained'] and result['error'] == 'option_cap_exceeded'
    assert calls == []


def test_current_compiler_blocked_destructive_capacity_is_not_a_measured_envelope(tmp_path):
    now = datetime.now(timezone.utc)
    entries = {risk: entry(now) for risk in RISKS}
    entries['destructive'] = {'allowed_k': 0, 'bar_derived': None,
                              'inputs': {'measured': False, 'blocked': True, 'finding_ids': []},
                              'finding_ids': [], 'wilson_lower': None,
                              'task': ['T1', 'T2', 'T3', 'T4'], 'expires_at': now.isoformat()}
    write_policy(tmp_path, entries=entries)
    assert effective_cap(.6, IDENTITY, tmp_path) == 3
