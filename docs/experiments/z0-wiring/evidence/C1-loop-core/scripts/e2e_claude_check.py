"""Check the rows written by the real `claude -p` e2e run (homes/C1-loop-core/e2e-claude)."""
import json
import sys
from pathlib import Path

H = Path(sys.argv[1])


def rows(name):
    p = H / 'z0' / 'state' / 'claude-code' / name
    return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []


out = json.loads((H / 'claude-out.json').read_text())
opps, outs, fails = rows('opportunities.jsonl'), rows('outcomes.jsonl'), rows('failures.jsonl')
by_key = {r['turn_key']: r for r in outs}
joined = [(o['cohort'], o['trace_id'][:12], by_key[o['turn_key']]['cohort']) for o in opps if o['turn_key'] in by_key]
payload = {e: json.loads(l) for e in ('SubagentStart', 'SubagentStop') for l in
           (H / 'payloads' / f'{e}.jsonl').read_text().splitlines() if l.strip()}
requests = [json.loads(line) for line in (H / 'stub-requests.jsonl').read_text().splitlines()]
report = {
    'claude_result': {k: out.get(k) for k in ('type', 'subtype', 'is_error', 'num_turns')},
    'stub_requests': [r.get('reply') for r in requests],
    'opportunities': [(r['cohort'], r['schema'], r['gate'], r['model_id']) for r in opps],
    'outcomes': [(r['cohort'], r['asked_user'], r['tool_calls'], r['assistant_messages'], r['model_id']) for r in outs],
    'joined_on_turn_key': joined,
    'failures': [(f['kind'], f['detail']) for f in fails],
    'drops': len(rows('drops.jsonl')),
    'real_payload_keys': {e: sorted(p) for e, p in payload.items()},
}
prompts = [json.loads(l) for l in (H / 'payloads' / 'UserPromptSubmit.jsonl').read_text().splitlines() if l.strip()]
opp_keys = {o['turn_key'] for o in opps}
report['prompts'] = [('harness' if p['prompt'].lstrip().startswith('<') else 'user', p['prompt_id'][:12]) for p in prompts]
report['outcome_cohorts'] = sorted(r['cohort'] for r in outs)
report['late_rows'] = [f['detail'] for f in fails if 'late_messages' in f['detail']]
# Fix round (verifier REVISE): every prompt closes its own turn, every opportunity has its outcome, an outcome
# without an opportunity is only ever a harness-injected prompt (cohort harness), and no outcome is cohort unknown.
closed = {r['trace_id'] for r in outs}
ok = (out.get('subtype') == 'success'
      and any(c == ('interactive') and oc == 'interactive' for c, _, oc in joined)
      and any(c == 'agent' and oc == 'agent' for c, _, oc in joined)
      and all(o['turn_key'] in by_key for o in opps)
      and all(p['prompt_id'] in closed for p in prompts)
      and all(r['cohort'] == 'harness' for r in outs if r['turn_key'] not in opp_keys)
      and 'unknown' not in report['outcome_cohorts']
      and not any(f['kind'] in ('misattribution', 'unsupported_harness', 'unsupported_schema') for f in fails)
      and report['drops'] == 0)
report['ok'] = ok
print(json.dumps(report, indent=1))
sys.exit(0 if ok else 1)
