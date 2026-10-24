"""Private, prequential shadow learning. No model credit authorizes execution.

Receipts are immutable observations; SQLite materializes their exact session/tool
join, outcomes and online arm scores. Missing joins stay missing, never guessed.
Features contain only preceding tool names (not current arguments or outcomes).
"""
from __future__ import annotations

from collections import Counter, defaultdict, deque
import argparse
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import time
from typing import Any

SCHEMA = 'z0int.shadow.learning.v1'
STRATEGIES = (
    ('history-1', 'One preceding tool predicts the next action', 1, 1, 'knn'),
    ('history-3', 'Three preceding tools disambiguate workflows', 3, 3, 'knn'),
    ('history-8', 'Longer workflow state improves nearest-neighbour agreement', 8, 5, 'knn'),
    ('transition-rule', 'Exact last-tool transition rules beat similarity', 1, 1, 'rules'),
    ('history-bigram', 'Ordered tool bigrams distinguish workflow phases', 5, 3, 'bigram'),
)
PROJECTION_STRATEGIES = (
    ('projection-wide', 'Raise candidate width and budget before judging model accuracy', 8, 5, 'knn'),
    ('projection-maximum', 'Use maximum bounded candidate width to recover projection recall', 8, 9, 'knn'),
)


def projection_key(config: dict) -> str:
    return f"k{config.get('candidate_k', 24)}-b{config.get('prompt_bytes', 3000)}"


def projection_metrics(db: sqlite3.Connection) -> dict:
    metrics = {}
    for row in db.execute('''SELECT l.family,l.action,r.body FROM labels l JOIN receipts r
          ON l.session=r.session AND l.call=r.call'''):
        receipt = json.loads(row['body'])
        for model in receipt.get('shadow') or []:
            candidates = model.get('model_candidate_ids')
            if not isinstance(candidates, list):
                continue
            family = metrics.setdefault(row['family'], {'n': 0, 'hits': 0, 'by_config': {}})
            group = family['by_config'].setdefault(projection_key(receipt.get('projection_config') or {}),
                                                   {'n': 0, 'hits': 0})
            for bucket in (family, group):
                bucket['n'] += 1
                bucket['hits'] += int(row['action'] in candidates)
                bucket['recall'] = bucket['hits']/bucket['n']
    return metrics


def wilson(correct: int, n: int) -> float:
    if not n:
        return 0.0
    z = 1.959963984540054
    p = correct / n
    return (p + z*z/(2*n) - z*math.sqrt(p*(1-p)/n + z*z/(4*n*n))) / (1 + z*z/n)


def connect(home: Path) -> sqlite3.Connection:
    folder = home / 'loop'
    folder.mkdir(parents=True, exist_ok=True)
    os.chmod(folder, 0o700)
    db = sqlite3.connect(folder / 'shadow-learning.sqlite')
    db.row_factory = sqlite3.Row
    db.executescript('''
      CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, inode INTEGER, offset INTEGER);
      CREATE TABLE IF NOT EXISTS calls(session TEXT, call TEXT, tool TEXT, features TEXT,
        outcome TEXT, ts REAL, PRIMARY KEY(session,call));
      CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY, session TEXT, call TEXT,
        ts REAL, family TEXT, body TEXT);
      CREATE INDEX IF NOT EXISTS receipt_join ON receipts(session,call);
      CREATE TABLE IF NOT EXISTS labels(id TEXT PRIMARY KEY, session TEXT, call TEXT,
        family TEXT, ts REAL, action TEXT, features TEXT, outcome TEXT, joined_at REAL);
      CREATE UNIQUE INDEX IF NOT EXISTS label_call ON labels(session,call);
      CREATE TABLE IF NOT EXISTS scores(family TEXT, arm TEXT, total INTEGER DEFAULT 0,
        covered INTEGER DEFAULT 0, correct INTEGER DEFAULT 0, PRIMARY KEY(family,arm));
      CREATE TABLE IF NOT EXISTS evaluated(id TEXT, arm TEXT, PRIMARY KEY(id,arm));
      CREATE TABLE IF NOT EXISTS examples(id TEXT PRIMARY KEY, family TEXT, action TEXT,
        features TEXT, ts REAL);
      CREATE TABLE IF NOT EXISTS ticks(ts REAL PRIMARY KEY, body TEXT);
    ''')
    return db


