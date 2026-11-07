---
name: z0int-contribute
description: Start contributing to z0intelligence. Use when the user asks for general help with this repo, wants a first task, or says contribute, claim, open a PR, or "what can I work on". Calls the z0int contribute CLI only.
---

# z0int contribute

## Principle

The **`z0int contribute` CLI** holds the protocol (states S0–S7, docs/contrib/CONTRIBUTOR-LAYER.md).
This skill only runs it and follows `next_action`. Do not invent tasks: the only task
source is `.z0/priorities.yaml`.

## Procedure

1. From the repo root, run:

```bash
z0int contribute --json
```

   If `z0int` is missing: `./scripts/bootstrap.sh` (or `python -m venv .venv && .venv/bin/pip install -e '.[test]'`), then run it again.

2. Read the JSON. It always has `state`, `next_action {kind, command, why}`, `needs_human`, `warnings`.
   - `kind: run` → run `next_action.command`, then run `z0int contribute --json` again.
   - `kind: ask_human` → show the pick and both alternatives (from `data.suggestion`) or the
     `needs_human` lines to the user, ask the one question in `why`, and **stop**.
   - `kind: blocked` → tell the user `why` and stop.
   - `kind: done` → report and stop.
3. After the user says yes to a claim: `z0int contribute claim <task-id> --json`.
   Only after a **separate** yes for the public post: `z0int contribute claim <task-id> --post --yes --json`.
4. Work only inside the worktree that `z0int contribute start <task-id>` made (`data.worktree`).
   Commit there, then `z0int contribute evidence <task-id> --json`.
5. `z0int contribute pr <task-id> --json` writes the PR body. Only after the user's explicit yes
   to push and open a draft PR: `z0int contribute pr <task-id> --open --yes --json`.

## Do not

- Post to GitHub, comment, label, push, or open a PR without the user's explicit yes for that exact action.
- Push to origin, or target `dev`, `preview` or `nightly`. PRs target the default branch (`master`) from the contributor's fork.
- Claim success without a receipt from `z0int contribute evidence` on the exact HEAD.
- Read or paste the user's private files (credentials, transcripts, settings) into the PR or a prompt.
- Use grok or cursor as an LLM provider.
