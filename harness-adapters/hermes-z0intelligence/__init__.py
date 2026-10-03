"""hermes-z0intelligence: the one z0 plugin for Hermes (stdlib only; no Hermes core change).

Capture (``mode: shadow``; default ``off`` registers no capture hook at all): every hook builds a small
content-free job and puts it on a bounded queue, then returns None. One writer thread hands the jobs in order to
the installed z0int (``z0int_python`` / ``Z0INT_PYTHON``) as ``python -m z0int.hermes_capture batch``, a detached
child that writes ``$Z0INT_HOME/state/hermes/{events,opportunities,outcomes,failures,drops}.jsonl`` through the
shared capture core. DecisionOpportunity builds run in that core's bounded pool, never on this thread. Nothing
opens a socket; there is no service host or port setting. A full queue, an unavailable z0int and a bounded
``close()`` are counted into ``drops.jsonl``. ``pre_tool_call`` is never registered.

Automatic (#95): the existing ``pre_llm_call`` routing call runs only while ``$Z0INT_HOME/config/automatic.json``
has ``hermes.enabled: true`` at load; otherwise it is not called and nothing is spawned for it.
"""
import atexit
import fcntl
import json
import logging
import math
import os
import queue
import re
import subprocess
import threading
import time
import uuid
from pathlib import Path

PLUGIN = 'hermes-z0intelligence'
VERSION = '0.2.0'
POLICY_REVISION = f'{PLUGIN}@{VERSION}'
HARNESS = 'hermes'
INSTANCE_ID = uuid.uuid4().hex
MAX_QUEUE = 4096  # jobs waiting for the writer; one H3 flood (3,400 hook calls) fits without a drop
BATCH_MAX = 512
CLOSE_TIMEOUT = 2.0
BATCH_TIMEOUT = 60.0
UNAVAILABLE_RETRY_S = 30.0
MAX_REQUEST_CHARS = 8192  # longer requests are captured as turns but not projected (as bend #385)
OBSERVED = ('pre_api_request', 'post_api_request', 'api_request_error', 'pre_auxiliary_call', 'post_auxiliary_call')
CAPTURE_HOOKS = ('on_session_start', 'pre_llm_call', 'post_llm_call', 'on_session_end', 'pre_approval_request',
                 'post_tool_call', *OBSERVED, 'subagent_stop')
SETTINGS = ('mode', 'opportunities', 'z0int_python', 'z0int_home', 'persist_packet_text')
SERVICE_KEYS = ('stack_service_port', 'service_port', 'service_host', 'service_url', 'host', 'port', 'url')
# Keep in sync with z0int.hermes_decisions (tests/test_hermes_decisions.py checks parity).
NON_USER_PLATFORMS = frozenset({'cron', 'subagent', 'curator', 'kanban', 'batch', 'raft'})
SYSTEM_PREFIXES = ('[System:', '[SYSTEM:', '[SYSTEM NOTICE', '[IMPORTANT: Background process', '[IMPORTANT:',
                   '<agent-message', '<task-notification', '<system-reminder')
NOTE_PREFIX = '[System note:'
ASK_TOOLS = frozenset({'clarify'})
ESCALATE_TOOLS = frozenset({'delegate_task'})
# Session sources run without a human typing (cron scheduler, kanban workers, cluster services): cohort automated.
AUTOMATED_SOURCES = frozenset({'cron', 'kanban', 'curator', 'batch', 'raft', 'cluster'})
SHELL_TOOLS = frozenset({'terminal'})
SCALARS = ('task_id', 'api_request_id', 'tool_call_id', 'platform', 'surface', 'model', 'provider', 'api_mode',
           'aux_task', 'api_call_count', 'retry_count', 'max_retries', 'message_count', 'tool_count',
           'approx_input_tokens', 'request_char_count', 'max_tokens', 'api_duration', 'duration_ms', 'started_at',
           'ended_at', 'status_code', 'retryable', 'reason', 'finish_reason', 'response_model',
           'assistant_content_chars', 'assistant_tool_call_count', 'streaming', 'completed', 'failed',
           'interrupted', 'turn_exit_reason', 'child_role', 'child_status', 'telemetry_schema_version')
