# Observer-first evaluation

Status: architecture contract for J2. This document builds on the existing `DecisionBackend`, decision-receipt, outcome-join and ABAB machinery. It does not declare a new backend authoritative.

## Thesis

JEV is the seed semantic observer/reference backend for evaluations.

It provides cheap typed semantic readings over replayable agent state. Those readings are features and weak labels. They are not verified truth.

```text
replayable events
   |
   +--> objective counters ---------------- Tokenomics
   |
   +--> semantic readings ----------------- DecisionBackend
   |       JEV / NanoJev / OpenJev /
   |       rules / ridge / MLP / fly / ...
   |
   +--> independent future outcome/verifier
   |
   v
joined evaluation example
   |
   v
Evolution Lab
   |
   +--> grouped split
   +--> calibration / proper scoring
   +--> cost / latency / coverage
   +--> frozen promotion gate
   |
   v
question-family specialist may enter runtime
```

## J1/J1.1 interpretation

Existing JEV/NanoJev parity work remains useful, but parity is not ground truth.

Record at least:

- exact state and question-pack identity;
- candidate set and its ordering/ids;
- backend/model/revision;
- complete probability distribution;
- confidence if supplied;
- latency/cost where known;
- repeat index for stability trials.

Repeated JEV runs should be used to estimate self-agreement, probability variance and threshold-flip rate. A high-risk NanoJev/JEV disagreement becomes a priority adjudication example rather than an automatic NanoJev error.

## Evaluation identity

An evaluation example needs enough lineage to prevent accidental leakage:

```text
trace_id
work_item_id
attempt_lineage_id
task_family
harness
environment
event_index
state_hash
question_pack_id
question_pack_version
backend
model
revision
repeat_index
```

The first implementation may extend the existing receipt/join system. Do not duplicate Tokenomics measurement semantics merely to create a new file format.

## Observer reading

Each question result should preserve:

```text
question_id
question_type
value
probabilities
confidence
latency_ms
diagnostics
```

Do not collapse a full distribution to an argmax when the backend produced more information.

## Independent outcome join

The observer cannot write the gold label that later judges it.

Useful outcome-grounded targets include:

- remaining wall time;
- remaining cost/tokens;
- retries and recovery path;
- artifact existence and verifier/test result;
- whether escalation/replan was subsequently required;
- user correction or explicit adjudication.

Existing `Outcome` semantics remain authoritative for the distinction between execution completion and verified success.

## First baseline experiment

For the same frozen work-item groups compare:

1. counters only;
2. semantic observer only;
3. counters + semantic observer.

Use a simple statistical baseline before more complex candidates. If ridge/logistic regression captures the useful signal, that is a valid win.

The question is not "does JEV sound sensible?" It is:

> Does the semantic reading add held-out predictive or decision value over the objective counters already available?

## Split discipline

Never random-split events from one work lineage.

```text
event
  -> trace
    -> attempt lineage
      -> work item
        -> task family
          -> harness/environment
```

At minimum, keep complete work items in one fold. Harder transfer tests should hold out full task families and eventually complete harnesses/environments.

## Metrics

For probabilistic questions use appropriate proper scoring and calibration diagnostics:

- Brier score or log loss where applicable;
- reliability/calibration error;
- self-agreement and threshold-flip rate;
- risk/coverage and abstention;
- false-confidence rate.

System-level comparison additionally records latency, cost, verified outcomes and downstream retries/corrections.

## Promotion

Promotion is per question family.

A candidate population may contain:

```text
deterministic rule
ridge/logistic model
small MLP
NanoJev
OpenJev/local semantic model
mushroom-body learner
fly/temporal specialist
other backend
```

Evolution Lab owns the frozen comparison and promotion gate. The simplest candidate satisfying the registered quality, calibration, cost, latency and safety constraints wins.

JEV teacher agreement may seed weak supervision. Verified outcomes should increasingly drive selection as evidence accumulates.

## Runtime boundary

A runtime policy is downstream of eval credit.

The bounded JEV policy in #12 therefore remains shadow/experimental until the corresponding question family clears this evaluation path. The deterministic AODL gate in #13 remains independent of learned observers.

## Security boundary

Neither JEV nor a distilled specialist grants authority.

Credential access, destructive operations, payments, deployment, privileged capabilities and other irreversible actions remain under deterministic policy, sandboxing, scoped credentials and explicit approval where required.

An observer may emit a warning feature. It cannot make an unsafe action legal.

## Ownership

- **z0intelligence** owns this typed observer/backend and replay-receipt contract.
- **Tokenomics** owns objective measurement and verified-outcome semantics.
- **Evolution Lab** owns experiment design, grouped splits, comparison and promotion.
- **frontier-kb** stores research evidence, failure modes and kill criteria.
- **AODL** may supply authored intent/workflow context but is not required in the observer path.
- **Kerdoios** may consume calibrated forecasts for placement but does not produce semantic labels.
- **Harnesses** execute and emit replayable events.

Tracked by #14.
