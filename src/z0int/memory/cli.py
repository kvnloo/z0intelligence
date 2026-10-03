"""`z0int memory {doctor,bench}`."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import doctor, surface


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
    args = p.parse_args(argv)
    if args.cmd == 'doctor':
        out = doctor.run(av_dir=args.av_dir, agentsview_bin=args.agentsview_bin, claude_home=args.claude_home,
                         stale_hours=args.stale_hours)
        _print(out, args.json)
        return 0 if out['ok'] else 1
    out = surface.bench(json.loads(Path(args.queries).read_text(encoding='utf-8')))
    _print(out, args.json)
    return 0


if __name__ == '__main__':
    sys.exit(main())
