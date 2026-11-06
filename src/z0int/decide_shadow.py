"""Shadow taps for decide() (ledger Z17): log what decide() would pick next to what ran.

Read-only on every source: Hermes state.db (sqlite mode=ro), fx session logs, vllm-sr pod logs.
The only write is the shadow log under ~/.z0int/shadow/decide/. No live routing changes.

    python -m z0int.decide_shadow tap       # every 15 min
    python -m z0int.decide_shadow report --hours 24
"""
import argparse
import collections
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .decide import decide, load_roles

OUT = Path(os.environ.get('Z0INT_DECIDE_SHADOW_DIR', Path.home() / '.z0int/shadow/decide'))
HERMES_PROFILES = Path.home() / '.hermes/profiles'
FX_SESSIONS = Path.home() / '.fx/sessions'
FREELANES = Path('/mnt/zer0models/z0-wt/salvage/free-lanes')
HERMES_AGENT = Path(os.environ.get('HERMES_AGENT_ROOT', '/workspace/hermes-home/hermes-agent'))
SETTLE_S = 600          # only judge sessions old enough to have closed
COOLDOWN_S = 60         # provider_saturation default health_cooldown_seconds for 429
POOL = {'anthropic': 'claude', 'openai-codex': 'codex'}
NORMAL = {'codex': 'openai-codex', 'openai': 'openai-codex'}

# Secret material or real coordinates, not words about them ("no secrets in output" stays public).
_CRED = re.compile(r'(\bsk-[A-Za-z0-9_-]{20,}|\bgh[pousr]_[A-Za-z0-9]{30,}|\bxox[abp]-[A-Za-z0-9-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY'
                   r'|\b(password|passwd|api[_-]?key|secret)\s*[:=]\s*\S{6,})', re.I)
_LOC = re.compile(r'(-?\d{1,3}\.\d{4,}\s*,\s*-?\d{1,3}\.\d{4,}|\bhome address\s*[:=])', re.I)
# The old word-match personal regex had precision 1/30 on a hand-labelled sample (Z17 labels).
# Personal content is now only *flagged* (owner-message sources) and reported as unresolved,
# never fed to decide() as a violation, until its class is confirmed.
_PERSONAL_HINT = re.compile(r"(task-source\.json|owner messages|kevin'?s own messages|kevin-routing-msgs|ayo-ledger|sft-svlm/data)", re.I)


def classify_privacy(text):
    """Tap-side class from secret material or coordinates only; decide() itself only takes the class."""
    text = text or ''
    if _CRED.search(text):
        return 'credentials'
    if _LOC.search(text):
        return 'location'
    return 'public'


def personal_hint(text):
    return bool(_PERSONAL_HINT.search(text or ''))


def _tok(i=0, o=0, cr=0, cw=0):
    return {'input': i or 0, 'output': o or 0, 'cache_read': cr or 0, 'cache_write': cw or 0}


def hermes_events(root, since, until):
    out = []
    for db in sorted(Path(root).glob('*/state.db')):
        prof = db.parent.name
        try:
            con = sqlite3.connect(f'file:{db}?mode=ro', uri=True, timeout=5)
            rows = con.execute(
                'select id, model, billing_provider, started_at, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,'
                " (select content from messages m where m.session_id=s.id and m.role='user' order by m.id limit 1)"
                ' from sessions s where parent_session_id is not null and started_at>? and started_at<=?', (since, until)).fetchall()
            con.close()
        except sqlite3.Error:
            continue
        for sid, model, bp, ts, i, o, cr, cw, text in rows:
            provider = (bp or '').removeprefix('custom:') or 'unknown'
            out.append({'source': 'hermes-delegation', 'id': f'{prof}:{sid}', 'ts': ts,
                        'req': {'role': 'default', 'privacy': classify_privacy(text), 'executors': ['hermes']},
                        'personal_hint': personal_hint(text),
                        'actual': {'harness': 'hermes', 'provider': NORMAL.get(provider, provider), 'model': model, 'effort': None},
                        'tokens': _tok(i, o, cr, cw)})
    return out


def fx_events(root, since, until):
    out = []
    for log in Path(root).glob('v2/*/log.jsonl'):
        if log.stat().st_mtime < since:
            continue
        created, prefs, text, t = None, {}, '', _tok()
        for line in log.open():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            k = r.get('kind')
            if k == 'session_created':
                created = r.get('ts', 0) / 1000
            elif k == 'set' and r.get('key') == 'prefs' and not prefs:
                prefs = r.get('value') or {}
            elif k == 'item' and r.get('type') == 'user' and not text:
                text = (r.get('data') or {}).get('text', '')
            elif k == 'item' and r.get('type') == 'turn_end':
                tp = ((r.get('data') or {}).get('turn_summary') or {}).get('token_progress') or {}
                t['input'] += tp.get('input_tokens', 0); t['output'] += tp.get('output_tokens', 0)
        if created is None or not since < created <= until or not prefs:
            continue
        out.append({'source': 'fx', 'id': log.parent.name, 'ts': created,
                    'req': {'role': 'task', 'privacy': classify_privacy(text), 'executors': ['fx']},
                    'personal_hint': personal_hint(text),
                    'actual': {'harness': 'fx', 'provider': NORMAL.get(prefs.get('provider'), prefs.get('provider')),
                               'model': prefs.get('model'), 'effort': prefs.get('effort')},
                    'tokens': t})
    return out


