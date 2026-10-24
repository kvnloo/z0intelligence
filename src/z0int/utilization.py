"""z0 utilization: how often z0's cognition actually changes what happens, per harness and cohort.

North-star metric (definition and justification: docs/utilization.md):

    z0 utilization = U / N
    N = frontier-bound work units (one prompt -> >= 1 model call) in the interactive + agent
        cohorts, across every harness the user runs, whether or not z0 is installed there
    U = units where z0 displaced work (a completed offload whose result reached the parent),
        shortened it (ObservationPack withheld output from the context, net of recalls), or
        verifiably improved the decision (an enforced z0 decision whose outcome a verifier passed)

Two wider funnels are reported beside it and never substituted for it:
``coverage`` (z0 saw the unit at all) >= ``influence`` (z0 changed the model input) >= utilization.

Sources (count-only: ids, lengths, timestamps; no prompt, tool or model text is retained):

* Claude Code transcripts ``~/.claude/projects`` (turns, z0 hook attachments, ObservationPack
  markers, route_worker calls) via ``claude_code_tokenomics.parse_transcript``
* ``~/.z0int/state/claude-code/{opportunities,outcomes}.jsonl`` (shadow DecisionOpportunities)
  and ``<session>.json`` Stop-hook cursors (message ids the live hook billed)
* ``~/.z0int/receipts/decisions.jsonl`` (automatic dispatch + worker receipts)
* Hermes ``~/.hermes/state.db`` (sessions + message role/timestamp only), OMP
  ``~/.omp/agent/sessions`` and Codex ``~/.codex/sessions`` (row types + timestamps only):
  volume z0 does not see yet. ``~/.z0int/state/hermes`` / bridge receipts when installed.

  z0int utilization [--range 7d] [--json]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sqlite3
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from . import paths

SCHEMA = 'z0int.utilization.report.v0'
DEFINITION_VERSION = 'z0_utilization_v0'
COHORTS = ('interactive', 'agent', 'eval')
HEADLINE_COHORTS = ('interactive', 'agent')
HARNESSES = ('claude-code', 'hermes', 'omp', 'codex')
# Scratch cwds are probes/eval arms, not the user's work (extends engagement.EVAL_CWD).
SCRATCH_CWD = re.compile(r'^/tmp(/|$)|/\.cache/')
HERMES_AGENT_SOURCES = {'cron', 'subagent', 'curator', 'kanban', 'batch', 'raft', 'delegate', 'a2a'}
Z0_TAG = '<z0-'
COUNT_KEYS = (
    'sessions', 'units', 'units_root', 'units_subagent', 'frontier_api_calls', 'frontier_prompt_tokens',
    'frontier_output_tokens', 'seen_units', 'input_changed_units', 'context_carried_units', 'context_injections',
    'context_injected_tokens_est', 'prompt_context_injections', 'obspack_units', 'obspack_compactions',
    'obspack_tokens_withheld_est', 'obspack_recall_tokens_est', 'worker_result_units', 'decisions_shadow',
    'decisions_enforced', 'decisions_linked_to_outcome', 'decisions_gate_agrees_with_observed',
    'offloads_attempted', 'offloads_completed', 'offloads_verified', 'offloads_parent_only',
    'frontier_tokens_displaced_est', 'utilized_units', 'utilized_displaced', 'utilized_shortened',
    'utilized_improved')


def est_tokens(chars: int) -> int:
    return int(math.ceil(max(0, chars) / 4))


def _ts(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
        except ValueError:
            return None
    return None


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _jsonl(path: Path) -> Iterable[dict[str, Any]]:
    try:
        fh = path.open(encoding='utf-8', errors='replace')
    except OSError:
        return
    with fh:
        for line in fh:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                yield row


# --------------------------------------------------------------------------- cohorts

def cohort_for(harness: str, *, cwd: str | None = None, entrypoint: str | None = None,
               source: str | None = None, parent: bool = False, originator: str | None = None) -> str:
    """interactive (a human at the harness), agent (automation on real work), eval (probes/arms).

    Claude Code matches ``claude_code_engagement.cohort`` (its 'harness' cohort is 'eval' here),
    widened so any scratch cwd (``/tmp``, ``~/.cache``) is eval for every harness.
    """
    from .claude_code_engagement import EVAL_CWD
    if cwd and (EVAL_CWD.search(cwd) or SCRATCH_CWD.search(cwd)):
        return 'eval'
    if harness == 'claude-code':
        return 'interactive' if entrypoint == 'cli' else 'agent'
    if harness == 'hermes':
        return 'agent' if parent or (source or '').lower() in HERMES_AGENT_SOURCES else 'interactive'
    if harness == 'codex':
        return 'agent' if 'exec' in (originator or '').lower() else 'interactive'
    if harness == 'omp':
        return 'agent' if parent else 'interactive'
    return 'agent'


class Table:
    """harness x cohort counters plus per-row categorical breakdowns."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], Counter] = defaultdict(Counter)
        self.cats: dict[tuple[str, str], dict[str, Counter]] = defaultdict(lambda: defaultdict(Counter))

    def add(self, harness: str, cohort: str, key: str, n: int | float = 1) -> None:
        if n:
            self.rows[(harness, cohort)][key] += n

    def cat(self, harness: str, cohort: str, family: str, key: str, n: int = 1) -> None:
        self.cats[(harness, cohort)][family][key] += n


