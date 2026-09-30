"""Claude Code -> Tokenomics: per-turn canonical events, mechanism attribution, displacement estimates.

Evidence rule (z0): token claims require Tokenomics evidence. This module keeps three
things apart and never adds them together:

* OBSERVED  provider usage copied by Claude Code into the transcript (one row per API
            message id; streamed chunks repeat an id and the last copy wins). Emitted as
            ``tokenomics.event.v0`` kind=llm, usage.source=provider.
* ESTIMATED tokens a z0 mechanism kept away from the frontier (ObservationPack compaction,
            worker offload). Emitted/reported with ``economics.estimated_tokens_avoided``
            and an explicit ``estimate_method``; never as measured.
* ATTRIBUTED which z0 mechanisms were active for a turn (lean profile, state packet,
            ObservationPack, worker offload). Attribution alone claims no savings: lean and
            packet savings are only knowable from paired A/B runs (tokenomics
            ``compare-costs``), so per-turn they stay ``unknown``.

Only counts, lengths, hashes and ids are read out of transcripts; no prompt, tool or
model text is retained or written anywhere.

Anthropic usage mapping (documented per event in ``extra.usage_semantics``):
``input_tokens`` = uncached input + cache reads + cache writes (all prompt tokens
processed), ``cached_input_tokens`` = cache reads, ``cache_write_input_tokens`` = cache
writes. The raw four Anthropic counters are kept in ``extra.anthropic_usage``.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import math
import os
import re
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from . import paths

SCHEMA = 'tokenomics.event.v0'
HARNESS = 'claude-code'
REPORT_SCHEMA = 'z0int.claude_code.tokenomics_report.v0'
USAGE_KEYS = ('input_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens', 'output_tokens')
USAGE_SEMANTICS = 'anthropic_v1: input_tokens=uncached+cache_read+cache_write; cached_input_tokens=cache_read'
TOKEN_METHOD = 'ceil_chars_div_4_v1'  # same 4-chars/token heuristic as worker receipts' ceil_utf8_bytes_div_4_v1
OBS_MARKER = '[z0 ObservationPack]'
OBS_SPLIT = re.compile(r'(\d+) middle lines \((\d+) chars total')
OBS_SAME = re.compile(r'identical to archived obs-\w+ \((\d+) lines, (\d+) chars\)')
PACKET_PREFIX = '<z0-state-packet'
WORKER_TOOL = re.compile(r'(route|delegate|dispatch)_worker$')
RECALL_CMD = re.compile(r'\bz0obs\b|claude_code_obs\s+recall')
PROFILE_ENV = 'Z0INT_CLAUDE_CODE_PROFILE'


def est_tokens(chars: int) -> int:
    return int(math.ceil(max(0, chars) / 4))


def _hex(*parts: Any, n: int) -> str:
    h = hashlib.sha256('\0'.join(str(p) for p in parts).encode()).hexdigest()[:n]
    return h if int(h, 16) else '1'.rjust(n, '0')


def _ts(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return _dt.datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
        except ValueError:
            return None
    return None


def _text_len(content: Any) -> int:
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        return sum(_text_len(b.get('text') if isinstance(b, dict) else b) for b in content)
    return 0


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return ''.join(_text(b.get('text') if isinstance(b, dict) else b) for b in content)
    return ''


# --------------------------------------------------------------------------- parsing

def parse_transcript(path: Path, start_line: int = 0) -> dict[str, Any]:
    """Reduce one transcript file to count-only records (no text retained).

    Returns ``{'records': [...], 'lines': n, 'session': {...}}``. Records are ordered; kinds:
    ``prompt`` (turn boundary), ``assistant`` (usage per message id), ``obs`` (ObservationPack
    compaction), ``recall`` (z0obs recall result re-entering context), ``worker_call`` /
    ``worker_result`` (z0 worker offload), ``compact`` (context reset).
    """
    records: list[dict[str, Any]] = []
    session: dict[str, Any] = {'packet_chars': 0, 'packet_injections': 0, 'skill_listing': False,
                               'cwd': None, 'first_ts': None, 'last_ts': None, 'session_id': None}
    pending_tools: dict[str, dict[str, Any]] = {}
    n = 0
    try:
        stream = open(path, encoding='utf-8', errors='replace')
    except OSError:
        return {'records': records, 'lines': 0, 'session': session}
    with stream:
        for n, line in enumerate(stream, 1):
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict):
                continue
            ts = _ts(row.get('timestamp'))
            if ts is not None:
                session['first_ts'] = session['first_ts'] or ts
                session['last_ts'] = ts
            session['cwd'] = session['cwd'] or row.get('cwd')
            session['session_id'] = session['session_id'] or row.get('sessionId')
            kind = row.get('type')
            if kind == 'attachment':
                att = row.get('attachment') or {}
                if att.get('type') == 'skill_listing':
                    session['skill_listing'] = True
                if att.get('type') == 'hook_additional_context' and att.get('hookEvent') == 'SessionStart':
                    text = _text(att.get('content'))
                    if text.lstrip().startswith(PACKET_PREFIX):
                        session['packet_injections'] += 1
                        session['packet_chars'] += len(text)
                continue
            if n <= start_line:
                # Already accounted by a previous hook run: only tool ids are needed to pair results.
                if kind == 'assistant':
                    for block in ((row.get('message') or {}).get('content') or []):
                        if isinstance(block, dict) and block.get('type') == 'tool_use':
                            pending_tools[block.get('id')] = _tool_info(block)
                continue
            if kind == 'system' and row.get('subtype') == 'compact_boundary':
                records.append({'kind': 'compact', 'ts': ts})
            elif kind == 'assistant':
                msg = row.get('message') or {}
                if not isinstance(msg, dict) or msg.get('model') == '<synthetic>':
                    continue
                if isinstance(msg.get('usage'), dict) and msg.get('id'):
                    records.append({'kind': 'assistant', 'id': msg['id'], 'model': msg.get('model'), 'ts': ts,
                                    'usage': {k: int(msg['usage'].get(k) or 0) for k in USAGE_KEYS}})
                for block in msg.get('content') or []:
                    if isinstance(block, dict) and block.get('type') == 'tool_use':
                        info = _tool_info(block)
                        pending_tools[block.get('id')] = info
                        if info['worker']:
                            records.append({'kind': 'worker_call', 'tool_use_id': block.get('id'), 'ts': ts,
                                            'tool': info['name'], 'args_chars': info['args_chars']})
            elif kind == 'user':
                msg = row.get('message') or {}
                content = msg.get('content')
                results = [b for b in content if isinstance(b, dict) and b.get('type') == 'tool_result'] \
                    if isinstance(content, list) else []
                if not results:
                    if not row.get('isMeta') and not row.get('isCompactSummary'):
                        records.append({'kind': 'prompt', 'prompt_id': row.get('promptId'), 'ts': ts,
                                        'harness_message': _text(content).lstrip().startswith(
                                            ('<agent-message', '<task-notification', '<system-reminder'))})
                    continue
                for block in results:
                    info = pending_tools.get(block.get('tool_use_id')) or {}
                    text = _text(block.get('content'))
                    if OBS_MARKER in text:
                        same = OBS_SAME.search(text)
                        split = OBS_SPLIT.search(text)
                        original = int((same or split).group(2)) if (same or split) else None
                        if original is not None:
                            records.append({'kind': 'obs', 'ts': ts, 'identical': bool(same),
                                            'original_chars': original, 'shown_chars': len(text)})
                    if info.get('recall'):
                        records.append({'kind': 'recall', 'ts': ts, 'chars': len(text)})
                    if info.get('worker'):
                        records.append({'kind': 'worker_result', 'ts': ts, 'tool_use_id': block.get('tool_use_id'),
                                        'result_chars': len(text), **_worker_result(text)})
    return {'records': records, 'lines': n, 'session': session}


def _tool_info(block: dict[str, Any]) -> dict[str, Any]:
    name = str(block.get('name') or '')
    inp = block.get('input') if isinstance(block.get('input'), dict) else {}
    command = inp.get('command') if name == 'Bash' and isinstance(inp.get('command'), str) else ''
    return {'name': name, 'worker': bool(WORKER_TOOL.search(name)) and 'z0int' in name.lower().replace('z0intelligence', 'z0int'),
            'recall': bool(RECALL_CMD.search(command)),
            'args_chars': len(json.dumps(inp, ensure_ascii=False, separators=(',', ':')))}


def _worker_result(text: str) -> dict[str, Any]:
    """Pull ids and counters (never text) out of a route_worker MCP result."""
    try:
        data = json.loads(text)
    except ValueError:
        return {'parsed': False}
    if not isinstance(data, dict):
        return {'parsed': False}
    return {'parsed': True, 'ok': data.get('ok'), 'subagent_id': data.get('subagent_id'),
            'provider': data.get('provider'), 'model': data.get('model'),
            'worker_input_tokens': data.get('input_tokens'), 'worker_output_tokens': data.get('output_tokens'),
            'output_chars': len(data['output']) if isinstance(data.get('output'), str) else None}


# --------------------------------------------------------------------------- turns

def split_turns(records: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    turns: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for rec in records:
        if rec['kind'] == 'prompt' and any(r['kind'] == 'assistant' for r in current):
            turns.append(current)
            current = []
        current.append(rec)
    if current:
        turns.append(current)
    return turns


def carried_requests(records: list[dict[str, Any]]) -> list[int]:
    """For each record index: distinct later API requests before the next context reset.

    A compacted tool result would have been re-read (mostly as cache reads) by every later
    request in the same context window; this is the carry-forward multiplier.
    """
    out = [0] * len(records)
    seen: set[str] = set()
    for i in range(len(records) - 1, -1, -1):
        rec = records[i]
        if rec['kind'] == 'compact':
            seen = set()
        out[i] = len(seen)
        if rec['kind'] == 'assistant':
            seen.add(rec['id'])
    return out


def _usage_of(assistants: Iterable[dict[str, Any]]) -> tuple[dict[str, int], list[str], list[str]]:
    last: dict[str, dict[str, Any]] = {}
    for rec in assistants:
        last[rec['id']] = rec  # streamed chunks repeat an id with growing usage
    usage = {k: sum(r['usage'][k] for r in last.values()) for k in USAGE_KEYS}
    models = sorted({r['model'] for r in last.values() if r.get('model')})
    return usage, sorted(last), models


def turn_events(turn: list[dict[str, Any]], carry: list[int], *, session_id: str, role: str,
                source_name: str, mechanisms: dict[str, Any], complete: bool,
                observer_id: str) -> list[dict[str, Any]]:
    """Canonical events for one turn: one observed llm event + optional estimated context event."""
    assistants = [r for r in turn if r['kind'] == 'assistant']
    events: list[dict[str, Any]] = []
    prompt = next((r for r in turn if r['kind'] == 'prompt'), None)
    usage, ids, models = _usage_of(assistants)
    obs = [(r, c) for r, c in zip(turn, carry) if r['kind'] == 'obs']
    recalls = [(r, c) for r, c in zip(turn, carry) if r['kind'] == 'recall']
    workers = [r for r in turn if r['kind'] == 'worker_result']
    anchor = (prompt or {}).get('prompt_id') or (ids[0] if ids else (turn[0].get('ts') if turn else ''))
    trace_id = _hex(HARNESS, session_id, role, source_name, anchor, n=32)
    ts = max((r['ts'] for r in turn if r.get('ts')), default=None) or time.time()
    started = min((r['ts'] for r in turn if r.get('ts')), default=None)
    turn_mech = {**mechanisms,
                 'obspack_compactions': len(obs), 'obspack_recalls': len(recalls), 'worker_offloads': len(workers)}
    if ids:
        state = 'complete' if complete else 'partial'
        events.append(_drop_none({
            'schema': SCHEMA, 'kind': 'llm', 'name': 'claude_code.turn', 'capability_id': 'claude_code.turn',
            'trace_id': trace_id, 'span_id': _hex(trace_id, 'turn', n=16),
            'event_id': 'cc-turn-' + _hex(session_id, source_name, *ids, n=24),
            'session_id': session_id, 'harness': HARNESS, 'role': role, 'status': 'ok',
            'model': {'provider': 'anthropic', 'name': models[0] if len(models) == 1 else ','.join(models)},
            'measurement_source': {
                'observer_id': observer_id, 'logical_source_id': f'claude-code:{session_id}',
                'physical_source_id': 'anthropic-messages:' + _hex(*ids, n=32), 'identity_basis': 'provider',
                'measurement_state': state,
                'state_reason': None if complete else 'turn_in_progress_or_transcript_truncated'},
            'usage': {
                'input_tokens': usage['input_tokens'] + usage['cache_read_input_tokens'] + usage['cache_creation_input_tokens'],
                'output_tokens': usage['output_tokens'], 'cached_input_tokens': usage['cache_read_input_tokens'],
                'cache_write_input_tokens': usage['cache_creation_input_tokens'],
                'attribution': 'incremental', 'source': 'provider'},
            'started_at': started, 'ended_at': ts if started is not None else None, 'ts': ts,
            'attributes': {'claude_code.api_requests': len(ids),
                           'claude_code.harness_message': bool((prompt or {}).get('harness_message'))},
            'extra': {'anthropic_usage': usage, 'usage_semantics': USAGE_SEMANTICS, 'mechanisms': turn_mech,
                      'transcript': source_name},
        }))
    if obs:
        removed = sum(max(0, r['original_chars'] - r['shown_chars']) for r, _ in obs)
        first = est_tokens(removed)
        carried = sum(est_tokens(max(0, r['original_chars'] - r['shown_chars'])) * c for r, c in obs)
        back = sum(est_tokens(r['chars']) for r, _ in recalls)
        back_carried = sum(est_tokens(r['chars']) * c for r, c in recalls)
        net_first = max(0, first - back)
        events.append(_drop_none({
            'schema': SCHEMA, 'kind': 'context', 'name': 'claude_code.obspack',
            'capability_id': 'claude_code.context_compression.obspack',
            'trace_id': trace_id, 'span_id': _hex(trace_id, 'obspack', n=16),
            'parent_span_id': _hex(trace_id, 'turn', n=16) if ids else None,
            'event_id': 'cc-obs-' + _hex(session_id, source_name, anchor, n=24),
            'session_id': session_id, 'harness': HARNESS, 'role': role, 'status': 'ok',
            'measurement_source': {'observer_id': observer_id, 'logical_source_id': f'claude-code:{session_id}',
                                   'identity_basis': 'derived', 'measurement_state': 'complete' if complete else 'partial'},
            'economics': {'estimated_tokens_avoided': net_first or None},
            'context': {'policy': 'z0.obspack', 'spilled_bytes': removed, 'spill_count': len(obs),
                        'retrieval_calls': len(recalls), 'reintroduced_bytes': sum(r['chars'] for r, _ in recalls)},
            'ts': ts,
            'extra': {'estimate_method': TOKEN_METHOD + '+carry_forward_v1',
                      'estimate_note': 'chars withheld from the context window / 4; counts, not currency',
                      'first_exposure_tokens_avoided_est': first, 'recall_tokens_reintroduced_est': back,
                      'carried_forward_tokens_avoided_est': max(0, carried - back_carried),
                      'carry_forward_basis': 'withheld tokens x later API requests before next compact_boundary '
                                             '(mostly cache-read priced)',
                      'identical_collapses': sum(1 for r, _ in obs if r['identical'])},
        }))
    return events


def _drop_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _drop_none(v) for k, v in value.items() if v is not None}
    return value


# --------------------------------------------------------------------------- mechanisms

def launches_path(root: Path | None = None) -> Path:
    return paths.ensure_layout(root)['state'] / HARNESS / 'launches.jsonl'


def _launches(root: Path | None = None) -> list[dict[str, Any]]:
    rows = []
    try:
        for line in launches_path(root).read_text().splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
    except OSError:
        pass
    try:  # legacy single-entry-per-directory state from claude_code_launch.record
        warm = json.loads((paths.ensure_layout(root)['state'] / HARNESS / 'warm.json').read_text())
        rows += [{'dir': d, **v} for d, v in warm.items() if isinstance(v, dict)]
    except (OSError, ValueError):
        pass
    return rows


def session_mechanisms(session: dict[str, Any], *, role: str, env_profile: str | None = None,
                       launches: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Session-level mechanism attribution with an explicit basis for each claim."""
    if env_profile:
        profile, basis = env_profile, 'launch_env'
    else:
        profile, basis = None, 'unknown'
        first = session.get('first_ts')
        for row in launches or []:
            if row.get('dir') == session.get('cwd') and first and row.get('ts') and 0 <= first - row['ts'] <= 120:
                profile, basis = row.get('profile'), 'launch_ledger_match_120s'
                break
        if profile is None and role == 'root':
            # Lean passes --disable-slash-commands, so no skill listing reaches the transcript.
            if session.get('skill_listing'):
                profile, basis = 'stock_or_custom', 'transcript_skill_listing_present'
            elif session.get('first_ts'):
                profile, basis = 'lean_inferred', 'transcript_no_skill_listing'
    # lean_profile is asserted only on launch evidence; a transcript-shape inference stays None.
    lean = True if profile == 'lean' else False if profile in ('stock', 'stock_or_custom') else None
    return {'lean_profile': lean, 'profile': profile,
            'profile_basis': basis, 'packet_injected': session.get('packet_injections', 0) > 0,
            'packet_tokens_est': est_tokens(session.get('packet_chars', 0)) or None}


