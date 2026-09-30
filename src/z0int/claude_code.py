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


def shadow():
    # Shadow is the default: route and record, never change what the model sees.
    return os.environ.get('Z0INT_CLAUDE_CODE_SHADOW', '1') != '0'


def turn_id(hook):
    if isinstance(hook.get('prompt_id'), str) and hook['prompt_id']:
        return hook['prompt_id']
    # Older Claude Code sends no prompt_id; transcript size at submit time is stable per turn.
    try:
        size = Path(hook['transcript_path']).stat().st_size
    except (KeyError, OSError, TypeError):
        size = -1
    return hashlib.sha256(f"{hook.get('session_id')}\0{size}\0{hook.get('prompt')}".encode()).hexdigest()


def on_prompt(hook):
    text = hook.get('prompt')
    if not isinstance(text, str) or not text.strip():
        return None
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
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({'message_ids': sorted(seen)}))
    return emitted


def on_session_start(stdin_text):
    # Opt-in: measured as an aid to tools (held-out 14/14 vs 12/14, -30% input tokens),
    # not a replacement for them; off by default until a per-host A/B says otherwise.
    if os.environ.get('Z0INT_CLAUDE_CODE_PACKET') != '1':
        return None
    from .state_packet import session_start_hook
    out = session_start_hook(stdin_text, max_tokens=int(os.environ.get('Z0INT_CLAUDE_CODE_PACKET_TOKENS', '1500')))
    return out if out['hookSpecificOutput']['additionalContext'] else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('event', choices=['prompt', 'stop', 'session-start'])
    args = parser.parse_args()
    try:
        if args.event == 'session-start':
            output = on_session_start(sys.stdin.read())
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
