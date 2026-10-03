# tencentdb-memory: TencentDB Agent Memory gateway for Hermes

## What it is

"TencentDB" here is **TencentDB Agent Memory**, Tencent's open-source four-layer
agent memory (github.com/TencentCloud/TencentDB-Agent-Memory, MIT). It is not the
Tencent Cloud SQL product. It has two parts:

- **Memory Core gateway** (`memory-core`, Node.js): an HTTP service on
  `127.0.0.1:8420`. It does L0 conversation capture, L1 episodic extraction (LLM
  plus dedup), L2 scene blocks and L3 persona, all stored in SQLite with FTS5/BM25
  (no embeddings).
- **Hermes provider** `memory_tencentdb`: a thin Python HTTP client. It is installed
  as a symlink at `~/.hermes/plugins/memory_tencentdb`, and also under
  `profiles/chiefstaff/plugins` and `profiles/memory-lab/plugins`. All three point at
  `/workspace/hermes-home/kanban/boards/zer0-company/workspaces/t_d35bb79c/candidate-tencent/MemoryCore/hermes-plugin/memory/memory_tencentdb`
  (upstream rev 97f9465, v2.0.1-beta.2).
  - It expects `http://127.0.0.1:8420` with no auth: it sends `Bearer local` and
    `x-tdai-service-id: default`, with team/agent/user set to `default`.
  - It counts the gateway as available when `GET /health` returns `ok` or `degraded`.
  - Per turn it calls `/v3/atomic/search`, `/v3/core/read` and `/v3/scenario/ls`
    (in parallel), then `/v3/conversation/add` in the background.
- The OMP extension `~/.omp/agent/extensions/tencentdb-memory` uses the same
  gateway and the same `default` tenant.

`memory.provider: memory_tencentdb` is set in `~/.hermes/config.yaml` (root/default),
`profiles/chiefstaff` (the active profile) and `profiles/memory-lab`.

## How it ran before

| When | What | Source |
|---|---|---|
| 2026-08-04 | First install. Cloned v2.0.0 (0aff21a) to `/mnt/zer0models/oss/TencentDB-Agent-Memory`. Ran `deploy/global-images/start-all.sh` as Docker containers: core :8420, hub (panel :8125 + knowledge :8424), proxy :8096. Volumes `tdai-memory-core-data` and `tdai-panel-data`. Hermes plugin symlink plus `MEMORY_TENCENTDB_GATEWAY_URL/LLM_BASE_URL/LLM_MODEL` in `~/.hermes/.env`. LLM: an **xAI OAuth access token** (`api.x.ai`, `grok-4-1-fast-non-reasoning`), with the expiry caveat noted. | hermes:20260803_215944_bcaf13 (msgs 119-210) |
| 2026-08-17 | Gateway down (":8420 connection refused"). Plugin provenance pinned to TencentCloud rev 97f9465. | hermes:20260815_184109_cb6489; `.../t_d35bb79c/memory-tencentdb-provenance-gate.md` |
| 2026-09-17 22:43Z | Stack brought back with `PULL=1 start-all.sh` (image `agentmemory/memory-core:latest`, id 55fec3a6…). Core health ok, chiefstaff wired, OMP extension written and verified. | grok:01a0b06a-0f19-7e61-af25-8f4c6a651d21 (msgs 242-340) |
| 2026-09-18 07:57Z | **Host shutdown** (`last -x`: 02:57 CDT). All containers exited at that second. They had `RestartPolicy=no`, so nothing brought them back. | `docker inspect tdai-memory-core` |
| 2026-09-19 | OMP P0.7B/C: the "8 s blocker" was the OMP extension's 8000 ms fetch timeout against the dead :8420. It was changed to a 100 ms bounded fetch with a cache. | omp:01a0b899-ad06-7000-a6df-c852aad9fa26 |
| 2026-09-19 to 20 | Probes reported "configured but not installed". Plugin re-symlinked from the kanban candidate. Blocked on the LLM key (no xAI API key, only OAuth). Model shootout was designed but never run against a live gateway. | hermes:20260919_120228_b9b756, hermes:20260919_193149_aa5c45, hermes:20260919_201004_ec2c4f |