# --------------------------------------------------------------------------- worker displacement

def worker_receipts(root: Path | None = None, *, since: float = 0.0) -> list[dict[str, Any]]:
    """Completed physical worker attempts attributed to the claude-code harness."""
    path = paths.ensure_layout(root)['receipts'] / 'decisions.jsonl'
    rows = []
    try:
        stream = path.open(encoding='utf-8')
    except OSError:
        return rows
    with stream:
        for line in stream:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            e = r.get('extra') if isinstance(r.get('extra'), dict) else {}
            if (e.get('harness') == HARNESS and r.get('provider') and e.get('physical_call_attempted')
                    and e.get('status') in ('completed', 'failed', 'incomplete', 'completed_unmetered_or_unidentified')
                    and float(r.get('ts') or 0) >= since):
                rows.append(r)
    return rows


def displacement_estimate(receipt: dict[str, Any], transcript_call: dict[str, Any] | None = None) -> dict[str, Any]:
    """Frontier tokens an offloaded worker call would have cost. Always an ESTIMATE.

    Two counterfactuals, chosen by who called the worker:

    * ``frontier_agent_inline`` (call observed as an MCP tool_use in a Claude Code
      transcript): had the parent done the work itself it would have generated the output
      (est output tokens) but it would NOT have written the task/context into a tool call
      (est args tokens, already spent as frontier output). net = output_est - args_est,
      which can be negative for short answers over long pasted context.
    * ``frontier_api_call`` (programmatic caller, e.g. a z0int shadow probe): the
      replacement would be one frontier call reading the same messages and writing the same
      output: gross = baseline_input_tokens + baseline_output_tokens from the receipt.

    Both use the receipt's ``ceil_utf8_bytes_div_4_v1`` token heuristic; no tokenizer is run.
    Only a completed (winning) attempt displaces anything.
    """
    e = receipt.get('extra') or {}
    completed = e.get('status') == 'completed'
    base_in = receipt.get('baseline_input_tokens')
    base_out = receipt.get('baseline_output_tokens')
    free = bool(e.get('free_tier_validated')) and e.get('validated_price_usd') in (0, 0.0)
    reported = e.get('provider_reported_cost_usd')
    out: dict[str, Any] = {
        'receipt_trace_id': receipt.get('trace_id'), 'subagent_id': e.get('subagent_id'),
        'provider': receipt.get('provider'), 'model': receipt.get('model'), 'status': e.get('status'),
        'route_source': e.get('route_source'), 'parent_agent_class': 'frontier_agent' if transcript_call else 'programmatic',
        'worker_input_tokens': receipt.get('input_tokens'), 'worker_output_tokens': receipt.get('output_tokens'),
        'worker_usage_source': e.get('usage_source'),
        'cost_usd': 0.0 if free and reported in (None, 0, 0.0) else reported,
        'cost_basis': ('validated_free_tier_price' if reported is None else 'provider_reported') if free else
                      ('provider_reported' if reported is not None else 'unknown'),
        'measurement_state': 'estimated', 'estimate_method': (e.get('baseline') or {}).get('method') or 'ceil_utf8_bytes_div_4_v1',
    }
    if not completed or base_in is None or base_out is None:
        out.update(counterfactual=None, frontier_tokens_displaced_est=0, reason='no winning attempt or no baseline')
        return out
    if transcript_call:
        args = est_tokens(transcript_call.get('args_chars') or 0)
        out.update(counterfactual='frontier_agent_inline', delegation_overhead_tokens_est=args,
                   frontier_tokens_displaced_est=int(base_out) - args)
    else:
        out.update(counterfactual='frontier_api_call', frontier_tokens_displaced_est=int(base_in) + int(base_out))
    return out


