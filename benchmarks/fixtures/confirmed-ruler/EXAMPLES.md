# Evidence strength: worked examples

Required output C. Each example states exactly why the relation does — or does
not — establish answerhood.

**Accepted fixtures: 0.** The miner produced none, so none are shown. Presenting
fabricated "accepted" items to fill this section would be the same error in a new
costume: manufacturing ground truth from correlation. The first three entries
below are the strongest candidates the mechanical rules *did* produce, each
labelled `NEAR-MISS — NOT ACCEPTED` with the precise link that is missing. The
rest are rejects from the gate and from the tiers.

---

## 1. NEAR-MISS — NOT ACCEPTED · Tier A · URL

**Question** (verbatim user turn, codex, ordinal 4465)

> where can i go to see the ui-cohesion latest dev portal on localhost? i can see
> it on my phone on port 8443 thru https tailscale link

**Candidate** `https://<tailnet-host>:8443/dev.html?mode=command`

**What the mechanical tier established**

* discovered after the question and not present in it (`tool_calls#175262`);
* later consumed by a real fetch (`tool_calls#175476`) whose output carries a
  successful HTTP status;
* shares the question's terms (`https`, `8443`).

**Why it does NOT establish answerhood**

The question asks where to go **on localhost**. The candidate is the
**Tailscale-facing** route. Reading the source passage settles it
(`inspect` on `agentsview:codex:019f53aa-…@4465`, context 5):

```
ordinal 4466  The feature worktree I'm validating right now is available locally at
              http://localhost:5210/dev.html?mode=control. Port 8443 is the
              HTTPS/Tailscale-facing route; ...
ordinal 4469  - **Latest `ui-cohesion` dev portal:** http://localhost:5173/dev.html
              - **Current mobile-control feature preview:** http://localhost:5210/dev.html?mode=control
              - **Phone/Tailscale HTTPS route:** `https://<host>:8443/dev.html`
              The Hermes and latest mobile fixes are currently on `5210`; they have
              not yet been promoted to the `ui-cohesion`/`5173` portal.
```

Three real, source-backed routes. The consumed one is the third. The correct
answer is `http://localhost:5173/dev.html`. This is the same question that
produced the single `verified_wrong = 1` on the earlier ruler — the same defect
seen from the other side, now confirmed by reading rather than by scoring.

**Missing link:** the candidate is *a* real URL in the passage, not *the* answer
to the question asked. Nothing in the event structure says which of the three.

---

## 2. NEAR-MISS — NOT ACCEPTED · Tier A · PATH

**Question** (hermes)

> where will this data live? will it live inside the sft svlm pipeline? or within
> hermes data…

**Candidate** `/workspace/hermes-home/hermes-agent/venv/bin/python3`

**What the mechanical tier established**

* discovered after the question; later executed as the program in command
  position (`python3 -m tui_gateway.entry`), receipt `-> exit 0`;
* the basename shares the term `hermes` with the question.

**Why it does NOT establish answerhood**

The matched term lives in a **parent directory**, not in the artifact's name, and
the artifact is an interpreter binary. The question asks where *data* will live;
successfully running Python proves the interpreter exists, not where data goes.
The relation is `question topic ~ directory name`, which is the topical-similarity
fallacy in path form. Rejected by the basename rule (47 → 5 candidates) and, had
it survived, by the question filter.

**Missing link:** a shared topic word is not an artifact identity.

---

## 3. NEAR-MISS — NOT ACCEPTED · Tier A · PATH (ambiguity)

**Question** (codex)

> ok can u commit everything push and use worktrees going forward? all work should
> be on separate worktrees…

**Candidates** `./ops/bootstrap_worktrees.sh` **and** `./ops/dev_forward.sh`

**What the mechanical tier established**

Both were discovered after the question and both were later consumed with a
successful receipt.

**Why it does NOT establish answerhood**

Two failures at once. The turn is an **action request**, not a request for a path
— there is no information need to answer. And even granting the slot, two real
scripts were consumed with equal standing; "which did the user mean?" is semantic.

This is the case that removed the last seven candidates: requiring the named
artifact to be **unique in the session** (`TIER_A_AMBIGUOUS_ARTIFACT`, 7) took the
ruler from 7 to 0. It is also why ambiguity is preserved rather than broken.

