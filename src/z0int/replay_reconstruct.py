"""Replay reconstruction v0 (z0int#143 lane D; z0int#54/#56): decision opportunities rebuilt from history.

The learning loop can only train on turns that carry prompt-time DecisionOpportunity features, and only live-captured
turns have them. This module walks the full AgentsView history (``sessions`` / ``messages`` / ``tool_calls``, opened
``mode=ro``) and the Claude Code transcripts AgentsView indexed, and for each user turn rebuilds what was knowable at
that moment:

  request    the question-scoping fact families (``decision_opportunity.required_families``), the effect classes
             (``effect_inference`` when this checkout has it, else the harness default ``read``) and the authority
             (harness default: no AODL contract is replayed);
  context    what happened earlier in the same session only: prior turns, tool calls, shell exits, tool errors, edits,
             files touched (a hash), interrupts, asks, subagents; never anything from this turn or later;
  identity   repo/cwd and project as hashes, the harness;
  git        **unknown**: git state at decision time was not recorded and is never guessed. The rebuilt State Packet
             (``z0int.reconstructed_packet.v0``) has no claim and one ``git`` unknown, so a request that depends on a
             git fact family has it as a blocking ``source_unavailable`` unknown, exactly as a live turn without
             readable git state;
  observed   post-decision, never a feature: answered / asked / tool families used / delegated / interrupted
             (escalation is not observable in transcripts and stays null);
  outcome    ``outcome_verifier`` on the same turns (tests / CI exits, commits reverted or SZZ-fixed, user correction
             cues, interrupts, asks) with GitHub lookups disabled: no network, no model.

Every row is ``provenance: reconstructed`` with label classes in their own namespace (``reconstructed/<class>``). The
tables live in their own cohorts (``<harness>/reconstructed`` for root sessions, ``<harness>/reconstructed-agent``
for subagent sessions); ``loop_export.Table`` refuses to put an observed row there or a reconstructed row anywhere
else, ``episodes.is_labelled`` never counts a reconstructed episode as observed truth, and ``episode_split`` splits
one provenance at a time. Rows hold features, counts, closed vocabulary and hashes only: no prompt, response,
command or path text.

Deterministic and idempotent: one watermark per session (its AgentsView fingerprint, transcript size/mtime, this
module's version and the fix window). An unchanged session is never recomputed; a changed one is recomputed whole.
Outputs go only under ``$Z0INT_HOME/replay_reconstruct`` and are refused inside a git worktree.

    z0int reconstruct run    [--budget-s S] [--agents claude,codex,...]   rebuild sessions (claude first)
    z0int reconstruct build                                               tables + episodes + dataset receipt
    z0int reconstruct split  [--eval-start D --eval-end D]                temporal + lineage split (locked window)
    z0int reconstruct learn                                               separate learner run per stratum
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
import socket
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

from . import agentsview_ro as av
from . import loop_export as le

VERSION = '0.1.0'
RECONSTRUCTOR = {'id': 'z0int.replay_reconstruct', 'version': VERSION}
PROVENANCE = 'reconstructed'
LABEL_NS = 'reconstructed/'
COHORT_ROOT, COHORT_AGENT = 'reconstructed', 'reconstructed-agent'
COHORTS = (COHORT_ROOT, COHORT_AGENT)
PACKET_SCHEMA = 'z0int.reconstructed_packet.v0'
RECEIPT_SCHEMA = 'z0int.reconstructed_dataset_receipt.v0'
WATERMARK_SCHEMA = 'z0int.reconstructed_watermarks.v0'
# AgentsView agent -> z0int harness, in processing order (Claude Code first). Agents without a harness here (chat
# exports, IDE logs) have no capture schema or exit-frame rule and are counted, never emitted.
AGENT_HARNESS = {'claude': 'claude-code', 'codex': 'codex', 'hermes': 'hermes', 'omp': 'omp', 'grok': 'grok',
                 'deepseek-harness': 'dsh', 'omo': 'omo'}
AGENT_ORDER = tuple(AGENT_HARNESS)
DEFAULT_LOCK = '~/.z0int/loop/quiet-lane.lock'
# The verifier's git calls are read-only and local. A partial clone would lazily fetch missing objects (blame, show)
# over its remote: forbidden here. Set for the duration of a run; a missing object is then an unresolved signal.
OFFLINE_GIT_ENV = {'GIT_NO_LAZY_FETCH': '1', 'GIT_ALLOW_PROTOCOL': 'file', 'GIT_TERMINAL_PROMPT': '0',
                   'GIT_SSH_COMMAND': 'false', 'GIT_ASKPASS': 'false'}
MIN_CONTENT_CHARS = 32  # below this a normalised request is not a content identity (split contamination/dedup)
GIT_STATE = {'state': 'unknown', 'why': 'git state at decision time was not recorded; never guessed'}
TOOL_FAMILIES = ('shell', 'edit', 'read', 'search', 'web', 'delegate', 'ask', 'plan', 'mcp', 'skill', 'other')
_FAMILY_OF = {'Bash': 'shell', 'Edit': 'edit', 'Write': 'edit', 'MultiEdit': 'edit', 'NotebookEdit': 'edit',
              'Read': 'read', 'Glob': 'search', 'Grep': 'search', 'LS': 'search', 'WebFetch': 'web',
              'WebSearch': 'web', 'Agent': 'delegate', 'Task': 'delegate', 'AskUserQuestion': 'ask',
              'TodoWrite': 'plan', 'ExitPlanMode': 'plan', 'Skill': 'skill'}
SESSION_COLUMNS = ('id', 'agent', 'project', 'cwd', 'entrypoint', 'session_kind', 'is_automated', 'relationship_type',
                   'parent_session_id', 'started_at', 'file_path', 'file_hash', 'file_size', 'file_mtime',
                   'message_count', 'user_message_count', 'transcript_revision', 'local_modified_at')
# fields of a session row that change when its content changes (the watermark)
FINGERPRINT_COLUMNS = ('file_hash', 'file_size', 'file_mtime', 'message_count', 'user_message_count',
                       'transcript_revision', 'local_modified_at', 'started_at', 'parent_session_id')


def _sha(obj: Any, n: int = 16) -> str:
    return le._sha(obj, n)


def _iso(t: float | None) -> str | None:
    return None if t is None else time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(t))


def _bucket(x: float | int | None) -> int | None:
    """log2 bucket of a non-negative count or duration (closed, coarse)."""
    if x is None or x < 0:
        return None
    return int(math.log2(x + 1))


def in_git_worktree(path: str | Path) -> bool:
    p = Path(os.path.abspath(path))
    return any((q / '.git').exists() for q in (p, *p.parents))


def out_root(home: str | Path | None = None) -> Path:
    from . import paths
    base = Path(home).expanduser().resolve() if home else paths.home()
    root = base / 'replay_reconstruct'
    if in_git_worktree(root):
        raise ValueError(f'refusing output root {root}: it is inside a git worktree (history is never committed)')
    return root


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(obj, indent=1, sort_keys=True) + '\n')
    os.replace(tmp, path)


def _write_lines(path: Path, rows: Iterable[Mapping[str, Any]]) -> str:
    body = ''.join(json.dumps(r, sort_keys=True) + '\n' for r in rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(body)
    os.replace(tmp, path)
    return hashlib.sha256(body.encode()).hexdigest()


# ----------------------------------------------------------------------------- the rebuilt opportunity
def reconstructed_packet() -> dict[str, Any]:
    """The State Packet a reconstructed turn is scoped against: no claim (nothing was recorded), git unknown."""
    return {'schema': PACKET_SCHEMA, 'packet_id': None, 'built_at': None, 'current_claims': [],
            'unknowns': [{'key': 'git', 'source_status': 'unknown',
                          'reason': 'reconstructed from history: git state at decision time was not recorded'}],
            'coverage': {'git': 'none'}, 'source_revisions': {}, 'evidence': []}


def infer_effects(text: str) -> tuple[list[str], str]:
    """Effect classes from ``effect_inference`` when this checkout has it (deterministic rules only, never a model),
    else the harness default ``read`` -- the same default the live capture uses."""
    from .decision_opportunity import EFFECTS
    try:
        from .effect_inference import infer_effects as fn  # type: ignore[attr-defined]
    except ImportError:
        return ['read'], 'harness-default'
    try:
        out = fn(text, None, slm=False)
    except Exception:  # noqa: BLE001 - an inference failure falls back to the default, counted by source
        return ['read'], 'harness-default:inference_error'
    effects = out.get('effects') if isinstance(out, Mapping) else out
    effects = sorted({str(e) for e in effects or [] if str(e) in EFFECTS}) if isinstance(effects, (list, tuple, set)) \
        else []
    return (effects or ['read']), 'effect_inference'


def opportunity_for(text: str, harness: str, trace: str) -> tuple[dict[str, Any], str, str]:
    """(DecisionOpportunity, deterministic gate, effects source) for one historical request. The request text is
    used here only; callers never write the opportunity itself."""
    from .decision_opportunity import build_decision_opportunity, deterministic_gate
    effects, source = infer_effects(text)
    opp = build_decision_opportunity('.', text, effects=effects, packet=reconstructed_packet(), scoped=True,
                                     harness=harness, trace_id=trace)
    return opp, deterministic_gate(opp), source


# ----------------------------------------------------------------------------- AgentsView sessions (mode=ro)
def raw_session(av_id: str, agent: str) -> str:
    """The harness's own session id (what a live capture records): AgentsView prefixes non-Claude ids with the agent."""
    return av_id[len(agent) + 1:] if av_id.startswith(f'{agent}:') else av_id