log = logging.getLogger(__name__)


# ----------------------------------------------------------------------------- turn text (hot path, strings only)
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


def cohort_of(platform, parent_session_id=''):
    """Capture-time cohort: subagent turns ``agent``; cron, kanban and cluster-service sessions ``automated``."""
    if (platform or '').lower() == 'subagent':
        return 'agent'
    if os.environ.get('HERMES_KANBAN_TASK'):
        return 'automated'
    words = set(re.split(r'[^a-z0-9]+', f'{platform or ""} {_session_source() or ""}'.lower()))
    return 'automated' if words & AUTOMATED_SOURCES else 'interactive'


def turn_behaviour(messages):
    """What the agent did after the last user message: observed, NOT an optimal label."""
    tail = []
    for m in reversed(messages if isinstance(messages, list) else []):
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


def _key(session_id, turn_id):
    # Hermes turn ids usually embed the session id already ("<session>:<task>:<uuid>"); same rule as harness_id.
    if not turn_id:
        return None
    turn_id = str(turn_id)
    return turn_id if session_id and turn_id.startswith(f'{session_id}:') else f'{session_id or HARNESS}:{turn_id}'


def _scalar(value):
    if isinstance(value, bool) or value is None or isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return value[:200] if isinstance(value, str) else None


def _fields(payload):
    out = {k: _scalar(payload[k]) for k in SCALARS if payload.get(k) is not None}
    return {k: v for k, v in out.items() if v is not None}


def _usage(value):
    if not isinstance(value, dict):
        return {}
    return {str(k): v for k, v in value.items() if not isinstance(v, bool) and isinstance(v, (int, float))
            and math.isfinite(v)}


def _shell_result(result):
    """(exit code, output tail) of a terminal tool result; both stay in memory (the child keeps only the class)."""
    if not isinstance(result, str) or len(result) > 1 << 20:
        return None, ''
    try:
        body = json.loads(result)
    except ValueError:
        return None, ''
    if not isinstance(body, dict):
        return None, ''
    code = body.get('exit_code')
    output = body.get('output')
    return (code if isinstance(code, int) and not isinstance(code, bool) else None,
            output[-2000:] if isinstance(output, str) else '')


# ----------------------------------------------------------------------------- Hermes lookups (fail open)
def _hermes_workspace_root(task_id):
    """The task's terminal cwd as Hermes resolves it (session cwd, cwd override, TERMINAL_CWD), never os.getcwd()."""
    try:
        from tools.file_tools_paths import _authoritative_workspace_root
        return _authoritative_workspace_root(task_id or 'default')
    except Exception:
        return None


def _session_source():
    try:
        from gateway.session_context import get_session_env
        return get_session_env('HERMES_SESSION_SOURCE', '') or None
    except Exception:
        return os.environ.get('HERMES_SESSION_SOURCE') or None


def _profile_config():
    try:
        from hermes_cli.config import load_config_readonly
        return load_config_readonly() or {}
    except Exception:
        return {}


def other_vehicle(profile):
    """The other Hermes opportunity vehicle enabled in this profile, if any (there must be exactly one)."""
    plugins = (profile or {}).get('plugins') or {}
    enabled = plugins.get('enabled') or []
    if 'z0int-decisions' in enabled:
        return 'z0int-decisions'
    bend = (((plugins.get('entries') or {}).get('bend') or {}).get('settings') or {})
    if 'bend' in enabled and bend.get('stack_opportunities') is True:
        return 'bend'
    return None


# ----------------------------------------------------------------------------- z0 home, plugin-side rows
def _now():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def z0_home(settings=None):
    value = (settings or {}).get('z0int_home') or os.environ.get('Z0INT_HOME')
    return Path(value).expanduser() if value else Path.home() / '.z0int'


def _append(home, name, row):
    path = home / 'state' / HARNESS / name
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'a', encoding='utf-8') as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        fh.write(json.dumps(row, ensure_ascii=False, default=str) + '\n')


