"""Round-2 cost probe for the minor O(ledger) finding (synthetic ledger; isolated Z0INT_HOME from run.sh).

Builds a 20k-event ledger of synthetic references, then times 50 new ingest_reference calls, one duplicate
ingest, and one temporal search. Usage: perf.py <worktree>
"""
import sys
import time

sys.path.insert(0, sys.argv[1] + '/src')
import os  # noqa: E402

os.environ['Z0INT_MEMORY_SOURCE_INGEST'] = 'references'
from z0int.memory import surface as ms  # noqa: E402
from z0int.memory.event_log import EventLog  # noqa: E402
from z0int.memory_contract import EventIdentity  # noqa: E402


def ident(i):
    return EventIdentity.from_source(source_system='codex', source_session='perf', source_event_id=str(i),
                                     payload_hash=f'sha256:{i:064x}')


log = EventLog()
if not log.events_path.is_file() or log.events_path.stat().st_size == 0:
    for i in range(20000):
        log.append('source.reference', {'locator': f'agentsview:perf#{i}'}, source='agentsview', identity=ident(i))
n = sum(1 for _ in EventLog(read_only=True).iter_events())
base = n + 1000000
t0 = time.perf_counter()
for i in range(50):
    ms.ingest_reference(ident(base + i), f'agentsview:perf#{base + i}')
t_ing = time.perf_counter() - t0
t0 = time.perf_counter()
ms.ingest_reference(ident(base + 7), f'agentsview:perf#{base + 7}')
t_dup = time.perf_counter() - t0
t0 = time.perf_counter()
ms.search('perf 4242', layers=('temporal',), config={})
t_tmp = time.perf_counter() - t0
print(f'ledger events before: {n}')
print(f'50 ingest_reference calls (one process): {t_ing * 1000:.0f} ms total, {t_ing * 20:.1f} ms each')
print(f'duplicate ingest_reference: {t_dup * 1000:.1f} ms')
print(f'temporal search: {t_tmp * 1000:.0f} ms')
