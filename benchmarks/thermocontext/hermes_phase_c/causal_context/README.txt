Hermes #320: evidence-use discriminator, using the current Phase-C evidence pool

Status: OFFLINE_CONFORMANCE_ONLY / VALIDATED BUT NOT WIRED.
No provider calls, runtime activation, RLM installation, or semantic-use claims.

What existing evidence already answers

The earlier hermes-state-v0 study completed native execution, accounting and
replay conformance with a scripted provider. It deliberately rejected the generic
StatePacket donor for three inspected semantic questions because the necessary
prose was not exposed, or deterministic reporting already sufficed. This work
preserves that negative result. It does not silently reinstall that donor.

The current ThermoContext Phase-C development pool exposes actual public R2-03
EvidenceRefs. Its qualified ContextPacket renderer already serializes precisely
the same evidence, contradictions and gaps as a plain deterministic bundle.
For the three cases here, the model-visible context is byte-identical. Therefore
a separate packet-vs-bundle model trial is redundant and cannot show an advantage
for added RLM architecture. No learned or thermodynamic selector is involved.

The remaining bounded uncertainty is whether the model's source-identification
behavior depends on the supplied evidence. The new pair holds the prompt,
renderer, authority and judge fixed, and withholds only source-identity in one
arm. That EvidenceRef is the sole exposed source of the tested full revision.
The absence is not annotated in the context as a hint to the model.

Why this is a NEW task

The previous live_baseline OUTPUT_CONTRACT requires a revision string and integer
facts and does not permit abstention. It would be unfair to expect abstention
after deleting decisive evidence under that contract. This new frozen task asks
only tested-source identity plus the Hermes-scope distinction and explicitly
admits an unknown tested source. It pins limits, not source-identity. This is a
separately declared host policy, not a selector permission to drop a previously
mandatory source. Previous model responses cannot be reused as its paired
baseline. The Phase A objective, Phase A verifier and prior baseline judge stay
unchanged.

The independently frozen checker requires correct source identification on the
full arm and abstention on the withheld-source arm. It also checks the retained
scope claim and legal citations. A synthetic contradictory declaration is an
explicitly labeled development control, never asserted as a real publication.
Its preservation/abstention checker passes offline; model robustness is NOT_RUN.
No part of the checker is used to select or serialize context.

Run offline preparation from this directory using the repository's isolated
Python environment. Set HERMES_SOURCE_TREE to the pinned Hermes checkout recorded
in fixtures/sources.json. The Phase-C source defaults to this directory's parent
in tests; HERMES_PHASE_C_ROOT optionally overrides that location.

  PYTHONPATH=../../../../src python discriminator.py \
    --phase-c .. \
    --hermes-repo "$HERMES_SOURCE_TREE" \
    --out /tmp/hermes-context-discriminator-new

  PYTHONPATH=../../../../src python -m unittest -v test_discriminator test_pair

Without HERMES_SOURCE_TREE, only native-dependent tests are skipped; the pure
intervention, checker and attribution tests still run. With a checkout supplied,
source/revision mismatches fail rather than silently qualifying a different host.

Outputs are create-only. Each prepared case has prompt.txt, context.txt,
selection.json, pool.json, constructed-request.json and wire-witness.json.
freeze.json pins the checker, renderer, actual imported ContextPacket source,
Hermes composition revision and source, prompt, pools and exact context bytes.
The byte witness uses real native compose_user_api_content and sidecar
substitution but never transmits a request. It leaves model consumption unknown.

Native admission correction discovered during the real baseline attempt

The default Hermes hook path spills context above 10,000 characters to a
head/tail preview and disk pointer. These zero-tool tasks cannot recover the
missing middle. Therefore direct composition alone was insufficient admission
evidence. results/prepared-01 is preserved as the earlier serialization-only
receipt; it is not qualified for an actual call with default hook settings.

