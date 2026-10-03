"""Lean entry for Claude-compatible hook events (Claude Code, Codex, Grok) and normalized bridge events.

  python -m z0int.hook_adapter [--harness <id>] {prompt,stop,subagent-start,subagent-stop,session-start}

Reads one hook JSON object on stdin and always fails open. The harness is ``--harness`` or else comes from
the payload and the runner env (``harness_id.detect_hook_harness``), never from where the hook file lives;
a disagreement is written down as a misattribution failure row. Only claude-code keeps its ``automatic.*``
routing call and the opt-in SessionStart packet (``z0int.claude_code``, imported lazily); every other harness
is capture-only and prints nothing.

At import this module loads stdlib + ``harness_capture`` / ``harness_id`` only. The DecisionOpportunity
build runs in a detached child (internal event ``opportunity``) that holds one of a bounded set of slots.
"""

import argparse
import json
import os
import sys

from . import harness_capture as hc
from .harness_id import detect_hook_harness

EVENTS = ('prompt', 'stop', 'subagent-start', 'subagent-stop', 'session-start')
# Stop variants that end a turn early: its effects are uncertain (Claude names, Grok's snake_case names).
ENDED = {'StopFailure': 'failed', 'stop_failure': 'failed', 'StopCancelled': 'interrupted',
         'stop_cancelled': 'interrupted'}


def _ended(payload, env):
    return ENDED.get(env.get('GROK_HOOK_EVENT') or payload.get('hookEventName') or payload.get('hook_event_name'),
                     'completed')


def _behaviour(payload):
    """Content-free behaviour from the hook payload alone: whether the last reply asked the user."""
    text = payload.get('last_assistant_message') or payload.get('lastAssistantMessage')
    return {'asked_user': text.rstrip().endswith('?') if isinstance(text, str) else None,
            'tool_calls': None, 'assistant_messages': None}


def _subagent_outcome(harness, payload, ctx):
    """(ctx, behaviour) for a finished subagent; Claude Code agent transcripts give the measured behaviour
    and model (the transcript may lag the hook: then only the payload is used)."""
    path = payload.get('agent_transcript_path')
    if harness == 'claude-code' and path:
        from . import claude_code
        fresh = claude_code.messages(path)
        measured = claude_code.turn_behaviour(fresh)
        if measured['assistant_messages']:
            models = sorted({m.get('model') for m in fresh.values() if m.get('model')})
            return (dict(ctx, model_id=','.join(models)) if models else ctx), measured
    return ctx, _behaviour(payload)


def _claude_code(event, raw, payload, capture, env):
    from . import claude_code  # the one harness that keeps the automatic.* call and the SessionStart packet
    if event == 'session-start':
        if capture:
            hc.note_session('claude-code', payload, env=env)
        out = claude_code.on_session_start(raw)
    elif event == 'prompt':
        out = claude_code.on_prompt(payload, capture=capture)
    else:
        claude_code.on_stop(payload, record=hc.outcome_context('claude-code', payload, env=env) if capture else None,
                            outcome=capture)
        out = None
    return json.dumps(out, ensure_ascii=False) if out else None


def _opportunity_child(raw, harness):
    job = json.loads(raw)
    with hc.build_slot() as slot:
        if not slot:
            hc.record_drop(harness, 'opportunity_record', 'fanout_cap')
            return None
        hc.record_opportunity(harness, job['payload'], job['ctx'])
    return None


def handle(event, raw, harness=None, *, env=None):
    """One hook event -> the stdout text to print (None for nothing). Raises only on internal errors."""
    env = os.environ if env is None else env
    if event == 'opportunity':
        return _opportunity_child(raw, harness)
    capture = hc.enabled(env)
    try:
        payload = json.loads(raw)
    except ValueError:
        payload = None
    if not isinstance(payload, dict):
        if capture:
            hc.record_failure(harness or 'unknown', 'unsupported_schema',
                              detail={'record': 'hook_payload', 'schema_version': None})
        return None
    harness, conflict = detect_hook_harness(payload, env, harness)
    if capture and conflict and harness in hc.HARNESSES:
        hc.record_failure(harness, 'misattribution', detail=conflict)
    if harness == 'claude-code' and event in ('prompt', 'stop', 'session-start'):
        return _claude_code(event, raw, payload, capture, env)
    if not capture or not hc.supported(harness):
        return None
    if event == 'prompt':
        if hc.prompt_of(payload).strip():
            ctx = hc.begin_turn(harness, payload, env=env)
            hc.spawn_detached(hc.child_argv(harness), {'payload': payload, 'ctx': ctx})
    elif event == 'subagent-start':
        ctx = hc.subagent_turn(harness, payload, env=env)
        if ctx:
            hc.spawn_detached(hc.child_argv(harness), {'payload': payload, 'ctx': ctx})
    elif event == 'subagent-stop':
        ctx = hc.subagent_turn(harness, payload, env=env)
        if ctx:
            ctx, behaviour = _subagent_outcome(harness, payload, ctx)
            hc.record_outcome(harness, ctx, behaviour, ended=_ended(payload, env))
    elif event == 'stop':
        ctx = hc.outcome_context(harness, payload, env=env)
        if ctx['trace_id'] is not None:
            cwd = payload.get('cwd') or payload.get('workspaceRoot')
            hc.record_outcome(harness, ctx, _behaviour(payload), ended=_ended(payload, env),
                              seen_revisions=hc.observed_revisions(cwd) if cwd else None)
    elif event == 'session-start':
        hc.note_session(harness, payload, env=env)
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(prog='python -m z0int.hook_adapter', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--harness', default=None, help='harness id; default: from the payload and runner env')
    ap.add_argument('event', choices=[*EVENTS, 'opportunity'])
    args = ap.parse_args(argv)
    raw = sys.stdin.read()
    try:
        out = handle(args.event, raw, args.harness)
    except Exception:
        out = None  # a hook failure must never block or alter the native turn
    if out:
        print(out)
    return 0
