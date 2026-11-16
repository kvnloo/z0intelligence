"""HC-2 memory-search benchmark: run the 50 frozen queries, print p50/p90/max ms and a result-id hash.

    PYTHONPATH=src python3 benchmarks/memory_search/bench_search.py --label after

Writes ``results/<label>.json`` (per-query latency, hash, event_uids) next to this script.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import bench_lib  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label', required=True, help='name for results/<label>.json')
    args = parser.parse_args(argv)

    fixtures = bench_lib.load_fixtures()
    with tempfile.TemporaryDirectory(prefix='z0int-hc2-bench-') as tmp:
        paths = bench_lib.build_env(Path(tmp), fixtures)
        rows = bench_lib.run_queries(paths['av_db'], paths['ledger_root'], fixtures['queries'])
    summary = bench_lib.summarize(rows)

    out_dir = HERE / 'results'
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f'{args.label}.json').write_text(
        json.dumps({'summary': summary, 'rows': rows}, indent=2), encoding='utf-8')

    print(f"label={args.label} n={summary['n']} p50_ms={summary['p50_ms']} p90_ms={summary['p90_ms']} "
          f"max_ms={summary['max_ms']} combined_hash={summary['combined_hash']}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
