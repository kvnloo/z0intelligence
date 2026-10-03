"""Memory acceptance rows (z0evals#56 cases A-F) for one harness over the frozen 6-question cohort.

A retrieval (``retrieval_ok``), B injection into the model-visible request (``injected``), C answer support from
the injected evidence (``answer_supported``, ``verified``), D abstention (``abstained``), E idempotency
(``duplicate_injection``: the same turn replayed), F supersession (``superseded_answer``).

dsh, hermes, omo and omp emit ``z0eval.unified_memory_receipt.v0`` rows (the harness enum of z0evals fb14919
receipt.schema.json is exactly those four). claude-code, codex and grok emit ``z0int.memory_acceptance.v0``
("z0 memory acceptance", the same fields) and are refused the study label until the study is amended (C12).

B is honest about what was observed: the seam result alone is ``B=UNVERIFIED`` (no model request seen); a
recorded model request carrying the brief (``observed``) makes it ``B=VERIFIED``; a harness without a push seam
(Grok, OMO without a senpi ``context`` hook) is ``B=UNSUPPORTED`` and never counts as injected.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..memory_contract import BitemporalClaim, MemoryScope
from . import seam, surface

STUDY_HARNESSES = ('dsh', 'hermes', 'omo', 'omp')  # z0evals fb14919 receipt.schema.json properties.harness.enum
Z0_HARNESSES = ('claude-code', 'codex', 'grok')
STUDY_SCHEMA = 'z0eval.unified_memory_receipt.v0'
ACCEPTANCE_SCHEMA = 'z0int.memory_acceptance.v0'
LABEL = 'z0 memory acceptance'
PULL_ONLY = {'grok': 'pull-only (no push seam)'}
EVAL_ENDPOINT = 'http://127.0.0.1/eval-fixture'  # no model is called; the gate sees a local endpoint
# The cohort's synthetic sources as AgentsView sessions, one harness each (cross-harness recall).
AGENTS = ('deepseek-harness', 'hermes', 'omo', 'omp', 'claude', 'codex', 'grok')
_CLAIM_RE = re.compile(r'^([\w.-]+)=(\S+)$')


# ----------------------------------------------------------------------------- the synthetic substrate
def seed_cohort(cohort: Mapping[str, Any], *, av_db: str | Path, ledger_root: str | Path | None = None,
                project: str = 'z0') -> None:
    """Write the cohort's evidence into a synthetic AgentsView DB and the z0 ledger: ``name=value`` claims that
    share a name become scoped BitemporalClaims (recorded at their observed_at, so the newest is current); every
    other claim is one message (the claim, then its cohort locator) of a session of its own harness. No real
    transcript text."""
    import sqlite3
    av_db = Path(av_db)
    av_db.parent.mkdir(parents=True, exist_ok=True)
    evidence = list(cohort.get('evidence') or [])
    names: dict[str, int] = {}
    for e in evidence:
        m = _CLAIM_RE.match(str(e.get('claim')))
        if m:
            names[m.group(1)] = names.get(m.group(1), 0) + 1
    scope = MemoryScope(user=surface.DEFAULT_USER, project=project)
    conn = sqlite3.connect(av_db)
    conn.executescript("""
create table if not exists sessions (id text primary key, project text not null, machine text not null default 'local',
  agent text not null default 'claude', started_at text, ended_at text, cwd text not null default '', file_path text,
  message_count integer not null default 0);
create table if not exists messages (id integer primary key, session_id text not null, ordinal integer not null,
  role text not null, content text not null, timestamp text, unique(session_id, ordinal));
create virtual table if not exists messages_fts using fts5(content, content='messages', content_rowid='id',
  tokenize='porter unicode61');
create trigger if not exists messages_ai after insert on messages begin
  insert into messages_fts(rowid, content) values (new.id, new.content); end;
