"""C1 e2e (1): replay synthetic hook payloads through the EXACT command strings of each shim hooks file.

Runs `sh -c <command>` per event (as Claude Code / Codex / Grok run command hooks), with isolated
HOME / Z0INT_HOME / CLAUDE_CONFIG_DIR / CODEX_HOME / GROK_HOME under homes/C1-loop-core/e2e-replay, waits
for the detached opportunity builds, then checks joined opportunity+outcome rows per harness, zero drops,
and prompt-hook latency against a bare-python baseline measured in the same run.
usage: e2e_replay.py <worktree> <home-root> <python>
"""
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

WT, ROOT, PY = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
SHIMS = {'claude-code': WT / 'harness-adapters/claude-code-z0intelligence/hooks/hooks.json',
         'codex': WT / 'harness-adapters/codex-z0intelligence/hooks/hooks.json',
         'grok': WT / 'harness-adapters/grok-z0intelligence/hooks/z0-capture.json'}
GROK_EVENT = {'SessionStart': 'session_start', 'UserPromptSubmit': 'user_prompt_submit', 'Stop': 'stop',
              'StopFailure': 'stop_failure', 'SubagentStart': 'subagent_start', 'SubagentStop': 'subagent_stop'}
TURNS = 3


def env_for(harness, hook_event, session):
    env = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'LANG': 'C.UTF-8', 'HOME': str(ROOT / 'home'),
           'Z0INT_HOME': str(ROOT / 'z0'), 'Z0INT_PYTHON': PY, 'PYTHONPATH': str(WT / 'src'),
           'PYTHONPYCACHEPREFIX': str(ROOT / 'pycache'), 'TMPDIR': str(ROOT / 'tmp'),
           'CLAUDE_CONFIG_DIR': str(ROOT / 'claude'), 'CODEX_HOME': str(ROOT / 'codex'), 'GROK_HOME': str(ROOT / 'grok')}
    if harness == 'claude-code':
        env['CLAUDE_PROJECT_DIR'] = str(ROOT / 'work')
    if harness == 'codex':
        env['CLAUDE_PLUGIN_ROOT'] = str(SHIMS['codex'].parent.parent)
    if harness == 'grok':
        env.update(GROK_HOOK_EVENT=GROK_EVENT[hook_event], GROK_SESSION_ID=session, GROK_WORKSPACE_ROOT=str(ROOT / 'work'))
    return env


def transcript(path, msg_id, text, tools=0):
    content = [{'type': 'tool_use', 'name': 'Bash', 'input': {}}] * tools + [{'type': 'text', 'text': text}]
    with open(path, 'a') as fh:
        fh.write(json.dumps({'type': 'assistant', 'message': {'id': msg_id, 'model': 'claude-fixture',
                                                              'usage': {'input_tokens': 3, 'output_tokens': 2},
                                                              'content': content}}) + '\n')


def events(harness, session, turn):
    """(hook event name, payload, before) for one synthetic turn as the harness would deliver it."""
    work = str(ROOT / 'work')
    t = ROOT / 'transcripts' / f'{harness}-{session}.jsonl'
    a = ROOT / 'transcripts' / f'{harness}-{session}-agent{turn}.jsonl'
    prompt = f'synthetic task {turn}: tidy the changelog'
    reply = 'Done. Should I also bump the version?' if turn == 1 else 'Done.'
    if harness == 'grok':
        base = {'session_id': session, 'sessionId': session, 'cwd': work, 'workspaceRoot': work, 'modelId': 'grok-fixture'}
        ev = lambda name, **kw: dict(base, hook_event_name=name, hookEventName=GROK_EVENT[name], **kw)  # noqa: E731
        return [('UserPromptSubmit', ev('UserPromptSubmit', prompt=prompt, timestamp=f'2026-10-03T00:00:0{turn}Z'), None),
                ('SubagentStart', ev('SubagentStart', subagentId=f'a{turn}', agentType='general'), None),
                ('SubagentStop', ev('SubagentStop', subagentId=f'a{turn}', lastAssistantMessage='agent done'), None),
                ('Stop', ev('Stop', lastAssistantMessage=reply, stopHookActive=False), None)]
    base = {'session_id': session, 'cwd': work, 'transcript_path': str(t)}
    ids = {'prompt_id': f'p{turn}'} if harness == 'claude-code' else {'turn_id': f't{turn}', 'model': 'gpt-fixture'}
    before_stop = (lambda: transcript(t, f'm{turn}', reply, tools=turn)) if harness == 'claude-code' else None
    before_sub = (lambda: transcript(a, f'a{turn}', 'agent done', tools=1)) if harness == 'claude-code' else None
    sub = {} if harness == 'claude-code' else ids
    start = [('SessionStart', dict(base, hook_event_name='SessionStart', source='startup', model='claude-fixture'), None)] \
        if harness == 'claude-code' and turn == 1 else []
    return start + [('UserPromptSubmit', dict(base, hook_event_name='UserPromptSubmit', prompt=prompt, **ids), None),
            ('SubagentStart', dict(base, hook_event_name='SubagentStart', agent_id=f'a{turn}', agent_type='general', **sub), None),
            ('SubagentStop', dict(base, hook_event_name='SubagentStop', agent_id=f'a{turn}', agent_type='general',
                                  agent_transcript_path=str(a), last_assistant_message='agent done',
                                  stop_hook_active=False, **sub), before_sub),
            ('Stop', dict(base, hook_event_name='Stop', last_assistant_message=reply, stop_hook_active=False, **ids),
             before_stop)]


