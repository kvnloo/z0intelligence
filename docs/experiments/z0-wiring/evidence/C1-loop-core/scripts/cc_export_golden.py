"""Generate the Claude Code export golden from the 6fee859 tree (run with PYTHONPATH=<base>/src).

Synthetic CC hook payloads (repo turns, non-repo turns, a harness-injected prompt) are replayed through the
6fee859 capture path (claude_code.on_prompt -> detached on_opportunity, on_stop) with a fixed synthetic State
Packet, then exported with loop_export.build_table + manifest counts. Output: the inputs and the expected
export, as one JSON document (tests/fixtures/cc_export_6fee859.json).
"""
import io
import json
import subprocess
import sys
import tempfile
from pathlib import Path

PACKET = {'schema': 'z0int.state_packet.v0', 'packet_id': 'p-fixture', 'built_at': '2026-09-30T10:00:00Z',
          'current_claims': [{'key': 'git.branch', 'value': 'feat/fixture', 'evidence': ['e:1'], 'status': 'observed'},
                             {'key': 'git.head', 'value': 'abc123', 'evidence': ['e:2'], 'status': 'observed'}],
          'superseded_claims': [], 'contradictions': [], 'unknowns': [{'key': 'gh.issues', 'reason': 'gh unavailable'}],
          'blocking_unknowns': [], 'coverage': {'git': 'full', 'docs': 'full'}, 'evidence': {},
          'source_revisions': {'git': {'head': 'abc123', 'status': 's', 'refs': 'r'},
                               'resource': {'posture': 'p', 'factory': 'BURN'}},
          'allowed_transitions': []}

# (session, prompt_id, cwd placeholder, prompt, assistant reply, tool calls)
TURNS = [
    ('A', 'a1', '{repo}', 'which branch is checked out?', 'feat/fixture. Anything else?', 0),
    ('A', 'a2', '{repo}', 'is the CI status green on the pull request?', 'I cannot see CI from here.', 1),
    ('A', 'a3', '{repo}', '<task-notification>agent x finished</task-notification>', 'Noted.', 0),
    ('A', 'a4', '{repo}', 'please refactor the payments module', 'Done, tests pass.', 3),
    ('B', 'b1', '{plain}', 'draft an email to the team about the outage', 'Here is a draft.', 0),
    ('B', 'b2', '{plain}', 'which branch am I on?', 'Which repository do you mean?', 0),
]
VERIFIED = [
    ('A', 'a1', 'verified_success'), ('A', 'a2', 'unverified'), ('A', 'a4', 'verified_failure'),
    ('B', 'b1', 'contested'), ('B', 'b2', 'verified_success'),
]
COHORTS = {'A': 'interactive', 'B': 'agent', 'L': 'interactive'}
# Legacy rows (integrate/claude-code-z0-stack era): written by on_opportunity for every prompt, harness-injected
# ones included, with the request text and no capture flags. 6fee859 dropped the harness one at export.
LEGACY = [('L', 'l1', '{repo}', 'run the test suite and report'),
          ('L', 'l2', '{repo}', '<task-notification>agent y finished</task-notification>')]
LEGACY_VERIFIED = [('L', 'l1', 'verified_success'), ('L', 'l2', 'verified_failure')]


def verified_row(session, trace, state):
    sig = [] if state == 'unverified' else [{'kind': 'tests_in_turn', 'polarity': 1 if state == 'verified_success' else -1,
                                             'confidence': 'medium', 'label_class': 'deterministic_gold',
                                             'oracle': 'test_runner'}]
    return {'schema': 'z0int.claude_code.turn_outcome_verified.v0', 'session_id': session, 'trace_id': trace,
            'verification_state': state, 'label_class': 'unknown' if state == 'unverified' else 'deterministic_gold',
            'label_confidence': None, 'signals': sig, 'turn': {'started_at': '2026-09-30T10:00:05Z'},
            'verifier': {'id': 'x', 'version': '0.2.0'}}


