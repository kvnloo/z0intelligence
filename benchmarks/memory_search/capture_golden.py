"""Capture the pre-optimization golden fixtures HC-2's equivalence tests compare against.

Run ONCE against the unmodified src/z0int/memory/{scrub,event_log,surface}.py, before any HC-2
change, to freeze:
  - fixtures/scrub_golden.json: scrub_text(text) -> (text, count) for every frozen corpus entry.
  - fixtures/golden.json: search(query) -> ordered event_uid list for every frozen query.

These files are committed fixtures; this script is provenance, not something later runs re-execute.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
for p in (str(HERE), str(REPO_ROOT / 'src'), str(REPO_ROOT / 'tests')):
    if p not in sys.path:
        sys.path.insert(0, p)

import bench_lib  # noqa: E402
from z0int.memory import scrub  # noqa: E402


def main() -> None:
    fixtures = bench_lib.load_fixtures()

    scrub_golden = [{'text': text, **dict(zip(('scrubbed', 'count'), scrub.scrub_text(text)))}
                    for text in fixtures['corpus']]
    (HERE / 'fixtures' / 'scrub_golden.json').write_text(json.dumps(scrub_golden, indent=2), encoding='utf-8')

    with tempfile.TemporaryDirectory(prefix='z0int-hc2-golden-') as tmp:
        paths = bench_lib.build_env(Path(tmp), fixtures)
        rows = bench_lib.run_queries(paths['av_db'], paths['ledger_root'], fixtures['queries'])
    golden = [{'query': r['query'], 'event_uids': r['event_uids'], 'hash': r['hash']} for r in rows]
    (HERE / 'fixtures' / 'golden.json').write_text(json.dumps(golden, indent=2), encoding='utf-8')

    print(f'wrote {len(scrub_golden)} scrub golden rows and {len(golden)} search golden rows')


if __name__ == '__main__':
    main()