""")
    for n, e in enumerate(evidence):
        m = _CLAIM_RE.match(str(e.get('claim')))
        if m and names[m.group(1)] > 1:
            surface.record_claim(BitemporalClaim(
                claim_id=str(e['source_id']), scope=scope, subject=m.group(1), predicate='value', value=m.group(2),
                status='observed', observed_at=e['observed_at'], recorded_at=e['observed_at'],
                origin_trust=str(e.get('trust_class') or 'unknown')), ledger_root=ledger_root)
            continue
        sid = 'cohort-' + hashlib.sha256(str(e['source_id']).encode()).hexdigest()[:12]
        conn.execute('insert or ignore into sessions (id, project, agent, started_at, cwd, message_count) '
                     'values (?,?,?,?,?,1)', (sid, project, AGENTS[n % len(AGENTS)], e['observed_at'], f'/w/{project}'))
        conn.execute('insert into messages (session_id, ordinal, role, content, timestamp) values (?,?,?,?,?)',
                     (sid, 0, 'assistant', f"{e['claim']} [{e.get('locator') or e['source_id']}]", e['observed_at']))
    conn.execute('pragma user_version = 113')
    conn.commit()
    conn.close()


# ----------------------------------------------------------------------------- scoring
def _supported(text: str, claims: Iterable[Mapping[str, Any]], support: str) -> bool:
    if support in text:
        return True
    m = _CLAIM_RE.match(support)
    return bool(m) and any(c.get('subject') == m.group(1) and str(c.get('value')) == m.group(2) for c in claims)


def _ref(ref: Mapping[str, Any]) -> dict[str, Any]:
    return {'source_id': str(ref.get('source_id') or ''), 'source_version': str(ref.get('source_version') or ''),
            'trust_class': str(ref.get('trust_class') or 'unknown'),
            'locator_hash': 'sha256:' + hashlib.sha256(str(ref.get('locator') or '').encode()).hexdigest()[:16]}


def _b_note(harness: str, push_supported: bool | None, observed: bool | None, injected: bool) -> tuple[bool, str]:
    if harness in PULL_ONLY:
        return False, f'B=UNSUPPORTED: {PULL_ONLY[harness]}'
    if push_supported is False:
        return False, 'B=UNSUPPORTED: the host API has no model-visible context hook (stop condition)'
    if observed is None:
        return injected, 'B=UNVERIFIED: seam result only, no model request observed (fixture run, not counted)'
    if observed and injected:
        return True, 'B=VERIFIED: the brief was in the recorded model request'
    return False, 'B=FAILED: the brief was not in the recorded model request'


def run(harness: str, cohort: Mapping[str, Any], *, revision: str, schema: str | None = None,
        observed: Mapping[str, bool] | None = None, push_supported: bool | None = None,
        endpoint: str = EVAL_ENDPOINT, session_id: str | None = None) -> list[dict[str, Any]]:
    """One row per cohort question, through the harness's real seam path (gates, cache, replay)."""
    schema = schema or (STUDY_SCHEMA if harness in STUDY_HARNESSES else ACCEPTANCE_SCHEMA)
    if schema == STUDY_SCHEMA and harness not in STUDY_HARNESSES:
        raise ValueError(f'{harness} is not in the z0evals fb14919 harness enum {list(STUDY_HARNESSES)}: its rows are '
                         f'{ACCEPTANCE_SCHEMA} ("{LABEL}") until the study is amended (C12)')
    session = session_id or f'eval-{int(time.time())}'
    out = []
    for q in cohort['questions']:
        key = f'{harness}:{session}:{q["id"]}'
        t0 = time.perf_counter()
        res = seam.turn(harness, turn_key=key, query=q['prompt'], mode='on', endpoint=endpoint)
        latency = round((time.perf_counter() - t0) * 1000, 3)
        replay = seam.turn(harness, turn_key=key, query=q['prompt'], mode='on', endpoint=endpoint)
        brief = res.get('brief') or {}
        context = res.get('context') or ''
        claims = brief.get('current_claims') or []
        expected, forbidden = list(q.get('expected_support') or []), list(q.get('forbidden') or [])
        clean = not any(f in context for f in forbidden)
        hits = [s for s in expected if _supported(context, claims, s)]
        # D is scored on the question's needs: the brief abstains when it carries no evidence for any of them
        # (lexical recall may still add unrelated lines; their count is in evidence_count).
        needs = {n['id'] for n in q.get('needs') or []}
        need_hit = any(_supported(context, claims, e['claim']) for e in cohort.get('evidence') or []
                       if e.get('required_need') in needs)
        abstained = bool(brief.get('abstained')) or not need_hit
        if q.get('expected_action') == 'abstain':
            supported = abstained and clean
            verified = surface.verify(q['prompt'], requires=[n['id'] for n in q.get('needs') or []])['verdict'] == 'FALLBACK'
        else:
            supported = bool(expected) and len(hits) == len(expected) and clean
            verified = supported and surface.verify(q['prompt'], requires=[s for s in expected if s in context])['verdict'] == 'USE'
        injected, note = _b_note(harness, push_supported, None if observed is None else bool(observed.get(q['id'])),
                                 res.get('outcome') == 'injected')
        layers = [k for k in ('temporal', 'lexical', 'semantic') if k in ((brief.get('receipt') or {}).get('capability_ids') or [])]
        row = {
            'schema': schema, 'harness': harness, 'question_id': q['id'], 'session_id': session, 'trace_id': key,
            'harness_revision': revision, 'z0int_revision': revision,
            'retrieval_capability': 'z0-memory:' + '+'.join(layers or ['none']),
            'retrieval_ok': bool(brief) and not brief.get('abstained') and (need_hit or q.get('expected_action') == 'abstain'),
            'injected': injected, 'answer_supported': supported, 'verified': bool(verified), 'abstained': abstained,
            'duplicate_injection': replay.get('outcome') == 'injected',
            'evidence_refs': [_ref(r) for r in brief.get('evidence_refs') or []],
            'latency_ms': latency, 'retrieval_latency_ms': float((brief.get('receipt') or {}).get('retrieval_latency_ms') or 0),
            'context_bytes': len(context.encode('utf-8')) if injected else 0,
            'raw_source_reads': int((brief.get('receipt') or {}).get('raw_source_reads') or 0),
            'evidence_count': len(brief.get('evidence') or []),
            'notes': [note, f'seam outcome {res.get("outcome")}', f'replay outcome {replay.get("outcome")}'],
        }
        if q.get('kind') == 'temporal':
            row['superseded_answer'] = supported and not any(
                _supported(context, claims, e['claim']) for e in cohort.get('evidence') or []
                if e['source_id'] in q.get('evidence_ids', []) and e['claim'] not in expected)
        if schema == ACCEPTANCE_SCHEMA:
            row['label'] = LABEL
        out.append(row)
    return out


