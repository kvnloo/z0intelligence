"""Synthetic AgentsView sessions.db builder for the turn-reader tests (schema only, no real rows).

The four core tables come from tests/fixtures/agentsview_core_schema.sql, a copy of the real v0.44 schema
(user_version 113), or, for user_version 74, tests/fixtures/agentsview_core_schema_v039.sql, copied from
agentsview v0.39.0 internal/db/schema.sql (no sessions.session_kind). Session ids follow AgentsView's
``<agent>:<raw id>`` convention.
"""
import datetime as dt
import json
import sqlite3
from pathlib import Path

FIXTURES = Path(__file__).parent / 'fixtures'
SCHEMA = (FIXTURES / 'agentsview_core_schema.sql').read_text()
SCHEMA_V039 = (FIXTURES / 'agentsview_core_schema_v039.sql').read_text()


def iso(t):
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')


class AVFixture:
    def __init__(self, path, user_version=113):
        self.path = Path(path)
        self.conn = sqlite3.connect(self.path)
        self.conn.executescript(SCHEMA_V039 if int(user_version) == 74 else SCHEMA)
        self.columns = {r[1] for r in self.conn.execute('pragma table_info(sessions)')}
        self.conn.execute(f'pragma user_version = {int(user_version)}')
        self.ordinal = {}

    def session(self, agent, raw_id, *, started=0.0, project='fixture', cwd='/nonexistent', session_kind='',
                is_automated=0, relationship_type='', parent_session_id=None, source_session_id='', av_id=None):
        av_id = av_id or f'{agent}:{raw_id}'
        row = {'id': av_id, 'project': project, 'agent': agent, 'session_kind': session_kind,
               'started_at': iso(started), 'cwd': cwd, 'is_automated': is_automated,
               'relationship_type': relationship_type, 'parent_session_id': parent_session_id,
               'source_session_id': source_session_id}
        row = {k: v for k, v in row.items() if k in self.columns}  # v0.39 has no session_kind
        self.conn.execute(f'insert into sessions ({", ".join(row)}) values ({", ".join("?" * len(row))})',
                          tuple(row.values()))
        self.ordinal[av_id] = 0
        return av_id

    def _message(self, sid, role, content, t, *, has_tool_use=0, is_system=0, source_subtype=''):
        n = self.ordinal[sid]
        self.ordinal[sid] = n + 1
        cur = self.conn.execute(
            'insert into messages (session_id, ordinal, role, content, timestamp, has_tool_use, content_length, is_system,'
            ' source_subtype) values (?,?,?,?,?,?,?,?,?)',
            (sid, n, role, content, iso(t), has_tool_use, len(content), is_system, source_subtype))
        return cur.lastrowid, n

    def user(self, sid, text, t, **kw):
        self._message(sid, 'user', text, t, **kw)

    def say(self, sid, text, t):
        self._message(sid, 'assistant', text, t)

    def tool(self, sid, name, category, inp, result, t, *, status='completed', dt_=1):
        """status None: no tool_result_events row (the pi/OMP/OMO, Hermes and DSH parsers write none)."""
        mid, n = self._message(sid, 'assistant', '', t, has_tool_use=1)
        self.conn.execute(
            'insert into tool_calls (message_id, session_id, tool_name, category, tool_use_id, input_json,'
            ' result_content_length, result_content, call_index) values (?,?,?,?,?,?,?,?,0)',
            (mid, sid, name, category, f'tu-{mid}', json.dumps(inp), len(result), result))
        if status is None:
            return
        self.conn.execute(
            'insert into tool_result_events (session_id, tool_call_message_ordinal, call_index, tool_use_id, source,'
            ' status, content, content_length, timestamp) values (?,?,0,?,?,?,?,?,?)',
            (sid, n, f'tu-{mid}', 'tool_result', status, result, len(result), iso(t + dt_)))

    def bash(self, sid, command, t, exit=0, out='', name='Bash', key='command'):
        result = f'Exit code {exit}\n{out}' if exit else out
        self.tool(sid, name, 'Bash', {key: command}, result, t, status='errored' if exit else 'completed')

    def close(self):
        self.conn.commit()
        self.conn.close()
        return self.path