def list_sessions(conn: sqlite3.Connection, agents: Iterable[str] = AGENT_ORDER) -> list[dict[str, Any]]:
    have = {r[1] for r in conn.execute('pragma table_info(sessions)')}
    cols = [c for c in SESSION_COLUMNS if c in have]
    agents = [a for a in agents if a in AGENT_HARNESS]
    if not agents:
        return []
    cur = conn.execute(f'select {", ".join(cols)} from sessions where deleted_at is null and agent in '
                       f'({", ".join("?" * len(agents))})', agents)
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    order = {a: i for i, a in enumerate(AGENT_ORDER)}
    # claude first; within an agent, root sessions before subagents (a parent's fold sees its subagents), then time
    rows.sort(key=lambda s: (order[s['agent']], s.get('relationship_type') == 'subagent', str(s.get('started_at') or ''),
                             s['id']))
    return rows


def root_of(session: Mapping[str, Any], by_id: Mapping[str, Mapping[str, Any]]) -> Mapping[str, Any]:
    """The lineage root (follow parent_session_id); a missing parent ends the walk at the last known session."""
    cur, seen = session, {session['id']}
    for _ in range(32):
        parent = by_id.get(cur.get('parent_session_id') or '')
        if parent is None or parent['id'] in seen:
            break
        seen.add(parent['id'])
        cur = parent
    return cur


def is_subagent(session: Mapping[str, Any]) -> bool:
    return session.get('relationship_type') == 'subagent'


def claude_transcript(session: Mapping[str, Any]) -> Path | None:
    p = session.get('file_path')
    if session.get('agent') != 'claude' or not p:
        return None
    p = Path(p)
    return p if p.suffix == '.jsonl' and p.is_file() else None


def fingerprint(session: Mapping[str, Any], transcript: Path | None, *, fix_days: int) -> str:
    extra: dict[str, Any] = {}
    if transcript is not None:
        try:
            st = transcript.stat()
            extra['transcript'] = [st.st_size, st.st_mtime_ns]
        except OSError:
            extra['transcript'] = None
        sub = transcript.with_suffix('') / 'subagents'
        if sub.is_dir():
            try:
                extra['subagents'] = sorted([p.name, p.stat().st_size] for p in sub.glob('agent-*.jsonl'))
            except OSError:
                extra['subagents'] = None
    return _sha({'session': {k: session.get(k) for k in FINGERPRINT_COLUMNS}, 'files': extra,
                 'reconstructor': VERSION, 'fix_days': fix_days}, 32)


def source_cohort(session: Mapping[str, Any], harness: str) -> str:
    """The cohort the live classifier would give this session (interactive / agent / automated / harness / unknown)."""
    if is_subagent(session):
        return 'agent'
    if harness == 'claude-code':
        from .claude_code_engagement import cohort
        return cohort(session.get('entrypoint'), session.get('cwd'))
    from .turn_readers import classify_cohort
    return classify_cohort(harness, {k: session.get(k) for k in ('id', 'project', 'session_kind', 'is_automated',
                                                                  'relationship_type') if k in session})


