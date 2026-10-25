import json
import subprocess

import pytest

from z0int import claude_code, claude_code_engagement as eng


def git_repo(path, *, dirty=False):
    path.mkdir(parents=True)
    run = lambda *a: subprocess.run(['git', '-C', str(path), *a], check=True, capture_output=True)
    run('init', '-q', '-b', 'main')
    (path / 'a.txt').write_text('a')
    run('add', 'a.txt')
    run('-c', 'user.email=t@t', '-c', 'user.name=t', 'commit', '-q', '-m', 'init')
    if dirty:
        (path / 'b.txt').write_text('b')
    return path


@pytest.fixture
def no_env(monkeypatch):
    for k in ('Z0INT_CLAUDE_CODE_PACKET', 'Z0INT_CLAUDE_CODE_OFFLOAD_HINT', 'Z0INT_CLAUDE_CODE_WORKSPACE_PACKET',
              'Z0INT_CLAUDE_CODE_SUBAGENT_PACKET'):
        monkeypatch.delenv(k, raising=False)


def test_offload_hint_follows_posture():
    assert eng.offload_hint(None) == ''
    burn = eng.offload_hint({'posture': 'BURN'})
    assert 'do the work here' in burn and eng.ROUTE_TOOL in burn
    for p in ('OFFLOAD', 'RESERVE'):
        assert 'before doing them inline' in eng.offload_hint({'posture': p})
    assert 'PARENT_ONLY' in eng.offload_hint({'posture': 'BALANCED'})


def test_workspace_packet_summarizes_repos_under_non_repo_cwd(tmp_path, no_env):
    ws = tmp_path / 'ws'
    git_repo(ws / 'alpha', dirty=True)
    git_repo(ws / 'group' / 'beta')
    (ws / 'notes').mkdir()
    out = eng.session_context(json.dumps({'cwd': str(ws)}), factory={'posture': 'BURN'})
    ctx = out['hookSpecificOutput']['additionalContext']
    assert out['hookSpecificOutput']['hookEventName'] == 'SessionStart'
    assert '<z0-workspace-packet' in ctx and 'repos=2' in ctx
    assert '- alpha [main dirty=1' in ctx and '- group/beta [main' in ctx
    assert '<z0-offload posture=BURN>' in ctx


def test_workspace_packet_is_bounded_and_can_be_disabled(tmp_path, no_env, monkeypatch):
    ws = tmp_path / 'ws'
    for i in range(4):
        git_repo(ws / f'r{i}')
    rows, total = eng.workspace_repos(ws, max_repos=2)
    assert total == 4 and len(rows) == 2
    out = eng.session_context(json.dumps({'cwd': str(ws)}), cfg={'workspace_packet': False}, factory=None)
    assert out is None
    monkeypatch.setenv('Z0INT_CLAUDE_CODE_WORKSPACE_PACKET', '0')
    assert eng.session_context(json.dumps({'cwd': str(ws)}), factory=None) is None


def test_repo_cwd_keeps_the_repo_packet(tmp_path, no_env, monkeypatch):
    repo = git_repo(tmp_path / 'r')
    import z0int.state_packet as sp
    monkeypatch.setattr(sp, 'session_start_hook', lambda text, max_tokens: {
        'hookSpecificOutput': {'hookEventName': 'SessionStart', 'additionalContext': f"pkt {json.loads(text)['cwd']}"}})
    ctx = eng.session_context(json.dumps({'cwd': str(repo)}), factory=None)['hookSpecificOutput']['additionalContext']
    assert ctx == f'pkt {repo}'


def test_subagent_packet_is_opt_in(tmp_path, no_env):
    ws = tmp_path / 'ws'
    git_repo(ws / 'alpha')
    hook = json.dumps({'cwd': str(ws), 'hook_event_name': 'SubagentStart'})
    assert eng.session_context(hook, factory={'posture': 'BURN'}) is None
    out = eng.session_context(hook, cfg={'subagent_packet': True}, factory={'posture': 'BURN'})
    assert out['hookSpecificOutput']['hookEventName'] == 'SubagentStart'


def test_packet_off_hint_only(tmp_path, no_env):
    out = eng.session_context(json.dumps({'cwd': str(tmp_path)}), packet=False, factory={'posture': 'OFFLOAD'})
    assert out['hookSpecificOutput']['additionalContext'].startswith('<z0-offload posture=OFFLOAD>')



