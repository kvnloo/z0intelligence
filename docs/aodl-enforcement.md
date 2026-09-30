# AODL enforcement: from an authored intent contract to action-level allow / ask / deny

Status: **shadow** (branch `feat/aodl-grants-v0`, off `feat/action-authority-v0`). Nothing is blocked yet.
This page describes how the typed AODL intent/authority contract feeds the action-level check, and the evidence
each rollout stage has to meet before it can go live.

## The pieces

| Piece | Where | Role |
|---|---|---|
| Intent contract | AODL HOTL 0.2 document (kvnloo/aodl) | Declares who may do what, and exactly where. It is authored, never inferred. |
| Target scopes | aodl `feat/authority-scopes`: node `authorityScopes` + `prohibitions` (`spec/authority-scopes.md`) | Narrows `authorityCeiling` kinds to exact repos/branches/hosts/paths. |
| Fingerprint | aodl `aodl-canon-1` (`semantic_fingerprint`) | Names the exact contract revision each grant came from. |
| Compiler | `src/z0int/aodl_grants.py` | Validated doc -> exact grants + prohibitions with provenance `{source: "aodl", fingerprint, node}`. |
| Check | `action_authority.authority_check` | Compares grants with the effects of the actual tool call (`action_effects`). |
| Hook | `action_hook` (Claude Code PreToolUse, shadow) | Loads the contract for privileged calls and logs the decision to `actions.jsonl`. |

`decision_opportunity._authority` keeps using `authorityCeiling` as coarse effect *classes*
(read/write/privileged) and fails closed the same way. This page is about the per-action layer.

## Declaring the contract

A user or a planning step writes one or both of these files. Both are read, and each one is validated,
fingerprinted and pinned on its own:

```
~/.z0int/state/claude-code/intent.aodl.json   # session-level: applies to every Claude Code session
<repo>/.aodl/intent.json                       # per-repo: relative "repos" entries resolve against <repo>
```

Minimal executor node (the rest of the document is ordinary HOTL 0.2):

```json
{"id": "agent", "kind": "executor", "harness": "claude",
 "ports": [{"id": "in", "direction": "in", "schema": "Task"}, {"id": "out", "direction": "out", "schema": "Task"}],
 "capabilities": ["execute"],
 "authorityCeiling": ["execute", "push", "pr"],
 "authorityScopes": [
   {"effect": "push", "targets": {"repos": ["~/workspace/z0intelligence"], "branches": ["feat/aodl-grants-v0"]}},
   {"effect": "pr",   "targets": {"repos": ["~/workspace/z0intelligence"]}}],
 "prohibitions": [{"effect": "push", "targets": {"branches": ["master", "main"]}}, {"effect": "merge"}]}
```

```
python -m z0int.aodl_grants show [--cwd DIR] [--require-pin]   # what compiles here, and what was dropped and why
python -m z0int.aodl_grants pin PATH                           # approve the current fingerprint (interactive TTY only)
```

The hook needs `aodl_contract` importable from its interpreter. Install it editable from the aodl branch
(`uv pip install -e ~/workspace/aodl-scopes`) so that `harnesses/catalog.json` resolves. If the validator
cannot be imported, the contract is `rejected` and grants nothing.

## Compile rules (all fail closed)

1. **Validate, then fingerprint.** Unreadable JSON, a missing validator, an unknown `specVersion`, or any
   validator issue gives `status: rejected` with no grants and no prohibitions. The conversation-derived grants
   are unaffected.
2. **Pin.** `aodl-pins.json` (state dir) maps a contract's real path to the fingerprint the user approved.
   A pinned contract whose fingerprint moved is `pin_mismatch` and grants nothing, so an edit to the contract
   (including an agent editing an in-repo `.aodl/intent.json`) can only revoke. Whitespace and key order are not
   edits (canonical fingerprint). Unpinned contracts compile with `pinned: false`; `require_pin=True`
   (`Z0INT_AODL_REQUIRE_PIN=1`) makes them `unpinned` with no grants. Enforcing stages require it.