# ----------------------------------------------------------------------------- turns -> rows
def _tool_family(name: str) -> str:
    if name.startswith('mcp__'):
        return 'mcp'
    return _FAMILY_OF.get(name, 'other')


def _families(names: Mapping[str, int]) -> dict[str, int]:
    out: Counter = Counter()
    for n, k in names.items():
        out[_tool_family(str(n))] += int(k)
    return {f: out[f] for f in TOOL_FAMILIES if out[f]}


class Context:
    """What the session had shown before the current turn (prompt-time knowable). Paths are hashed on entry."""

    def __init__(self, started: float | None):
        self.started = started
        self.turns = 0
        self.tool_calls = self.bash = self.bash_nonzero = self.tool_errors = self.edits = 0
        self.interrupts = self.asks = self.subagents = 0
        self.tests_failed_last: bool | None = None
        self.prev_end: float | None = None
        self.files: set[str] = set()

    def snapshot(self, now: float | None) -> dict[str, Any]:
        since_start = (now - self.started) if now is not None and self.started is not None else None
        since_prev = (now - self.prev_end) if now is not None and self.prev_end is not None else None
        return {'prior_turns': self.turns, 'prior_tool_calls': self.tool_calls, 'prior_shell_calls': self.bash,
                'prior_shell_nonzero_exit': self.bash_nonzero, 'prior_tool_errors': self.tool_errors,
                'prior_edits': self.edits, 'prior_interrupts': self.interrupts, 'prior_asks': self.asks,
                'prior_subagents': self.subagents, 'prior_tests_failed_last': self.tests_failed_last,
                'files_touched': len(self.files),
                'files_touched_sha': _sha(sorted(self.files), 16) if self.files else None,
                'session_age_bucket': _bucket(since_start), 'idle_bucket': _bucket(since_prev)}

    def add(self, t: Mapping[str, Any], test_re: Any) -> None:
        self.turns += 1
        self.tool_calls += int(t.get('tool_calls') or 0)
        self.bash += len(t.get('bash') or [])
        self.bash_nonzero += sum(1 for c in t.get('bash') or [] if c.get('exit') not in (None, 0))
        self.tool_errors += sum(1 for c in t.get('tool_seq') or [] if c.get('error'))
        self.edits += len(t.get('edits') or [])
        self.interrupts += bool(t.get('interrupted'))
        self.asks += bool(t.get('ask_tool')) or bool(t.get('asked_text'))
        self.subagents += int(t.get('agents_spawned') or 0)
        tests = [c for c in t.get('bash') or [] if test_re.search(c.get('command', '')) and c.get('exit') is not None]
        if tests:
            self.tests_failed_last = tests[-1]['exit'] != 0
        for p in list(t.get('edits') or []) + sorted(t.get('reads') or []):
            self.files.add(_sha(str(p), 16))
        self.prev_end = t.get('ended_at') or self.prev_end


def _label(core: Mapping[str, Any] | None) -> dict[str, Any]:
    lab = le.label_of(None if core is None else {'verification_state': core['verification_state'],
                                                   'label_class': core['label_class'],
                                                   'label_confidence': core['label_confidence'],
                                                   'signals': core['signals']})
    lab['label_class'] = LABEL_NS + str(lab['label_class'] or 'unknown')
    lab['provenance'] = PROVENANCE
    lab['signal_kinds'] = dict(sorted(Counter(f"{s['kind']}[{s['polarity']:+d}]"
                                              for s in (core or {}).get('signals') or []).items()))
    return lab


def session_turns(session: Mapping[str, Any], harness: str, reader: Any) -> tuple[list[dict[str, Any]], str]:
    """(turns in the outcome verifier's shape, label source). Claude Code: its transcript with subagent work folded
    in; every other harness (and a Claude session whose transcript is gone): the AgentsView rows."""
    from . import outcome_verifier as ov
    path = claude_transcript(session)
    if path is not None:
        turns, source = ov.session_turns(path), 'claude_transcript'
    else:
        turns, source = reader.turns(session['id'], session.get('cwd') or None, harness), 'agentsview'
    seen: set = set()
    for i, t in enumerate(turns, 1):  # every turn needs a unique trace for the verifier's per-turn cores
        pid = t.get('prompt_id') or f'#{i}'
        if pid in seen:
            pid = f'{pid}@{i}'
        seen.add(pid)
        t['prompt_id'] = pid
    return turns, source


