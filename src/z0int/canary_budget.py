"""Cross-run worst-case spend reservations for the explicitly paid golden canary."""
import fcntl
import json
from pathlib import Path


def reserve(policy, provider, model, messages, max_tokens):
    config = policy.get('paid_canary')
    if not config:
        return
    # At most 32768 input tokens (one token per UTF-8 byte) plus 2048 output
    # tokens, with provider-enforced $2/$10 per million ceilings, is <$0.10.
    ceiling = {'prompt': 2, 'completion': 10, 'request': 0}
    if (provider != 'openrouter' or model not in config['models']
            or config['max_price'] != ceiling or max_tokens > 2048
            or len(json.dumps(messages, ensure_ascii=False).encode()) > 32768
            or config['total_cap_usd'] != 1 or config['attempt_reservation_usd'] != 0.1
            or config['max_attempts'] > 3):
        raise ValueError('paid canary exceeds fixed spend envelope')
    path = Path(config['ledger'])
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+', encoding='utf-8') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.seek(0)
        text = stream.read()
        state = json.loads(text) if text else {'reserved_usd': 0, 'attempts': []}
        if (state['reserved_usd'] + 0.1 > config['total_cap_usd']
                or len(state['attempts']) >= config['max_attempts']):
            raise ValueError('paid canary total spend or attempt cap reached')
        state['reserved_usd'] = round(state['reserved_usd'] + 0.1, 2)
        state['attempts'].append({'provider': provider, 'model': model, 'reserved_usd': 0.1})
        stream.seek(0)
        stream.truncate()
        json.dump(state, stream)
        stream.flush()
        import os
        os.fsync(stream.fileno())
