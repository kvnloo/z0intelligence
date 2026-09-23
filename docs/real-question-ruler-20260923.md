# The real-question ruler, and what actually distinguishes the answer (2026-09-23)

Frozen champion (`champion-991b531`) and `DEFAULT_POLICY` were **not modified**.
This pass only built a new ruler, measured with it, and ran exactly one
disambiguation challenger.

## Dataset

Mined from `AgentsView sessions.db` (`messages`, `tool_calls`). The user turn is
the question, verbatim; the answer is bound to downstream source evidence.

```
sessions with tool calls scanned      5587
user turns scanned                   22683
utterances after naturalness filter   3833
slot-cued real requests                913
mechanically bound pairs                50   (10 distinct after near-copy collapse)
bound pairs surviving critique           0   <-- every one rejected
ambiguity bucket                        70   (real question, answer named >=2 candidates)
ambiguity items surviving both critics  37   -> 4 distinct questions
final scored ruler                       4
dataset sha256   928d7022af7d220612e9da629deeefdebd42de20ff4ca163b2488a27d9216150
splits           train 0 / dev 4 / sealed 0
```

### Why each loss happened (nothing silently discarded)

| stage | lost | reason |
|---|---:|---|
| user turn → utterance | 18850 | not a question (imperatives, "continue", "yes") |
| utterance → slot-cued | 2920 | no request for a slot-typed value |
| slot-cued → bound | 281 | no evidence in the window |
| | 173 | no assistant narration at all (harness records only tool calls) |
| | 168 | no slot-shaped value anywhere in the window |
| | 151 | assistant narration names no slot-shaped value |
| | 70 | assistant answer names **several** candidates → ambiguity bucket |
| deterministic gate | 40 | action request / value shape |
| near-duplicate collapse | 40 | same question forked across resumed sessions |
| **independent critique** | **10** | **all 10 bound pairs rejected** |

Reject codes are in `benchmarks/fixtures/resolve-real-v1/candidates.jsonl`.

### The one real structural finding about the corpus

Real memory questions are **rare**: 913 slot-cued requests in 22,683 user turns
(4.0%), of which 50 could be determinately bound (0.22%), of which 10 were
distinct (0.044%). This corpus cannot supply a large real-question ruler; that,
not effort, is the binding constraint.

## The independent critic rejected 100% of the bound pairs

Two rounds, six separate agents, no access to the binding logic:

```
pass 1   4 agents x 19 items:  10 "accepted" pairs -> 10 rejected
pass 2   2 agents x 14 items:  critic A rejects 10/10 ; critic B rejects 7/10
          inter-agent agreement on the same 14 items: 11/14
```

Survivors (both pass-2 critics agree, all four are URL/link questions):

| id | question | answer | rivals |
|---|---|---|---|
| real-S00 | can i see the website progress? link me to the design page | `http://localhost:5173/design.html?theme=living-night` | 1 |
| real-S01 | is the dev console live rn? what is the https link i can view it at? | `https://<tailnet-host>:8443/dev.html?mode=command` | 1 |
| real-S02 | where can i go to see the ui-cohesion latest dev portal on localhost? | `http://localhost:5173/dev.html` | 1 |
| real-S03 | (progress update request, URL ask embedded) | `http://localhost:5173/dev.html` | 3 |

All four come from **one session** (`<one codex session>`). The
ruler is therefore single-lineage and cannot measure session-level
generalisation; that is recorded in the manifest.

Two pointer defects were caught by the tests written for this fixture, not by me:
the citations had been recorded as *session-relative ordinals* mislabelled as
global message ids, so they resolved to unrelated rows. `freeze_real_questions.py`
now resolves `(session, ordinal) -> messages.id` via the archive, and
`tests/test_real_question_ruler.py` fails if any frozen citation stops containing
its own answer. A citation a reviewer cannot resolve is not a citation.

The dominant rejection reason was the same in every agent's report: **the bound
value was an artifact of the agent's own tool call** — a path from a `write_file`
record, a commit hash from a license commit, a directory named in a kanban card —
rather than an answer to anything the user asked. My binder's truth signal ("the
assistant's narration names exactly one candidate") is satisfied by *incidental
mentions*, so it had a 0% precision rate on this corpus.

## Frozen champion baseline (unchanged)

| set | n | safe coverage | wrong | fallback | p50 TTFA | evidence |
|---|---:|---:|---:|---:|---:|---:|
| real-v1 dev (new) | 4 | 0 (0.0%) | **0** | 100% | — | 87 |
| v3 dev (synthetic, credible) | 83 | 1 (1.2%) | **1** | 97.6% | 415 ms | 78 |
| v2 dev (contaminated) | 154 | 1 (0.6%) | **6** | 95.5% | 422 ms | 79.5 |
| v1 dev (contaminated) | 198 | 0 (0.0%) | **25** | 87.9% | — | 87 |
| regression-17 | — | — | — | — | — | — |

