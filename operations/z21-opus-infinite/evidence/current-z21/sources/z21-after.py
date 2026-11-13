#!/usr/bin/env python3
"""Z21 after-measurement (t_01df79f7): Opus weighted tokens per finished task, read-only.

Same source and shape as the pre-change baseline (bududget.py / families-7d.json:
session_model_usage join sessions over every profile state.db), plus the kanban board
so the number is per FINISHED task, not per session. No writes: state.db and kanban.db
are opened with mode=ro, and burn-pace is only read.

Change window (what "after" means): 2026-11-07 18:20-18:35 CST
  - clean cron fleet default + auxiliary routing  (config.yaml cron:/auxiliary:)
  - clean cron job 6b604f772f6d pinned sonnet/low  (cron/jobs.json)
  - tool-context-trim plugin live for cron/kanban/oneshot subprocesses
  - ideate.py critic-panel routed to sonnet/medium

Usage: z21-after.py [--hours 24] [--json out.json]

Note on the per-finished-task join: `tasks.session_id` is only written back for
gateway-originated tasks, so kanban workers that finished in the window mostly do
not join (1 of 40 at 2026-11-07 18:45). The by-source breakdown is the reliable
before/after signal; per-finished-task is a floor, not the full number.
"""
import argparse, collections, json, pathlib, sqlite3, time

FIELDS = ('api_call_count', 'input_tokens', 'output_tokens', 'cache_read_tokens',
          'cache_write_tokens', 'reasoning_tokens')
W = {'input_tokens': 1, 'output_tokens': 1, 'cache_read_tokens': 1,
     'cache_write_tokens': 1.25, 'reasoning_tokens': 2}  # weighted, matches families report
PROFILES = pathlib.Path('/workspace/hermes-home/profiles')
BOARD = pathlib.Path('/home/kvn/.hermes/kanban/boards/zer0-company/kanban.db')


def family(model):
    return ('opus' if 'opus' in model else 'sonnet' if 'sonnet' in model else
            'haiku' if 'haiku' in model else 'other')


def usage(since):
    for db in sorted(PROFILES.glob('*/state.db')):
        try:
            con = sqlite3.connect(f'file:{db}?mode=ro', uri=True, timeout=30)
            q = ("""select u.model, u.task, s.parent_session_id is not null, s.source, s.id, """
                 + ', '.join('u.' + f for f in FIELDS)
                 + """ from session_model_usage u join sessions s on s.id = u.session_id
                      where u.last_seen >= ?""")
            for r in con.execute(q, (since,)):
                yield (db.parent.name,) + r
        except sqlite3.Error as e:
            print(f'{db.parent.name}: {e}')


def finished_task_ids(since):
    ids = []
    if not BOARD.exists():
        return ids
    con = sqlite3.connect(f'file:{BOARD}?mode=ro', uri=True, timeout=30)
    stamp = since
    try:
        for (tid,) in con.execute(
                "select id from tasks where status='done' and cast(completed_at as integer) >= ?",
                (int(stamp),)):
            ids.append(tid)
    except sqlite3.Error as e:
        print(f'board: {e}')
    return ids


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--hours', type=float, default=24)
    ap.add_argument('--json')
    a = ap.parse_args()
    since = time.time() - a.hours * 3600
    fam = collections.Counter()
    by_source = collections.Counter()
    sess_w = collections.Counter()    # opus weighted tokens per session id
    for prof, model, task, child, source, session_id, *v in usage(since):
        w = sum(W[f] * (val or 0) for f, val in zip(FIELDS, v) if f in W)
        f = family(model)
        fam[(prof, f)] += w
        by_source[(prof, source or 'nil', f)] += w
        if f == 'opus':
            sess_w[session_id] += w
    done = set(finished_task_ids(since))
    task_of = {}   # session_id -> finished task id
    if done and BOARD.exists():
        con = sqlite3.connect(f'file:{BOARD}?mode=ro', uri=True, timeout=30)
        ph = ','.join('?' * len(done))
        try:
            for tid, sid in con.execute(
                    f"select id, session_id from tasks where id in ({ph}) and session_id is not null",
                    tuple(done)):
                task_of[sid] = tid
        except sqlite3.Error as e:
            print(f'board: {e}')
    per_task = collections.Counter()
    for sid, tid in task_of.items():
        per_task[tid] = sess_w.get(sid, 0)
    finished_opus = [w for w in per_task.values() if w]
    out = {
        'window_hours': a.hours,
        'since_epoch': round(since, 1),
        'weighted_tokens_by_profile_family': {f'{p}/{f}': round(w) for (p, f), w in sorted(fam.items())},
        'weighted_tokens_by_source': {f'{p}/{s}/{f}': round(w) for (p, s, f), w in sorted(by_source.items())},
        'finished_tasks_in_window': len(done),
        'finished_tasks_with_opus_tokens': len(finished_opus),
        'opus_weighted_per_finished_task': round(sum(finished_opus) / len(finished_opus)) if finished_opus else 0,
        'opus_weighted_total_on_finished_tasks': round(sum(finished_opus)),
        'opus_share_of_weighted': round(
            100 * sum(w for (p, f), w in fam.items() if f == 'opus') / max(1, sum(fam.values())), 1),
    }
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


if __name__ == '__main__':
    main()
