# Pre-registration: verification density v1 (re-label after the pipe fix)

Committed before any v1 label exists. Follows [v0](verification-density-v0.md); v0 result in
[../verification-density.md](../verification-density.md): every density verifier was demoted to low.

**Change under test.** `outcome_verifier` 0.2.0 + `verification_density` 0.1.1, with these fixes:

- test/lint exit codes are pipe-aware (`effective_exit`);
- install / locate / version segments are not test runs;
- probe detection is quote- and redirect-aware.

Verifier rules and design confidences are as in v0.

**When.** The first day on which `z0int outcomes density --since 7d --label-sample DIR` reports at
least 50 firings for a verifier. Only verifiers with >= 50 firings are labelled.

**Protocol.** Same as v0:

- seed 20260930, up to 60 firings per verifier;
- a separate agent labels blind from excerpts stored under `~/.z0int/research/verification-density/<date>/`;
- labels are holds / wrong / unsure.

**Bar.** The bar is unchanged: precision >= 0.90, n >= 50, unsure <= 20%. A verifier that passes is
added to `PROMOTED` in one commit that cites the labels' date and counts. Nothing else changes in
that commit.

**Also measured.** The same labeller labels a 60-row random sample of v0.2 `tests_in_turn` firings,
covering both polarities. The v0 signal was never precision-checked, and the pipe defect shows it
needs to be.

**Outcome.** T1 is the live verified share with the promoted set. Target >= 0.20, same definition as
v0, gh on.