def test_session_context_never_raises(no_env):
    assert eng.session_context('not json', packet=False, factory=None) is None


def test_workspace_scan_is_capped(tmp_path):
    for i in range(30):
        (tmp_path / f'd{i:02d}' / 'sub').mkdir(parents=True)
    git_repo(tmp_path / 'zz')
    assert eng.candidate_repos(tmp_path, max_dirs=10) == []
    assert eng.candidate_repos(tmp_path) == [tmp_path / 'zz']



def test_shell_init_and_lean_settings_are_printed_not_applied():
    assert eng.shell_init('fish').startswith('function claude-lean --wraps claude')
    assert 'z0int claude-code launch --profile lean -- "$@"' in eng.shell_init('bash', 'claude')
    ls = eng.lean_settings()
    assert '--strict-mcp-config' in ls['not_expressible'] and 'skillListingMaxDescChars' in ls['settings']


def test_cohorts_and_report_count_only(tmp_path):
    assert eng.cohort('cli', '/home/u/workspace') == 'interactive'
    assert eng.cohort('sdk-cli', '/tmp/cc-fix-lean-abcd1234') == 'harness'
    assert eng.cohort('sdk-cli', '/home/u/workspace/repo') == 'agent'
    proj = tmp_path / 'p'
    proj.mkdir()
    rows = [
        {'type': 'user', 'cwd': '/home/u/workspace', 'entrypoint': 'cli', 'message': {'content': 'secret prompt'}},
        {'type': 'attachment', 'attachment': {'type': 'hook_additional_context', 'hookEvent': 'SessionStart',
                                              'content': ['<z0-workspace-packet root="~"> <z0-offload posture=BURN>']}},
        {'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'name': eng.ROUTE_TOOL, 'input': {}}]}},
    ]
    (proj / 's1.jsonl').write_text('\n'.join(json.dumps(r) for r in rows) + '\n')
    sub = proj / 's1' / 'subagents'
    sub.mkdir(parents=True)
    (sub / 'agent-1.jsonl').write_text(json.dumps({'type': 'assistant', 'cwd': '/x', 'message': {'content': []}}) + '\n')
    rep = eng.report(days=1, base=tmp_path)
    assert rep['cohorts']['interactive/root'] == {'sessions': 1, 'assistant_msgs': 1, 'packet': 0, 'workspace_packet': 1,
                                                  'offload_hint': 1, 'z0_session_start': 0, 'skill_listing': 0,
                                                  'route_worker_calls': 1, 'obspack_results': 0}
    assert rep['cohorts']['interactive/subagent']['sessions'] == 1
    assert 'secret' not in json.dumps(rep)


def test_subagent_start_hook_is_capture_only():
    # C1 (z0int#56 M1) registers SubagentStart for capture: the adapter records an agent-cohort opportunity and
    # prints nothing; the subagent packet (subagent_packet, off by default) is never served from this hook.
    from pathlib import Path
    hooks = json.loads((Path(__file__).resolve().parents[1] / 'harness-adapters' / 'claude-code-z0intelligence'
                        / 'hooks' / 'hooks.json').read_text())['hooks']
    (command,) = [h['command'] for group in hooks['SubagentStart'] for h in group['hooks']]
    assert '-m z0int.hook_adapter --harness claude-code subagent-start' in command
    assert 'engagement' not in command and 'session-start' not in command
    assert 'session-start' in hooks['SessionStart'][0]['hooks'][0]['command']


def test_scan_recognises_the_z0_session_start_hook_by_either_command(tmp_path):
    def hook_row(event, command):
        return {'type': 'attachment', 'attachment': {'type': 'hook_success', 'hookEvent': event, 'command': command}}
    old = '"$Z0INT_PYTHON" -m z0int.claude_code session-start || true'
    new = '"$Z0INT_PYTHON" -m z0int.hook_adapter --harness claude-code session-start || true'
    capture = '"$Z0INT_PYTHON" -m z0int.hook_adapter --harness claude-code subagent-start || true'
    for rows, expected in (([hook_row('SessionStart', old)], True), ([hook_row('SessionStart', new)], True),
                           ([hook_row('SubagentStart', capture)], False)):
        path = tmp_path / f'{len(list(tmp_path.iterdir()))}.jsonl'
        path.write_text(''.join(json.dumps(r) + '\n' for r in rows))
        assert eng.scan(path)['z0_session_start'] is expected, rows
