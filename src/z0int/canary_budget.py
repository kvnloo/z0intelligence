"""Locked, cross-run accounting for the explicitly authorized $1 canary.

Integration: call ``reserve`` immediately before each physical call and retain its
opaque ID. Call ``settle`` with observed usage presence and provider-reported cost;
missing cost is never interpreted as zero. Explicit no-usage HTTP 403 outcomes are
unexecuted at cost zero under the user's policy. Other unknown outcomes retain the
reservation. ``paid_canary.routes`` authorizes exact provider/model pairs without
changing global free-only routing. All canary runs must use the same ledger path.
New adapter routes are restricted to OMP openai-codex/anthropic subscriptions;
OpenCode is not authorized. Historical OpenRouter/Vercel admission remains pinned.
"""
import fcntl
import json
import os
import tempfile
import uuid
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from pathlib import Path


_RESERVATION = Decimal('0.1')
_CAP = Decimal('1')


def _money(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise ValueError('invalid canary monetary amount')
    try:
        amount = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError('invalid canary monetary amount') from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError('invalid canary monetary amount')
    return amount


def _config(policy):
    config = policy.get('paid_canary')
    if config is None:
        return None
    if (not isinstance(config, dict)
            or _money(config.get('total_cap_usd')) != _CAP
            or _money(config.get('attempt_reservation_usd')) != _RESERVATION
            or type(config.get('max_attempts')) is not int
            or config['max_attempts'] < 1
            or not isinstance(config.get('ledger'), str) or not config['ledger']):
        raise ValueError('paid canary exceeds fixed spend envelope')
    return config


def _admit(config, provider, model, messages, max_tokens):
    routes = config.get('routes')
    if routes is None:
        allowed = provider in ('openrouter', 'vercel') and model in config.get('models', [])
    else:
        if not isinstance(routes, list):
            raise ValueError('paid canary exceeds fixed spend envelope')
        allowed = any(isinstance(route, dict) and route.get('provider') == provider
                      and route.get('model') == model for route in routes)
    if (provider not in ('openrouter', 'vercel', 'openai-codex', 'anthropic')
            or not allowed or type(max_tokens) is not int or not 1 <= max_tokens <= 2048
            or len(json.dumps(messages, ensure_ascii=False).encode()) > 32768):
        raise ValueError('paid canary exceeds fixed spend envelope')
    if provider not in ('openrouter', 'vercel'):
        return
    ceilings = ({'prompt': 2, 'completion': 10, 'request': 0},
                {'prompt': 2, 'completion': 12, 'request': 0})
    if config.get('max_price') not in ceilings:
        raise ValueError('paid canary exceeds fixed spend envelope')
    # OpenRouter enforces this max_price in the physical request constraints.
    # Vercel exposes a live catalog instead, so fail closed on missing pricing.
    if provider == 'vercel':
        import urllib.request
        request = urllib.request.Request('https://ai-gateway.vercel.sh/v1/models',
            headers={'Authorization': 'Bearer ' + os.environ['AI_GATEWAY_API_KEY']})
        with urllib.request.urlopen(request, timeout=30) as response:
            catalog = json.load(response)['data']
        selected = next(item for item in catalog if item['id'] == model)
        prices = selected['pricing']
        ceiling = config['max_price']
        if (_money(prices['input']) > _money(ceiling['prompt']) / 1000000
                or _money(prices['output']) > _money(ceiling['completion']) / 1000000
                or _money(prices.get('request', 0)) > _money(ceiling['request'])
                or 'tools' not in selected.get('supported_parameters', [])):
            raise ValueError('current gateway catalog exceeds paid canary ceiling')


def _totals(state):
    state['reserved_usd'] = float(sum((_money(row['reserved_usd'])
                                         for row in state['attempts']), Decimal(0)))
    state['spent_usd'] = float(sum((_money(row['actual_cost_usd'])
                                      for row in state['attempts']
                                      if row['actual_cost_usd'] is not None), Decimal(0)))
    state['usage_bearing_calls'] = sum(row['usage_present'] is True for row in state['attempts'])


def _load(stream):
    text = stream.read()
    if not text:
        return {'version': 2, 'reserved_usd': 0, 'spent_usd': 0,
                'usage_bearing_calls': 0, 'attempts': []}
    state = json.loads(text)
    if not isinstance(state, dict) or not isinstance(state.get('attempts'), list):
        raise ValueError('invalid canary ledger')
    if 'version' not in state:
        # Upgrade structure only: no reservation is released without outcome facts.
        for index, row in enumerate(state['attempts']):
            if (not isinstance(row.get('provider'), str) or not isinstance(row.get('model'), str)
                    or _money(row['reserved_usd']) != _RESERVATION):
                raise ValueError('invalid legacy canary ledger')
            row.update(id=f'legacy-{index}', legacy=True, status='reserved',
                       usage_present=None, actual_cost_usd=None, http_status=None)
        if _money(state['reserved_usd']) != sum((_money(row['reserved_usd'])
                                                for row in state['attempts']), Decimal(0)):
            raise ValueError('inconsistent legacy canary reservations')
        state['version'] = 2
        _totals(state)
    elif state['version'] != 2:
        raise ValueError('unsupported canary ledger version')
    ids = set()
    for row in state['attempts']:
        if (not isinstance(row.get('id'), str) or row['id'] in ids
                or row.get('status') not in ('reserved', 'unknown_cost', 'settled', 'unexecuted')
                or row.get('usage_present') is not None and type(row['usage_present']) is not bool):
            raise ValueError('invalid canary reservation')
        ids.add(row['id'])
        reserved = _money(row['reserved_usd'])
        cost = row['actual_cost_usd']
        if row['status'] in ('reserved', 'unknown_cost'):
            valid = reserved == _RESERVATION and cost is None
        else:
            valid = reserved == 0 and cost is not None
            if cost is not None:
                _money(cost)
            if row['status'] == 'settled':
                valid = valid and row['usage_present'] is True
            if row['status'] == 'unexecuted':
                valid = valid and row['http_status'] == 403 and row['usage_present'] is False and _money(cost) == 0
        if not valid:
            raise ValueError('inconsistent canary reservation')
    old_totals = (state['reserved_usd'], state['spent_usd'], state['usage_bearing_calls'])
    _totals(state)
    if (any(_money(old) != _money(state[key]) for old, key in
            zip(old_totals[:2], ('reserved_usd', 'spent_usd')))
            or type(old_totals[2]) is not int or old_totals[2] != state['usage_bearing_calls']):
        raise ValueError('inconsistent canary ledger totals')
    return state


@contextmanager
def _ledger(config):
    path = Path(config['ledger'])
    path.parent.mkdir(parents=True, exist_ok=True)
    # A stable sidecar lock protects atomic ledger replacement across processes.
    with Path(str(path) + '.lock').open('a', encoding='utf-8') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if path.exists():
            with path.open(encoding='utf-8') as stream:
                state = _load(stream)
        else:
            from io import StringIO
            state = _load(StringIO(''))
        yield state
        _totals(state)
        name = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                                             dir=path.parent, delete=False) as stream:
                name = stream.name
                json.dump(state, stream, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, path)
            name = None
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if name is not None:
                os.unlink(name)


