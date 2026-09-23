# Fixing the ruler: resolve-fast eval audit (2026-09-23)

Resolver, policy and verifier were frozen for this pass. Nothing in
`DEFAULT_POLICY`, slot eligibility, corroboration, provider order or acceptance
logic changed. The only question asked was: how much of the measured failure was
the resolver, and how much was the ruler?

## Eval cleanup

```
v1 dev count        2814
v1 VALID             551   (19.6%)
v1 rejected          ANSWER_DEPENDENT 1389 (49.4%)  WRONG_SLOT 520 (18.5%)
                     UNNATURAL 273 (9.7%)  ANSWER_LEAK 81 (2.9%)
                     -- the entire `context` family was ANSWER_DEPENDENT, 0 valid
v2 dev count         233   (regenerated with the gate at generation time)
v2 VALID              83   (35.6%)   <- still not trustworthy
v2 rejected          CORRUPT_VALUE 85 (36.5%)  PREFIX_ECHO 65 (27.9%)
v3 = v2 ∩ VALID      train 663  dev 83  sealed 71
dataset sha256       4a24f7678352520f4179812199867cf90f931c0c00ebd7293f078eb39873ac1e
```

## Frozen champion rerun (champion-991b531, eligibility=PATH-only, require_fs off)

| set | n | safe coverage | wrong | fallback | p50 TTFA | evidence |
|---|---:|---:|---:|---:|---:|---:|
| regression-17 (frozen) | 17 | 1 | **0** | 15 | ~96 ms | 21 |
| v1 dev (contaminated) | 156 | 2 (1.3%) | **91** | 43.6% | 405 ms | 88 |
| v2 dev (contaminated) | 154 | 1 (0.6%) | **6** | 95.5% | 419 ms | 80 |
| **v3 dev (credible)** | **83** | **1 (1.2%)** | **1** | 97.6% | 506 ms | 78 |

## Remaining real failure (v3, the only one)

```
question : where is the stage0 oracle loop located?
slot     : PATH
claimed  : /skills/pm-oracle-authoring/SKILL.md
correct  : /pm-stage0/oracle_loop.py
verify   : evidence      pointer: tool_calls#38483
root cause: MULTIPLE_PLAUSIBLE_CANDIDATES / VERIFIER_TOO_WEAK -- the claimed
            value is a real, source-backed path that shared the question's
            terms, and the uniqueness rule could not tell it from the answer.
            This is a genuine false claim, not eval contamination.
```

## Measurement conclusion

Most of the apparent unsafety was the ruler. On the contaminated v1 dev set the
champion looked like it fabricated on 58% of questions; on a gate-validated,
independently-critiqued ruler it fabricates on 1.2%. The v1 `context` family —
49.4% of the set — was answer-dependent by construction, and 18.5% of v1 had
slot labels that disagreed with the answer's own shape, so a large share of those
"verified-wrong" verdicts were the benchmark's fault, not the resolver's. But the
failure did not vanish: one genuine false claim survives on clean data, so the
champion still fails the zero-false-memory gate and cannot be promoted.

## Structural finding: value-derived questions cannot avoid echoing the answer

The dominant defect in v2 was not in the old vocabulary at all. Because the
question topic was built from the *value's own tokens*, 51/233 lines were the
ground-truth value re-emitted as the question with punctuation turned into spaces
(`CP036_TASK_MISSING` -> "which identifier is used for CP036 TASK MISSING?"), and
123/233 were token-prefix echoes. That is not a bug to patch: **any generator
that derives the question from the answer's tokens will leak the answer**, and one
that derives it from schema keys instead produces answer-dependent token salad
(the v1 `context` family). A trustworthy ruler therefore has to take its questions
from a source that never saw the answer.

## Vocabulary additions (adopted from the independent critique)

`PREFIX_ECHO`, `CORRUPT_VALUE`, `TEMPLATE_ARTIFACT`, `UNVERIFIABLE_EVIDENCE`.
`PREFIX_ECHO` is now the most-firing rejection in the gate. The critique pass was
run by a separate agent with the generation prompt unavailable to it, and it
found a defect the first gate missed -- which is the reason to keep that step.
