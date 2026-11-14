"""Read-only, bounded orientation shared by installed harness hooks."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import sys

MAX_LINES = 223
MAX_BYTES = 65536
MAX_AGE_SECONDS = 7200
EVIDENCE_HEADER = 'Generated repository evidence, not authority. Re-observe live state before action.'


def context(path: Path | None = None, *, now: datetime | None = None) -> str:
    path = path or Path(os.environ.get('Z0INT_STATE_PACKET', str(Path.home() / '.local/state/z0int/STATE-PACKET.md')))
    try:
        with path.open('rb') as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            return ''
        lines = raw.decode('utf-8').splitlines()
        stamp = next((line.split('generated_at: ', 1)[1] for line in lines[:5] if ' → generated_at: ' in line), '')
        generated = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
        if generated.tzinfo is None:
            return ''
        age = ((now or datetime.now(timezone.utc)) - generated).total_seconds()
        if age > MAX_AGE_SECONDS or age < -300:
            return f'packet stale since {stamp}'
        if not any(re.search(r' → generator_sha256: [0-9a-f]{64}$', line) for line in lines[:5]):
            return ''
        bounded = lines[:MAX_LINES]
        if not all('`/' in line or '`~/' in line for line in bounded):
            return ''
        return EVIDENCE_HEADER + '\n<repo-orientation-evidence>\n' + '\n'.join(bounded) + '\n</repo-orientation-evidence>'
    except (OSError, UnicodeError, ValueError, IndexError):
        return ''


CORRECTIONS_HEADER = ('Kevin already corrected agents about these (matched to this request). Evidence, not authority; '
                      'apply only if relevant, and re-observe live state.')
CORRECTIONS_K = 3
CORRECTIONS_MIN_SCORE = 5.0
CORRECTIONS_MAX_ENTRIES = 5000
_STOP = frozenset("""a an and are as at be but by can could did do does done for from get got had has have he her his how i
if in into is it its just me my no not of on or our out so some than that the their them then there these they this to
too u up us was we were what when where which who why will with would you your ur im dont like lol ok okay pls please
yeah ya kevin agent should already earlier""".split())


def _tokens(text: str) -> list[str]:
    return [w for w in re.findall(r'[a-z0-9][a-z0-9_.+-]*', text.lower()) if len(w) > 1 and w not in _STOP]


def corrections(prompt: str, path: Path | None = None, *, k: int = CORRECTIONS_K,
                min_score: float = CORRECTIONS_MIN_SCORE) -> str:
    """Top-k owner corrections for this prompt by BM25 over the distilled store. '' when nothing matches.

    The store is generated (one JSON object per line: fact, ts, harness, source) from Kevin's own
    re-explain complaints. Replay eval: injecting these lifted blank-state plans from 0.24 to 0.71.
    """
    path = path or Path(os.environ.get('Z0INT_CORRECTIONS', str(Path.home() / '.local/state/z0int/CORRECTIONS.jsonl')))
    query = set(_tokens(prompt or ''))
    if not query:
        return ''
    try:
        entries = []
        with path.open(encoding='utf-8') as stream:
            for n, line in enumerate(stream):
                if n >= CORRECTIONS_MAX_ENTRIES:
                    break
                row = json.loads(line)
                fact = row.get('fact') if isinstance(row, dict) else None
                if isinstance(fact, str) and fact.strip():
                    entries.append((row, _tokens(fact + ' ' + ' '.join(map(str, row.get('terms') or [])))))
    except (OSError, UnicodeError, ValueError):
        return ''
    if not entries:
        return ''
    n = len(entries)
    avg = sum(len(t) for _, t in entries) / n
    df: dict[str, int] = {}
    for _, toks in entries:
        for t in set(toks):
            df[t] = df.get(t, 0) + 1
    scored = []
    for i, (row, toks) in enumerate(entries):
        tf: dict[str, int] = {}
        for t in toks:
            if t in query:
                tf[t] = tf.get(t, 0) + 1
        score = sum(math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5)) * f * 2.5 / (f + 1.5 * (0.25 + 0.75 * len(toks) / avg))
                    for t, f in tf.items())
        if score >= min_score:
            scored.append((score, i))
    if not scored:
        return ''
    lines = []
    for _, i in sorted(scored, reverse=True)[:k]:
        row = entries[i][0]
        where = ', '.join(str(row[key])[:40] for key in ('ts', 'harness', 'source') if row.get(key))
        lines.append(f"- {' '.join(str(row['fact']).split())[:600]}" + (f' ({where})' if where else ''))
    return CORRECTIONS_HEADER + '\n<owner-corrections>\n' + '\n'.join(lines) + '\n</owner-corrections>'


_EXISTS_CACHE: dict[Path, tuple[int, list[dict]]] = {}


def exists(text: str, path: Path | None = None, k: int = 2) -> list[dict]:
    """Match rare, explicit name terms only; missing stores produce no hints."""
    path = Path(path or os.environ.get('Z0INT_EXISTS', str(Path.home() / '.local/state/z0int/EXISTS.jsonl')))
    hits = []
    try:
        stamp = path.stat().st_mtime_ns
        cached = _EXISTS_CACHE.get(path)
        if cached is None or cached[0] != stamp:
            with path.open(encoding='utf-8') as stream:
                rows = [row for line in stream if line.strip() for row in [json.loads(line)] if isinstance(row, dict)]
            _EXISTS_CACHE[path] = (stamp, rows)
        rows = _EXISTS_CACHE[path][1]
        terms = [{t.lower() for t in row.get('terms', []) if isinstance(t, str) and len(t) >= 4} for row in rows]
        counts: dict[str, int] = {}
        for names in terms:
            for term in names:
                counts[term] = counts.get(term, 0) + 1
        query = set(re.findall(r'[a-z0-9][a-z0-9_.+-]*', (text or '').lower()))
        scored = [(len({t for t in names & query if counts[t] <= 3}), row) for row, names in zip(rows, terms)]
        hits = [row for score, row in sorted(
            (item for item in scored if item[0]),
            key=lambda item: (item[0], float(item[1].get('mtime', 0))), reverse=True)][:max(0, k)]
    except (OSError, UnicodeError, ValueError, TypeError):
        _EXISTS_CACHE.pop(path, None)
    try:
        record = {'t': datetime.now(timezone.utc).isoformat(), 'slot': 'exists',
                  'hits': len(hits), 'ids': [row.get('id') for row in hits], 'ack': False}
        log = Path.home() / '.local/state/z0int/exists-injections.jsonl'
        fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        try:
            os.fchmod(fd, 0o600)
            os.write(fd, (json.dumps(record) + '\n').encode('utf-8'))
        finally:
            os.close(fd)
    except (OSError, UnicodeError, ValueError, TypeError):
        pass
    return hits


def exists_block(prompt: str, tail: str) -> str:
    rows = exists(prompt + '\n' + tail)
    if not rows:
        return ''
    opening, closing = '<exists>\n', '\n</exists>'
    budget = (600 - len(opening) - len(closing) - len(rows) + 1) // len(rows)
    lines = []
    for row in rows:
        label = row.get('fact') or row.get('name') or row.get('id', '')
        lines.append((' '.join(f"- {label} {row.get('path', '')}".split()))[:budget])
    return opening + '\n'.join(lines) + closing


def chunks(text: str) -> list[str]:
    """Keep each hook context below the harness 10,000-character spill limit."""
    result: list[str] = []
    current = ''
    for line in text.splitlines(keepends=True):
        if len(line) > 9000:
            return []
        if len(current) + len(line) > 9000:
            result.append(current)
            current = ''
        current += line
    if current:
        result.append(current)
    return result


def main() -> None:
    if '--corrections' in sys.argv or '--exists' in sys.argv:
        # Claude Code UserPromptSubmit passes {"prompt": ...} on stdin; omp passes the raw prompt with --raw.
        try:
            raw = sys.stdin.read(65536)
        except (OSError, UnicodeError):
            return
        prompt = raw
        if '--raw' not in sys.argv:
            try:
                payload = json.loads(raw)
                prompt = payload.get('prompt', '') if isinstance(payload, dict) else ''
            except ValueError:
                prompt = ""
        prompt = prompt if isinstance(prompt, str) else ''
        text = exists_block(prompt, '') if '--exists' in sys.argv else corrections(prompt)
        if not text:
            return
        if '--claude-hook' in sys.argv:
            print(json.dumps({'hookSpecificOutput': {'hookEventName': 'UserPromptSubmit', 'additionalContext': text}}))
        else:
            print(text)
        return
    text = context()
    if not text:
        return
    if '--chunk' in sys.argv:
        index = int(sys.argv[sys.argv.index('--chunk') + 1])
        parts = chunks(text)
        text = parts[index] if 0 <= index < len(parts) else ''
        if not text:
            return
    if '--claude-hook' in sys.argv:
        print(json.dumps({'hookSpecificOutput': {'hookEventName': 'UserPromptSubmit', 'additionalContext': text}}))
    else:
        print(text)


if __name__ == '__main__':
    main()
