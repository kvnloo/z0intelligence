"""Claude Code hook adapter; no provider or routing policy here.

A thin wrapper over the shared capture core (``harness_capture``, reached through
``python -m z0int.hook_adapter --harness claude-code``). What stays Claude Code specific:
``prompt`` keeps the shared automatic event path (the only harness that does), ``stop`` projects the
turn's billed usage from the Claude Code transcript into Tokenomics and measures the turn's behaviour
from the transcript messages of that prompt, and ``session-start`` serves the opt-in State Packet. Every
hook always fails open.
"""
import hashlib
import json
import os
from pathlib import Path
import sys

from . import automatic, harness_capture, paths, tokenomics_emit
# Moved to the capture core; re-exported for existing callers.
from .harness_capture import HARNESS_MESSAGE_PREFIXES, is_harness_message, turn_id  # noqa: F401

HARNESS = 'claude-code'
USAGE_KEYS = ('input_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens', 'output_tokens')


def config():
    """Host settings for interactive sessions (env vars still override): ~/.z0int/config/claude-code.json."""
    try:
        return json.loads((paths.home() / 'config' / 'claude-code.json').read_text())
    except (OSError, ValueError):
        return {}


def shadow():
    # Shadow is the default: route and record, never change what the model sees.
    env = os.environ.get('Z0INT_CLAUDE_CODE_SHADOW')
    return env != '0' if env is not None else config().get('shadow', True) is not False


def emit_opportunity_async(hook):
    """z0int#62 shadow emission: build a DecisionOpportunity for this prompt OFF the hot path.

    The hook only fixes the turn's ids and work item (harness_capture.begin_turn) and returns; a detached
    child builds the record (1-3 s) and appends it to a private local file. Nothing is injected, routed or
    sent anywhere.
    """
    if os.environ.get('Z0INT_CLAUDE_CODE_OPPORTUNITIES', '1' if config().get('opportunities', True) else '0') == '0':
        return False
    ctx = harness_capture.begin_turn(HARNESS, hook)
    harness_capture.spawn_detached(harness_capture.child_argv(HARNESS), {'payload': hook, 'ctx': ctx})
    return True


def on_opportunity(hook, root=None):
    """One opportunity record through the shared core (also the body of older in-flight children).

    Sessions often run outside a repo; those are recorded with empty state rather than dropped.
    """
    ctx = harness_capture.begin_turn(HARNESS, hook, root=root)
    return harness_capture.record_opportunity(HARNESS, hook, ctx, root=root)


def on_prompt(hook, capture=None):
    text = hook.get('prompt')
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        # Claude Code keeps 6fee859's rule: harness-injected prompts get no opportunity record here. They
        # still open a turn (cohort harness, own work item), so their Stop closes that turn, not a stranger's.
        if harness_capture.enabled() if capture is None else capture:
            if is_harness_message(text):
                harness_capture.begin_turn(HARNESS, hook)
            else:
                emit_opportunity_async(hook)
    except Exception:
        pass  # shadow emission never affects the turn
    session = hook.get('session_id') or HARNESS
    event = dict(harness=HARNESS, session_id=session, turn_id=turn_id(hook), instance_id=session, text=text)
    result = automatic.handle_event(event)
    if shadow() or result.get('action') != 'context':
        # Nothing reaches the model, so nothing is recorded as delivered.
        return None
    try:
        automatic.post('/v1/automatic/consumed', dict(harness=HARNESS, instance_id=session, receipt_id=result['receipt_id']))
    except Exception:
        pass
    return {'hookSpecificOutput': {'hookEventName': 'UserPromptSubmit', 'additionalContext': result['context']}}


