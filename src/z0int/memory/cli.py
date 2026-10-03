"""`z0int memory {doctor,bench,eval,rules}`."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import acceptance, doctor, surface


def _print(out: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(out, indent=1, default=str))
        return
    if out.get('schema') == doctor.SCHEMA:
        print(f"memory doctor: {'OK' if out['ok'] else 'FAIL'}")
        for c in out['checks']:
            print(f"  [{'ok' if c['ok'] else 'FAIL'}] {c['name']}: {c['detail']}")
        return
    for r in out['queries']:
        print(f"{r['query'][:40]:40} cold {r['cold_ms']:8.1f} ms  warm {r['warm_ms']:7.1f} ms ({r['warm_cache']})  "
              f"reads {r['raw_reads']:5}  tokens {r['tokens']:5}  hit {r['hit']}")
    print(f"hit_rate {out['hit_rate']}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog='z0int memory', description='z0 memory surface (read-only)')
    sub = p.add_subparsers(dest='cmd', required=True)
    d = sub.add_parser('doctor', help='Read-only health of AgentsView, the Claude home index and the TencentDB gateway')
    d.add_argument('--av-dir', help='AgentsView data dir (default: AGENTSVIEW_DATA_DIR or ~/.agentsview)')
    d.add_argument('--agentsview-bin', help='agentsview binary (default: on PATH)')
    d.add_argument('--claude-home', help='Claude home (default: CLAUDE_CONFIG_DIR or ~/.claude-home)')
    d.add_argument('--stale-hours', type=float, default=doctor.STALE_HOURS)
    d.add_argument('--json', action='store_true')
    b = sub.add_parser('bench', help='Per-query cold/warm latency, raw reads, tokens and hit rate (z0int#22)')
    b.add_argument('--queries', required=True, help='JSON list of {query, project?, harness?, expect?}')
    b.add_argument('--json', action='store_true')
    e = sub.add_parser('eval', help='Memory acceptance rows (z0evals#56 A-F) for one harness over the frozen cohort')
    e.add_argument('--harness', required=True, choices=[*acceptance.STUDY_HARNESSES, *acceptance.Z0_HARNESSES])
    e.add_argument('--cohort', required=True, help='cohort.json (z0evals studies/unified-memory-v0 at fb14919)')
    e.add_argument('--revision', required=True, help='harness/plugin revision (git sha) recorded in every row')
    e.add_argument('--out', required=True, help='rows are appended here (jsonl)')
    e.add_argument('--schema', help='row schema (default: the study schema for dsh/hermes/omo/omp, else '
                                    f'{acceptance.ACCEPTANCE_SCHEMA}); the study label is refused for other harnesses')
    e.add_argument('--observed', help='JSON {question_id: bool}: the brief was seen in the recorded model request')
    e.add_argument('--endpoint', default=acceptance.EVAL_ENDPOINT, help='the model endpoint the seam gate sees')
    e.add_argument('--cwd', default=os.getcwd(), help='task directory whose project scopes recall (default: here)')
    e.add_argument('--seed-av-db', help='first write the cohort evidence into this NEW synthetic AgentsView DB '
                                        '(an existing file is refused); needs --seed-ledger')
    e.add_argument('--seed-ledger', help='the z0 ledger directory the seeded claims go to (no default)')
    r = sub.add_parser('rules', help='Grok only, opt-in: write a scrubbed project-scoped memory rule file')
    r.add_argument('--harness', required=True, choices=['grok'])
    r.add_argument('--scope', choices=['project'], help='required: project (a global rules file is never written)')
    r.add_argument('--owner-approved', action='store_true', help='required: every Grok rule is sent to xAI')
    r.add_argument('--project-dir', required=True)
    r.add_argument('--query', required=True)
    args = p.parse_args(argv)
    if args.cmd == 'eval':
        cohort = acceptance.load_json(args.cohort)
        if args.seed_av_db or args.seed_ledger:
            if not (args.seed_av_db and args.seed_ledger):
                p.error('--seed-av-db and --seed-ledger go together')
            try:
                acceptance.seed_cohort(cohort, av_db=args.seed_av_db, ledger_root=args.seed_ledger)
            except FileExistsError as exc:
                p.error(str(exc))
        status = _push_status().get(args.harness) or {}
        rows = acceptance.run(args.harness, cohort, revision=args.revision, schema=args.schema,
                              observed=acceptance.load_json(args.observed) if args.observed else None,
                              push_supported=False if status.get('status') == 'UNSUPPORTED' else None,
                              endpoint=args.endpoint, cwd=args.cwd)
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        with open(args.out, 'a', encoding='utf-8') as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + '\n')
        print(json.dumps({'harness': args.harness, 'rows': len(rows), 'schema': rows[0]['schema'] if rows else None,
                          'out': args.out}))
        return 0
    if args.cmd == 'rules':
        if args.scope != 'project' or not args.owner_approved:
            print('z0int memory rules: refused: needs --scope project and --owner-approved (no global Grok rules '
                  'file: it leaks across projects and every rule is sent to xAI)', file=sys.stderr)
            return 2
        print(acceptance.write_grok_rules(args.project_dir, args.query))
        return 0
    if args.cmd == 'doctor':
        out = doctor.run(av_dir=args.av_dir, agentsview_bin=args.agentsview_bin, claude_home=args.claude_home,
                         stale_hours=args.stale_hours)
        _print(out, args.json)
        return 0 if out['ok'] else 1
    out = surface.bench(json.loads(Path(args.queries).read_text(encoding='utf-8')))
    _print(out, args.json)
    return 0


def _push_status() -> dict:
    """Push-seam support a shim recorded at load (e.g. OMO without a senpi ``context`` hook)."""
    from .. import paths
    try:
        return json.loads((paths.home() / 'state' / 'memory' / 'seam' / 'push_status.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


if __name__ == '__main__':
    sys.exit(main())