Historical data in `tdai-memory-core-data` was 1.7 MB: 2 L0 conversation files,
**0 L1 records and 0 scenes**. Extraction never produced anything before this
deployment.

## How it runs now (2026-10-03)

- **Unit**: `~/.config/systemd/user/tencentdb-memory.service`. The source is
  `./tencentdb-memory.service`. It is enabled in `default.target` and Linger=yes, so
  it starts at boot. `Restart=always`, `RestartSec=30`, and it retries without limit.
- **What runs**: `bin/tencentdb-memory-run` runs
  `docker run --rm -i --network host` with the same image (pinned by id
  `sha256:55fec3a6…`) and the **same volume `tdai-memory-core-data`**.
  - `TDAI_GATEWAY_HOST=127.0.0.1` keeps it on loopback only (`ss` shows `127.0.0.1:8420`).
  - Memory is capped at 2 GB and CPU at 2 cores.
  - Only memory-core runs. The hub, proxy and panel are not part of this unit.
- **Config**: `./tdai-gateway.yaml`, mounted read-only. It holds no secrets. It is the
  historical generated config without its inline credentials.
- **Settings**: `./tencentdb-memory.env` (non-secret): image, port, volume, LLM URL
  and model, and the BWS secret id.
- **Data**: Docker volume `tdai-memory-core-data`, at
  `/mnt/zer0models/docker-data/volumes/tdai-memory-core-data/_data` (root-owned).
  Snapshot: `/mnt/zer0models/z0-wt/wiring/backups/tencentdb/tdai-memory-core-data-20261003.tgz` (0600).
- **LLM (interim)**: OpenRouter `nvidia/nemotron-3-super-120b-a12b:free`.
- **Secrets used** (names only):
  - BWS secret `OPENROUTER_API_KEY` (id b3968151-…). Its credit limit is $0, so only
    `:free` models can be called and no paid usage is possible.
  - The BWS machine token comes from the GNOME keyring item
    `service=org.hermes.agent key=bws-access-token`. This is the same item that
    `omp-keyring-bootstrap.sh` and `dsh-bws-refresh` use.
  - The key is read at start and passed to the container **on stdin only**. It is not
    in argv, the unit, files, `docker inspect`, or the logs.
  - No new secret was created in BWS.

### Health check

```bash
/mnt/zer0models/z0-wt/wiring/ops/tencentdb/bin/tencentdb-memory-health            # one-shot, exits 0 when ok|degraded
/mnt/zer0models/z0-wt/wiring/ops/tencentdb/bin/tencentdb-memory-health --wait 60  # wait up to 60 s
curl -s http://127.0.0.1:8420/health | jq
```

`embeddingService:false` is expected because embeddings are set to `none`. Recall
uses BM25.

### Logs

`journalctl --user -u tencentdb-memory -f`. This is the only copy, because the
container uses `--log-driver none`. The gateway always logs at DEBUG, so fragments of
queries and memories appear in the journal.

### Start, stop and status

```bash
systemctl --user status tencentdb-memory
systemctl --user restart tencentdb-memory   # ~20 s graceful
systemctl --user stop tencentdb-memory      # Hermes falls back to built-in memory
```

### End-to-end test (isolated, synthetic)

```bash
E=/mnt/zer0models/z0-wt/wiring/homes/tencentdb-e2e
cd /mnt/zer0models/z0-wt/wiring/ops/tencentdb/e2e
env -i HOME=$E HERMES_HOME=$E/.hermes PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 \
  ~/.hermes/hermes-agent/venv/bin/python provider_e2e.py --calls 20 --l1-wait 420 --out results.json
```

