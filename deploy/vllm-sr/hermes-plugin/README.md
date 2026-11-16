# sr: Hermes plugin for the free semantic router

Install into a profile: `ln -s "$PWD" ~/.hermes/profiles/<p>/plugins/sr && hermes plugins enable sr`.

| Command | What it does |
|---|---|
| `hermes sr status` / `/sr` | Router up?, model count, turbo present, entry points, live pool size, which provider keys this profile has (names only) |
| `hermes sr onboard --tier minimal\|poweruser\|all --store hermes\|bws [--check]` | Lists the keys the tier needs with signup links and how many router backends each unlocks; on a terminal, a masked prompt per missing key, Enter skips |
| `hermes sr bench [N C]` | `bench_fanout.py` through `vllm-sr/turbo`: p50/p95, tok/s, which backends served |
| `hermes sr scores [--from results.jsonl] [--write]` | bench-r1 trials -> `profiles/capability.jsonl` (`{model, source, score0to1, n, date}`), the ledger #339's ranker reads. Only clean, non-smoke, non-infra free-model trials count |
| `hermes sr lanes ...` / `/sr tiers` | `free_pool.py lanes` plan / per-tier key → backend counts |

Tiers (cumulative; `all` is derived from `free_pool.CANDIDATES`, so a new provider joins it automatically and the
test fails until it has a signup URL here):

- **minimal**: OpenRouter + Groq (+ 9 keyless backends that need no signup)
- **poweruser**: + NVIDIA NIM, Cohere, Z.ai
- **all**: + SambaNova, SiliconFlow, and anything added later

Stores (pick one per run; values are never printed or returned):

- `hermes`: the profile `.env` via `save_env_value_secure` (same as Hermes' own masked prompt).
- `bws`: the project from `hermes secrets bitwarden setup` (`secrets.bitwarden.project_id`, or `--bws-project`).
  Updates an existing key in place, never duplicates. Note: the `bws` CLI takes the value as an argument, so it
  is visible in `/proc` for the duration of that call.
- Cua Keyvault is **not** a store here: it holds browser cookies/passwords delivered into Spaces and is never
  readable by apps or agents, so a router cannot pull an API key from it. Use it (Google sign-in autofill) to
  create the provider accounts, then paste the key into one of the stores above.

## Continuous hill-climb loop

1. bench-r1 `run_matrix.py --suite skillsbench --only free/...` (free models via `vllm-sr/m/<model>`) appends trials.
2. `hermes sr scores --write` turns them into capability rows; infra failures never become a model score.
3. `free_pool.capability()` (#339) ranks first-draw weight by those rows, beating the 4-benchmark Hermes Index when n is larger.
4. A ranking change ships through `live` with the receipt. Prompt search (GEPA) only after the scorer separates arms:
   both GEPA runs and hill-climb v1 ended with best == seed.