def commands(path):
    doc = json.loads(path.read_text())
    return {event: [h['command'] for g in groups for h in g['hooks']] for event, groups in doc['hooks'].items()}


def run(cmd, payload, env):
    t0 = time.perf_counter()
    r = subprocess.run(['sh', '-c', cmd], input=json.dumps(payload), text=True, env=env, capture_output=True, timeout=60)
    return time.perf_counter() - t0, r


def rows(harness, name):
    p = ROOT / 'z0' / 'state' / harness / name
    return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []


def main():
    for d in ('home', 'z0', 'claude', 'codex', 'grok', 'work', 'transcripts', 'tmp', 'pycache'):
        (ROOT / d).mkdir(parents=True, exist_ok=True)
    report = {'commands': {}, 'harness': {}}
    bare, prompt_wall = [], {}
    for harness, path in SHIMS.items():
        cmds = commands(path)
        report['commands'][harness] = {e: c for e, c in cmds.items()}
        prompt_wall[harness] = []
        session = f'{harness}-e2e'
        stdout = []
        for turn in range(1, TURNS + 1):
            for event, payload, before in events(harness, session, turn):
                if before:
                    before()
                for cmd in cmds.get(event, []):
                    wall, r = run(cmd, payload, env_for(harness, event, session))
                    assert r.returncode == 0, (harness, event, r.stderr[-400:])
                    stdout.append(r.stdout)
                    if event == 'UserPromptSubmit':
                        prompt_wall[harness].append(wall)
                        b0 = time.perf_counter()
                        subprocess.run([PY, '-c', 'pass'], env=env_for(harness, event, session), check=True)
                        bare.append(time.perf_counter() - b0)
        report['harness'][harness] = {'stdout_nonempty': [s for s in stdout if s]}
    deadline = time.time() + 120  # detached opportunity builds
    want = {h: 2 * TURNS for h in SHIMS}
    while time.time() < deadline and any(len(rows(h, 'opportunities.jsonl')) < n for h, n in want.items()):
        time.sleep(0.5)

    def p95(xs):
        return sorted(xs)[max(0, int(len(xs) * 0.95) - 1)]

    ok = True
    for harness in SHIMS:
        opps, outs, fails = rows(harness, 'opportunities.jsonl'), rows(harness, 'outcomes.jsonl'), rows(harness, 'failures.jsonl')
        o_keys, out_keys = {r['turn_key']: r for r in opps}, {r['turn_key']: r for r in outs}
        joined = sorted(set(o_keys) & set(out_keys))
        cohorts = {}
        for k in joined:
            cohorts[o_keys[k]['cohort']] = cohorts.get(o_keys[k]['cohort'], 0) + 1
        drops = sum(int(r.get('count') or 0) for r in rows(harness, 'drops.jsonl'))
        fail_kinds = {}
        for f in fails:
            fail_kinds[f['kind']] = fail_kinds.get(f['kind'], 0) + 1
        h = report['harness'][harness]
        h.update(opportunities=len(opps), outcomes=len(outs), joined=len(joined), joined_by_cohort=cohorts,
                 drops=drops, failures=fail_kinds, schemas=sorted({r['schema'] for r in opps + outs}),
                 prompt_wall_ms={'median': round(statistics.median(prompt_wall[harness]) * 1000, 1),
                                 'p95': round(p95(prompt_wall[harness]) * 1000, 1)},
                 work_items={r['trace_id']: [r['work_item_id'], r['attempt_id']] for r in opps if r['cohort'] == 'interactive'},
                 model_ids=sorted({r['model_id'] for r in opps + outs}))
        good = (len(joined) == 2 * TURNS and cohorts == {'interactive': TURNS, 'agent': TURNS} and drops == 0
                and not h['stdout_nonempty'] and 'misattribution' not in fail_kinds
                and p95(prompt_wall[harness]) <= p95(bare) + 0.040)
        h['ok'] = good
        ok &= good
    report['bare_python_ms'] = {'median': round(statistics.median(bare) * 1000, 1), 'p95': round(p95(bare) * 1000, 1)}
    report['ok'] = ok
    print(json.dumps(report, indent=1, sort_keys=True))
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
