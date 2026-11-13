#!/usr/bin/env python3
"""Z21 step 1: 7-day Opus token budget from every Hermes profile's state.db (read-only).

Source: session_model_usage (per session x model x task). A row counts if last_seen is in the window;
rows whose first_seen predates the window are reported separately (they may include older tokens).
Usage: budget.py [--days 7] [--json out.json]
"""
import argparse, collections, json, pathlib, sqlite3, time

PROFILES = pathlib.Path.home() / '.hermes' / 'profiles'
FIELDS = ('api_call_count', 'input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens', 'reasoning_tokens')


def rows(days):
    since = time.time() - days * 86400
    for db in sorted(PROFILES.glob('*/state.db')):
        try:
            con = sqlite3.connect(f'file:{db}?mode=ro', uri=True, timeout=30)
            q = f"""select u.model, u.task, s.parent_session_id is not null, s.source, u.first_seen < ?, {', '.join('u.' + f for f in FIELDS)}
                    from session_model_usage u join sessions s on s.id = u.session_id where u.last_seen >= ?"""
            for r in con.execute(q, (since, since)):
                yield (db.parent.name,) + r
        except sqlite3.Error as e:
            print(f'{db.parent.name}: {e}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=float, default=7)
    ap.add_argument('--json')
    a = ap.parse_args()
    agg = collections.defaultdict(lambda: collections.Counter())
    for prof, model, task, child, source, straddle, *v in rows(a.days):
        fam = 'opus' if 'opus' in model else 'sonnet' if 'sonnet' in model else 'haiku' if 'haiku' in model else 'other'
        key = (prof, fam, task or 'main', 'child' if child else 'top', source)
        c = agg[key]
        c.update(dict(zip(FIELDS, v)))
        c['sessions'] += 1
        c['straddle_sessions'] += int(bool(straddle))
    out = [dict(zip(('profile', 'family', 'task', 'level', 'source'), k)) | dict(c) for k, c in agg.items()]
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(out, indent=1))
    print(len(out), 'groups')


if __name__ == '__main__':
    main()
