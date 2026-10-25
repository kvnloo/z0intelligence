"""Turn readers: where each harness's verification labels come from (z0int#56 M1, z0int#54).

  claude-code  Claude Code transcripts under ``$CLAUDE_CONFIG_DIR/projects`` (``outcome_verifier.session_turns``).
  every other  AgentsView ``sessions.db``, read-only (``agentsview_ro``: mode=ro, user_version 74/113, staleness),
               turned into the same turn shape so ``outcome_verifier`` applies the same signal semantics.

A captured turn is placed on an AgentsView row by ONE documented rule per harness (``JOIN_RULES``). Anything the
rule cannot place unambiguously is ``unjoined`` (with a counted failure row), never guessed; a turn the index has
not reached yet is ``pending_index`` while the index is stale. ``classify_cohort`` keeps automated, subagent and
harness-injected turns out of the interactive cohort. Text read here (prompts, tool output) stays in memory for
the verifier's cue matching and never reaches a row.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from . import agentsview_ro as av
from . import harness_capture as hc
from . import outcome_verifier as ov
# The Claude Code transcript reader is the outcome verifier's own; re-exported so both readers live in one place.
from .outcome_verifier import find_transcript, session_turns, turns_from_transcript  # noqa: F401

ALIGN_TOLERANCE_S = 60.0  # hook clock vs AgentsView message clock (same host); INFERRED, pinned by the tests
STALE_HOURS = 6.0  # the AgentsView sync timer runs hourly (C6); older than this, a missing turn is pending, not lost
COHORTS = ('interactive', 'agent', 'automated', 'harness', 'eval', 'unknown')
AUTOMATED_KINDS = ('non-interactive', 'roborev')  # AgentsView session_kind values (parser/types.go)
HERMES_AUTOMATED_SOURCES = ('cron', 'kanban', 'cluster')  # Hermes session source -> AgentsView project hermes-<src>
ASK_TOOLS = (ov.ASK_TOOL, 'ask_question', 'ask_followup_question', 'clarify')
EDIT_CATEGORIES = {'Edit': 'Edit', 'Write': 'Write'}
# Each harness's own exit frame (never the command output inside it): OMP/OMO append "Command exited with code N"
# as the LAST line (oh-my-pi bash.ts); Codex heads its result with "Process exited with code N" / "Exit code: N"
# before "Output:"; Hermes terminal and DSH results are JSON with a top-level exit_code.
OMP_EXIT = re.compile(r'(?:\A|\n)Command exited with code (-?\d+)\s*\Z')
CODEX_EXIT = re.compile(r'^(?:Process exited with code|Exit code:?) (-?\d+)\s*$', re.M)
PATCH_FILE = re.compile(r'^\*\*\* (?:Update|Add) File: (\S+)', re.M)

_ORDINAL = ('the n-th captured prompt turn of the session (capture order; subagent turns excluded) is the n-th '
            'AgentsView user message of that session. Ambiguous: more than one AgentsView row claims the session '
            '(id or source_session_id), the row holds more user messages than turns were captured, or a turn was '
            'captured more than ALIGN_TOLERANCE_S before the message it would join (from that turn on: '
            'ordinal_misaligned).')


# Which test-run labels a harness's shell results can give (exit evidence is the harness's own exit frame only):
#   both          every run carries its code (Codex 'Process exited with code N', Hermes terminal JSON exit_code)
#   failure_only  only a failing run does (oh-my-pi prints 'Command exited with code N' on failure only): OMP, OMO
#   sparse        almost no run does (DSH: exit_code JSON on a handful of results)
#   none          no run does (Grok: the ACP status says the call finished, not how the command exited)
# Recorded in the verify report and every exported table manifest so a one-sided class balance is not read as real.
TEST_LABEL_POLARITIES = ('both', 'failure_only', 'sparse', 'none')


@dataclass(frozen=True)
class JoinRule:
    harness: str
    agent: str  # AgentsView sessions.agent
    name: str
    doc: str
    test_labels: str  # one of TEST_LABEL_POLARITIES

    def av_id(self, session: Any) -> str:
        return f'{self.agent}:{session}'


JOIN_RULES = {
    'hermes': JoinRule('hermes', 'hermes', 'hermes.session_user_ordinal.v0',
                       'Hermes session id -> AgentsView hermes:<sid> (hermes.go: "hermes:" + state.db id); ' + _ORDINAL,
                       'both'),
    'codex': JoinRule('codex', 'codex', 'codex.session_user_ordinal.v0',
                      'hook session_id is the Codex thread (rollout) id -> codex:<thread>; the captured turns are '
                      'ordered by capture time (recorded_at), not by turn_id. The id mapping is INFERRED from the AgentsView codex parser and pinned by '
                      'tests/test_turn_readers.py; ' + _ORDINAL + ' Cohort: without sessions.session_kind (v0.39) '
                      'an exec run cannot be told apart, so an unflagged session is unknown.', 'both'),
    'grok': JoinRule('grok', 'grok', 'grok.session_user_ordinal.v0',
                     'GROK_SESSION_ID (the hook env) -> grok:<id>; ' + _ORDINAL, 'none'),
    'omp': JoinRule('omp', 'omp', 'omp.session_user_ordinal.v0',
                    'the z0int-bridge session id (OMP_SESSION_ID) -> omp:<sid>; ' + _ORDINAL, 'failure_only'),
    'omo': JoinRule('omo', 'omo', 'omo.session_user_ordinal.v0',
                    'the z0int-bridge session id of the OMO process -> omo:<sid>; ' + _ORDINAL, 'failure_only'),
    'dsh': JoinRule('dsh', 'deepseek-harness', 'dsh.session_user_ordinal.v0',
                    'the lineage session_id the DSH capture records (its trace is the lineage turn_key) -> '
                    'deepseek-harness:<sid>; ' + _ORDINAL, 'sparse'),
}


# ----------------------------------------------------------------------------- cohorts
def _hermes_automated(meta: Mapping[str, Any]) -> bool:
    project = str(meta.get('project') or '')
    raw = str(meta.get('id') or '').partition(':')[2]
    return any(project == f'hermes-{s}' or project.startswith((f'hermes-{s}-', f'hermes-{s}_')) or
               raw.startswith(f'{s}_') for s in HERMES_AUTOMATED_SOURCES)


def classify_cohort(harness: str, meta: Mapping[str, Any] | None, capture: Mapping[str, Any] | None = None) -> str:
    """interactive / agent / automated / harness / eval / unknown for one turn.

    What the capture fixed wins (a harness-injected prompt, a subagent turn, an automated or eval run). Otherwise
    the AgentsView session decides: a subagent relationship is ``agent``; a non-interactive session_kind
    (``codex exec``, roborev), AgentsView's own automation flag, or a Hermes cron/kanban/cluster source is
    ``automated``. A turn whose session is not known is ``unknown`` and is never pooled into interactive; so is a
    Codex session on a schema without session_kind (v0.39) that AgentsView does not flag as automated.
    """
    cap = capture or {}
    if (cap.get('capture') or {}).get('is_harness_message') or cap.get('cohort') == 'harness':
        return 'harness'
    if cap.get('cohort') in ('agent', 'automated', 'eval'):
        return cap['cohort']
    if not meta:
        return 'unknown'
    if meta.get('relationship_type') == 'subagent':
        return 'agent'
    if meta.get('session_kind') in AUTOMATED_KINDS or meta.get('is_automated'):
        return 'automated'
    if harness == 'codex' and 'session_kind' not in meta:  # v0.39 has none: a `codex exec` run looks interactive
        return 'unknown'
    if harness == 'hermes' and _hermes_automated(meta):
        return 'automated'
    return 'interactive'


# ----------------------------------------------------------------------------- captured turns
def captured_turns(harness: str, root: str | Path | None = None) -> dict[Any, list[dict[str, Any]]]:
    """Prompt turns captured for one harness, per session in capture order (from opportunity or outcome rows).

    Subagent turns (trace ``agent:<id>``) are not user messages of the session and are left out.
    """
    sdir = hc.state_dir(harness, root)
    wanted = {hc.schema(harness, 'opportunity_record'): 0, hc.schema(harness, 'turn_outcome'): 1}
    turns: dict[tuple, dict[str, Any]] = {}
    for name in ('opportunities.jsonl', 'outcomes.jsonl'):
        for i, r in enumerate(hc._read_jsonl(sdir / name)):
            if r.get('schema') not in wanted or not r.get('trace_id') or str(r['trace_id']).startswith('agent:'):
                continue
            key = (r.get('session_id'), r['trace_id'])
            order = (str(r.get('recorded_at') or ''), wanted[r['schema']], i)
            t = turns.setdefault(key, {'session_id': key[0], 'trace_id': key[1], '_order': order})
            t['_order'] = min(t['_order'], order)  # the earliest row: its recorded_at is the capture time
            for k in ('turn_key', 'cohort', 'capture'):
                if t.get(k) is None and r.get(k) is not None:
                    t[k] = r[k]
            if wanted[r['schema']] == 1:
                t['observed'] = r
    out: dict[Any, list[dict[str, Any]]] = {}
    for t in sorted(turns.values(), key=lambda t: t['_order']):
        t['recorded_at'] = t.pop('_order')[0] or None
        t.setdefault('turn_key', hc.turn_key(harness, t['session_id'], t['trace_id']))
        out.setdefault(t['session_id'], []).append(t)
    return out


# ----------------------------------------------------------------------------- AgentsView (read-only)
def exit_code(text: str, status: str | None, harness: str) -> int | None:
    """N from the harness's own exit frame around a shell tool result, else None (unknown).

    Only the frame counts: exit-like text in the command's output (a JSON assertion diff, a nested process's
    notice) never does. ``status`` is never exit evidence: no AgentsView parser maps a non-zero exit to
    ``errored`` (Grok's ACP ``completed`` and Codex's default ``completed`` only say the call finished), and the pi
    (OMP/OMO), Hermes and deepseek-harness parsers record none. Execution completing is never a pass.
    """
    if harness in ('omp', 'omo'):
        m = OMP_EXIT.search(text)
    elif harness == 'codex':
        head, sep, _ = text.partition('\nOutput:')
        m = CODEX_EXIT.search(head if sep else head.partition('\n')[0])
    elif harness in ('hermes', 'dsh'):
        try:
            code = json.loads(text).get('exit_code')
        except (ValueError, AttributeError):
            return None
        return code if isinstance(code, int) and not isinstance(code, bool) else None
    else:  # grok: the ACP result carries no exit code
        return None
    return int(m.group(1)) if m else None


def _command(inp: Mapping[str, Any]) -> str | None:
    cmd = inp.get('command') or inp.get('cmd') or inp.get('script')
    if isinstance(cmd, list):
        cmd = ' '.join(str(c) for c in cmd)
    return cmd if isinstance(cmd, str) else None


SESSION_COLUMNS = ('id', 'project', 'agent', 'session_kind', 'is_automated', 'relationship_type', 'parent_session_id',
                   'cwd')


class AgentsViewReader:
    """One read-only AgentsView connection; ``unavailable`` says why there is none.

    The queries raise ``sqlite3.Error`` on a schema they cannot read; the verifier turns that into a
    ``reader_unavailable`` failure row (``reader_error``), never an exception into its caller.
    """

    def __init__(self, path: str | Path | None = None, *, now: float | None = None):
        got = av.connect(path)
        self.conn = None if isinstance(got, av.Unavailable) else got
        self.unavailable = got if isinstance(got, av.Unavailable) else None
        self.staleness = av.staleness_hours(self.conn, now) if self.conn is not None else None
        self._session_columns: str | None = None

    @property
    def available(self) -> bool:
        return self.conn is not None

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()

    def has_agent(self, agent: str) -> bool:
        return self.conn.execute('select 1 from sessions where agent = ? limit 1', (agent,)).fetchone() is not None

    def candidates(self, rule: JoinRule, session: Any) -> list[dict[str, Any]]:
        """Session rows that claim this captured session: its own id, or a copy naming it as source."""
        if self._session_columns is None:  # session_kind only where present (v0.44; v0.39 has none)
            have = {r[1] for r in self.conn.execute('pragma table_info(sessions)')}
            self._session_columns = ', '.join(c for c in SESSION_COLUMNS if c in have or c != 'session_kind')
        cur = self.conn.execute(
            f'select {self._session_columns} from sessions'
            ' where agent = ? and deleted_at is null and (id = ? or source_session_id = ?)',
            (rule.agent, rule.av_id(session), str(session)))
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def turns(self, av_id: str, cwd: str | None, harness: str) -> list[dict[str, Any]]:
        """The session's turns in the Claude transcript reader's shape: one per user message (ids ``#<ordinal>``)."""
        events = {}
        for ordinal, idx, status, content, ts in self.conn.execute(
                'select tool_call_message_ordinal, call_index, status, content, timestamp from tool_result_events'
                ' where session_id = ? order by event_index', (av_id,)):
            events[(ordinal, idx)] = (status, content or '', ov._ts(ts))
        calls: dict[int, list[tuple]] = {}
        for mid, name, cat, inp, result, idx in self.conn.execute(
                'select message_id, tool_name, category, input_json, result_content, call_index from tool_calls'
                ' where session_id = ? order by message_id, call_index', (av_id,)):
            calls.setdefault(mid, []).append((name, cat, inp, result, idx or 0))
        turns: list[dict[str, Any]] = []
        cur, n = None, 0
        for mid, ordinal, role, content, ts, is_system, subtype in self.conn.execute(
                'select id, ordinal, role, content, timestamp, is_system, source_subtype from messages'
                ' where session_id = ? order by ordinal', (av_id,)):
            t, text = ov._ts(ts), content or ''
            if role == 'user' and not is_system and subtype != 'tool_result' and text.strip():
                if text.lstrip().startswith(ov.INTERRUPT):
                    if cur is not None:
                        cur['interrupted'] = True
                    continue
                n += 1
                cur = ov.new_turn(f'#{n}', av_id, t, cwd, text, text.lstrip().startswith(ov.HARNESS_PREFIXES))
                turns.append(cur)
                continue
            if cur is None or role != 'assistant':
                continue
            if t is not None:
                cur['ended_at'] = max(cur['ended_at'] or t, t)
            cur['assistant_ids'].add(mid)
            if text.strip():
                cur['asked_text'] = text.rstrip().endswith('?')
                cur['_final_text'] = text
            for name, cat, inp, result, idx in calls.get(mid, []):
                self._tool(cur, name, cat, inp, result, events.get((ordinal, idx)), t, cwd, harness)
        return ov.finish_turns(turns)

    @staticmethod
    def _tool(cur: dict[str, Any], name: str, cat: str, raw_inp: str | None, result: str | None,
              event: tuple | None, t: float | None, cwd: str | None, harness: str) -> None:
        try:
            inp = json.loads(raw_inp or '{}')
        except ValueError:
            inp = {}
        inp = inp if isinstance(inp, dict) else {}
        status, text, t1 = event or (None, result or '', t)
        kind = 'Bash' if cat == 'Bash' else EDIT_CATEGORIES.get(cat) or ('Read' if cat == 'Read' else
                                                                         ov.ASK_TOOL if name in ASK_TOOLS else name)
        cur['tool_calls'] += 1
        cur['tool_names'][kind] += 1
        call = {'name': kind, 'turn': cur, 'cwd': cwd, 't0': t, 't1': t1 or t, 'error': status == 'errored'}
        cur['tool_seq'].append(call)
        if kind == 'Bash' and (cmd := _command(inp)):
            call['command'] = cmd
            cur['bash'].append(call)
            for m in ov.PR_MERGE.finditer(cmd):
                cur['pr_merged_by_agent'].append({'number': int(m.group(1)) if m.group(1) else None, 'repo': m.group(2)})
            ov.bash_result(call, text, exit_code(text, status, harness))
            if call['exit'] is None and call.get('commits'):
                call['committed'] = True  # git printed '[branch sha] subject', which it does only once committed
        elif kind in ('Edit', 'Write'):
            paths = [p for p in (inp.get('file_path') or inp.get('path') or inp.get('notebook_path'),) if isinstance(p, str)]
            paths += PATCH_FILE.findall(inp.get('input') or inp.get('patch') or '') if not paths else []
            for p in paths:
                cur['edits'].append(p)
                cur['edit_ops'].append({**{k: v for k, v in call.items() if k != 'turn'}, 'path': p, 'created': False,
                                        '_new': inp.get('new_string') or inp.get('content'),
                                        '_old': inp.get('old_string')})
        elif kind == 'Read' and isinstance(inp.get('file_path') or inp.get('path'), str):
            call['path'] = inp.get('file_path') or inp.get('path')
            if not call['error']:
                cur['reads'].add(call['path'])
        elif kind == ov.ASK_TOOL:
            call['answered'] = not call['error']
            cur['ask_tool'].append(call)
        elif cat == 'Task':
            cur['agents_spawned'] += 1


def reader_error(exc: sqlite3.Error) -> dict[str, str]:
    """A query the schema cannot answer -> the ``reader_unavailable`` detail (exception type only, no SQL text)."""
    missing = isinstance(exc, sqlite3.OperationalError) and str(exc).startswith('no such')
    return {'reason': 'schema' if missing else 'error', 'error': type(exc).__name__}


# ----------------------------------------------------------------------------- join
def join_session(rule: JoinRule, reader: AgentsViewReader, turns: list[dict[str, Any]], *,
                 stale: bool) -> tuple[list[dict[str, Any]], dict[str, Any] | None, list[dict[str, Any]]]:
    """(join per captured turn, the session's AgentsView row or None, its AgentsView turns) by the harness rule."""
    cands = reader.candidates(rule, turns[0]['session_id'])
    base = {'rule': rule.name, 'candidates': len(cands)}
    if len(cands) != 1:
        if len(cands) > 1:
            return [dict(base, state='unjoined', reason='ambiguous_session') for _ in turns], None, []
        state = 'pending_index' if stale else 'unjoined'
        return [dict(base, state=state, reason='session_missing') for _ in turns], None, []
    meta = cands[0]
    av_turns = reader.turns(meta['id'], meta.get('cwd') or None, rule.harness)
    if len(av_turns) > len(turns):
        return [dict(base, state='unjoined', reason='ordinal_mismatch') for _ in turns], meta, []
    joins, misaligned = [], False
    for k, turn in enumerate(turns, 1):
        if k > len(av_turns):
            joins.append(dict(base, state='pending_index' if stale else 'unjoined', reason='ordinal_missing'))
            continue
        # fewer AgentsView user messages than captured turns (e.g. an injected prompt stored as is_system) would
        # put this turn on a LATER message: a capture before that message is a misalignment, never a label
        captured, message = ov._ts(turn.get('recorded_at')), av_turns[k - 1]['started_at']
        misaligned = misaligned or (captured is not None and message is not None
                                    and captured < message - ALIGN_TOLERANCE_S)
        joins.append(dict(base, state='unjoined', reason='ordinal_misaligned') if misaligned
                     else dict(base, state='joined', ordinal=k))
    return joins, meta, av_turns
