# Z08 capability inventory receipt

This is a bounded 2026-11-05 inventory, not a replacement catalog. `qualify.py` reads the existing `z0int.capability_registry.v1` registry and emits evidence without executing providers. The receipt explicitly remains incomplete. [qualification.json:1-5][receipt]

## Existing owners and decision paths

| Category | Observed availability | Decision path | Evidence |
|---|---|---|---|
| Tools | Git, Python, uv, uvx, ctx, fleet-build, Bun, and Ollama resolve on this host's PATH. Executable discovery alone does not prove task quality. | Use the existing LookFirst query for discovery. Keep heavy checks on the fleet. Do not infer a globally best tool. | [qualification.json:6-18][host], [integration-probes.log][probes] |
| APIs | No live API request or credential probe ran in this qualification. | Retain privacy, admission, cost, and existing function-evidence gates. Unknown availability is not an eligible integration. | [qualification.json:4-5][host], `/mnt/zer0models/z0-wt/z0int-weight-space/src/z0int/intelligence.py:67-105` |
| Libraries | The inspected Python has pytest 9.0.2. Serena, MCP, z0int, torch, and transformers have no distribution metadata in this interpreter. This does not describe other virtual environments. SQLite FTS5 insert and search succeeded. | Use a verified environment for integration tests. Do not import models, download dependencies, or infer system-wide absence from one interpreter. | [qualification.json:20-31][host], [integration-probes.log][probes] |
| Models | The existing registry contains 79 records. Ten records' evaluation-source files match their recorded hashes. Sixty-nine have no evaluation source. No live inference ran. | Preserve `eligible`, exact function contracts, and existing route gates. A matching file hash is integrity evidence, not renewed model availability or quality. | [qualification.json:175-1321][registry] |
| Skills | The five configured roots enumerate 476, 227, 54, 86, and 13 skill paths respectively. Counts include duplicate and archived paths, not unique capabilities. The two Hermes roots each contain 12 unreadable links. | Read canonical skill roots as well as Hermes roots. Keep dangling links visible as errors, not working integrations. Do not alter active harness links during this receipt. | [preflight.json][preflight], `scripts/look-first/roots.json:23-29` |
| Serena | Neither `serena` nor `serena-mcp-server` resolves on this PATH. No Serena package metadata exists in this interpreter. No MCP handshake or task comparison ran. | Keep unqualified. Installation, an offline fixture project, protocol handshake, and a baseline-versus-Serena task comparison remain necessary. | [qualification.json:13-30][host] |

The registry owner is `/mnt/zer0models/z0-wt/z0int-weight-space`. Its recorded HEAD is `ee994576b659d93c7dec4fc416a7782caebab79b`; its registry file was clean and hashes to `8fe208cbb40e1efbd5870c1e28bab9cfc6f21741461d0a0aafb406d6036d337b`. No registry entry was edited or promoted. [qualification.json:1314-1323][registry]

LookFirst remains a bounded read-only evidence lookup, with an existing 180 ms database budget and unavailable-result handling. Its live-index smoke returned `status=ok`, 12 Serena-related hits, and 26.258 ms. Those hits are references, not proof that Serena runs. Sources: `src/z0int/look_first.py:19-48`, `src/z0int/look_first.py:62-76`, [lookup-smoke.json][lookup].

The existing intelligence router reads `manifests/capabilities.v1.json`, checks automatic-routing evidence and availability, and can return `PARENT_ONLY`. Actual callers include the bridge, intelligence service, and dispatch path. These are source observations, not a live router qualification. Sources under the registry owner: `src/z0int/intelligence.py:42-52`, `src/z0int/intelligence.py:67-107`, `src/z0int/intelligence.py:176-181`, `src/z0int/agentweb_bridge.py:532`, `src/z0int/intelligence_service.py:146`.

## T3 OS and prior router research

The prior audit discusses T3 OS, a deterministic skill route table, jev skill selection, and the family-level lexical gate. These are existing research leads. This execution did not independently verify the audit's external T3 OS claims or compare router quality, so it makes no current release-status or improvement claim. Source: `/mnt/zer0models/workspace/zer0/data/operations/tool-skill-audit-20261104/AUDIT.md:7-17,52-74`.

## Reproduction and limits

The generator now records its checkout HEAD, dirty-state flag, tracked-diff hash, and exact generator, collector, and roots-config hashes. A dirty receipt is not a reproducibility claim for HEAD alone. `--preflight` inspects discovery without writing the cache or index. Sources: `scripts/look-first/generate.py:682-725`, [preflight.json][preflight].

Run bounded host checks from the LookFirst checkout. Exit 2 from preflight means discovery errors remain. The qualification command exits 0 when it writes a receipt, not when all integrations qualify.

```sh
python3 scripts/look-first/generate.py --config scripts/look-first/roots.json --checkout . --preflight > /path/to/preflight.json
python3 scripts/look-first/qualify.py --registry /mnt/zer0models/z0-wt/z0int-weight-space/manifests/capabilities.v1.json --preflight /path/to/preflight.json > /path/to/qualification.json
```

The initial 31-test generator and lookup suite passed on an admitted MBP worker. A clean committed-source rerun passed 30 tests but failed the unchanged lookup latency assertion at 202.859 ms against 200 ms. This timing failure remains recorded, not fixed or hidden. The first job also observed that the host repository, skill, and cache paths are absent on the worker. The complete production index was not regenerated. That requires a reviewed local-data snapshot of the source repositories, skill targets, and offline cache on the admitted worker. No local full-index generation was used as a fallback. Sources: [fleet-final.log][fleet], `/mnt/zer0models/workspace/zer0/data/operations/intent-analyzer-20261104/ayo-runs/exec-Z08/logs/fleet-committed.log`.

The held installation remains unapplied. Active skill links, credentials, services, provider configuration, and the live index were not changed. The preflight and qualification receipts record `installation.applied=false` and no routing promotion. [preflight.json][preflight], [qualification.json:1323-1325][registry]

[receipt]: /mnt/zer0models/workspace/zer0/data/operations/intent-analyzer-20261104/ayo-runs/exec-Z08/qualification.json
[host]: /mnt/zer0models/workspace/zer0/data/operations/intent-analyzer-20261104/ayo-runs/exec-Z08/qualification.json
[registry]: /mnt/zer0models/workspace/zer0/data/operations/intent-analyzer-20261104/ayo-runs/exec-Z08/qualification.json
[preflight]: /mnt/zer0models/workspace/zer0/data/operations/intent-analyzer-20261104/ayo-runs/exec-Z08/preflight.json
[lookup]: /mnt/zer0models/workspace/zer0/data/operations/intent-analyzer-20261104/ayo-runs/exec-Z08/lookup-smoke.json
[probes]: /mnt/zer0models/workspace/zer0/data/operations/intent-analyzer-20261104/ayo-runs/exec-Z08/logs/integration-probes.log
[fleet]: /mnt/zer0models/workspace/zer0/data/operations/intent-analyzer-20261104/ayo-runs/exec-Z08/logs/fleet-final.log
