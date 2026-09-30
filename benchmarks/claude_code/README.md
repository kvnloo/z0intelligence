# Claude Code paired arms (z0evals-style)

- `run_pair.py` — same task instance per arm, arms interleaved/reversed per rep, create-only JSONL,
  Claude Code's own billed usage + plugin Tokenomics receipts + programmatic verifier.
- `attribute.py` — estimated replay attribution by tool (chars/4 x later requests). Locating, not billing.
- `tasks/` — pinned fixtures (`source.sha` archived at run time; `sabotage` for debug tasks; hidden tests).

Known harness confound: every trial runs in a fresh temp dir, so each trial is a *cold* session start.
Claude Code's cross-session prefix cache is keyed by cwd, so fixed-prefix effects are overstated
relative to repeated sessions in one directory (they are representative of fresh worktrees).
