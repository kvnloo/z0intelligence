// DSH memory seam (C8) on the agent/pre-step waterfall: the z0 memory brief joins the step's admitted messages.
//
// Only a root agent's first step of a turn, and only messages a user sent, form the query. In canary/on the brief
// is one extra user-role message (source kind `z0-memory`, form `recall`) after the native batch; DSH logs every
// admitted message (its model-visible content always goes through logged channels), so unlike Hermes the brief is
// in the DSH session log too. Shadow (the default) never changes the decision. Any failure returns next()'s
// decision unchanged. Only the z0 memory surface is used (`python -m z0int.memory.seam`), nothing of Hermes.
import {randomUUID} from 'node:crypto'
import {createMemoryClient} from '../memory-client.mjs'
import {attribution, cwdOf, isRootAgent, lineageTurnKey} from './lineage.mjs'

export const counters = {failed: 0}

function userText(messages) {
  return (messages ?? []).filter((m) => m?.role === 'user' && (m.source?.kind ?? 'user') === 'user')
    .flatMap((m) => (m.content ?? []).filter((b) => b?.type === 'text').map((b) => b.text)).join('\n')
}

function briefMessage(text) {
  return Object.freeze({id: randomUUID(), role: 'user', content: Object.freeze([Object.freeze({type: 'text', text})]),
                        source: Object.freeze({kind: 'z0-memory', form: 'recall'})})
}

// config: memory_inject (off|shadow|canary|on, default shadow), model_endpoint (the profile's model base URL; unset
// means the cloud DeepSeek API, so canary/on stay blocked until the owner sets inject.dsh.allow_cloud_injection in
// the z0 memory config), memory_injector (single-owner id).
export function registerMemory(ctx, config = {}, {env = process.env, spawn} = {}) {
  const client = createMemoryClient({harness: 'dsh', mode: config.memory_inject, env, spawn,
                                     injector: config.memory_injector})
  if (client.mode === 'off') return null
  ctx.on('agent/pre-step', async (payload, next) => {
    const decision = await next()
    try {
      const agent = payload?.agent
      if (decision?.kind !== 'enter' || payload?.step !== 1 || !isRootAgent(agent)) return decision
      const query = userText(payload.messages)
      if (!query.trim()) return decision
      const sessionId = attribution(agent, payload.turn, payload.step).session_id ?? agent?.id
      const context = await client.turn({sessionId, turnId: lineageTurnKey(agent, payload.turn), query,
                                         endpoint: config.model_endpoint ?? env.Z0INT_MEMORY_ENDPOINT, cwd: cwdOf(agent)})
      if (!context) return decision
      return {...decision, messages: [...decision.messages, briefMessage(context)]}
    } catch {
      counters.failed++
      return decision
    }
  })
  return client
}
