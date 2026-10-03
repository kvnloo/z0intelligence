"""Cross-harness capture core (z0int#62): one semantic record family for every harness.

Append-only JSONL under ``$Z0INT_HOME/state/<harness>/``:

  opportunities.jsonl  z0int.<harness>.opportunity_record.v0  DecisionOpportunity + deterministic gate
  outcomes.jsonl       z0int.<harness>.turn_outcome.v0        observed behaviour (never an optimal label)
  failures.jsonl       z0int.<harness>.failure.v0             explicit F-cases instead of silent drops
  drops.jsonl          z0int.<harness>.drop.v0                rows refused by a full queue or the fan-out cap

``<harness>`` is spelled with ``_`` in schema names (``z0int.claude_code.*`` are the existing Claude Code
names). Every record carries harness, turn_key (``harness_id.turn_key``), work_item_id, attempt_id, cohort,
model_id and policy_revision (a value or ``"unknown"``), privacy_class and recorded_at. Outcome and failure
rows hold no prompt, response, command or path text; opportunity request text is kept only with
privacy_class ``request_opt_in``.

Hook processes import this module on every turn, so it loads stdlib + ``harness_id`` only; the
DecisionOpportunity build (decision_opportunity + state_packet, 1-3 s) runs in a detached child.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from .harness_id import turn_key

HARNESSES = ('claude-code', 'codex', 'grok', 'hermes', 'omp', 'omo', 'dsh')
VERSION = 'v0'
RECORD_FILES = {'opportunity_record': 'opportunities.jsonl', 'turn_outcome': 'outcomes.jsonl',
                'failure': 'failures.jsonl'}
FAILURE_KINDS = ('unsupported_harness', 'unsupported_schema', 'stale_evidence', 'missing_verifier',
                 'partial_measurement', 'uncertain_execution', 'duplicate_event', 'misattribution')
PRIVACY_CLASSES = ('content_free', 'request_opt_in')
UNKNOWN = 'unknown'
UNSUPPORTED = '_unsupported'
# Harnesses whose turns an outcome verifier labels today; the others get a missing_verifier row per outcome.
VERIFIERS = {'claude-code': 'z0int.outcome_verifier'}
MEASURED = ('asked_user', 'tool_calls', 'assistant_messages')
HARNESS_MESSAGE_PREFIXES = ('<agent-message', '<task-notification', '<system-reminder')
SUBAGENT_TOOLS = ('Agent', 'Task')
IDENTITY = ('harness', 'session_id', 'trace_id', 'turn_key', 'work_item_id', 'attempt_id', 'cohort', 'model_id',
            'policy_revision', 'privacy_class')
MAX_CHILDREN = 4  # detached builds at once, across all hook processes (a subagent burst must not fork-bomb)
KEEP = 32  # per-session memory (recent turns, appended markers, source revisions): small, read on every hook
SLOT_WAIT_S = 60.0
_SCHEMA_RE = re.compile(r'^z0int\.([a-z0-9_]+)\.([a-z_]+)\.(v\d+)$')
GIT_UNKNOWN = {'key': 'git', 'source_status': 'source_unavailable'}


# ----------------------------------------------------------------------------- layout + small helpers
def home() -> Path:
    # Same rule as paths.home(); repeated so a hook process imports nothing beyond stdlib + this module.
    override = os.environ.get('Z0INT_HOME')
    return (Path(override).expanduser() if override else Path.home() / '.z0int').resolve()


def _base(root: str | Path | None) -> Path:
    return Path(root) if root else home()


def state_dir(harness: str, root: str | Path | None = None) -> Path:
    return _base(root) / 'state' / harness


def schema(harness: str, kind: str) -> str:
    return f'z0int.{harness.replace("-", "_")}.{kind}.{VERSION}'


def parse_schema(name: Any) -> tuple[str, str, str] | None:
    m = _SCHEMA_RE.match(name) if isinstance(name, str) else None
    return (m.group(1).replace('_', '-'), m.group(2), m.group(3)) if m else None


def _now() -> str:
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def _h(obj: Any, n: int = 20) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:n]


def _first(payload: Mapping[str, Any], *keys: str) -> Any:
    for k in keys:
        v = payload.get(k)
        if v not in (None, ''):
            return v
    return None


def _write_line(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'a', encoding='utf-8') as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        fh.write(json.dumps(row, ensure_ascii=False, default=str) + '\n')


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    out = []
    try:
        with open(path, encoding='utf-8', errors='replace') as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    out.append(row)
    except OSError:
        pass
    return out


def _config(root: str | Path | None = None) -> dict[str, Any]:
    try:
        value = json.loads((_base(root) / 'config' / 'capture.json').read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def enabled(env: Mapping[str, str] | None = None, root: str | Path | None = None) -> bool:
    """Kill switch: Z0INT_CAPTURE=0 or config/capture.json {"enabled": false} keeps every hook native."""
    env = os.environ if env is None else env
    return env.get('Z0INT_CAPTURE') != '0' and _config(root).get('enabled', True) is not False


def privacy_class(env: Mapping[str, str] | None = None, root: str | Path | None = None) -> str:
    env = os.environ if env is None else env
    value = env.get('Z0INT_CAPTURE_PRIVACY') or _config(root).get('privacy_class')
    return value if value in PRIVACY_CLASSES else 'content_free'


# ----------------------------------------------------------------------------- content-free capture flags
def is_harness_message(text: Any) -> bool:
    """Subagent hand-backs and task notifications arrive as prompts but carry no user intent."""
    return isinstance(text, str) and text.lstrip().startswith(HARNESS_MESSAGE_PREFIXES)


def capture_flags(text: Any) -> dict[str, Any]:
    """Prompt-time flags computed once at capture, so no export-time code path needs the request text."""
    n = len(text.strip()) if isinstance(text, str) else 0
    bucket = '0' if n == 0 else '1-49' if n < 50 else '50-199' if n < 200 else '200-999' if n < 1000 else '1000+'
    return {'is_harness_message': is_harness_message(text), 'request_chars': bucket}


# ----------------------------------------------------------------------------- payload fields (both spellings)
def session_id_of(payload: Mapping[str, Any], env: Mapping[str, str] | None = None) -> str | None:
    env = os.environ if env is None else env
    return _first(payload, 'session_id', 'sessionId') or env.get('GROK_SESSION_ID') or None


def prompt_of(payload: Mapping[str, Any]) -> str:
    text = _first(payload, 'prompt', 'user_message')
    if isinstance(text, list):  # multimodal content blocks
        text = '\n'.join(b.get('text', '') if isinstance(b, dict) else str(b) for b in text)
    if not isinstance(text, str):
        text = ((payload.get('tool_input') or {}).get('prompt') if isinstance(payload.get('tool_input'), dict)
                else None)
    return text if isinstance(text, str) else ''


def _model_of(payload: Mapping[str, Any]) -> Any:
    return _first(payload, 'model', 'model_id', 'modelId')


def turn_id(payload: Mapping[str, Any]) -> str:
    tid = _first(payload, 'prompt_id', 'turn_id')
    if isinstance(tid, (str, int)):
        return str(tid)
    # No per-turn id (older Claude Code, Grok): transcript size at submit time is stable per turn.
    try:
        size = Path(_first(payload, 'transcript_path', 'transcriptPath')).stat().st_size
    except (OSError, TypeError):
        size = -1
    parts = f"{payload.get('session_id') or payload.get('sessionId')}\0{size}\0{payload.get('prompt')}"
    if payload.get('timestamp'):
        parts += f"\0{payload['timestamp']}"
    return hashlib.sha256(parts.encode()).hexdigest()


# ----------------------------------------------------------------------------- identity, work items
def _label(harness: Any) -> str:
    text = str(harness)
    return text if re.fullmatch(r'[a-z0-9_.-]{1,40}', text) else 'sha256:' + _h(text, 12)


def supported(harness: str, root: str | Path | None = None) -> bool:
    if harness in HARNESSES:
        return True
    record_failure(harness, 'unsupported_harness', detail={'harness': _label(harness)}, root=root)
    return False


def _session_path(harness: str, session: Any, root: str | Path | None) -> Path:
    return state_dir(harness, root) / 'sessions' / f'{_h(str(session), 16)}.json'


def _read_session(harness: str, session: Any, root: str | Path | None) -> dict[str, Any]:
    try:
        value = json.loads(_session_path(harness, session, root).read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _keep(entries: Mapping[str, Any] | list, add: Any = None) -> Any:
    """Bound one per-session collection to its newest KEEP entries (dicts keep insertion order)."""
    if isinstance(entries, list):
        return [*entries, add][-KEEP:]
    return dict(list({**entries, **(add or {})}.items())[-KEEP:])


def _update_session(harness: str, session: Any, root: str | Path | None,
                    step: Callable[[dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
    path = _session_path(harness, session, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'a+', encoding='utf-8') as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        fh.seek(0)
        try:
            state = json.loads(fh.read() or '{}')
        except ValueError:
            state = {}
        state = step(state if isinstance(state, dict) else {})
        fh.seek(0)
        fh.truncate()
        fh.write(json.dumps(state))
    return state


def _ctx(harness: str, session: Any, tid: Any, *, work_item_id: Any, attempt_id: Any, cohort: str, model: Any,
         payload: Mapping[str, Any], env: Mapping[str, str] | None, root: str | Path | None,
         flags: dict[str, Any] | None = None) -> dict[str, Any]:
    ctx = {'harness': harness, 'session_id': session, 'trace_id': tid, 'turn_key': turn_key(harness, session, tid),
           'work_item_id': work_item_id, 'attempt_id': attempt_id, 'cohort': cohort, 'model_id': model or UNKNOWN,
           'policy_revision': payload.get('policy_revision') or UNKNOWN, 'privacy_class': privacy_class(env, root)}
    if flags is not None:
        ctx['capture'] = flags
    return ctx


def note_session(harness: str, payload: Mapping[str, Any], *, env: Mapping[str, str] | None = None,
                 root: str | Path | None = None) -> None:
    """SessionStart: remember the session's model so prompt-time records can name it (#62 R5)."""
    model = _model_of(payload)
    if model and harness in HARNESSES:
        _update_session(harness, session_id_of(payload, env), root, lambda st: dict(st, model=model))


