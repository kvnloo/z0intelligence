# z0live desktop voice profile

`manifests/z0live_voice.v1.json` is an **owner-selected deployment profile**, not a measured model promotion. It extends RFC kvnloo/z0live#1's VoicePlan with pinned role bindings. It does not modify the local cognition specialist `role_defaults`, execute tools, load GPUs, rank models, or activate automatic Hermes routing.

```sh
PYTHONPATH=src python -m z0int.voice_plan manifests/z0live_voice.v1.json
PYTHONPATH=src python -m unittest discover -s tests -p test_voice_plan.py -v
```

Install the emitted JSON privately at `$Z0INT_HOME/config/z0live-voice-plan.json` (default `~/.z0int/config/`); or configure the desktop consumer's `Z0LIVE_VOICE_PLAN` with an explicit exported path. z0live validates the schema/digest and stores a complete immutable copy per session. Updating the source affects only newly minted sessions. Missing/invalid plans must be reported; they do not authorize an implicit paid fallback.

- L0 `openai/gpt-realtime`, voice `marin`: direct WebRTC.
- L1 `smol`: `anthropic/claude-haiku-4-5`, matching the owner's OMP smol role. Luna is an owner-selectable replacement, not an automatic failover.
- L2 `plan`/`slow`: `anthropic/claude-opus-5-5`, retaining the existing persistent Hermes z0live session. OMP's current plan/task roles are xai-oauth; this desktop-specific profile deliberately keeps the already authorized Hermes deployment instead of assuming cross-harness support.
- L3 `task` and general `default`: `openai-codex/gpt-6.1-sol`, the owner's general model and existing Hermes delegation provider. These are cheaper than the Opus meta lane; no unqualified Groq/Cerebras/DeepSeek fallback.
- `judge`: `typesafe/jev-latest`, reserved for bounded legal-action gating. No private voice memory is sent to Jev by this profile.

The harness owns credentials, approvals, native runs and cancellation. z0live owns floor/barge, replay, per-turn observations and bounded delivery. `meta_role` is part of the plan, not selected per turn. Model inference latency and subscription billing are measured/reported by the consumer, not asserted here. API-reported usage without a provider price yields unknown cost, not zero.
