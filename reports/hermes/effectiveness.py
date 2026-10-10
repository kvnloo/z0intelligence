"""Read-only Hermes evidence accounting. A route label is not a model call."""
from __future__ import annotations
from collections import Counter
from z0int.receipt import effective_tier


def audit_native(rows):
    messages = [row.get('message') or {} for row in rows]
    assistant = [m for m in messages if m.get('role') == 'assistant']
    unique = {}
    unidentified = []
    for m in assistant:
        identity = m.get('responseId')
        if identity:
            unique.setdefault((m.get('provider'), identity), m)
        else:
            unidentified.append(m)
    completed = [m for m in unique.values()
                 if m.get('stopReason') not in ('error', 'aborted') and m.get('usage')]
    proposals = [c for m in assistant for c in m.get('content') or []
                 if isinstance(c, dict) and c.get('type') == 'toolCall']
    results = [m for m in messages if m.get('role') == 'toolResult']
    def denied(m):
        text = '\n'.join(c.get('text', '') for c in m.get('content') or [] if isinstance(c, dict))
        return bool(m.get('isError')) or 'Tool call denied by user' in text or 'reuse check did not allow implementation' in text
    costs = [(m.get('usage') or {}).get('cost', {}).get('total') for m in completed]
    costs = [x for x in costs if isinstance(x, (float, int)) and not isinstance(x, bool)]
    return {
        'assistant_rows': len(assistant), 'unique_response_ids': len(unique),
        'duplicate_response_rows': len(assistant) - len(unique) - len(unidentified),
        'unidentified_assistant_rows': len(unidentified),
        'completed_response_count': len(completed),
        'proposed_tool_calls': len(proposals), 'proposed_by_tool': dict(Counter(c.get('name') for c in proposals)),
        'tool_results': len(results), 'failed_or_denied_tool_results': sum(denied(m) for m in results),
        'successful_write_results': sum(m.get('toolName') == 'write' and not denied(m) for m in results),
        'token_sums': {k: (sum(m['usage'].get(k) or 0 for m in completed)
                          if any(m['usage'].get(k) is not None for m in completed) else None)
                       for k in ('input', 'output', 'cacheRead', 'cacheWrite', 'reasoningTokens', 'totalTokens')},
        'token_coverage': {k: sum(m['usage'].get(k) is not None for m in completed)
                           for k in ('input', 'output', 'cacheRead', 'cacheWrite', 'reasoningTokens', 'totalTokens')},
        'model_duration_ms': sum(m.get('duration') or 0 for m in completed),
        'duration_coverage': sum(m.get('duration') is not None for m in completed),
        'provider_reported_cost_coverage': len(costs),
        'provider_reported_cost_usd': sum(costs) if costs else None,
        'invoice_verified_cost_usd': None,
        'gap': 'Response IDs/usage establish observed completions, not independent served-model or invoice attestation. Tool success is not quality verification.',
    }


def audit_shadow(rows):
    attempts = [s for row in rows for s in row.get('shadow') or []]
    return {
        'raw_rows': len(rows),
        'compiler_only_rows': sum(not row.get('shadow') for row in rows),
        'deterministic_solution_rows': sum(row.get('deterministic_solution') is not None for row in rows),
        'backend_attempt_rows': len(attempts),
        'failed_backend_attempts': sum(bool(s.get('parse_error')) for s in attempts),
        'selection_shaped_returns': sum(s.get('selected_action') is not None and not s.get('parse_error') for s in attempts),
        'observed_executions': sum(row.get('executed_action') is not None for row in rows),
        'independently_attested_model_invocations': None,
        'attestation_gap': 'Backend-shaped selections/latency do not independently prove inference; transport failures are not model completions.',
        'backend_labels': dict(Counter(s.get('backend') or '<unknown>' for s in attempts)),
    }


def audit_decisions(rows, outcomes):
    latest = {row['trace_id']: row for row in rows if row.get('trace_id')}
    joined = {row['trace_id']: row for row in outcomes if row.get('trace_id')}
    gold = [key for key, row in joined.items()
            if effective_tier(row.get('outcome'), stored_tier=row.get('outcome_tier')) == 'gold'
            and (row.get('outcome') or {}).get('verification_source')]
    return {
        'raw_rows': len(rows), 'unique_traces': len(latest),
        'receipt_revisions_not_calls': len(rows) - len(latest),
        'compiler_planner_traces': sum(row.get('provider') == 'kerdoios_plan' for row in latest.values()),
        'estimated_tokens_avoided': sum(row.get('estimated_frontier_tokens_avoided') or 0 for row in latest.values()),
        'claimed_actual_tokens_saved': sum(row.get('actual_tokens_saved') or 0 for row in latest.values()),
        'measured_paired_savings': None,
        'actual_model_invocation_count': None,
        'actual_model_invocation_gap': 'DecisionReceipt alone cannot establish call count; join native requests/completions.',
        'independent_gold_traces': len(gold),
        'providers': dict(Counter(row.get('provider') or '<unknown>' for row in latest.values())),
        'measurement_states': dict(Counter(row.get('measurement_state') or '<unknown>' for row in latest.values())),
    }