# --------------------------------------------------------------------------- Claude Code

def _z0_attachments(path: Path) -> dict[str, Any]:
    """Session facts and z0 hook attachments of one transcript; text never kept, only lengths."""
    out: dict[str, Any] = {'cwd': None, 'entrypoint': None, 'injections': [], 'hook_ts': []}
    try:
        fh = open(path, 'rb')
    except OSError:
        return out
    with fh:
        for line in fh:
            first = out['cwd'] is None and b'"cwd"' in line
            if not first and not (b'"attachment"' in line and b'z0' in line):
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if not isinstance(d, dict):
                continue
            if out['cwd'] is None and d.get('cwd'):
                out['cwd'], out['entrypoint'] = d['cwd'], d.get('entrypoint')
            if d.get('type') != 'attachment':
                continue
            a = d.get('attachment') or {}
            ts = _ts(d.get('timestamp'))
            if 'z0int' in str(a.get('command') or ''):
                out['hook_ts'].append(ts)
            if a.get('type') == 'hook_additional_context':
                content = a.get('content')
                text = ''.join(c for c in content if isinstance(c, str)) if isinstance(content, list) else \
                    content if isinstance(content, str) else ''
                if Z0_TAG in text:
                    out['injections'].append({'ts': ts, 'event': a.get('hookEvent'), 'chars': len(text)})
                    out['hook_ts'].append(ts)
    return out


def _billed_ids(state_dir: Path) -> set[str]:
    ids: set[str] = set()
    for p in state_dir.glob('*.json'):
        try:
            data = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            ids.update(i for i in data.get('message_ids') or [] if isinstance(i, str))
    return ids


def _carried(turn_start: float | None, injections: list[dict[str, Any]], compacts: list[float]) -> bool:
    """A context injection is in this turn's model input if it landed before the turn started
    and no compact boundary wiped the window in between (SessionStart:compact re-injects)."""
    if turn_start is None:
        return False
    for inj in injections:
        t = inj['ts']
        if t is None or t > turn_start:
            continue
        # SessionStart:compact output is stamped around its own boundary: allow 5 s of skew.
        if not any(t + 5 < c <= turn_start for c in compacts):
            return True
    return False