def reserve(policy, provider, model, messages, max_tokens):
    """Reserve $0.10 before one physical call; return its opaque ID, or None.

    Unknown previous outcomes stay reserved. Only observed usage-bearing calls
    count against max_attempts; every pending reservation counts against the $1
    spend cap. This function makes no model inference call.
    """
    config = _config(policy)
    if config is None:
        return None
    _admit(config, provider, model, messages, max_tokens)
    with _ledger(config) as state:
        if any(row['actual_cost_usd'] is not None and _money(row['actual_cost_usd']) > _RESERVATION
               for row in state['attempts']):
            raise ValueError('paid canary prior charge exceeded worst-case reservation')
        if (_money(state['spent_usd']) + _money(state['reserved_usd']) + _RESERVATION > _CAP
                or state['usage_bearing_calls'] >= config['max_attempts']):
            raise ValueError('paid canary total spend or usage-bearing attempt cap reached')
        reservation_id = uuid.uuid4().hex
        state['attempts'].append({'id': reservation_id, 'provider': provider, 'model': model,
                                 'reserved_usd': float(_RESERVATION), 'actual_cost_usd': None,
                                 'usage_present': None, 'http_status': None, 'status': 'reserved'})
    return reservation_id


def _settle(row, *, http_status, usage_present, actual_cost_usd):
    if usage_present is not None and type(usage_present) is not bool:
        raise ValueError('usage presence must be an observed boolean or unknown')
    if http_status is not None and (type(http_status) is not int or not 100 <= http_status <= 599):
        raise ValueError('invalid observed HTTP status')
    cost = None if actual_cost_usd is None else _money(actual_cost_usd)
    if http_status == 403 and usage_present is False:
        if cost is not None and cost != 0:
            raise ValueError('no-usage 403 contradicts reported cost')
        status, cost = 'unexecuted', Decimal(0)
    elif usage_present is True and cost is not None:
        status = 'settled'
    else:
        status = 'unknown_cost'
        if cost is not None:
            raise ValueError('known cost requires observed usage presence')
    if row['usage_present'] is True and usage_present is not True:
        raise ValueError('cannot discard observed usage')
    if row['status'] in ('settled', 'unexecuted'):
        if (status != row['status'] or row['usage_present'] is not usage_present
                or row['http_status'] != http_status or _money(row['actual_cost_usd']) != cost):
            raise ValueError('conflicting settlement for completed reservation')
        return
    row.update(http_status=http_status, usage_present=usage_present, status=status,
               actual_cost_usd=None if cost is None else float(cost),
               reserved_usd=float(_RESERVATION) if status == 'unknown_cost' else 0)