# ----------------------------------------------------------------------------- validation (no jsonschema dependency)
_TYPES = {'string': str, 'boolean': bool, 'number': (int, float), 'integer': int, 'array': list, 'object': dict}


def validate(value: Any, schema: Mapping[str, Any], path: str = '$') -> list[str]:
    """The JSON-Schema subset receipt.schema.json uses: type, required, const, enum, pattern, minimum, items,
    properties, additionalProperties=false. Returns the errors (empty = valid)."""
    errs: list[str] = []
    t = schema.get('type')
    if t:
        ok = isinstance(value, _TYPES[t]) and not (t in ('number', 'integer') and isinstance(value, bool))
        if not ok:
            return [f'{path}: expected {t}']
    if 'const' in schema and value != schema['const']:
        errs.append(f'{path}: expected const {schema["const"]!r}')
    if 'enum' in schema and value not in schema['enum']:
        errs.append(f'{path}: {value!r} not in enum {schema["enum"]}')
    if 'pattern' in schema and isinstance(value, str) and not re.search(schema['pattern'], value):
        errs.append(f'{path}: does not match {schema["pattern"]}')
    if 'minimum' in schema and isinstance(value, (int, float)) and value < schema['minimum']:
        errs.append(f'{path}: below minimum {schema["minimum"]}')
    if isinstance(value, dict):
        props = schema.get('properties') or {}
        errs += [f'{path}.{k}: required' for k in schema.get('required') or [] if k not in value]
        if schema.get('additionalProperties') is False:
            errs += [f'{path}.{k}: not allowed' for k in value if k not in props]
        for k, v in value.items():
            if k in props:
                errs += validate(v, props[k], f'{path}.{k}')
    if isinstance(value, list) and 'items' in schema:
        for i, v in enumerate(value):
            errs += validate(v, schema['items'], f'{path}[{i}]')
    return errs


# ----------------------------------------------------------------------------- Grok project rules (opt-in only)
def write_grok_rules(project_dir: str | Path, query: str, *, max_tokens: int = 600) -> Path:
    """Scrubbed, project-scoped brief as a Grok project rule (``<project>/.grok/rules/z0-memory.md``). Never a
    global file: Grok sends every rule to xAI and a global rule leaks across projects."""
    project_dir = Path(project_dir).resolve()
    scope = MemoryScope(user=surface.DEFAULT_USER, project=project_dir.name)
    brief = surface.memory_brief(query, surface.ScopePolicy(scope=scope, requester='grok', cross_harness=True),
                                 max_tokens=max_tokens, use_cache=False)
    path = project_dir / '.grok' / 'rules' / 'z0-memory.md'
    path.parent.mkdir(parents=True, exist_ok=True)
    text = seam.scrub_text(brief['text'])[0]
    path.write_text(f'<!-- z0-memory: project {project_dir.name}; snapshot {brief["memory_snapshot_id"]}; '
                    f'owner-approved -->\n{text}\n', encoding='utf-8')
    return path


def load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding='utf-8'))