3. **Who is granted.** Only `executor` nodes whose `harness` is `claude` produce grants. Scopes on a `codex`
   executor are dropped, with the reason recorded. **Prohibitions come from every node** and bind every executor.
4. **Exact scope per kind.** A scope must name the target keys that make its kind exact. Repo-relative kinds also
   need `repos`, which is matched by git common dir, so a repo scope covers its worktrees and nothing else:

   | kind | required target key(s) | also allowed | `repos` required |
   |---|---|---|---|
   | push, commit, force, rewrite | branches | | yes |
   | merge | branches or prs | | yes |
   | delete | branches or paths | | only for branches |
   | fs_outside_repo | paths | | no |
   | ssh, network | hosts | | no |
   | service | units | hosts | no |
   | install | packages | | no |
   | deploy, publish | envs (namespace / env / registry) | | no |
   | pr, comment, issue, ci, discard | (none: the kind is the scope) | | yes |
   | secret, sudo, message, external_system, upload | never granted by a contract: the effect carries no target a scope can narrow | | |
   | read, write, execute | capability classes, not action kinds; standing authority decides | | |

   Unknown kinds, keys that do not narrow the kind, and repos that do not resolve are all dropped, each with a
   reason in `dropped`. Wildcards (`*`, `any`, `all`, `non_default`, ...) are already rejected by the aodl
   validator.
5. **Strict coverage.** Contract grants carry `scope.strict`, so `covers()` uses `strict_covers`. The effect must
   have the same kind (a push scope never covers force). Every named key has to match the effect's actual target:
   `all`/`unknown`/`pr_base` branches never match, every package has to be named, and `gh --repo other/x` from
   inside the granted repo does not match (origin slug is compared).
6. **Precedence.** Contract grants have `turn = 0`, so a later in-session prohibition ("no PRs") still wins.
   Contract prohibitions have `turn = 10^9` and win over every grant, whether from the conversation or the
   contract. A targetless `push` prohibition also forbids force. A prohibition whose `repos` cannot be resolved
   still binds (conservative).
7. **Not persisted.** Contract grants are recompiled for each privileged call and never folded into the session
   cache, so edits take effect on the next call. The compiled result is cached under `state/aodl_cache/`, keyed on
   the file's exact bytes plus pin, harness and repo; any byte change misses the cache.

Tests: `tests/test_aodl_grants.py` covers every grantable class (in-scope allow, out-of-scope not allow), never-granted
kinds, scope narrowing, repo/worktree scoping, prohibition precedence in all four directions, invalid documents,
a missing validator, fingerprint mismatch after an edit (grant revoked), pin gating, cache behaviour and the
shadow hook end to end. The aodl side is in `tests/test_authority_scopes.py` plus fixtures.

## Known gaps (fix, or explicitly accept, before stage 2)

- **The state dir is trusted.** The contract, `aodl-pins.json`, the compile cache and the session cache all live
  under `~/.z0int/state/claude-code/`. Writing there is `fs_outside_repo` (privileged) when the parser sees it.
  A script that the parser classifies as `unknown_command` (write) could still forge a pin or cache entry. The TTY
  requirement on `pin` stops the CLI path but not a raw file write.
- **Effects that carry no repo.** `git -C /other push` or `cd /other && git push` are checked against the session
  repo's contract, because push effects have no repo field. Commit, merge and rebase effects do carry one and are
  checked.
- **Remotes are not scoped.** A `branches` scope covers that branch on any remote.
- **Parser recall bounds everything.** v0 missed 16 of 134 unauthorized privileged calls at the parser: no effect,
  so there was nothing to check. A contract cannot fix a call that is never seen as privileged.

## Staged rollout