def report(home=None):
    """Counts from disk, as a fresh process sees them (drops are never only in memory)."""
    path = Path(home or z0_home()) / 'state' / HARNESS / 'drops.jsonl'
    try:
        lines = path.read_text(encoding='utf-8').splitlines()
    except OSError:
        lines = []
    return {'rows_dropped': sum(int(json.loads(line).get('count') or 0) for line in lines if line.strip())}


def _python(settings, home):
    python = settings.get('z0int_python') or os.environ.get('Z0INT_PYTHON')
    if not python:
        try:
            python = json.loads((home / 'config' / 'hermes.json').read_text()).get('python')
        except (OSError, ValueError, AttributeError):
            python = None
    return python or None


# ----------------------------------------------------------------------------- capture
class Capture:
    """Hooks -> bounded queue -> one writer thread -> ``z0int.hermes_capture batch`` children, one at a time."""

    def __init__(self, *, home, python, opportunities=True, persist_packet_text=False):
        self.home, self.python = home, python
        self.opportunities, self.persist_packet_text = opportunities, persist_packet_text
        self.counts = {'dropped': 0, 'z0int_unavailable': 0, 'config_warnings': 0}
        self.queue = queue.Queue(maxsize=MAX_QUEUE)
        self._turns, self._ended = {}, {}  # open turns (key -> state) and recently closed turn keys
        self._lock = threading.Lock()  # turn state
        self._put_lock = threading.Lock()  # put's seal check + enqueue vs close's seal
        self._io = threading.Lock()  # plugin-side writes vs close
        self._drops, self._dlock = {}, threading.Lock()  # (kind, reason) -> count not yet written
        self._sealed = self._stopping = self._closed = False
        self._child = self._last_child = self._inflight = None
        self._retry_at, self._reason = 0.0, 'child_failed'
        self._launched = 0  # projection children started for this instance (acked 'p' by the batch child)
        self.gate = self._open_gate()
        self._writer = threading.Thread(target=self._run, name='z0int-hermes-capture', daemon=True)
        self._writer.start()

    def _open_gate(self):
        """The per-instance gate a projection child must find (and lock) to write; close() unlinks it."""
        path = self.home / 'runtime' / 'hermes-gates' / f'{uuid.uuid4().hex}.gate'
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
            return str(path)
        except OSError:
            self.opportunities = False  # an ungated build could outlive the plugin: build none
            return None

    # ------------------------------------------------------------------ hooks (each returns None, never raises)
    def on_pre_llm_call(self, session_id='', turn_id='', user_message='', platform='', model='', task_id='',
                        parent_session_id='', cwd=None, **_):
        t0 = time.perf_counter()
        try:
            key = _key(session_id, turn_id)
            reason, text = classify(user_message, platform)
            cohort = 'harness' if reason in ('empty', 'system_message') else cohort_of(platform, parent_session_id)
            job = {'kind': 'turn' if key else 'event', 'event': 'pre_llm_call', 'session_id': session_id,
                   'turn_id': turn_id or None, 'model': model or None, 'policy_revision': POLICY_REVISION,
                   'cohort': cohort, 'excluded': reason, 'text': text[:MAX_REQUEST_CHARS] if text else None,
                   'fields': _fields({'platform': platform, 'task_id': task_id})}
            # Opportunities are for user intent only: automated, subagent and injected turns get outcomes, no build.
            if key and text and cohort == 'interactive' and len(text) <= MAX_REQUEST_CHARS and self.opportunities:
                job.update(opportunity=True, persist_packet_text=self.persist_packet_text, gate=self.gate,
                           cwd=cwd if isinstance(cwd, str) and cwd else _hermes_workspace_root(task_id))
            if key:
                with self._lock:
                    self._turns[key] = {'t0': time.time(), 'session_id': session_id, 'approvals': 0,
                                        'platform': platform, 'excluded': reason, 'hook_s': 0.0}
                    if len(self._turns) > 1024:
                        self._turns.pop(next(iter(self._turns)), None)
            self._put(job)
            if key in self._turns:
                self._turns[key]['hook_s'] += time.perf_counter() - t0
        except Exception:
            pass
        return None

    def on_post_llm_call(self, session_id='', turn_id='', assistant_response=None, conversation_history=None,
                         model='', platform='', **_):
        t0 = time.perf_counter()
        try:
            key = _key(session_id, turn_id)
            turn = self._close_turn(key)
            if turn is None:
                self._put({'kind': 'event', 'event': 'post_llm_call', 'session_id': session_id,
                           'turn_id': turn_id or None})
                return None
            b = turn_behaviour(conversation_history)
            text = assistant_response if isinstance(assistant_response, str) else ''
            approvals = turn.get('approvals', 0)
            b.update(asked_user=b['asked_via_tool'] or text.rstrip().endswith('?'),
                     approval_requested=approvals > 0, escalated=b['delegated'] or approvals > 0)
            turn['hook_s'] = turn.get('hook_s', 0.0) + time.perf_counter() - t0
            self._put(self._outcome(session_id, turn_id, model, platform, turn, b, 'completed', 'post_llm_call'))
        except Exception:
            pass
        return None

    def on_session_end(self, session_id='', turn_id='', completed=None, failed=None, interrupted=None, model='',
                       platform='', **_):
        """Turn end (Hermes fires it after every run): closes the turn when post_llm_call never did, or every open
        turn of the session when no turn id is given. A turn closed normally is not closed twice."""
        try:
            ended = 'interrupted' if interrupted else 'failed' if failed else 'completed'
            with self._lock:
                keys = [_key(session_id, turn_id)] if turn_id else \
                    [k for k, t in self._turns.items() if t.get('session_id') == session_id]
            closed = [(k, self._close_turn(k)) for k in keys if k]
            closed = [(k, t) for k, t in closed if t is not None]
            for key, turn in closed:
                approvals = turn.get('approvals', 0)
                behaviour = {'asked_user': None, 'tool_calls': None, 'assistant_messages': None,
                             'approval_requested': approvals > 0, 'escalated': True if approvals else None}
                self._put(self._outcome(session_id, key, model, platform, turn, behaviour, ended, 'on_session_end'))
            if not closed:
                self._put({'kind': 'event', 'event': 'on_session_end', 'session_id': session_id,
                           'turn_id': turn_id or None, 'fields': _fields({'completed': completed, 'failed': failed,
                                                                         'interrupted': interrupted})})
        except Exception:
            pass
        return None

    def on_approval_request(self, session_key='', surface=None, **_):
        try:
            with self._lock:
                live = [t for t in self._turns.values() if t.get('session_id') == session_key] or \
                    (list(self._turns.values())[-1:] if len(self._turns) == 1 else [])
                for t in live:
                    t['approvals'] += 1
            self._put({'kind': 'event', 'event': 'pre_approval_request', 'session_id': session_key or None,
                       'fields': _fields({'surface': surface})})
        except Exception:
            pass
        return None

    def on_post_tool_call(self, tool_name='', args=None, result=None, session_id='', turn_id='', status=None,
                          error_type=None, duration_ms=None, tool_call_id=None, **_):
        try:
            tool = {'tool_name': _scalar(str(tool_name or '')), 'status': _scalar(status),
                    'error_type': _scalar(error_type), 'duration_ms': _scalar(duration_ms),
                    'tool_call_id': _scalar(tool_call_id)}
            job = {'kind': 'tool', 'event': 'post_tool_call', 'session_id': session_id, 'turn_id': turn_id or None,
                   'tool': {k: v for k, v in tool.items() if v is not None}}
            command = args.get('command') if isinstance(args, dict) else None
            if tool_name in SHELL_TOOLS and isinstance(command, str):
                job['command'] = command[:4096]
                job['exit'], job['out_tail'] = _shell_result(result)
            self._put(job)
        except Exception:
            pass
        return None

    def observe(self, event, **payload):
        try:
            session, turn = payload.get('session_id'), payload.get('turn_id')
            if event == 'subagent_stop':  # announced from the parent's side
                session, turn = session or payload.get('parent_session_id'), turn or payload.get('parent_turn_id')
            job = {'kind': 'session' if event == 'on_session_start' else 'event', 'event': event,
                   'session_id': session, 'turn_id': turn or None, 'model': _scalar(payload.get('model')),
                   'fields': _fields(payload), 'usage': _usage(payload.get('usage'))}
            if event == 'subagent_stop':
                history = payload.get('tool_call_history')
                job['subagent'] = {'child_role': _scalar(payload.get('child_role')),
                                   'child_status': _scalar(payload.get('child_status')),
                                   'duration_ms': _scalar(payload.get('duration_ms')),
                                   'tool_calls': len(history) if isinstance(history, list) else 0}
            self._put(job)
        except Exception:
            pass
        return None

    def hook(self, event):
        method = {'pre_llm_call': self.on_pre_llm_call, 'post_llm_call': self.on_post_llm_call,
                  'on_session_end': self.on_session_end, 'pre_approval_request': self.on_approval_request,
                  'post_tool_call': self.on_post_tool_call}.get(event)
        if method is not None:
            return method

        def callback(**payload):
            return self.observe(event, **payload)
        callback.__name__ = f'z0int_observe_{event}'
        return callback

    # ------------------------------------------------------------------ turn bookkeeping
    def _close_turn(self, key):
        if not key:
            return None
        with self._lock:
            if key in self._ended:
                return None
            self._ended[key] = True
            if len(self._ended) > 4096:
                for k in list(self._ended)[:2048]:
                    self._ended.pop(k, None)
            return self._turns.pop(key, None)

    def _outcome(self, session_id, turn_id, model, platform, turn, behaviour, ended, event):
        return {'kind': 'outcome', 'event': event, 'session_id': session_id, 'turn_id': turn_id,
                'model': model or None, 'policy_revision': POLICY_REVISION, 'ended': ended, 'behaviour': behaviour,
                'extra': {'platform': platform or turn.get('platform') or None, 'excluded': turn.get('excluded'),
                          'hook_ms': round(turn.get('hook_s', 0.0) * 1000, 3),
                          'wall_s': round(time.time() - turn['t0'], 3) if turn.get('t0') else None}}

    # ------------------------------------------------------------------ queue, writer, accounting
    def _put(self, job):
        with self._put_lock:
            if self._closed:
                return  # after unload nothing is captured, counted or written
            if not self._sealed:
                try:
                    self.queue.put_nowait(job)
                    return
                except queue.Full:
                    reason = 'queue_full'
            else:
                reason = 'closed'
        self._count_drop(job.get('kind'), reason)

    def _count_drop(self, kind, reason, n=1):
        with self._dlock:
            self._drops[(kind, reason)] = self._drops.get((kind, reason), 0) + n

    def _write_drops(self):
        with self._io:
            if self._closed:
                return
            with self._dlock:
                pending, self._drops = self._drops, {}
            for (kind, reason), count in pending.items():
                try:
                    _append(self.home, 'drops.jsonl', {'schema': f'z0int.{HARNESS}.drop.v0', 'harness': HARNESS,
                                                       'kind': kind, 'reason': reason, 'count': count,
                                                       'recorded_at': _now()})
                except OSError:
                    pass  # a lost drop marker never affects the host
                self.counts['dropped'] += count

    def failure(self, kind, detail):
        with self._io:
            if self._closed:
                return
            try:
                _append(self.home, 'failures.jsonl', {'schema': f'z0int.{HARNESS}.failure.v0', 'kind': kind,
                                                      'harness': HARNESS, 'recorded_at': _now(), 'detail': detail})
            except OSError:
                pass

    def _run(self):
        while True:
            try:
                job = self.queue.get(timeout=0.05)
            except queue.Empty:
                self._write_drops()
                if self._stopping:
                    return
                continue
            jobs = [job]
            while len(jobs) < BATCH_MAX:
                try:
                    jobs.append(self.queue.get_nowait())
                except queue.Empty:
                    break
            self._inflight = jobs
            acked = 0
            if not self._stopping:
                try:
                    acked = self._deliver(jobs)
                except Exception:
                    self._reason = 'child_failed'
            self._settle(jobs, acked, 'closed' if self._stopping else self._reason)
            self._write_drops()
            for _ in jobs:
                self.queue.task_done()

    def _settle(self, jobs, acked, reason):
        with self._io:
            if self._inflight is not jobs:  # close() already counted this batch
                return
            self._inflight = None
        for job in jobs[acked:]:
            self._count_drop(job.get('kind'), reason)

    def _deliver(self, jobs):
        """Run one batch child; the number of jobs it acknowledged."""
        if not self.python or time.monotonic() < self._retry_at:
            return self._unavailable(jobs)
        data = ''.join(json.dumps(j, default=str) + '\n' for j in jobs).encode()
        try:
            child = subprocess.Popen([self.python, '-m', 'z0int.hermes_capture', 'batch'], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True,
                                     env=dict(os.environ, Z0INT_HOME=str(self.home)))
        except OSError:
            return self._unavailable(jobs)
        self._child = self._last_child = child
        if self._stopping:
            child.kill()
        try:
            out, _ = child.communicate(data, timeout=BATCH_TIMEOUT)
        except subprocess.TimeoutExpired:
            child.kill()
            out, _ = child.communicate()
        finally:
            self._child = None
        acks = out.split(b'\n')[:len(jobs)]
        acked = min(out.count(b'\n'), len(jobs))
        self._launched += acks.count(b'p')
        if acked == 0 and child.returncode != 0 and not self._stopping:
            return self._unavailable(jobs)
        self._reason = 'child_failed'
        return acked

    def _unavailable(self, jobs):
        self._retry_at = time.monotonic() + UNAVAILABLE_RETRY_S
        self.counts['z0int_unavailable'] += 1
        self.failure('z0int_unavailable', {'jobs': len(jobs), 'python': 'set' if self.python else 'unset'})
        self._reason = 'z0int_unavailable'
        return 0

    def flush(self, timeout=5.0):
        """Test/proof helper: wait until every accepted job is written or counted."""
        deadline = time.monotonic() + timeout
        while (self.queue.unfinished_tasks or self._drops) and time.monotonic() < deadline:
            time.sleep(0.01)
        return not self.queue.unfinished_tasks and not self._drops

    def close(self):
        """Bounded unload (P-2): drain while time remains, then stop the child and wait for running projections;
        whatever is left (jobs, and projections that did not finish) is counted as dropped before this returns,
        and nothing is written after it (projections write only under the gate this removes). Runs at plugin
        unload and at exit; the second call does nothing."""
        deadline = time.monotonic() + CLOSE_TIMEOUT
        with self._put_lock:
            if self._sealed:
                return
            self._sealed = True
        while self.queue.unfinished_tasks and time.monotonic() < deadline - 0.4:
            time.sleep(0.01)
        self._stopping = True
        child = self._child
        if child is not None and child.poll() is None:
            child.kill()
        self._writer.join(timeout=max(0.0, deadline - time.monotonic() - 0.2))
        with self._io:
            left, self._inflight = list(self._inflight or []), None
        while True:
            try:
                left.append(self.queue.get_nowait())
            except queue.Empty:
                break
        for job in left:
            self._count_drop(job.get('kind'), 'closed')
        unsettled = self._seal_gate(deadline)
        if unsettled:
            self._count_drop('opportunity_record', 'closed', unsettled)
        self._write_drops()
        with self._put_lock, self._io:
            self._closed = True

    def _settled(self):
        try:
            return os.path.getsize(self.gate) // 2  # one '1\n' per projection that wrote (or failed) in time
        except (OSError, TypeError):
            return 0

    def _seal_gate(self, deadline):
        """Wait (within the close budget) for running projections, then unlink the gate under its exclusive lock:
        a projection still running finds it gone and writes nothing. Returns how many never settled."""
        if not self.gate:
            return 0
        while self._settled() < self._launched and time.monotonic() < deadline - 0.1:
            time.sleep(0.01)
        try:
            fd = os.open(self.gate, os.O_RDONLY)
        except OSError:
            return 0
        try:
            while True:  # a writer holds it only for one append; never past the budget
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        break
                    time.sleep(0.005)
            settled = self._settled()
            os.unlink(self.gate)
        except OSError:
            settled = self._launched
        finally:
            os.close(fd)
        return max(0, self._launched - settled)

    def stats(self):
        with self._dlock:
            pending = sum(self._drops.values())
        child = self._last_child
        return {'queued': self.queue.qsize(), 'dropped': self.counts['dropped'] + pending,
                'z0int_unavailable': self.counts['z0int_unavailable'],
                'config_warnings': self.counts['config_warnings'], 'child_pid': child.pid if child else None}


