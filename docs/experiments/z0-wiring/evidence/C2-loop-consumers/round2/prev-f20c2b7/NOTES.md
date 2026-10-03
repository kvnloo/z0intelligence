# C2-loop-consumers, build round 2 (findings: 1 blocker, 1 minor)

Worktree /mnt/zer0models/z0-wt/wiring/wt/C2-loop-consumers, branch feat/wire-loop-consumers-20261003. New commits
on 0a73899, nothing rewritten, not pushed. The previous fix round's evidence is kept in prev-0a73899/.

Test-only commits (red): f8132c8, 5a06cfb, ef229e2, f5e4118, c7332e5, e537c14. Fix commits: df247e5, f20c2b7.

| finding | red | fix |
|---|---|---|
| blocker: Grok 'completed' counted as exit 0 | test_grok_completed_tool_call_without_exit_line_is_never_a_pass (pytest failed, traceback, fatal, cargo FAILED, even a pass), test_codex_completed_status_is_not_exit_evidence_but_its_exit_line_is, exit_code unit (`'completed'` -> None). The signal-semantics test now writes each harness's real shell shape (SHELL_SHAPE: Grok run_terminal_command + 'completed' + no exit line; Codex 'Process exited with code N'; Hermes JSON exit_code; OMP/OMO notice on failure only; DSH no code) instead of the CC 'Exit code N' shape | df247e5: exit_code() reads only the text's exit-code line; the completed -> 0 fallback is gone for every AgentsView harness (no parser maps a non-zero exit to errored) |
| found by the new semantics test: OMP/OMO/DSH (and Grok after the fix) lost commit/revert labels, because commit credit needed exit == 0 | semantics test t2 (commit_reverted missing for omp/omo/dsh at red) | df247e5: a commit whose exit is unknown counts when git printed '[branch sha] subject' (call['committed'], set only by the AgentsView reader; CC output byte-identical) |
| minor: one-sided test labels | test_report_and_table_manifests_record_the_test_label_polarity (x6), test_cli_verify_json_names_the_test_label_polarity, semantics test per polarity | df247e5 + f20c2b7: JoinRule.test_labels -> `test_label_polarity` in the verify report, the CLI --json report and every exported training table manifest of an AgentsView harness: codex/hermes both, omp/omo failure_only, dsh sparse, grok none. Claude Code reports and tables do not get the key |

Red evidence: red.txt at f5e4118 (24 failed / 86 passed): Grok semantics x2 (tests +1 at line 163), omp/omo/dsh
semantics x6 (commit_reverted missing), hermes/codex semantics x4 (missing polarity key), exit_code unit (0 is not
None), Grok shape x4 (tests_in_turn +1 / deterministic_gold), Codex completed (verified_success), polarity x6
(KeyError). The Grok 'fatal' case passed at red already (no test command, no label) and is kept as a guard.
red-cli.txt at e537c14: the CLI polarity test fails on KeyError. green.txt at f20c2b7: 111/111.

Test changes after the first red run (all before the fix was committed, each its own commit, rationale in the
message): the polarity assertion moved after the signal checks (so red shows the Grok label failure, not a
KeyError); ended_on_error counted as exit-derived; polarity 0 (execution_only) allowed where no exit is known.
c7332e5 (t5 is contested only for polarity 'both') was written after seeing the fix's result: without exit
evidence for a pass only the correction cue is left, so verified_failure is the correct state.

Behaviour change for A0: on Grok no shell result is labelled by its exit (polarity none); OMP/OMO failing runs are
labelled, passing runs are not; DSH almost never. Commits, reverts, PRs and correction cues still label every
harness. A0/G-SUFF must read test-derived class balance through test_label_polarity.

Rules: everything ran under flock -s; e2e also under hostless; isolated homes under homes/C2-loop-consumers/
(r3-*, e2e, e2e-pre, golden); socket guard 0 violations; synthetic fixtures only; the live sessions.db was opened
only for `.schema` (mode=ro); no TencentDB, z0 service, ~/.z0int or ~/.dsh access; temporary base/pre-fix worktrees
were removed.
