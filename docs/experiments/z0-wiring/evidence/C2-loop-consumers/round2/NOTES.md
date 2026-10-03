# C2-loop-consumers, round 3 fix (verdict: ../../../round2/C2-last-verdict.json)

Worktree /mnt/zer0models/z0-wt/wiring/wt/C2-loop-consumers, branch feat/wire-loop-consumers-20261003 (new commits
only, no rewrite, not pushed). Previous head f20c2b7 -> HEAD 9de0d53:
  206e254 test: red tests (exit frame per harness, table-less export/merge, legacy label source, v0.39 Codex cohort)
  c777ebf fix: exit evidence from each harness's own frame; v0.39 Codex cohort is unknown        (turn_readers.py)
  fdbafba fix: export/merge write the index with no table; legacy manifests carry label_source  (loop_export.py)
  4c29471 test: merge counts turn keys that hosts placed in different cohorts
  9de0d53 fix: merge index cross_cohort_turn_keys                                               (loop_export.py)
Previous round-2 evidence moved to prev-f20c2b7/ (prev-0a73899/ kept).

## Findings -> fixes
blocker exit_code: exit_code(text, status, harness) now reads only the harness frame. OMP/OMO: a trailing
  'Command exited with code N' line anchored at the end of the result. Codex: 'Process exited with code N' or
  'Exit code: N' in the header before the first 'Output:' line (first line when there is no 'Output:'). Hermes/DSH:
  json.loads and the top-level exit_code (int, not bool). Grok: always None. The AgentsView reader passes the join
  rule's harness down (turns(av_id, cwd, harness) -> _tool -> exit_code).
major table-less export/merge: _write_tables creates out_dir before writing; export on an empty home or a home with
  only failure rows writes manifest.json with the failure counts; merge of two table-less sets writes an empty index.
minor legacy polarity: training tables of cohort legacy get label_source='effective_tier' and no
  test_label_polarity; AgentsView-labelled tables keep their polarity.
minor v0.39 Codex cohort: classify_cohort returns 'unknown' for a Codex session whose row has no session_kind key
  (v0.39 schema) and no automation flag; documented in the codex JoinRule doc and the classify_cohort docstring.
  Plus the optional part: the merge index counts turn keys that hosts placed in different training cohorts
  (cross_cohort_turn_keys, per harness). The `loop merge` CLI stdout is unchanged; the count is in manifest.json.

## Red -> green
red-attempt1-testbug.txt: the first red run; 8 Codex cases hit KeyError 'codex' in the test helper SHELL_TOOL (a
  test bug, fixed by adding codex there before any implementation). Kept for honesty.
red.txt (tests only, HEAD f20c2b7 + test changes; committed as 206e254): 49 failed / 109 passed. Intended reasons:
  24 frame cases end verified_success (the blocker reproduced on omp/omo/grok/dsh/hermes/codex 'Exit code:' header);
  19 fail on exit_code() taking no harness argument (the missing per-harness API: codex 'Process exited' header cases,
  where the old first-match happened to be right, plus the existing unit test updated to pass the harness);
  3 FileNotFoundError (table-less export/merge/CLI); legacy manifest has test_label_polarity; v0.39 Codex cohort
  'interactive' != 'unknown' (x2, one of them the existing v0.39 test whose expectation changed with the spec rule).
red-merge-count.txt (4c29471 before 9de0d53): 1 failed (KeyError cross_cohort_turn_keys) / 158 passed.
green.txt at 9de0d53: 159/159.
Test expectation changed: test_v039_schema_without_session_kind_joins_and_classifies now expects 'unknown' for the
  unflagged Codex sessions (was 'interactive'), per SPEC "Codex exec runs never pooled into interactive" and the
  verdict's suggested fix. test_exit_code_reads_the_omp_notice... now passes the harness.

## Suite, guard, e2e
suite.txt: base 914dc99 11 failed (same set) / head 9de0d53 11 failed, identical set, no new failures.
CC regression guard byte-identical to 6fee859. e2e.txt: round-3 hosts D-H plus every earlier e2e step, exit 0.

## Behaviour change for A0 / C6
Exit-like text in output no longer labels a run. On hosts still on AgentsView v0.39 every Codex session not flagged
is_automated is cohort unknown (counted, not interactive); the live DB is v113 so this affects other hosts only.
Merged indexes carry cross_cohort_turn_keys.

## Rules
Everything under flock -s quiet-lane.lock; e2e also under hostless; isolated homes under homes/C2-loop-consumers/
(r3-red, r3-red2, r3-green, r3-green2, r3b-suite-*, r3c-suite-head, golden, e2e); socket guard 0 violations;
synthetic fixtures only; the live sessions.db was opened only for `.schema` (mode=ro); no TencentDB (no memory
feature in C2), no z0 service, ~/.z0int, ~/.dsh, ~/.hermes or Hermes profile access. The temporary base worktree was
first created by mistake under /mnt/zer0models/z0-wt/z0intelligence/wt/ (relative path), removed at once (and the
empty wt/ dir), recreated at wiring/wt/C2-loop-consumers-base and removed after the base suite run.