def lines_since(db: sqlite3.Connection, path: Path):
    """Commit watermarks in the caller's transaction, and never consume a partial line."""
    st = path.stat()
    old = db.execute('SELECT inode,offset FROM files WHERE path=?', (str(path),)).fetchone()
    offset = old['offset'] if old and old['inode'] == st.st_ino and old['offset'] <= st.st_size else 0
    with path.open('rb') as stream:
        stream.seek(offset)
        while True:
            line = stream.readline()
            if not line or not line.endswith(b'\n'):
                break
            offset = stream.tell()
            try:
                row = json.loads(line)
            except (ValueError, UnicodeError):
                continue
            if isinstance(row, dict):
                yield row
    db.execute('INSERT OR REPLACE INTO files VALUES(?,?,?)', (str(path), st.st_ino, offset))


def _result(message: dict) -> dict:
    details = message.get('details') or {}
    outcome = {'tool_error': message.get('isError'), 'exit_code': None,
               'later_correction': None, 'source': 'omp.toolResult'}
    if isinstance(details, dict):
        outcome['exit_code'] = details.get('exitCode', details.get('exit_code'))
    # Some bash versions record only their rendered exit marker.
    if outcome['exit_code'] is None:
        for item in message.get('content') or []:
            if isinstance(item, dict) and item.get('type') == 'text':
                match = re.search(r'(?:Command exited with code|Process exited with code)\s+(\d+)', item.get('text', ''))
                if match:
                    outcome['exit_code'] = int(match[1])
    return outcome


def ingest_sessions(db: sqlite3.Connection, sessions: Path, deadline: float) -> bool:
    for path in sessions.rglob('*.jsonl'):
        if time.monotonic() >= deadline:
            return False
        # Read only the tiny session header even on an incremental append.
        sid = None
        with path.open('rb') as stream:
            for _ in range(8):
                try:
                    row = json.loads(stream.readline())
                except ValueError:
                    continue
                if row.get('type') == 'session':
                    sid = row.get('id')
                    break
        if not sid:
            continue
        history = deque(maxlen=8)
        previous = db.execute('SELECT tool FROM calls WHERE session=? ORDER BY rowid DESC LIMIT 8', (sid,)).fetchall()
        history.extend(r['tool'] for r in reversed(previous))
        for row in lines_since(db, path):
            if row.get('type') == 'custom' and row.get('customType') == 'tool_execution_start':
                data = row.get('data') or {}
                cid, tool = data.get('toolCallId'), data.get('toolName')
                if cid and tool:
                    inserted = db.execute('INSERT OR IGNORE INTO calls VALUES(?,?,?,?,?,?)',
                        (sid, cid, tool, json.dumps(list(history)), None, 0)).rowcount
                    if inserted:
                        history.append(tool)
            msg = row.get('message') or {}
            if msg.get('role') == 'assistant':
                for item in msg.get('content') or []:
                    if not isinstance(item, dict) or item.get('type') != 'toolCall':
                        continue
                    cid, tool = item.get('id'), item.get('name')
                    if cid and tool:
                        inserted = db.execute('INSERT OR IGNORE INTO calls VALUES(?,?,?,?,?,?)',
                            (sid, cid, tool, json.dumps(list(history)), None, 0)).rowcount
                        if inserted:
                            history.append(tool)
            if msg.get('role') == 'toolResult' and msg.get('toolCallId'):
                db.execute('UPDATE calls SET outcome=? WHERE session=? AND call=?',
                           (json.dumps(_result(msg)), sid, msg['toolCallId']))
    return True


def family_of(receipt: dict) -> str:
    families = ((receipt.get('escalation') or {}).get('signals') or {}).get('families') or ['omp.tool']
    return '+'.join(sorted(set(str(x) for x in families)))


def ingest_receipts(db: sqlite3.Connection, path: Path, deadline: float) -> bool:
    if not path.exists():
        return True
    for row in lines_since(db, path):
        if row.get('schema') != 'z0int.cognition.shadow.receipt.v1':
            continue
        key = str(row.get('trace_id') or hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest())
        db.execute('INSERT OR IGNORE INTO receipts VALUES(?,?,?,?,?,?)',
            (key, row.get('session_id'), row.get('tool_call_id'), row.get('ts', 0), family_of(row), json.dumps(row)))
        # Stop only between full lines; generator closes without advancing the watermark.
        if time.monotonic() >= deadline:
            return False
    return True


