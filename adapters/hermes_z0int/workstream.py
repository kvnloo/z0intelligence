"""Hermes opt-in read-only continuation over existing canonical z0 events.

No store, admission, shared policy, cache, model call or execution permission.
Scope is supplied by the trusted host, not extracted from retrieved prose.
Work outcomes must join an unambiguous task-scoped reuse trace. Evidence is
returned with original event IDs/checksums; it is not an independently rerun
verifier and never widens authority.
"""
from __future__ import annotations
from z0int.memory.event_log import EventLog, EventLogCorruption
from z0int.memory_contract import MemoryScope
from z0int.memory.claims import project_claims
from z0int.decision_opportunity import build_decision_opportunity, deterministic_gate


def recover_workstream(scope: MemoryScope, *, ledger_root=None):
    if not isinstance(scope, MemoryScope) or scope.level != 'task' or not scope.user or not scope.repo:
        raise ValueError('explicit user/project/repo/task scope required')
    log = EventLog(ledger_root, read_only=True)
    try:
        events = [log.get(e.event_id, resolve_blob=True)
                  for e in log.iter_events(require_complete=True)]
    except (OSError, ValueError, EventLogCorruption) as exc:
        return {'schema':'hermes.workstream_recovery.v1', 'scope':scope.to_dict(),
                'coverage':'unavailable', 'outcomes':[], 'current_outcome':None,
                'execution_permission':False, 'gap':type(exc).__name__}
    requested = tuple(getattr(scope, key) for key in ('user', 'project', 'repo', 'task'))
    bindings = {}
    for event in events:
        p = event.payload or {}
        if event.event_type == 'reuse.prepared' and p.get('trace_id') and isinstance(p.get('scope'), dict):
            key = tuple(p['scope'].get(k) for k in ('user', 'project', 'repo', 'task'))
            bindings.setdefault(p['trace_id'], set()).add(key)
    visible = []
    for event in events:
        p = event.payload or {}
        if (event.event_type == 'work.outcome' and event.source == 'codex-independent-verifier'
                and event.project == scope.project and p.get('workstream') == scope.task
                and bindings.get(p.get('trace_id')) == {requested}):
            visible.append({'event_id':event.event_id, 'checksum':event.checksum,
                            'source':event.source, 'parent_event_ids':list(event.parent_event_ids),
                            'payload':dict(p)})
    claims = project_claims(scope, ledger_root=ledger_root).measurements
    history = [{key:row.get(key) for key in ('claim_id', 'event_id', 'current', 'superseded_by', 'origin_trust')}
               for row in claims.get('claim_history', [])]
    for item, row in zip(history, claims.get('claim_history', [])):
        value = row.get('value') if isinstance(row.get('value'), dict) else {}
        item['synthetic_test_input'] = value.get('synthetic')
        item['attribution'] = value.get('attribution')
    packet = {
        'schema':'hermes.read_only_continuation_evidence.v1',
        'current_claims':[{'key':'continuity.admitted_outcome', 'value':row['payload'],
                           'evidence':['eventlog:'+str(row['event_id'])+':'+row['checksum']]} for row in visible],
        'blocking_unknowns':[] if visible else [{'key':'continuity.outcome','reason':'no scoped admitted outcome','source_status':'unknown'}],
        'unknowns':[], 'contradictions':[], 'allowed_transitions':[],
        'source_revisions':{'canonical_eventlog_last_id':events[-1].event_id if events else None},
    }
    opportunity = build_decision_opportunity(scope.repo,
        'Read-only inspection of admitted functional verdict and unresolved strict qualification gates; no implementation or live execution',
        effects=('read',), packet=packet, harness='hermes', scoped=False)
    return {'schema':'hermes.workstream_recovery.v1', 'scope':scope.to_dict(),
            'outcome_lineage_rule':'distinct artifact/trace outcomes do not implicitly supersede one another',
            'next_legal_action':{'gate':deterministic_gate(opportunity),'opportunity':opportunity},
            'claim_projection_coverage':claims.get('coverage'),
            'selected_claim_ids':claims.get('selected_claim_ids', []), 'claim_history':history,
            'coverage':'complete', 'outcomes':visible,
            'current_outcome':visible[-1] if visible else None,
            'execution_permission':False,
            'source_last_event_id':events[-1].event_id if events else None,
            'gap':None if visible else 'no admitted independently sourced outcome with exact scoped trace binding'}
