"""Cross-session awareness for one Hermes profile, injected per turn (pre_llm_call).

Why: memory and the skills index enter the system prompt as a FROZEN snapshot when a session starts,
and nothing tells a session what its siblings are doing. Kevin keeps several T3 threads open for hours,
so a rule he set in thread A never reaches thread B, and he re-specifies it (audit 2026-11-03).

Each turn this adds at most three short blocks, each only when it has something NEW for this session:
  1. <sop-updates>      memory/USER entries and skills added or changed since this session started
  2. <sibling-sessions> other live sessions in this profile: T3 title + Kevin's latest ask (redacted)
  3. <owner-corrections> top-3 past corrections matching this message (z0intelligence#239 reader)
  4. <skill-cards>       top-2 skill cards (When -> Use), same reader over CARDS.jsonl; OFF unless
                         plugins.entries.owner-corrections.settings.skill_cards is true (ROUTER-DESIGN step 1)
Injected context persists in the transcript (api_content sidecar), so each item is sent once per session.
Read-only on every source. Any failure injects nothing; never raises into the turn.
"""
import hashlib
import importlib.util
import json
import logging
import os
import re
import sqlite3
import time
from pathlib import Path

_log = logging.getLogger(__name__)
HOME = Path.home()
PROFILE = Path(__file__).resolve().parents[2]  # <profile>/plugins/owner-corrections/__init__.py (HERMES_HOME is the root in the gateway)
STATE_DB = PROFILE / 'state.db'
MEMORIES = [PROFILE / 'memories/MEMORY.md', PROFILE / 'memories/USER.md']
SKILLS = PROFILE / 'skills'
T3_DB = HOME / '.t3/userdata/statev2.sqlite'
STATE = PROFILE / 'cache/owner-context'
READER = HOME / '.local/lib/z0-state-packet/current/src/z0int/orientation.py'
CARDS = HOME / '.local/state/z0int/CARDS.jsonl'  # best-way-map refresh.sh installs render_map.py's cards.jsonl here
CARDS_K = 2
CARDS_HEADER = ('Skill cards matching this request (When -> Use). Load the named skill before generic tools '
                'when the When fits; skip a card whose "not when" applies:')

SIBLING_WINDOW_S = 6 * 3600
SIBLING_REFRESH_S = 10 * 60
MAX_SIBLINGS = 10
MAX_SKILLS = 8
OWNER_SOURCES = ('local', 'acp', 'gui', 'cli', 'tui', 'telegram', 'discord')
NOT_ASKS = re.compile(r'^\s*(Update on pull request|Delegated task|Heads-up from|\[|<)', re.I)
SECRET = re.compile(r'(?i)((?:code|token|key|secret|state|password|pw)=)[^&\s]+|[A-Za-z0-9_\-#+/=]{28,}')
# Kevin stating a rule meant to outlive this chat. The live agent saves it; sop_updates carries it to siblings.
STANDING = re.compile(
    r"(?i)\b(from now on|going forward|in the future|for the future|next time|every ?time|always|never|"
    r"don'?t ever|stop (using|doing)|make sure (to|that|we|u|you)|remember (to|that)|codify|write a skill|"
    r"(have|having) to (explain|respecify|repeat|tell)|i (already|alr) (told|said|explained)|"
    r"standing rule|by default|should (always|never|just know))\b")
SAVE_NUDGE = ('<standing-rule-check>This message may state a standing rule, preference or workflow. If it does '
              'and it is not already in memory or a skill, save it now (memory for one-line facts, skill_manage '
              'for procedures) so every other session of this profile receives it on its next turn. Say in one '
              'line what you saved.</standing-rule-check>')

_orientation = None
_ctx = None  # PluginContext from register(); settings are read through it on every turn


def _setting(key, default):
    try:
        return _ctx.get_config(key, default) if _ctx is not None else default
    except Exception:
        return default


def skill_cards(prompt, path=None, k=CARDS_K):
    """Top-k skill cards for this prompt: the corrections reader over the cards corpus, re-tagged."""
    text = _reader().corrections(prompt, path=Path(path or CARDS), k=k)
    if '<owner-corrections>\n' not in text:
        return ''
    body = text.split('<owner-corrections>\n', 1)[1].rsplit('\n</owner-corrections>', 1)[0]
    return CARDS_HEADER + '\n<skill-cards>\n' + body + '\n</skill-cards>'


def _reader():
    global _orientation
    if _orientation is None:
        spec = importlib.util.spec_from_file_location('z0_owner_corrections_orientation', READER)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _orientation = mod
    return _orientation


def _text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return _text(value.get('content') if 'content' in value else value.get('text'))
    if isinstance(value, (list, tuple)):
        return ' '.join(_text(p) for p in value)
    return ''


def owner_text(raw):
    """Kevin's own words from a stored user row: strip T3/plugin wrappers, keep <user_request> if present."""
    t = raw or ''
    m = re.search(r'<user_request>(.*?)</user_request>', t, re.S)
    if m:
        t = m.group(1)
    t = re.sub(r'<(runtime_info|t3_code_instructions|pull_request_linking|owner-corrections|sop-updates|'
               r'sibling-sessions|standing-rule-check|skill-cards)>.*?</\1>', '', t, flags=re.S)
    t = re.sub(r'(Kevin already corrected agents|Saved in other sessions of this profile|Other live sessions in '
               r'this profile|Skill cards matching this request)[^\n]*\n?', '', t)
    return ' '.join(t.split())