def join_labels(db: sqlite3.Connection, now: float) -> int:
    before = db.execute('SELECT COUNT(*) FROM labels').fetchone()[0]
    db.execute('''INSERT OR IGNORE INTO labels SELECT r.id,r.session,r.call,r.family,r.ts,
      c.tool,c.features,c.outcome,? FROM receipts r JOIN calls c
      ON r.session=c.session AND r.call=c.call WHERE r.session IS NOT NULL AND r.call IS NOT NULL''', (now,))
    db.execute('''UPDATE labels SET outcome=(SELECT c.outcome FROM calls c
      WHERE c.session=labels.session AND c.call=labels.call) WHERE EXISTS
      (SELECT 1 FROM calls c WHERE c.session=labels.session AND c.call=labels.call AND c.outcome IS NOT NULL)''')
    return db.execute('SELECT COUNT(*) FROM labels').fetchone()[0] - before


def predict(history: list, examples: list, strategy: tuple) -> tuple[str | None, float]:
    _, _, width, k, kind = strategy
    state = history[-width:]
    if not examples:
        return None, 0.0
    def tokens(h):
        h = h[-width:]
        return set(zip(h, h[1:])) if kind == 'bigram' else set(h)
    wanted = tokens(state)
    ranked = []
    for ex in examples:
        previous = ex['history'][-width:]
        if kind == 'rules':
            if previous == state:
                ranked.append((1.0, ex['action']))
        else:
            got = tokens(previous)
            sim = len(wanted & got) / max(1, len(wanted | got))
            if sim:
                ranked.append((sim, ex['action']))
    if not ranked:
        return None, 0.0
    ranked.sort(key=lambda x: x[0], reverse=True)
    votes = Counter(a for _, a in (ranked if kind == 'rules' else ranked[:k]))
    action, n = votes.most_common(1)[0]
    return action, n / sum(votes.values())


def score(db: sqlite3.Connection, threshold: float, deadline: float) -> bool:
    # Bounded memory and prequential order: predict before adding the current row.
    memory = defaultdict(lambda: deque(maxlen=512))
    for ex in db.execute('SELECT * FROM examples ORDER BY ts,id'):
        memory[ex['family']].append({'action': ex['action'], 'history': json.loads(ex['features']), 'ts': ex['ts']})
    todo = db.execute('''SELECT l.*,r.body FROM labels l JOIN receipts r USING(id)
      WHERE NOT EXISTS(SELECT 1 FROM examples e WHERE e.id=l.id) ORDER BY l.ts,l.id''').fetchall()
    for row in todo:
        if time.monotonic() >= deadline:
            return False
        receipt = json.loads(row['body'])
        history = json.loads(row['features'])
        past = [e for e in memory[row['family']] if e['ts'] < row['ts']]
        predictions = [('rules', *predict(history, past, STRATEGIES[3]))]
        predictions += [(s[0], *predict(history, past, s)) for s in STRATEGIES if s[4] != 'rules']
        for shadow in receipt.get('shadow') or []:
            confidence = shadow.get('confidence')
            predictions.append(('slm:' + str(shadow.get('model') or shadow.get('label')),
                                shadow.get('selected_action'), float(confidence) if confidence is not None else 0.0))
        # A late join may be older than retained examples; it must abstain,
        # never train on later usage and call that a historical prediction.
        for arm, action, confidence in predictions:
            if not db.execute('INSERT OR IGNORE INTO evaluated VALUES(?,?)', (row['id'], arm)).rowcount:
                continue
            covered = int(action is not None and confidence >= threshold)
            correct = int(bool(covered and action == row['action']))
            db.execute('''INSERT INTO scores VALUES(?,?,1,?,?) ON CONFLICT(family,arm)
              DO UPDATE SET total=total+1,covered=covered+excluded.covered,correct=correct+excluded.correct''',
                       (row['family'], arm, covered, correct))
        db.execute('INSERT INTO examples VALUES(?,?,?,?,?)',
                   (row['id'], row['family'], row['action'], row['features'], row['ts']))
        memory[row['family']].append({'action': row['action'], 'history': history, 'ts': row['ts']})
    return True


