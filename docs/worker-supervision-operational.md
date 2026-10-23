# Bounded operational worker supervision

The scheduled desktop projection uses the existing engine with
`--operational-window 4000 --live-dir <existing-live-directory>`.
It does not discover repositories or run `git status` across worktrees. It reads
only messages in a captured primary-key interval `(max_id - N, max_id]`, recent
changed delegation/fleet logs, and session/delegation metadata. Existing unresolved
items and lease metadata outside the window are carried as explicit holds.
Missing bounded evidence is never completion, abandonment, or authorization.

The historical replay CLI remains unchanged by default. Run that only on demand
with an appropriate resource envelope; it is not suitable for a repeated HDD timer.
The bounded mode is not a full incremental historical index and its observed
job/violation counts are window-local, not lifetime totals. Recommendations from
bounded mode are `hold`, with no actionable decision line until an explicit
on-demand inspection resolves the missing evidence.

Regression test (through fleet admission, only when the worker is healthy):

```sh
fleet-build --node mbp --wait 0 -- env PYTHONPATH=src python3 tests/test_fsm_operational_window.py
```

Tests use synthetic SQLite rows and fail if repository discovery, inventory, or
worktree-state scans occur. They also assert the primary-key boundary and preserve
an older unresolved worker's dispatch hold. They do not run Bend, stress CPUs,
mutate real worker decisions, or certify a sustained desktop-latency guarantee.
