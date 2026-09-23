# confirmed-ruler — the personal ruler, built from confirmed outcomes only

This ruler exists because the previous personal binder inferred answerhood from
**post-question proximity** ("the assistant mentioned this value near the
question") and an independent critic rejected **100%** of what it produced. That
causal assumption is false and is not used here.

A pair may enter this ruler only through a **confirmation tier**:

| tier | relation | may it be truth? |
|---|---|---|
| **A** | mechanically verified outcome — the candidate was *consumed* by a real command whose success depends on it being the requested value | yes |
| **B** | explicit user confirmation or correction (carries a rejection *and* an acceptance) | yes |
| **C** | authoritative source-native relation, e.g. `git ref → commit`, verified against the live authority | yes |
| **D** | the assistant says so; an LLM judge or critic believes it | **no** — counted, never written to truth |

Ambiguity is preserved, not adjudicated: one question with several equally
consumed candidates goes to the ambiguity bucket, never to ground truth.

## Result on this machine

```
sessions scanned                              7327
Tier A found                                     0
Tier B found                                     0
Tier C found                                     0
Tier D excluded (counted, not truth)          4750
correction pairs                                 0
verified-execution pairs                         0
ambiguous                                        0
duplicates removed                               0
final open / dev / sealed                 0 / 0 / 0
slot distribution                               {}
dataset sha256   e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
```

`e3b0c442…` is the SHA-256 of the empty string: the ruler is empty.

### Where every candidate was lost

```
TIER_A_NO_URL_QUESTION             141248   exec call with no URL-lookup question before it
TIER_A_NO_PATH_QUESTION            123791   exec call with no PATH-lookup question before it
TIER_A_NO_QUESTION_MATCH              981   candidate shares no distinctive artifact term with the question
TIER_A_AMBIGUOUS_ARTIFACT               7   the named artifact is NOT unique in the session
TIER_B_NO_PRIOR_CANDIDATE              22   correction marker, but no slot-typed candidate to correct
TIER_B_AMBIGUOUS_PRIOR                  5   the thing being corrected offered several candidates
TIER_B_CORRECTION_WITHOUT_ACCEPTED_VALUE 5  a correction that never states the accepted value
TIER_B_NO_PRIOR_ANSWER                  2   nothing to confirm
TIER_C_NO_REPO_IN_QUESTION              2   asked about a commit/branch without naming a repository
```

The tightening sequence is the finding. Each constraint removed real candidates
and each was necessary:

1. "discovered after the question, then executed successfully" → 70,011 chains
2. require the *program* to be in command position (not a `cd` target or a data
   argument) → 125
3. require a genuine information request for that slot → 47
4. require the distinctive term to be in the candidate's **basename** → 5
5. require the artifact to be **unique in the session** → **0**

At step 4 the survivors still failed on reading: an interpreter binary, a URL
fragment, a topic word. At step 5 the last seven — one question, several real
scripts consumed successfully — collapsed into "which of these two real scripts
did the user mean?", which is the semantic question a mechanical tier cannot
answer.

**Consumption proves a candidate was real and usable. It does not prove it
answers the question.** The missing link in the brief's own Tier-A chain is
`→ candidate identified`, and that link is semantic.

## Reproduce

```bash
export PYTHONPATH=$PWD/src
python scripts/mine_confirmed_pairs.py
```

Reads the AgentsView session archive at `$Z0INT_SESSIONS_DB` (default
`/mnt/zer0models/sft-svlm/data/agentsview/sessions.db`), read-only. Writes:

```
.work/confirmed-ruler.jsonl      accepted rows      (gitignored: private user turns)
.work/confirmed-ambiguous.jsonl  ambiguity bucket   (gitignored)
.work/confirmed-stats.json       same stats        (gitignored)
benchmarks/fixtures/confirmed-ruler/manifest.json  COMMITTED — statistics + protocol
```

The rows are **not committed**: they are verbatim private user turns, execution
receipts and session identifiers, and this repository is public. The ruler is
versioned by generator + `dataset_sha256` instead, which is reproducible from the
same archive.

Tests: `pytest tests/test_confirmed_ruler.py` (25 cases). They pin the tier
discipline, the schema, the ambiguity rule, and three real defects found while
building this:

* Hermes writes `-> exit 127`; the receipt check only knew `exit code: N` and
  `exited with code N`, so a **failed** command was being counted as a successful
  consumption;
* `HTTP_OK` matched only single-digit versions, so `HTTP/1.1 200` did not count as
  a successful fetch;
* `path_answers_question` matched any path segment, so a shared *parent directory*
  (`.../hermes-agent/venv/bin/python3`) counted as the artifact — this alone
  produced 47 junk "verified" pairs.

## What this means

The personal ruler is **still too sparse**, and the honest reading is that the
label does not exist in this history — not that more mining is needed. The
corpus records what was *said* and what was *run*; it does not record what was
*correct*.

The fix is prospective, not retroactive: future execution receipts should make
the chain explicit —

```
question → chosen capability → returned candidate → candidate consumed
        → verifier result → user correction → eventual successful value
```

— so that real traffic becomes clean evaluation data on its own instead of being
reconstructed from correlations.
