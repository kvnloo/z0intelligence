# DONOR: compilers-and-routines

Donor branch cut from `dd39238` (tag `archive/pre-reorg-dd39238`). One commit on
top of that tag removes everything outside this feature. `README.md` and
`zer0.repo.yaml` are the old whole-repo files, kept unmodified; this file
describes the branch.

## What it is

A set of offline compilers and experiments from 2026-09-17..26: compile
specialist behaviour into deterministic routines and confidence cascades, gate
them with statistical credit on a sealed split, repair them from
counterexamples, and run them through an ABAB archive loop. Bundled with them
are the smaller experiments of the same family: the Hermes-trace next-action
baseline (`compile`/`train`/`families`), contrastive evidence and the
data-recipe race, the OS-episode `next_operator` specialist, the
`context_project_state` capability fixture, `alignbench`, and the image
decision adapter and CLI.

## Entry points

All are human-typed commands. Nothing on this branch (or at `dd39238`) calls
them from a hook, service, unit or extension.

| Command | Code |
|---|---|
| `python -m z0int.routines` / `z0int routine` | `src/z0int/routines.py:664` (`_main`), dispatched at `src/z0int/cli.py:1179` |
| `python -m z0int.cascade` / `z0int cascade` | `src/z0int/cascade.py:424`, `src/z0int/cli.py:1181` |
| `python -m z0int.aodl` / `z0int aodl` | `src/z0int/aodl.py:900`, `src/z0int/cli.py:1183` |
| `python -m z0int.refinement` / `z0int repair` | `src/z0int/refinement.py:436`, `src/z0int/cli.py:1185` |
| `python -m z0int.abab` / `z0int abab` | `src/z0int/abab.py:460`, `src/z0int/cli.py:1192` |
| `z0int contrastive fixture\|eval\|race` | `src/z0int/cli.py:1022` -> `contrastive_evidence.py`, `data_recipe_race.py:26` |
| `z0int context-state fixture\|compile` | `src/z0int/cli.py:1062` -> `capabilities/context_project_state.py:24,106` |
| `z0int os-context import\|stats\|next-operator` | `src/z0int/cli.py:1144` -> `os_context.py`, `specialists/next_operator.py:152` |
| `z0int-compile` | `src/z0int/compile.py:96` (pyproject.toml:29) |
| `z0int-train-next-action` | `src/z0int/train.py:85` (pyproject.toml:30) |
| `z0int-image-decide` | `src/z0int/image_decision_cli.py:145` (pyproject.toml:31) -> `backends/image_decision.py` |
| `python -m z0int.alignbench run\|score` | `src/z0int/alignbench.py:270` |
| `python examples/refinement/demo.py --output DIR` | `examples/refinement/demo.py` |

Library only (no command of their own): `battery.py`, `credit.py` (`lift_credit`
:62, `paired_credit` :86), `families.py`.

Checked on this branch: every module above imports; each `--help` above exits
0; `z0int contrastive fixture`, `z0int contrastive race --json`,
`z0int context-state fixture`, `z0int os-context stats` and the refinement demo
exit 0.

## Running the tests

```
pip install -e '.[test]'
pytest -m 'not live' -q
```

Result on this branch: 446 passed, 2 skipped, 0 failed, 0 collection errors
(full tree at `dd39238`: 1164 passed, 2 skipped, 5 deselected). The kept tests
pass with only `src/` on `PYTHONPATH`; torch, transformers and PIL are imported
lazily and are not exercised offline.

The feature's own tests: `tests/test_z0int_{routines,cascade,credit,refinement,refinement_cli,abab,battery,aodl,families}.py`,
`tests/test_contrastive_evidence.py`, `tests/test_os_context.py`,
`tests/test_alignbench.py`, `tests/test_image_decision_backend.py`.
The rest of the kept tests cover code kept only as import closure (see below).

No extension with bun tests is kept. CI: `.github/workflows/core-unit.yml`
(trimmed by line removal to what still exists), `receipt.yml`, and the
feature's own `image-decision-unit.yml`.

## Cost on a turn's hot path

None. No hook, bridge op, service route or extension imports any of these
modules; they run only when a person types a command. The cost of keeping them
on master was maintenance and import surface, not latency or tokens.

## Evidence (quoted from the audit of `dd39238`)

There is no measurement in favour. The audit found no consumer for any output.

- routines/cascade/credit/refinement/abab/battery (2,397 lines): "NONE. Only
  reachable from human-typed subcommands `z0int routine|cascade|repair|abab`
  ... and examples/refinement/demo.py; battery.py has zero importers outside
  tests." Effect: "NONE. The live preflight ... looks for its own
  routine_registry.json format, not this compiler's output, and no such
  registry file exists". "One experiment, one commit each on 2026-09-18, never
  touched again."
- `cascade.py`: "Its target is premium tokens per verified success, but no
  hook, service route or unit loads a compiled policy."
- `compile.py`, `train.py`, `families.py` (272 lines): "NONE. No live module
  loads the trained ridge model." "The first prototype of the project."
- `contrastive_evidence.py`, `data_recipe_race.py`, `capabilities/` (768
  lines): "NONE that executes." "Its journal directory ... does not exist on
  this host."
