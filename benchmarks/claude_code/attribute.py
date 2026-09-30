"""Attribute Claude Code context replay to the tool results that caused it.

For each tool_result, estimated tokens (chars/4) x the number of later API requests
in the same transcript = replay tokens that result added to cache reads. This is an
estimate for locating savings, not a billing figure; billed usage comes from run rows.
"""
import argparse, glob, json, os
from collections import defaultdict


def transcript(session_id):
    hits = glob.glob(os.path.expanduser(f'~/.claude/projects/*/{session_id}.jsonl'))
    return hits[0] if hits else None


def attribute(path):
    rows = [json.loads(l) for l in open(path, encoding='utf-8', errors='replace') if l.strip()]
    tool_of, requests, results = {}, [], []
    seen = set()
    for i, row in enumerate(rows):
        msg = row.get('message') or {}
        content = msg.get('content') if isinstance(msg.get('content'), list) else []
        if row.get('type') == 'assistant':
            if msg.get('id') not in seen:
                seen.add(msg.get('id')); requests.append(i)
            for block in content:
                if block.get('type') == 'tool_use':
                    tool_of[block['id']] = block.get('name')
        elif row.get('type') == 'user':
            for block in content:
                if block.get('type') == 'tool_result':
                    body = block.get('content')
                    text = body if isinstance(body, str) else json.dumps(body)
                    results.append((i, tool_of.get(block.get('tool_use_id'), '?'), len(text)))
    per = defaultdict(lambda: {'calls': 0, 'chars': 0, 'replay_tokens_est': 0})
    for i, tool, chars in results:
        later = sum(1 for r in requests if r > i)
        p = per[tool]; p['calls'] += 1; p['chars'] += chars; p['replay_tokens_est'] += chars // 4 * later
    return {'requests': len(requests), 'tools': dict(per)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('runs', nargs='+')
    args = ap.parse_args()
    total = defaultdict(lambda: defaultdict(int))
    for f in args.runs:
        for line in open(f):
            r = json.loads(line)
            path = r.get('session_id') and transcript(r['session_id'])
            if not path:
                continue
            a = attribute(path)
            u = r.get('usage') or {}
            print(f"{r['task']:<12} {r['arm']:<10} ok={r['verified']!s:<5} ${r['cost_usd']:.3f} req={a['requests']:<3} "
                  f"cr={u.get('cache_read_input_tokens')} cw={u.get('cache_creation_input_tokens')} "
                  + ' '.join(f"{t}:{v['calls']}x/{v['replay_tokens_est']//1000}k" for t, v in sorted(a['tools'].items(), key=lambda kv: -kv[1]['replay_tokens_est'])))
            for t, v in a['tools'].items():
                for k, n in v.items():
                    total[t][k] += n
    grand = sum(v['replay_tokens_est'] for v in total.values()) or 1
    print('\nreplay share by tool:', ', '.join(f"{t} {v['replay_tokens_est']/grand:.0%}" for t, v in sorted(total.items(), key=lambda kv: -kv[1]['replay_tokens_est'])))


if __name__ == '__main__':
    main()
