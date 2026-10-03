"""Synthetic AgentsView fixture for the memory-surface tests (no real transcript, prompt or credential).

The schema is the subset of the AgentsView ``sessions.db`` the memory surface reads (real column names; the
messages_fts definition and its insert trigger are copied from the v0.39 schema). Secret-shaped strings are
assembled at runtime so no credential-looking literal sits in the repository.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

SECRETS = {
    'api_key': 'sk-' + 'proj-' + 'FAKE' * 6,
    'bearer': 'fakebearer' + 'x' * 20,
    'bws': '0.' + '00000000-0000-4000-8000-000000000001' + '.' + 'FAKEbwsClientSecret0123' + ':' + 'RkFLRWJ3c0tleUZBS0U9PQ==',
    'aws': 'AKIA' + 'FAKEFAKEFAKEFAKE',
    'pem': 'MIIEfake' + 'fakefakefake',
}
SECRET_MESSAGE = (
    f"tool output: OPENAI_API_KEY={SECRETS['api_key']}\n"
    f"Authorization: Bearer {SECRETS['bearer']}\n"
    f"BWS_ACCESS_TOKEN {SECRETS['bws']}\n"
    f"aws id {SECRETS['aws']}\n"
    f"-----BEGIN RSA PRIVATE KEY-----\n{SECRETS['pem']}\n-----END RSA PRIVATE KEY-----\n"
    "quokka deploy notes end"
)

BASE_SCHEMA = """
create table sessions (id text primary key, project text not null, machine text not null default 'local',
  agent text not null default 'claude', started_at text, ended_at text, cwd text not null default '',
  file_path text, message_count integer not null default 0);
create table messages (id integer primary key, session_id text not null references sessions(id) on delete cascade,
  ordinal integer not null, role text not null, content text not null, timestamp text, unique(session_id, ordinal));
"""
FTS_SCHEMA = """
create virtual table messages_fts using fts5(content, content='messages', content_rowid='id',
  tokenize='porter unicode61');
create trigger messages_ai after insert on messages begin
  insert into messages_fts(rowid, content) values (new.id, new.content);
end;
"""

# (session id, agent, project, cwd, [(role, content), ...])
SESSIONS = [
    ('h1', 'hermes', 'z0', '/w/z0', [('user', 'how do we deploy the quokka gateway'),
                                     ('assistant', 'the quokka gateway deploys through a systemd user unit')]),
    ('c1', 'claude', 'z0', '/w/z0', [('assistant', 'quokka cache lives under state memory'),
                                     ('assistant', SECRET_MESSAGE)]),
    ('x1', 'codex', 'z0', '/w/z0', [('assistant', 'quokka bench numbers were recorded for codex')]),
    ('o1', 'omp', 'z0', '/w/z0', [('assistant', 'quokka routing goes through the omp bridge')]),
    ('d1', 'deepseek-harness', 'z0', '/w/z0', [('assistant', 'dsh quokka worker finished')]),
    ('sib', 'claude', 'other', '/w/other', [('assistant', 'quokka sibling project plan')]),
]


def iso(t: float) -> str:
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(t))


def build_av_db(path: str | Path, *, user_version: int = 74, now: float | None = None, age_hours: float = 1.0,
                sessions=SESSIONS, fts: bool = True) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(BASE_SCHEMA + (FTS_SCHEMA if fts else ''))
    t0 = (time.time() if now is None else now) - age_hours * 3600
    mid = 0
    for n, (sid, agent, project, cwd, msgs) in enumerate(sessions):
        conn.execute('insert into sessions (id, project, agent, started_at, cwd, message_count) values (?,?,?,?,?,?)',
                     (sid, project, agent, iso(t0 + n), cwd, len(msgs)))
        for ordinal, (role, content) in enumerate(msgs):
            mid += 1
            conn.execute('insert into messages (id, session_id, ordinal, role, content, timestamp) values (?,?,?,?,?,?)',
                         (mid, sid, ordinal, role, content, iso(t0 + n + ordinal / 10)))
    conn.execute(f'pragma user_version = {int(user_version)}')
    conn.commit()
    conn.close()
    return path


def message_id(path: str | Path, session: str, ordinal: int = 0) -> int:
    conn = sqlite3.connect(path)
    try:
        return conn.execute('select id from messages where session_id=? and ordinal=?', (session, ordinal)).fetchone()[0]
    finally:
        conn.close()


def add_message(path: str | Path, session: str, content: str) -> None:
    conn = sqlite3.connect(path)
    n = conn.execute('select coalesce(max(ordinal), -1) + 1 from messages where session_id=?', (session,)).fetchone()[0]
    conn.execute('insert into messages (session_id, ordinal, role, content, timestamp) values (?,?,?,?,?)',
                 (session, n, 'assistant', content, iso(time.time())))
    conn.commit()
    conn.close()


SECRET_FINDINGS_SCHEMA = """
create table if not exists secret_findings (id integer primary key, session_id text not null references sessions(id)
  on delete cascade, rule_name text not null, confidence text not null, location_kind text not null,
  message_ordinal integer not null, call_index integer, event_index integer, match_start integer not null,
  match_end integer not null, match_index integer not null, redacted_match text not null, rules_version text not null,
  created_at text not null default (strftime('%Y-%m-%dT%H:%M:%fZ','now')));
