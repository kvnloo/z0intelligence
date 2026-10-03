# grok-z0intelligence

`hooks/z0-capture.json` is a Grok global hooks file: copy it to `~/.grok/hooks/z0-capture.json`
(activation step A5; remove the file to turn it off). Each hook runs
`python -m z0int.hook_adapter --harness grok <event>` and records the turn in the shared z0int record family
under `$Z0INT_HOME/state/grok/`. Capture only: Grok ignores UserPromptSubmit stdout, and the adapter prints
nothing for Grok anyway.

Grok also runs hooks from `~/.claude/settings.json` (Claude compatibility). Attribution never depends on the
file a hook came from: Grok's runner sets `GROK_HOOK_EVENT`, which the adapter reads. `StopFailure` and
`StopCancelled` close the turn as `failed` / `interrupted` and add an `uncertain_execution` failure row.