def redact(t):
    return SECRET.sub(lambda m: (m.group(1) or '') + '[redacted]', t)


def _ro(path):
    con = sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=2)
    con.execute('pragma busy_timeout=2000')
    return con


def _load(path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def _save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f'.{os.getpid()}.tmp')
    tmp.write_text(json.dumps(data))
    os.replace(tmp, path)


def _session_start(sid):
    try:
        with _ro(STATE_DB) as con:
            row = con.execute('select started_at from sessions where id=?', (sid,)).fetchone()
        return float(row[0]) if row else time.time()
    except sqlite3.Error:
        return time.time()


# ---- 1. SOP updates ---------------------------------------------------------------------------

def memory_ledger(now=None):
    """{hash: {first_seen, text}} for every memory entry; entries present at first run count as old (0)."""
    now = now or time.time()
    path = STATE / 'memory-ledger.json'
    ledger = _load(path, None)
    boot = ledger is None
    ledger = ledger or {}
    changed = boot
    for f in MEMORIES:
        try:
            body = f.read_text()
        except OSError:
            continue
        for entry in (e.strip() for e in body.split('§')):
            if not entry:
                continue
            h = hashlib.sha1(entry.encode()).hexdigest()[:16]
            if h not in ledger:
                ledger[h] = {'first_seen': 0 if boot else now, 'text': entry, 'file': f.name}
                changed = True
    if changed:
        _save(path, ledger)
    return ledger


def _skill_desc(path):
    try:
        head = path.read_text(errors='replace')[:1500]
    except OSError:
        return ''
    m = re.search(r'^description:\s*["\']?(.+?)["\']?\s*$', head, re.M)
    return m.group(1).strip() if m else ''


def skill_events():
    """Diff every changed SKILL.md against the plugin's last snapshot; return all change events.

    Skills are not under git, so the plugin keeps its own snapshot (STATE/skills-snapshot.json). The first
    run only snapshots. Each event: {ts, skill, added: [new non-empty lines], new: bool}.
    """
    snap_path, ev_path = STATE / 'skills-snapshot.json', STATE / 'skill-events.jsonl'
    snap = _load(snap_path, None)
    boot = snap is None
    snap = snap or {}
    events, changed = [], boot
    for p in SKILLS.rglob('SKILL.md'):
        try:
            mt = p.stat().st_mtime
        except OSError:
            continue
        key = str(p)
        old = snap.get(key)
        if old and old['mtime'] == mt:
            continue
        try:
            text = p.read_text(errors='replace')
        except OSError:
            continue
        if not boot and (old is None or old['text'] != text) and (old is None or any(
                ln.strip() and ln not in set(old['text'].splitlines()) for ln in text.splitlines())):
            before = set((old or {}).get('text', '').splitlines())
            added = [ln.strip() for ln in text.splitlines() if ln.strip() and ln not in before
                     and not ln.startswith(('---', 'name:', 'version:'))]
            events.append({'ts': mt, 'skill': p.parent.name, 'new': old is None, 'added': added[:6],
                           'desc': _skill_desc(p)[:160]})
        snap[key] = {'mtime': mt, 'text': text}
        changed = True
    if changed:
        _save(snap_path, snap)
    if events:
        ev_path.parent.mkdir(parents=True, exist_ok=True)
        with ev_path.open('a') as f:
            for e in events:
                f.write(json.dumps(e) + '\n')
    try:
        return [json.loads(l) for l in ev_path.open()]
    except OSError:
        return []


def sop_updates(started, sent):
    lines = []
    for h, e in sorted(memory_ledger().items(), key=lambda kv: kv[1]['first_seen']):
        if e['first_seen'] > started and h not in sent:
            sent.append(h)
            lines.append(f"- [{e['file'].removesuffix('.md')}] {' '.join(e['text'].split())[:400]}")
    latest = {}
    for e in skill_events():
        key = f"{e['skill']}:{int(e['ts'])}"
        if e['ts'] > started and key not in sent:
            sent.append(key)
            prev = latest.get(e['skill'])
            latest[e['skill']] = {**e, 'added': (prev or {}).get('added', []) + e['added'],
                                  'new': e['new'] or bool(prev and prev['new'])}
    shown = sorted(latest.values(), key=lambda e: -e['ts'])
    for e in shown[:MAX_SKILLS]:
        when = time.strftime('%H:%M', time.localtime(e['ts']))
        what = ('new skill: ' + e['desc']) if e['new'] else 'added: ' + ' / '.join(e['added'])[:360]
        lines.append(f"- [skill {e['skill']} {when}] {what} (skill_view before related work)")
    if len(shown) > MAX_SKILLS:
        lines.append('- also changed: ' + ', '.join(e['skill'] for e in shown[MAX_SKILLS:]))
    if not lines:
        return ''
    return ('Saved in other sessions of this profile AFTER this session started (your system-prompt memory and '
            'skill index predate them). Treat as current standing rules and workflows:\n<sop-updates>\n'
            + '\n'.join(lines) + '\n</sop-updates>')


