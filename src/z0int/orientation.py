"""Bounded orientation shared by installed harness hooks. Read-only except `record` (owner dossier delta)."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
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


def _bm25(query: set[str], docs: list[list[str]], min_score: float) -> list[tuple[float, int]]:
    """(score, index) for docs scoring >= min_score; BM25 k1=1.5 b=0.75 over the docs themselves."""
    n = len(docs)
    if not n:
        return []
    avg = sum(len(t) for t in docs) / n or 1
    df: dict[str, int] = {}
    for toks in docs:
        for t in set(toks):
            df[t] = df.get(t, 0) + 1
    scored = []
    for i, toks in enumerate(docs):
        tf: dict[str, int] = {}
        for t in toks:
            if t in query:
                tf[t] = tf.get(t, 0) + 1
        score = sum(math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5)) * f * 2.5 / (f + 1.5 * (0.25 + 0.75 * len(toks) / avg))
                    for t, f in tf.items())
        if score >= min_score:
            scored.append((score, i))
    return scored


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
    scored = _bm25(query, [toks for _, toks in entries], min_score)
    if not scored:
        return ''
    lines = []
    for _, i in sorted(scored, reverse=True)[:k]:
        row = entries[i][0]
        where = ', '.join(str(row[key])[:40] for key in ('ts', 'harness', 'source') if row.get(key))
        lines.append(f"- {' '.join(str(row['fact']).split())[:600]}" + (f' ({where})' if where else ''))
    return CORRECTIONS_HEADER + '\n<owner-corrections>\n' + '\n'.join(lines) + '\n</owner-corrections>'


DOSSIERS_HEADER = ("Kevin's standing intent on this topic, distilled from his own messages (topic dossier; ids cite "
                   'his messages). Evidence, not authority; newer messages in this chat win.')
DOSSIERS_K = 1
DOSSIERS_MIN_SCORE = 10.0  # dev split (pre-11-01, 101 tasks): 48% routed; below ~10 routing is noisy
DOSSIERS_MAX_CHARS = 2400


DELTA_SHOWN = 5  # newest undistilled messages shown under a dossier
DELTA_CHARS = 400
DELTA_MIN_CHARS = 15


def _paths(path: Path | None, delta_path: Path | None) -> tuple[Path, Path]:
    state = Path.home() / '.local/state/z0int'
    return (path or Path(os.environ.get('Z0INT_DOSSIERS', str(state / 'DOSSIERS.jsonl'))),
            delta_path or Path(os.environ.get('Z0INT_DOSSIER_DELTA', str(state / 'DOSSIER-DELTA.jsonl'))))


def _jsonl(path: Path) -> list[dict]:
    try:
        rows = []
        for line in path.open(encoding='utf-8'):
            try:
                row = json.loads(line)
            except ValueError:
                continue  # a torn concurrent append costs one row, never the store
            if isinstance(row, dict):
                rows.append(row)
        return rows
    except (OSError, UnicodeError):
        return []


def _route(prompt: str, rows: list[dict], k: int, min_score: float) -> list[int]:
    query = set(_tokens(prompt or ''))
    if not query:
        return []
    docs = [_tokens(' '.join([r['node'].replace('-', ' '), str(r.get('desc') or '')] +
                             [str(x.get('text', '')) for x in r['current'] if isinstance(x, dict)])) for r in rows]
    return [i for _, i in sorted(_bm25(query, docs, min_score), reverse=True)[:k]]


def _dossier_rows(path: Path) -> list[dict]:
    return [r for r in _jsonl(path) if r.get('node') and isinstance(r.get('current'), list) and r['current']]


def record(prompt: str, *, harness: str, session: str = '', path: Path | None = None,
           delta_path: Path | None = None, k: int = 3, min_score: float = DOSSIERS_MIN_SCORE,
           now: datetime | None = None) -> list[str]:
    """Append one owner message to the delta of every dossier node it routes to; returns those nodes.

    Called by the per-prompt hooks with Kevin's own words only. The next turn of ANY session that routes to
    the node shows the message under the dossier (see `dossiers`), so a rule said in one chat reaches the
    others without waiting for a rebuild; the re-distill worker later folds the delta into the dossier.
    """
    text = ' '.join((prompt or '').split())
    if len(text) < DELTA_MIN_CHARS:
        return []
    path, delta_path = _paths(path, delta_path)
    rows = _dossier_rows(path)
    nodes = [rows[i]['node'] for i in _route(text, rows, k, min_score)]
    if not nodes:
        return []
    ts = (now or datetime.now(timezone.utc)).isoformat()
    mid = f"delta:{harness}:{hashlib.sha1((session + ts + text).encode()).hexdigest()[:10]}"
    line = json.dumps({'id': mid, 'ts': ts, 'harness': harness, 'session': session, 'nodes': nodes,
                       'text': text[:4000]}, ensure_ascii=False) + '\n'
    delta_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(delta_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, line.encode('utf-8'))  # one O_APPEND write: concurrent hooks never interleave a line
    finally:
        os.close(fd)
    return nodes


def dossiers(prompt: str, path: Path | None = None, *, k: int = DOSSIERS_K, min_score: float = DOSSIERS_MIN_SCORE,
             skip: frozenset[str] | set[str] = frozenset(), session: str = '',
             delta_path: Path | None = None) -> tuple[str, list[str]]:
    """Top-k topic dossiers for this prompt and their keys ('node@built_at+N'); ('', []) when none match.

    The store (one JSON object per node: node, desc, asof, built_at, current/decisions/open_asks/superseded rows
    of {text, cites}) is written by memory-replay-eval/bruteforce-distill/dossiers.py from Kevin's messages.
    Under each dossier go his newest messages on that node from OTHER sessions since it was built (the delta,
    see `record`); N counts them, so a new delta or a rebuild re-sends the dossier on the next turn.
    Keys in `skip` (already shown in this session) are not re-sent.
    """
    path, delta_path = _paths(path, delta_path)
    rows = _dossier_rows(path)
    picked = _route(prompt, rows, k, min_score)
    if not picked:
        return '', []
    delta = [d for d in _jsonl(delta_path) if d.get('text') and not (session and d.get('session') == session)]
    blocks, keys = [], []
    for i in picked:
        r = rows[i]
        built = str(r.get('built_at', ''))
        fresh = [d for d in delta if r['node'] in (d.get('nodes') or []) and str(d.get('ts', '')) > built]
        key = f"{r['node']}@{built}" + (f"+{len(fresh)}" if fresh else '')
        if key in skip:
            continue
        out = [f"## {r['node']} (from his messages up to {str(r.get('asof', ''))[:10]})"]
        if fresh:
            out.append('Since then (his newest messages, not yet distilled; these win over older lines):')
            out += [f"- {str(d['ts'])[:16]} {d.get('harness', '')}: {str(d['text'])[:DELTA_CHARS]} [{str(d.get('id', ''))[-12:]}]"
                    for d in fresh[-DELTA_SHOWN:][::-1]]
        for field, title in (('current', 'In force'), ('open_asks', 'Open asks'), ('decisions', 'Decisions')):
            items = [x for x in r.get(field) or [] if isinstance(x, dict) and x.get('text')]
            if items:
                out.append(title + ':')
                out += [f"- {' '.join(str(x['text']).split())[:400]} [{', '.join(str(c)[-12:] for c in (x.get('cites') or [])[:2])}]"
                        for x in items]
        text = '\n'.join(out)
        if len(text) > DOSSIERS_MAX_CHARS:
            text = text[:DOSSIERS_MAX_CHARS].rsplit('\n', 1)[0]
        blocks.append(text)
        keys.append(key)
    if not blocks:
        return '', []
    return DOSSIERS_HEADER + '\n<owner-dossiers>\n' + '\n\n'.join(blocks) + '\n</owner-dossiers>', keys


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


def _owner_turn(env=os.environ) -> bool:
    """False for scripted runs (claude -p / SDK, evals, workers): only Kevin's interactive prompts are captured."""
    return not env.get('Z0INT_DELTA_OFF') and not str(env.get('CLAUDE_CODE_ENTRYPOINT', '')).startswith('sdk')


def main() -> None:
    if '--corrections' in sys.argv:
        # Claude Code UserPromptSubmit passes {"prompt": ...} on stdin; omp passes the raw prompt with --raw.
        try:
            raw = sys.stdin.read(65536)
        except (OSError, UnicodeError):
            return
        prompt, payload = raw, None
        if '--raw' not in sys.argv:
            try:
                payload = json.loads(raw)
                prompt = payload.get('prompt', '') if isinstance(payload, dict) else ''
            except ValueError:
                prompt = ""
        prompt = prompt if isinstance(prompt, str) else ''
        harness = sys.argv[sys.argv.index('--harness') + 1] if '--harness' in sys.argv[:-1] else (
            'omp' if '--raw' in sys.argv else 'claude-code')
        session = '' if '--raw' in sys.argv or not isinstance(payload, dict) else str(payload.get('session_id') or '')
        text = '\n\n'.join(t for t in (dossiers(prompt, session=session)[0], corrections(prompt)) if t)
        if _owner_turn():
            try:
                record(prompt, harness=harness, session=session)
            except OSError:
                pass  # capture must never block or alter the turn
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