def settle(policy, reservation_id, *, http_status=None, usage_present=None, actual_cost_usd=None):
    """Persist observed outcome, charging known actual cost before releasing hold.

    No-usage must be explicit (False), not missing (None). A usage-bearing result
    without a known cost retains its full hold. Repeated identical settlement is
    idempotent. An unexpectedly over-reservation charge is recorded, then raises:
    incurred spend cannot be undone and all subsequent admission fails closed.
    """
    config = _config(policy)
    if config is None:
        if reservation_id is not None:
            raise ValueError('reservation requires its original canary policy')
        return None
    with _ledger(config) as state:
        row = next((row for row in state['attempts'] if row['id'] == reservation_id), None)
        if row is None:
            raise ValueError('unknown canary reservation ID')
        _settle(row, http_status=http_status, usage_present=usage_present, actual_cost_usd=actual_cost_usd)
        result = dict(row)
    if actual_cost_usd is not None and _money(actual_cost_usd) > _RESERVATION:
        raise ValueError('actual canary charge exceeded worst-case reservation; recorded spend retained')
    return result


def migrate_legacy_403(policy, observed_outcomes):
    """Release only legacy holds with explicit, identity-matched 403/no-usage facts.

    Each fact contains attempt_index (zero based), provider, model, http_status=403,
    usage_present=False. Validate the entire batch before persisting any release;
    unmentioned or unknown legacy outcomes remain reserved. Never infer outcomes
    from the existence, age, provider, or count of legacy ledger entries.
    """
    config = _config(policy)
    if config is None:
        raise ValueError('legacy migration requires canary policy')
    if not isinstance(observed_outcomes, list) or not observed_outcomes:
        raise ValueError('legacy migration requires explicit observed outcomes')
    with _ledger(config) as state:
        seen = set()
        for fact in observed_outcomes:
            index = fact.get('attempt_index')
            if (type(index) is not int or index < 0 or index >= len(state['attempts']) or index in seen
                    or fact.get('http_status') != 403 or fact.get('usage_present') is not False):
                raise ValueError('legacy migration requires unique observed 403/no-usage facts')
            seen.add(index)
            row = state['attempts'][index]
            if (row.get('legacy') is not True or row['id'] != f'legacy-{index}'
                    or row['provider'] != fact.get('provider') or row['model'] != fact.get('model')):
                raise ValueError('legacy outcome identity does not match reservation')
            _settle(row, http_status=403, usage_present=False, actual_cost_usd=0)
    return [f'legacy-{fact["attempt_index"]}' for fact in observed_outcomes]
