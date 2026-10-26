# z0int-contribute (Claude Code and Codex plugin)

Ships one skill, `z0int-contribute`, the thin wrapper around `z0int contribute --json`
(spec: `docs/contrib/CONTRIBUTOR-LAYER.md` §1.5). The canonical text is
`skills/z0int-contribute/SKILL.md` at the repo root; `skills/z0int-contribute/SKILL.md`
here is a byte-for-byte copy that `tests/test_contribute_docs.py` keeps in sync.

Codex, opencode and OMP read `AGENTS.md` directly, so they need no plugin for the
start-here text. Hermes gets the same `AGENTS.md` text through its adapter.
