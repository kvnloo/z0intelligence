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

Pre-registered test (`benchmarks/decision_opportunity/falsification.py`, committed in 402d622 before any
run) on the 11 pinned held-out questions, deterministic arms only:

| arm | strict | answer | abstain | conflict | false ACT |
| --- | --- | --- | --- | --- | --- |
| packet alone | 2/11 | 0/8 | 2/2 | 0/1 | 0 |
| DecisionOpportunity, repo scope | 3/11 | 0/8 | 2/2 | 1/1 | 0 |
| **DecisionOpportunity, question scope** | **8/11** | 5/8 | 2/2 | 1/1 | **0** |

The contract survives its falsification rule. All three misses are ASK (the safe side) and trace to a
State Packet gap: sub-agent-only transcripts leave `conv.latest_session` unknown although the facts exist.
Caveats: n=11, one snapshot; the vocabulary was written after seeing one pinned prompt (not its key).

## Effect inference v0 (z0int#55): when does a turn need the human's authority?

Before this change, Claude Code emitted every prompt with `effects=("read",)`, so the gate could never ASK
for authority. For example, a `/loop` turn that pushes branches gated ACT. `z0int.effect_inference.infer_effects(request, packet)`
maps a request to a subset of read/write/privileged, with per-rule evidence (rule, phrase, packet fact).
It works like this:
- A deterministic lexicon runs over sentences. The vocabulary follows AODL `PRIVILEGED`, plus git, forge, release and messaging actions.
- Questions *about* an action count as read. Negated actions ("don't push") don't count.
- A commit while checked out on the default branch is privileged.
- A request that is ambiguous between write and privileged resolves to privileged, so the gate ASKs.
- User pre-approval phrases are recorded in `user_grant_phrases` and **never applied**.
- `Z0INT_EFFECTS_SLM=1` adds a groot qwen3-8b label as shadow only.

`on_opportunity` now uses the inferred effects. Its standing authority comes from the permission mode: `plan`
gives read, any other mode gives read+write. Privileged is never standing (`harness_grants` rejects it). The
record also keeps `gate_readonly_baseline`. Everything is still shadow.

Pre-registered evaluation: `benchmarks/effect_inference/PREREG.md` and its held-out addendum. The primary authority is read+write.

| set | arm | class acc | false ACT on privileged | unnecessary ASK on read |
| --- | --- | --- | --- | --- |
| v0 set (80, in-sample: author saw all cases) | read-only baseline | 26/80 | 30 | 0/26 |
| | inferred | 80/80 | 0 | 0/26 |
| | SLM alone (shadow) | 62/80 | 12 | 1/26 |
| **held-out (60, fresh agent, committed unread, scored once)** | read-only baseline | 20/60 | 20 | 0/20 |
| | **inferred** | **47/60** | **6** | **1/20** |
| | SLM alone (shadow) | 48/60 | 10 | 2/20 |

**On the held-out set, v0 fails its own decision rule.** It needs 0 false ACT on privileged and got 6 (the baseline has 20).
The other two criteria pass: unnecessary ASK is 5% (limit 10%) and class accuracy beats the baseline. Five of the six false
ACTs are privileged requests phrased in vocabulary the lexicon lacks: "send it up", "get this onto main", "npm i", "slack the team",
"wrap this up so X can review". The sixth, "commit straight to master", names the target branch in the text instead of the
packet, so it is also missed. The under-classifications fall back to read, because v0 treats an unknown imperative as
read. Post-hoc and exploratory (not registered): a privileged-if-either(deterministic, SLM) union reduces held-out false ACT
from 6 to 4 but raises unnecessary read ASKs to 3/20. It also fails.

Live cohort re-score (counts only, `results_v0_live_cohort.json`): before the change the table was ACT→answered 8,
ACT→asked 1, ESCALATE→answered 1. With inferred effects under read+write it is ACT→answered 2, ASK→asked 1, ASK→answered 6,
ESCALATE→answered 1. The single frontier ASK (the `/loop` default-branch push) is now gated ASK. The six ASK→answered are
turns whose prompt itself instructed the privileged action. v0 does not honour in-prompt grants, which is the next lever.
