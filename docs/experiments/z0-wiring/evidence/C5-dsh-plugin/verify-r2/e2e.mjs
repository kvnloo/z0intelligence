// verifier e2e: real spawn of hook_adapter --harness dsh, fake z0 service on a private port, capture on vs off
import http from 'node:http'
import {readFileSync, existsSync, readdirSync} from 'node:fs'
import {join} from 'node:path'
const T = process.argv[2], mode = process.argv[3]  // on | off
const p = await import(join(T, 'harness-adapters/dsh-z0intelligence/index.mjs'))
const seen = []
const server = http.createServer((req, res) => { let b=''; req.on('data', d => b += d); req.on('end', () => {
  seen.push({path: req.url, body: JSON.parse(b)}); res.writeHead(200, {'content-type': 'application/json'})
  res.end(JSON.stringify({ok: true, mode: 'shadow', executed: false, route: {kind: 'PARENT_ONLY', executed: false}})) }) })
let port
for (port = 11552; port <= 11559; port++) { if (await new Promise(r => { server.once('error', () => r(false)); server.listen(port, '127.0.0.1', () => r(true)) })) break }
const hs = {}
const ctx = {on: (e, fn) => { (hs[e] ??= []).push(fn) }}
p.apply(ctx, {capture: mode === 'on', router: false, shadow: {url: `http://127.0.0.1:${port}`, sample_rate: 1, max_inflight: 4}})
const transcript = []
const llm = async (cfg) => { const chunks = [{type:'text-delta', index:0, text:'VERIFY-RESPONSE-ZETA ok'}, {type:'finish', reason:'stop'}]
  let out = []; const hl = hs['llm/stream'] ?? []
  const run = (i) => i < hl.length ? hl[i]({}, () => run(i + 1)) : (async function*(){ yield* chunks })()
  for await (const c of run(0)) out.push(c); return out }
const req = async (agent, turn, step) => { const base = {provider: 'p', model: 'verify-model', n: step}
  const hl = hs['agent/request'] ?? []; const run = (i) => i < hl.length ? hl[i]({agent, turn, step}, () => run(i + 1)) : Promise.resolve(base)
  const cfg = await run(0); transcript.push(['req', cfg === base, JSON.stringify(cfg)]); transcript.push(['llm', JSON.stringify(await llm(cfg))]) }
const stop = async (agent, turn) => { for (const fn of hs['agent/turn-stopping'] ?? []) transcript.push(['stop', String(await fn({agent, turn}))]) }
const err = (agent, turn) => { for (const fn of hs['agent/error'] ?? []) transcript.push(['err', String(fn({agent, turn, step: 1, error: new Error('VERIFY-ERR-OMEGA')}))]) }
const root = (sid, text) => ({id: `session-${sid}`, parentId: null, session: {header: {id: sid, cwd: null}},
  frozenMessages: [{role: 'user', content: [{type: 'text', text}]}]})
const kid = {id: 'kid-1', parentId: null, session: {header: {id: 'k1', parentSession: 'vA', origin: 'subagent', delegationDepth: 1}},
  frozenMessages: [{role: 'user', content: 'VERIFY-CHILD-TASK'}]}
await req(root('vA', 'VERIFY-PROMPT-ALPHA write docs'), 1, 1); await req(root('vA', 'x'), 1, 2); await req(kid, 1, 1); await stop(kid, 1); await stop(root('vA','x'), 1)
await req(root('vA', 'VERIFY-PROMPT-BETA fix tests'), 2, 1); await stop(root('vA','x'), 2)
await req(root('vB', 'VERIFY-PROMPT-GAMMA plan'), 1, 1); await stop(root('vB','x'), 1)
await req(root('vB', 'VERIFY-PROMPT-DELTA crash'), 2, 1); err(root('vB','x'), 2); await stop(root('vB','x'), 2)
await new Promise(r => setTimeout(r, 500)); server.close()
console.log(JSON.stringify({transcript, seenPaths: seen.map(s => s.path), seenTasks: seen.map(s => s.body.task.slice(0, 20)), port,
  keys: [['vA','session-vA:1'],['vA','session-vA:2'],['vB','session-vB:1'],['vB','session-vB:2']].map(([s,t]) => p.canonicalTurnKey(s,t)), counters: p.counters}))
