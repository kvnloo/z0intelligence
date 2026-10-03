"""Integration e2e, Grok leg (fixtures: the real Grok CLI is paid): replay synthetic Grok hook payloads through the
EXACT command strings of harness-adapters/grok-z0intelligence/hooks/z0-capture.json (`sh -c <command>`, payload on
stdin, GROK_HOOK_EVENT / GROK_SESSION_ID in the env as Grok sets them). Arms on/off (off = Z0INT_CAPTURE=0): the hook
stdout (the only thing a Grok command hook can hand back to Grok) is recorded per arm for the inertness check.
"on" writes into the SHARED integration Z0INT_HOME. Synthetic prompts only.
usage: e2e_grok_replay.py <worktree> <e2e-root> <python> <turns>
"""
import json
import subprocess
import sys
from pathlib import Path

WT, R, PY, TURNS = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], int(sys.argv[4])
H = R / 'grok'
HOOKS = WT / 'harness-adapters/grok-z0intelligence/hooks/z0-capture.json'
GROK_EVENT = {'UserPromptSubmit': 'user_prompt_submit', 'Stop': 'stop', 'SubagentStart': 'subagent_start',
              'SubagentStop': 'subagent_stop'}


def env_for(arm, event, session):
    return {'PATH': '/usr/local/bin:/usr/bin:/bin', 'LANG': 'C.UTF-8', 'HOME': str(H / 'home'),
            'Z0INT_HOME': str(R / ('z0home' if arm == 'on' else 'z0home-off')), 'Z0INT_PYTHON': PY,
            'Z0INT_CAPTURE': '1' if arm == 'on' else '0', 'PYTHONPYCACHEPREFIX': str(H / 'pycache'),
            'TMPDIR': str(H / 'tmp'), 'GROK_HOME': str(H / 'grok'), 'GROK_HOOK_EVENT': GROK_EVENT[event],
            'GROK_SESSION_ID': session, 'GROK_WORKSPACE_ROOT': str(H / 'work')}


def events(session, turn):
    work = str(H / 'work')
    base = {'session_id': session, 'sessionId': session, 'cwd': work, 'workspaceRoot': work, 'modelId': 'grok-fixture'}

    def ev(name, **kw):
        return dict(base, hook_event_name=name, hookEventName=GROK_EVENT[name], **kw)
    return [('UserPromptSubmit', ev('UserPromptSubmit', prompt=f'GROK-MARKER-int task {turn}: tidy the changelog',
                                    timestamp=f'2026-10-03T00:00:0{turn}Z')),
            ('SubagentStart', ev('SubagentStart', subagentId=f'a{turn}', agentType='general')),
            ('SubagentStop', ev('SubagentStop', subagentId=f'a{turn}', lastAssistantMessage='agent done')),
            ('Stop', ev('Stop', lastAssistantMessage='Done.', stopHookActive=False))]


def main():
    for d in ('home', 'grok', 'work', 'tmp', 'pycache'):
        (H / d).mkdir(parents=True, exist_ok=True)
    doc = json.loads(HOOKS.read_text())
    cmds = {e: [h['command'] for g in groups for h in g['hooks']] for e, groups in doc['hooks'].items()}
    out = {'commands': cmds, 'stdout': {}, 'rc': {}}
    for arm in ('on', 'off'):
        session = f'grok-int-{arm}'
        stdout, rcs = [], []
        for turn in range(1, TURNS + 1):
            for event, payload in events(session, turn):
                for cmd in cmds.get(event, []):
                    r = subprocess.run(['sh', '-c', cmd], input=json.dumps(payload), text=True,
                                       env=env_for(arm, event, session), capture_output=True, timeout=60)
                    stdout.append([event, r.stdout])
                    rcs.append(r.returncode)
        out['stdout'][arm], out['rc'][arm] = stdout, rcs
    (H / 'replay.json').write_text(json.dumps(out, indent=1))
    print(json.dumps({'rc_nonzero': {a: sum(1 for x in v if x) for a, v in out['rc'].items()},
                      'hook_calls': {a: len(v) for a, v in out['rc'].items()}}))


if __name__ == '__main__':
    main()