def transcript_lines(prompt_id, reply, tools):
    content = [{'type': 'tool_use', 'name': 'Bash', 'input': {}}] * tools + [{'type': 'text', 'text': reply}]
    return [{'type': 'assistant', 'message': {'id': f'msg-{prompt_id}', 'model': 'claude-fixture',
                                              'usage': {'input_tokens': 1, 'output_tokens': 1}, 'content': content}}]


def payloads(work):
    """Yield (event, payload, transcript lines to append before the event) for the whole fixture set."""
    for session, pid, cwd, prompt, reply, tools in TURNS:
        cwd = cwd.format(repo=work / 'repo', plain=work / 'plain')
        t = str(work / f'{session}.jsonl')
        yield 'prompt', {'hook_event_name': 'UserPromptSubmit', 'session_id': session, 'prompt_id': pid,
                         'transcript_path': t, 'cwd': cwd, 'prompt': prompt}, []
        yield 'stop', {'hook_event_name': 'Stop', 'session_id': session, 'prompt_id': pid, 'transcript_path': t,
                       'cwd': cwd, 'stop_hook_active': False, 'last_assistant_message': reply}, \
            transcript_lines(pid, reply, tools)


def export(state):
    from z0int import loop_export as le
    rows = le.build_table(state, cohort_fn=lambda sid: COHORTS.get(sid, 'unknown'))
    le.assert_private(rows)
    return {'rows': rows, 'counts': le.manifest(rows, sources={}, generated_at=0)['counts']}


def main():
    work = Path(tempfile.mkdtemp(dir=sys.argv[1]))
    (work / 'repo').mkdir()
    (work / 'plain').mkdir()
    import os
    os.environ['Z0INT_HOME'] = str(work / 'z0')
    from z0int import automatic, claude_code
    from z0int import state_packet as sp
    sp.repo_root = lambda p: Path(p) if str(p).endswith('repo') else None
    sp.build_state_packet = lambda *a, **k: json.loads(json.dumps(PACKET))
    automatic.handle_event = lambda e: {'action': 'native', 'disabled': True}

    class InlineChild:  # the detached opportunity child, run in-process
        def __init__(self, argv, **kw):
            assert argv[-1] == 'opportunity'
            outer = self

            class Stdin(io.BytesIO):
                def close(self):
                    claude_code.on_opportunity(json.loads(self.getvalue()))
                    super().close()
            outer.stdin = Stdin()

    subprocess.Popen = InlineChild
    for event, p, lines in payloads(work):
        if lines:
            with open(p['transcript_path'], 'a') as fh:
                fh.write(''.join(json.dumps(r) + '\n' for r in lines))
        if event == 'prompt':
            claude_code.on_prompt(p)
        else:
            claude_code.on_stop(p)
    state = work / 'z0' / 'state' / 'claude-code'
    with open(state / 'outcomes_verified.jsonl', 'w') as fh:
        fh.write(''.join(json.dumps(verified_row(*v)) + '\n' for v in VERIFIED))
    out = export(state)
    for session, pid, cwd, prompt in LEGACY:
        claude_code.on_opportunity({'hook_event_name': 'UserPromptSubmit', 'session_id': session, 'prompt_id': pid,
                                    'cwd': cwd.format(repo=work / 'repo', plain=work / 'plain'), 'prompt': prompt})
    legacy = [r for r in map(json.loads, open(state / 'opportunities.jsonl')) if r['session_id'] == 'L']
    with open(state / 'outcomes_verified.jsonl', 'a') as fh:
        fh.write(''.join(json.dumps(verified_row(*v)) + '\n' for v in LEGACY_VERIFIED))
    with_legacy = export(state)
    doc = {'about': 'C1-loop-core regression guard: exported CC training rows + cohorts from 6fee859 capture',
           'generated_by': '6fee8594e67dcd63684f50f6ec0c5445c5a98bed', 'packet': PACKET, 'turns': TURNS,
           'verified': VERIFIED, 'cohorts': COHORTS, 'expected': out,
           'legacy_rows': legacy, 'legacy_verified': LEGACY_VERIFIED, 'expected_with_legacy': with_legacy}
    print(json.dumps(doc, indent=1, sort_keys=True))


if __name__ == '__main__':
    main()