def _iso(ts):
    return datetime.fromisoformat(ts.replace('Z', '')).replace(tzinfo=timezone.utc).timestamp()


def vllm_sr_events(lines, since, until):
    """Join routing_decision/access/llm_usage by request_id; saturation = 429 on that model within COOLDOWN_S before."""
    reqs = collections.OrderedDict()
    for line in lines:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        rid = r.get('request_id')
        if not rid:
            continue
        e = reqs.setdefault(rid, {})
        if r.get('msg') == 'routing_decision':
            e['ts'], e['model'] = _iso(r['ts']), r.get('selected_model', '')
        elif r.get('msg') == 'access':
            e['code'] = r.get('response_code')
        elif r.get('msg') == 'llm_usage':
            e['tok'] = _tok(r.get('prompt_tokens'), r.get('completion_tokens'), r.get('cached_prompt_tokens'))
    out, last429 = [], {}
    for rid, e in sorted(((k, v) for k, v in reqs.items() if 'model' in v), key=lambda kv: kv[1]['ts']):
        model = e['model'].removeprefix('groq-')
        sat = {f'groq/{m}': {'available': False, 'reason': f'429 {e["ts"] - t:.0f}s ago'}
               for m, t in last429.items() if e['ts'] - t < COOLDOWN_S}
        if since < e['ts'] <= until:
            out.append({'source': 'vllm-sr', 'id': rid, 'ts': e['ts'], 'status': e.get('code'),
                        'req': {'role': 'free', 'privacy': 'public', 'executors': ['vllm-sr']},
                        'actual': {'harness': 'vllm-sr', 'provider': 'groq', 'model': model, 'effort': None},
                        'tokens': e.get('tok', _tok()), 'saturation': sat})
        if e.get('code') == 429:
            last429[model] = e['ts']
    return out


FAILOVER = {'vllm-sr': 'vllm-sr-cerebras'}   # counterfactual executor, not configured live


def shadow_rows(events, signals):
    rows = []
    for e in events:
        s = dict(signals, saturation={**(signals.get('saturation') or {}), **e.get('saturation', {})})
        try:
            d = decide(e['req'], s)
        except Exception as exc:  # one bad signal must not drop the batch; the cursor has already advanced
            d = {'kind': 'ERROR', 'reason': repr(exc)[:200]}
        agree = d['kind'] == 'ROUTE' and (d['provider'], d['model']) == (e['actual']['provider'], e['actual']['model'])
        row = {k: e[k] for k in ('source', 'id', 'ts', 'req', 'actual', 'tokens')} | {
            'status': e.get('status'), 'personal_hint': e.get('personal_hint', False), 'decided': d, 'agree': agree}
        extra = [FAILOVER[x] for x in e['req'].get('executors') or [] if x in FAILOVER]
        if extra and d['kind'] == 'HOLD':
            try:
                row['decided_failover'] = decide(dict(e['req'], executors=e['req']['executors'] + extra), s)
            except Exception as exc:
                row['decided_failover'] = {'kind': 'ERROR', 'reason': repr(exc)[:200]}
        rows.append(row)
    return rows


GAP_S = 20 * 60


def coverage(rows, source='vllm-sr', start=None, end=None):
    """Holes > GAP_S in a source's row stream (e.g. pod logs rotated while the tap was down)."""
    ts = sorted(r['ts'] for r in rows if r['source'] == source)
    if not ts and (start is None or end is None):
        return {'source': source, 'pct': 0.0, 'gaps': []}
    start = ts[0] if start is None else start
    end = ts[-1] if end is None else end
    if end < start:
        raise ValueError('coverage window end is before start')
    ts = [t for t in ts if start <= t <= end]
    edges = [start] + ts + [end]
    gaps = [(a, b) for a, b in zip(edges, edges[1:]) if b - a > GAP_S]
    lost = sum(b - a for a, b in gaps)
    fmt = lambda t: datetime.fromtimestamp(t).strftime('%m-%d %H:%M')
    return {'source': source, 'pct': round(100.0 * (1 - lost / max(end - start, 1)), 1),
            'gaps': [{'from': fmt(a), 'to': fmt(b), 'minutes': round((b - a) / 60)} for a, b in gaps],
            'note': 'rate figures cover only the time with rows'}


