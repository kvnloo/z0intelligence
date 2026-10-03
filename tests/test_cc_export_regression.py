"""Regression guard: the Claude Code export through the new hook path equals the 6fee859 export.

tests/fixtures/cc_export_6fee859.json holds synthetic CC hook payloads, a fixed State Packet and the training
rows + manifest counts that the 6fee859 capture path (claude_code.on_prompt / on_opportunity / on_stop)
exported for them. Raw opportunity rows may differ (request text is opt-in now); exported rows may not.
"""
import json
from pathlib import Path

from z0int import automatic
from z0int import harness_capture as hc
from z0int import hook_entry as he
from z0int import loop_export as le
from z0int import state_packet as sp

GOLDEN = json.loads((Path(__file__).parent / 'fixtures' / 'cc_export_6fee859.json').read_text())


def verified_row(session, trace, state):
    sig = [] if state == 'unverified' else [{'kind': 'tests_in_turn', 'polarity': 1 if state == 'verified_success' else -1,
                                             'confidence': 'medium', 'label_class': 'deterministic_gold',
                                             'oracle': 'test_runner'}]
    return {'schema': 'z0int.claude_code.turn_outcome_verified.v0', 'session_id': session, 'trace_id': trace,
            'verification_state': state, 'label_class': 'unknown' if state == 'unverified' else 'deterministic_gold',
            'label_confidence': None, 'signals': sig, 'turn': {'started_at': '2026-09-30T10:00:05Z'},
            'verifier': {'id': 'x', 'version': '0.2.0'}}


def test_claude_code_export_equals_the_6fee859_export(monkeypatch, tmp_path):
    monkeypatch.setenv('Z0INT_HOME', str(tmp_path / 'z0'))
    for name in ('Z0INT_CAPTURE', 'Z0INT_CAPTURE_PRIVACY', 'Z0INT_CLAUDE_CODE_OPPORTUNITIES', 'Z0INT_CLAUDE_CODE_SHADOW'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(sp, 'repo_root', lambda p: Path(p) if str(p).endswith('repo') else None)
    monkeypatch.setattr(sp, 'build_state_packet', lambda *a, **k: json.loads(json.dumps(GOLDEN['packet'])))
    monkeypatch.setattr(automatic, 'handle_event', lambda e: {'action': 'native', 'disabled': True})
    monkeypatch.setattr(hc, 'spawn_detached',
                        lambda argv, job: he.handle('opportunity', json.dumps(job), argv[argv.index('--harness') + 1]))
    (tmp_path / 'repo').mkdir()
    (tmp_path / 'plain').mkdir()
    for session, pid, cwd, prompt, reply, tools in GOLDEN['turns']:
        cwd = cwd.format(repo=tmp_path / 'repo', plain=tmp_path / 'plain')
        t = tmp_path / f'{session}.jsonl'
        he.handle('prompt', json.dumps({'hook_event_name': 'UserPromptSubmit', 'session_id': session, 'prompt_id': pid,
                                        'transcript_path': str(t), 'cwd': cwd, 'prompt': prompt}), 'claude-code', env={})
        content = [{'type': 'tool_use', 'name': 'Bash', 'input': {}}] * tools + [{'type': 'text', 'text': reply}]
        with t.open('a') as fh:
            fh.write(json.dumps({'type': 'assistant', 'message': {'id': f'msg-{pid}', 'model': 'claude-fixture',
                                                                  'usage': {'input_tokens': 1, 'output_tokens': 1},
                                                                  'content': content}}) + '\n')
        he.handle('stop', json.dumps({'hook_event_name': 'Stop', 'session_id': session, 'prompt_id': pid,
                                      'transcript_path': str(t), 'cwd': cwd, 'stop_hook_active': False,
                                      'last_assistant_message': reply}), 'claude-code', env={})
    state = tmp_path / 'z0' / 'state' / 'claude-code'
    with (state / 'outcomes_verified.jsonl').open('w') as fh:
        fh.write(''.join(json.dumps(verified_row(*v)) + '\n' for v in GOLDEN['verified']))
    rows = le.build_table(state, cohort_fn=lambda sid: GOLDEN['cohorts'].get(sid, 'unknown'))
    le.assert_private(rows)
    counts = le.manifest(rows, sources={}, generated_at=0)['counts']
    assert json.loads(json.dumps(rows)) == GOLDEN['expected']['rows']
    assert counts == GOLDEN['expected']['counts']
    raw = (state / 'opportunities.jsonl').read_text()
    assert 'payments' not in raw  # the raw rows did change: request text is no longer stored by default
