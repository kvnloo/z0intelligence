import io
import json
import subprocess
import sys

from z0int import automatic, claude_code, harness_id


def assistant(mid, model='claude-sonnet-5-5', **usage):
    return json.dumps({'type': 'assistant', 'message': {'id': mid, 'model': model, 'usage': usage}})


def write(path, *rows):
    path.write_text('\n'.join(rows) + '\n')


def test_claude_code_is_a_harness(monkeypatch):
    assert 'claude-code' in automatic.HARNESSES
    monkeypatch.setenv('Z0INT_AUTO_CLAUDE_CODE', '0')
    assert automatic.settings('claude-code')[0] is False
    for name in ('Z0INT_HARNESS_ID', 'HARNESS_ID', 'OMP_SESSION_ID', 'PI_SESSION_ID', 'HERMES_HOME', 'HERMES_PROFILE'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('CLAUDECODE', '1')
    assert harness_id.detect_harness_id() == 'claude-code'


def test_shadow_prompt_is_inert(monkeypatch, tmp_path):
    monkeypatch.setenv('Z0INT_CLAUDE_CODE_OPPORTUNITIES', '0')
    transcript = tmp_path / 't.jsonl'
    transcript.write_text('')
    seen = []
    monkeypatch.setattr(automatic, 'handle_event', lambda e: seen.append(e) or {'action': 'context', 'context': 'x', 'receipt_id': 'r'})
    monkeypatch.setattr(automatic, 'post', lambda *a: (_ for _ in ()).throw(AssertionError('consumed in shadow')))
    monkeypatch.delenv('Z0INT_CLAUDE_CODE_SHADOW', raising=False)
    assert claude_code.on_prompt({'session_id': 's', 'transcript_path': str(transcript), 'prompt': 'fix it'}) is None
    assert seen[0]['harness'] == 'claude-code' and seen[0]['text'] == 'fix it'


def test_live_prompt_delivers_additional_context(monkeypatch, tmp_path):
    monkeypatch.setenv('Z0INT_CLAUDE_CODE_OPPORTUNITIES', '0')
    consumed = []
    monkeypatch.setattr(automatic, 'handle_event', lambda e: {'action': 'context', 'context': 'data', 'receipt_id': 'r1'})
    monkeypatch.setattr(automatic, 'post', lambda path, value: consumed.append(value))
    monkeypatch.setenv('Z0INT_CLAUDE_CODE_SHADOW', '0')
    out = claude_code.on_prompt({'session_id': 's', 'transcript_path': str(tmp_path / 'none'), 'prompt': 'q'})
    assert out['hookSpecificOutput'] == {'hookEventName': 'UserPromptSubmit', 'additionalContext': 'data'}
    assert consumed == [{'harness': 'claude-code', 'instance_id': 's', 'receipt_id': 'r1'}]


def test_native_result_is_never_injected(monkeypatch):
    monkeypatch.setenv('Z0INT_CLAUDE_CODE_OPPORTUNITIES', '0')
    monkeypatch.setattr(automatic, 'handle_event', lambda e: {'action': 'native'})
    monkeypatch.setenv('Z0INT_CLAUDE_CODE_SHADOW', '0')
    assert claude_code.on_prompt({'session_id': 's', 'prompt': 'q'}) is None


def test_stop_emits_deduplicated_incremental_usage(tmp_path):
    root = tmp_path / 'home'
    transcript = tmp_path / 'sess.jsonl'
    write(transcript,
          assistant('m1', input_tokens=3, cache_read_input_tokens=100, output_tokens=5),
          assistant('m1', input_tokens=3, cache_read_input_tokens=100, output_tokens=9),
          assistant('m2', input_tokens=1, cache_creation_input_tokens=40, output_tokens=2),
          assistant('m3', model='<synthetic>', output_tokens=7),
          json.dumps({'type': 'user', 'message': {'content': 'hi'}}))
    hook = {'session_id': 'sess', 'transcript_path': str(transcript)}
    first = claude_code.on_stop(hook, root=root)
    assert first == [{'role': 'root', 'messages': 2, 'usage': {
        'input_tokens': 4, 'cache_creation_input_tokens': 40, 'cache_read_input_tokens': 100, 'output_tokens': 11}}]
    assert claude_code.on_stop(hook, root=root) == []
    with transcript.open('a') as fh:
        fh.write(assistant('m4', input_tokens=2, output_tokens=1) + '\n')
    sub = tmp_path / 'sess' / 'subagents'
    sub.mkdir(parents=True)
    write(sub / 'agent-a.jsonl', assistant('s1', model='claude-haiku-4-5', input_tokens=8, output_tokens=3))
    second = {r['role']: r for r in claude_code.on_stop(hook, root=root)}
    assert second['root']['usage']['input_tokens'] == 2 and second['root']['messages'] == 1
    assert second['subagent']['usage']['input_tokens'] == 8
    rows = [json.loads(l) for l in (root / 'tokenomics' / 'events.jsonl').read_text().splitlines()]
    assert [r['schema'] for r in rows] == ['claude-code.provider_usage.v0'] * 3
    assert rows[0]['measurement_state'] == 'complete' and rows[0]['provider'] == 'anthropic'


def test_cli_fails_open_on_garbage():
    out = subprocess.run([sys.executable, '-m', 'z0int.claude_code', 'prompt'], input='not json',
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0 and out.stdout == ''


def test_turn_id_prefers_prompt_id():
    assert claude_code.turn_id({'prompt_id': 'p-1', 'session_id': 's', 'prompt': 'x'}) == 'p-1'
    assert len(claude_code.turn_id({'session_id': 's', 'prompt': 'x'})) == 64


def test_session_start_packet_is_opt_in(monkeypatch):
    monkeypatch.delenv('Z0INT_CLAUDE_CODE_PACKET', raising=False)
    assert claude_code.on_session_start('{"cwd": "/"}') is None
    import z0int.state_packet as sp
    monkeypatch.setenv('Z0INT_CLAUDE_CODE_PACKET', '1')
    monkeypatch.setattr(sp, 'session_start_hook', lambda text, max_tokens: {'hookSpecificOutput': {'hookEventName': 'SessionStart', 'additionalContext': f'packet {max_tokens}'}})
    assert claude_code.on_session_start('{}')['hookSpecificOutput']['additionalContext'] == 'packet 1500'


def test_host_config_drives_packet_and_shadow(monkeypatch, tmp_path):
    import json as _json
    monkeypatch.setenv('Z0INT_HOME', str(tmp_path))
    for name in ('Z0INT_CLAUDE_CODE_PACKET', 'Z0INT_CLAUDE_CODE_SHADOW'):
        monkeypatch.delenv(name, raising=False)
    assert claude_code.shadow() is True and claude_code.config() == {}
    (tmp_path / 'config').mkdir()
    (tmp_path / 'config' / 'claude-code.json').write_text(_json.dumps({'shadow': False, 'packet': True}))
    assert claude_code.shadow() is False
    import z0int.state_packet as sp
    monkeypatch.setattr(sp, 'session_start_hook', lambda text, max_tokens: {'hookSpecificOutput': {'hookEventName': 'SessionStart', 'additionalContext': 'p'}})
    assert claude_code.on_session_start('{}') is not None
    monkeypatch.setenv('Z0INT_CLAUDE_CODE_PACKET', '0')  # env overrides config
    assert claude_code.on_session_start('{}') is None


def test_prompt_hook_emits_opportunity_off_the_hot_path(monkeypatch):
    spawned = []

    class FakePopen:
        def __init__(self, argv, **kw):
            spawned.append((argv, kw))
            self.stdin = __import__('io').BytesIO()

    import subprocess
    monkeypatch.setattr(subprocess, 'Popen', FakePopen)
    monkeypatch.setattr(automatic, 'handle_event', lambda e: {'action': 'native'})
    monkeypatch.delenv('Z0INT_CLAUDE_CODE_OPPORTUNITIES', raising=False)
    assert claude_code.on_prompt({'session_id': 's', 'prompt': 'what branch?', 'cwd': '/tmp'}) is None
    argv, kw = spawned[0]
    assert argv[-1] == 'opportunity' and kw['start_new_session'] is True
    monkeypatch.setenv('Z0INT_CLAUDE_CODE_OPPORTUNITIES', '0')
    spawned.clear()
    claude_code.on_prompt({'session_id': 's', 'prompt': 'x'})
    assert spawned == []


def test_opportunity_record_written_for_a_repo(monkeypatch, tmp_path):
    import subprocess
    from z0int import state_packet as sp
    repo = tmp_path / 'r'; repo.mkdir()
    subprocess.run(['git', 'init', '-q', '-b', 'main', str(repo)], check=True)
    (repo / 'f').write_text('x')
    subprocess.run(['git', '-C', str(repo), 'add', '-A'], check=True)
    subprocess.run(['git', '-C', str(repo), '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'i'], check=True)
    (tmp_path / 'projects').mkdir()
    monkeypatch.setattr(sp, 'default_projects_root', lambda: tmp_path / 'projects')
    rec = claude_code.on_opportunity({'session_id': 's', 'prompt': 'what branch am I on?', 'cwd': str(repo), 'prompt_id': 'p1'},
                                     root=tmp_path / 'home')
    assert rec['gate'] == 'ACT' and rec['opportunity']['trace']['harness'] == 'claude-code'
    rows = (tmp_path / 'home' / 'state' / 'claude-code' / 'opportunities.jsonl').read_text().splitlines()
    assert len(rows) == 1 and json.loads(rows[0])['opportunity']['scope']['families'] == ['git.branch']
    assert claude_code.on_opportunity({'prompt': 'q', 'cwd': str(tmp_path)}, root=tmp_path / 'home') is None  # not a repo


def test_stop_writes_observed_turn_behaviour(tmp_path):
    root = tmp_path / 'home'
    transcript = tmp_path / 's.jsonl'
    rows = [
        {'type': 'assistant', 'message': {'id': 'm1', 'model': 'claude-sonnet-5-5', 'usage': {'input_tokens': 1, 'output_tokens': 1},
                                         'content': [{'type': 'tool_use', 'name': 'Bash', 'input': {}}]}},
        {'type': 'assistant', 'message': {'id': 'm2', 'model': 'claude-sonnet-5-5', 'usage': {'input_tokens': 1, 'output_tokens': 1},
                                         'content': [{'type': 'text', 'text': 'Which branch did you mean?'}]}},
    ]
    transcript.write_text('\n'.join(json.dumps(r) for r in rows) + '\n')
    claude_code.on_stop({'session_id': 's', 'transcript_path': str(transcript), 'prompt_id': 'p9'}, root=root)
    out = [json.loads(l) for l in (root / 'state' / 'claude-code' / 'outcomes.jsonl').read_text().splitlines()]
    assert out == [{'schema': 'z0int.claude_code.turn_outcome.v0', 'session_id': 's', 'trace_id': 'p9',
                    'label_kind': 'observed_behaviour_not_optimal', 'asked_user': True, 'asked_via_tool': False,
                    'tool_calls': 1, 'assistant_messages': 2}]
