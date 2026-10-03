Title: docs: ground feature promotion in independent outcomes, not shadow logs

Docs only — no code, threshold or default changes.

Shadow mode is the right way to collect evidence, but the current docs read as if a plausible shadow log is enough to turn a feature on ("Turn it on when the log looks right"). This tightens that into a promotion sequence:

shadow readings → independent outcomes → grouped held-out replay → calibration / quality / latency / cost → human review → on

- **CONTRIBUTING.md / README:** keeps the probability contract the thresholds rely on (0.65 GUI floor, 0.7 transcript floor, "unsure is not hard"), and scopes calibration claims to the question family and workload they were measured on. A threshold stays a fail-open rail; it is not proof that a decision is correct.
- **docs/turning-a-jev-feature-on.md:** the shadow log is instrumentation, not validation. Join decisions to an outcome Jev did not create (tests/CI, a verifier, user correction, environment success, measured cost/latency). Teacher agreement ("agrees with Jev") is weak supervision when distilling into NanoJev, a rule or a smaller model, not the release criterion.
- **docs/measuring-a-router.md:** cost replay answers "what would this have cost", not "would it have done the work". Compare baseline features, Jev-only and features+Jev on frozen groups, and keep every turn of a session/work item in one fold so retries don't leak across the split.
- CHANGELOG line under Unreleased.

`python3 -m unittest discover -s tests` (1417 tests, OK) and `python3 scripts/check_release.py` (clean) pass on this branch.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
fork branch: kvnloo/hermes-jev-skills upstream-pr/outcome-grounded-promotion-docs @7dd1e13 → base kerpopule/hermes-jev-skills main
