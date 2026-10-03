// C8 round-3 e2e, DSH join leg: the real plugin (index.mjs apply, capture ON, memory shadow) on a mock cordis host
// with real spawns, one root turn per question: agent/request (capture prompt) -> agent/pre-step (memory) ->
// agent/turn-stopping. The opportunity row must carry the turn's shadow receipt (checked later). Never runs dsh.
// usage: node e2e_dsh_join.mjs <worktree> <questions.tsv> <task cwd>
import {readFileSync} from 'node:fs'
import {join} from 'node:path'
import {pathToFileURL} from 'node:url'

const [wt, tsv, cwd] = process.argv.slice(2)
const plugin = await import(pathToFileURL(join(wt, 'harness-adapters', 'dsh-z0intelligence', 'index.mjs')).href)
const handlers = {}
const ctx = {on(event, fn) { (handlers[event] ??= []).push(fn); return () => {} }}
plugin.apply(ctx, {capture: true, memory_inject: 'shadow'}, {env: process.env})
const chain = async (event, payload, last) => {
  const hs = handlers[event] ?? []
  const run = (k) => (k < hs.length ? hs[k](payload, () => run(k + 1)) : Promise.resolve(last))
  return run(0)
}
const questions = readFileSync(tsv, 'utf8').split('\n').filter(Boolean).slice(0, 2).map((l) => l.split('\t'))
for (const [qid, prompt] of questions) {
  const msg = {id: `u-${qid}`, role: 'user', content: [{type: 'text', text: prompt}], source: {kind: 'user'}}
  const sid = `join-dsh-${qid}`
  const agent = {id: `session-${sid}`, parentId: null, session: {header: {id: sid, cwd}}, frozenMessages: [msg]}
  await chain('agent/request', {agent, turn: 1, step: 1}, {provider: 'sandbox', model: 'm'})
  await chain('agent/pre-step', {agent, messages: [msg], turn: 1, step: 1}, {kind: 'enter', messages: [msg]})
  for (const fn of handlers['agent/turn-stopping'] ?? []) await fn({agent, turn: 1})
  console.log(JSON.stringify({qid, ok: true}))
}
await new Promise((r) => setTimeout(r, 3000))
