# feat(capture): one cross-harness capture record, shared core, lean hook adapter, CC/Codex/Grok shims (C1)

Branch `feat/wire-loop-core-20261003` @ `914dc99` · base `feat/shadow-loop-v0` (6fee859) · component C1-loop-core · **verified**

Staged branch only. Do not open as a PR until the owner reviews it. Do not merge before the integrate branch has been reviewed.

## What

- `harness_id.turn_key()` and an alias table. One canonical turn key covers six trace-id conventions: CC prompt_id, the two Hermes forms, bend-native sha256, automatic, DSH lineage and the OMP bridge.
- `src/z0int/harness_capture.py`: the record family `z0int.<harness>.{opportunity_record,turn_outcome,failure}.v0`. Each record carries:
  - turn_key, work_item_id / attempt_id, model_id / policy_revision (a value or `unknown`) and privacy_class;
  - content-free capture-time flags and a capture-time cohort.
- The capture core has:
  - a bounded spool and persisted drops (`drops.jsonl`);
  - a bounded `close()`;
  - explicit failure rows (stale_evidence, missing_verifier, partial_measurement, uncertain_execution, duplicate_event, misattribution, unsupported_*).
- `z0int.hook_entry` / `python -m z0int.hook_adapter --harness <id> {prompt,stop,subagent-start,subagent-stop,session-start}`:
  - The entry is lean: it imports stdlib only and builds the opportunity in a detached child.
  - The harness comes from the payload or env (GROK_HOOK_EVENT), never from where the hook file lives.
- `check_class.py` is extracted from the outcome_verifier regexes. The verifier now imports it.
- `agentsview_ro.py`: a shared read-only connection (mode=ro, user_version guard 74/113, staleness).
- Shims:
  - Claude Code hooks add SubagentStart/SubagentStop, giving agent-cohort opportunities joined on turn_key.
  - New hooks-only `harness-adapters/codex-z0intelligence`, with `.agents/plugins/marketplace.json`.
  - New `harness-adapters/grok-z0intelligence/hooks/z0-capture.json`.
- Only claude-code keeps `automatic.*` calls. Every other harness is capture-only.
- `z0int outcomes backfill-capture`: a one-shot, idempotent pass that flags legacy CC opportunity rows and drops their stored request text.

## Why

z0int#62 asks for one semantic record for every harness. z0int#56 M0/M1 asks for subagent and eval cohorts separated at capture time. The shared core and adapter come first; every other component depends on them.

## Tests

- 22 planned red tests were written first (`evidence/C1-loop-core/red.txt`), then turned green. The verifier REVISE added further red tests (cc097b1), fixed in 914dc99:
  - CC Stop race and late messages;
  - legacy rows kept out of user cohorts;
  - O(1) Stop-time checks;
  - spool race.
- Full suite under `flock -s` quiet-lane with the socket guard on:
  - base 6fee859: 11 failed / 964 passed;
  - head: 11 failed / 1018 passed;
  - the failure sets are identical (known live-provider / quota environment tests);
  - 0 socket-guard violations.
- Regression guard: on the CC fixture set, the exported training rows and cohorts equal the 6fee859 export (`tests/fixtures/cc_export_6fee859.json`). The fixture holds feature rows only, with no text.

## Evidence

`/mnt/zer0models/z0-wt/wiring/evidence/C1-loop-core/` (`red*.txt`, `green*.txt`, `suite.txt`, `e2e.txt`, `NOTES.md`). The isolated e2e covered:

- a real `claude -p` against a local Anthropic-Messages stub (interactive and agent cohorts joined);
- a real `codex exec` against a stub;
- Grok via the exact hook command strings on fixtures;
- the backfill-capture CLI smoke.

## Activation (NOT performed; owner approval per step)

These steps follow the A0 pin. See `ACTIVATE.md` A1 and A5.

- **A1 Claude Code:**
  - Back up `~/.claude-home/settings.json`.
  - Use a local-directory marketplace at the pinned checkout and set `Z0INT_PYTHON` to venv-wiring.
  - Back up opportunities.jsonl, then run `backfill-capture` with `--dry-run` and then without it.
  - The owner runs one real turn and one subagent turn.
- **A5 Codex:** `codex plugin marketplace add <pinned>`, then `codex plugin add codex-z0intelligence@z0intelligence`. `z0intelligence@personal` stays untouched (A9).
- **A5 Grok:** copy `hooks/z0-capture.json` to `~/.grok/hooks/`.
- Kill switch: `Z0INT_CAPTURE=0` or `$Z0INT_HOME/config/capture.json {"enabled": false}`.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
