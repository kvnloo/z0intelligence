"""Claude Code hook adapter; no provider or routing policy here.

``prompt`` handles UserPromptSubmit through the shared automatic event path.
``stop`` projects the turn's billed usage from the Claude Code transcript into
Tokenomics as canonical ``tokenomics.event.v0`` rows (see claude_code_tokenomics). Both read one hook JSON object on stdin and always fail open.
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


def on_opportunity(hook, root=None):
    from .decision_opportunity import build_decision_opportunity, deterministic_gate
    from .state_packet import repo_root
    repo = repo_root(hook.get('cwd') or os.getcwd())
    if repo is None:
        return None
    opp = build_decision_opportunity(repo, hook['prompt'], harness=HARNESS, trace_id=turn_id(hook))
    record = {'schema': 'z0int.claude_code.opportunity_record.v0', 'session_id': hook.get('session_id'),
              'gate': deterministic_gate(opp), 'opportunity': opp}
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
        state = json.loads(state_path.read_text())
    except (OSError, ValueError):
        state = {}
    seen = set(state.get('message_ids', []))
    files = list(transcripts(hook))
    fresh_by_role = {}
    for role, path in files:
        fresh = {k: v for k, v in messages(path).items() if k not in seen}
        if fresh:
            fresh_by_role.setdefault(role, {}).update(fresh)
    # Canonical tokenomics.event.v0 per turn (observed usage + mechanism attribution + estimates).
    from . import claude_code_tokenomics as cct
    events = cct.events_for_stop(files, session_id=session, state=state,
                                 env_profile=os.environ.get(cct.PROFILE_ENV), root=root)
    for event in events:
        tokenomics_emit.emit_event(event, root=root)
    emitted = []
    for event in events:
        if event['name'] != 'claude_code.turn':
            continue
        emitted.append({'role': event['role'], 'usage': event['extra']['anthropic_usage'],
                        'messages': event['attributes']['claude_code.api_requests']})
    for role, fresh in fresh_by_role.items():
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
    state_path.write_text(json.dumps(state))
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
