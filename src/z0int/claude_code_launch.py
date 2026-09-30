"""Claude Code launch policy: measured session profiles + prefix-cache residency.

Measured (z0evals claude-code-savings-v0): the per-session static prefix (user settings,
skills, MCP) is rewritten on every cold session and re-read on every request; `lean` cut
billed cost ~64% on cold and ~27% on warm short tasks at equal quality. Claude Code's
prefix cache is scoped to directory + startup git snapshot, so a cold session costs ~3x a
warm one. `warm` ranks candidate directories by likely cache residency so fleets can
place work where the prefix is still cached.

  z0int claude-code profile lean
  z0int claude-code launch --profile lean -- -p "fix the test" --output-format json
  z0int claude-code warm --json DIR [DIR ...]
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from . import paths

PROFILES = {
    'stock': [],
    # Drops user-level settings/hooks, non-explicit MCP servers and skill listings.
    'lean': ['--setting-sources', 'project', '--strict-mcp-config', '--disable-slash-commands'],
}
PLUGIN = Path(__file__).resolve().parents[2] / 'harness-adapters' / 'claude-code-z0intelligence'
# Main-conversation cache TTL: 1h on a subscription within plan usage, else 5m.
DEFAULT_TTL = int(os.environ.get('Z0INT_CLAUDE_CACHE_TTL', '3600'))


def state_path(root=None):
    return paths.ensure_layout(root)['state'] / 'claude-code' / 'warm.json'


def snapshot(directory):
    """What Claude Code's startup prefix embeds: cwd plus the git branch/commits/status snapshot."""
    directory = Path(directory).resolve()
    def git(*args):
        r = subprocess.run(['git', '-C', str(directory), *args], capture_output=True, text=True)
        return r.stdout if r.returncode == 0 else ''
    parts = [str(directory), git('rev-parse', '--abbrev-ref', 'HEAD'), git('log', '-5', '--format=%H'), git('status', '--porcelain')]
    return hashlib.sha256('\0'.join(parts).encode()).hexdigest()[:16]


def _load(root=None):
    try:
        return json.loads(state_path(root).read_text())
    except (OSError, ValueError):
        return {}


def record(directory, profile, root=None):
    data = _load(root)
    data[str(Path(directory).resolve())] = {'snapshot': snapshot(directory), 'profile': profile, 'ts': time.time()}
    path = state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, sort_keys=True))


def rank(candidates, profile='lean', ttl=DEFAULT_TTL, root=None, now=None):
    now = now or time.time()
    data = _load(root)
    rows = []
    for c in candidates:
        key = str(Path(c).resolve())
        seen = data.get(key)
        age = now - seen['ts'] if seen else None
        reasons = []
        if not seen:
            reasons.append('never launched')
        else:
            if age > ttl:
                reasons.append(f'cache likely expired ({int(age)}s > ttl {ttl}s)')
            if seen['profile'] != profile:
                reasons.append(f"different profile ({seen['profile']})")
            if seen['snapshot'] != snapshot(c):
                reasons.append('git snapshot changed since last launch')
        rows.append({'dir': key, 'warm': not reasons, 'age_s': None if age is None else round(age), 'reasons': reasons})
    # Warm first, then most recently used (a recent cold entry may still share the core prefix).
    rows.sort(key=lambda r: (not r['warm'], r['age_s'] if r['age_s'] is not None else float('inf')))
    return rows