**Missing link:** the same as everywhere else — nothing mechanically identifies
*the* intended artifact.

---

## 4. REJECTED · Tier B · correction with no accepted value

**User turn** (omp)

> bruh i meant the openjev fork u npc

**Prior assistant candidates** `https://github.com/kvnloo/z0int`,
`https://kvnloo.github.io/z0int/`, `kvnloo/evolution-lab`

**Why it is rejected** — two independent reasons, both recorded:

* `TIER_B_AMBIGUOUS_PRIOR` — the thing being corrected offered three candidates,
  so the *rejected* value is unknown;
* `TIER_B_CORRECTION_WITHOUT_ACCEPTED_VALUE` — the user names a descriptor
  ("the openjev fork"), not a value, so the *accepted* value is unknown too.

This is exactly the class the brief calls ideal disambiguation data — a rejection
*and* an acceptance — and it is unusable here because neither side is a slot
value. Resolving it would require guessing which of three the user rejected and
which fork they meant.

**Missing link:** an explicit correction that never states either value.

---

## 5. REJECTED · Tier B · confirmation against an ambiguous prior

**User turn** (omp)

> yes thats the one! make sure we're using that skill & maybe we can wire it up w/
> jev too?

**Prior assistant candidates** `https://github.com/can1357/oh-my-pi/pull/12373`
(URL) and `#12373` (ISSUE) — the same referent in two slot shapes.

**Why it is rejected** — `TIER_B_AMBIGUOUS_PRIOR`. The confirmation is as explicit
as it gets, but "that one" does not select between the prior's candidates. Joining
them ("the PR and the issue are the same thing") is an interpretation I would be
supplying, not one the trace records.

Of 22 turns carrying a correction marker, this is the near-miss; the rest had no
slot-typed candidate to correct at all (`TIER_B_NO_PRIOR_CANDIDATE`) — e.g. *"i
mean you literally copied omp earlier, but then there were some glitches…"*,
which corrects a behaviour, not a value.

**Missing link:** the confirmation does not disambiguate.

---

## 6. REJECTED · Tier C · authoritative source, but the label is diurnal

**User turn** (codex) — appears three times in the archive

> What is the current git branch in /workspace/zer0/products/alpha? Just run git
> branch and report.

This is the one genuinely Tier-C-shaped question in the corpus. The repository
exists, and `git -C /workspace/zer0/products/alpha branch --show-current` resolves
against a live authority right now — exactly the "git ref → exact commit"
relation the brief describes.

**Why it is rejected** — the question asks for **current state**. The authority
supplies the branch *today*; it cannot supply the branch *at the time of asking*.
Accepting today's value would be retrofitting certainty into a trace that never
recorded it, and if the branch has moved the label is simply wrong. The same test
is applied to every Tier-C candidate: the live value must still be corroborated
within the session, or the item is dropped.

**Missing link:** a time-varying relation needs a timestamped source, not a live
query.

---

## 7. REJECTED · Tier C · no source encodes the relation

**Question** — same ui-cohesion portal question as example 1.

A Tier-C answer would need a source in which the relation "this worktree → this
port" is *defined* rather than merely mentioned: a `vite.config.*` at the worktree
root. There is none. The port appears across `tools/console/server.mjs`,
`tools/ui-audit/*.mjs`, `packages/ui-foundation/runtime-links.test.mjs` and
prose. No single authority defines it, so no source-native relation can be cited.

**Missing link:** the source relation does not exist; only commentary about it
does.

---

## 8. REJECTED · Tier A · bogus consumption receipt

**Question** (hermes)

> I haven't setup Google cloud app yet can u link me to where I can set it up

**Candidate** `/google/callback`

**Why it is rejected** — the value is a fragment of a URL (`…/google/callback`)
harvested as a PATH, and the "consumption" is an unrelated `printf 'MCP AUDIT'`
that exited 0. Both sides are artifacts of extraction. The earlier receipt check
also accepted `-> exit 127` as success, which is how a *failed* execution was once
counted as consumption.

**Missing link:** neither the candidate nor the receipt concerns the question.