`regression-17` **cannot be rerun**: it is not in the repository or its history.
The previous report quoted numbers from a fixture that was never committed. That
is a reproducibility gap in the prior result, and it is recorded rather than
papered over.

The v3 numbers reproduce the previous pass exactly (1/83, wrong 1, same
`stage0 oracle loop` failure), so the frozen scorer is stable.

On the new ruler the champion **falls back on all 4 items**. It is safe, and the
ruler currently tells us nothing about it: coverage 0 means there is no signal
below the gate.

## Ambiguity findings — what distinguishes correct from plausible-wrong?

Ambiguity corpus: 70 real questions whose answer text named ≥2 slot-shaped
values, 194 candidates, mean 2.77 (URL 47 cases, PATH 14, ISSUE 7, REVISION 2).

For the 4 frozen items, every candidate was profiled for the provenance signals
the brief names (in tool arguments, is a file path, edited after, read after,
appeared in a tool result, first-seen ordinal, tool identity) and for the lexical
overlap of its *local label* with the question.

```
cases where provenance separates truth from every rival : 4/4
  ... but the separating signal is only `first_ordinal`  : 3/4
cases where a causal signal (args/result/tool) separates  : 1/4
cases where same evidence pointer carried truth+rivals    : 0/4
cases where local-label overlap separates                : 1/4
```

**Enumeration order is not evidence.** In three of four cases the only thing that
differs between the true URL and its rival is that the true one was printed
first. In S01 the truth and the rival (`:8443` and `:8444`) both arrive through
the same tool argument and both share every label term with the question — the
disambiguator there is the assistant's *sentence*, "8443 primary, 8444 fallback",
which is attribution, not interaction structure. In S02 both candidates were read
and produced by the same tool family; what separates them is the label
"Latest `ui-cohesion` dev portal" versus "Current mobile-control feature
preview", and lexical overlap with the question **ties (8 vs 8)**.

So the answer to the brief's question is: on the observed data, **no deterministic
provenance signal establishes answerhood**. Candidates that arrive in the same
tool result share all provenance. The discriminator is the local attribution
text, and topical overlap cannot read it because the candidates share vocabulary
by construction. This is the same failure shape as the `stage0 oracle loop` case
in v3 — two real, source-backed values, one question.

## One experiment

`scripts/provenance_disambig.py` — smallest deterministic challenger over the
critic-identified candidate field; weights from the brief (edit 3, read 2, tool
argument 2, explicit result 1, earliest 1). A tie resolves to **no answer**.

| | champion (unchanged) | provenance challenger |
|---|---:|---:|
| correct | 0 | 2 |
| wrong | **0** | **1** |
| unresolved (tie) | 0 | 1 |
| fallback | 4 | 0 |
| n | 4 | 4 |

The challenger picks `http://localhost:5174/` over the true
`http://localhost:5173/design.html?theme=living-night` — a fresh false claim,
manufactured by trusting provenance over attribution.

The same challenger over the *wide* field (every slot-shaped value the resolver's
own extractor produces after the question) is worse: 363–931 candidates per
question, and it resolves to nothing on all four because everything ties at 4.

## Verdict

**REJECT** the deterministic provenance challenger. It introduces a false claim
on the only ruler where the truth is independently established, and the hard gate
(`verified_wrong == 0`) fails for it. The champion is unchanged and is still not
promoted: it fails the gate on v3 dev, and on the new ruler it is safe only
because it declines everything.

`NEEDS_MODEL_PROBE` is the honest state of the *hypothesis*, not of this
challenger: deterministic evidence was measured and is insufficient, which is
exactly the condition the brief names for moving to the navigator probe. That is
the next step, not this one.

## Next action

Enlarge the critic-validated real-question ruler — the URL/link class is the only
one that survived — to at least ~30 confirmed items before running any
disambiguation probe, because at n=4 no challenger can be separated from the
champion.

## Artifacts

```
benchmarks/fixtures/resolve-real-v1/{train,dev,sealed,candidates}.jsonl + manifest.json
scripts/mine_real_questions.py     question mining + downstream binding
scripts/gate_real_questions.py     deterministic gate + ambiguity profile
scripts/build_critic_pack.py       redacted pack for the independent critic
scripts/freeze_real_questions.py   critic-gated freeze + manifest
scripts/analyze_ambiguity.py       Part 6 measurement, provenance vs attribution
scripts/provenance_disambig.py     Part 7 challenger
.work/critic-verdicts-*.json       raw independent-critic output (not committed)
```
