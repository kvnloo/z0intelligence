# ctx history bakeoff (#116, handoff step 6)

`run.py` runs one private, real-history question set through the current z0 history path and through the
read-only ctx capability. Each arm runs in its own child process, and the results are graded against known
answers.

| arm | path |
|---|---|
| `control` | `context_resolve.resolve_context` without ctx, imported from `--control-src`. It uses qmd, plus the z0 memory surface (AgentsView FTS5, opened read-only) when that tree has one. |
| `ctx_lexical` | `CtxHistoryCapability.search`: lexical, `--refresh off`, limit 5 (the seam's limit) |
| `ctx_exact_hydration` | `ctx_lexical`, plus `ctx show event --window 3` on the top `--hydrate-top` hits. Each window injects at most `--hydrate-cap` chars, target event first. |
| `ctx_hybrid` | Runs only when `--allow-ctx-semantic` is passed **and** `ctx status` reports a ready local semantic backend. A remote executor is refused. Otherwise every row records `not_run:` or `unavailable:` with the reason. |

**Grading.** Each question carries a known answer as regexes (`all`, `any`). An arm answers a question when a
single injected evidence item matches the answer. The first such item is the cited source. Grading is
deterministic: no model is involved.

**Per-row metrics.** These are recorded for every question in every arm:
- the generation or revision;
- the evidence ids;
- the cited id, its rank and its source system;
- latency, cold (first run) and warm (best later run);
- bytes opened;
- injected excerpt chars and tokens (chars/4);
- abstention and the error class.

**Control-only grades.** The control rows also carry two extra grades:
- `answered_fresh`: AgentsView evidence newer than `--fresh-cutoff` (the ctx publish time) is ignored. This gives a freshness-matched comparison.
- `answered_covered`: only evidence from `--covered-providers` (the providers ctx indexed) is kept. This gives a coverage-matched comparison.

`--cutoff` drops control evidence observed at or after that time. Use it to keep the session that writes the
question set out of the control.

## Run

```bash
source ctxenv.sh          # CTX_DATA_ROOT for an isolated, manually indexed root
python benchmarks/ctx_history/run.py \
  --questions /private/questions.jsonl \
  --private-dir /private/out --results-dir benchmarks/ctx_history/results/<run> \
  --control-src /path/to/z0-tree/src \
  --cutoff <session start> --fresh-cutoff <ctx publish time> --covered-providers claude,hermes
```

Question rows are JSONL, with these fields:
- `id`, `category`, `topic`, `question`;
- `answer`: `{"all": [...], "any": [...]}`;
- `source`: the citation for the known answer.

The questions stay private. Only their ids, topics and sha256 prefixes reach `results/`.

## Privacy

- **`--private-dir`** gets the full rows, including the evidence text each arm would inject. Never commit it.
- **`--results-dir`** gets only what `sanitize()` lets through:
  - question ids and hashes;
  - metrics;
  - opaque ctx and AgentsView ids;
  - every other locator as `h:<sha256 prefix>`;
  - error class names.

## Read-only

- **ctx.** The ctx arms run only `ctx search --refresh off` and `ctx show event`, with the adapter's read-only env. The summary records the command kinds and how many files changed in `CTX_DATA_ROOT`.
- **Control.** The control opens AgentsView through `agentsview_ro.connect` (sqlite `mode=ro`). Run it with an isolated `Z0INT_HOME`: the memory surface's single-injector guard writes a turn marker there.