def messages(path, owners=None):
    """Assistant messages keyed by id; streamed chunks repeat an id with growing usage.

    ``owners`` (a dict) is filled with each message's prompt: the ``promptId`` of the user line before it
    (None in transcripts that carry no prompt ids).
    """
    found, prompt = {}, None
    try:
        with open(path, encoding='utf-8', errors='replace') as stream:
            for line in stream:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get('type') == 'user' and row.get('promptId'):
                    prompt = row['promptId']
                message = row.get('message') if row.get('type') == 'assistant' else None
                if not isinstance(message, dict) or not isinstance(message.get('usage'), dict) or not message.get('id'):
                    continue
                if message.get('model') == '<synthetic>':
                    continue
                found[message['id']] = message
                if owners is not None:
                    owners[message['id']] = prompt
    except OSError:
        pass
    return found


def transcripts(hook):
    main = Path(hook['transcript_path'])
    yield 'root', main
    subagents = main.with_suffix('') / 'subagents'
    if subagents.is_dir():
        for path in sorted(subagents.glob('*.jsonl')):
            yield 'subagent', path


def turn_behaviour(fresh):
    """Observed behaviour of the turn (z0int#54): what the frontier did — NOT an optimal label."""
    ordered = [fresh[k] for k in sorted(fresh)]
    tools = [b for m in ordered for b in (m.get('content') or []) if isinstance(b, dict) and b.get('type') == 'tool_use']
    last_text = ''
    for m in reversed(ordered):
        texts = [b.get('text', '') for b in (m.get('content') or []) if isinstance(b, dict) and b.get('type') == 'text']
        if texts:
            last_text = texts[-1].strip()
            break
    asked_tool = any(t.get('name') == 'AskUserQuestion' for t in tools)
    return {'asked_user': asked_tool or last_text.endswith('?'), 'asked_via_tool': asked_tool,
            'tool_calls': len(tools), 'assistant_messages': len(ordered)}


def outcomes_path(root=None):
    return paths.ensure_layout(root)['state'] / HARNESS / 'outcomes.jsonl'


def _measure(hook, ctx, found, owners, fresh, root=None):
    """The root turn's outcome in the shared record family; nothing about it is silent.

    Measured from the transcript messages of the Stop's own prompt (owner promptId; in transcripts without
    prompt ids, the messages no earlier Stop has seen). When none are there yet (the Stop can fire before
    Claude Code flushes the reply) the outcome comes from the payload and carries a partial_measurement row.
    Unseen messages of an EARLIER prompt arrived after that prompt's Stop: they are not credited to this
    turn but close the earlier one with a partial_measurement row (``late_messages``).
    """
    pid = ctx.get('trace_id')
    if pid is None:
        harness_capture.record_failure(HARNESS, 'partial_measurement', ctx=ctx, root=root,
                                       detail={'record': 'turn_outcome', 'missing': ['turn']})
        return
    mine = {k: m for k, m in found.items() if owners.get(k) == pid or (owners.get(k) is None and k in fresh)}
    behaviour, error = harness_capture.payload_behaviour(hook), None
    if mine:
        try:
            behaviour = turn_behaviour(mine)
            models = sorted({m.get('model') for m in mine.values() if m.get('model')})
            ctx = dict(ctx, model_id=','.join(models)) if models else ctx
        except Exception as exc:  # an unexpected transcript shape: keep the payload outcome, say why
            error = type(exc).__name__
    cwd = hook.get('cwd')
    row = harness_capture.record_outcome(HARNESS, ctx, behaviour, root=root,
                                         seen_revisions=harness_capture.observed_revisions(cwd) if cwd else None)
    if error and row:
        harness_capture.record_failure(HARNESS, 'partial_measurement', ctx=ctx, root=root,
                                       detail={'record': 'turn_outcome', 'error': error})
    late = {}
    for k in fresh:
        if owners.get(k) not in (None, pid):
            late[owners[k]] = late.get(owners[k], 0) + 1
    for other, count in sorted(late.items()):
        octx = harness_capture.outcome_context(HARNESS, {'session_id': hook.get('session_id'), 'prompt_id': other},
                                               env={}, root=root)
        harness_capture.record_failure(HARNESS, 'partial_measurement', ctx=octx, root=root,
                                       detail={'record': 'turn_outcome', 'late_messages': count})


