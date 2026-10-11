# Donor: optchat

Cut from `dd39238` (tag `archive/pre-reorg-dd39238`) as one commit. Not on master.
`README.md` is the old whole-repo README (its "OptChat fresh-view turns" section is
the only prose documentation of this feature); this file describes the branch.

## What it is

OptChat is an opt-in OMP mode that keeps one endless chat log as a binary tree of
one-line summaries and sends the model a compact view of that tree instead of the
conversation history. A resident Python process owns the log and the background
summarizer; an OMP extension intercepts each input while the mode is on and runs
its own model/tool loop against the view.

## Entry points

- `omp-extensions/z0-optchat/index.ts:167` `optchat(pi)`: the extension. Registers
  `/optchat` (`:199`) and `/optchat-browse` (`:222`), and the `input` handler (`:230`)
  that takes over a turn when the flag is on. Tools are registered only when the
  flag is on (`:197`).
- `omp-extensions/z0-optchat/turn.ts:463` `runTurn`, `:455` `cancelTurn`.
- `omp-extensions/z0-optchat/index.ts:85` spawns `python -u -m z0int.optchat`.
- `src/z0int/optchat/__main__.py:213` `serve()` (module guard at `:260`): the
  resident JSON-lines server.
- `src/z0int/optchat/switch.py:13` `enabled()`, `:20` `set_enabled()`: the flag file
  `$Z0INT_HOME/optchat/enabled`.
- `src/z0int/optchat/compact.py:252` spawns `bun src/z0int/optchat/sonnet.mjs`
  (the default summarizer bridge).

The extension is not installed by anything in this branch. On the audited host it
was a symlink in the OMP extensions directory.

## Tests

```
PYTHONPATH=src python -m pytest -m 'not live' -q
bun scripts/prove-optchat-conformance.mjs all <out-dir>
bun scripts/prove-optchat-sonnet.mjs <out-dir>
```

Results on this branch (trimmed tree, clean environment, Python 3 venv with pytest):

- pytest: 76 passed, 4 subtests passed. That is `tests/test_optchat.py` plus
  `tests/test_check_receipt.py` (covers `.github/scripts/check-receipt.py`).
- `prove-optchat-conformance.mjs all`: 9 scenarios, 41 checks, 0 failed,
  `real_provider_model_calls: 0`. Drives the real `turn.ts` and the real resident
  against scripted loopback responses.
- `prove-optchat-sonnet.mjs`: 17 checks ok, `real_inference_calls: 0`.

Both proof scripts need `bun` and an installed OMP package
(`@oh-my-pi/pi-coding-agent`); set `Z0INT_OMP_ROOT` to it and `Z0INT_PYTHON` to a
Python that can import `z0int` from this tree. They are not run by CI on this
branch: the kept workflow only runs pytest. There are no `*.test.ts` files.

All 8 modules under `src/` import. `tests/test_architecture_manifest.py` was
dropped: it asserts every path in `zer0.repo.yaml` exists, and that file is kept
unmodified as the whole-repo map.

## Hot-path cost

- Flag off: one `readFileSync` of the flag file per input. No process, no tools
  injected. (Audit: "While off: one readFileSync per input, no spawn, no tools
  injected.")
- Flag on: replaces OMP's agent loop for the turn, so other turn hooks do not
  run. Adds a resident Python process and background summarizer calls. With no
  `Z0INT_OPTCHAT_HOST` set, the summarizer is `claude-sonnet-4-6`
  (`compact.py:381-382`), so it spends frontier tokens in the background. That
  cost has not been counted anywhere.

## Evidence (from the audit of dd39238)

- "Aims directly at the north star (send a compact view instead of full history)
  but the repo has no measured token or task-success number."
- "OptChat: aimed at the north star, no measurement, unused since 2026-10-07, and
  its default summarizer is claude-sonnet-4-6 (optchat/compact.py:381-382)."
- Verdict from two auditors: UNSURE. "Coherent and zero-cost while off, but
  unproven and unused for 4 days." A third auditor marked the test file KEEP
  "pending confirmation that the flag is on"; the flag file was 4 bytes, which is
  consistent with "off".
- Size: `src/z0int/optchat` 1,531 lines, `omp-extensions/z0-optchat` 830 lines,
  proof scripts 1,075 lines, `tests/test_optchat.py` 736 lines.

There is no evidence against it either. Nobody has measured it.

## Did it run live on the audited host?

The extension was loaded (symlinked into OMP), so its off-path ran on every input.
The mode itself was not in use: chat logs were last written 2026-10-07 and the
audit records `runs_live_here: no` for the Python package. The audit also noted
the live copy of the three `.ts` files differed from this commit (uncommitted
edits on the host); those edits are not in this branch.

## Known defects

- `turn.ts:66-69`, `compact-ui.ts:19-26`, `sonnet.mjs:7-12`,
  `prove-optchat-conformance.mjs:141-144` and `prove-optchat-sonnet.mjs:447-452`
  hard-code absolute paths to one machine's bun install of the OMP packages.
  On another machine the `.mjs` files work only with `Z0INT_OMP_ROOT` set; the
  two `.ts` files have no override and need an edit.
- Default summarizer is a frontier model; a local model needs
  `Z0INT_OPTCHAT_HOST` and `Z0INT_OPTCHAT_MODEL`.
- No installer: nothing here links the extension into OMP.
- `pyproject.toml` still declares the whole-repo dependency set (torch and
  friends) although this code uses only the standard library. Only the
  `[project.scripts]` entries for removed modules were trimmed. The package is
  still named `openjev-phase1`.
- The CI workflow on this branch installs that full dependency set to run 76
  tests.
- `README.md` describes the whole pre-reorg repository; most of what it names is
  not on this branch.

## To earn a place on master

One measurement: the same set of verified tasks run with OptChat on and off,
reporting frontier tokens per verified task and wall time, with the summarizer's
tokens counted in the "on" total. If "on" does not win with that cost included,
delete it.

Smallest subset worth taking if it wins:

- `src/z0int/optchat/` (all nine files; they import nothing else from the repo)
- `omp-extensions/z0-optchat/` (three files), after replacing the hard-coded
  package paths
- `tests/test_optchat.py`
- `scripts/prove-optchat-conformance.mjs`, turned into a CI test

`prove-optchat-sonnet.mjs` is only needed while the Sonnet bridge stays the
default summarizer.
