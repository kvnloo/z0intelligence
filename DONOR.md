# Donor: omp-jev-extensions

Cut from `dd39238` (tag `archive/pre-reorg-dd39238`) as one commit. This branch is a
parts shelf, not a product: nothing here is on master unless it is salvaged later.

## What it is

Three OMP (oh-my-pi) extensions that put a small-model "Jev-like" lane next to
the frontier model: `flyforge-jev` (log-only shadow of two evolution-lab
predictions per prompt), `flyforge-recovery` (a `recovery_advise` tool plus a
hint appended to failed tool results) and `vllm-jev` (a `vllm_decide` tool and a
per-prompt shadow call to a vLLM DiffusionGemma server). With them come their
two setup scripts, the Route C doc, and `src/openjev_phase1`, because the setup
scripts and the doc end in `openjev-score` commands.

The `openjev` and `openjev-06b` extensions are not here; they are on
`donor/decision-backends`.

## Entry points

OMP loads an extension when its directory is linked into
`~/.omp/agent/extensions/<name>`. On this donor that link is made by hand
(`ln -s "$PWD/omp-extensions/<name>" ~/.omp/agent/extensions/<name>`); the
`z0int onboard` step that did it for every extension is not on this branch.

| What | Where |
|---|---|
| flyforge-jev extension | `omp-extensions/flyforge-jev/index.ts:165`; `before_agent_start` hook `:167` awaits `shadow()` `:79` |
| flyforge-recovery extension | `omp-extensions/flyforge-recovery/index.ts:114`; tool `recovery_advise` `:157`; system-prompt line `:191`; `tool_result` hook `:198`; `/recovery` command `:230` |
| recovery worker | `omp-extensions/flyforge-recovery/recovery.py:60` (`--worker` = resident stdin JSONL loop, `serve()` `:32`) |
| vllm-jev extension | `omp-extensions/vllm-jev/index.ts:87`; tool `vllm_decide` `:91`; shadow call `:133`; system-prompt line `:157`; `/vllm` command `:164` |
| `openjev-score` | `src/openjev_phase1/cli.py:33` (`--mode direct|serial|shared|reranker|vllm`; vLLM branch `:55`) |
| `openjev-data`, `-train`, `-eval`, `-predict`, `-bridge-score` | `src/openjev_phase1/jevlike/{data,train,eval,predict,bridge}.py` (`pyproject.toml` `[project.scripts]`) |
| FlyForge bootstrap | `scripts/setup-flyforge.sh` |
| Route C bootstrap | `scripts/setup-vllm-diffusion.sh` |
| Lane timing race | `scripts/race-omp-backends.py:174` |

Checked on this branch:

- `import openjev_phase1.cli`, `openjev_phase1.vllm_route.client`,
  `openjev_phase1.vllm_route.score`, `openjev_phase1.jevlike.{data,train,eval,predict,bridge}`
  all import; `python -m openjev_phase1.cli --help` runs. All 20 modules under
  `src/` import.
- The three `index.ts` files bundle with `bun build --external '@oh-my-pi/*'`
  (syntax and local imports resolve). They were not loaded into a running OMP.
- `recovery.py '{"action":"status"}'` returns a status object when an
  evolution-lab checkout is present at the path it expects.

## Tests

```
python -m pytest -m 'not live' -q        # 60 passed
```

Kept tests: `test_core.py`, `test_serial.py`, `test_shared.py`,
`test_reranker.py`, `test_vllm_route.py`, `test_jevlike_smoke.py` (all cover
`src/openjev_phase1`) and `test_check_receipt.py` (the CI receipt gate).

**No test covers any of the three extensions**, here or at `dd39238`. There are
no `*.test.ts` files in the kept extension directories, so there is no bun test
step. `tests/test_architecture_manifest.py` is dropped (it asserts every path in
`zer0.repo.yaml` exists; that file is kept unmodified as the whole-repo map).
Every other dropped test covers code that is not on this branch (`z0int.*`).

`core-unit.yml` was trimmed (line removals only) to the `offline-suite` job.

## Cost on a turn's hot path

All three run on every OMP turn once linked. From the audit:

- flyforge-jev: "the hook awaits the two subprocesses, so each OMP turn can
  block up to 8 s before the agent starts, and it never changes the turn."
- flyforge-recovery: "about 45 system-prompt tokens per turn, a resident Python
  process per OMP session, about 40 tokens added to each failed tool result, and
  up to 35s wait on each tool error." Plus one `essential` tool schema.
- vllm-jev: "about 45 tokens of system prompt and a tool schema to every OMP
  turn", plus a fetch with a 2.5 s timeout per prompt (not awaited).

Token figures are the auditors' estimates from text length, not measurements.

## Evidence (quoted from the audit of `dd39238`)

- vllm-jev, verdict DELETE, high confidence: "nothing listens on :8000, and
  route.log holds 558 lines, all 'shadow fail' (2026-10-04 to 2026-10-11
  21:46Z), which nobody reads. The shadow question is hardcoded to two options
  (shell / none_of_these), so it could not produce a decision even if the server
  were up." "Pure per-turn frontier-token cost with zero successful calls ever
  recorded."