# --------------------------------------------------------------------------- live hook

def events_for_stop(transcript_files: list[tuple[str, Path]], *, session_id: str, state: dict[str, Any],
                    env_profile: str | None = None, root: Path | None = None) -> list[dict[str, Any]]:
    """Events for everything appended to the session's transcripts since the last Stop."""
    lines = state.setdefault('lines', {})
    lines_before = dict(lines)
    seen = set(state.get('message_ids', []))
    launches = _launches(root)
    events: list[dict[str, Any]] = []
    for role, path in transcript_files:
        start = int(lines.get(path.name, 0))
        parsed = parse_transcript(path, start_line=start)
        lines[path.name] = parsed['lines']
        # Keep only message ids not yet billed (streaming/partial rows can straddle a Stop).
        records = parsed['records']
        if path.name not in lines_before and seen:
            # Migrating from id-only state: skip everything up to the last already-billed message.
            last = max((i for i, r in enumerate(records) if r['kind'] == 'assistant' and r['id'] in seen), default=-1)
            records = records[last + 1:]
        records = [r for r in records if r['kind'] != 'assistant' or r['id'] not in seen]
        if not records:
            continue
        mech = session_mechanisms(parsed['session'], role=role, env_profile=env_profile, launches=launches)
        carry = carried_requests(records)
        # One Stop == one turn for the root; subagent files are one turn per Stop window.
        events += turn_events(records, carry, session_id=session_id, role=role, source_name=path.name,
                              mechanisms=mech, complete=True, observer_id='z0int.claude_code.stop')
        seen.update(r['id'] for r in records if r['kind'] == 'assistant')
    state['message_ids'] = sorted(seen)
    return events


