"""Seam diagnostics over a frozen memory cohort, not native-agent acceptance.

This runner observes retrieval, returned context, abstention and replay.
It invokes neither a native harness nor a model. A retrieved supporting claim
does not establish provider-input visibility or a source-supported answer.
Native study receipts require separate transport and answer observations.
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

HARNESSES = ('dsh', 'hermes', 'omo', 'omp', 'claude-code', 'codex', 'grok')
DIAGNOSTIC_SCHEMA = 'z0int.memory_seam_diagnostic.v1'
PULL_ONLY = {'grok': 'pull-only (no push seam)'}
EVAL_ENDPOINT = 'http://127.0.0.1/eval-fixture'  # no model is called; the gate sees a local endpoint
# The cohort's synthetic sources as AgentsView sessions, one harness each (cross-harness recall).
AGENTS = ('deepseek-harness', 'hermes', 'omo', 'omp', 'claude', 'codex', 'grok')
_CLAIM_RE = re.compile(r'^([\w.-]+)=(\S+)$')


# ----------------------------------------------------------------------------- the synthetic substrate
def seed_cohort(cohort: Mapping[str, Any], *, av_db: str | Path, ledger_root: str | Path, project: str = 'z0') -> None:
    """Write the cohort's evidence into a NEW synthetic AgentsView DB and an explicit z0 ledger.

    Repeated ``name=value`` facts become untrusted BitemporalClaims, not attested user corrections.
    Recency cannot establish supersession: a frozen cohort expecting that behavior remains a failed
    diagnostic. Other evidence becomes synthetic messages. No real transcript text is imported.
    Existing DB files are refused and there is no default ledger.
    """
    import sqlite3
    av_db = Path(av_db)
    if av_db.exists():
        raise FileExistsError(f'refusing to seed an existing database: {av_db}')
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


def _b_note(harness: str, push_supported: bool | None) -> str:
    if harness in PULL_ONLY:
        return f'B=UNSUPPORTED: {PULL_ONLY[harness]}'
    if push_supported is False:
        return 'B=UNSUPPORTED: the host API has no model-visible context hook (stop condition)'
    return 'B=UNVERIFIED: seam result only, no model request observed'


def run(harness: str, cohort: Mapping[str, Any], *, revision: str,
        push_supported: bool | None = None, endpoint: str = EVAL_ENDPOINT,
        session_id: str | None = None, cwd: str | None = None) -> list[dict[str, Any]]:
    """Exercise the common seam; the named harness selects its gates, not a client.

    ``revision`` identifies the z0int code under test, not a native harness.
    ``cwd`` scopes recall; no task directory means no project scope.
    """
    if harness not in HARNESSES:
        raise ValueError(f'Unknown memory seam harness: {harness}')
    session = session_id or f'eval-{int(time.time())}'
    out = []
    for q in cohort['questions']:
        key = f'{harness}:{session}:{q["id"]}'
        t0 = time.perf_counter()
        res = seam.turn(harness, turn_key=key, query=q['prompt'], mode='on', endpoint=endpoint, cwd=cwd)
        latency = round((time.perf_counter() - t0) * 1000, 3)
        replay = seam.turn(harness, turn_key=key, query=q['prompt'], mode='on', endpoint=endpoint, cwd=cwd)
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
        note = _b_note(harness, push_supported)
        layers = [k for k in ('temporal', 'lexical', 'semantic') if k in ((brief.get('receipt') or {}).get('capability_ids') or [])]
        row = {
            'schema': DIAGNOSTIC_SCHEMA, 'harness': harness, 'question_id': q['id'],
            'session_id': session, 'trace_id': key, 'z0int_revision': revision,
            'evidence_scope': 'common_seam_only',
            'model_request_observed': False, 'model_answer_observed': False,
            'retrieval_capability': 'z0-memory:' + '+'.join(layers or ['none']),
            'retrieval_ok': bool(brief) and not brief.get('abstained') and (need_hit or q.get('expected_action') == 'abstain'),
            'context_supports_expectation': supported, 'surface_verified': bool(verified),
            'retrieval_abstained': abstained, 'seam_emitted_context': bool(context),
            'duplicate_context': replay.get('outcome') == 'injected',
            'evidence_refs': [_ref(r) for r in brief.get('evidence_refs') or []],
            'latency_ms': latency, 'retrieval_latency_ms': float((brief.get('receipt') or {}).get('retrieval_latency_ms') or 0),
            'context_bytes': len(context.encode('utf-8')),
            'raw_source_reads': int((brief.get('receipt') or {}).get('raw_source_reads') or 0),
            'evidence_count': len(brief.get('evidence') or []),
            'notes': [note, 'C=UNVERIFIED: no model answer observed',
                      f'seam outcome {res.get("outcome")}', f'replay outcome {replay.get("outcome")}'],
        }
        if q.get('kind') == 'temporal':
            row['superseded_evidence_absent'] = supported and not any(
                _supported(context, claims, e['claim']) for e in cohort.get('evidence') or []
                if e['source_id'] in q.get('evidence_ids', []) and e['claim'] not in expected)
        out.append(row)
    return out




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
