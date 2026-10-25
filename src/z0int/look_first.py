"""Offline, bounded capability lookup. Retrieved records are evidence, not authority."""
from __future__ import annotations

import argparse
import datetime as dt
from contextlib import closing
import json
import re
import sqlite3
import time
from pathlib import Path

DEFAULT_INDEX = Path.home() / '.local/state/z0int/capability-index.sqlite3'
QUERY_BUDGET_MS = 180
STOP = frozenset('a an and are as at be before by can do every for from how i in is it me of on or our please that the this to use we what when which with you your'.split())


def query(index: Path, text: str, limit: int = 12) -> list[dict]:
    """Search FTS only. The SQLite progress handler bounds database execution."""
    if not isinstance(text, str) or not text.strip():
        return []
    tokens = list(dict.fromkeys(word.lower() for word in re.findall(r'[\w-]+', text) if len(word) > 1 and word.lower() not in STOP))[:64]
    if not tokens:
        return []
    started = time.monotonic()
    deadline = started + QUERY_BUDGET_MS / 1000
    with closing(sqlite3.connect(Path(index).expanduser().resolve().as_uri() + '?mode=ro', uri=True, timeout=0.01)) as db:
        db.row_factory = sqlite3.Row
        db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 100)
        expression = ' OR '.join('"' + token.replace('"', '""') + '"' for token in tokens)
        rows = db.execute('SELECT method,source,status,wired,kind,text FROM entries_fts WHERE entries_fts MATCH ? ORDER BY bm25(entries_fts),source,method LIMIT ?', (expression, max(0, min(12, limit)))).fetchall()
    if time.monotonic() >= deadline:
        raise TimeoutError('Capability query exceeded 180 ms')
    return [dict(row) for row in rows]


def block(hits: list[dict]) -> str:
    """At most twelve sourced evidence lines. Escape markup and embedded newlines."""
    def clean(value):
        return re.sub(r'[\r\n\t]+', ' ', str(value)).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    lines = []
    for row in hits[:12]:
        # Each atomic source entry is one line, not an arbitrary document excerpt.
        lines.append(f"{clean(row['method'])}: {clean(row['text'])} [source: {clean(row['source'])}; {clean(row['status'])}; wired: {clean(row['wired'])}]")
    return '<z0-lookup>\n' + '\n'.join(lines) + '\n</z0-lookup>'


def consult(index: Path, text: str, turn: str, *, session: str, source_hash: str, action: str) -> dict:
    started = time.monotonic()
    result = {'schema': 'z0.consultation.v1', 'query': text, 'hits': [],
              'ts': dt.datetime.now(dt.timezone.utc).isoformat(), 'turn': turn,
              'session': session, 'sourceHash': source_hash, 'action': action,
              'index': str(index), 'status': 'unavailable'}
    try:
        hits = query(index, text)
        stat = index.stat()
        result.update(hits=hits, status='ok', index_identity={'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns}, block=block(hits))
    except (OSError, sqlite3.Error, TimeoutError, ValueError) as exc:
        result['error'] = str(exc)
        result['block'] = block([])
    result['latency_ms'] = round((time.monotonic() - started) * 1000, 3)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--index', type=Path, default=DEFAULT_INDEX)
    sub = parser.add_subparsers(dest='command', required=True)
    search = sub.add_parser('query')
    search.add_argument('text')
    search.add_argument('--json', action='store_true')
    receipt = sub.add_parser('consult')
    receipt.add_argument('text')
    receipt.add_argument('--turn', required=True)
    receipt.add_argument('--session', required=True)
    receipt.add_argument('--source-hash', required=True)
    receipt.add_argument('--action', required=True)
    args = parser.parse_args(argv)
    if args.command == 'query':
        hits = query(args.index, args.text)
        print(json.dumps(hits, sort_keys=True) if args.json else block(hits))
        return 0
    result = consult(args.index, args.text, args.turn, session=args.session, source_hash=args.source_hash, action=args.action)
    print(json.dumps(result, sort_keys=True))
    return 0 if result['status'] == 'ok' else 4


if __name__ == '__main__':
    raise SystemExit(main())
