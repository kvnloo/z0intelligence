# Repository reuse and scoped correction guard

The existing context resolver can select a canonical component from a registry,
match its Git origin to a supplied checkout or worktree, and discover code with
the resident FFF index. An explicit matching checkout wins; missing, wrong,
ambiguous or unavailable roots produce gaps. Search never defaults to this
package's checkout when the task root is missing.

```sh
python -m z0int context resolve --query normalize_rows \
  --canonical-repo component-id --registry-path /absolute/components.yaml \
  --candidate-root /absolute/checkout --project-root /absolute/checkout \
  --no-qmd --json
```

The registry file, origin, HEAD, branch, worktree bytes/modes/submodule state,
FFF generation and hydrated files are versioned separately. FFF completeness
describes its bounded query over eligible indexed files; it does not prove
semantic recall or inclusion of ignored files. Partial results and provider
failures remain visible. A complete empty query does not establish novelty;
`NOVEL` stays unavailable.

The resident bridge adds `reuse_resolve`, `reuse_injected`, `reuse_model_input`
and `reuse_check` to its existing JSONL protocol. Its generation-local turn cache
holds prepared packets; EventLog and DecisionReceipt remain the durable stores.
Discovery hits are hydrated before they become code candidates. Candidate test
links come from the selected repository's revision-bound `zer0.repo.yaml`
`architecture.subsystems[].paths` groups. Missing or invalid ownership evidence
produces `OBSERVE`. A subsystem link is an ownership hypothesis; independent
test execution must establish the task outcome.

## Independent outcome join

`reuse_resolve` accepts an optional `verifier_binding` with a verifier ID, a
candidate ID, an argument vector, and repository-relative test paths. The paths
must be hydrated owning tests for that candidate and must appear as exact
arguments. The prepared packet ID binds this declaration to its test evidence;
the mutation check revalidates it and records the preparation, model-input, and
mutation EventLog IDs under the same trace. The existing MemoryUseReceipt and
DecisionReceipt `extra` carry those links. Omitting the binding keeps ordinary
reuse consumers compatible.

The binding names work for a future independent verifier. It does not run the
command, authorize a mutation, or prove success. After the actual change, an
independent verifier must run the declared command and join its result through
the existing receipt `join_outcome` API or `receipt join` CLI, using
`verification_source` as the result reference and `verified_success` as the
verdict. This supports a result arriving after the harness has closed the turn.
An initial `turn_close` can also carry the result when it is already available.
A turn close without that
independent result remains execution evidence and does not become verified
success.

OMP configuration is opt-in:

| Variable | Meaning |
| --- | --- |
| `OMP_Z0INT_REUSE_MODE` | `off` (default), `shadow`, or `enforce` |
| `OMP_Z0INT_REUSE_CANONICAL_REPO` | Registry component ID or repository slug |
| `OMP_Z0INT_REUSE_REGISTRY_PATH` | Explicit registry file |
| `OMP_Z0INT_REUSE_CANDIDATE_ROOTS` | JSON array of checkout paths |
| `OMP_Z0INT_REUSE_TASK_ID` | Stable workstream ID, separate from turn trace |
| `OMP_Z0INT_REUSE_MEMORY_SCOPE` | Optional JSON object with explicit user/project/repo/task |
| `OMP_Z0INT_REUSE_MEMORY_SUBJECT` | Optional subject filter for current decisions and discovery hints |
| `OMP_Z0INT_REUSE_MEMORY_PREDICATE` | Optional predicate filter for current decisions and discovery hints |
| `OMP_Z0INT_REUSE_VERIFIER` | Optional JSON verifier binding with candidate ID, argv and owning test paths |

When memory scope is configured, its task supplies the fallback workstream ID.
The bridge rejects mismatched task or canonical repository scopes. A required
admitted decision cannot be satisfied by a model assertion.

Enforce checks native write/edit target paths against the prepared root before
execution. Bash, custom mutators, unresolved edit inputs, repository escapes,
symlinks outside the root and `.git` targets are unsupported. Read/search tools
remain available. Shadow records proposed checks without blocking.

The prepared witness belongs to the primary conversation agent. Advisor and
auto-learn tools carry their own agent identities and cannot inherit its
readiness. Enforce blocks their mutations; shadow records that their evidence is
unqualified. Their inputs, session side requests and background cache-warm
replays do not emit the primary final-payload event.

A context callback receipt alone cannot unlock a mutation: later extensions can
replace context. The final provider request must witness the exact context block
and serialized request hash, bound to the current injection ID, trace and
session. A new context invocation revokes the old witness. Readiness also fails
when fresh source observations differ or become unavailable. These observations
never grant tool authorization or establish provider delivery, model use or
verified success.

The final observer requires the companion OMP patch based on
`v18.8.1@4ff490a1cd3e567e0f4d0bc76643bf679fac4138`. It adds the read-only
`provider_payload_finalized` event after payload replacements and serialization.
Only Ollama Chat and OpenAI Completions are covered. Unpatched OMP and unsupported
providers produce no final witness, so enforce remains blocked. This change
does not activate an installed harness or change its approval settings.

Scoped lookup also works through the existing CLI:

```sh
python -m z0int context resolve --allow-memory \
  --memory-scope /absolute/scope.json \
  --memory-subject delegated_agent --memory-predicate configuration \
  --no-qmd --json
```

The scope file contains explicit contiguous `user`, `project`, `repo` and `task`
segments. Missing scope is an unavailable required operation. Visibility is
filtered before reduction. Validated explicit user decisions outrank model
assertions; rejected lower-trust conflicts remain in history. Conflicting user
decisions require an explicit correction link. Correction evidence includes its
validated base sources so unchanged inherited fields retain provenance.

The trusted in-process native admission entry is
`z0int.memory.native_preferences.admit_delegated_agent_config`. It hydrates exact
AgentsView session ordinals and recognizes a deliberately narrow explicit Luna
configuration grammar. It requires a host workstream binding, references-only
ingestion opt-in, and later same-session ordinals for corrections. Source bodies
are checked in memory; only references and bounded normalized claims enter the
canonical EventLog. Generic writers cannot set native admission markers. There
is no model-facing claim-write command or automatic capture activation.

The existing Hermes plugin supports an explicit `context_memory_scope` setting
and optional exact `context_memory_subject` and `context_memory_predicate`
selectors. It reads the same canonical projection and omits incomplete or
unavailable memory. Its companion native `provider_request_body` hook observes
the main HTTPX request after serialization. The consumer binds inclusion to the
current human user row and records the exact body hash/count, packet fingerprint
and canonical EventLog reference in `DecisionReceipt.extra`. It stores no raw
request body. Missing or unsupported body capture stays unavailable. The
setting does not enable automatic integration or change installed profiles.

Tests cover current and stale evidence, scope/ownership failures, provider
coverage, replay, native correction ordering, model assertions, actual OMP hook
and native-tool boundaries, and supported provider serialization using mocked
transport. Mock transport tests do not establish live model reuse. The private
fresh Codex client observation is unqualified as a study result because its full
provider request was unavailable; the versioned z0evals #88/#56 contract records
that gap explicitly.