# --------------------------------------------------------------------------- backfill

def projects_dir() -> Path:
    return Path(os.environ.get('CLAUDE_CONFIG_DIR', Path.home() / '.claude')).expanduser() / 'projects'


def discover(since: float, base: Path | None = None) -> list[tuple[str, str, Path]]:
    """(session_id, role, path) for transcripts touched since ``since``."""
    base = base or projects_dir()
    out = []
    for path in sorted(base.glob('*/*.jsonl')):
        try:
            if path.stat().st_mtime < since:
                continue
        except OSError:
            continue
        out.append((path.stem, 'root', path))
        sub = path.with_suffix('') / 'subagents'
        if sub.is_dir():
            out += [(path.stem, 'subagent', p) for p in sorted(sub.glob('*.jsonl'))]
    return out


def backfill_events(since: float, *, base: Path | None = None, root: Path | None = None,
                    now: float | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    now = now or time.time()
    launches = _launches(root)
    events: list[dict[str, Any]] = []
    sessions: dict[str, dict[str, Any]] = {}
    transcript_calls: dict[str, dict[str, Any]] = {}
    files = discover(since, base)
    for session_id, role, path in files:
        parsed = parse_transcript(path)
        mech = session_mechanisms(parsed['session'], role=role, launches=launches)
        if role == 'root':
            sessions[session_id] = mech
        else:
            # CLI profile flags apply to the whole process; the SessionStart packet reaches only the root.
            mech = {**mech, **{k: v for k, v in sessions.get(session_id, {}).items()
                               if k in ('lean_profile', 'profile', 'profile_basis')}}
        records = parsed['records']
        carry = carried_requests(records)
        idx = 0
        turns = split_turns(records)
        for n, turn in enumerate(turns):
            c = carry[idx: idx + len(turn)]
            idx += len(turn)
            if not turn or max((r.get('ts') or 0) for r in turn) < since:
                continue
            # The newest turn of a file written in the last 2 minutes may still be streaming.
            live = n == len(turns) - 1 and now - (parsed['session'].get('last_ts') or 0) < 120
            events += turn_events(turn, c, session_id=session_id, role=role, source_name=path.name,
                                  mechanisms=mech, complete=not live, observer_id='z0int.claude_code.backfill')
        for r in records:
            if r['kind'] == 'worker_call':
                transcript_calls[r['tool_use_id']] = r
            if r['kind'] == 'worker_result' and r.get('subagent_id'):
                call = transcript_calls.get(r['tool_use_id'])
                if call:
                    transcript_calls[r['subagent_id']] = call
    return events, {'files': len(files), 'sessions': sessions, 'transcript_worker_calls': transcript_calls}


# --------------------------------------------------------------------------- report

def build_report(events: list[dict[str, Any]], *, sessions: dict[str, Any] | None = None,
                 receipts: list[dict[str, Any]] | None = None, transcript_calls: dict[str, Any] | None = None,
                 window: dict[str, Any] | None = None) -> dict[str, Any]:
    turns = [e for e in events if e['name'] == 'claude_code.turn']
    obs = [e for e in events if e['name'] == 'claude_code.obspack']
    usage = Counter()
    by_model: dict[str, Counter] = defaultdict(Counter)
    states = Counter()
    roles = Counter()
    requests = 0
    for e in turns:
        au = e['extra']['anthropic_usage']
        usage.update(au)
        by_model[e['model']['name']].update(au)
        states[e['measurement_source']['measurement_state']] += 1
        roles[e['role']] += 1
        requests += int(e['attributes']['claude_code.api_requests'])
    mech_turns = Counter()
    for e in turns:
        m = e['extra']['mechanisms']
        mech_turns['lean_profile_confirmed'] += m.get('lean_profile') is True
        mech_turns['lean_profile_inferred_only'] += m.get('profile') == 'lean_inferred'
        mech_turns['packet_injected'] += bool(m.get('packet_injected'))
        mech_turns['obspack'] += bool(m.get('obspack_compactions'))
        mech_turns['worker_offload'] += bool(m.get('worker_offloads'))
    session_ids = {e['session_id'] for e in turns}
    sess = sessions or {}
    basis = Counter(v.get('profile_basis') + ':' + str(v.get('profile')) for v in sess.values())
    offloads = []
    for r in receipts or []:
        sid = (r.get('extra') or {}).get('subagent_id')
        offloads.append(displacement_estimate(r, (transcript_calls or {}).get(sid)))
    won = [o for o in offloads if o.get('counterfactual')]
    total_in = usage['input_tokens'] + usage['cache_read_input_tokens'] + usage['cache_creation_input_tokens']
    return {
        'schema': REPORT_SCHEMA, 'window': window or {}, 'evidence_rule': 'token claims require Tokenomics evidence',
        'observed': {
            'label': 'OBSERVED provider usage from Claude Code transcripts (tokenomics usage.source=provider)',
            'sessions': len(session_ids), 'turn_events': len(turns), 'turns_by_role': dict(roles),
            'api_requests': requests, 'measurement_state': dict(states),
            'anthropic_usage': dict(usage), 'prompt_tokens_total': total_in,
            'cache_read_share_of_prompt': round(usage['cache_read_input_tokens'] / total_in, 4) if total_in else None,
            'by_model': {k: dict(v) for k, v in sorted(by_model.items())},
        },
        'attribution': {
            'label': 'ATTRIBUTED mechanism presence; no per-turn savings claimed (needs paired A/B)',
            'turns_with': dict(mech_turns), 'session_profile_basis': dict(basis),
            'packet_sessions': sum(1 for v in sess.values() if v.get('packet_injected')),
            'packet_injected_tokens_est': sum(v.get('packet_tokens_est') or 0 for v in sess.values()),
            'lean_savings': 'unknown per turn; see z0evals claude-code-savings-v0 paired result (not re-measured here)',
            'packet_savings': 'unknown per turn; injection cost counted above as an estimate',
        },
        'estimated': {
            'label': 'ESTIMATED tokens kept from the frontier; method stated; never summed with observed',
            'obspack': {
                'turns': len(obs), 'compactions': sum(e['context']['spill_count'] for e in obs),
                'identical_collapses': sum(e['extra']['identical_collapses'] for e in obs),
                'recalls': sum(e['context'].get('retrieval_calls', 0) for e in obs),
                'chars_withheld': sum(e['context']['spilled_bytes'] for e in obs),
                'first_exposure_tokens_avoided_est': sum(e['extra']['first_exposure_tokens_avoided_est'] for e in obs),
                'recall_tokens_reintroduced_est': sum(e['extra']['recall_tokens_reintroduced_est'] for e in obs),
                'carried_forward_tokens_avoided_est': sum(e['extra']['carried_forward_tokens_avoided_est'] for e in obs),
                'method': TOKEN_METHOD + '+carry_forward_v1',
            },
            'worker_offload': {
                'transcript_mcp_calls': sum(1 for k, v in (transcript_calls or {}).items() if k == v.get('tool_use_id')),
                'receipts': len(offloads), 'winning_attempts': len(won),
                'by_parent_class': dict(Counter(o['parent_agent_class'] for o in won)),
                'by_provider_model': dict(Counter(f"{o['provider']}/{o['model']}" for o in won)),
                'cost_usd_total': sum(o['cost_usd'] or 0 for o in won) if all(o['cost_usd'] is not None for o in won) else None,
                'cost_basis': dict(Counter(o['cost_basis'] for o in won)),
                'worker_tokens_observed': sum((o['worker_input_tokens'] or 0) + (o['worker_output_tokens'] or 0) for o in won),
                'frontier_tokens_displaced_est': {
                    cf: sum(o['frontier_tokens_displaced_est'] for o in won if o['counterfactual'] == cf)
                    for cf in sorted({o['counterfactual'] for o in won})},
                'method': 'displacement_estimate_v1 (see z0int.claude_code_tokenomics.displacement_estimate)',
            },
        },
    }


def tokenomics_report(events: list[dict[str, Any]], range_spec: str = '7d', now: float | None = None) -> dict[str, Any] | None:
    """The canonical tokenomics.report.v1 over the same events (tiers kept apart)."""
    try:
        from tokenomics.models import TokenomicsEvent
        from tokenomics.report import build_savings_report
    except Exception:
        return None
    rows = [TokenomicsEvent.from_dict(e) for e in events]
    return build_savings_report(rows, range_spec=range_spec, now=now, sources=['z0int.claude_code_tokenomics'])


def live_events(root: Path | None = None, since: float = 0.0) -> list[dict[str, Any]]:
    from .tokenomics_emit import events_path
    out = []
    try:
        for line in events_path(root).read_text().splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get('schema') == SCHEMA and r.get('harness') == HARNESS and float(r.get('ts') or 0) >= since:
                out.append(r)
    except OSError:
        pass
    return out


def parse_range(spec: str) -> float:
    m = re.fullmatch(r'(\d+)([hd])', spec)
    if not m:
        raise ValueError('range must look like 7d or 24h')
    return int(m.group(1)) * (86400 if m.group(2) == 'd' else 3600)


def format_text(rep: dict[str, Any]) -> str:
    o, a, est = rep['observed'], rep['attribution'], rep['estimated']
    u = o['anthropic_usage']
    ob, w = est['obspack'], est['worker_offload']
    lines = [
        f"Claude Code tokenomics  ({rep['window'].get('source')}, {rep['window'].get('range')})",
        f"OBSERVED  sessions={o['sessions']} turns={o['turn_events']} {o['turns_by_role']} api_requests={o['api_requests']} "
        f"state={o['measurement_state']}",
        f"          uncached_in={u.get('input_tokens', 0):,} cache_read={u.get('cache_read_input_tokens', 0):,} "
        f"cache_write={u.get('cache_creation_input_tokens', 0):,} out={u.get('output_tokens', 0):,} "
        f"cache_read_share={o['cache_read_share_of_prompt']}",
        f"ATTRIBUTED turns_with={a['turns_with']} packet_sessions={a['packet_sessions']} "
        f"packet_injected_tokens_est={a['packet_injected_tokens_est']:,}",
        f"          profile_basis={a['session_profile_basis']}",
        f"ESTIMATED obspack compactions={ob['compactions']} (identical={ob['identical_collapses']}) recalls={ob['recalls']} "
        f"first_exposure_avoided~{ob['first_exposure_tokens_avoided_est']:,} carried_forward_avoided~{ob['carried_forward_tokens_avoided_est']:,} "
        f"reintroduced~{ob['recall_tokens_reintroduced_est']:,}  [{ob['method']}]",
        f"          worker offload: transcript_mcp_calls={w['transcript_mcp_calls']} receipts={w['receipts']} won={w['winning_attempts']} "
        f"by_parent={w['by_parent_class']} cost_usd={w['cost_usd_total']} displaced~{w['frontier_tokens_displaced_est']}",
    ]
    t = rep.get('tokenomics_report_v1')
    if t:
        tot = t.get('totals') or {}
        lines.append(f"tokenomics.report.v1 n_events={t.get('n_events')} actual_frontier={tot.get('actual_frontier_tokens'):,} "
                     f"measured_avoided={tot.get('measured_tokens_avoided')} estimated_avoided={tot.get('estimated_tokens_avoided'):,} "
                     f"state={tot.get('measurement_state')}")
    return '\n'.join(lines)


def run(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog='z0int claude-code tokenomics', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--range', default='7d')
    ap.add_argument('--backfill', action='store_true', help='rebuild events from local transcripts (read-only)')
    ap.add_argument('--projects-dir', type=Path, help='Claude Code projects dir (default ~/.claude/projects)')
    ap.add_argument('--events-out', type=Path, help='with --backfill: also write the rebuilt events as JSONL')
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args(argv)
    now = time.time()
    since = now - parse_range(args.range)
    if args.backfill:
        events, meta = backfill_events(since, base=args.projects_dir, now=now)
        sessions, calls = meta['sessions'], meta['transcript_worker_calls']
        window = {'source': 'backfill:transcripts', 'range': args.range, 'files': meta['files']}
        if args.events_out:
            args.events_out.parent.mkdir(parents=True, exist_ok=True)
            args.events_out.write_text(''.join(json.dumps(e, sort_keys=True) + '\n' for e in events))
    else:
        events = live_events(since=since)
        sessions, calls = {}, {}
        for e in events:
            if e['name'] == 'claude_code.turn' and e['role'] == 'root':
                sessions[e['session_id']] = e['extra']['mechanisms']
        window = {'source': 'live:~/.z0int/tokenomics/events.jsonl', 'range': args.range}
    rep = build_report(events, sessions=sessions, receipts=worker_receipts(since=since), transcript_calls=calls,
                       window={**window, 'generated_at': now})
    rep['tokenomics_report_v1'] = tokenomics_report(events, args.range, now)
    print(json.dumps(rep, indent=1, default=str) if args.json else format_text(rep))
    return 0