def decisions_report(root=None):
    """Join shadow DecisionOpportunity records with observed turn outcomes (z0int#55 live cohort)."""
    base = paths.ensure_layout(root)['state'] / 'claude-code'
    def rows(name):
        try:
            return [json.loads(l) for l in (base / name).read_text().splitlines() if l.strip()]
        except OSError:
            return []
    outcomes = {r.get('trace_id'): r for r in rows('outcomes.jsonl') if r.get('trace_id')}
    from .claude_code import is_harness_message
    table, unlinked, examples, harness, rescored = {}, 0, [], 0, {}
    for rec in rows('opportunities.jsonl'):
        opp = rec['opportunity']
        if is_harness_message(opp['intent']['request']):  # recorded before emission skipped them
            harness += 1
            continue
        out = outcomes.get(opp['trace'].get('trace_id'))
        if out is None:
            unlinked += 1
            continue
        observed = 'asked' if out.get('asked_user') else 'answered'
        key = (rec['gate'], observed)
        table[key] = table.get(key, 0) + 1
        if (rec['gate'] == 'ACT') != (observed == 'answered') and len(examples) < 10:
            examples.append({'gate': rec['gate'], 'observed': observed, 'scope': opp['scope'].get('mode'),
                             'families': opp['scope'].get('families'), 'request': opp['intent']['request'][:80]})
        for name, grants in RESCORE_AUTHORITY.items():
            gate = rescore_gate(opp, grants)
            if gate is not None:
                rescored.setdefault(name, {})
                rescored[name][(gate, observed)] = rescored[name].get((gate, observed), 0) + 1
    fmt = lambda t: {f'{g}->{o}': n for (g, o), n in sorted(t.items())}
    return {'linked': sum(table.values()), 'unlinked': unlinked, 'harness_messages_excluded': harness,
            'table': fmt(table), 'disagreements': examples,
            # z0int#55 effect inference: the same recorded state, re-gated with inferred effects (counts only)
            'rescore_inferred_effects': {name: fmt(t) for name, t in rescored.items()}}


# Recorded authority was the harness default; 'session' is Claude Code's standing authority outside plan mode.
RESCORE_AUTHORITY = {'default_read': ('read',), 'session_read_write': ('read', 'write')}


def rescore_gate(opp, grants):
    """Recompute the gate from the opportunity's *recorded* state with inferred effects (no packet rebuild)."""
    from .decision_opportunity import _action_space, deterministic_gate
    from .effect_inference import infer_effects
    state = opp.get('state')
    if not isinstance(state, dict) or 'unknowns' not in state:
        return None
    effects = infer_effects(opp['intent']['request'], {'current_claims': state.get('claims') or []})['effects']
    observe = [a for a in opp.get('action_space') or [] if a.get('kind') == 'OBSERVE']
    space = _action_space(state['unknowns'], state.get('contradictions') or [], effects, {'grants': list(grants)}, observe)
    return deterministic_gate({'action_space': space})


def build_argv(profile, claude_args, plugin=True):
    argv = ['claude', *PROFILES[profile]]
    if plugin and PLUGIN.is_dir():
        argv += ['--plugin-dir', str(PLUGIN)]
    return argv + list(claude_args)


def _main(argv=None):
    ap = argparse.ArgumentParser(prog='z0int claude-code', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    pr = sub.add_parser('profile', help='print the flags for a profile')
    pr.add_argument('name', choices=sorted(PROFILES))
    la = sub.add_parser('launch', help='run claude with a profile and record cache residency')
    la.add_argument('--profile', choices=sorted(PROFILES), default='lean')
    la.add_argument('--no-plugin', action='store_true', help='do not attach the z0 harness plugin')
    la.add_argument('--dry-run', action='store_true')
    la.add_argument('claude_args', nargs=argparse.REMAINDER)
    sub.add_parser('decisions', help='gate vs observed behaviour on live shadow DecisionOpportunity records')
    wa = sub.add_parser('warm', help='rank candidate directories by likely prefix-cache residency')
    wa.add_argument('dirs', nargs='+')
    wa.add_argument('--profile', choices=sorted(PROFILES), default='lean')
    wa.add_argument('--ttl', type=int, default=DEFAULT_TTL)
    wa.add_argument('--json', action='store_true')
    args = ap.parse_args(argv)
    if args.cmd == 'profile':
        print(' '.join(PROFILES[args.name]))
        return 0
    if args.cmd == 'decisions':
        print(json.dumps(decisions_report(), indent=1, ensure_ascii=False))
        return 0
    if args.cmd == 'warm':
        rows = rank(args.dirs, args.profile, args.ttl)
        if args.json:
            print(json.dumps(rows, indent=1))
        else:
            for r in rows:
                print(('WARM ' if r['warm'] else 'cold ') + r['dir'] + ('' if r['warm'] else '  (' + '; '.join(r['reasons']) + ')'))
        return 0
    rest = args.claude_args[1:] if args.claude_args[:1] == ['--'] else args.claude_args
    cmd = build_argv(args.profile, rest, plugin=not args.no_plugin)
    if args.dry_run:
        print(json.dumps(cmd))
        return 0
    record(os.getcwd(), args.profile)
    return subprocess.call(cmd)


if __name__ == '__main__':
    sys.exit(_main())