def begin_turn(harness: str, payload: Mapping[str, Any], *, env: Mapping[str, str] | None = None,
               root: str | Path | None = None, cohort: str | None = None) -> dict[str, Any] | None:
    """Prompt time, on the hook's hot path: canonical ids, work item + attempt, cohort and capture flags.

    A user message opens the session's next work item (an ordinal persisted per session, so every hook
    process agrees); a payload ``retry: true`` keeps the work item and counts one more attempt. A
    harness-injected prompt gets cohort ``harness`` and a work item of its own. A repeated hook for the same
    turn changes nothing here; its duplicate row is caught when it is appended. ``cohort`` is the shim's own
    capture-time cohort when it knows more than the prompt text (Hermes: ``automated`` sources, injected
    system turns as ``harness``).
    """
    if not supported(harness, root):
        return None
    session, tid = session_id_of(payload, env), turn_id(payload)
    flags = capture_flags(prompt_of(payload))
    if cohort == 'harness':
        flags['is_harness_message'] = True

    def step(st: dict[str, Any]) -> dict[str, Any]:
        if st.get('turn_id') == tid:
            return st
        if flags['is_harness_message']:
            wi, attempt, kind = _h({'harness': harness, 'session': session, 'injected': tid}), 0, 'harness'
        else:
            if not (payload.get('retry') is True and st.get('ordinal')):
                st['ordinal'], st['attempt'] = st.get('ordinal', 0) + 1, -1
            st['attempt'] = st.get('attempt', -1) + 1
            wi, attempt, kind = _h({'harness': harness, 'session': session, 'ordinal': st['ordinal']}), \
                st['attempt'], cohort or 'interactive'
        turn = {'work_item_id': wi, 'attempt_id': attempt, 'cohort': kind}
        return dict(st, turn_id=tid, **turn, turns=_keep(st.get('turns') or {}, {tid: turn}))

    st = _update_session(harness, session, root, step)
    return _ctx(harness, session, tid, work_item_id=st['work_item_id'], attempt_id=st['attempt_id'],
                cohort=st['cohort'], model=_model_of(payload) or st.get('model'), payload=payload, env=env,
                root=root, flags=flags)


