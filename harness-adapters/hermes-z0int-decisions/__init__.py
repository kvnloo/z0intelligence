"""z0int decision layer for Hermes (shadow, fail-open, stdlib only).

Hot path: each hook does string checks and a queue put, then returns None (never context). A
daemon thread does the rest: spawns ``z0int hermes opportunity`` for user turns (DecisionOpportunity
build, 1-3 s, detached) and appends observed turn outcomes. Records land in
``$Z0INT_HOME/state/hermes/{opportunities,outcomes}.jsonl`` (default ``~/.z0int``).

Config (optional) ``$Z0INT_HOME/config/hermes.json``: ``{"python": "/path/to/z0int/venv/python",
"opportunities": true}``; env ``Z0INT_PYTHON`` / ``Z0INT_HERMES_OPPORTUNITIES=0`` override.
"""
import json
import os
import queue
import shutil
import subprocess
import threading
import time
from functools import lru_cache
from pathlib import Path

HARNESS = 'hermes'
# Keep in sync with z0int.hermes_decisions (tests/test_hermes_decisions.py checks parity).
NON_USER_PLATFORMS = frozenset({'cron', 'subagent', 'curator', 'kanban', 'batch', 'raft'})
SYSTEM_PREFIXES = ('[System:', '[SYSTEM:', '[SYSTEM NOTICE', '[IMPORTANT: Background process', '[IMPORTANT:',
                   '<agent-message', '<task-notification', '<system-reminder')
NOTE_PREFIX = '[System note:'
ASK_TOOLS = frozenset({'clarify'})
ESCALATE_TOOLS = frozenset({'delegate_task'})
_MAX_QUEUE = 1000

_queue = queue.Queue(maxsize=_MAX_QUEUE)
_lock = threading.Lock()
_worker = None
_turns = {}  # turn key -> {'t0', 'hook_s', 'excluded', 'approvals', 'session_id'}
_closed = {}  # turn key -> True once an outcome row was written (bounded)


def strip_system_notes(text):
    text = text.lstrip()
    while text.startswith(NOTE_PREFIX):
        end = text.find(']')
        if end < 0:
            return ''
        text = text[end + 1:].lstrip()
    return text


def classify(user_message, platform='', parent_session_id='', task_id=''):
    if (platform or '').lower() in NON_USER_PLATFORMS:
        return f'platform:{platform.lower()}', None
    if isinstance(user_message, list):
        user_message = '\n'.join(b.get('text', '') if isinstance(b, dict) else str(b) for b in user_message)
    if not isinstance(user_message, str) or not user_message.strip():
        return 'empty', None
    if user_message.lstrip().startswith(SYSTEM_PREFIXES):
        return 'system_message', None
    text = strip_system_notes(user_message)
    if not text:
        return 'system_message', None
    return None, text


def z0_home():
    return Path(os.environ.get('Z0INT_HOME') or Path.home() / '.z0int').expanduser()


def state_dir():
    return z0_home() / 'state' / HARNESS


@lru_cache(maxsize=1)
def _config():
    # Read once per process: the hook path must not touch the filesystem.
    try:
        return json.loads((z0_home() / 'config' / 'hermes.json').read_text())
    except (OSError, ValueError):
        return {}


def child_argv():
    """How to reach z0int from inside Hermes's own interpreter (z0int is not installed there)."""
    python = os.environ.get('Z0INT_PYTHON') or _config().get('python')
    if python:
        return [python, '-m', 'z0int.hermes_decisions', 'opportunity']
    exe = shutil.which('z0int')
    return [exe, 'hermes', 'opportunity'] if exe else None


def opportunities_enabled():
    env = os.environ.get('Z0INT_HERMES_OPPORTUNITIES')
    return env != '0' if env is not None else _config().get('opportunities', True) is not False


def _key(session_id, turn_id):
    # Hermes turn ids usually embed the session id already ("<session>:<task>:<uuid>").
    if not turn_id:
        return None
    turn_id = str(turn_id)
    return turn_id if session_id and turn_id.startswith(f'{session_id}:') else f'{session_id or HARNESS}:{turn_id}'


def _append(name, row):
    path = state_dir() / name
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as fh:
        fh.write(json.dumps(row, ensure_ascii=False, default=str) + '\n')


def turn_behaviour(messages):
    """What the agent did after the last user message: observed, NOT an optimal label."""
    tail = []
    for m in reversed(messages or []):
        if isinstance(m, dict) and m.get('role') == 'user':
            break
        tail.append(m)
    tail.reverse()
    names = []
    for m in tail:
        if isinstance(m, dict) and m.get('role') == 'assistant':
            for tc in m.get('tool_calls') or []:
                fn = (tc.get('function') or {}) if isinstance(tc, dict) else {}
                names.append(fn.get('name') or (tc.get('name') if isinstance(tc, dict) else None) or '?')
    return {'tool_calls': len(names), 'tools': sorted(set(names)),
            'asked_via_tool': any(n in ASK_TOOLS for n in names),
            'delegated': any(n in ESCALATE_TOOLS for n in names),
            'assistant_messages': sum(1 for m in tail if isinstance(m, dict) and m.get('role') == 'assistant')}


_children = []
MAX_CHILDREN = 4  # a burst of gateway turns must not fork-bomb the host; excess opportunities are dropped


