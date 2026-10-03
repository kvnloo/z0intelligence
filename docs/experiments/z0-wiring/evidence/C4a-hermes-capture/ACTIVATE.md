# C4a-hermes-capture activation (NOT performed; owner approval required; gated by G0)

Prerequisite A0: integrate/wiring-20261003 pushed; pinned checkout /mnt/zer0models/z0-wt/pinned/z0intelligence and
venv /mnt/zer0models/z0-wt/venv-wiring built from it.

1. The owner names the active profile (~/.hermes/active_profile) and checks `hermes --version` against the fork SHA
   used in the e2e (ad31bbf079f0ee559ce1b61c5f85178c4c3d9396).
2. Back up <profile>/config.yaml to config.yaml.bak-z0wiring-20261003. Record
   `readlink -f <profile>/plugins/hermes-z0intelligence`, then copy that dir (or the symlink itself) to
   plugins/hermes-z0intelligence.bak-z0wiring-20261003.
3. Install. UNVERIFIED: the GitHub-URL install below was never run. Hermes PM syncs its runtime from the network
   first, and the isolated e2e had no network.
     hermes plugins install https://github.com/kvnloo/z0intelligence/tree/<integrate sha>/harness-adapters/hermes-z0intelligence --ref <integrate sha> --force
   Do not use `hermes plugins install <local dir>`: this Hermes parses a path as GitHub owner/repo shorthand.
   FALLBACK (the layout the e2e installs and runs):
     git -C /mnt/zer0models/z0-wt/pinned/z0intelligence archive <integrate sha> harness-adapters/hermes-z0intelligence | tar -x -C <scratch>
     mv <scratch>/harness-adapters/hermes-z0intelligence <profile>/plugins/hermes-z0intelligence   # after the step-2 backup
   then add hermes-z0intelligence to plugins.enabled in <profile>/config.yaml.
4. Set plugins.entries.hermes-z0intelligence.settings to {mode: shadow, opportunities: true,
   z0int_python: /mnt/zer0models/z0-wt/venv-wiring/bin/python, z0int_home: ~/.z0int}. Leave persist_packet_text
   false. There is no host or port setting; any such key is ignored and recorded as a config_warning row.
5. Confirm z0int-decisions is not in plugins.enabled and bend is not enabled with stack_opportunities: true.
   Otherwise the plugin writes double_capture_guard and emits no opportunities. A stale z0int-decisions install shows
   up as retired_vehicle rows in failures.jsonl and writes no opportunity.
6. Keep automatic.json hermes off (#95). The automatic pre_llm_call is then not registered at all.
7. Plugins load at process start (INFERRED). Live Hermes services pick up the plugin only when the owner restarts
   them.
8. Live proof: run one real Hermes turn. Expect ~/.z0int/state/hermes/{events,opportunities,outcomes,failures}.jsonl
   with an opportunity and an outcome joined on turn_key (missing_verifier rows are expected). `z0int hermes
   decisions` should show rows_dropped 0, and ~/.z0int/runtime/hermes-gates should be empty after Hermes exits.
   Record the hook dispatch latency.

Kill switch: settings mode off (no capture hook on the next load), or Z0INT_CAPTURE=0 in Hermes's environment, or
$Z0INT_HOME/config/capture.json {"enabled": false}.
Rollback: restore config.yaml from .bak-z0wiring-20261003 and plugins/hermes-z0intelligence from its backup
(re-create the symlink if readlink showed one), then restart own sessions.
