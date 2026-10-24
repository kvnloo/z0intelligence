"""Legacy importers (z0int#56 M1, D1b): OMP v1 spine, OMP cognition-shadow and DSH jev receipts -> cohort legacy.

  omp-v1                decision_receipt.v1 + outcome_join.v1  -> state/omp/imported_turns.jsonl   (imported_turn.v0)
  omp-cognition-shadow  cognition.shadow.receipt.v1            -> state/omp/shadow_decisions.jsonl (shadow_decision.v0)
  dsh-jev               ~/.dsh/jev/receipts.jsonl jev_decision -> state/dsh/shadow_decisions.jsonl (shadow_decision.v0)

Rows carry hashed ids and closed-vocabulary values only: no prompt, state, tool input, path or session id is
copied. The only legacy label is the effective tier of an OMP v1 outcome (gold -> 1, negative -> 0; ambient or
execution-only closes are never a success); shadow decisions have ``y`` null. Each source is read incrementally
from a byte-offset watermark (state/<harness>/import_watermarks.json), so a re-run reads nothing new, appends
nothing and returns the same manifest hash. Sources under /workspace/hermes-home (also through a symlink) are
refused unopened.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Iterable

from . import harness_capture as hc
from .harness_id import turn_key
from .receipt import effective_tier, is_ambient_close_source

MANIFEST_SCHEMA = 'z0int.legacy_import.manifest.v0'
WATERMARKS = 'import_watermarks.json'
REFUSED_ROOTS = ('/workspace/hermes-home',)
ID_RE = re.compile(r'[A-Za-z0-9_.:@+-]{1,80}')  # ids and closed-vocabulary values; anything else is "other"
ACTUAL_TOOL = re.compile(r'^OMP tool_call: ([A-Za-z0-9_.:-]{1,64})')
# omp-extensions/local-cognition riskFor(): these tools are "write", every other tool "read".
WRITE_TOOLS = frozenset({'bash', 'write', 'edit', 'multiedit', 'apply_patch', 'notebook_edit', 'write_file', 'create_file'})
HEAD = 4096
JEV_TAIL_LINES = 64  # a jev_decision is held for its model_request only near the end of the file ...
JEV_GRACE_S = 600.0  # ... and only this soon after it was written; past that its request is not coming (a crash)


def _h(obj: Any, n: int = 16) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:n]


def _id(value: Any) -> str | None:
    if value is None:
        return None
    return value if isinstance(value, str) and ID_RE.fullmatch(value) else 'other'


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _day(ts: Any) -> str | None:
    if isinstance(ts, (int, float)):
        return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime('%Y-%m-%d')
    return ts[:10] if isinstance(ts, str) and re.match(r'\d{4}-\d\d-\d\d', ts) else None


def _guard(path: Path) -> Path:
    """Refuse a source under a live harness home before anything opens it. realpath resolves symlinks (lstat and
    readlink only), so a link that points into the home is refused as well."""
    p = os.path.abspath(os.path.expanduser(str(path)))
    roots = {*REFUSED_ROOTS, *map(os.path.realpath, REFUSED_ROOTS)}
    if any(q == r or q.startswith(r + '/') for q in (p, os.path.realpath(p)) for r in roots):
        raise ValueError(f'refusing to read {p}: legacy imports never read /workspace/hermes-home')
    return Path(p)


# ----------------------------------------------------------------------------- watermark + output
class _Run:
    """One import run for one harness: watermarked reads, changed-content appends, the manifest."""

    def __init__(self, harness: str, source: str, out_name: str, root: str | Path | None):
        self.harness, self.source, self.root = harness, source, root
        self.out = hc.state_dir(harness, root) / out_name
        self.marks_path = hc.state_dir(harness, root) / WATERMARKS
        try:
            self.marks = json.loads(self.marks_path.read_text())
        except (OSError, ValueError):
            self.marks = {}
        self.read_lines = 0
        self.counts: dict[str, int] = {}
        self._pending: dict[str, dict[str, Any]] = {}
        self._seen: dict[str, tuple[int, set[int]]] = {}  # per key: counted up to, offsets held last run

    def count(self, key: str, n: int = 1) -> None:
        self.counts[key] = self.counts.get(key, 0) + n

    def lines(self, path: Path, key: str) -> list[tuple[int, dict[str, Any]]]:
        """(byte offset, row) for every complete line past the watermark; the mark moves only in ``finish``.

        A held line (``hold``) keeps the watermark at its offset, so the lines after it are read again next run:
        their rows dedupe by content in ``finish`` and ``is_new`` keeps their counts from being added twice.
        """
        path = _guard(path)
        try:
            fh = open(path, 'rb')
        except OSError:
            return []
        with fh:
            mark = self.marks.get(key) or {}
            head = hashlib.sha256(fh.read(mark.get('head_len', 0))).hexdigest()[:16]
            same = head == mark.get('head')
            start = mark.get('offset', 0) if same else 0  # rewritten file: start over
            self._seen[key] = (mark.get('counted', start), set(mark.get('held', []))) if same else (0, set())
            fh.seek(start)
            data = fh.read()
            fh.seek(0)
            head_len = min(HEAD, start + len(data))
            new_head = hashlib.sha256(fh.read(head_len)).hexdigest()[:16]
        out, pos = [], start
        for raw in data.split(b'\n')[:-1]:  # a last line without its newline is still being written
            try:
                row = json.loads(raw)
            except ValueError:
                row = None
            if isinstance(row, dict):
                out.append((pos, row))
            pos += len(raw) + 1
        self.read_lines += len(out)
        self._pending[key] = {'offset': pos, 'counted': pos, 'held': [], 'head': new_head, 'head_len': head_len}
        return out

    def is_new(self, key: str, offset: int) -> bool:
        """True for a line no earlier run has counted (past the last read, or held last time)."""
        counted, held = self._seen.get(key, (0, set()))
        return offset >= counted or offset in held

    def hold(self, key: str, offset: int) -> None:
        """Keep the watermark at ``offset`` (a row whose partner line has not been written yet)."""
        self._pending[key]['offset'] = min(self._pending[key]['offset'], offset)
        self._pending[key]['held'].append(offset)

    def finish(self, rows: Iterable[dict[str, Any]], id_key: str) -> dict[str, Any]:
        latest: dict[str, str] = {}
        for r in hc._read_jsonl(self.out):
            latest[r.get(id_key)] = r.get('content_sha')
        fresh = []
        for r in rows:
            r['content_sha'] = _h({k: v for k, v in r.items() if k != 'content_sha'})
            if latest.get(r[id_key]) != r['content_sha']:
                latest[r[id_key]] = r['content_sha']
                fresh.append(r)
        self.out.parent.mkdir(parents=True, exist_ok=True)
        if fresh:
            with open(self.out, 'a', encoding='utf-8') as fh:
                fh.write(''.join(json.dumps(r, sort_keys=True) + '\n' for r in fresh))
        totals = self.marks.setdefault('totals', {}).setdefault(self.source, {})
        for k, v in self.counts.items():
            totals[k] = totals.get(k, 0) + v
        self.marks.update(self._pending)
        self.marks_path.write_text(json.dumps(self.marks, sort_keys=True))
        try:
            data = self.out.read_bytes()
        except OSError:
            data = b''
        man = {'schema': MANIFEST_SCHEMA, 'source': self.source, 'harness': self.harness, 'cohort': 'legacy',
               'output': {'file': self.out.name, 'rows': data.count(b'\n'), 'sha256': hashlib.sha256(data).hexdigest()},
               'totals': dict(sorted(totals.items()))}
        man['manifest_sha256'] = _h(man, 64)
        return dict(man, rows_added=len(fresh), counts=dict(sorted(self.counts.items())),
                    run={'read_lines': self.read_lines})


def _base(harness: str, source: str, session: Any, trace: Any, ts: Any) -> dict[str, Any]:
    return {'schema': hc.schema(harness, 'imported_turn' if source == 'omp-v1' else 'shadow_decision'),
            'harness': harness, 'cohort': 'legacy', 'source': source,
            'turn_key': turn_key(harness, session or '', trace), 'group': _h({'session': session or ''}, 12),
            'day': _day(ts), 'privacy': 'hashed_ids_and_closed_vocabulary_only'}


# ----------------------------------------------------------------------------- OMP v1 spine
def import_omp_v1(decisions: Path, outcomes: Path, *, root: str | Path | None = None) -> dict[str, Any]:
    """decision_receipt.v1 rows (with their outcome) and outcome_join.v1 rows -> z0int.omp.imported_turn.v0.

    The label is the effective tier (receipt.effective_tier: an ambient close never mints gold): gold -> y 1,
    negative -> y 0, execution/soft -> y null. A receipt with no outcome is counted (no_outcome), not imported.
    """
    run = _Run('omp', 'omp-v1', 'imported_turns.jsonl', root)
    traces: dict[str, dict[str, Any]] = {}
    for key, path, kind in (('omp-v1:decisions', decisions, 'z0int.decision_receipt.v1'),
                            ('omp-v1:outcomes', outcomes, 'z0int.outcome_join.v1')):
        for _, row in run.lines(path, key):
            if row.get('schema') != kind or not row.get('trace_id'):
                continue
            rec = row if kind == 'z0int.decision_receipt.v1' else (row.get('receipt') or {})
            t = traces.setdefault(row['trace_id'], {})
            for k in ('session_id', 'capability_id', 'provider', 'route', 'execution', 'ts'):
                if t.get(k) is None and rec.get(k) is not None:
                    t[k] = rec[k]
            if row.get('outcome') or row.get('outcome_tier'):
                t['outcome'], t['stored'] = row.get('outcome'), row.get('outcome_tier')
    rows = []
    for trace, t in traces.items():
        if 'outcome' not in t:
            run.count('no_outcome')
            continue
        oc = t['outcome'] if isinstance(t['outcome'], dict) else None
        tier = effective_tier(oc, stored_tier=t['stored'] if isinstance(t['stored'], str) else None)
        y = 1 if tier == 'gold' else 0 if tier == 'negative' else None
        run.count(f'tier_{tier}')
        rows.append({**_base('omp', 'omp-v1', t.get('session_id'), trace, t.get('ts')),
                     'capability': _id(t.get('capability_id')), 'provider': _id(t.get('provider')),
                     'route': _id(t.get('route')), 'execution': _id(t.get('execution')),
                     'ambient': is_ambient_close_source((oc or {}).get('source')),
                     'label': {'tier': tier, 'y_success': y,
                               'state': {1: 'verified_success', 0: 'verified_failure'}.get(y, 'unverified')}})
    return run.finish(rows, 'turn_key')


# ----------------------------------------------------------------------------- OMP cognition-shadow
def import_cognition_shadow(path: Path, *, root: str | Path | None = None) -> dict[str, Any]:
    """Served shadow answers -> z0int.omp.shadow_decision.v0; transport errors and not-served answers are only
    counted (backend_unavailable). The observed tool is kept as its name + risk class; its input is dropped."""
    run = _Run('omp', 'omp-cognition-shadow', 'shadow_decisions.jsonl', root)
    rows = []
    for _, rec in run.lines(path, 'omp-cognition-shadow'):
        if rec.get('schema') != 'z0int.cognition.shadow.receipt.v1':
            run.count('unsupported_schema')
            continue
        m = ACTUAL_TOOL.match(rec.get('state') or '')
        actual = {'name': m.group(1), 'risk_class': 'write' if m.group(1) in WRITE_TOOLS else 'read'} if m else None
        for i, sh in enumerate(rec.get('shadow') or []):
            if not isinstance(sh, dict) or not sh.get('backend') or sh.get('error'):
                run.count('backend_unavailable')
                continue
            run.count('served')
            rows.append({**_base('omp', 'omp-cognition-shadow', rec.get('session_id'), rec.get('trace_id'), rec.get('ts')),
                         'decision_id': _h({'trace': rec.get('trace_id'), 'shadow': i}, 20),
                         'challenger': {'id': _id(sh.get('label')), 'backend': _id(sh.get('backend')),
                                        'model': _id(sh.get('model'))},
                         'decision': {'selected_action': _id(sh.get('selected_action')),
                                      'abstained': bool(sh.get('abstained')), 'invalid_call': bool(sh.get('invalid_call')),
                                      'confidence': _num(sh.get('confidence'))},
                         'candidate_action_count': rec.get('candidate_action_count'), 'actual_tool': actual,
                         'latency_ms': _num(sh.get('latency_ms')), 'y': None, 'label': None})
    return run.finish(rows, 'decision_id')


# ----------------------------------------------------------------------------- DSH jev receipts
JEV_FIELDS = ('tier', 'specialty', 'model_routing', 'selected_provider', 'selected_model', 'selected_effort')
JEV_FLAGS = ('costly_mistake', 'kept_current', 'route_changed', 'effort_changed')


def _epoch(ts: Any) -> float | None:
    if isinstance(ts, (int, float)) and not isinstance(ts, bool):
        return float(ts)
    try:
        t = dt.datetime.fromisoformat(ts.replace('Z', '+00:00'))
    except (AttributeError, ValueError):
        return None
    return (t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)).timestamp()


def import_dsh_jev(path: Path, *, root: str | Path | None = None, now: float | None = None) -> dict[str, Any]:
    """jev_decision rows, with the lineage of the routed model_request that follows each of them, ->
    z0int.dsh.shadow_decision.v0 (y null: a JEV routing answer is never a label). Other model_request rows are
    counted (model_request_only).

    A jev_decision with no later line of its agent may still get its model_request (the same handler writes it
    next): it holds the watermark while it is within the last JEV_TAIL_LINES lines and younger than JEV_GRACE_S.
    Every other decision without a routed request (e.g. a crash between the two lines) is imported with an
    ``agent:<hash>`` session and counted no_lineage. A held decision never stops the rows after it.
    """
    now = time.time() if now is None else now
    run = _Run('dsh', 'dsh-jev', 'shadow_decisions.jsonl', root)
    lines = run.lines(_guard(path), 'dsh-jev')
    used: set[int] = set()
    rows = []
    for i, (offset, rec) in enumerate(lines):
        if rec.get('type') != 'jev_decision':
            continue
        agent, turn = rec.get('agentId'), rec.get('turn')
        later = [(j, r) for j, (_, r) in enumerate(lines[i + 1:], i + 1) if r.get('agentId') == agent]
        lineage = next(((j, r) for j, r in later if r.get('type') == 'model_request' and r.get('turn') == turn
                        and r.get('routed') and j not in used), None)
        written = _epoch(rec.get('ts'))
        if (lineage is None and not later and len(lines) - i - 1 < JEV_TAIL_LINES and written is not None
                and now - written < JEV_GRACE_S):
            run.hold('dsh-jev', offset)  # its model_request comes in the same handler: read it next run
            continue
        if lineage is None:
            if run.is_new('dsh-jev', offset):
                run.count('no_lineage')
            session = f'agent:{_h(agent, 12)}'
        else:
            used.add(lineage[0])
            session = lineage[1].get('session_id')
        reason = rec.get('fallback_reason')
        decision = {**{k: _id(rec.get(k)) for k in JEV_FIELDS}, **{k: rec.get(k) is True for k in JEV_FLAGS},
                    'difficulty': _num(rec.get('difficulty')),
                    'fallback': _id(str(reason).split(':', 1)[0]) if reason else None}
        rows.append({**_base('dsh', 'dsh-jev', session, str(turn), rec.get('ts')),
                     'decision_id': _h({'session': session, 'turn': turn, 'ts': rec.get('ts')}, 20),
                     'challenger': {'id': 'jev', 'policy': _id(rec.get('policy'))}, 'decision': decision,
                     'confidence': _num(rec.get('confidence')), 'latency_ms': _num(rec.get('jev_latency_ms')),
                     'y': None, 'label': None})
    run.count('model_request_only', sum(1 for j, (off, r) in enumerate(lines) if r.get('type') == 'model_request'
                                        and j not in used and run.is_new('dsh-jev', off)))
    return run.finish(rows, 'decision_id')


# ----------------------------------------------------------------------------- CLI
def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog='z0int loop import', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='source', required=True)
    o = sub.add_parser('omp-v1')
    o.add_argument('--decisions', type=Path, required=True, help='receipts/decisions.jsonl')
    o.add_argument('--outcomes', type=Path, required=True, help='receipts/outcomes.jsonl')
    for name in ('omp-cognition-shadow', 'dsh-jev'):
        sub.add_parser(name).add_argument('--src', type=Path, required=True)
    for p in sub.choices.values():
        p.add_argument('--root', type=Path, default=None, help='Z0INT_HOME to write state/<harness>/ under')
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    if args.source == 'omp-v1':
        man = import_omp_v1(args.decisions, args.outcomes, root=args.root)
    elif args.source == 'omp-cognition-shadow':
        man = import_cognition_shadow(args.src, root=args.root)
    else:
        man = import_dsh_jev(args.src, root=args.root)
    print(json.dumps(man, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(_main())
