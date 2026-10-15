# Portable z0intelligence lab (any fleet host)

Adapts the desktop's `hermes-k8s-lab` principles instead of copying its files:
disposable Kind cluster `hermes-lab`, exact-context guard (`kind-hermes-lab`),
mutations only in the `hermes-lab` namespace, and **host authority**: the router,
local models and credentials stay on the host; pods reach them through selectorless
Services whose EndpointSlices are published `ready: true` only after an in-cluster probe.

Host deltas are detected, not hard-coded: the Kind bridge gateway differs per host
(desktop `172.19.0.1`, mbp `172.21.0.1`), and the local model device differs
(desktop: NVIDIA/CUDA; mbp: AMD Radeon R9 M370X 2 GB via llama.cpp **Vulkan**).

```bash
deploy/lab/up --plan     # read-only
deploy/lab/up            # create/verify cluster, probe + publish host services
```

Host services (systemd user units, linked from this repo — one source of truth):

| unit | env file (host delta only) | port |
| --- | --- | --- |
| `z0intelligence.service` | `~/.config/z0intelligence/service.env` (`Z0INT_PYTHON`, `Z0INT_BIND`, optional `Z0INT_BIND_2` = Kind gateway) | 11501 |
| `z0-slm.service` | `~/.config/z0intelligence/slm.env` (`LLAMA_SERVER`, `Z0INT_SLM_MODEL`, `Z0INT_SLM_DEVICE`, `Z0INT_SLM_BIND`, `Z0INT_SLM_PORT`) | 11510 |

User-level pinned tools (no sudo): kind v0.33.0 and kubectl v1.36.4 (checksum-verified,
matching the desktop), llama.cpp `b11270` ubuntu-vulkan-x64 (sha256-verified).

Measured on mbp (Qwen3-0.6B Q8_0, llama-bench): Radeon Vulkan pp512 405 t/s, tg64 30 t/s;
CPU (4 threads, under load) pp512 17.7 t/s, tg64 5.4 t/s.

Mesh: the router has no authentication, so it is **not** bound to the tailnet by default.
Cross-host use (e.g. mbp borrowing the desktop's GPU via llama.cpp RPC, or routing to a
peer's router) is an explicit opt-in that needs an auth/allowlist decision first.

Fleet declaration (dotfiles `fleet.toml`, owned there, not here) would add only the delta:

```toml
[clusters.hermes-lab-mbp]
enabled = true
host = "mbp"
distribution = "kind"
owner_repo = "z0intelligence"   # deploy/lab
purpose = "portable-z0-host-authority"
```