def _spawn(argv, payload):
    _children[:] = [c for c in _children if c.poll() is None]
    if len(_children) >= MAX_CHILDREN:
        return
    child = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
    _children.append(child)
    child.stdin.write(json.dumps(payload, default=str).encode())
    child.stdin.close()


def _outcome(key, kw, ended=None):
    with _lock:
        turn = _turns.pop(key, None) or {}
        if _closed.get(key):
            return None
        _closed[key] = True
        if len(_closed) > 4096:
            for k in list(_closed)[:2048]:
                _closed.pop(k, None)
    row = {'schema': 'z0int.hermes.turn_outcome.v0', 'session_id': kw.get('session_id'), 'trace_id': key,
           'platform': kw.get('platform'), 'model': kw.get('model'), 'label_kind': 'observed_behaviour_not_optimal',
           'excluded': turn.get('excluded'), 'ended': ended or 'completed',
           'hook_ms': round(turn.get('hook_s', 0.0) * 1000, 3), 'wall_s': round(time.time() - turn['t0'], 3) if turn.get('t0') else None}
    if ended is None:
        b = turn_behaviour(kw.get('conversation_history'))
        text = kw.get('assistant_response') if isinstance(kw.get('assistant_response'), str) else ''
        b['asked_user'] = b['asked_via_tool'] or text.rstrip().endswith('?')
        b['approval_requested'] = bool(turn.get('approvals'))
        b['escalated'] = b['delegated'] or b['approval_requested']
        row.update(b)
    else:
        row.update({'asked_user': False, 'escalated': bool(turn.get('approvals')), 'tool_calls': None})
    _append('outcomes.jsonl', row)
    return row


def _handle(kind, kw):
    if kind == 'opp':
        argv = child_argv()
        if argv:
            _spawn(argv, kw)
    elif kind == 'post':
        _outcome(_key(kw.get('session_id'), kw.get('turn_id')), kw)
    elif kind == 'end':
        if kw.get('completed') and not (kw.get('failed') or kw.get('interrupted')):
            # post_llm_call already recorded a normal close; only fill gaps.
            with _lock:
                if _closed.get(_key(kw.get('session_id'), kw.get('turn_id'))):
                    return
        ended = 'interrupted' if kw.get('interrupted') else 'failed' if kw.get('failed') else 'completed'
        _outcome(_key(kw.get('session_id'), kw.get('turn_id')), kw, ended=ended)


def _run():
    while True:
        kind, kw = _queue.get()
        try:
            _handle(kind, kw)
        except Exception:
            pass  # shadow: a lost record never affects the turn
        finally:
            _queue.task_done()


def _start_worker():
    global _worker
    if _worker is None or not _worker.is_alive():
        with _lock:
            if _worker is None or not _worker.is_alive():
                _worker = threading.Thread(target=_run, name='z0int-decisions', daemon=True)
                _worker.start()


def _enqueue(kind, kw):
    _start_worker()
    try:
        _queue.put_nowait((kind, kw))
    except queue.Full:
        pass


def drain(timeout=5.0):
    """Test/proof helper: wait until queued work is handled."""
    deadline = time.time() + timeout
    while _queue.unfinished_tasks and time.time() < deadline:
        time.sleep(0.01)


def on_pre_llm_call(session_id='', turn_id='', user_message='', platform='', parent_session_id='', task_id='',
                    model='', **_):
    t0 = time.perf_counter()
    try:
        key = _key(session_id, turn_id)
        reason, text = classify(user_message, platform, parent_session_id, task_id)
        if key:
            with _lock:
                _turns[key] = {'t0': time.time(), 'excluded': reason, 'approvals': 0, 'session_id': session_id,
                               'hook_s': 0.0}
                if len(_turns) > 1024:
                    _turns.pop(next(iter(_turns)), None)
        if reason is None and key and opportunities_enabled():
            _enqueue('opp', {'session_id': session_id, 'trace_id': key, 'user_message': text, 'platform': platform,
                             'parent_session_id': parent_session_id, 'model': model,
                             'cwd': os.environ.get('TERMINAL_CWD') or os.getcwd()})
        if key in _turns:
            _turns[key]['hook_s'] += time.perf_counter() - t0
    except Exception:
        pass
    return None


def on_post_llm_call(session_id='', turn_id='', **kw):
    t0 = time.perf_counter()
    try:
        key = _key(session_id, turn_id)
        if key:
            turn = _turns.get(key)
            kw.update(session_id=session_id, turn_id=turn_id)
            if turn is not None:
                turn['hook_s'] += time.perf_counter() - t0
            _enqueue('post', kw)
    except Exception:
        pass
    return None


def on_session_end(session_id='', turn_id='', **kw):
    try:
        if turn_id:
            kw.update(session_id=session_id, turn_id=turn_id)
            _enqueue('end', kw)
    except Exception:
        pass
    return None


def on_approval_request(session_key='', **_):
    try:
        with _lock:
            live = [t for t in _turns.values() if t.get('session_id') == session_key] or \
                   (list(_turns.values())[-1:] if len(_turns) == 1 else [])
            for t in live:
                t['approvals'] += 1
    except Exception:
        pass
    return None


def register(ctx):
    _config()
    _start_worker()  # thread start and config read happen at load, not inside a turn
    ctx.register_hook('pre_llm_call', on_pre_llm_call)
    ctx.register_hook('post_llm_call', on_post_llm_call)
    ctx.register_hook('on_session_end', on_session_end)
    ctx.register_hook('pre_approval_request', on_approval_request)