def reconstruct_session(session: Mapping[str, Any], *, root: Mapping[str, Any], reader: Any, index: Any,
                        as_of: float, fix_days: int, host: str, counts: Counter) -> list[dict[str, Any]]:
    """One record per non-injected user turn: ``{'training': training_row.v0, 'episode': routing_episode.v0}``."""
    from . import episodes as E
    from . import outcome_verifier as ov
    from .decision_opportunity import ACTIONS
    agent = session['agent']
    harness = AGENT_HARNESS[agent]
    turns, source = session_turns(session, harness, reader)
    if not turns:
        counts['sessions_without_turns'] += 1
        return []
    sid = raw_session(session['id'], agent)
    group = _sha({'session': raw_session(root['id'], root['agent'])}, 12)
    cores = ov._rows_for_session(turns, sid, index=index, gh=ov.GitHub(enabled=False), fix_days=fix_days,
                                 now=as_of, density=True)
    cohort = COHORT_AGENT if is_subagent(session) else COHORT_ROOT
    src_cohort = source_cohort(session, harness)
    one_hot = src_cohort if src_cohort in le.COHORTS else 'unknown'
    repo_id = _sha({'cwd': session.get('cwd') or ''}, 12) if session.get('cwd') else None
    project_id = _sha({'project': session.get('project') or ''}, 12) if session.get('project') else None
    ctx = Context(turns[0].get('started_at'))
    out = []
    for ordinal, t in enumerate(turns, 1):
        if t.get('harness_message'):
            counts['harness_messages_skipped'] += 1
            ctx.add(t, ov.TEST_CMD)
            continue
        tid = t['prompt_id']
        text = t.get('_prompt') or ''
        opp, gate, eff_source = opportunity_for(text, harness, tid)
        counts[f'effects_source:{eff_source}'] += 1
        feats = le.opportunity_features({'opportunity': opp, 'gate': gate}, one_hot)
        context = {**ctx.snapshot(t.get('started_at')), 'turn_ordinal': ordinal, 'repo_id': repo_id,
                   'project_id': project_id, 'harness': harness, 'git': dict(GIT_STATE),
                   'request_len_bucket': _bucket(len(text)),
                   'request_keywords': E.keyword_features(text)}
        hashes = E.content_hashes(text)
        asked_tool = bool(t.get('ask_tool'))
        asked = asked_tool or bool(t.get('asked_text'))
        tool_calls = int(t.get('tool_calls') or 0)
        observed = {'post_decision': True, 'action': 'ASK' if asked else 'ACT', 'asked_via_tool': asked_tool,
                    'tool_calls': tool_calls, 'tool_calls_subagents': int(t.get('tool_calls_subagents') or 0),
                    'answered_only': not tool_calls and not asked,
                    'tool_families': _families(t.get('tool_names') or {}),
                    'delegated': bool(t.get('agents_spawned')), 'subagents_spawned': int(t.get('agents_spawned') or 0),
                    'escalated': None, 'interrupted': bool(t.get('interrupted')),
                    'shell_calls': len(t.get('bash') or []), 'edits': len(t.get('edits') or []),
                    'assistant_messages': int(t.get('assistant_messages') or 0)}
        core = cores.get(tid)
        label = _label(core)
        started, ended = t.get('started_at'), t.get('ended_at')
        day = (_iso(started) or '')[:10] or None
        turn_key = _sha({'provenance': PROVENANCE, 'harness': harness, 'session': sid, 'trace': tid})
        twin = le.row_turn_key(harness, sid, tid) if harness == 'claude-code' and not tid.startswith('#') else None
        lineage = {'group': group, 'harness': harness, 'cohort': cohort, 'source_cohort': src_cohort,
                   'subagent_session': is_subagent(session), 'root_is_self': root['id'] == session['id'],
                   'label_source': source, 'live_twin_turn_key': twin, 'live_capture_group': None}
        space = [a for a in opp.get('action_space') or [] if isinstance(a, dict)]
        kinds = sorted({a['kind'] for a in space if a.get('kind') in ACTIONS}, key=ACTIONS.index)
        legal = sorted({a['kind'] for a in space if a.get('legal') and a.get('kind') in ACTIONS}, key=ACTIONS.index)
        if label['resolved']:
            outcome = {'observed': True, 'y_success': label['y_success'], 'state': label['state'], 'independent': True,
                       'source': f'outcome_verifier:{PROVENANCE}:{harness}', 'label_class': label['label_class'],
                       'label_confidence': label['label_confidence'], 'oracles': label['oracles']}
        else:
            outcome = E.no_outcome('unverified' if core is not None else 'no_verification_core')
        draft = E.Draft(
            family='decision_opportunity', source=f'{PROVENANCE}.{harness}', decided_ts=started,
            source_refs=[{'store': f'agentsview:{source}', 'line_no': ordinal,
                          'line_sha256': _sha({'fp': session.get('file_hash'), 'turn': ordinal, 'trace': tid}, 32),
                          'id_hash': _sha({'id': f'{sid}:{tid}'}, 20), 'ts': _iso(started)}],
            evidence={'kind': 'hashed_features', 'features': feats, 'feature_schema_sha': le.FEATURE_SCHEMA_SHA,
                      'request_sha256': opp['intent']['revision'], 'content_sha': hashes['content_sha'],
                      'content_norm_sha': hashes['content_norm_sha'],
                      'state_ref': {'semantic_id': opp['semantic_id'],
                                    'opportunity_id': opp['trace']['opportunity_id'], 'packet_id': None,
                                    'packet_schema': PACKET_SCHEMA, 'git_state': 'unknown'},
                      # a short request ("yes", "continue") has no honest content identity: no semantic dedup
                      'semantic_key': hashes['content_norm_sha'] if len(E._norm_text(text)) >= MIN_CONTENT_CHARS
                      else None, 'context': context},
            candidates={'set': kinds, 'source': 'reconstructed_action_space', 'exact': False, 'legal': legal,
                        'skipped': []},
            action={'chosen': observed['action'], 'decided_by': 'agent', 'fallbacks': 0, 'model': None},
            receipt={'execution_completed': None, 'status': None, 'attempts': 1,
                     'latency_ms': round((ended - started) * 1000, 3) if started and ended and ended >= started
                     else None, 'input_tokens': 0, 'output_tokens': 0, 'tool_calls': tool_calls,
                     'assistant_messages': observed['assistant_messages'], 'cost_usd': None,
                     'physical_call_attempted': None, 'output_sha256': None},
            outcome=outcome,
            provenance={'kind': PROVENANCE, 'producer': RECONSTRUCTOR['id'], 'version': VERSION,
                        'label_class_namespace': LABEL_NS, 'features_from': 'reconstructed_packet'},
            lineage={**lineage, 'turn_key': turn_key, 'parent_episode_ids': []},
            key=f'rc:{harness}:{sid}:{tid}')
        if observed['action'] not in kinds:
            draft.candidates['set'] = sorted(set(kinds) | {observed['action']}, key=ACTIONS.index)
        draft.policy_replays = {'deterministic_gate': {'choice': gate, 'replayed': gate, 'revision_match': True,
                                                       'state': PACKET_SCHEMA}}
        ep = draft.finish(host)
        E.validate(ep)
        row = {'schema': le.SCHEMA, 'table_version': le.TABLE_VERSION, 'feature_schema_sha': le.FEATURE_SCHEMA_SHA,
               'provenance': PROVENANCE, 'turn_key': turn_key, 'episode_id': ep['episode_id'], 'group': group,
               'day': day, 'harness': harness, 'cohort': cohort, 'source_cohort': src_cohort,
               'has_opportunity': True, 'gate': gate, 'features': feats, 'context': context,
               'observed': observed, 'label': label, 'verifier': ov.VERIFIER['version'],
               'reconstructor': VERSION, 'as_of': _iso(as_of)[:10], 'lineage': lineage,
               'privacy': 'features_counts_and_hashed_ids_only'}
        out.append({'training': row, 'episode': ep})
        counts['turns'] += 1
        ctx.add(t, ov.TEST_CMD)
    le.assert_private([r['training'] for r in out])
    return out


# ----------------------------------------------------------------------------- the run (watermarked, lock-aware)
SZZ_BUDGET = 1500  # SZZ fix-origin checks (git diff + blame) per repository per run