def on_stop(hook, root=None, record=None, outcome=True):
    """Usage receipts for every fresh transcript message, plus the root turn's observed outcome row.

    ``record`` (the capture core's outcome context) writes the outcome in the shared record family
    (``_measure``); without it the 6fee859 row is written (direct callers). ``outcome=False`` (capture off)
    writes usage only.
    """
    session = hook.get('session_id')
    if not session:
        return []
    state_path = paths.ensure_layout(root)['state'] / HARNESS / f'{session}.json'
    try:
        seen = set(json.loads(state_path.read_text()).get('message_ids', []))
    except (OSError, ValueError):
        seen = set()
    emitted, owners, root_found, root_fresh = [], {}, {}, set()
    for role, path in transcripts(hook) if hook.get('transcript_path') else ():
        found = messages(path, owners if role == 'root' else None)
        fresh = {k: v for k, v in found.items() if k not in seen}
        if role == 'root':
            root_found, root_fresh = found, set(fresh)
        if not fresh:
            continue
        usage = {key: sum(int(m['usage'].get(key) or 0) for m in fresh.values()) for key in USAGE_KEYS}
        models = sorted({m.get('model') for m in fresh.values() if m.get('model')})
        last = sorted(fresh)[-1]
        tokenomics_emit.emit_provider_usage(
            harness=HARNESS, trace_id=hashlib.sha256(f'{session}\0{role}\0{last}'.encode()).hexdigest(),
            session_id=session, provider='anthropic', model=models[0] if len(models) == 1 else ','.join(models),
            usage=usage, role=role, measurement_state='complete', observer_id='z0int.claude_code.stop',
            physical_source_id=path.name, identity_basis='transcript_message_id',
            extra={'message_count': len(fresh)}, root=root)
        seen.update(fresh)
        emitted.append({'role': role, 'usage': usage, 'messages': len(fresh)})
        if role == 'root' and outcome and record is None:
            try:  # link to the prompt's DecisionOpportunity record by trace id (prompt_id)
                row = {'schema': 'z0int.claude_code.turn_outcome.v0', 'session_id': session,
                       'trace_id': hook.get('prompt_id'), 'label_kind': 'observed_behaviour_not_optimal',
                       **turn_behaviour(fresh)}
                out = outcomes_path(root)
                out.parent.mkdir(parents=True, exist_ok=True)
                with out.open('a', encoding='utf-8') as fh:
                    fh.write(json.dumps(row) + '\n')
            except Exception:
                pass
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({'message_ids': sorted(seen)}))
    if outcome and record is not None:
        try:
            _measure(hook, record, root_found, owners, root_fresh, root=root)
        except Exception as exc:  # fail open, but never silently
            harness_capture.record_failure(HARNESS, 'partial_measurement', ctx=record, root=root,
                                           detail={'record': 'turn_outcome', 'error': type(exc).__name__})
    return emitted


def on_session_start(stdin_text):
    # Opt-in: measured as an aid to tools (held-out 14/14 vs 12/14, -30% input tokens),
    # not a replacement for them; off by default until a per-host A/B says otherwise.
    env = os.environ.get('Z0INT_CLAUDE_CODE_PACKET')
    if not (env == '1' if env is not None else config().get('packet') is True):
        return None
    from .state_packet import session_start_hook
    out = session_start_hook(stdin_text, max_tokens=int(os.environ.get('Z0INT_CLAUDE_CODE_PACKET_TOKENS', '1500')))
    return out if out['hookSpecificOutput']['additionalContext'] else None


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('event', choices=['prompt', 'stop', 'session-start', 'opportunity'])
    args = parser.parse_args()
    if args.event != 'opportunity':
        # Older installed hooks call this module: they take the shared hook adapter path now.
        from .hook_entry import main as hook_main
        return hook_main(['--harness', HARNESS, args.event])
    try:
        on_opportunity(json.load(sys.stdin))
    except Exception:
        pass  # a hook failure must never block or alter the native turn
    return 0


if __name__ == '__main__':
    main()