- `os_context.py`, `specialists/` (652 lines): "NONE. Only `z0int os-context
  import|stats|next-operator`". Outputs "were last written 2026-09-18".
- `alignbench.py` (297 lines): "Zero importers in src; only
  tests/test_alignbench.py. Has a __main__ block nobody invokes."
- Image decision (346 + 152 lines): "NONE. Image decisions are not on any
  harness turn path." "A human-only CLI with a CI job that tests itself."
  The bench fixture "is a red square never loaded by its own test."
- Plan summary: "Nothing else in the repo has any evidence of saving tokens or
  latency: not ... autoresearch, or the routine/cascade compilers."

The only numbers attached to this code are internal to its own demo and tests
(for example `tests/test_z0int_refinement_cli.py` asserts 384 fallback calls
after demotion in the synthetic demo). They show the code is self-consistent,
not that it saves anything on real tasks.

## Did it run live on the audited host

No. Audit verdicts for every module here: `runs_live_here: no`. Data
directories these commands write were either absent or last written on
2026-09-18.

## Known defects

- `z0int` on this branch is the whole-repo CLI kept shallow. The subcommands
  in the table above work, and so do `cognition`, `receipt`, `counterfactual
  mine|summary` and `autoresearch`, which arrive as import closure. Every
  other subcommand still parses but fails at call time with
  `ModuleNotFoundError`, because `cli.py` lazily imports modules that are not
  on this branch: `doctor`, `status`, `onboard`, `posture`, `preflight`,
  `state_packet`, `context_resolve`, `task_loop`, `ao_experiment`, `ao_report`,
  `artifacts`, `claude_code_launch`, `hermes_decisions`, `kerdoios_export`,
  `replay`, `backends.bench`.
- `src/z0int/cli.py` imports `.autoresearch.research.pipeline` inside one
  autoresearch branch; that module did not exist at `dd39238` either.
- `compile.py:21` defaults to a hardcoded Hermes `state.db` path that will not
  exist on most machines; pass the path explicitly.
- `alignbench run --backend NAME` needs a real decision backend (model weights,
  torch); only the scoring half is covered offline.
- `image_decision` needs torch, transformers, PIL and Qwen2.5-VL weights at run
  time; the unit test covers only the pure adapter logic.
- `aodl.py` here is the Sept-18 AODL/HOTL binding over routines and cascades.
  It is not the AODL admission gate (`aodl_admission.py`, not on this branch).
- `tests/test_architecture_manifest.py` is dropped by design: it asserts that
  every path in `zer0.repo.yaml` exists, and most do not on a donor.
- `tests/test_llama_http_backend.py` is dropped although
  `backends/llama_http.py` is kept (as registry closure). The test also needs
  `z0int.backends.bench`, which belongs to `donor/decision-backends`; the
  backend is covered there, not here.
- The feature description mentions "schemas": `schemas/` at `dd39238` holds
  only the AgentWeb bridge schema, which is not part of this feature and is
  not kept.

## Extra code kept so the branch works

Not part of the feature; kept because something in it imports or reads them.

- `src/z0int/cli.py`, `__main__.py` (shallow: module-level imports only):
  `contrastive`, `context-state` and `os-context` have no other entry point.
- `src/z0int/cognition/` (20 files), `scripts/probe_local_cognition.py`,
  `manifests/local_cognition.v1.json`, `docs/local-cognition-portfolio.md`:
  `cli.py` imports `cognition.cli` at module level. With them come the
  cognition tests whose imports are all present.
- `src/z0int/receipt.py`, `counterfactual.py`, `tests/test_receipt.py`:
  `cognition/cascade.py` imports `receipt`; its test calls the CLI and the
  counterfactual miner.
- `src/z0int/backends/` (base, registry and the six registered backends),
  `models_mgmt.py`, `src/openjev_phase1/{__init__,core,direct}.py`,
  `manifests/models*`, `manifests/capabilities.v1.json`,
  `manifests/capability-evidence/`: `image_decision.py` lives in the
  `backends` package, whose `__init__` imports the registry, and
  `alignbench.run_backend` calls `registry.create_backend(name)`.
- `src/z0int/autoresearch/` (8 files, all but `measurement_gaps.py`):
  `tests/test_contrastive_evidence.py` exercises the replay consumer of
  contrastive evidence.
- `src/z0int/paths.py`: shared layout helper.

## To earn a place on master

One consumer, with one paired measurement: a live path (hook, router or bridge
op) that loads a compiled routine registry or cascade policy and answers a
class of turns without a frontier call, compared against the same tasks with
it off, showing fewer frontier tokens or lower latency per verified task. No
such loader exists; the audit notes the live preflight reads a different
registry format.

Smallest subset worth taking if that happens: `src/z0int/credit.py` (134
lines, the sealed-split statistical gate, no first-party imports) and
`src/z0int/routines.py` with `tests/test_z0int_credit.py` and
`tests/test_z0int_routines.py`. Add `cascade.py` and `refinement.py` only when
the consumer needs thresholds or repair. Everything else here (compile/train,
os_context, specialists, capabilities, contrastive evidence, alignbench, image
decision) has no proposed consumer; whether any of it would help is unknown.