# ----------------------------------------------------------------------------- automatic (#95; inert while off)
def automatic_enabled(home):
    try:
        value = json.loads((home / 'config' / 'automatic.json').read_text()).get('hermes') or {}
    except (OSError, ValueError, AttributeError):
        value = {}
    return isinstance(value, dict) and value.get('enabled') is True and os.environ.get('Z0INT_AUTO_HERMES') != '0'


def invoke(operation, value, python, home):
    """The installed z0int (the interpreter capture uses), on the z0 home whose automatic.json gated this call."""
    if not python:
        raise RuntimeError('no z0int interpreter (z0int_python / Z0INT_PYTHON)')
    result = subprocess.run([python, '-m', 'z0int.automatic', operation], input=json.dumps(value), text=True,
                            capture_output=True, timeout=30, env=dict(os.environ, Z0INT_HOME=str(home)), check=True)
    return json.loads(result.stdout)


def before_turn(python, home, session_id='', turn_id=None, user_message='', **kwargs):
    if not isinstance(user_message, str) or not user_message.strip():
        return None
    try:
        result = invoke('event', dict(harness='hermes', session_id=session_id or 'hermes',
                                      turn_id=str(turn_id) if turn_id is not None else uuid.uuid4().hex,
                                      instance_id=INSTANCE_ID, text=user_message), python, home)
        context = {'context': result['context']} if result.get('action') == 'context' else None
        if result.get('receipt_id'):
            invoke('consume', dict(harness='hermes', instance_id=INSTANCE_ID, receipt_id=result['receipt_id']),
                   python, home)
        return context
    except Exception:
        return None