def shadow_portfolio(home: Path, history: list[str], family: str, legal: frozenset[str]) -> dict | None:
    """Read-only active strategy, bounded to 512 examples; never an execution path."""
    path = home / 'loop' / 'shadow-learning.sqlite'
    if not path.exists():
        return None
    status_path = home / 'loop' / 'shadow-learning-status.json'
    state = json.loads(status_path.read_text()) if status_path.exists() else {}
    strategy = next((s for s in (*STRATEGIES, *PROJECTION_STRATEGIES)
                     if s[0] == state.get('current_strategy')), STRATEGIES[0])
    with sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=.05) as db:
        rows = db.execute('SELECT action,features FROM examples WHERE family=? ORDER BY ts DESC LIMIT 512',
                          (family,)).fetchall()
    examples = [{'action': a, 'history': json.loads(h)} for a,h in rows]
    action, confidence = predict(history, examples, strategy)
    return {'label': strategy[0], 'backend': 'shadow-portfolio', 'model': strategy[0],
            'selected_action': action if action in legal else None, 'confidence': confidence,
            'abstained': action not in legal, 'invalid_call': False, 'latency_ms': 0,
            'shadow_credit_only': True}


def arm_scores(db: sqlite3.Connection) -> dict:
    families = defaultdict(dict)
    for r in db.execute('SELECT * FROM scores'):
        n = r['covered']
        families[r['family']][r['arm']] = dict(n=n, total=r['total'], correct=r['correct'],
            accuracy=r['correct']/n if n else None, coverage=n/r['total'] if r['total'] else 0,
            wilson95_lower=wilson(r['correct'], n))
    return dict(families)


def evolve(folder: Path, state: dict, reasons: list, now: float, window: float) -> dict | None:
    """Persist hypothesis -> prospective result; failed configs are never selected again."""
    path = folder / 'strategy-ledger.json'
    ledger = json.loads(path.read_text()) if path.exists() else {'trials': [], 'current': None}
    current = next((t for t in ledger['trials'] if t['id'] == ledger['current']), None)
    def lower(strategy_id):
        if strategy_id.startswith('projection-'):
            key = projection_key({'candidate_k': 64 if strategy_id == 'projection-maximum' else 48,
                                  'prompt_bytes': 3500})
            return max((m['by_config'].get(key, {}).get('recall', 0)
                        for m in state.get('projection_recall', {}).values()), default=0)
        arm = 'rules' if strategy_id == 'transition-rule' else strategy_id
        return max((arms.get(arm, {}).get('wilson95_lower', 0)
                    for arms in state['families'].values()), default=0)
    # Give each trial its full window before judging it. Transport and label
    # failures invalidate the trial immediately rather than waiting another day.
    invalid = any(r.startswith(('transport', 'labels', 'projection_recall')) for r in reasons)
    if current and current['result'] == 'running' and (invalid or now-current['started_at'] >= window):
        best = lower(current['id'])
        current.update(result='improved' if best > current['baseline'] and not invalid else 'failed',
                       ended_at=now, observed_lower=best, reasons=reasons)
    event = None
    if reasons and (not current or current['result'] != 'running'):
        used = {t['id'] for t in ledger['trials']}
        choices = (*PROJECTION_STRATEGIES, *STRATEGIES) if any(
            r.startswith('projection_recall') for r in reasons) else (*STRATEGIES, *PROJECTION_STRATEGIES)
        candidate = next((s for s in choices if s[0] not in used), None)
        if candidate:
            best = lower(candidate[0])
            event = {'id': candidate[0], 'hypothesis': candidate[1], 'started_at': now,
                     'baseline': best, 'result': 'running', 'trigger': reasons,
                     'config': {'history_width': candidate[2], 'k': candidate[3], 'arm': candidate[4],
                                'candidate_k': 64 if candidate[0] == 'projection-maximum' else
                                               48 if candidate[0] == 'projection-wide' else 24,
                                'prompt_bytes': 3500 if candidate[0].startswith('projection-') else 3000}}
            ledger['trials'].append(event)
            ledger['current'] = candidate[0]
        else:
            event = {'result': 'strategies_exhausted', 'at': now, 'trigger': reasons}
    ledger['last_evolution'] = event or ledger.get('last_evolution')
    atomic_json(path, ledger)
    state['current_strategy'] = ledger['current'] or STRATEGIES[0][0]
    state['last_evolution'] = ledger.get('last_evolution')
    active = next((t for t in ledger['trials'] if t['id'] == ledger['current']), None)
    state['projection_config'] = {k: (active or {}).get('config', {}).get(k, default)
                                  for k, default in (('candidate_k', 24), ('prompt_bytes', 3000))}
    return event


def atomic_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')
    os.chmod(temporary, 0o600)
    temporary.replace(path)