def bounded_index(budget: int = SZZ_BUDGET) -> Any:
    """The verifier's RepoIndex with a per-repository SZZ budget. A busy upstream fork can hold tens of thousands of
    'fix' commits in the window; past the budget a fix candidate is not examined (counted in ``szz_truncated``).
    That can only miss a negative ``commit_szz_fixed`` signal; the survival signal it leaves is low-confidence soft and
    never decides a label on its own, so truncation never creates a success label."""
    from . import outcome_verifier as ov

    class BoundedRepoIndex(ov.RepoIndex):
        def __init__(self) -> None:
            super().__init__()
            self.szz_calls: Counter = Counter()
            self.szz_truncated: Counter = Counter()

        def origins(self, repo, fix):  # type: ignore[override]
            key = (str(repo), fix.sha)
            if key not in self._szz and self.szz_calls[str(repo)] >= budget:
                self.szz_truncated[str(repo)] += 1
                return set()
            self.szz_calls[str(repo)] += key not in self._szz
            return super().origins(repo, fix)
    return BoundedRepoIndex()

class SharedLock:
    """flock(2) LOCK_SH on the quiet-lane lock (the same kernel lock ``flock -s`` and the loop tick take), held in
    bounded slices so an exclusive quiet window can always get in between them."""

    def __init__(self, path: str | Path | None, *, hold_s: float = 300.0, wait_s: float = 7200.0):
        self.path = Path(os.path.expanduser(str(path))) if path else None
        self.hold_s, self.wait_s = hold_s, wait_s
        self.fh = None
        self.since = 0.0
        self.waited_s = 0.0
        self.slices = 0

    def acquire(self) -> bool:
        if self.path is None:
            return True
        self.fh = open(self.path, 'a')
        t0 = time.monotonic()
        while True:
            try:
                fcntl.flock(self.fh, fcntl.LOCK_SH | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() - t0 > self.wait_s:
                    self.fh.close()
                    self.fh = None
                    return False
                time.sleep(1.0)
        self.waited_s += time.monotonic() - t0
        self.since = time.monotonic()
        self.slices += 1
        return True

    def release(self) -> None:
        if self.fh is not None:
            fcntl.flock(self.fh, fcntl.LOCK_UN)
            self.fh.close()
            self.fh = None

    def maybe_yield(self) -> bool:
        """Release and re-take the lock once the slice is used up. False when it could not be re-taken."""
        if self.path is None or time.monotonic() - self.since < self.hold_s:
            return True
        self.release()
        time.sleep(2.0)
        return self.acquire()


def _session_file(root: Path, harness: str, sid_hash: str) -> Path:
    return root / 'sessions' / harness / sid_hash[:2] / f'{sid_hash}.jsonl'


def load_watermarks(root: Path) -> dict[str, Any]:
    try:
        wm = json.loads((root / 'watermarks.json').read_text())
        return wm if wm.get('schema') == WATERMARK_SCHEMA else {'schema': WATERMARK_SCHEMA, 'sessions': {}}
    except (OSError, ValueError):
        return {'schema': WATERMARK_SCHEMA, 'sessions': {}}


def run(home: str | Path | None = None, *, agentsview: str | Path | None = None, agents: Iterable[str] = AGENT_ORDER,
        budget_s: float | None = None, as_of: float | None = None, fix_days: int = 7, lock: str | Path | None = None,
        hold_s: float = 300.0, lock_wait_s: float = 7200.0, limit: int | None = None,
        host: str | None = None, szz_budget: int = SZZ_BUDGET) -> dict[str, Any]:
    """Reconstruct every session whose watermark changed. Counts-only report; rows land under the out root."""
    from . import outcome_verifier as ov
    from .turn_readers import AgentsViewReader
    root = out_root(home)
    host = host or socket.gethostname()
    as_of = time.time() if as_of is None else as_of
    t0 = time.monotonic()
    db = av.db_path(agentsview) if agentsview and Path(agentsview).is_dir() else agentsview
    reader = AgentsViewReader(db, now=as_of)
    if not reader.available:
        return {'status': 'reader_unavailable', 'reason': reader.unavailable.reason}
    counts: Counter = Counter()
    per_harness: dict[str, Counter] = {}
    wm = load_watermarks(root)
    lk = SharedLock(lock, hold_s=hold_s, wait_s=lock_wait_s)
    log = root / 'logs' / f"run-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    status = 'complete'
    index, szz_seen = None, 0
    saved_env = {k: os.environ.get(k) for k in OFFLINE_GIT_ENV}
    os.environ.update(OFFLINE_GIT_ENV)
    try:
        sessions = list_sessions(reader.conn, agents)
        by_id = {s['id']: s for s in sessions}
        index = bounded_index(szz_budget)
        if not lk.acquire():
            return {'status': 'skipped_exclusive_window', 'lock_waited_s': round(lk.waited_s, 1)}
        done = 0
        for s in sessions:
            harness = AGENT_HARNESS[s['agent']]
            c = per_harness.setdefault(harness, Counter())
            if budget_s is not None and time.monotonic() - t0 > budget_s:
                status = 'partial_budget'
                break
            if limit is not None and done >= limit:
                status = 'partial_limit'
                break
            if not lk.maybe_yield():
                status = 'partial_lock'
                break
            sid_hash = _sha({'av_session': s['id']}, 24)
            fp = fingerprint(s, claude_transcript(s), fix_days=fix_days)
            prev = wm['sessions'].get(sid_hash)
            target = _session_file(root, harness, sid_hash)
            c['sessions_seen'] += 1
            if prev and prev.get('fp') == fp and (prev.get('rows') == 0 or target.is_file()):
                c['sessions_unchanged'] += 1
                continue
            sc: Counter = Counter()
            try:
                recs = reconstruct_session(s, root=root_of(s, by_id), reader=reader, index=index, as_of=as_of,
                                           fix_days=fix_days, host=host, counts=sc)
            except Exception as exc:  # noqa: BLE001 - one bad session is counted; the run goes on, no watermark
                c[f'session_error:{type(exc).__name__}'] += 1
                continue
            c.update({k: v for k, v in sc.items()})
            trunc = sum(index.szz_truncated.values())
            if trunc > szz_seen:
                c['sessions_with_szz_truncated'] += 1
            szz_seen = trunc
            c['sessions_reconstructed'] += 1
            if s['agent'] == 'claude':
                c['claude_transcript_found' if claude_transcript(s) else 'claude_transcript_missing'] += 1
            rows_sha = _write_lines(target, recs) if recs else None
            if not recs and target.exists():
                target.unlink()
            wm['sessions'][sid_hash] = {'fp': fp, 'harness': harness, 'agent': s['agent'], 'rows': len(recs),
                                        'rows_sha256': rows_sha, 'as_of': _iso(as_of)[:10],
                                        'subagent': is_subagent(s)}
            done += 1
            if done % 25 == 0:
                _write_json(root / 'watermarks.json', wm)
                with log.open('a') as fh:
                    fh.write(json.dumps({'t': round(time.monotonic() - t0, 1), 'sessions_done': done,
                                         'harness': harness, 'turns': sum(x['turns'] for x in per_harness.values())},
                                        sort_keys=True) + '\n')
    finally:
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        lk.release()
        reader.close()
        _write_json(root / 'watermarks.json', wm)
    report = {'schema': 'z0int.reconstructed_run.v0', 'status': status, 'host': host,
              'reconstructor': RECONSTRUCTOR, 'as_of': _iso(as_of), 'fix_days': fix_days,
              'runtime_s': round(time.monotonic() - t0, 1),
              'network': 'none: GitHub disabled, git offline (no lazy fetch, file protocol only)',
              'lock': {'path': str(lk.path) if lk.path else None, 'mode': 'flock(2) LOCK_SH, sliced',
                       'slices': lk.slices, 'waited_s': round(lk.waited_s, 1), 'hold_s': hold_s},
              'per_harness': {h: dict(sorted(c.items())) for h, c in per_harness.items()},
              'szz': {'budget_per_repo': szz_budget,
                      'repos_truncated': len(index.szz_truncated) if index is not None else 0,
                      'candidates_skipped': sum(index.szz_truncated.values()) if index is not None else 0},
              'privacy': 'counts only'}
    with log.open('a') as fh:
        fh.write(json.dumps(report, sort_keys=True) + '\n')
    return report


# ----------------------------------------------------------------------------- build: tables, episodes, receipt
def live_groups(home: Path) -> dict[str, set[str]]:
    """harness -> session groups that have live opportunity records on this host (read-only). Reconstructed rows of
    those groups share lineage with the live validation pool and are quarantined from reconstructed splits."""
    from . import harness_capture as hc
    out: dict[str, set[str]] = {}
    for h in hc.HARNESSES:
        rows = hc._read_jsonl(hc.state_dir(h, home) / 'opportunities.jsonl')
        out[h] = {_sha({'session': r.get('session_id')}, 12) for r in rows
                  if r.get('schema') == hc.schema(h, 'opportunity_record') and r.get('session_id')}
    return out


def iter_records(root: Path) -> Iterator[dict[str, Any]]:
    wm = load_watermarks(root)
    for sid_hash, w in sorted(wm['sessions'].items(), key=lambda kv: (AGENT_ORDER.index(kv[1]['agent']), kv[0])):
        if not w.get('rows'):
            continue
        p = _session_file(root, w['harness'], sid_hash)
        try:
            body = p.read_bytes()
        except OSError:
            continue
        if w.get('rows_sha256') and hashlib.sha256(body).hexdigest() != w['rows_sha256']:
            raise ValueError(f'session rows {p.name} do not match their watermark (edited outside the run?)')
        for line in body.decode().splitlines():
            if line.strip():
                yield json.loads(line)


def _code() -> dict[str, Any]:
    import subprocess
    here = Path(__file__).resolve().parent
    env = dict(os.environ, GIT_OPTIONAL_LOCKS='0')
    try:
        sha = subprocess.run(['git', '-C', str(here), 'rev-parse', 'HEAD'], capture_output=True, text=True, env=env,
                             timeout=10).stdout.strip() or None
        dirty = bool(subprocess.run(['git', '-C', str(here), 'status', '--porcelain', '--', '.'], capture_output=True,
                                    text=True, env=env, timeout=10).stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        sha, dirty = None, None
    return {'git_sha': sha, 'dirty': dirty, 'reconstructor': VERSION}


def stratum_counts(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    from .loop_tick import analysis_rows, sufficiency
    ana = analysis_rows(rows)
    lab = [r for r in rows if r['label']['resolved']]
    return {'turns': len(rows), 'labelled': len(lab), 'labelled_share': round(len(lab) / len(rows), 4) if rows else None,
            'states': le._count(r['label']['state'] for r in rows),
            'observed_action': le._count(r['observed']['action'] for r in rows),
            'gate': le._count(r['gate'] for r in rows), 'groups': len({r['group'] for r in rows}),
            'analysis_rows': len(ana), 'sufficiency': sufficiency(ana),
            'days': {'first': min((r['day'] for r in rows if r['day']), default=None),
                     'last': max((r['day'] for r in rows if r['day']), default=None)}}


def build(home: str | Path | None = None, *, agentsview: str | Path | None = None,
          host: str | None = None) -> dict[str, Any]:
    from . import episodes as E
    from . import paths
    base = Path(home).expanduser().resolve() if home else paths.home()
    root = out_root(base)
    host = host or socket.gethostname()
    live = live_groups(base)
    tables: dict[tuple[str, str], list[dict[str, Any]]] = {}
    eps: list[dict[str, Any]] = []
    invalid: Counter = Counter()
    for rec in iter_records(root):
        row, ep = rec['training'], rec['episode']
        flag = row['group'] in live.get(row['harness'], set())
        row['lineage']['live_capture_group'] = ep['lineage']['live_capture_group'] = flag
        try:
            E.validate(ep)
        except E.EpisodeInvalid as exc:
            invalid[str(exc).split(':')[0][:60]] += 1
            continue
        tables.setdefault((row['harness'], row['cohort']), []).append(row)
        eps.append(ep)
    built = [le.Table(h, c, sorted(rows, key=lambda r: (r['day'] or '', r['turn_key'])))
             for (h, c), rows in sorted(tables.items())]
    for t in built:
        le.assert_private(t.rows)
    eps, dd = E.dedup(eps)
    ep_body = ''.join(json.dumps(e, sort_keys=True) + '\n' for e in eps)
    wm = load_watermarks(root)
    sources = {'agentsview': _agentsview_source(agentsview),
               'sessions': {'count': len(wm['sessions']),
                            'fingerprints_sha256': _sha(sorted((k, v['fp']) for k, v in wm['sessions'].items()), 64),
                            'rows_sha256': _sha(sorted((k, v.get('rows_sha256')) for k, v in wm['sessions'].items()),
                                                64)},
               'live_state_groups': {h: len(g) for h, g in live.items() if g}}
    code = _code()
    stable = dict(sources, agentsview={k: v for k, v in sources['agentsview'].items() if k != 'staleness_hours'})
    build_id = _sha({'sources': stable, 'code': code, 'episodes': hashlib.sha256(ep_body.encode()).hexdigest()}, 16)
    d = root / 'builds' / build_id
    index = {}
    for t in built:
        body = le._body(t.rows)
        man = le._table_manifest(t, body, generated_at=time.time(), host=host, provenance=PROVENANCE,
                                 reconstructor=VERSION)
        (d / 'tables' / t.harness).mkdir(parents=True, exist_ok=True)
        (d / 'tables' / t.harness / f'{t.cohort}.jsonl').write_text(body)
        _write_json(d / 'tables' / t.harness / f'{t.cohort}.manifest.json', man)
        index[f'{t.harness}/{t.cohort}'] = {**stratum_counts(list(t.rows)), 'rows_sha256': man['rows_sha256'],
                                            'live_capture_group_rows': sum(1 for r in t.rows
                                                                           if r['lineage']['live_capture_group'])}
    d.mkdir(parents=True, exist_ok=True)
    (d / 'episodes.jsonl').write_text(ep_body)
    receipt = {'schema': RECEIPT_SCHEMA, 'build_id': build_id, 'host': host,
               'built_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'code': code,
               'provenance': PROVENANCE, 'label_class_namespace': LABEL_NS, 'episode_schema': E.SCHEMA,
               'row_schema': le.SCHEMA, 'table_version': le.TABLE_VERSION, 'feature_schema_sha': le.FEATURE_SCHEMA_SHA,
               'sources': sources, 'episodes_sha256': hashlib.sha256(ep_body.encode()).hexdigest(),
               'episodes': len(eps), 'dedup': dd, 'invalid': dict(invalid), 'strata': index,
               'git_state': GIT_STATE, 'network': 'none (GitHub lookups disabled; no model calls)',
               'pooling': 'never with observed rows: own cohorts, own label-class namespace, own split and learner run',
               'privacy': 'no prompt/response/command/path text; content and ids as sha256 prefixes'}
    _write_json(d / 'receipt.json', receipt)
    (root / 'LATEST_BUILD').write_text(build_id + '\n')
    return receipt


def _agentsview_source(agentsview: str | Path | None) -> dict[str, Any]:
    p = av.db_path(agentsview) if agentsview and Path(agentsview).is_dir() else (Path(agentsview) if agentsview
                                                                                  else av.db_path())
    conn = av.connect(p)
    if isinstance(conn, av.Unavailable):
        return {'available': False, 'reason': conn.reason}
    try:
        version = conn.execute('pragma user_version').fetchone()[0]
        by_agent = dict(conn.execute('select agent, count(*) from sessions where deleted_at is null group by agent')
                        .fetchall())
        return {'available': True, 'user_version': version, 'staleness_hours': av.staleness_hours(conn),
                'sessions_by_agent': {str(k): int(v) for k, v in sorted(by_agent.items())},
                'unmapped_agents': sorted(str(a) for a in by_agent if a not in AGENT_HARNESS),
                'db': str(p).replace(str(Path.home()), '~')}
    finally:
        conn.close()


# ----------------------------------------------------------------------------- split + learn
DEFAULT_EVAL = ('2026-10-07', '2026-10-15')  # [start, end): the owner's 2026-10-07..2026-10-14, both days included


def _latest(root: Path, kind: str, given: str | None) -> str:
    if given:
        return given
    p = root / f'LATEST_{kind.upper()}'
    if not p.is_file():
        raise ValueError(f'no {kind} yet')
    return p.read_text().strip()


def split(home: str | Path | None = None, *, build_id: str | None = None, eval_start: str | None = None,
          eval_end: str | None = None, dev_days: int = 7) -> dict[str, Any]:
    """The episode_split rules on reconstructed episodes only, then the split's ids projected onto each stratum's
    training rows (train / dev / eval / recent tables). A group with live capture on this host is removed from train
    and dev (it shares lineage with the LIVE validation pool); it may stay in the held-out eval/recent windows, which
    never feed fitting."""
    from .episode_split import resolve_lock, split as do_split
    root = out_root(home)
    bid = _latest(root, 'build', build_id)
    d = root / 'builds' / bid
    lock = resolve_lock(root / 'locks' / 'eval_window.json', eval_start, eval_end)
    eps = [json.loads(x) for x in (d / 'episodes.jsonl').read_text().splitlines() if x.strip()]
    ids, man = do_split(eps, eval_start=lock['eval_start'], eval_end=lock['eval_end'], dev_days=dev_days,
                        provenance=PROVENANCE)
    live = {e['episode_id'] for e in eps if e['lineage'].get('live_capture_group')}
    for s in ('train', 'dev'):
        n = sum(1 for i in ids[s] if i in live)
        ids[s] = [i for i in ids[s] if i not in live]
        man['removed'][f'live_capture_group:{s}'] = n
        man['counts'][s]['live_capture_group_removed'] = n
    man['ids_sha256'] = {s: hashlib.sha256('\n'.join(xs).encode()).hexdigest() for s, xs in ids.items()}
    split_id = _sha({'build': bid, 'lock': [lock['eval_start'], lock['eval_end']], 'dev_days': dev_days,
                     'ids': man['ids_sha256']}, 16)
    sd = root / 'splits' / split_id
    where = {eid: s for s, xs in ids.items() for eid in xs}
    strata: dict[str, Any] = {}
    for mp in sorted((d / 'tables').glob('*/*.manifest.json')):
        harness, cohort = mp.parent.name, mp.name.replace('.manifest.json', '')
        rows = le._read_jsonl(mp.with_name(f'{cohort}.jsonl'))
        parts: dict[str, list] = {s: [] for s in ('train', 'dev', 'eval', 'recent')}
        for r in rows:
            s = where.get(r['episode_id'])
            if s:
                parts[s].append(r)
        strata[f'{harness}/{cohort}'] = {}
        for s, rs in parts.items():
            t = le.Table(harness, cohort, rs)
            body = le._body(t.rows)
            (sd / harness).mkdir(parents=True, exist_ok=True)
            (sd / harness / f'{cohort}.{s}.jsonl').write_text(body)
            m = le._table_manifest(t, body, generated_at=time.time(), provenance=PROVENANCE, split=s,
                                   split_id=split_id, build_id=bid)
            _write_json(sd / harness / f'{cohort}.{s}.manifest.json', m)
            strata[f'{harness}/{cohort}'][s] = stratum_counts(rs)
    man.update(split_id=split_id, build_id=bid, dev_days=dev_days, lock=lock, strata=strata, provenance=PROVENANCE,
               created_at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
    _write_json(sd / 'manifest.json', man)
    (root / 'LATEST_SPLIT').write_text(split_id + '\n')
    return man


def learn(home: str | Path | None = None, *, split_id: str | None = None, live_tables: str | Path | None = None,
          timeout_s: float = 7200.0) -> dict[str, Any]:
    """The separate learner run (evolution-lab prereg verified-loop-reconstructed-v0): per stratum, the pinned
    ``evolution_lab.verified_loop_reconstructed`` over the train table, the held-out eval-window table and the LIVE
    observed tables of the same harness. Config: ``<out root>/config.json`` {learner_path, learner_sha,
    learner_python}; the live loop's own ``config/loop.json`` is never read or changed. Nothing is promoted."""
    import subprocess
    from .loop_tick import learner_env, learner_status
    root = out_root(home)
    sid = _latest(root, 'split', split_id)
    sd = root / 'splits' / sid
    try:
        cfg = json.loads((root / 'config.json').read_text())
    except (OSError, ValueError):
        cfg = {}
    python = cfg.get('learner_python') or sys.executable
    status = learner_status(cfg, python)
    out: dict[str, Any] = {'split_id': sid, 'learner': status, 'results': {}}
    if status['status'] != 'ok':
        return out
    live_dir = Path(os.path.expanduser(str(live_tables))) if live_tables else None
    for train in sorted(sd.glob('*/*.train.jsonl')):
        harness, cohort = train.parent.name, train.name.split('.')[0]
        res = root / 'results' / sid / harness / f'{cohort}.json'
        res.parent.mkdir(parents=True, exist_ok=True)
        cmd = [python, '-m', 'evolution_lab.verified_loop_reconstructed', '--train', str(train),
               '--eval', str(train.with_name(f'{cohort}.eval.jsonl')), '--out', str(res),
               '--md', str(res.with_suffix('.md'))]
        for lt in sorted((live_dir / harness).glob('*.jsonl')) if live_dir and (live_dir / harness).is_dir() else []:
            if not lt.name.endswith('.shadow.jsonl') and lt.stem != 'legacy':
                cmd += ['--live', str(lt)]
        t0 = time.monotonic()
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s, env=learner_env(cfg))
            r = json.loads(res.read_text()) if p.returncode == 0 else None
        except (subprocess.TimeoutExpired, OSError, ValueError):
            r, p = None, None
        out['results'][f'{harness}/{cohort}'] = {
            'verdict': (r or {}).get('verdict') or 'learner_error', 'seconds': round(time.monotonic() - t0, 1),
            'result': os.path.relpath(res, root) if r else None,
            'returncode': None if p is None else p.returncode}
    _write_json(root / 'results' / sid / 'summary.json', out)
    return out


# ----------------------------------------------------------------------------- CLI
def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog='z0int reconstruct', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--home', type=Path, default=None, help='Z0INT_HOME (default $Z0INT_HOME or ~/.z0int)')
    ap.add_argument('--agentsview', default=None, help='AgentsView data dir or sessions.db (default ~/.agentsview)')
    sub = ap.add_subparsers(dest='cmd', required=True)
    r = sub.add_parser('run', help='reconstruct sessions whose watermark changed (claude first)')
    r.add_argument('--agents', default=','.join(AGENT_ORDER))
    r.add_argument('--budget-s', type=float, default=None)
    r.add_argument('--limit', type=int, default=None, help='at most this many sessions recomputed')
    r.add_argument('--fix-days', type=int, default=7)
    r.add_argument('--as-of', default=None, help='outcome horizon (ISO date/epoch; default now)')
    r.add_argument('--lock', default=DEFAULT_LOCK, help="quiet-lane lock taken LOCK_SH in slices ('' = none)")
    r.add_argument('--hold-s', type=float, default=300.0)
    r.add_argument('--lock-wait-s', type=float, default=7200.0)
    sub.add_parser('build', help='tables + episodes + dataset receipt from the reconstructed sessions')
    s = sub.add_parser('split', help='temporal + lineage split, reconstructed provenance only')
    s.add_argument('--build', default=None)
    s.add_argument('--eval-start', default=None)
    s.add_argument('--eval-end', default=None, help='exclusive')
    s.add_argument('--dev-days', type=int, default=7)
    lp = sub.add_parser('learn', help='separate learner run per reconstructed stratum (pinned evolution-lab)')
    lp.add_argument('--split', default=None)
    lp.add_argument('--live-tables', default='~/.z0int/loop/tables', help='live observed tables (read-only)')
    lp.add_argument('--timeout-s', type=float, default=7200.0)
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if args.cmd == 'run':
            from .outcome_verifier import parse_since
            rep = run(args.home, agentsview=args.agentsview, agents=args.agents.split(','), budget_s=args.budget_s,
                      as_of=parse_since(args.as_of) if args.as_of else None, fix_days=args.fix_days,
                      lock=args.lock or None, hold_s=args.hold_s, lock_wait_s=args.lock_wait_s, limit=args.limit)
        elif args.cmd == 'build':
            rep = build(args.home, agentsview=args.agentsview)
            rep = {k: rep[k] for k in ('build_id', 'episodes', 'dedup', 'invalid', 'strata')}
        elif args.cmd == 'split':
            rep = split(args.home, build_id=args.build, eval_start=args.eval_start or DEFAULT_EVAL[0],
                        eval_end=args.eval_end or DEFAULT_EVAL[1], dev_days=args.dev_days)
            rep = {k: rep[k] for k in ('split_id', 'locked_eval_window', 'dev_window', 'removed', 'residual_overlap',
                                       'strata')}
        else:
            rep = learn(args.home, split_id=args.split, live_tables=args.live_tables, timeout_s=args.timeout_s)
    except ValueError as exc:
        print(f'refused: {exc}')
        return 2
    print(json.dumps(rep, indent=1, sort_keys=True, default=str))
    return 0


if __name__ == '__main__':
    raise SystemExit(_main())
