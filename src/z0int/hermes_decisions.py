"""Hermes decision layer (shadow): DecisionOpportunity records + observed turn outcomes.

The records are written by the Hermes plugin ``harness-adapters/hermes-z0intelligence`` through its child
``z0int.hermes_capture`` (the shared #62 capture core). This module classifies turns the same way the plugin
does and reports the gate against observed behaviour. Nothing is injected, routed or sent anywhere.

  z0int hermes decisions        # gate vs observed behaviour, joined by trace_id
  z0int hermes paths            # where the records live
"""
import argparse
import json
import sys

from . import paths

HARNESS = 'hermes'
# Agent roles that run turns without a human typing: never user intent.
NON_USER_PLATFORMS = frozenset({'cron', 'subagent', 'curator', 'kanban', 'batch', 'raft'})
# Whole-turn injections by Hermes itself (budget notices, background-process events, retries, kanban).
SYSTEM_PREFIXES = ('[System:', '[SYSTEM:', '[SYSTEM NOTICE', '[IMPORTANT: Background process', '[IMPORTANT:',
                   '<agent-message', '<task-notification', '<system-reminder')
NOTE_PREFIX = '[System note:'


def strip_system_notes(text):
    """Hermes prepends ``[System note: ...]`` blocks to real user messages; drop them, keep the ask."""
    text = text.lstrip()
    while text.startswith(NOTE_PREFIX):
        end = text.find(']')
        if end < 0:
            return ''
        text = text[end + 1:].lstrip()
    return text


def classify(user_message, platform='', parent_session_id='', task_id=''):
    """Return ``(reason, text)``: reason None means a user turn with ``text`` as the intent."""
    if (platform or '').lower() in NON_USER_PLATFORMS:
        return f'platform:{platform.lower()}', None
    if isinstance(user_message, list):  # multimodal content blocks
        user_message = '\n'.join(b.get('text', '') if isinstance(b, dict) else str(b) for b in user_message)
    if not isinstance(user_message, str) or not user_message.strip():
        return 'empty', None
    if user_message.lstrip().startswith(SYSTEM_PREFIXES):
        return 'system_message', None
    text = strip_system_notes(user_message)
    if not text:
        return 'system_message', None
    return None, text


def state_dir(root=None):
    return paths.ensure_layout(root)['state'] / HARNESS


def on_opportunity(payload, root=None):
    """Child-process body: build + append one DecisionOpportunity record (1-3 s, off the hot path)."""
    from .decision_opportunity import build_decision_opportunity, deterministic_gate
    from .state_packet import repo_root
    reason, text = classify(payload.get('user_message'), payload.get('platform', ''))
    if reason:
        return None
    repo = repo_root(payload['cwd']) if payload.get('cwd') else None
    # Factory turns often run outside a repo; record them with empty state rather than dropping them.
    opp = build_decision_opportunity(repo or '.', text, harness=HARNESS, trace_id=payload.get('trace_id'),
                                     packet=None if repo else {})
    record = {'schema': 'z0int.hermes.opportunity_record.v0', 'session_id': payload.get('session_id'),
              'platform': payload.get('platform'), 'repo': str(repo) if repo else None,
              'gate': deterministic_gate(opp), 'opportunity': opp}
    path = state_dir(root) / 'opportunities.jsonl'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as fh:
        fh.write(json.dumps(record, ensure_ascii=False, default=str) + '\n')
    return record


def _rows(path):
    try:
        return [json.loads(l) for l in path.read_text(encoding='utf-8').splitlines() if l.strip()]
    except (OSError, ValueError):
        return []


# What each gate verdict would look like if the agent had followed it.
GATE_EXPECTS = {'ACT': 'answered', 'ASK': 'asked', 'ESCALATE': 'escalated'}


def observed(out):
    if out.get('asked_user'):
        return 'asked'
    if out.get('escalated'):
        return 'escalated'
    if out.get('ended') and out.get('ended') != 'completed':
        return out['ended']
    return 'answered'


def decisions_report(root=None):
    """Join shadow DecisionOpportunity records with observed Hermes turn outcomes."""
    base = state_dir(root)
    outcomes, excluded = {}, {}
    for r in _rows(base / 'outcomes.jsonl'):
        if r.get('excluded'):
            excluded[r['excluded']] = excluded.get(r['excluded'], 0) + 1
        elif r.get('trace_id') and r['trace_id'] not in outcomes:
            outcomes[r['trace_id']] = r
    table, unlinked, examples, tools, overhead = {}, 0, [], 0, []
    dropped = sum(int(r.get('count') or 0) for r in _rows(base / 'drops.jsonl'))
    for rec in _rows(base / 'opportunities.jsonl'):
        opp = rec['opportunity']
        out = outcomes.get(opp['trace'].get('trace_id'))
        if out is None:
            unlinked += 1
            continue
        obs = observed(out)
        tools += int(out.get('tool_calls') or 0)
        if out.get('hook_ms') is not None:
            overhead.append(float(out['hook_ms']))
        key = (rec['gate'], obs)
        table[key] = table.get(key, 0) + 1
        if GATE_EXPECTS.get(rec['gate']) != obs and len(examples) < 10:
            examples.append({'gate': rec['gate'], 'observed': obs, 'scope': opp['scope'].get('mode'),
                             'families': opp['scope'].get('families'), 'platform': rec.get('platform'),
                             'request': (opp['intent'].get('request') or '')[:80] or None})
    overhead.sort()
    return {'harness': HARNESS, 'linked': sum(table.values()), 'unlinked': unlinked, 'rows_dropped': dropped,
            'non_user_turns_excluded': excluded, 'tool_calls_on_linked_turns': tools,
            'hook_ms_p50': overhead[len(overhead) // 2] if overhead else None,
            'hook_ms_max': overhead[-1] if overhead else None,
            'table': {f'{g}->{o}': n for (g, o), n in sorted(table.items())}, 'disagreements': examples}


def _main(argv=None):
    ap = argparse.ArgumentParser(prog='z0int hermes', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('decisions', help='gate vs observed behaviour on live shadow DecisionOpportunity records')
    sub.add_parser('paths', help='print the opportunity/outcome record paths')
    sub.add_parser('opportunity', help='(plugin child) read one turn payload on stdin and record it')
    args = ap.parse_args(argv)
    if args.cmd == 'decisions':
        print(json.dumps(decisions_report(), indent=1, ensure_ascii=False))
    elif args.cmd == 'paths':
        base = state_dir()
        print(json.dumps({name: str(base / f'{name}.jsonl')
                          for name in ('opportunities', 'outcomes', 'events', 'failures', 'drops')}, indent=1))
    else:
        try:
            on_opportunity(json.load(sys.stdin))
        except Exception:
            pass  # shadow emission never surfaces errors into the harness
    return 0


if __name__ == '__main__':
    sys.exit(_main())