def subagent_turn(harness: str, payload: Mapping[str, Any], *, env: Mapping[str, str] | None = None,
                  root: str | Path | None = None) -> dict[str, Any] | None:
    """SubagentStart/SubagentStop, or the fallback pair PreToolUse/PostToolUse on Agent|Task: cohort ``agent``,
    keyed by the subagent id. The fallback pair is keyed by the tool_use id on both sides (a real SubagentStop
    names only the agent id, so it never closes a fallback opportunity). Other tools: None."""
    if not supported(harness, root):
        return None
    if payload.get('tool_name') is not None:
        if payload['tool_name'] not in SUBAGENT_TOOLS:
            return None
        agent = payload.get('tool_use_id')
    else:
        agent = _first(payload, 'agent_id', 'subagentId', 'subagent_id', 'tool_use_id')
    if not agent:
        return None
    session = session_id_of(payload, env)
    return _ctx(harness, session, f'agent:{agent}', work_item_id=_h({'harness': harness, 'session': session,
                                                                     'agent': str(agent)}),
                attempt_id=0, cohort='agent', model=_model_of(payload) or _read_session(harness, session, root).get('model'),
                payload=payload, env=env, root=root, flags=capture_flags(prompt_of(payload)))


def outcome_context(harness: str, payload: Mapping[str, Any], *, env: Mapping[str, str] | None = None,
                    root: str | Path | None = None) -> dict[str, Any] | None:
    """Stop time: the turn this outcome closes. A Stop without its own turn id (Grok) closes the session's
    latest prompt; work item, attempt and cohort come from the prompt with that turn id (the session's recent
    turns are remembered, so a Stop that arrives after the next prompt still finds its own)."""
    if not supported(harness, root):
        return None
    session = session_id_of(payload, env)
    st = _read_session(harness, session, root)
    tid = _first(payload, 'prompt_id', 'turn_id')
    tid = str(tid) if tid is not None else st.get('turn_id')
    turn = (st.get('turns') or {}).get(tid) or (st if tid is not None and tid == st.get('turn_id') else {})
    return _ctx(harness, session, tid, work_item_id=turn.get('work_item_id'), attempt_id=turn.get('attempt_id'),
                cohort=turn.get('cohort', UNKNOWN), model=_model_of(payload) or st.get('model'), payload=payload,
                env=env, root=root)