def _cooldown_section(rows):
    """vllm-sr calls sent while every Groq candidate was in 429 cooldown, and what a Cerebras failover recovers."""
    sr = [r for r in rows if r['source'] == 'vllm-sr']
    held = [r for r in sr if r['decided']['kind'] == 'HOLD']
    codes = collections.Counter(r.get('status') for r in held)
    fo = [r for r in held if (r.get('decided_failover') or {}).get('kind') == 'ROUTE']
    recovered = [r for r in fo if r.get('status') != 200]
    served = sorted(_total(r['tokens']) for r in sr if r.get('status') == 200 and _total(r['tokens']))
    med = served[len(served) // 2] if served else 0
    per_min = collections.Counter(int(r['ts'] // 60) for r in fo)
    return {'vllm_sr_calls': len(sr), 'sent_during_groq_cooldown': len(held),
            'pct_during_cooldown': round(100.0 * len(held) / len(sr), 1) if sr else None,
            'their_status_codes': {str(k): v for k, v in codes.most_common()},
            'cerebras_failover_routable': len(fo),
            'cerebras_failover_recovers_failed_calls': len(recovered),
            'failover_targets': dict(collections.Counter(f"{r['decided_failover']['provider']}/{r['decided_failover']['model']}" for r in fo)),
            'est_tokens_recovered': len(recovered) * med, 'est_basis': f'recovered calls x median tokens of served vllm-sr calls ({med})',
            'peak_failover_rpm': max(per_min.values()) if per_min else 0, 'cerebras_rpm_limit_gpt_oss_120b': 1000,
            'cost': 'unpriced: no Cerebras price in Hermes usage_pricing or the kerdoios inventory; billed against the ~$15 prepaid credit (K1104-credit)'}


def _total(t):
    return sum(t.values())


def report(rows, price, *, window_start=None, window_end=None):
    """price(provider, model, tokens) -> API-equivalent USD or None.

    Explicit bounds prevent a partial event stream from appearing to cover 24 hours.
    """
    rows = list({(r['source'], r['id']): r for r in rows}.values())
    n = len(rows)
    pct = lambda xs: round(100.0 * sum(r['agree'] for r in xs) / len(xs), 1) if xs else None
    by = collections.defaultdict(list)
    for r in rows:
        by[r['source']].append(r)
    groups = collections.defaultdict(lambda: {'n': 0, 'tokens': 0, 'cost_delta_usd': 0.0, 'reasons': collections.Counter()})
    cost, quota, unpriced = 0.0, collections.Counter(), 0
    for r in rows:
        if r['agree']:
            continue
        a, d, t = r['actual'], r['decided'], r['tokens']
        dk = f"{d['provider']}/{d['model']}" if d['kind'] == 'ROUTE' else d['kind']
        g = groups[(r['source'], f"{a['provider']}/{a['model']}", dk)]
        pa = price(a['provider'], a['model'], t)
        pd = price(d['provider'], d['model'], t) if d['kind'] == 'ROUTE' else 0.0
        if pa is None or pd is None:
            unpriced += 1
        else:
            g['cost_delta_usd'] += pd - pa; cost += pd - pa
        g['n'] += 1; g['tokens'] += _total(t)
        g['reasons'][('; '.join(d['rejected']) if d['kind'] == 'HOLD' else d['reason'])[:200]] += 1
        quota[POOL.get(a['provider'], a['provider'])] -= _total(t)
        if d['kind'] == 'ROUTE':
            quota[POOL.get(d['provider'], d['provider'])] += _total(t)
    top = sorted(groups.items(), key=lambda kv: -kv[1]['n'])
    window = None if window_start is None or window_end is None else {
        'start': datetime.fromtimestamp(window_start, timezone.utc).isoformat(),
        'end': datetime.fromtimestamp(window_end, timezone.utc).isoformat()}
    return {'window_utc': window,
            'vllm_sr_groq_cooldown': _cooldown_section(rows),
            'vllm_sr_coverage': coverage(rows, start=window_start, end=window_end),
            'errors': sum(1 for r in rows if r['decided']['kind'] == 'ERROR'),
            'n': n, 'agree_pct': pct(rows),
            'privacy_unresolved': {'rows_with_personal_hint': sum(1 for r in rows if r.get('personal_hint')),
                                   'note': 'flagged, not scored as violations: eval use of owner messages on GPT awaits a cited approval'},
            'by_source': {s: {'n': len(xs), 'agree_pct': pct(xs),
                              'privacy': dict(collections.Counter(r['req']['privacy'] for r in xs))} for s, xs in by.items()},
            'cost_delta_usd': round(cost, 4), 'unpriced_disagreements': unpriced, 'quota_delta_tokens': dict(quota),
            'top_disagreements': [{'source': s, 'actual': a, 'decided': d, 'n': g['n'], 'tokens': g['tokens'],
                                   'cost_delta_usd': round(g['cost_delta_usd'], 4), 'why': g['reasons'].most_common(1)[0][0]}
                                  for (s, a, d), g in top[:10]]}


# ---- live signal readers (side-effect free) ----

def live_signals():
    burn = json.loads(subprocess.run(['burn-pace', '--json'], capture_output=True, text=True, timeout=120).stdout)
    try:
        with urllib.request.urlopen('http://127.0.0.1:11501/v1/providers', timeout=10) as r:
            sat = {k: {'available': v.get('available'), 'reason': v.get('reason')} for k, v in json.load(r)['providers'].items()}
    except (OSError, ValueError, KeyError):
        sat = {}
    inv = {'disabled': [], 'paid': []}
    try:
        inv['disabled'] = list(json.loads((FREELANES / 'cache/disabled.json').read_text()))
    except (OSError, ValueError):
        pass
    return {'roles': load_roles(), 'burn': burn, 'saturation': sat, 'free_inventory': inv}


def _vllm_sr_lines(since):
    stamp = datetime.fromtimestamp(since - COOLDOWN_S, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    p = subprocess.run(['kubectl', '-n', 'vllm-sr', 'logs', 'deploy/semantic-router', f'--since-time={stamp}'],
                       capture_output=True, text=True, timeout=120)
    return p.stdout.splitlines()


def signal_digest(signals, now):
    lanes = {k: v for k, v in signals['burn'].get('lanes', {}).items() if isinstance(v, dict) and 'band' in v}
    return {'at': now, 'burn_at': signals['burn'].get('at'), 'bands': {k: v.get('band') for k, v in lanes.items()},
            'pace_unknown': sorted(k for k, v in lanes.items() if v.get('pace_ratio') is None)}


def tap(now=None):
    now = now or time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    cur_path = OUT / 'cursor.json'
    cur = json.loads(cur_path.read_text()) if cur_path.exists() else {}
    signals = live_signals()
    until = now - SETTLE_S
    events = []
    for src, fn in (('hermes-delegation', lambda s: hermes_events(HERMES_PROFILES, s, until)),
                    ('fx', lambda s: fx_events(FX_SESSIONS, s, until)),
                    ('vllm-sr', lambda s: vllm_sr_events(_vllm_sr_lines(s), s, now - 60))):
        since = cur.get(src, until - 900)
        try:
            got = fn(since)
        except Exception as exc:  # a broken source must not stop the other taps
            print(f'{src}: tap error {exc!r}', file=sys.stderr)
            continue
        events += got
        cur[src] = now - 60 if src == 'vllm-sr' else until
    rows = shadow_rows(events, signals)
    digest = signal_digest(signals, now)
    if digest['pace_unknown']:
        print(f"burn-pace pace_ratio unknown for {','.join(digest['pace_unknown'])}; ranked as on-pace unless band is under-burn", file=sys.stderr)
    with (OUT / 'rows.jsonl').open('a') as f:
        for r in rows:
            f.write(json.dumps(r | {'signals': digest}) + '\n')
    cur_path.write_text(json.dumps(cur))
    print(json.dumps({'rows': len(rows), 'by_source': dict(collections.Counter(r['source'] for r in rows))}))


def hermes_price(provider, model, t):
    if str(HERMES_AGENT) not in sys.path:
        sys.path.insert(0, str(HERMES_AGENT))
    from agent.usage_pricing import CanonicalUsage, estimate_usage_cost
    if provider in ('local', 'groq', 'opencode', 'vllm-sr'):
        return 0.0                       # free / local lanes
    u = CanonicalUsage(input_tokens=t['input'], output_tokens=t['output'], cache_read_tokens=t['cache_read'], cache_write_tokens=t['cache_write'])
    for p in ({'openai-codex': 'openai'}.get(provider, provider), 'openrouter'):
        c = estimate_usage_cost(model, u, provider=p)
        if c.amount_usd is not None:
            return float(c.amount_usd)
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(prog='z0int.decide_shadow')
    ap.add_argument('cmd', choices=['tap', 'report'])
    ap.add_argument('--hours', type=float, default=24)
    a = ap.parse_args(argv)
    if a.cmd == 'tap':
        return tap()
    end = time.time()
    cut = end - a.hours * 3600
    rows = [r for line in (OUT / 'rows.jsonl').open() if cut <= (r := json.loads(line))['ts'] <= end]
    print(json.dumps(report(rows, hermes_price, window_start=cut, window_end=end), indent=1))


if __name__ == '__main__':
    main()
