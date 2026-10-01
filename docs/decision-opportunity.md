# DecisionOpportunity v0 (`z0int.decision_opportunity.v0`, z0int#53)

The per-decision unit of the verified learning loop (z0#15). It **wraps** a State Packet (evidence →
state) and adds only what the packet does not carry:

| Adds | Why |
| --- | --- |
| intent + revision | the user's request, bound to a revision id (changes when the request, effects or AODL contract change) |
| authority from AODL | `aodl-canon-1` fingerprint names the exact intent contract; an invalid/unfingerprintable contract grants nothing (fails closed to read-only) |
| legal action space | `ACT / OBSERVE / ASK / ABSTAIN / ESCALATE`, each with `blocked_by` / `about` |
| harness-independent `semantic_id` | same evidence + intent = same opportunity from Claude Code, Hermes or OMP; `trace.opportunity_id` identifies one observation |
| deterministic gate | `deterministic_gate()` — the fixed-priority baseline #55 must beat |

Invariants (tested in `tests/test_decision_opportunity.py`):
`evidence ≠ belief/state ≠ authority ≠ action ≠ outcome`; unknown ≠ false ≠ absent
(`source_unavailable` asks the user, `no_match` is an answer); authority widens **only** via
`with_authority_grant(granted_by_user=…)` — no API accepts model confidence; claims keep evidence refs;
a source-revision change changes `semantic_id`.

## Example (pinned z0intelligence fixture, trimmed)

```json
{
 "schema": "z0int.decision_opportunity.v0",
 "semantic_id": "686fde93b5c0c960003e",
 "intent": {
  "request": "Which session last edited the Bend kernel, and what should I do next on it?",
  "revision": "bf925913be87",
  "effects": [
   "read"
  ]
 },
 "authority": {
  "source": "harness-default",
  "grants": [
   "read"
  ],
  "fingerprint": null
 },
 "state": {
  "claims": [
   {
    "key": "git.branch",
    "value": "feat/claude-code-harness",
    "evidence": [
     "e:5e41569a31"
    ],
    "status": "observed"
   },
   {
    "key": "git.head",
    "value": {
     "sha": "24424d1",
     "date": "2026-09-30T03:11:20-05:00",
     "subject": "feat(claude-code): z0int claude-code launch/warm/profile \u2014 measured session profiles + prefix-cache residency"
    },
    "evidence": [
     "e:a9d61b8cc4"
    ],
    "status": "observed"
   }
  ],
  "contradictions": [
   {
    "key": "priority",
    "kind": "priority_conflict",
    "contests": "docs.priority"
   }
  ],
  "unknowns": [
   {
    "key": "conv.latest_session",
    "status": "unknown",
    "blocking": true,
    "reason": "no source produced this fact"
   }
  ]
 },
 "action_space": [
  {
   "kind": "ACT",
   "action": "proceed",
   "legal": false,
   "blocked_by": [
    "unknown:conv.latest_session",
    "contradiction:docs.priority"
   ]
  },
  {
   "kind": "ASK",
   "action": "ask_user",
   "legal": true,
   "blocked_by": []
  },
  {
   "kind": "ESCALATE",
   "action": "surface_conflict",
   "legal": true,
   "blocked_by": []
  },
  {
   "kind": "ABSTAIN",
   "action": "state_unknown",
   "legal": true,
   "blocked_by": []
  }
 ],
 "trace": {
  "harness": "claude-code",
  "trace_id": "example",
  "attempt": 0,
  "opportunity_id": "4542f08d5d240c057c27"
 }
}
```

## Question scoping and the #53 falsification test

`scoped=True` (default) maps the request to packet fact families (`FACT_FAMILIES`, authored from the
packet key namespace only); only unknowns/contradictions in those families block, and a required family
with no claim becomes an explicit unknown (`source_unavailable` when the source was never collected).

Pre-registered test (`benchmarks/decision_opportunity/falsification.py` on nightly / `integrate/claude-code-z0-stack`, committed in 402d622 before any
run) on the 11 pinned held-out questions, deterministic arms only:

| arm | strict | answer | abstain | conflict | false ACT |
| --- | --- | --- | --- | --- | --- |
| packet alone | 2/11 | 0/8 | 2/2 | 0/1 | 0 |
| DecisionOpportunity, repo scope | 3/11 | 0/8 | 2/2 | 1/1 | 0 |
| **DecisionOpportunity, question scope** | **8/11** | 5/8 | 2/2 | 1/1 | **0** |

The contract survives its falsification rule. All three misses are ASK (the safe side) and trace to a
State Packet gap: sub-agent-only transcripts leave `conv.latest_session` unknown although the facts exist.
Caveats: n=11, one snapshot; the vocabulary was written after seeing one pinned prompt (not its key).
