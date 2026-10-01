"""claude-code-savings-v1b runner: State Packet at SessionStart (v1 behaviour) vs prompt-gated.

Pre-registered in PREREG.md. Reuses the frozen v1 runner unchanged (suites, tasks, fixtures,
check.py, qa scoring, claude flags, scheduling); only the arm table differs, and each trial row is
enriched with the plugin's count-only packet-gate ledger (UserPromptSubmit hook output is not in
Claude Code's stream-json, so the ledger is the only record of what the gate injected).

    .venv/bin/python benchmarks/claude_code_v1b/run.py run --suite qa --reps 4 --jobs 4 --tag main --out RAW/qa-main.jsonl
    .venv/bin/python benchmarks/claude_code_v1b/run.py route-probe --n 3 --out RAW/route-probe.jsonl
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

os.environ.setdefault('Z0_BENCH_SCRATCH', str(Path('~/.cache/z0-savings-v1b').expanduser()))
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE.parent / 'claude_code_v1'))
import run as R  # noqa: E402  (the frozen v1 runner)

# Both packet arms pin the offload hint off so SessionStart content equals v1's (b072468 had no hint);
# the only difference between them is where/what packet is injected.
PACKET_ENV = {'Z0INT_CLAUDE_CODE_OFFLOAD_HINT': '0'}
R.ARMS = {
    'lean': (R.LEAN, {}),
    'lean+packet': (R.LEAN + ['--plugin-dir', str(R.PLUGIN)], {**PACKET_ENV, 'Z0INT_CLAUDE_CODE_PACKET': '1'}),
    'lean+packet-gated': (R.LEAN + ['--plugin-dir', str(R.PLUGIN)], {**PACKET_ENV, 'Z0INT_CLAUDE_CODE_PACKET': 'gated'}),
}
STUDY = 'claude-code-savings-v1b'
_orig_run_unit = R.run_unit


def gate_rows(home: Path, session_id: str | None) -> list[dict]:
    try:
        lines = (home / 'state' / 'claude-code' / 'packet-gate.jsonl').read_text().splitlines()
    except OSError:
        return []
    return [r for r in map(json.loads, lines) if session_id and r.get('session_id') == session_id]


def run_unit(task, arm, reps, args, emit):
    unit = f"{task['id']}__{arm.replace('+', '-')}"
    home = R.SCRATCH / 'z0home' / args.tag / unit

    def enrich(row):
        rows = gate_rows(home, row.get('session_id'))
        row.update(study=STUDY, gate_prompts=len(rows),
                   gate_families=sorted({f for r in rows for f in r.get('families') or []}),
                   gate_injected=sorted({f for r in rows for f in r.get('injected') or []}),
                   gate_chars=sum(int(r.get('chars') or 0) for r in rows))
        emit(row)
    return _orig_run_unit(task, arm, reps, args, enrich)


R.run_unit = run_unit


# ------------------------------------------------------------------ exploratory: route_worker in lean
ROUTE_PROMPT = (
    'Use the route_worker tool (load it with ToolSearch if it is deferred) exactly once with '
    'task="Summarize in one sentence: The quick brown fox jumps over the lazy dog. The dog does not react.", '
    'parent_agent="claude-code", trace_id="v1b-route-probe", function="summarize". '
    'Then reply with one line: TOOL_LISTED=<yes|no> CALLED=<yes|no> VERDICT=<what the tool returned, <=12 words>.'
)


def route_probe(args):
    """Is route_worker listed and callable in a session launched as `z0int claude-code launch --profile lean`?"""
    from z0int import claude_code_launch as L
    out = Path(args.out).expanduser()
    if out.exists():
        sys.exit('output exists (create-only)')
    out.parent.mkdir(parents=True, exist_ok=True)
    work = R.SCRATCH / 'route-probe' / 'work'
    work.mkdir(parents=True, exist_ok=True)
    if not (work / '.git').exists():
        subprocess.run(['git', 'init', '-q', str(work)], check=True)
    with out.open('x') as fh:
        for i in range(args.n):
            home = R.SCRATCH / 'route-probe' / f'home-{i}'
            home.mkdir(parents=True, exist_ok=True)
            argv = L.build_argv('lean', ['-p', ROUTE_PROMPT, '--settings', R.NO_INSTALLED, '--output-format',
                                         'stream-json', '--verbose', '--model', args.model, '--effort', 'low',
                                         '--no-session-persistence', '--max-budget-usd', '1',
                                         '--allowedTools', 'ToolSearch', 'mcp__z0intelligence__route_worker'])
            env = L.launch_env('lean', base={**R.bench_env(), 'Z0INT_HOME': str(home), 'Z0INT_PYTHON': str(R.PY)})
            t0 = time.time()
            proc = subprocess.run(argv, cwd=work, env=env, capture_output=True, text=True, timeout=600,
                                  stdin=subprocess.DEVNULL)
            ps = R.parse_stream(proc.stdout)
            res = ps.pop('result')
            init_tools = []
            route_results = []
            for line in proc.stdout.splitlines():
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get('type') == 'system' and ev.get('subtype') == 'init':
                    init_tools = [t for t in ev.get('tools') or [] if 'route_worker' in t]
                if ev.get('type') == 'user':
                    for c in (ev.get('message') or {}).get('content') or []:
                        if isinstance(c, dict) and c.get('type') == 'tool_result':
                            s = c.get('content') if isinstance(c.get('content'), str) else json.dumps(c.get('content'))
                            if 'route' in s or 'PARENT_ONLY' in s or 'receipt' in s:
                                route_results.append({'is_error': bool(c.get('is_error')),
                                                      'parent_only': 'PARENT_ONLY' in s,
                                                      'has_receipt': 'receipt' in s, 'chars': len(s)})
            row = {'type': 'route_probe', 'study': STUDY, 'i': i, 'argv_has_mcp_config': '--mcp-config' in argv,
                   'mcp_servers': ps['init'].get('mcp_servers'), 'route_tools_listed': init_tools,
                   'route_worker_calls': sum(n for k, n in ps['tool_calls'].items() if 'route_worker' in k),
                   'tool_calls': ps['tool_calls'], 'route_results': route_results,
                   'final_line': (res.get('result') or '')[-300:], 'cost_usd': res.get('total_cost_usd'),
                   'tokens': R.billed(res), 'wall_s': round(time.time() - t0, 1), 'returncode': proc.returncode}
            fh.write(json.dumps(row) + '\n')
            fh.flush()
            print(json.dumps({k: row[k] for k in ('i', 'mcp_servers', 'route_tools_listed', 'route_worker_calls',
                                                  'route_results', 'final_line')}), flush=True)


def main():
    if sys.argv[1:2] == ['route-probe']:
        import argparse
        ap = argparse.ArgumentParser()
        ap.add_argument('cmd')
        ap.add_argument('--n', type=int, default=3)
        ap.add_argument('--model', default='sonnet')
        ap.add_argument('--out', required=True)
        route_probe(ap.parse_args())
        return
    R.main()


if __name__ == '__main__':
    main()
