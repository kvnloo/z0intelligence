"""The Hermes memory seam (C8), composed into the plugin's one ``pre_llm_call`` (stdlib only; no Hermes core change).

Hermes injects a ``pre_llm_call`` result's ``context`` into the current user message at API time only
(agent/turn_context.py ``_collect_pre_llm_call_context``); this module never writes the session store.

``memory_inject`` (plugin setting): off | shadow | canary | on. Unset, it follows the capture ``mode`` (shadow with
capture, off with a plugin that is off), so a profile with the plugin off keeps Hermes dispatch untouched.

* shadow: one detached ``z0int_python -m z0int.memory.seam shadow`` child per user turn; the hook returns at once.
  At most MAX_SHADOW_CHILDREN run at once; past that the turn writes a counted ``queue_saturated`` row instead.
* canary/on: ``z0int_python -m z0int.memory.seam turn`` within the 300 ms deadline (the child counts it from the
  job's ``started_at``; this side kills it a little later as a backstop). Past it, or on any failure, the turn
  keeps its native context and a counted ``timeout`` / ``error`` row is written.

The plugin calls this only for user turns (not cron/subagent/kanban/batch platforms or child sessions). The z0 side
decides everything else (replay, task-project scope, cloud-egress opt-in per harness, single injection owner,
scrub). The model endpoint is the profile's ``model.base_url`` and nothing else; with Hermes's own ``memory.provider: memory_tencentdb`` the z0
brief leaves TencentDB out (that provider already injects it). ``post_llm_call`` records the memory-use receipt of
an injected turn.
"""
import fcntl
import json
import os
import subprocess
import threading
import time
from pathlib import Path

HARNESS = 'hermes'
MODES = ('off', 'shadow', 'canary', 'on')
DEADLINE_MS = 300
BACKSTOP_S = 0.15  # after the child's own deadline: interpreter start-up and exit
MAX_QUERY_CHARS = 2000
MAX_SHADOW_CHILDREN = 4  # as C1 capture's harness_capture.MAX_CHILDREN
SCHEMA = 'z0int.memory_seam.v0'


def mode_of(settings):
    value = settings.get('memory_inject') or os.environ.get('Z0INT_MEMORY_INJECT')
    if not value:
        value = 'shadow' if settings.get('mode') == 'shadow' else 'off'
    return value if value in MODES else 'off'


class Memory:
    def __init__(self, *, home, python, mode, profile, injector=None, deadline_ms=DEADLINE_MS):
        self.home, self.python, self.mode, self.deadline_ms = home, python, mode, deadline_ms
        self.injector = injector or None
        model = (profile or {}).get('model') or {}
        self.endpoint = model.get('base_url') if isinstance(model, dict) else None
        provider = ((profile or {}).get('memory') or {}).get('provider')
        self.exclude_layers = ['semantic'] if provider == 'memory_tencentdb' else []
        self._injected = {}  # (session, turn) -> the turn's seam result, until post_llm_call
        self._lock = threading.Lock()
        self._shadows = []  # running shadow children (Popen), bounded by MAX_SHADOW_CHILDREN

    def _job(self, session_id, turn_id, user_message, cwd):
        return {'session_id': session_id or 'hermes', 'turn_id': str(turn_id or ''), 'query': user_message[:MAX_QUERY_CHARS],
                'mode': self.mode, 'endpoint': self.endpoint, 'cwd': cwd, 'injector': self.injector,
                'exclude_layers': self.exclude_layers, 'started_at': time.time()}

    def _env(self):
        return dict(os.environ, Z0INT_HOME=str(self.home))

    def _row(self, row):
        path = self.home / 'state' / 'memory' / 'seam' / f'{HARNESS}.jsonl'
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, 'a', encoding='utf-8') as fh:
                fcntl.flock(fh, fcntl.LOCK_EX)
                fh.write(json.dumps({'schema': SCHEMA, 'harness': HARNESS, 'mode': self.mode,
                                     'recorded_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), **row}) + '\n')
        except OSError:
            pass

    def pre_llm_call(self, session_id='', turn_id='', user_message='', cwd=None, **_):
        """{'context': brief} for the next model request, or None (native). Never raises."""
        if self.mode == 'off' or not isinstance(user_message, str) or not user_message.strip():
            return None
        job = self._job(session_id, turn_id, user_message, cwd)
        argv = [self.python or 'python3', '-m', 'z0int.memory.seam']
        try:
            if self.mode == 'shadow':
                with self._lock:
                    self._shadows = [c for c in self._shadows if c.poll() is None]
                    if len(self._shadows) >= MAX_SHADOW_CHILDREN:
                        self._row({'outcome': 'queue_saturated', 'injected': False, 'would_inject': False,
                                   'source': 'shim', 'in_flight': len(self._shadows)})
                        return None
                child = subprocess.Popen([*argv, 'shadow', '--harness', HARNESS], stdin=subprocess.PIPE,
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=self._env(),
                                         start_new_session=True)
                with self._lock:
                    self._shadows.append(child)
                child.stdin.write(json.dumps(job).encode())
                child.stdin.close()
                return None
            proc = subprocess.run([*argv, 'turn', '--harness', HARNESS], input=json.dumps(job), text=True,
                                  capture_output=True, env=self._env(),
                                  timeout=self.deadline_ms / 1000.0 + BACKSTOP_S)
            out = json.loads(proc.stdout or '{}')
        except subprocess.TimeoutExpired:
            self._row({'outcome': 'timeout', 'injected': False, 'would_inject': False, 'source': 'shim'})
            return None
        except Exception as exc:  # fail open: native context, counted
            self._row({'outcome': 'error', 'injected': False, 'would_inject': False, 'source': 'shim',
                       'error': type(exc).__name__})
            return None
        if not isinstance(out, dict) or not out.get('context'):
            return None
        with self._lock:
            self._injected[(session_id, str(turn_id))] = out
            while len(self._injected) > 256:
                self._injected.pop(next(iter(self._injected)))
        return {'context': out['context']}

    def post_llm_call(self, session_id='', turn_id='', **_):
        """The memory-use receipt of an injected turn, once the model call ran (content-free)."""
        with self._lock:
            out = self._injected.pop((session_id, str(turn_id)), None)
        if out:
            self._row({'event': 'post_llm_call', 'outcome': 'used', 'turn_key': out.get('turn_key'), 'injected': True,
                       'memory_snapshot_id': out.get('memory_snapshot_id'), 'memory': out.get('memory')})
        return None


def killed(home):
    """The capture kill switch (Z0INT_CAPTURE=0 or config/capture.json {"enabled": false}) keeps memory native too."""
    if os.environ.get('Z0INT_CAPTURE') == '0':
        return True
    try:
        return json.loads((Path(home) / 'config' / 'capture.json').read_text(encoding='utf-8')).get('enabled') is False
    except (OSError, ValueError, AttributeError):
        return False


def create(settings, home, python, profile):
    """A Memory for this profile, or None when memory_inject resolves to off or the kill switch is set."""
    mode = mode_of(settings)
    if mode == 'off' or killed(home):
        return None
    try:  # seam/hermes.active: capture's opportunity build waits briefly for a turn this seam has not marked yet
        marker = Path(home) / 'state' / 'memory' / 'seam' / f'{HARNESS}.active'
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
    except OSError:
        pass
    return Memory(home=home, python=python, mode=mode, profile=profile, injector=settings.get('memory_injector'))
