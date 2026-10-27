from datetime import datetime, timedelta, timezone
import json

from z0int.cognition.choice_capacity import effective_cap
from z0int.policy.findings import canonical, digest


IDENTITY = {'model': 'qwen3-0.6b-q8', 'quant': 'Q8_0',
            'engine': 'llama.cpp@build', 'backend': 'cuda', 'device': 'groot'}


def write_policy(home, *, synthetic=False, scope=None, days=1, tasks=None, lower=.97, allowed=5):
    now = datetime.now(timezone.utc)
    body = {'schema': 'z0.decision_policy.v1', 'compiled_at': now.isoformat(),
        'synthetic': synthetic, 'option_capacity': {canonical(scope or IDENTITY).decode(): {
            'allowed_k': allowed, 'finding_ids': [str(i)*64 for i in range(4)],
            'wilson_lower': lower, 'bar': .95,
            'task': tasks or ['T1', 'T2', 'T3', 'T4'],
            'expires_at': (now+timedelta(days=days)).isoformat()}}}
    body['version'] = digest(body)
    target = home/'policy'/'decision-policy.json'
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(body))
    return target


def test_capacity_defaults_and_only_exact_measured_policy_can_raise(tmp_path):
    assert effective_cap(.6, IDENTITY, tmp_path) == 3
    assert effective_cap(1.7, IDENTITY, tmp_path) == 5
    write_policy(tmp_path)
    assert effective_cap(.6, IDENTITY, tmp_path) == 5
    assert effective_cap(.6, {**IDENTITY, 'backend': 'cpu'}, tmp_path) == 3
    assert effective_cap(.6, {**IDENTITY, 'model': 'different'}, tmp_path) == 3


def test_synthetic_expired_incomplete_low_or_modified_capacity_never_raises(tmp_path):
    for kwargs in ({'synthetic': True}, {'days': -1}, {'tasks': ['T1']}, {'lower': .9}):
        write_policy(tmp_path, **kwargs)
        assert effective_cap(.6, IDENTITY, tmp_path) == 3
    path = write_policy(tmp_path)
    body = json.loads(path.read_text())
    next(iter(body['option_capacity'].values()))['allowed_k'] = 100
    path.write_text(json.dumps(body))
    assert effective_cap(.6, IDENTITY, tmp_path) == 3


def test_valid_policy_can_narrow_without_granting_routing_credit(tmp_path):
    write_policy(tmp_path, allowed=2)
    assert effective_cap(.6, IDENTITY, tmp_path) == 2
