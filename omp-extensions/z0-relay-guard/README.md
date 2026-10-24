# z0 relay guard

This OMP extension permits rewording. It blocks relays that omit an explicit account of the user's cited messages.
It creates no directive ledger, duplicate history, or new ids. It does not modify OMP settings or enable itself live.

## Coverage rule

The extension subscribes to OMP's `tool_call` event and returns `{ block: true, reason }` before execution.
It checks these outgoing payloads:

- Literal `bash` commands with `tern send <id> paste` or `tern run <id>`. Each relay requires its own coverage block.
- The `context` and every `tasks[].task` string in a `task` call. Each string requires its own coverage block.
- The `content` of a `write` call whose path starts with `agent://`.

Use existing session entry ids. No generated directive ids or verbatim-quote headers are required.
A relay carries this block, followed by its freely rewritten body:

```text
COVERAGE:
SOURCE: first-entry-id, later-entry-id
- "Keep running" -> superseded by later-entry-id
- "Try memory experiments" -> covered
- "Stop experiments" -> covered
ADDED-BY-AGENT: none

Please stop the experiment loop. Keep the research results for our discussion.
```

`COVERAGE:` is a readable label. The machine-readable block starts at `SOURCE:`.
Each cited id must identify a root-session user message. Duplicate source ids are invalid.
List sources in discussion order. Later ids can supersede earlier ones.
Every sentence from every cited message must appear in exactly one coverage line.
Repeated sentences require one line for each occurrence. Extra or duplicate lines block the call.

The splitter treats runs of `.`, `?`, `!`, CR, or LF as one boundary. Thus `....` does not create empty sentences.
It removes boundary punctuation and trims sentence-edge whitespace. It preserves internal bytes, case, and Unicode normalization.
Quote the resulting sentence as a JSON string. Escape internal quotation marks and backslashes with JSON syntax.
The decoded string must match the sentence exactly. Separate message text blocks remain separate.

Each line uses one of these dispositions:

- `covered`
- `dropped: <nonempty reason>`
- `superseded by <later cited id>`
- `not-an-ask`

An `ADDED-BY-AGENT:` line is required. Give the agent's additions, or write `none`.
The rewritten body follows that line and must not be empty.
The guard names missing sentences in its block message.

The guard checks deterministic accounting, not semantic truth. It cannot prove that a `covered` sentence appears in the rewritten body.
It cannot prove that an addition declaration is complete, or force the agent to cite every relevant earlier message.
The coverage block makes these choices explicit and reviewable. Use `--show` to see them in chat.

## Acknowledgement and headless exceptions

A pure acknowledgement can omit coverage when it starts with `ACK-ONLY` and contains fewer than 200 Unicode characters.
The deterministic acknowledgement vocabulary is conservative: `ok`, `okay`, `ack`, `acknowledged`, `received`, `thanks`, `thank you`, `yes`, `no`, `understood`, `noted`, or `done`.
Case does not matter. A colon after `ACK-ONLY`, spaces, periods, and exclamation marks are permitted.
For example, `ACK-ONLY: Understood.` passes. `ACK-ONLY: Please run experiments.` does not pass.
A `Parent:` prefix is not an exemption. Replies with work requests require coverage.

If the root session has zero user entries, matched, parseable calls are allowed as headless runs.
OMP's logger records that exception. Missing or malformed history never becomes a zero-entry exception.

## Existing history and child sessions

The extension reads `ctx.sessionManager.getSessionFile()`, `getHeader()`, `getEntries()`, and `getSessionDir()`.
OMP's read-only entries provide current history, including writes that OMP has not flushed yet.
No `input` listener copies messages to a second store.

A child follows recorded `parentSession` references to the root session.
Absolute and relative file references come from session headers. Session-id references resolve against actual headers in the API-provided session directory.
The root's user entries are authoritative. A child's synthetic task prompt cannot substitute for Kevin's message.
Only `type: "message"` entries with `message.role: "user"` qualify. Assistant, system, custom notice, and IRC entries do not qualify.
Missing parents, malformed history, ambiguous parent ids, and ancestry cycles block matched relays.

For `bash`, put coverage inside the actual literal argument sent to Tern. A shell comment does not qualify.
Variable expansion, command substitution, and unsupported nested shell relays fail closed.
The guard checks the listed tool surfaces. It is not a sandbox for arbitrary shell programs, aliases, or other transports.
Matched calls fail closed on parsing errors. Unmatched calls fail open, with parsing errors sent to OMP's logger.
The extension creates no log files and does not log message text. Block reasons include missing sentences for the agent.

## Block message

The agent receives this message. Bracketed fields below show runtime values:

```text
z0-relay-guard BLOCKED: [cause, including missing sentences]. Latest user entry ids: [up to five ids]. Required COVERAGE format: SOURCE: <entry id>[, <entry id>]. Then one line per sentence: - "<exact sentence>" -> covered | dropped: <reason> | superseded by <later cited id> | not-an-ask. End with ADDED-BY-AGENT: <items or none>, then the rewritten body. No Parent: exemption. Pure acknowledgements only: ACK-ONLY: <ack>, under 200 characters.
```

If history cannot be read, the id list says `unavailable`. The relay remains blocked.

## Enable manually

Choose a full, reviewed commit SHA. Then call the installer from that checkout:

```sh
bash /mnt/zer0models/z0-wt/relay-guard/scripts/install-z0-relay-guard.sh <full-commit-sha>
```

The installer reads only the extension's committed bytes at that exact revision.
It copies those bytes into `~/.local/lib/z0-relay-guard/<content-sha256>/` and makes the snapshot read-only.
The content digest covers sorted relative file paths and their SHA256 digests, independent of checkout paths and mtimes.
The live `~/.omp/agent/extensions/z0-relay-guard` symlink points to that immutable copy, never to the task worktree.
Repeated installation reuses the same verified snapshot. An altered snapshot or a non-symlink destination blocks installation.
The installer serializes deployment changes and atomically replaces the extension symlink.
It prints the installed commit, content digest, and final link target. It does not run OMP.
This branch did not run the installer against the live home.
Then start a new OMP session, or use OMP's extension reload.
To disable, remove only this deployment symlink and reload extensions.
OMP loads `index.js` directly. No dependencies or build step are required.

Start OMP with `omp --show` to enable the registered Boolean flag.
This is a real extension CLI flag, not an assumed built-in.
OMP's `src/cli/extension-flags.ts` reparses arguments using registered extension flags and passes values to the runner.
`src/cli/args.ts` accepts registered Boolean flags such as this extension's `show` flag.
After a permitted tool completes, the `tool_result` handler sends its coverage blocks as a visible custom chat message.
It sets `triggerTurn: false`. It does not send a user message or create user-authored history.
Notes match the issuing session and tool-call id. A failed tool still shows its coverage, labelled `tool failed`.
The flag defaults to false. Acknowledgement and headless exceptions have no coverage note.

## Synthetic proof

A throwaway script exercised the registered handlers on MBP through `fleet-build --node mbp`.
The job used a disposable Python venv and the image's existing Bun runtime. It installed no dependencies.
All 53 synthetic cases passed: allow/block for all relay forms, coverage completeness, additions, dispositions,
Unicode, acknowledgements, root ancestry, malformed input, zero-user exceptions, and post-tool note API delivery.
The proof captured `sendMessage` arguments. It did not exercise a live OMP UI or a real Tern relay.
No test suites, linters, or formatters ran. The throwaway script is not part of this extension.