def export_labels(db: sqlite3.Connection, folder: Path) -> None:
    """Materialize the private state→actual-action dataset, including failed predictions."""
    target = folder / 'labelled-shadow.jsonl'
    temporary = target.with_suffix('.jsonl.tmp')
    with temporary.open('w') as stream:
        for row in db.execute('SELECT l.*,r.body FROM labels l JOIN receipts r USING(id) ORDER BY l.ts,l.id'):
            receipt = json.loads(row['body'])
            enriched = {'schema': 'z0int.shadow.labelled.v1', 'trace_id': row['id'],
                'session_id': row['session'], 'tool_call_id': row['call'], 'ts': row['ts'],
                'family': row['family'], 'features': {'previous_tools': json.loads(row['features'])},
                'executed_action': row['action'], 'outcome': json.loads(row['outcome']) if row['outcome'] else None,
                'shadow': receipt.get('shadow') or [], 'label_source': 'exact_omp_session_tool_call_join'}
            stream.write(json.dumps(enriched) + '\n')
    os.chmod(temporary, 0o600)
    temporary.replace(target)



def learn(home: Path, *, sessions: Path | None = None, deadline: float | None = None,
          now: float | None = None, config: dict | None = None) -> dict:
    home = Path(home)
    cfg = config or {}
    now = time.time() if now is None else now
    deadline = deadline if deadline is not None else time.monotonic()+600
    folder = home / 'loop'
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / 'shadow-learning.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        db = connect(home)
        try:
            with db:
                complete = ingest_sessions(db, sessions or Path(os.environ.get('Z0INT_OMP_SESSIONS', '/workspace/kvn-home/.omp/agent/sessions')), deadline)
                complete = ingest_receipts(db, home/'shadow'/'cognition-shadow.jsonl', deadline) and complete
                for suffix in ('.3', '.2', '.1', ''):
                    failed_path = home/'shadow'/('cognition-shadow-failures.jsonl'+suffix)
                    complete = ingest_receipts(db, failed_path, deadline) and complete
                added = join_labels(db, now)
                complete = score(db, float(cfg.get('confidence_threshold', .8)), deadline) and complete
                families = arm_scores(db)
                counts = dict(db.execute('SELECT family,COUNT(*) FROM labels GROUP BY family').fetchall())
                recent = db.execute('SELECT body FROM receipts WHERE ts>=?', (now-86400,)).fetchall()
                trials = [s for row in recent for s in json.loads(row[0]).get('shadow', [])]
                from .shadow import failure_class
                failure_counts = Counter(s.get('failure_class') or failure_class(
                    s.get('parse_error') or s.get('error')) for s in trials)
                context_errors = failure_counts['context_overflow']
                errors = failure_counts['transport_error'] + failure_counts['timeout']
                rate = errors/len(trials) if trials else None
                health_path = home/'shadow'/'cognition-shadow-health.json'
                health = json.loads(health_path.read_text()) if health_path.exists() else {}
                bucket = health.get('days', {}).get(time.strftime('%Y-%m-%d', time.gmtime(now)), {})
                failure_rate = bucket.get('failures', 0)/bucket['attempts'] if bucket.get('attempts') else None
                credited = [f for f, arms in families.items() if any(v['n'] >= int(cfg.get('min_observations', 5)) and v['wilson95_lower'] >= float(cfg.get('credit_bar', .8)) for v in arms.values())]
                labels_day = db.execute('SELECT COUNT(*) FROM labels WHERE ts>=?', (now-86400,)).fetchone()[0]
                state = {'schema': SCHEMA, 'ts': now, 'families': families, 'labelled_per_family': counts,
                         'labelled_rows_added': added, 'labels_day': labels_day, 'credited_families': credited,
                         'shadow_credit_only': True, 'transport_attempts_day': len(trials),
                         'transport_errors_day': errors, 'transport_error_rate': rate,
                         'unjoined_receipts': db.execute('SELECT COUNT(*) FROM receipts WHERE id NOT IN (SELECT id FROM labels)').fetchone()[0],
                         'complete': complete, 'learning_curve': {}}
                state['context_overflows_day'] = context_errors
                state['projection_recall'] = projection_metrics(db)
                state['new_writer_health'] = {'attempts_day': bucket.get('attempts', 0),
                    'failures_day': bucket.get('failures', 0), 'failure_rate': failure_rate}
                state['new_writer_health']['identity_failures_day'] = bucket.get('identity_failures', 0)
                state['unjoined_by_reason'] = {
                    'missing_join_key': db.execute('SELECT COUNT(*) FROM receipts WHERE session IS NULL OR session=\"\" OR call IS NULL OR call=\"\"').fetchone()[0],
                    'session_tool_call_not_found': db.execute('''SELECT COUNT(*) FROM receipts r WHERE
                      r.session IS NOT NULL AND r.call IS NOT NULL AND r.session!=\"\" AND r.call!=\"\"
                      AND NOT EXISTS(SELECT 1 FROM calls c WHERE c.session=r.session AND c.call=r.call)''').fetchone()[0]}
                window = float(cfg.get('stall_window_s', 86400))
                prior = db.execute('SELECT body FROM ticks WHERE ts<=? ORDER BY ts DESC LIMIT 1', (now-window,)).fetchone()
                previous = db.execute('SELECT body FROM ticks ORDER BY ts DESC LIMIT 1').fetchone()
                reasons = []
                active_projection = (json.loads(previous[0]) if previous else {}).get('projection_config') or {}
                for family, metric in state['projection_recall'].items():
                    active_metric = metric['by_config'].get(projection_key(active_projection), {})
                    if active_metric.get('n', 0) >= 5 and active_metric.get('recall', 1) < .8:
                        reasons.append('projection_recall_low:'+family)
                if context_errors:
                    reasons.append('context_overflow')
                if bucket.get('identity_failures', 0):
                    reasons.append('labels_identity_invariant')
                if failure_rate is not None and failure_rate > float(cfg.get('transport_error_threshold', .05)):
                    reasons.append('transport_backend_failure_rate')
                if previous and added == 0:
                    reasons.append('labels_not_growing')
                if rate is not None and rate > float(cfg.get('transport_error_threshold', .05)):
                    reasons.append('transport_error_rate')
                if not complete:
                    reasons.append('partial_measurement')
                old = json.loads(prior[0]) if prior else None
                for family, arms in families.items():
                    best_arm = max(arms, key=lambda a: arms[a]['wilson95_lower'])
                    best = arms[best_arm]['wilson95_lower']
                    old_arms = (old or {}).get('families', {}).get(family, {})
                    old_best = max((v['wilson95_lower'] for v in old_arms.values()), default=0)
                    delta = best-old_best if old else None
                    state['learning_curve'][family] = {'best_arm': best_arm, 'best_lower': best,
                        'lower_delta_window': delta, 'baseline_ts': (old or {}).get('ts'),
                        'labelled_rows': counts.get(family, 0),
                        'labels_added_window': counts.get(family, 0)-(old or {}).get('labelled_per_family', {}).get(family, 0)}
                    if old and delta <= float(cfg.get('min_improvement', .001)):
                        reasons.append('plateau:'+family)
                if not families:
                    reasons.append('labels_unavailable')
                state['degraded_by'] = sorted(set(reasons))
                state['status'] = 'DEGRADED' if reasons else 'LEARNING'
                state['evolution'] = evolve(folder, state, reasons, now, window)
                db.execute('INSERT OR REPLACE INTO ticks VALUES(?,?)', (now, json.dumps(state)))
            export_labels(db, folder)
            atomic_json(folder/'shadow-learning-status.json', state)
            return state
        finally:
            db.close()


