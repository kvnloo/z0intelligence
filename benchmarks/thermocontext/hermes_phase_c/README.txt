ThermoContext -> z0 ContextPacket -> native Hermes: Phase-C discriminator

Status: VALIDATED BUT NOT WIRED. Default-off: these scripts are invoked manually;
there is no installed plugin, runtime registration or background job. The bridge
replay never calls a provider. The separate native baseline makes one real call
ONLY with --execute and a legitimate credential file. This directory does not
change the frozen Phase A objective or verifier.

The benchmark asks whether a subset proposal can preserve the existing context
and authority boundary. It does not demonstrate useful thermal selection or
Hermes task efficacy. In Phase A, required IDs remain inside the frozen synthetic
problem. This future real-evidence interface pins host-required evidence outside
selection: that is a separately preregistered problem, not a Phase A score change.

selection_boundary.py reuses z0int.context_resolve.ContextPacket. The sampler
proposes optional IDs only. The host supplies legal/pinned IDs and token counting;
candidate output cannot grant authority, change intent or redefine the judge.
Selection rejects changed pools, unknown/ineligible IDs, aliases, absent pinned
evidence and over-budget rendered context. Rejection should retain the known-good
baseline at the future caller. The current module makes no runtime decision.

An empty optional selection is legitimate if pinned evidence suffices. Count the
entire rendered addition, including provenance and pinned evidence. Request-wide
budget and authority checks still belong to Hermes/AODL. Trust-class labels alone
do not establish source legality. The test counter measures fixture UTF-8 bytes,
explicitly NOT provider tokens; it is not evidence of savings or a real tokenizer.

development_pool.json contains source-cited excerpts from a public experiment
publication, not personalized histories or raw sessions. The factual task is to
reconcile R2-03's tested revision, verified outcomes, consumed request change and
scope, including whether it establishes a Hermes speedup. This data was inspected
while creating it: it is development evidence, never a protected holdout. Source
URLs, publication SHA and original cached file digests are in sources.json. No
network fetch is performed here. Full originals, if needed, must be acquired
through the repository's approved source-acquisition process.

The source pool is materialized into the existing native Hermes user-message
context and api_content sidecar. Tests use actual compose_user_api_content and
substitute_api_content from the pinned Hermes revision. The witness distinguishes
prepared context, exact presence in a constructed request, actual model use and
independently verified outcome. Only the first two are established here. The
request is never sent. Unknown consumption, outcomes and savings remain unknown.

Run from the z0intelligence root with its isolated test environment. Native Hermes
composition additionally requires that checkout's installed dependencies. The
environment must import this repository's src/z0int/context_resolve.py; the replay
checks the actual imported file hash. Use the exact Hermes revision recorded in
sources.json, or explicitly requalify and publish a new manifest.

  PYTHONPATH=src python -m unittest discover \
    -s benchmarks/thermocontext/hermes_phase_c -p 'test_*.py'

The native-composition test is explicitly skipped unless HERMES_SOURCE_TREE is set:

  HERMES_SOURCE_TREE=/path/to/hermes PYTHONPATH=src python -m unittest discover \
    -s benchmarks/thermocontext/hermes_phase_c -p 'test_*.py'

  PYTHONPATH=src python benchmarks/thermocontext/hermes_phase_c/replay_bridge.py \
    --hermes-repo /path/to/hermes --out /tmp/hermes-phase-c-run-1

Repeat with a fresh output directory: selection.json, wire-witness.json and
receipt.json must match byte-for-byte. Outputs are create-only; no timestamps or
random IDs affect replay. The committed results/ directory contains one offline
conformance run and its validation record, not real-provider results.

Shared goal and smallest next step

The user's goal is lower total effort per independently verified intended task.
ThermoContext is one optional way to select sufficient evidence, not a required
new subsystem. Complete its sparse THRML gate separately while finishing one real
native Hermes baseline on the bounded claim above. That baseline remains useful
even if the sampler is KILL. See baseline_preregistration.json for the bounded
prepared experiment, current prerequisites and no-claim boundary.