def scan_claude_code(table: Table, since: float, *, projects: Path | None = None, z0_home: Path | None = None,
                     opportunities: dict[str, dict[str, Any]] | None = None,
                     outcomes: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    from . import claude_code_tokenomics as cct
    projects = projects or cct.projects_dir()
    layout = paths.ensure_layout(z0_home)
    billed = _billed_ids(layout['state'] / 'claude-code')
    opp_traces = set(opportunities or {})
    opp_first: dict[str, float] = {}
    for rec in (opportunities or {}).values():
        built = _ts(((rec.get('opportunity') or {}).get('provenance') or {}).get('built_at'))
        sid = rec.get('session_id')
        if built is not None and sid:
            opp_first[sid] = min(built, opp_first.get(sid, built))
    live_since: dict[str, float] = {}
    session_cohort: dict[str, str] = {}
    worker_calls: dict[str, dict[str, Any]] = {}
    files = cct.discover(since, projects)
    root_facts: dict[str, dict[str, Any]] = {}
    for session_id, role, path in files:
        facts = _z0_attachments(path)
        if role == 'root':
            root_facts[session_id] = facts
            cohort = cohort_for('claude-code', cwd=facts['cwd'], entrypoint=facts['entrypoint'])
            session_cohort[session_id] = cohort
        else:
            cohort = session_cohort.get(session_id) or cohort_for('claude-code', cwd=facts['cwd'],
                                                                   entrypoint=facts['entrypoint'])
        parsed = cct.parse_transcript(path)
        records = parsed['records']
        # z0 was live in this session from its earliest timestamped z0 evidence: a z0 hook
        # attachment, an ObservationPack marker, a route_worker call, or a shadow opportunity
        # built by the UserPromptSubmit hook. A subagent inherits its root's.
        evidence = [t for t in facts['hook_ts'] if t]
        evidence += [r['ts'] for r in records if r['kind'] in ('obs', 'worker_call') and r.get('ts')]
        if session_id in opp_first:
            evidence.append(opp_first[session_id])
        if session_id in live_since:
            evidence.append(live_since[session_id])
        first_live = min(evidence) if evidence else None
        if role == 'root' and first_live is not None:
            live_since[session_id] = first_live
        compacts = [r['ts'] for r in records if r['kind'] == 'compact' and r.get('ts')]
        # SessionStart reaches the root only; a subagent carries z0 context only from SubagentStart.
        injections = facts['injections']
        counted_session = False
        for turn in cct.split_turns(records):
            ids = {r['id'] for r in turn if r['kind'] == 'assistant'}
            if not ids:
                continue
            stamps = [r['ts'] for r in turn if r.get('ts')]
            end = max(stamps) if stamps else None
            if end is None or end < since:
                continue
            start = min(stamps)
            h = 'claude-code'
            if not counted_session and role == 'root':
                table.add(h, cohort, 'sessions')
                counted_session = True
            usage, _, _ = cct._usage_of([r for r in turn if r['kind'] == 'assistant'])
            table.add(h, cohort, 'units')
            table.add(h, cohort, f'units_{role}')
            table.add(h, cohort, 'frontier_api_calls', len(ids))
            table.add(h, cohort, 'frontier_prompt_tokens', usage['input_tokens'] + usage['cache_read_input_tokens']
                      + usage['cache_creation_input_tokens'])
            table.add(h, cohort, 'frontier_output_tokens', usage['output_tokens'])
            prompt = next((r for r in turn if r['kind'] == 'prompt'), None)
            obs = [r for r in turn if r['kind'] == 'obs']
            recalls = [r for r in turn if r['kind'] == 'recall']
            calls = [r for r in turn if r['kind'] == 'worker_call']
            results = [r for r in turn if r['kind'] == 'worker_result']
            here = [i for i in injections if i['ts'] is not None and start <= i['ts'] <= end]
            prompt_ctx = [i for i in here if i['event'] == 'UserPromptSubmit']
            carried = _carried(start, [i for i in injections if i['event'] in ('SessionStart', 'SubagentStart')],
                               compacts)
            # --- seen: z0 was live for this unit (could have acted on it), evidence-based lower bound.
            # Stop-hook billing alone is not enough: its first run back-fills turns that predate z0.
            basis = None
            if prompt and prompt.get('prompt_id') in opp_traces:
                basis = 'opportunity_record'
            elif obs or calls or here or carried:
                basis = 'z0_marker_in_turn'
            elif first_live is not None and end >= first_live:
                basis = 'z0_live_earlier_in_session'
            if basis:
                table.add(h, cohort, 'seen_units')
                table.cat(h, cohort, 'seen_basis', basis)
            elif ids & billed:
                table.cat(h, cohort, 'unseen_but_billed_after_the_fact', 'stop_hook_backfill')
            # --- input changed
            withheld = sum(max(0, r['original_chars'] - r['shown_chars']) for r in obs)
            back = sum(r['chars'] for r in recalls)
            displaced = [r for r in results if r.get('ok') is True and (r.get('output_chars') or 0) > 0]
            parent_only = [r for r in results if r.get('parsed') and not displaced and r.get('ok') is not False]
            if carried:
                table.add(h, cohort, 'context_carried_units')
            for i in here:
                table.add(h, cohort, 'context_injections')
                table.add(h, cohort, 'context_injected_tokens_est', est_tokens(i['chars']))
                table.cat(h, cohort, 'injections_by_event', str(i['event']))
            table.add(h, cohort, 'prompt_context_injections', len(prompt_ctx))
            # An enforced decision = z0 routing context actually delivered on this prompt
            # (UserPromptSubmit with shadow off). "Improved" needs a verifier-passed outcome.
            improved = False
            if prompt_ctx:
                table.add(h, cohort, 'decisions_enforced')
                out = (outcomes or {}).get((prompt or {}).get('prompt_id'))
                improved = bool(out and out.get('verified') is True)
            if obs:
                table.add(h, cohort, 'obspack_units')
                table.add(h, cohort, 'obspack_compactions', len(obs))
                table.add(h, cohort, 'obspack_tokens_withheld_est', est_tokens(withheld))
                table.add(h, cohort, 'obspack_recall_tokens_est', est_tokens(back))
            if results:
                table.add(h, cohort, 'worker_result_units')
            table.add(h, cohort, 'offloads_attempted', len(calls))
            table.add(h, cohort, 'offloads_completed', len(displaced))
            table.add(h, cohort, 'offloads_parent_only', len(parent_only))
            changed = carried or bool(obs) or bool(results) or bool(prompt_ctx)
            if changed:
                table.add(h, cohort, 'input_changed_units')
            # --- utilization (headline numerator)
            shortened = withheld - back > 0
            if displaced:
                table.add(h, cohort, 'utilized_displaced')
            if shortened:
                table.add(h, cohort, 'utilized_shortened')
            if improved:
                table.add(h, cohort, 'utilized_improved')
            if displaced or shortened or improved:
                table.add(h, cohort, 'utilized_units')
            for r in calls:
                worker_calls[r['tool_use_id']] = {**r, 'cohort': cohort}
            for r in results:
                call = worker_calls.get(r.get('tool_use_id'))
                if call and r.get('subagent_id'):
                    worker_calls[r['subagent_id']] = call
    return {'files': len(files), 'session_cohort': session_cohort, 'worker_calls': worker_calls}


def turn_outcomes(z0_home: Path | None = None) -> dict[str, dict[str, Any]]:
    base = paths.ensure_layout(z0_home)['state'] / 'claude-code'
    return {r.get('trace_id'): r for r in _jsonl(base / 'outcomes.jsonl') if r.get('trace_id')}


def scan_decisions(table: Table, since: float, session_cohort: dict[str, str], *,
                   z0_home: Path | None = None) -> dict[str, Any]:
    """Shadow DecisionOpportunity records vs observed outcomes (Claude Code).

    A DecisionOpportunity gate is never acted on by any harness today, so every record is a
    shadow decision regardless of the ``shadow`` config (which gates prompt-context routing,
    counted per turn as ``decisions_enforced`` in ``scan_claude_code``)."""
    outcomes = turn_outcomes(z0_home)
    base = paths.ensure_layout(z0_home)['state'] / 'claude-code'
    from .claude_code import is_harness_message
    expect = {'ACT': 'answered', 'ASK': 'asked', 'ESCALATE': 'asked', 'ABSTAIN': 'asked', 'OBSERVE': 'answered'}
    n = 0
    for rec in _jsonl(base / 'opportunities.jsonl'):
        opp = rec.get('opportunity') or {}
        built = _ts((opp.get('provenance') or {}).get('built_at'))
        if built is not None and built < since:
            continue
        if is_harness_message((opp.get('intent') or {}).get('request')):
            continue
        cohort = session_cohort.get(rec.get('session_id'), 'unattributed')
        n += 1
        table.add('claude-code', cohort, 'decisions_shadow')
        table.cat('claude-code', cohort, 'decisions_by_gate', str(rec.get('gate')))
        out = outcomes.get((opp.get('trace') or {}).get('trace_id'))
        if out:
            table.add('claude-code', cohort, 'decisions_linked_to_outcome')
            observed = 'asked' if out.get('asked_user') else 'answered'
            table.add('claude-code', cohort, 'decisions_gate_agrees_with_observed',
                      int(expect.get(str(rec.get('gate'))) == observed))
    return {'opportunities_in_window': n}


def opportunity_traces(z0_home: Path | None = None) -> dict[str, dict[str, Any]]:
    base = paths.ensure_layout(z0_home)['state'] / 'claude-code'
    out = {}
    for rec in _jsonl(base / 'opportunities.jsonl'):
        tid = ((rec.get('opportunity') or {}).get('trace') or {}).get('trace_id')
        if tid:
            out[tid] = rec
    return out


def scan_receipts(table: Table, since: float, session_cohort: dict[str, str], worker_calls: dict[str, Any], *,
                  z0_home: Path | None = None) -> dict[str, Any]:
    """Automatic dispatch decisions and physical worker attempts from the receipt spine."""
    from .claude_code_tokenomics import displacement_estimate
    path = paths.ensure_layout(z0_home)['receipts'] / 'decisions.jsonl'
    stats: Counter = Counter()
    for r in _jsonl(path):
        if float(r.get('ts') or 0) < since:
            continue
        e = r.get('extra') if isinstance(r.get('extra'), dict) else {}
        harness = e.get('harness') or 'unknown'
        status = e.get('status')
        cap = r.get('capability_id')
        if cap == 'intelligence.dispatch' and status == 'completed' and e.get('automatic'):
            result = e.get('result') if isinstance(e.get('result'), dict) else {}
            cohort = session_cohort.get(e.get('integration_instance') or r.get('session_id'), 'unattributed')
            executed = bool(result.get('executed'))
            table.add(harness, cohort, 'decisions_enforced' if executed else 'decisions_shadow')
            table.cat(harness, cohort, 'dispatch_routes', str(e.get('chosen_kind') or 'unknown'))
            stats['automatic_dispatch'] += 1
            continue
        if not (r.get('provider') and e.get('physical_call_attempted')):
            continue
        if status not in ('completed', 'failed', 'incomplete', 'completed_unmetered_or_unidentified'):
            continue
        call = worker_calls.get(e.get('subagent_id'))
        if call:
            cohort, basis = call['cohort'], 'transcript_linked'
        else:  # programmatic callers (shadow probes, dogfood, research) are not user work units
            cohort, basis = 'eval', 'programmatic_unlinked'
        stats[basis] += 1
        table.cat(harness, cohort, 'worker_receipts', basis)
        if not call:  # transcript-linked attempts were already counted from the transcript
            table.add(harness, cohort, 'offloads_attempted')
            if status == 'completed':
                table.add(harness, cohort, 'offloads_completed')
        outcome = r.get('outcome') if isinstance(r.get('outcome'), dict) else {}
        if status == 'completed' and (outcome.get('verified') is True or outcome.get('verifier_passed') is True):
            table.add(harness, cohort, 'offloads_verified')
        d = displacement_estimate(r, call)
        if d.get('counterfactual'):
            table.add(harness, cohort, 'frontier_tokens_displaced_est', int(d['frontier_tokens_displaced_est']))
            table.cat(harness, cohort, 'displacement_counterfactual', d['counterfactual'])
    return dict(stats)


# --------------------------------------------------------------------------- other harnesses (counts only)

def _units_from_roles(roles: Iterable[str]) -> tuple[int, int]:
    """(units, model_calls): a unit is a user message followed by >= 1 model response."""
    units = calls = 0
    pending = False
    for role in roles:
        if role == 'user':
            pending = True
        elif role == 'assistant':
            calls += 1
            if pending:
                units += 1
                pending = False
    return units, calls


def scan_hermes(table: Table, since: float, *, home: Path | None = None, z0_home: Path | None = None) -> dict[str, Any]:
    home = home or Path(os.environ.get('HERMES_HOME', Path.home() / '.hermes'))
    db = home / 'state.db'
    meta: dict[str, Any] = {'source': str(db), 'available': db.exists(), 'method': 'state.db sessions + message roles/timestamps'}
    if not db.exists():
        return meta
    try:
        con = sqlite3.connect(f'file:{db}?mode=ro', uri=True, timeout=5)
        sessions = con.execute(
            'select id, source, parent_session_id is not null, cwd, api_call_count from sessions '
            'where max(coalesce(last_activity_at, 0), coalesce(ended_at, 0), started_at) >= ?', (since,)).fetchall()
        for sid, source, parent, cwd, api_calls in sessions:
            cohort = cohort_for('hermes', cwd=cwd, source=source, parent=bool(parent))
            roles = [r for (r,) in con.execute(
                "select role from messages where session_id = ? and timestamp >= ? and role in ('user','assistant') "
                'order by timestamp, id', (sid, since))]
            units, calls = _units_from_roles(roles)
            table.add('hermes', cohort, 'sessions')
            table.add('hermes', cohort, 'units', units)
            table.add('hermes', cohort, 'units_subagent' if parent else 'units_root', units)
            table.add('hermes', cohort, 'frontier_api_calls', calls)
            table.cat('hermes', cohort, 'session_source', str(source))
        con.close()
    except sqlite3.Error as exc:
        meta['error'] = type(exc).__name__
        return meta
    # z0 sees Hermes only through the (not yet installed) decisions plugin.
    hz = paths.ensure_layout(z0_home)['state'] / 'hermes' / 'outcomes.jsonl'
    meta['z0_layer_installed'] = hz.exists()
    seen = 0
    for r in _jsonl(hz):
        if r.get('excluded') or float(r.get('ts') or since) < since:
            continue
        seen += 1
    meta['z0_turn_records'] = seen
    _cap_seen(table, 'hermes', seen)
    return meta


def posture_window(since: float, z0_home: Path | None = None) -> dict[str, Any]:
    """Factory posture snapshots in the window: offload volume is only expected under OFFLOAD/RESERVE."""
    rows = [r for r in _jsonl(paths.ensure_layout(z0_home)['state'] / 'posture' / 'history.jsonl')
            if (_ts(r.get('now')) or 0) >= since]
    return {'snapshots': len(rows), 'factory': dict(Counter(str(r.get('factory')) for r in rows))}


def _cap_seen(table: Table, harness: str, seen: int) -> None:
    """Distribute harness-level z0 turn records over cohorts, never above observed units."""
    for cohort in COHORTS:
        if seen <= 0:
            break
        units = table.rows[(harness, cohort)]['units']
        take = min(units, seen)
        table.add(harness, cohort, 'seen_units', take)
        if take:
            table.cat(harness, cohort, 'seen_basis', 'z0_adapter_turn_record')
        seen -= take


def scan_omp(table: Table, since: float, *, home: Path | None = None, z0_home: Path | None = None) -> dict[str, Any]:
    base = (home or Path.home() / '.omp') / 'agent' / 'sessions'
    meta: dict[str, Any] = {'source': str(base), 'available': base.is_dir(), 'method': 'session jsonl row types + timestamps'}
    files = 0
    for path in base.glob('**/*.jsonl') if base.is_dir() else []:
        try:
            if path.stat().st_mtime < since:
                continue
        except OSError:
            continue
        files += 1
        parent = len(path.relative_to(base).parts) > 2  # <project>/<session>/<Agent>.jsonl = subagent
        cwd, roles = None, []
        for d in _jsonl(path):
            if d.get('type') == 'session':
                cwd = d.get('cwd')
            elif d.get('type') == 'message' and (_ts(d.get('timestamp')) or 0) >= since:
                roles.append((d.get('message') or {}).get('role'))
        units, calls = _units_from_roles(roles)
        if not units and not calls:
            continue
        cohort = cohort_for('omp', cwd=cwd, parent=parent)
        if not parent:
            table.add('omp', cohort, 'sessions')
        table.add('omp', cohort, 'units', units)
        table.add('omp', cohort, 'units_subagent' if parent else 'units_root', units)
        table.add('omp', cohort, 'frontier_api_calls', calls)
    meta['files_in_window'] = files
    rc = paths.ensure_layout(z0_home)['receipts'] / 'decisions.jsonl'
    opens = sum(1 for r in _jsonl(rc) if float(r.get('ts') or 0) >= since
                and (r.get('extra') or {}).get('harness') == 'omp' and 'turn_open' in json.dumps(r.get('extra')))
    meta['z0_layer_installed'] = (base.parent / 'extensions' / 'z0int-bridge').exists()
    meta['z0_turn_records'] = opens
    _cap_seen(table, 'omp', opens)
    return meta


def scan_codex(table: Table, since: float, *, home: Path | None = None) -> dict[str, Any]:
    base = (home or Path.home() / '.codex') / 'sessions'
    meta: dict[str, Any] = {'source': str(base), 'available': base.is_dir(), 'method': 'rollout row types + timestamps'}
    files = 0
    for path in base.glob('**/rollout-*.jsonl') if base.is_dir() else []:
        try:
            if path.stat().st_mtime < since:
                continue
        except OSError:
            continue
        files += 1
        cwd = originator = None
        units = calls = 0
        for d in _jsonl(path):
            p = d.get('payload') if isinstance(d.get('payload'), dict) else {}
            if d.get('type') == 'session_meta':
                cwd, originator = p.get('cwd'), p.get('originator')
            elif (_ts(d.get('timestamp')) or 0) >= since and d.get('type') == 'event_msg':
                units += p.get('type') == 'task_started'
                calls += p.get('type') == 'token_count'
        if not units:
            continue
        cohort = cohort_for('codex', cwd=cwd, originator=originator)
        table.add('codex', cohort, 'sessions')
        table.add('codex', cohort, 'units', units)
        table.add('codex', cohort, 'units_root', units)
        table.add('codex', cohort, 'frontier_api_calls', calls)
    meta['files_in_window'] = files
    return meta


# --------------------------------------------------------------------------- report

MEASUREMENT = {
    'claude-code': {'units': 'complete', 'seen': 'complete', 'decisions': 'complete', 'offloads': 'complete',
                    'displacement': 'estimated', 'basis': 'transcripts + z0 state (observed counts)'},
    'hermes': {'units': 'estimated', 'seen': 'complete', 'basis': 'local state.db counts; z0 layer not installed'},
    'omp': {'units': 'estimated', 'seen': 'complete', 'basis': 'local session files; z0 bridge not installed'},
    'codex': {'units': 'estimated', 'seen': 'complete', 'basis': 'local rollout files; no z0 turn adapter'},
}


def _ratio(a: float, b: float) -> float | None:
    return round(a / b, 4) if b else None


def _row(c: Counter, cats: dict[str, Counter] | None) -> dict[str, Any]:
    row = {k: int(c.get(k, 0)) for k in COUNT_KEYS}
    row['frontier_tokens_displaced_measured'] = None  # no paired measurement exists for any offload
    row['rates'] = {
        'coverage': _ratio(row['seen_units'], row['units']),
        'influence': _ratio(row['input_changed_units'], row['units']),
        'utilization': _ratio(row['utilized_units'], row['units']),
        'utilization_within_seen': _ratio(row['utilized_units'], row['seen_units']),
    }
    if cats:
        row['breakdown'] = {k: dict(v) for k, v in sorted(cats.items())}
    return row


def build_report(table: Table, *, range_spec: str, since: float, now: float, sources: dict[str, Any],
                 shadow: bool) -> dict[str, Any]:
    by_harness: dict[str, Any] = {}
    for harness in sorted({h for h, _ in table.rows} | set(HARNESSES)):
        cohorts = {}
        for cohort in sorted({c for h, c in table.rows if h == harness} | set(COHORTS)):
            cohorts[cohort] = _row(table.rows.get((harness, cohort), Counter()), table.cats.get((harness, cohort)))
        total = Counter()
        for cohort in HEADLINE_COHORTS:
            total.update(table.rows.get((harness, cohort), Counter()))
        by_harness[harness] = {'measurement': MEASUREMENT.get(harness, {'units': 'unknown'}),
                               'headline_cohorts': _row(total, None), 'by_cohort': cohorts}
    head = Counter()
    for (harness, cohort), c in table.rows.items():
        if cohort in HEADLINE_COHORTS:
            head.update(c)
    n, u = head['units'], head['utilized_units']
    estimated_units = sum(table.rows[(h, c)]['units'] for (h, c) in table.rows
                          if c in HEADLINE_COHORTS and MEASUREMENT.get(h, {}).get('units') != 'complete')
    state = 'complete' if not estimated_units else 'partial'
    displaced_est = sum(c['frontier_tokens_displaced_est'] for c in table.rows.values())
    obs_est = sum(c['obspack_tokens_withheld_est'] - c['obspack_recall_tokens_est'] for c in table.rows.values())
    eval_units = sum(c['units'] for (h, k), c in table.rows.items() if k == 'eval')
    return {
        'schema': SCHEMA,
        'definition': DEFINITION_VERSION,
        'range': range_spec,
        'period': {'start_ts': since, 'end_ts': now, 'start_iso': _iso(since), 'end_iso': _iso(now)},
        'sources': sources,
        'mode': {'claude_code_shadow': shadow},
        'headline': {
            'z0_utilization': _ratio(u, n),
            'numerator_utilized_units': int(u),
            'denominator_frontier_units': int(n),
            'utilized_by': {'displaced': int(head['utilized_displaced']), 'shortened': int(head['utilized_shortened']),
                            'verifiably_improved': int(head['utilized_improved'])},
            'coverage': _ratio(head['seen_units'], n),
            'influence': _ratio(head['input_changed_units'], n),
            'utilization_within_seen': _ratio(u, head['seen_units']),
            'seen_units': int(head['seen_units']), 'input_changed_units': int(head['input_changed_units']),
            'cohorts': list(HEADLINE_COHORTS), 'eval_units_excluded': int(eval_units),
            'measurement_state': state,
            'measurement_note': ('numerator from observed per-turn markers; denominator includes '
                                 f'{estimated_units} estimated units from harnesses z0 does not observe')
            if estimated_units else 'all units observed',
        },
        'by_harness': by_harness,
        # tokenomics.report.v1-compatible totals: measured and estimated never collapsed.
        'totals': {
            'actual_frontier_tokens': int(head['frontier_prompt_tokens'] + head['frontier_output_tokens']),
            'measured_tokens_avoided': 0,
            'estimated_tokens_avoided': int(max(0, displaced_est) + max(0, obs_est)),
            'estimated_tokens_avoided_by_mechanism': {'worker_offload': int(displaced_est),
                                                      'obspack_net_first_exposure': int(obs_est)},
            'measurement_state': state,
            'authoritative': False,
            'note': 'actual_frontier_tokens covers Claude Code interactive+agent only (other harnesses: units, not tokens)',
        },
    }


def tokenomics_fields(report: dict[str, Any]) -> dict[str, Any]:
    """Flat ``utilization.*`` fields for embedding in a tokenomics.report.v1 ``extensions`` block."""
    h = report['headline']
    out = {'utilization.schema': report['schema'], 'utilization.definition': report['definition'],
           'utilization.z0': h['z0_utilization'], 'utilization.units': h['denominator_frontier_units'],
           'utilization.utilized_units': h['numerator_utilized_units'], 'utilization.coverage': h['coverage'],
           'utilization.influence': h['influence'], 'utilization.measurement_state': h['measurement_state']}
    for harness, row in report['by_harness'].items():
        t = row['headline_cohorts']
        out[f'utilization.by_harness.{harness}.units'] = t['units']
        out[f'utilization.by_harness.{harness}.utilization'] = t['rates']['utilization']
        out[f'utilization.by_harness.{harness}.coverage'] = t['rates']['coverage']
    return out


def compute(range_spec: str = '7d', *, now: float | None = None, projects: Path | None = None,
            z0_home: Path | None = None, hermes_home: Path | None = None, omp_home: Path | None = None,
            codex_home: Path | None = None, external: bool = True) -> dict[str, Any]:
    from .claude_code import config
    from .claude_code_tokenomics import parse_range
    now = now or time.time()
    since = now - parse_range(range_spec)
    shadow_env = os.environ.get('Z0INT_CLAUDE_CODE_SHADOW')
    shadow = shadow_env != '0' if shadow_env is not None else config().get('shadow', True) is not False
    table = Table()
    opps = opportunity_traces(z0_home)
    cc = scan_claude_code(table, since, projects=projects, z0_home=z0_home, opportunities=opps,
                          outcomes=turn_outcomes(z0_home))
    sources: dict[str, Any] = {'claude-code': {'transcript_files': cc['files']}}
    sources['claude-code'].update(scan_decisions(table, since, cc['session_cohort'], z0_home=z0_home))
    sources['receipts'] = scan_receipts(table, since, cc['session_cohort'], cc['worker_calls'], z0_home=z0_home)
    if external:
        sources['hermes'] = scan_hermes(table, since, home=hermes_home, z0_home=z0_home)
        sources['omp'] = scan_omp(table, since, home=omp_home, z0_home=z0_home)
        sources['codex'] = scan_codex(table, since, home=codex_home)
    rep = build_report(table, range_spec=range_spec, since=since, now=now, sources=sources, shadow=shadow)
    rep['posture'] = posture_window(since, z0_home)
    rep['tokenomics_fields'] = tokenomics_fields(rep)
    return rep


def _pct(x: float | None) -> str:
    return '  n/a' if x is None else f'{100 * x:5.1f}%'


def format_text(rep: dict[str, Any]) -> str:
    h = rep['headline']
    lines = [
        f"z0 utilization ({rep['range']}, {rep['definition']}, state={h['measurement_state']})",
        f"  HEADLINE  {_pct(h['z0_utilization'])}  = {h['numerator_utilized_units']} utilized / "
        f"{h['denominator_frontier_units']} frontier units (interactive+agent, all harnesses)",
        f"            displaced={h['utilized_by']['displaced']} shortened={h['utilized_by']['shortened']} "
        f"verifiably_improved={h['utilized_by']['verifiably_improved']}",
        f"  funnel    coverage {_pct(h['coverage'])}  influence {_pct(h['influence'])}  "
        f"utilization-within-seen {_pct(h['utilization_within_seen'])}  (eval units excluded: {h['eval_units_excluded']})",
        '',
        f"{'harness/cohort':24}{'units':>7}{'seen':>7}{'changed':>8}{'inj_tok':>9}{'obs':>6}{'dec_sh':>7}"
        f"{'dec_enf':>8}{'off_att':>8}{'off_ok':>7}{'off_ver':>8}{'disp_est':>9}{'util':>6}{'util%':>7}",
    ]
    for harness, hrow in rep['by_harness'].items():
        for cohort, r in hrow['by_cohort'].items():
            if not any(r[k] for k in COUNT_KEYS):
                continue
            lines.append(
                f"{harness + '/' + cohort:24}{r['units']:>7}{r['seen_units']:>7}{r['input_changed_units']:>8}"
                f"{r['context_injected_tokens_est']:>9}{r['obspack_units']:>6}{r['decisions_shadow']:>7}"
                f"{r['decisions_enforced']:>8}{r['offloads_attempted']:>8}{r['offloads_completed']:>7}"
                f"{r['offloads_verified']:>8}{r['frontier_tokens_displaced_est']:>9}{r['utilized_units']:>6}"
                f"{_pct(r['rates']['utilization']):>7}")
    t = rep['totals']
    lines += ['', f"  tokens    actual_frontier={t['actual_frontier_tokens']:,} measured_avoided={t['measured_tokens_avoided']} "
                  f"estimated_avoided~{t['estimated_tokens_avoided']:,} {t['estimated_tokens_avoided_by_mechanism']}"]
    if rep.get('posture'):
        lines.append(f"  posture   {rep['posture']}  (offload displacement is expected ~0 under BURN)")
    return '\n'.join(lines)


def run(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog='z0int utilization', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--range', default='7d', help='window, e.g. 7d or 24h')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--projects-dir', type=Path, help='Claude Code projects dir (default ~/.claude/projects)')
    ap.add_argument('--no-external', action='store_true', help='skip Hermes/OMP/Codex volume counts')
    args = ap.parse_args(argv)
    rep = compute(args.range, projects=args.projects_dir, external=not args.no_external)
    print(json.dumps(rep, indent=1, default=str) if args.json else format_text(rep))
    return 0


if __name__ == '__main__':
    raise SystemExit(run())