def payload_behaviour(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Content-free behaviour from the hook payload alone: whether the last reply asked the user."""
    text = payload.get('last_assistant_message') or payload.get('lastAssistantMessage')
    return {'asked_user': text.rstrip().endswith('?') if isinstance(text, str) else None,
            'tool_calls': None, 'assistant_messages': None}


# ----------------------------------------------------------------------------- writers
def append(harness: str, kind: str, row: Mapping[str, Any], *, root: str | Path | None = None) -> dict | None:
    """Append one record; None when it was refused (unsupported harness/schema) or is a duplicate of the same
    turn_key + kind. Every refusal leaves a failure row: nothing is dropped silently.

    The duplicate check reads the session's small state file (the turn_key + kind markers it has appended),
    never the record file, so its cost does not grow with the number of turns ever captured.
    """
    if not supported(harness, root):
        return None
    parsed = parse_schema(row.get('schema'))
    if parsed is None or parsed[:2] != (harness, kind) or parsed[2] != VERSION or kind not in RECORD_FILES:
        record_failure(harness, 'unsupported_schema', ctx=row, root=root,
                       detail={'record': kind, 'schema_version': parsed[2] if parsed else None})
        return None
    row = dict(row, recorded_at=row.get('recorded_at') or _now())
    duplicate = False
    if row.get('turn_key') and kind != 'failure':
        mark = f"{kind}:{row['turn_key']}"

        def step(st: dict[str, Any]) -> dict[str, Any]:
            nonlocal duplicate
            duplicate = mark in (st.get('appended') or [])
            return st if duplicate else dict(st, appended=_keep(st.get('appended') or [], mark))
        _update_session(harness, row.get('session_id'), root, step)
    if not duplicate:
        _write_line(state_dir(harness, root) / RECORD_FILES[kind], row)
    if duplicate:
        record_failure(harness, 'duplicate_event', ctx=row, root=root, detail={'record': kind})
        return None
    return row


def record_failure(harness: str, kind: str, *, ctx: Mapping[str, Any] | None = None,
                   detail: Mapping[str, Any] | None = None, root: str | Path | None = None) -> dict[str, Any]:
    """One explicit F-case row. ``detail`` must be content-free (kinds, field names, counts, hashed ids)."""
    target = harness if harness in HARNESSES else UNSUPPORTED
    row = {'schema': schema(target.strip('_'), 'failure'), 'kind': kind,
           **{k: ctx[k] for k in IDENTITY if ctx and ctx.get(k) is not None},
           'harness': target.strip('_'), 'recorded_at': _now(), 'detail': dict(detail or {})}
    _write_line(state_dir(target, root) / RECORD_FILES['failure'], row)
    return row


def record_drop(harness: str, kind: str, reason: str, *, count: int = 1, root: str | Path | None = None) -> None:
    _write_line(state_dir(harness, root) / 'drops.jsonl', {'schema': schema(harness, 'drop'), 'harness': harness,
                                                           'kind': kind, 'reason': reason, 'count': count,
                                                           'recorded_at': _now()})


def drop_count(harness: str, root: str | Path | None = None) -> int:
    return sum(int(r.get('count') or 0) for r in _read_jsonl(state_dir(harness, root) / 'drops.jsonl'))


# ----------------------------------------------------------------------------- opportunity (detached child)
def _git_unreadable_packet(reason: str) -> dict[str, Any]:
    return {'blocking_unknowns': [dict(GIT_UNKNOWN, reason=reason)], 'coverage': {'git': 'none'}}


def _git_marker(cwd: Path) -> bool:
    return any((p / '.git').exists() for p in (cwd, *cwd.parents))


def redact_packet_text(opp: Mapping[str, Any]) -> dict[str, Any]:
    """The opportunity without State Packet text (P-9): every claim, superseded-claim and contradiction value
    (branch names, commit subjects, README open items) becomes its digest, so a change is still visible."""
    state = dict(opp.get('state') or {})
    for section in ('claims', 'superseded', 'contradictions'):
        state[section] = [_digest_values(c) for c in state.get(section) or []]
    return dict(opp, state=state)


def _digest_values(entry: Any) -> Any:
    if not isinstance(entry, Mapping):
        return {'sha256': _h(entry)}
    out = dict(entry)
    if 'value' in out:
        out['value'] = {'sha256': _h(out['value'])}
    if isinstance(out.get('values'), list):
        out['values'] = [_digest_values(v) for v in out['values']]
    return out


def opportunity_record(harness: str, payload: Mapping[str, Any], ctx: Mapping[str, Any], *,
                       packet_text: str | None = None) -> dict[str, Any]:
    """Build one opportunity record (1-3 s; runs in the detached child).

    P-7: state comes from the payload's task cwd, never the process cwd. Outside any repository the turn is
    recorded with empty state (git facts absent, as 6fee859 records non-repo turns). When the task's git state
    cannot be read at all (no cwd in the payload, a .git that git cannot open, a packet with no git claim)
    every git fact is unknown: the whole git source becomes one blocking unknown and the question is not
    narrowed, so the deterministic gate can never return ACT on it. Confidence fields in the payload are
    never read.
    """
    from . import state_packet as sp
    from .decision_opportunity import build_decision_opportunity, deterministic_gate
    cwd = _first(payload, 'cwd', 'workspaceRoot')
    repo, packet, scoped = None, {}, True
    if not cwd or not Path(cwd).is_dir():
        packet, scoped = _git_unreadable_packet('the payload carries no readable task cwd'), False
    elif (repo := sp.repo_root(cwd)) is None:
        if _git_marker(Path(cwd).resolve()):
            packet, scoped = _git_unreadable_packet('git cannot read the repository at the task cwd'), False
    else:
        packet = sp.build_state_packet(repo)
        if not any(str(c.get('key', '')).startswith('git.') for c in packet.get('current_claims') or []):
            packet = dict(packet, blocking_unknowns=[*(packet.get('blocking_unknowns') or []),
                                                     dict(GIT_UNKNOWN, reason='no git fact could be read')])
            scoped = False
    request = prompt_of(payload)
    opp = build_decision_opportunity(repo or '.', request, packet=packet, scoped=scoped, harness=harness,
                                     trace_id=ctx.get('trace_id'), attempt=ctx.get('attempt_id') or 0)
    gate = deterministic_gate(opp)
    if ctx.get('privacy_class') != 'request_opt_in':
        opp['intent'] = dict(opp['intent'], request=None)
    # packet_text (P-9): None keeps the record as before; 'redacted' digests State Packet text, 'opt_in' keeps it.
    extra = {} if packet_text is None else {'packet_text': packet_text}
    if packet_text == 'redacted':
        opp = redact_packet_text(opp)
    return {'schema': schema(harness, 'opportunity_record'), **ctx,
            'capture': ctx.get('capture') or capture_flags(request), 'repo': str(repo) if repo else None,
            'gate': gate, **extra, 'opportunity': opp}


def record_opportunity(harness: str, payload: Mapping[str, Any], ctx: Mapping[str, Any], *,
                       root: str | Path | None = None, packet_text: str | None = None) -> dict[str, Any] | None:
    if not supported(harness, root):
        return None
    row = append(harness, 'opportunity_record', opportunity_record(harness, payload, ctx, packet_text=packet_text),
                 root=root)
    revisions = (((row or {}).get('opportunity') or {}).get('invalidation') or {}).get('source_revisions')
    if revisions:  # what the stale-evidence check compares with at Stop time, without reading this file
        _update_session(harness, row.get('session_id'), root, lambda st: dict(
            st, revisions=_keep(st.get('revisions') or {}, {row['turn_key']: revisions})))
    return row


def observed_revisions(cwd: Any) -> dict[str, Any]:
    """The task repo's git HEAD now (one git call), to compare with the revision the opportunity saw."""
    try:
        proc = subprocess.run(['git', '--no-optional-locks', '-C', str(cwd), 'rev-parse', 'HEAD'], capture_output=True,
                              text=True, timeout=5, env=dict(os.environ, GIT_OPTIONAL_LOCKS='0'), check=False)
    except (OSError, subprocess.TimeoutExpired, TypeError):
        return {}
    return {'git': {'head': proc.stdout.strip()}} if proc.returncode == 0 and proc.stdout.strip() else {}


def _stale_sources(harness: str, session: Any, key: str, seen: Mapping[str, Any],
                   root: str | Path | None) -> list[str]:
    revisions = (_read_session(harness, session, root).get('revisions') or {}).get(key) or {}
    changed = []
    for source, now in seen.items():
        then = revisions.get(source)
        if isinstance(then, dict) and isinstance(now, dict) and any(
                k in then and then[k] != v for k, v in now.items()):
            changed.append(source)
    return sorted(changed)


def _uncertain_execution(ctx: Mapping[str, Any]) -> dict[str, Any]:
    from .cognition.mutation_outcome import MutationOutcome
    # A turn that ended early may have crossed an effect boundary: observe before any retry (F3).
    return MutationOutcome(mutation_key=str(ctx.get('turn_key')), authority_scope=str(ctx.get('harness')),
                           attempted=True, effect='unknown', verification='unverified',
                           retry_disposition='observe').to_dict()


def record_outcome(harness: str, ctx: Mapping[str, Any], behaviour: Mapping[str, Any], *, ended: str = 'completed',
                   seen_revisions: Mapping[str, Any] | None = None, root: str | Path | None = None,
                   extra: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
    """One turn_outcome row (content-free behaviour only) plus the F-case rows it implies."""
    row = {'schema': schema(harness, 'turn_outcome'), **{k: v for k, v in ctx.items() if k != 'capture'},
           **(extra or {}), 'label_kind': 'observed_behaviour_not_optimal', 'ended': ended, **behaviour}
    row = append(harness, 'turn_outcome', row, root=root)
    if row is None:
        return None
    if harness not in VERIFIERS:
        record_failure(harness, 'missing_verifier', ctx=ctx, root=root, detail={'record': 'turn_outcome'})
    missing = sorted(k for k in MEASURED if row.get(k) is None)
    if missing:
        record_failure(harness, 'partial_measurement', ctx=ctx, root=root, detail={'missing': missing})
    if ended != 'completed':
        record_failure(harness, 'uncertain_execution', ctx=ctx, root=root,
                       detail={'ended': ended, 'mutation_outcome': _uncertain_execution(ctx)})
    stale = _stale_sources(harness, row.get('session_id'), row['turn_key'], seen_revisions, root) \
        if seen_revisions else []
    if stale:
        record_failure(harness, 'stale_evidence', ctx=ctx, root=root, detail={'sources': stale})
    return row


def backfill_capture(harness: str, *, root: str | Path | None = None, env: Mapping[str, str] | None = None,
                     dry_run: bool = False) -> dict[str, int]:
    """One-shot and idempotent, for opportunity rows written before capture flags existed (request text kept,
    no ``capture``): compute their content-free flags here, on the capture side, and drop the stored request
    text unless privacy_class is request_opt_in. Until then the export puts such rows in cohort unknown.
    The file is rewritten in place under the writers' lock, so concurrent appends wait instead of being lost.
    """
    counts = {'rows': 0, 'flagged': 0, 'redacted': 0}
    keep = privacy_class(env, root) == 'request_opt_in'
    try:
        fh = open(state_dir(harness, root) / RECORD_FILES['opportunity_record'], 'r+', encoding='utf-8')
    except OSError:
        return counts
    with fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        out = []
        for line in fh:
            try:
                row = json.loads(line)
            except ValueError:
                row = None
            if not isinstance(row, dict):
                out.append(line if line.endswith('\n') else line + '\n')
                continue
            counts['rows'] += 1
            intent = (row.get('opportunity') or {}).get('intent')
            request = intent.get('request') if isinstance(intent, dict) else None
            if 'capture' not in row:
                row['capture'] = capture_flags(request)
                counts['flagged'] += 1
            if request is not None and not keep:
                intent['request'] = None
                counts['redacted'] += 1
            out.append(json.dumps(row, ensure_ascii=False, default=str) + '\n')
        if not dry_run and (counts['flagged'] or counts['redacted']):
            fh.seek(0)
            fh.truncate()
            fh.write(''.join(out))
    return counts


# ----------------------------------------------------------------------------- detached build
def child_argv(harness: str) -> list[str]:
    return [sys.executable, '-m', 'z0int.hook_adapter', '--harness', harness, 'opportunity']


def spawn_detached(argv: list[str], payload: Mapping[str, Any]) -> None:
    """Hand one job to a detached child (own session, no stdio): the hook returns while the child builds."""
    child = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
    child.stdin.write(json.dumps(payload, default=str).encode())
    child.stdin.close()


def slot_path(i: int, root: str | Path | None = None) -> Path:
    return _base(root) / 'runtime' / 'capture-slots' / f'{i}.lock'


def try_slot(root: str | Path | None = None) -> Any:
    """A free build slot now (its locked file; the lock lasts while any process holds the file open), else None."""
    for i in range(MAX_CHILDREN):
        path = slot_path(i, root)
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(path, 'a')
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            continue
        return fh
    return None


@contextlib.contextmanager
def build_slot(root: str | Path | None = None) -> Iterator[bool]:
    """One of MAX_CHILDREN build slots (flock, shared by every hook process); False after SLOT_WAIT_S."""
    deadline = time.monotonic() + SLOT_WAIT_S
    while True:
        fh = try_slot(root)
        if fh is not None:
            try:
                yield True
            finally:
                fh.close()
            return
        if time.monotonic() >= deadline:
            yield False
            return
        time.sleep(0.1)


# ----------------------------------------------------------------------------- in-process hosts: bounded spool
class Spool:
    """Bounded writer for hosts that live across turns (P-2/P-3 contracts).

    ``put`` never blocks: a full queue drops the row and persists the drop (drops.jsonl), so a later process
    reads the same count. ``close`` returns within ``close_timeout``: it lets the worker drain while time
    remains, then stops it between writes; whatever is still unwritten is counted as dropped, and nothing is
    written after it returns (a single write already in flight longer than the reserve is the only exception).
    """

    def __init__(self, harness: str, *, root: str | Path | None = None, maxsize: int = 1000,
                 close_timeout: float = 2.0, write: Callable[[str, Mapping[str, Any]], Any] | None = None):
        self.harness, self.root, self.close_timeout, self.dropped = harness, root, close_timeout, 0
        self._write = write or (lambda kind, row: append(harness, kind, row, root=root))
        self._queue: queue.Queue = queue.Queue(maxsize=maxsize)
        self._lock = threading.Lock()
        self._put_lock = threading.Lock()  # put's closed-check + enqueue vs close's seal: nothing slips between
        self._stopping = self._closed = self._sealed = False
        self._late: list[tuple[str, Any]] = []
        self._worker = threading.Thread(target=self._run, name=f'z0int-capture-{harness}', daemon=True)
        self._worker.start()

    def put(self, kind: str, row: Mapping[str, Any]) -> bool:
        with self._put_lock:
            reason = 'closed'
            if not self._sealed:
                try:
                    self._queue.put_nowait((kind, row))
                    return True
                except queue.Full:
                    reason = 'queue_full'
        self._drop(kind, 1, reason)
        return False

    def _drop(self, kind: str, count: int, reason: str) -> None:
        self.dropped += count
        try:
            record_drop(self.harness, kind, reason, count=count, root=self.root)
        except OSError:
            pass  # a lost drop marker never affects the host

    def _run(self) -> None:
        while True:
            try:
                item = self._queue.get(timeout=0.05)
            except queue.Empty:
                if self._stopping:
                    return
                continue
            if self._stopping:  # close() is waiting: never start another write
                self._late.append(item)
                return
            with self._lock:
                if self._closed:
                    self._late.append(item)
                    return
                try:
                    self._write(*item)
                except Exception:
                    pass  # shadow capture: a lost record never affects the host
            self._queue.task_done()

    def close(self) -> None:
        deadline = time.monotonic() + self.close_timeout
        reserve = min(0.5, self.close_timeout / 4)
        with self._put_lock:  # from here every put is a counted 'closed' drop; everything queued is drained below
            self._sealed = True
        while self._queue.unfinished_tasks and time.monotonic() < deadline - reserve:
            time.sleep(0.01)
        self._stopping = True
        locked = self._lock.acquire(timeout=max(0.0, deadline - time.monotonic()))
        self._closed = True
        if locked:
            self._lock.release()
        self._worker.join(timeout=max(0.0, min(0.2, deadline - time.monotonic())))
        left = list(self._late)
        while True:
            try:
                left.append(self._queue.get_nowait())
            except queue.Empty:
                break
        kinds: dict[str, int] = {}
        for kind, _ in left:
            kinds[kind] = kinds.get(kind, 0) + 1
        for kind, count in kinds.items():
            self._drop(kind, count, 'closed')


# ----------------------------------------------------------------------------- semantic view + frozen bundle
SEMANTIC = ('cohort', 'capture', 'gate', 'model_id', 'policy_revision', 'privacy_class', 'kind', 'label_kind', 'ended',
            'asked_user', 'detail')
FROZEN_IDS = ('schema', 'harness', 'turn_key', 'work_item_id', 'attempt_id', 'recorded_at')
FROZEN_MEASURES = ('tool_calls', 'assistant_messages', 'asked_via_tool')


def semantic_view(record: Mapping[str, Any]) -> dict[str, Any]:
    """What a record says about the turn, without who observed it (no harness, ids, times or measurement
    extras that only some harnesses expose; those gaps are partial_measurement rows)."""
    view = {'record': (parse_schema(record.get('schema')) or (None, None))[1],
            **{k: record[k] for k in SEMANTIC if k in record}}
    opp = record.get('opportunity')
    if isinstance(opp, Mapping):
        scope, intent = opp.get('scope') or {}, opp.get('intent') or {}
        view['opportunity'] = {
            'semantic_id': opp.get('semantic_id'), 'scope': {'mode': scope.get('mode'), 'families': scope.get('families')},
            'intent': {'revision': intent.get('revision'), 'effects': intent.get('effects')},
            'action_space': [{'kind': a.get('kind'), 'legal': a.get('legal')} for a in opp.get('action_space') or []]}
    return view


def frozen_row(record: Mapping[str, Any]) -> dict[str, Any]:
    return {**{k: record[k] for k in FROZEN_IDS if k in record}, **semantic_view(record),
            **{k: record[k] for k in FROZEN_MEASURES if k in record}}


def freeze(root: str | Path | None = None) -> dict[str, Any]:
    """A z0evals-style frozen bundle (#62 A2) over every state/<id>/ record file.

    One reader: the record family is parsed from each row's schema name alone, so a new id with the same
    fields needs no change here. Rows are text-free projections in canonical order; the bundle hash covers
    the rows and the manifest, so rebuilding the same files gives the same hash. Rows of an unknown version
    are counted in the manifest, never silently left out.
    """
    rows, by_schema, unknown_versions = [], {}, {}
    for path in sorted((_base(root) / 'state').glob('*/*.jsonl')):
        if path.name not in RECORD_FILES.values():
            continue
        for record in _read_jsonl(path):
            parsed = parse_schema(record.get('schema'))
            if parsed is None or parsed[2] != VERSION:
                unknown_versions[str(record.get('schema'))] = unknown_versions.get(str(record.get('schema')), 0) + 1
                continue
            rows.append(frozen_row(record))
            by_schema[record['schema']] = by_schema.get(record['schema'], 0) + 1
    rows.sort(key=lambda r: json.dumps(r, sort_keys=True))
    body = ''.join(json.dumps(r, sort_keys=True) + '\n' for r in rows)
    manifest = {'schema': 'z0int.capture.frozen_bundle.v0', 'rows': len(rows), 'by_schema': dict(sorted(by_schema.items())),
                'unsupported_schemas': dict(sorted(unknown_versions.items())),
                'rows_sha256': hashlib.sha256(body.encode()).hexdigest()}
    manifest['bundle_sha256'] = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    return {'manifest': manifest, 'rows': rows}
