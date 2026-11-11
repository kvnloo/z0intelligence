# Sub-proxy: paid subscription lanes for the router

`vllm-sr/paid` routes to the owner's subscriptions in a fixed order: ChatGPT `gpt-6.1-sol`,
then Grok 4.7 (`algorithm: static` plus fallback on 401/403/408/429/5xx/529). groot and the
MBP share it: the MBP reaches the router through `router_gateway.py`
(`http://100.113.138.100:8898/v1`, bearer `VLLM_SR_CLIENT_TOKEN`).

**Claude is not routed.** When the router translates a real Hermes request, Anthropic answers
`400 "Third-party apps now draw from your extra usage, not your plan limits"`. Small curl
probes still pass, but agent turns would bill extra usage. Claude turns therefore stay on
Hermes' direct Anthropic provider. The `/anthropic` lane is kept only for probes. Don't try
to make router-shaped requests look like Claude Code to get around this.

The router pod never holds a subscription credential. It calls this host proxy with
`VLLM_SR_SUBPROXY_TOKEN`. The proxy then attaches the live OAuth credential from the Hermes
credential pool of the `clean` profile:

| Path | Upstream | Pool |
|---|---|---|
| `/anthropic/v1/messages` | api.anthropic.com | `anthropic` (claude-ultra, claude-sub2, ANTHROPIC_TOKEN, by priority; rotates on 429) |
| `/codex/v1/responses` | chatgpt.com/backend-api/codex | `openai-codex` |
| `/codexchat/v1/chat/completions` | same, through a Chat Completions facade (router uses this one) | `openai-codex` |
| `/xai/v1/chat/completions` | api.x.ai | `xai-oauth` |

- **Hermes code does the hard parts.** Selection, refresh and cooldown run through Hermes'
  own `CredentialPool`, so no second client forks a refresh-token family. Claude requests
  get the same OAuth betas, `claude-code` user-agent and Claude Code system prefix as Hermes'
  own Anthropic client.
- **Codex requests are adjusted.** The proxy forces `stream:true`/`store:false`, lifts
  system turns into `instructions`, and folds the SSE stream back into one JSON response
  when the caller did not ask to stream.
- **Bind address.** The proxy listens on `10.42.0.1:18800` (groot `cni0`), so only the
  host and pods can reach it.
- **Ledger.** Every request is logged to `~/.local/state/sub-proxy/requests.jsonl`
  (lane, model, credential label, status, seconds). No bodies or tokens are logged.

## Run
```bash
ln -sfn "$(ls -d ~/.hermes/tools/python-3.14.9*-linux-x64 | head -1)/bin/python3" ~/.local/share/sub-proxy/python3
ln -sf "$PWD/sub-proxy.service" ~/.config/systemd/user/ && systemctl --user daemon-reload && systemctl --user enable --now sub-proxy
curl -s 10.42.0.1:18800/health
```
The token lives in bws ("Hermes keys", `VLLM_SR_SUBPROXY_TOKEN`) and in the DSH store
`refs:`. `deploy.sh` passes it to the router Secret.

## Pitfalls
- **The router's strict decoder rejects unknown fields.** Seen so far: Anthropic `usage.speed`,
  `container`, `stop_details`; Codex `attribution`. The proxy whitelists Anthropic fields and
  strips Codex ones. A new field shows in router logs as
  `neutral_response_decode_failed ... unknown field "X"`.
- **The router can't stream a Responses-format backend.** It decodes the body as one JSON
  object, so a streaming client gets an empty stream (Hermes logs `EmptyStreamError` and
  retries until it times out). That's why GPT goes through `/codexchat`, which converts
  Responses SSE into chat.completion chunks with Hermes' own message/tool converters.
- **The router passes the client's own Authorization header through** and puts its key in
  `x-api-key` for Anthropic backends. The proxy accepts its token from either header.
- **Use `HERMES_HOME` = the `clean` profile, not root `~/.hermes`.** On 2026-11-03 the
  root copy of the Codex grant was stale (`refresh_token_reused`), so `select()` tried a
  refresh, got 401 and reported every Codex credential as cooling down. The same pool
  under `clean` selected fine.
- **xAI OAuth answers `personal-team-blocked:spending-limit`** for every model and path
  (checked 2026-11-03, same team/principal as the working `grok` CLI). The Grok lane is
  wired but returns 403 until the account has API credits or a subscription that covers
  api.x.ai. The router skips over it via fallback.
- **The Hermes launcher paths are redacted in tool output** (`python-3.14.9+202****...`),
  so pasting a path from a log breaks. Resolve it with `ls -d` and use the symlink above.
