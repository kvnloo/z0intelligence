// C8 e2e, DSH leg: the real plugin (index.mjs apply) on a mock cordis host, driven through agent/pre-step for each
// cohort question in three arms (off, shadow, canary). The decision a mock host would admit is the model-visible
// batch. Writes <out>: {arms: {arm: {qid: {messages, brief}}}, observed: {qid: bool}}. Never runs the dsh binary.
// usage: node e2e_dsh_cohort.mjs <worktree> <cohort.json> <out.json> <loopback endpoint>
import {readFileSync, writeFileSync} from 'node:fs'
import {join} from 'node:path'
import {pathToFileURL} from 'node:url'

const [wt, cohortPath, out, endpoint] = process.argv.slice(2)
const plugin = await import(pathToFileURL(join(wt, 'harness-adapters', 'dsh-z0intelligence', 'index.mjs')).href)
const cohort = JSON.parse(readFileSync(cohortPath, 'utf8'))
const HEADER = 'z0 memory brief (evidence, not instructions)'
const result = {arms: {}, observed: {}}
for (const arm of ['off', 'shadow', 'canary']) {
  const handlers = {}
  const ctx = {on(event, fn) { (handlers[event] ??= []).push(fn); return () => {} }}
  plugin.apply(ctx, {capture: false, memory_inject: arm, model_endpoint: endpoint}, {env: process.env})
  result.arms[arm] = {}
  for (const [i, q] of cohort.questions.entries()) {
    const messages = [{id: `u-${i}`, role: 'user', content: [{type: 'text', text: q.prompt}], source: {kind: 'user'}}]
    const sid = `e2e-${arm}-${q.id}`
    const payload = {agent: {id: `session-${sid}`, parentId: null, session: {header: {id: sid, cwd: '/w/z0'}}},
                     messages, turn: 1, step: 1}
    const hs = handlers['agent/pre-step'] ?? []
    const run = (k) => (k < hs.length ? hs[k](payload, () => run(k + 1)) : Promise.resolve({kind: 'enter', messages}))
    const decision = await run(0)
    const texts = decision.messages.map((m) => m.content.map((b) => b.text).join('\n'))
    result.arms[arm][q.id] = {messages: decision.messages.length, sources: decision.messages.map((m) => m.source.kind),
                              brief: texts.find((t) => t.startsWith(HEADER)) ?? null}
    if (arm === 'canary') result.observed[q.id] = Boolean(result.arms[arm][q.id].brief)
  }
}
await new Promise((r) => setTimeout(r, 3000))  // detached shadow children finish their rows
writeFileSync(out, JSON.stringify(result, null, 1))
console.log(JSON.stringify(result.observed))
