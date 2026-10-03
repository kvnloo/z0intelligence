"""The C7 FakeTencentDB (real MemoryCore response shapes, bearer check) on a fixed private loopback port.
usage: fake_tdb.py <worktree> <port>   (token from Z0_E2E_TDB_TOKEN; synthetic items only)"""
import os
import sys
import time

sys.path[:0] = [sys.argv[1] + '/tests']
from memory_fixture import FakeTencentDB  # noqa: E402

items = [{'id': 'sem-e2e-1', 'content': 'eval-fixture semantic note: RLM spill default is recorded in donor pr-77'}]
with FakeTencentDB(token=os.environ['Z0_E2E_TDB_TOKEN'], items=items, port=int(sys.argv[2])) as gw:
    print(gw.url, flush=True)
    while True:
        time.sleep(3600)