def status_line(state: dict) -> str:
    best = ','.join(f"{f}:{c['best_arm']}({c['best_lower']:.3f})" for f,c in state.get('learning_curve', {}).items()) or 'none'
    evolution = state.get('last_evolution') or {}
    projection = ','.join(f"{f}:{m['recall']:.3f}(n={m['n']})"
                          for f,m in state.get('projection_recall', {}).items()) or 'unmeasured'
    return (f"shadow {state.get('status', 'NOT_STARTED')} credited={len(state.get('credited_families', []))} "
            f"labels/day={state.get('labels_day', 0)} best={best} strategy={state.get('current_strategy', 'none')} "
            f"projection_recall={projection} context_overflow/day={state.get('context_overflows_day', 0)} "
            f"last_evolution={evolution.get('id', evolution.get('result', 'none'))}")


def main(argv: list[str] | None = None) -> int:
    from .. import paths
    ap = argparse.ArgumentParser(prog='z0int loop shadow')
    ap.add_argument('--home', type=Path, default=paths.home())
    ap.add_argument('--sessions', type=Path)
    ap.add_argument('--budget-s', type=float, default=600)
    args = ap.parse_args(argv)
    state = learn(args.home, sessions=args.sessions, deadline=time.monotonic()+args.budget_s)
    print(json.dumps(state, indent=2))
    return 1 if state['status'] == 'DEGRADED' else 0


if __name__ == '__main__':
    raise SystemExit(main())
