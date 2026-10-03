# Shadow loop: verified-loop v0

- **What:** The first verified-loop table built from the host's agent sessions. `loop-table*.jsonl` holds counts and hashed ids only, with no message text. It sits beside the sweep manifests, the result (`verified-loop-host-zer0.{json,md}`) and the z0intelligence#56 issue body before and after, plus an addendum.
- **When:** 2026-10-03.
- **How:** Built with the `feat/shadow-loop-v0` code on this fork, run against the local session stores.
- **Verdict:** `INSUFFICIENT_DATA`. 9 rows, 0 with an improvement opportunity, so no learning signal yet. The RFC addendum was posted to z0intelligence#56 (body updated 2026-10-03 04:10Z). `i56-body-after.md` is the local, path-redacted copy and `i56-body-before-20261003.md` is the original body.
- **Code branches:** `feat/shadow-loop-v0` and `feat/wire-loop-core-20261003` (both pushed).