The corrected preparation invokes the real _collect_pre_llm_call_context and
real spill function with a controlled hook result. Every case records both the
default truncation failure and exact preservation under the shared driver's
study-only spill policy, max_chars=max(10000,len(context)). Hook registration is stubbed for this offline
test. No global or active runtime config changes are made. The actual transport
must still reject, before forwarding, anything without exactly one complete
context occurrence or over 20,000 serialized bytes. A character cap is not a
wire-byte or token guarantee. The synthetic contradiction case is an offline
control and is not part of the proposed two-call pair.

The aggregate receipt records stale-revision and changed-excerpt rejection,
alias rejection, duplicate-context detection, deterministic replay and explicit
contradiction preservation. These are structural controls, not live model tests.
Preparation timing is kept separately because elapsed time is not replay-stable.
It is offline overhead, not provider token use or total verified-task latency.

Prepared pair runner and current provider block

run_pair.py now reuses live_baseline.run(..., prepared_case=...) directly. It does
not copy or replace the proxy, worker, routing or authority policy. The default
mode prepares only; --execute is explicit. Each arm uses a fresh profile, at most
one actual upstream POST, openrouter/free, max_price=0, no fallback, a 20,000-byte
request cap, 1024 output tokens and 120 seconds. No resource adequacy retries are
part of this pair. The first ineligible arm stops the second attempt.

The frozen pair is results/causal-pair-01/pair-freeze.json. Its paired study is
under results/causal-pair-01/study/. The live provider returned HTTP 429 during
root's separate resource-adequacy baseline, so this pair is currently
BLOCKED_PROVIDER_RATE_LIMIT, with ZERO provider calls and NO semantic result.
provider-block.json binds the 429 source receipt and prevents execution before
any attempt marker is consumed. There is no automatic polling or retry. Root may
clear the block only after confirming free quota is available; the original
checker and two-call protocol remain frozen.

Safe no-network replay now:

  PYTHONPATH=../../../../src python run_pair.py \
    --replay --out results/causal-pair-01

Archived freezes retain the original execution machine's absolute paths and
hashes unchanged. Replaying those exact archives requires the recorded files;
on another checkout, prepare a new create-only output with --phase-c .. and
--hermes-repo "$HERMES_SOURCE_TREE". Do not rewrite archival path records.

For later root execution after that resource block is deliberately cleared, use
run_pair.py with --phase-c, --hermes-repo, --out results/causal-pair-01,
--credential-file <authorized-file> and --execute. Every bound source and context
hash is checked first. An exclusive execution-started marker prevents accidental
re-execution, including after a crash. The frozen shared-driver default is
explicitly held to 1024 tokens even if another study uses a larger output limit.

After execution, --replay regrades saved answers with the unchanged independent
checker, rechecks exact context and route/budget policy against physical records,
and reconstructs pair-result.json without inference. Missing usage stays unknown;
changed or missing actual model/provider identity makes attribution unidentifiable.

Compatibility with the current live driver

The root worker can reuse existing live_baseline.worker, OneRequestPolicy and
the existing native recorder.prepare_profile: supply a case's exact prompt.txt
as --prompt and exact context.txt to prepare_profile(context=...), then apply the
frozen study-only spill configuration and actual transport admission guard. The live
driver's default run() uses the old task, so do not execute that default and call
it this experiment. run_pair.py passes the explicit host-prepared case. The
independent frozen_check.grade(case_id,answer)
runs only after the worker exits. Do not expose the judge module or result label
to the model. Root coordinates the two bounded provider attempts; this module
contains no provider execution capability.

Retain every physical attempt, returned model/provider, raw request witness and
actual usage, including failures. A different served model/provider invalidates
the causal comparison. Missing usage is unknown. Require full-arm correctness
AND withheld-arm abstention; correctness of a serialized request is not proof
of semantic use. One development pair is a discriminator, not population
noninferiority, speedup, proof that a model was necessary, or thermal benefit.

Stop added RLM architecture at the byte-identical deterministic comparator.
Only the bounded evidence-dependence question remains eligible for a live pair.