- flyforge-jev, verdict DELETE, high confidence: "Output files are 1089 and 1030
  rows, last written 2026-10-07; the only reader in src is
  receipt.find_receipt ... scanning raw.jsonl by trace_id, which is a lookup,
  not a decision." A second auditor: "Dead on this host: the interpreter it
  needs is missing."
- flyforge-recovery, verdict UNSURE (medium / low confidence): "NONE measured:
  no receipt, log or benchmark in this repo records whether the appended hint
  reduces retries or tokens." "The only OMP step that alters model input on
  purpose; keep only if a paired run shows fewer tokens per recovered failure,
  otherwise delete." "If kept it belongs in evolution-lab, since all logic lives
  there (recovery.py is a 70-line path shim ...)."
- `src/openjev_phase1` tests: verdict ARCHIVE_OUT_OF_REPO. "Research code with
  benchmark-only callers; test_jevlike_smoke also forces the torch CPU wheel
  install in the offline-suite CI job."
- Plan, hot-path cuts: "four 'tool X is available' sentences ... (about 180
  tokens, estimated from text length) plus five or six `essential` tool schemas
  on every turn, for tools never called or always failing. Remove."

There is no evidence for any of the three: no run shows fewer frontier tokens,
lower latency, or fewer retries with an extension on.

## Did it run live on the audited host

Yes, all three were linked into the live OMP agent (from a checkout that was not
`dd39238`). vllm-jev ran and failed every call. flyforge-jev loaded and returned
at once because its interpreter path did not exist. flyforge-recovery ran with a
resident worker process per OMP session.

## Known defects

- `flyforge-jev/index.ts:23-26` defaults to an interpreter and working directory
  under `/workspace/evolution-lab`; if the interpreter is missing the hook
  silently does nothing (`:80`). Override with `EVOLUTION_LAB_PYTHON` and
  `EVOLUTION_LAB_ROOT`.
- `flyforge-recovery/recovery.py:20` hard-codes `/workspace/evolution-lab`
  (override: `FLYFORGE_EVOLUTION_LAB`) and `index.ts:48` hard-codes
  `/usr/bin/python3`. All recovery logic lives in the external `evolution_lab`
  package; this repo holds only the shim.
- `vllm-jev/index.ts:17` writes `route.log` inside
  `~/.omp/agent/extensions/vllm-jev/`, which is the symlinked repo directory, so
  it dirties the checkout. The shadow call's options are hard-coded (`:141-144`).
  The tool needs vLLM PR 57250 or an equivalent build; none was available to
  test against.
- `scripts/setup-flyforge.sh:83` runs `python -m z0int doctor`; `z0int` is not on
  this branch. The line ends in `|| true`, so the script continues. The script
  also prints `Prefer z0int onboard`, which does not exist here.
- `scripts/setup-flyforge.sh` and `setup-vllm-diffusion.sh` create a venv, `pip
  install`, and clone other repositories. Neither was run for this cut.
- `scripts/race-omp-backends.py` posts to the paid TypeSafe API when
  `TYPESAFE_API_KEY` is set and expects the recovery extension to be linked
  under `~/.omp`. It was not run for this cut.
- `AGENTS.md` and `README.md` are the whole-repo versions and describe much that
  is not on this branch. `AGENTS.md:29` is the line that says these extensions
  "must be symlinked"; the audit names it as what kept dead extensions on the
  hot path.
- `docs/evolution-lab.md` and `docs/vllm-diffusion-route.md` are dated design
  notes; the audit files both under historical docs.

## Extra files kept beyond the assignment

- `src/openjev_phase1/` (20 files) and its six tests: both setup scripts verify
  or print `openjev-score` commands, `setup-flyforge.sh:77` imports
  `openjev_phase1`, and the Route C doc is about `openjev-score --mode vllm`
  (`openjev_phase1.vllm_route`). Without it the scripts would describe commands
  that do not exist. The assignment lists the package under decision-backends.
- `docs/evolution-lab.md`: `setup-flyforge.sh` points at it.
- `scripts/race-omp-backends.py`: the only script that times the recovery and
  vLLM lanes. Stdlib only.
- `AGENTS.md`: only place that documents the extension install convention.

Not kept on purpose: `src/z0int` (the `z0int doctor` call in
`setup-flyforge.sh` is optional and would pull in the CLI hub, about 280 files).

## To earn a place on master

- flyforge-recovery: one paired OMP run on tasks that hit tool errors, hint on
  vs off, measuring frontier tokens and wall time per recovered failure. If it
  wins, take `omp-extensions/flyforge-recovery/` (2 files, 326 lines) and
  nothing else, and consider moving it to evolution-lab where its logic lives.
- vllm-jev: a running vLLM structured-read server and one task where
  `vllm_decide` replaces a frontier call. Without a server there is nothing to
  measure. Smallest subset: `omp-extensions/vllm-jev/index.ts` minus the shadow
  call and the system-prompt line.
- flyforge-jev: no path. It is log-only by its own label; its data belongs in
  evolution-lab, off the turn.

Uncertain: whether the recovery hint ever changed a model's next action. No log
records it.
