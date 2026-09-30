from z0int import claude_code_obs as obs


def hook(stdout, session='s'):
    return {'tool_name': 'Bash', 'session_id': session, 'tool_response': {'stdout': stdout, 'stderr': '', 'interrupted': False}}


def test_small_output_untouched(monkeypatch, tmp_path):
    monkeypatch.setenv('Z0INT_HOME', str(tmp_path))
    assert obs.on_post_tool_use(hook('short')) is None
    assert obs.on_post_tool_use({'tool_name': 'Read', 'tool_response': {'stdout': 'x' * 99999}}) is None


def test_large_output_packed_and_exactly_recallable(monkeypatch, tmp_path):
    monkeypatch.setenv('Z0INT_HOME', str(tmp_path))
    text = '\n'.join(f'line {i} ' + 'x' * 80 for i in range(1, 501))
    out = obs.on_post_tool_use(hook(text))['hookSpecificOutput']['updatedToolOutput']
    assert out['stderr'] == '' and out['interrupted'] is False
    packed = out['stdout']
    assert packed.startswith('line 1 ') and packed.rstrip().endswith('line 500 ' + 'x' * 80)
    assert '440 middle lines' in packed and len(packed) < len(text) / 5
    handle = packed.split('archived verbatim as obs-')[1][:12]
    assert obs.recall(handle, 250, 2) == f'250: line 250 {"x" * 80}\n251: line 251 {"x" * 80}'
    assert obs.recall(handle, grep=r'^line 377 ') == f'377: line 377 {"x" * 80}'
    again = obs.on_post_tool_use(hook(text))['hookSpecificOutput']['updatedToolOutput']['stdout']
    assert again.startswith('[z0 ObservationPack] Output identical to archived obs-' + handle)


def test_cli_fails_open(tmp_path):
    import subprocess, sys
    r = subprocess.run([sys.executable, '-m', 'z0int.claude_code_obs', 'hook'], input='{bad', capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout == ''
