"""One decision core: role -> harness, provider, model, effort, account (ledger Z17).

Pure, like intelligence.route(): the caller passes every signal (role table, burn-pace,
provider saturation, free inventory). Shadow-only today; nothing routes live from it.
"""
import hashlib
import json
from pathlib import Path

ROLES_PATH = Path(__file__).resolve().parents[2] / 'manifests/roles.v1.json'
EFFORTS = ('low', 'medium', 'high', 'xhigh')
BLOCKED_BANDS = ('hard-throttle', 'hard-stop-exhausted')   # bands burn-pace emits for no-new-work lanes
CAPPING_BANDS = ('throttle', 'hard-throttle')


def load_roles(path=ROLES_PATH):
    return json.loads(Path(path).read_text())


def _saturated(c, saturation):
    for key in (c['provider'] + '/' + c['model'], c['provider']):
        row = saturation.get(key)
        if row is not None and row.get('available') is False:
            return f"saturated ({key}: {row.get('reason', 'unavailable')})"
    return None


def _pool(c, table, lanes):
    """Least-paced admitted burn-pace lane for a metered provider: ((name, row) | None, reason | None)."""
    names = table['pools'].get(c['provider'])
    if not names:
        return None, None
    rows = [(n, lanes[n]) for n in names if n in lanes]
    if not rows:
        return None, 'no burn-pace row for ' + '/'.join(names)
    open_rows = [(n, r) for n, r in rows if r.get('band') not in BLOCKED_BANDS and (r.get('max_workflow_agents') is None or r['max_workflow_agents'] > 0)]
    if not open_rows:
        return None, 'burn: ' + ', '.join(f"{n} {r.get('band')}" for n, r in rows)
    return min(open_rows, key=lambda nr: nr[1].get('pace_ratio') or 0), None  # burn-pace emits null pace_ratio


def _cap(effort, cap):
    if effort not in EFFORTS or cap not in EFFORTS:
        return effort
    return EFFORTS[min(EFFORTS.index(effort), EFFORTS.index(cap))]


def decide(req, signals):
    table = signals['roles']
    role, privacy = req.get('role'), req.get('privacy')
    if role not in table['roles']:
        raise ValueError('Unknown role: %r' % role)
    if privacy not in table['privacy']:
        raise ValueError('Unknown privacy class: %r' % privacy)
    allowed = table['privacy'][privacy]
    executors = req.get('executors')
    lanes = (signals.get('burn') or {}).get('lanes', {})
    saturation = signals.get('saturation') or {}
    inv = signals.get('free_inventory') or {}
    blocked_inv = set(inv.get('disabled', [])) | set(inv.get('paid', []))
    rejected = []
    for n, c in enumerate(table['roles'][role]):
        tag = f"{c['provider']}/{c['model']}"
        law = table['harness_law'].get(c['provider'])
        if allowed != '*' and c['provider'] not in allowed:
            why = f'privacy {privacy} excludes {c["provider"]}'
        elif law is not None and c['harness'] not in law:
            why = f'harness law: {c["provider"]} only via {"/".join(law)}'
        elif executors is not None and c['harness'] not in executors:
            why = f'executor {c["harness"]} not available to caller'
        elif c.get('cost') == 'free' and c.get('inventory_id') in blocked_inv:
            why = 'free inventory: disabled or no longer free'
        else:
            why = _saturated(c, saturation)
        pool = None
        if not why:
            pool, why = _pool(c, table, lanes)
        if why:
            rejected.append(f'{tag}: {why}')
            continue
        effort, account, note = c.get('effort'), None, 'unmetered'
        if pool:
            account, row = pool
            note = f"{account} {row.get('band')} {row.get('pace_ratio')}"
            if row.get('band') in CAPPING_BANDS:
                effort = _cap(effort, row.get('effort'))
        out = {'kind': 'ROUTE', 'role': role, 'harness': c['harness'], 'provider': c['provider'],
               'model': c['model'], 'effort': effort, 'account': account,
               'reason': f'role {role} #{n + 1} {tag} via {c["harness"]}; {note}' + (f'; effort {effort}' if effort else ''),
               'rejected': rejected}
        break
    else:
        out = {'kind': 'HOLD', 'role': role, 'reason': f'no admitted candidate for role {role}', 'rejected': rejected}
    out['receipt_id'] = hashlib.sha256(json.dumps([req, out], sort_keys=True).encode()).hexdigest()[:16]
    return out