This runs the installed Hermes plugin code under tenant
`z0-e2e/tencentdb-e2e/synthetic-e2e`, not `default`. Result on 2026-10-03
(`e2e/results-20261003-final.json`):

- `is_available=true`.
- A write through `sync_turn` read back on the first try.
- The default tenant did not see the test data.
- L1 extraction by the LLM produced a persona memory with the canary within 15 s.
- Latency: prefetch p50 3.1 ms / p95 4.1 ms; search p50 1.2 / p95 1.9 ms;
  conversation add p50 5.5 / p95 10.5 ms. The old blocker was 8 s.

Kill test: after `docker kill`, systemd brought it back healthy in 37 s.

### Changing the LLM, for example to a dedicated key

1. Create the key in the provider's console. Name it `tencentdb-memory-gateway`.
2. Store it in BWS: `bws secret create MEMORY_TENCENTDB_LLM_API_KEY "$(…)" ef35bed4-fc4f-42ab-a21f-b4bf013919b7`
   (pipe the value in; do not echo it).
3. In `tencentdb-memory.env`, set `TENCENTDB_LLM_BASE_URL`, `TENCENTDB_LLM_MODEL`,
   `TENCENTDB_LLM_BWS_SECRET_NAME` and `..._ID`.
4. Run `systemctl --user restart tencentdb-memory`.

To restore the historical model: base URL `https://api.x.ai/v1`, model
`grok-4-1-fast-non-reasoning`, using an xAI API key (not OAuth).

### Rollback

```bash
systemctl --user disable --now tencentdb-memory
rm ~/.config/systemd/user/tencentdb-memory.service && systemctl --user daemon-reload
```

This returns to the state before this deployment: gateway down and Hermes using
built-in memory. No other files were changed.

To restore the data, stop the unit and untar the snapshot into the volume, using a
container that mounts `tdai-memory-core-data`.

## Known issues

- **Upstream plugin bug.** The `memory_tencentdb_conversation_search` tool parses
  `data.items`, but the gateway returns `data.messages`. This applies to both this
  image and the plugin's own MemoryCore revision. The tool therefore always says
  "No conversations found", even though the HTTP call returns hits. The prefetch
  path, L1 `memory_search` and capture are not affected. The fix is one line in
  `__init__.py:875` (`.get("messages", [])`). It has not been patched, because the
  plugin is a provenance-sealed third-party install.
- **Privacy.** L1/L2/L3 send conversation text to the OpenRouter free endpoint
  (provider: Nvidia). Free endpoints may log or train on inputs; the owner's
  OpenRouter account allows free endpoints. Historically the text went to xAI. Use a
  dedicated paid or private key if this is not acceptable.
- **Locked keyring at boot.** If the keyring is locked (boot before graphical login),
  the wrapper exits and systemd retries every 30 s. The service starts once the login
  keyring unlocks.
- **No API key on the gateway.** `TDAI_GATEWAY_API_KEY` is unset, which is the
  historical default and what the Hermes provider expects. It is safe only because
  the gateway is loopback-only.
- **Do not run the old stack alongside this unit.** Do not run
  `deploy/global-images/start-all.sh` / `start-memory-core.sh` at the same time:
  they bind `0.0.0.0:8420` and rewrite a config containing a plaintext key. The old
  exited containers `tdai-memory-core`, `tdai-memory-hub`, `tdai-proxy` and
  `tdai-memory-lab` were left untouched.
- **Plaintext credentials in the old deploy directory.** The files below are 0644
  and appear to hold the old expired xAI OAuth token (786 chars). Shred or rotate
  them:
  - `/mnt/zer0models/oss/TencentDB-Agent-Memory/deploy/global-images/.memory-core-config/tdai-gateway.yaml`
  - `.../.proxy-config/config.yaml`
  - The `.env` in that directory is also 0644. Its contents were not read.
- **Synthetic test data.** E2E data remains in the volume under tenant `z0-e2e`. It is
  isolated from `default`.
