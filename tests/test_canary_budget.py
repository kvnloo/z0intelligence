import json
import pytest
from z0int.canary_budget import reserve


def policy(tmp_path):
    return {'paid_canary': {'models': ['anthropic/claude-haiku-5.5'],
        'max_price': {'prompt': 2, 'completion': 10, 'request': 0},
        'total_cap_usd': 1, 'attempt_reservation_usd': 0.1,
        'max_attempts': 3, 'ledger': str(tmp_path / 'budget.json')}}


def test_global_default_has_no_paid_reservation(tmp_path):
    reserve({}, 'openrouter', 'free', [], 2048)
    assert not list(tmp_path.iterdir())


def test_cross_run_attempt_cap_retains_failed_reservations(tmp_path):
    p = policy(tmp_path)
    for _ in range(3):
        reserve(p, 'openrouter', 'anthropic/claude-haiku-5.5', [], 2048)
    with pytest.raises(ValueError, match='cap reached'):
        reserve(p, 'openrouter', 'anthropic/claude-haiku-5.5', [], 2048)
    state = json.loads((tmp_path / 'budget.json').read_text())
    assert state['reserved_usd'] == 0.3
    assert len(state['attempts']) == 3


@pytest.mark.parametrize('provider,model,messages,tokens', [
    ('other', 'anthropic/claude-haiku-5.5', [], 2048),
    ('openrouter', 'unapproved', [], 2048),
    ('openrouter', 'anthropic/claude-haiku-5.5', ['x' * 32769], 2048),
    ('openrouter', 'anthropic/claude-haiku-5.5', [], 2049),
])
def test_out_of_envelope_fails_before_ledger(tmp_path, provider, model, messages, tokens):
    with pytest.raises(ValueError, match='spend envelope'):
        reserve(policy(tmp_path), provider, model, messages, tokens)
    assert not (tmp_path / 'budget.json').exists()


def test_corrupt_ledger_fails_closed(tmp_path):
    p = policy(tmp_path)
    (tmp_path / 'budget.json').write_text('{broken')
    with pytest.raises(json.JSONDecodeError):
        reserve(p, 'openrouter', 'anthropic/claude-haiku-5.5', [], 2048)
