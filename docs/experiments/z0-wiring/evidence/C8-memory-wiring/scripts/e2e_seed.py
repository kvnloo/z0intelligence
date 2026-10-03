"""C8 e2e substrate (synthetic only): the frozen z0evals fb14919 cohort as AgentsView sessions + z0 ledger claims,
one session whose tool output carries fake secret-shaped strings (scrub probe), and the z0 memory config pointing
the semantic layer at the fake TencentDB gateway on a private loopback port.
usage: e2e_seed.py <worktree> <av sessions.db> <z0 home> <tdb port>   (Z0INT_HOME must equal <z0 home>)"""
import json
import sqlite3
import sys
from pathlib import Path

wt, av, z0, port = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), int(sys.argv[4])
sys.path[:0] = [str(wt / 'tests'), str(wt / 'src')]
from memory_fixture import SECRET_MESSAGE  # noqa: E402
from z0int.memory import acceptance  # noqa: E402

cohort = json.loads((wt / 'tests/fixtures/z0evals-fb14919/cohort.json').read_text())
acceptance.seed_cohort(cohort, av_db=av)
conn = sqlite3.connect(av)
conn.execute("insert into sessions (id, project, agent, started_at, cwd, message_count) values "
             "('probe-secret', 'z0', 'claude', '2026-10-01T00:00:00Z', '/w/z0', 1)")
conn.execute("insert into messages (session_id, ordinal, role, content, timestamp) values (?,?,?,?,?)",
             ('probe-secret', 0, 'assistant', SECRET_MESSAGE, '2026-10-01T00:00:00Z'))
conn.commit()
conn.close()
(z0 / 'config').mkdir(parents=True, exist_ok=True)
(z0 / 'config' / 'memory.json').write_text(json.dumps(
    {'tencentdb': {'url': f'http://127.0.0.1:{port}', 'auth_env': 'Z0_E2E_TDB_TOKEN', 'deadline_ms': 150}}))
print(json.dumps({'questions': [q['id'] for q in cohort['questions']], 'av': str(av), 'z0': str(z0)}))