# ---- 2. Sibling sessions ----------------------------------------------------------------------

def t3_titles(since_iso):
    """Hermes session id -> (T3 thread title, is_delegated_child)."""
    out = {}
    if not T3_DB.exists():
        return out
    try:
        with _ro(T3_DB) as con:
            rows = con.execute(
                'select p.payload_json, t.title, t.thread_id from orchestration_v2_projection_provider_threads p '
                'join orchestration_v2_projection_threads t on t.thread_id=p.thread_id '
                'where p.updated_at > ? and t.deleted_at is null', (since_iso,)).fetchall()
    except sqlite3.Error:
        return out
    for payload, title, tid in rows:
        try:
            native = json.loads(payload).get('nativeThreadRef', {}).get('nativeId')
        except ValueError:
            continue
        if native:
            out[native] = (title, 'delegated-task' in (tid or ''))
    return out


def siblings(sid, now=None):
    now = now or time.time()
    since = now - SIBLING_WINDOW_S
    with _ro(STATE_DB) as con:
        rows = con.execute(
            f"""select s.id, coalesce(s.title,''), coalesce(s.cwd,''), m.timestamp, m.content
                from sessions s join messages m on m.session_id=s.id
                where s.id != ? and s.parent_session_id is null
                  and s.source in ({','.join('?' * len(OWNER_SOURCES))})
                  and m.role='user' and m.timestamp > ? order by m.timestamp""",
            (sid, *OWNER_SOURCES, since)).fetchall()
    titles = t3_titles(time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(since)))
    last = {}
    for osid, title, cwd, ts, content in rows:
        text = owner_text(content)
        if not text or NOT_ASKS.match(text):
            continue
        t3 = titles.get(osid)
        if t3 and t3[1]:
            continue  # delegated child thread: a worker, not a sibling conversation
        last[osid] = (ts, (t3[0] if t3 else title) or osid[-8:], cwd, text)
    return last


def sibling_block(sid, st, now=None):
    now = now or time.time()
    if now - st.get('roster_at', 0) < SIBLING_REFRESH_S:
        return ''
    st['roster_at'] = now
    seen = st.setdefault('roster', {})
    fresh = [(v[0], k, v) for k, v in siblings(sid, now).items() if v[0] > seen.get(k, 0)]
    if not fresh:
        return ''
    lines = []
    for ts, osid, (_, title, cwd, text) in sorted(fresh, reverse=True)[:MAX_SIBLINGS]:
        seen[osid] = ts
        where = f' cwd={Path(cwd).name}' if cwd else ''
        lines.append(f"- {time.strftime('%H:%M', time.localtime(ts))} \"{title[:60]}\"{where} "
                     f"(session {osid[-8:]}): {redact(text)[:220]}")
    return ("Other live sessions in this profile (Kevin's latest ask in each, new since you last saw it). "
            'Do not redo their work; open one with session_search if it overlaps:\n<sibling-sessions>\n'
            + '\n'.join(lines) + '\n</sibling-sessions>')


# ---- hook -------------------------------------------------------------------------------------

def owner_context_hook(session_id=None, user_message=None, conversation_history=None, **_):
    parts = []
    sid = session_id or ''
    st_path = STATE / 'sessions' / f'{re.sub(r"[^A-Za-z0-9_.-]", "_", sid) or "none"}.json'
    st = _load(st_path, {})
    if sid:
        try:
            started = st.setdefault('started', _session_start(sid))
            block = sop_updates(started, st.setdefault('sop_sent', []))
            if block:
                parts.append(block)
        except Exception as exc:
            _log.warning('owner-context sop: %s', exc)
        try:
            block = sibling_block(sid, st)
            if block:
                parts.append(block)
        except Exception as exc:
            _log.warning('owner-context siblings: %s', exc)
    try:
        prompt = owner_text(_text(user_message))
        if not prompt.strip() and conversation_history:
            users = [m for m in conversation_history if isinstance(m, dict) and m.get('role') == 'user']
            prompt = owner_text(_text(users[-1])) if users else ''
        if STANDING.search(prompt):
            parts.append(SAVE_NUDGE)
        text = _reader().corrections(prompt) if prompt.strip() else ''
        if text:
            parts.append(text)
        if prompt.strip() and _setting('skill_cards', False) is True:
            text = skill_cards(prompt, _setting('skill_cards_path', None))
            if text:
                parts.append(text)
    except Exception as exc:
        _log.warning('owner-context corrections: %s', exc)
    if sid:
        try:
            _save(st_path, st)
        except OSError as exc:
            _log.warning('owner-context state: %s', exc)
    return {'context': '\n\n'.join(parts)} if parts else None


corrections_hook = owner_context_hook  # name used by the first version and the recall check


def register(ctx):
    global _ctx
    _ctx = ctx
    ctx.register_hook('pre_llm_call', owner_context_hook)
