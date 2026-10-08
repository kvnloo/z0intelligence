# z0 Capacity Export for Tern

Read-only window plugin for the federated capacity plane:

- reads `cx.hosts:list()`
- reads `cx.sessions:list()`
- writes a bounded local JSON projection every 5 seconds
- exposes **Export z0 capacity snapshot** in Tern's palette

It never switches hosts, opens/closes sessions, types into panes, or dispatches work.

Default output:

```text
~/.z0int/state/tern_capacity.json
```

Override with:

```text
Z0INT_TERN_CAPACITY_SNAPSHOT=/path/to/tern_capacity.json
```

Development install from the z0intelligence checkout:

```bash
tern plugin link ./tern-plugins/z0-capacity
tern plugin list
```

Then emit the joined z0 projection with:

```bash
z0int capacity snapshot --json
```

The z0 systemd snapshot timer can refresh the joined projection every five seconds.

This is a **window-half** plugin because Tern's `cx.hosts` and `cx.sessions` fleet APIs are window APIs. Installed Tern plugins run with the user's rights, so keep this plugin intentionally narrow and auditable.
