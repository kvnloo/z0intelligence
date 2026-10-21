# Integration owner (Loop 3)

One change follows `Branch -> DraftPR -> Queued -> OnIntegrationBranch ->
Deployed -> OwnerAccepted -> Merged`. `Parked` requires a reason and a future
expiry; expiry returns it to the queue. `Conflicted` requires repair and requeue.
See [the statechart](integration-owner.mmd).

## Laws and boundaries

- I1: every open draft must be contained in its designated integration branch
  or explicitly parked by 24 h. `overdue` is a deadline monitor. This is not a
  proof that an external agent will eventually act: without a scheduler/fairness
  assumption a temporal liveness guarantee would be false. A missing branch
  produces `DRAFT_DEADLINE_UNVERIFIABLE`, never fabricated non-containment.
- I2: Deployed requires evidence of exact runtime/integration equality; a drift
  observation revokes both deployment and acceptance. A checkout SHA is not a
  runtime SHA, and an APK version is not a source SHA. v0 records checkout HEAD,
  local integration ref and fresh GitHub head separately. Without a process/build
  attestation it reports `I2_RUNTIME_UNATTESTED`, not a healthy deployment.
- I3: one integration owner per repo, with an expiring lease. An unexpired held
  lease cannot be stolen. Integration also requires a held lease. The checker
  reads explicit lease records and reports zero/multiple holders; it never takes
  a lease or modifies a branch. Absence of supplied records means unrecorded,
  not proof that no informal owner exists.
- I4: the child's recorded `baseRefOid` must contain the parent's current head.
  Parent PRs include non-drafts. Comparisons resolve immutable SHAs via GitHub;
  stale local remote-tracking refs are never silently used as current evidence.
  This detects an old synchronized base even when the named base branch advanced.
- I5: all events preserve the rule that a new Merged state can only come from
  OwnerAccepted. Acceptance needs an owner fact, not an agent using Kevin's
  GitHub credentials. Independent review is a separate integration guard.

`Parked` abstracts an external reason/expiry record with boolean guard facts;
`Expire` is emitted when the collector's clock says the record expired. Actual
reason text, expiry, head and decision identity live in the local decisions
file. The finite control state does not retain private prose. Lease ids/times
are Nat values. No event executes git, deploys a service or touches a phone.

Kernel-checked claims live in `bend/integration_owner_fsm/{fsm,LAWS,PROOF}.bend`.
The receipt covers Bend's emitted book, with `source_semantics_attested=false`.
The Python twin is a separately checked implementation, not a theorem of
source equivalence. Differential tests compare every prefix of seeded tapes,
all single-edge cases, deadline boundaries and leases; a deliberately sabotaged
test runner must diverge. Semantic mutations compile on their own but fail laws.

## Run

From the repository root with Python 3.10–3.13, Node and Bend 2.0.34:

    PYTHONPATH=src python -m pytest -q tests/test_fsm_integration_owner.py
    PYTHONPATH=src python -m z0int.fsm.integration_parity
    python bend/integration_owner_fsm/mutations.py
    hermes bend verify bend/integration_owner_fsm --receipt /new/path/receipt.json
    hermes bend replay bend/integration_owner_fsm/receipt.json --project bend/integration_owner_fsm
    PYTHONPATH=src python -m z0int.fsm.integrate --output /outside/repo/integration-20261021

The checker runs read-only `gh pr list`, `gh api`, `git status/rev-parse`,
`systemctl --user show`, `/proc/PID/cwd` and `ss`. No fetch, checkout, install,
input or service restart. GitHub/observation errors remain in `errors` and
produce exit 2, while inventory violations are data, not execution failures.
The checked-in service map names the six requested service/build targets.
Only the three explicitly supplied canonical integration branches are asserted.
Other repos deliberately require the integration agent to choose/register a
branch; default/preview/feature branches are not promoted by naming convention.

Optional `--decisions /local/decisions.json` has this shape (illustrative only):

    {
      "leases": [{"repo":"kvnloo/dash", "owner":"independent-integrator",
                  "expires_at":"2026-10-22T00:00:00Z"}],
      "parking": {"kvnloo/dash#38": {"head":"FULL_CURRENT_SHA",
                  "reason":"explicit owner decision", "decided_by":"Kevin",
                  "expires_at":"2026-10-22T00:00:00Z"}},
      "parking_requested": {"kvnloo/dash#38": true}
    }

These records are assertions supplied by an operator, not authenticated owner
proof. No synthetic acceptance records are consumed. A parking request makes
one batched owner decision; normal review, leases, conflict repair, telemetry
and branch designation stay with agents. A snapshot state never retroactively
attests a past lease, independent review, deployment or owner acceptance.

## First observation

The run's private JSON/TXT report lives under `data/operations/fsm/` outside
this checkout. The public study imports only allowlisted ids, short SHAs,
finding codes, aggregate counts and proof/parity results. In particular, stale
NOOP checkout evidence is not called proof of what the installed S25 app runs.
Hapsland snapshot tapes show observed initial states plus explicitly labelled
what-if guard probes; they are not invented historical git/deploy actions.