Existing Hermes hooks:
  agent/turn_context.py::_collect_pre_llm_call_context
  agent/turn_context.py::compose_user_api_content
  agent/turn_context.py::substitute_api_content
  agent/turn_context.py::_stamp_api_content_sidecar
  agent/turn_api_request.py::_fire_pre_api_request_hook
Optional context enters through pre_llm_call. Observe the final physical request
after hook spill/truncation and middleware. Logical IDs can repeat on retries;
metadata requests can bypass model hooks. Preserve physical attempt accounting.

Existing z0evals study accounting/replay at revision
4b890b02c7489fca3d08fe0582d41c6d81962779:
  studies/hermes-state-v0/accounting.py::build_receipt
  studies/hermes-state-v0/replay.py::replay_bundle
That study's source-handoff checker is task-specific. Its scripted provider and
predetermined output must not be reused as a real reasoning baseline. Freeze an
independent factual checker for this NEW task before inference; keep judge labels
outside candidate inputs. Intent, permissions and verifier stay outside selector.

Only if THRML passes and the native baseline is valid, compare one optional
selection mutation against deterministic champion/greedy with identical model,
source pool, authority and judge. Count compile, sampling, wrappers, retries,
verification and fallback in total work. Test missing, stale, duplicate and
contradictory evidence. If deterministic selection ties at lower overhead, retain
it and stop thermal integration. Do not bundle routing, memory or new verifiers.

Parallelize independent implementation and review; serialize comparable latency
measurements or use fixed isolated CPU resources and record interference. No new
orchestrator is required.

Prepared real native baseline (distinct from conformance)

  PYTHONPATH=src python benchmarks/thermocontext/hermes_phase_c/live_baseline.py \
    --hermes-repo /path/to/hermes --out /tmp/hermes-baseline-prepared

This default freezes task, context, driver/checker hashes and limits; no inference
occurs. baseline_check.py is the independent NEW task checker, not the unchanged
Phase A verifier. The model sees only the question, output shape and public
evidence. The checker is run by the parent after native Hermes exits and is not
included in the context. No source-reading tools are enabled for this one-answer
task: the entire frozen source pool is already supplied to the native hook.

After reviewing the frozen task/checker and provisioning an authorized secret:

  PYTHONPATH=src python benchmarks/thermocontext/hermes_phase_c/live_baseline.py \
    --hermes-repo /path/to/hermes --out /tmp/hermes-baseline-live \
    --credential-file /secure/openrouter.key --execute

The existing native recorder and SDK run through an isolated measurement proxy.
Only one upstream inference POST is allowed, to OpenRouter's HTTPS endpoint with
model=openrouter/free, provider allow_fallbacks=false, max_price prompt/completion
both zero, usage.include=true, max_tokens<=1024 and serialized request <=20,000
bytes. TLS verification and inherited proxy/CA settings remain enabled. Known
model metadata GETs are answered locally and labeled synthetic non-inference;
they cannot appear as actual model work. SDK retry/fallback attempts are blocked
before another upstream call. The worker is bounded to 120 seconds.

No credential, Authorization header or raw environment is persisted. The credential
file must live outside version control. Exact-key redaction additionally protects
diagnostics; do not publish raw local profiles/logs without reviewing them.

The free router does not predeclare the served model/tokenizer. The 20k actual
input-token cap is therefore a POST-RESPONSE eligibility check, not a fabricated
preflight tokenizer guarantee. Missing usage or cost remains unknown. Zero spend
is claimed only when returned usage.cost is numeric zero. Every report preserves
the returned model/provider; a later pair with a different served model/provider
is invalid for attributing a context-selection effect.

A successful baseline is one verified inspected factual task. It is not causal
proof the evidence was necessary, non-inferiority, a speedup, or a thermal-selection
result. Compare these only in the separately frozen next phase.