# ----------------------------------------------------------------------------- registration
def register(ctx):
    """Mode off (the default) registers no capture hook; a settings error fails open (no hook at all)."""
    try:
        settings = {key: ctx.get_config(key) for key in SETTINGS}
        ignored = [key for key in SERVICE_KEYS if ctx.get_config(key) is not None]
        home = z0_home(settings)
        automatic = automatic_enabled(home)
    except Exception:
        log.warning('%s: settings unreadable; plugin stays inert', PLUGIN)
        return None
    capture, python = None, _python(settings, home)
    if settings.get('mode') == 'shadow' and os.environ.get('Z0INT_CAPTURE') != '0':
        try:
            capture = Capture(home=home, python=python,
                              opportunities=settings.get('opportunities') is not False,
                              persist_packet_text=settings.get('persist_packet_text') is True)
            if ignored:  # P-1: capture never talks to a service; a host/port setting is a counted warning
                capture.counts['config_warnings'] += len(ignored)
                capture.failure('config_warning', {'ignored': sorted(ignored)})
                log.warning('%s: ignoring service settings %s (capture writes files only)', PLUGIN, sorted(ignored))
            vehicle = other_vehicle(_profile_config())
            if vehicle:  # exactly one Hermes opportunity vehicle
                capture.opportunities = False
                capture.failure('double_capture_guard', {'vehicle': vehicle})
        except Exception:
            capture = None
    if capture is not None:
        for event in CAPTURE_HOOKS:
            if event != 'pre_llm_call':
                ctx.register_hook(event, capture.hook(event))
        ctx.on_unload(capture.close)
        atexit.register(capture.close)  # `hermes chat -q` exits right after its turn: drain, bounded, then stop
    if capture is not None or automatic:
        def pre_llm_call(**kw):
            if capture is not None:
                capture.on_pre_llm_call(**kw)
            return before_turn(python, home, **kw) if automatic else None
        ctx.register_hook('pre_llm_call', pre_llm_call)
    return capture
