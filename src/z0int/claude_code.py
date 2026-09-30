"""Claude Code hook adapter; no provider or routing policy here.

``prompt`` handles UserPromptSubmit through the shared automatic event path.
``stop`` projects the turn's billed usage from the Claude Code transcript into
Tokenomics. Both read one hook JSON object on stdin and always fail open.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

from . import automatic, paths, tokenomics_emit

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


def turn_id(hook):
    if isinstance(hook.get('prompt_id'), str) and hook['prompt_id']:
        return hook['prompt_id']
    # Older Claude Code sends no prompt_id; transcript size at submit time is stable per turn.
    try:
        size = Path(hook['transcript_path']).stat().st_size
    except (KeyError, OSError, TypeError):
        size = -1
    return hashlib.sha256(f"{hook.get('session_id')}\0{size}\0{hook.get('prompt')}".encode()).hexdigest()


def opportunities_path(root=None):
    return paths.ensure_layout(root)['state'] / HARNESS / 'opportunities.jsonl'


def emit_opportunity_async(hook):
    """z0int#62 shadow emission: build a DecisionOpportunity for this prompt OFF the hot path.

    The hook returns immediately; a detached child builds the record (1-3 s) and appends it to a
    private local file. Nothing is injected, routed or sent anywhere.
    """
    if os.environ.get('Z0INT_CLAUDE_CODE_OPPORTUNITIES', '1' if config().get('opportunities', True) else '0') == '0':
        return False
    import subprocess
    child = subprocess.Popen([sys.executable, '-m', 'z0int.claude_code', 'opportunity'], stdin=subprocess.PIPE,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    child.stdin.write(json.dumps(hook).encode())
    child.stdin.close()
    return True


HARNESS_MESSAGE_PREFIXES = ('<agent-message', '<task-notification', '<system-reminder')


def is_harness_message(text):
    """Subagent hand-backs and task notifications arrive as prompts but carry no user intent."""
    return isinstance(text, str) and text.lstrip().startswith(HARNESS_MESSAGE_PREFIXES)


def standing_grants(hook):
    """Claude Code's standing authority, authored by the user's permission mode: plan mode is read-only,
    any other mode may edit the working copy. Privileged work (push, PR, deploy, ...) is never standing."""
    return ('read',) if hook.get('permission_mode') == 'plan' else ('read', 'write')


def on_opportunity(hook, root=None):
    from .decision_opportunity import build_decision_opportunity, deterministic_gate
    from .effect_inference import infer_effects
    from .state_packet import build_state_packet, repo_root
    repo = repo_root(hook.get('cwd') or os.getcwd())
    if repo is None:
        return None
    packet = build_state_packet(repo)
    inferred = infer_effects(hook['prompt'], packet)
    grants = standing_grants(hook)
    opp = build_decision_opportunity(repo, hook['prompt'], effects=inferred['effects'], packet=packet,
                                     harness=HARNESS, trace_id=turn_id(hook), harness_grants=grants)
    gate_without_prompt_grants = deterministic_gate(opp)
    if inferred.get('prompt_grants') and inferred.get('privileged_actions'):
        # z0int#55 v1: the user's own prompt authorises exactly the actions it names, on the scope it names.
        from .decision_opportunity import with_prompt_grants
        opp = with_prompt_grants(opp, grants=inferred['prompt_grants'], required=inferred['privileged_actions'],
                                 source='user')
    # The hard-coded read-only emission this replaces, kept so the live cohort can compare both (shadow).
    baseline = build_decision_opportunity(repo, hook['prompt'], packet=packet, harness=HARNESS, trace_id=turn_id(hook))
    record = {'schema': 'z0int.claude_code.opportunity_record.v0', 'session_id': hook.get('session_id'),
              'gate': deterministic_gate(opp), 'gate_readonly_baseline': deterministic_gate(baseline),
              'gate_without_prompt_grants': gate_without_prompt_grants,
              'permission_mode': hook.get('permission_mode'), 'effects_inference': inferred, 'opportunity': opp}
    path = opportunities_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as fh:
        fh.write(json.dumps(record, ensure_ascii=False, default=str) + '\n')
    return record


def on_prompt(hook):
    text = hook.get('prompt')
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        if not is_harness_message(text):
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


def messages(path):
    """Assistant messages keyed by id; streamed chunks repeat an id with growing usage."""
    found = {}
    try:
        with open(path, encoding='utf-8', errors='replace') as stream:
            for line in stream:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                message = row.get('message') if row.get('type') == 'assistant' else None
                if not isinstance(message, dict) or not isinstance(message.get('usage'), dict) or not message.get('id'):
                    continue
                if message.get('model') == '<synthetic>':
                    continue
                found[message['id']] = message
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


def on_stop(hook, root=None):
    session = hook.get('session_id')
    if not session or not hook.get('transcript_path'):
        return []
    state_path = paths.ensure_layout(root)['state'] / HARNESS / f'{session}.json'
    try:
        seen = set(json.loads(state_path.read_text()).get('message_ids', []))
    except (OSError, ValueError):
        seen = set()
    emitted = []
    for role, path in transcripts(hook):
        fresh = {k: v for k, v in messages(path).items() if k not in seen}
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
        if role == 'root':
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('event', choices=['prompt', 'stop', 'session-start', 'opportunity'])
    args = parser.parse_args()
    try:
        if args.event == 'session-start':
            output = on_session_start(sys.stdin.read())
        elif args.event == 'opportunity':
            on_opportunity(json.load(sys.stdin))
            output = None
        else:
            hook = json.load(sys.stdin)
            output = on_prompt(hook) if args.event == 'prompt' else (on_stop(hook) and None)
    except Exception:
        # A hook failure must never block or alter the native turn.
        output = None
    if output:
        print(json.dumps(output, ensure_ascii=False))


if __name__ == '__main__':
    main()