Enforcement is **contract-scoped**. A stage only acts on privileged calls whose effect kind is named by an
*active, pinned* contract (in a scope or a prohibition). Every other call stays in shadow, whatever the stage.
This lets enforcement grow kind by kind as contracts are written, without first having to fix the v0 lexicon's
misses on its own.

The measurement protocol is `benchmarks/action_authority/PREREG_v0.md` (census of privileged candidates plus random
non-candidates, fresh labelling agents, counts only committed). It is re-run on the stage's window, and a new
PREREG file is committed **before** the window opens. "Contract-kind calls" means privileged calls in that window
whose parsed kind is named by the active pinned contract.

### Stage 0: shadow log (this branch)

The hook logs `decision`, per-effect provenance (`grant.fingerprint`, `grant.node`) and `aodl.contracts[]` status.
It prints nothing and exits 0; Claude Code decides exactly as it does without the hook.

Entry bar (met here): the unit suite passes (58 AODL cases plus the v0 suite); an invalid, missing, or
fingerprint-mismatched contract yields zero grants; the hook stays silent and fail-open with a contract present.

### Stage 1: ask

The hook returns `permissionDecision: "ask"` for contract-kind calls whose decision is `ask` or `deny`.
Claude Code then shows its native prompt, and the user's answer becomes a live label. Not implemented yet: the
hook is shadow-only on this branch.

Evidence bar. Every item must hold on one pre-registered shadow window:

1. Window of at least 14 days and at least 200 contract-kind calls, across at least 3 sessions in at least 2 repos.
   This addresses v0's single-principal caveat.
2. **Catch: 100%.** Every gold-unauthorized contract-kind call is `ask` or `deny`.
3. **Zero over-broad contract grants.** No gold-unauthorized call is `allow`ed with `provenance.grant.source ==
   "aodl"`. One case blocks promotion and becomes a regression test first.
4. **False friction ≤ 5%** (unweighted) on gold-authorized contract-kind calls.
5. **Parser (kind, target) precision ≥ 95%** on contract-kind calls, from at least 100 labelled calls. v0 overall was
   72.1%; stage 1 promotes only the kinds that clear this bar.
6. **Pin integrity.** Every fingerprint transition in `actions.jsonl` is either followed by `pin_mismatch` until a
   TTY pin, or corresponds to a user-run `pin`. Zero unexplained `active` fingerprints.
7. **Latency and failure.** Hook wall p95 < 50 ms in a fresh interpreter with a contract present, measured on an
   unloaded machine as in v0. Hook exceptions < 0.1% of calls, and an exception never yields a decision (fail-open).

### Stage 2: deny

The hook returns `deny` for contract-kind calls whose decision is `deny`, and keeps `ask` for `ask`.

Evidence bar, on a pre-registered ask-stage window:

1. At least 30 days in stage 1 and at least 500 contract-kind calls across at least 3 repos.
2. **Zero false denies, with a non-vacuous count.** Among at least 30 calls the check would deny, the user
   approved the native prompt 0 times. Every deny decision in the window is labelled; 0 are gold-authorized.
3. Stage-1 bars 2 to 7 still hold on this window.
4. **Known gaps closed.** The state dir is unwritable from agent tools: a Claude Code permission or sandbox rule denies
   Edit/Write/Bash writes under `~/.z0int/state/claude-code/`, verified by an agent attempt that fails. Push effects
   carry their repo, or a `-C` / `cd` into another repo is `ask`.
5. **Kill switch tested.** One env var (`Z0INT_AODL_CONTRACT=0` today) returns the hook to shadow within one call,
   with no restart.

### When

Stage 0 can start as soon as this branch is merged and the hook's interpreter has `aodl_contract`. The earliest
stage 1 is one 14-day window plus a labelling pass after that, if the bars hold. The earliest stage 2 is at least
30 days after stage 1. These are gates, not dates: a failed bar restarts that stage's window after the fix,
with the fix pre-registered.