"""


def add_secret_finding(path: str | Path, session: str, ordinal: int, start: int, end: int, *,
                       kind: str = 'message') -> None:
    """Record a finding the way AgentsView's scanner does (UTF-8 byte offsets into the located text)."""
    conn = sqlite3.connect(path)
    conn.executescript(SECRET_FINDINGS_SCHEMA)
    conn.execute('insert into secret_findings (session_id, rule_name, confidence, location_kind, message_ordinal, '
                 'match_start, match_end, match_index, redacted_match, rules_version) values (?,?,?,?,?,?,?,?,?,?)',
                 (session, 'generic', 'definite', kind, ordinal, start, end, 0, '****', 'test'))
    conn.commit()
    conn.close()


class FakeTencentDB:
    """Loopback stand-in for the TencentDB gateway: bearer check, /v3/atomic/search and GET /health."""

    def __init__(self, *, token: str = 'fake-tdb-bearer-value-123', items=None, revision: str = 'r1',
                 sleep_s: float = 0.0, port: int = 0):
        import json
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        self.token, self.items, self.revision, self.sleep_s = token, list(items or []), revision, sleep_s
        self.auth_headers: list[str | None] = []
        self.requests: list[str] = []  # 'METHOD /path' for every request, /health included
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, code, body):
                raw = json.dumps(body).encode()
                self.send_response(code)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                stub.requests.append(f'GET {self.path}')
                if stub.sleep_s:
                    time.sleep(stub.sleep_s)  # a hung gateway hangs on every endpoint, /health included
                if self.path == '/health':
                    self._send(200, {'status': 'ok', 'revision': stub.revision})
                else:
                    self._send(404, {})

            def do_POST(self):
                length = int(self.headers.get('Content-Length') or 0)
                body = json.loads(self.rfile.read(length) or b'{}')
                stub.requests.append(f'POST {self.path}')
                stub.auth_headers.append(self.headers.get('Authorization'))
                if stub.sleep_s:
                    time.sleep(stub.sleep_s)
                if self.headers.get('Authorization') != f'Bearer {stub.token}':
                    self._send(401, {'code': 401, 'message': 'unauthorized'})
                    return
                q = str(body.get('query') or '').lower().split()
                items = [i for i in stub.items if any(w in str(i.get('content', '')).lower() for w in q)]
                self._send(200, {'code': 0, 'data': {'items': items[: int(body.get('limit') or 5)],
                                                     'revision': stub.revision}})

        self.server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
        self.server.daemon_threads = True
        self.url = f'http://127.0.0.1:{self.server.server_address[1]}'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()

    def config(self, auth_env: str = 'Z0_TEST_TDB_TOKEN', deadline_ms: int = 300) -> dict:
        return {'tencentdb': {'url': self.url, 'auth_env': auth_env, 'deadline_ms': deadline_ms}}
