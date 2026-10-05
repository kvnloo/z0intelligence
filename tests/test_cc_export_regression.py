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


def replay(monkeypatch, tmp_path):
    """The fixture's hook payloads through the new hook path; returns the claude-code state dir."""
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
    return state


def export(state):
    return le.build_table(state, cohort_fn=lambda sid: GOLDEN['cohorts'].get(sid, 'unknown'))


def test_claude_code_export_equals_the_6fee859_export(monkeypatch, tmp_path):
    state = replay(monkeypatch, tmp_path)
    rows = le.build_table(state, cohort_fn=lambda sid: GOLDEN['cohorts'].get(sid, 'unknown'))
    le.assert_private(rows)
    counts = le.manifest(rows, sources={}, generated_at=0)['counts']
    assert json.loads(json.dumps(rows)) == GOLDEN['expected']['rows']
    assert counts == GOLDEN['expected']['counts']
    raw = (state / 'opportunities.jsonl').read_text()
    assert 'payments' not in raw  # the raw rows did change: request text is no longer stored by default


def test_legacy_rows_never_export_as_user_rows_and_the_backfill_restores_the_6fee859_export(monkeypatch, tmp_path):
    """Opportunity rows written before capture flags existed (request text, no ``capture``) next to the hook path.

    6fee859 dropped the legacy harness-injected row by reading its request at export. The export no longer reads
    request text, so an unflagged row is never a user (interactive/agent) row; the capture-side backfill flags it
    and drops the stored text, after which the user rows equal the 6fee859 export again.
    """
    state = replay(monkeypatch, tmp_path)
    with (state / 'opportunities.jsonl').open('a') as fh:
        fh.write(''.join(json.dumps(r) + '\n' for r in GOLDEN['legacy_rows']))
    with (state / 'outcomes_verified.jsonl').open('a') as fh:
        fh.write(''.join(json.dumps(verified_row(*v)) + '\n' for v in GOLDEN['legacy_verified']))
    legacy_keys = {le._sha({'session': r['session_id'], 'trace': r['opportunity']['trace']['trace_id']})
                   for r in GOLDEN['legacy_rows']}
    before = [r for r in export(state) if r['turn_key'] in legacy_keys]
    assert len(before) == 2 and all(r['cohort'] not in ('interactive', 'agent') for r in before)

    assert hc.backfill_capture('claude-code') == {'rows': 7, 'flagged': 2, 'redacted': 2}
    assert hc.backfill_capture('claude-code') == {'rows': 7, 'flagged': 0, 'redacted': 0}  # idempotent
    raw = (state / 'opportunities.jsonl').read_text()
    assert 'run the test suite' not in raw and 'agent y finished' not in raw
    rows = export(state)
    le.assert_private(rows)
    user = [r for r in rows if r['cohort'] != 'harness']
    assert json.loads(json.dumps(user)) == GOLDEN['expected_with_legacy']['rows']
    assert le.manifest(user, sources={}, generated_at=0)['counts'] == GOLDEN['expected_with_legacy']['counts']
    (harness,) = [r for r in rows if r['cohort'] == 'harness']  # 6fee859 dropped it; now its own cohort
    assert harness['turn_key'] in legacy_keys and harness['features']['cohort=harness'] == 1
