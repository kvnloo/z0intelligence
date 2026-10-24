# AODL intent gate

This package replaces #215's sentence-coverage block with AODL task identities.
It keeps the existing content-addressed installer. It does not enable itself.

## Authority and source

IntentService reconciles a message with unified memory and goals. It compiles the
HOTL 0.2 contract. Kevin approves the exact contract before CONTRACT-tier work.
The gate does not expose an agent-filled compilation or approval tool.

The service writes approved documents to
`$Z0INT_HOME/intent/<session_id>/<contract_id>.json` (default home: `~/.z0int`).
Only documents with `plan.bindings.approval.status = approved` bind execution.
The approval names Kevin, the session, the contract, its source hash and its
immutable contract hash. The loader checks the file name and real source bytes.
Pending drafts live outside this directory's root JSON documents.

Each source sentence retains an exact slice and its session entry citation.
Task and verifier nodes use canonical AODL kinds. Application fields reside in
`plan.bindings`, not custom node fields or a second intent format. Non-asks have
explicit dispositions and reasons. The compiler determines actionability and
acceptance. The deterministic gate cannot prove those semantic choices correct.

The service appends triage receipts to `triage.jsonl`:
`{entry_id,sourceHash,tier,override,features,rule_ids,slm_used,ts}`.
`sourceHash` is SHA256 of the exact UTF-8 source text. FAST and CHECK do not need
approval. CONTRACT does. Missing or stale triage fails closed for mutations.
Read-only tools are an explicit allowlist. Unknown tools, Bash and Eval count as
potential mutations. The tool gate is not a sandbox for arbitrary code.

For the pre-input approval UI, the service records a real OMP custom entry
`z0-intent-input` with `rawText`, `sha256`, `ts`, `extension_content_sha`, and
`kind` (`input`, `edit`, or `reject`). Its actual session entry ID is the citation.
The gate checks its stamp against the installed `z0-intent-service` extension,
and compares the following user entry with the documented prefix/context
transformation. It does not invent a user entry ID. A mismatch fails closed.

## Runtime tool

`intent` supports only:

- `view`: show approved contracts and runtime evidence.
- `verify`, with `taskId`: execute that task's approved acceptance verifier.
- `verify-precondition`, with `taskId` and `verifierId`: observe an approved
  prerequisite before the task may rely on claimed or unknown memory state.
- `drop`, with `taskId` and a nonempty `reason`: record an explicit disposition.
- `delegate`, with `taskId` and `delegateId`: attach a native, already live child.

Acceptance and target identities cannot change after approval. An amendment
requires a new service contract and Kevin's approval.
Approved amendments carry `compiler.parent_contract_id` and
`compiler.parent_contractHash`. Only approved leaf snapshots supersede parents.
Each prior task retains its identity and evidence or has an explicit reasoned
drop. Pending amendments never replace approved authority.

The fixed verifier set is:

| Type | Required identity/property |
| --- | --- |
| `command_exit_zero` | explicit `cmd`, exact `target` |
| `file_contains` | `path = target`, literal `pattern` |
| `url_status` | HTTP(S) `url = target`, integer `code` |
| `pr_state` | `repo`, PR `number`, full `head_sha`, `state`; target `repo#number@sha` |
| `service_active` | `unit = target`, local `systemctl is-active` |
| `metric_threshold` | Prometheus HTTP host `target`, `query`, `op`, numeric `value` |
| `human_review` | exact `target`, later explicit user outcome approval |

Human outcome approval is separate from contract approval. Send
`APPROVE <exact target>` or `I approve <exact target>.` as a real user entry.
The verifier records the actual approval entry and sentence. The agent cannot
approve a human review. A metric query must yield exactly one finite value.
Only the explicit command verifier uses a shell. Service checks are local. For
a remote unit use an approved command with its host in the target identity.

Tool results become canonical AODL `stateUpdate` eventLog records with their
call ID, tool name, observed exit metadata and host receipt timestamps. Missing
exit codes remain unknown, not invented zeroes. Arbitrary successful tools do
not close tasks. Only a passing acceptance verifier after the ask creates the
causally linked verified stateUpdate. Failed verification leaves the task open.

Native child lifecycle events bind live delegates to forwarded tasks. A final
assistant result from the child's recorded session must arrive before verifier
execution. A status-only completion does not close a task. After an extension
restart, persisted delegate liveness becomes unknown and cannot exempt I1.
OMP does not emit `session_stop` for subagents. This package enforces the parent
contract and result boundary, not child stop hooks.

## Laws and relay protocol

- I0: CONTRACT mutations require the exact Kevin-approved session contract.
- I1: no turn end with an open task, except an explicit reasoned drop or a live
  native delegate. Delegated tasks remain open until result plus verification.
- I2: a done/covered claim requires a verified stateUpdate.
- I3: task, approved verifier and execution use the same target identity.
- I4: a relay carries the task IDs it forwards.

The existing relay surfaces remain: `task` context and every task body,
`write agent://...` content, and literal `tern run` / `tern send ... paste`
arguments. Each non-acknowledgement payload carries one line:

```text
TASKS: contract.task1, contract.task2
The rewritten request follows here.
```

The conservative gate requires all currently open approved task IDs in each
work-bearing relay. It does not infer which tasks an English rewrite includes.
Pure `ACK-ONLY: understood` acknowledgements use the existing narrow vocabulary.
No COVERAGE or ASKS compatibility format remains. `omp --show` shows receipts.

Final replies may name `DONE: <task ids>` or `COVERED: <task ids>`. Unqualified
completion words conservatively claim all approved tasks. This is not a complete
natural-language claim detector. The open-task gate is independent of this
lexical check, so silence does not remove an unresolved task.

The native `session_stop` hook blocks and reinjects unresolved tasks at most
three times. It persists the cap across extension reloads. At the cap it shows
Kevin the unresolved list and stops automatic continuation. It never records
success at the cap. Tool authority remains closed. Runtime state is separate
from the immutable approved contract under `runtime/`. Corrupt state does not
silently become an empty contract.

## Formal scope and checks

`bend/intent_gate` models I0–I4 with 37 theorem statements and 20 mutations.
Verifier execution starts strictly after the ask. Delegate results precede it.
The mutant harness first requires a well-typed mutant. Parse, tool and type
errors do not count as rejected behavioral mutants.

These are abstract decision-model obligations. They do not prove faithful
source classification, approval authentication, timestamp normalization,
canonical serialization, real command success or full production equivalence.
Python/JavaScript parity covers controlled synthetic fixtures. The private
20-case replay distinguishes hypothetical approved reconstruction from the
historical transcript and reports wrong-property limits.

Run verification on MBP only. Use Python 3 venv, not uv:

```sh
node --test omp-extensions/z0-relay-guard/*.test.mjs
python -m pytest -q tests/test_intent_laws.py
(cd bend/intent_gate && "$BEND" PROOF.bend --verdict)
python3 bend/intent_gate/mutations.py --verdict --out mutations.json
```

## Install, do not apply automatically

Use the existing `scripts/install-z0-relay-guard.sh <full-40-character-sha>`.
It archives committed bytes into `~/.local/lib/z0-relay-guard/<content-sha256>`
and atomically points the OMP extension link at that immutable snapshot. The
`.content-sha256` stamp is excluded from its own digest. No worktree link is
installed. IntentService has its own stamped client/service bundle installer.
The operational report gives exact reviewed commit commands. This change does
not install either package into the live home.
